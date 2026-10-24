#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量试卷验收：PDF → layout / exam / quality → 指标汇总。

S6 的验收不是“某一张卷能打开”，而是同一套命令在多张不同版式的真实试卷上
留下可复核的证据。输入既可以是 PDF（会调用版式流水线），也可以是已经生成的
layout JSON（不重新 OCR，便于回归）。

用法：
  python tools/run_exam_acceptance.py data/exams/*.pdf
  python tools/run_exam_acceptance.py --reuse out/exams/*.json
  python tools/run_exam_acceptance.py --out-dir out/acceptance/2026-01 data/exams/*.pdf

输出 `acceptance.json` 和 `acceptance.md`。任何一张卷的硬门失败（结构门失败、
覆盖率不足、题干/选项缺失、公式结构损坏、表格未结构化）都会让退出码为 1。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from exam_structure import build_exam, build_quality  # noqa: E402


DEFAULT_THRESHOLDS = {
    "coverage": 0.99,
    "formula": 0.95,
    "table": 0.90,
    "manual_per_page": 1.0,
}


def _slug(path: Path) -> str:
    stem = re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff-]+", "_", path.stem).strip("_")
    if not stem:
        stem = "exam"
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:6]
    return f"{stem}_{digest}"


def _run(cmd: list[str], log: Path, cwd: Path) -> int:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    with log.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write("$ " + " ".join(cmd) + "\n")
        fh.flush()
        proc = subprocess.run(cmd, cwd=str(cwd), env=env, stdout=fh,
                              stderr=subprocess.STDOUT)
    with log.open("a", encoding="utf-8", errors="replace") as fh:
        fh.write(f"[exit {proc.returncode}]\n")
    return proc.returncode


def _server_alive(host: str) -> bool:
    try:
        with urllib.request.urlopen(host.rstrip("/") + "/v1/models", timeout=4) as resp:
            return resp.status == 200
    except Exception:
        return False


def _quality_summary(layout: dict, exam: dict, quality: dict) -> dict[str, Any]:
    pages = layout.get("pages") or []
    lines = sum(len(p.get("lines") or []) for p in pages)
    questions = exam.get("questions") or []
    choice_section_ids = {
        s.get("id") for s in (exam.get("sections") or [])
        if any(k in (s.get("title") or "") for k in ("选择", "单选", "多选"))
    }
    # 不能只统计“已经解析出选项”的题：选项解析失败时 choice_questions=0，
    # 4×0 会让缺选项的卷子显示 0/0 并通过。选择题大题下的题一律要 4 项，
    # 即使当前选项为空。
    choice = [q for q in questions
              if q.get("options") or q.get("section_id") in choice_section_ids]
    # 纯图形选项（如“函数图象大致为”）没有文字选项，不能按 4 个空文字项
    # 判失败；图是否归属由 quality 的 choice_options_graphical 单独报告。
    text_choice = [q for q in choice if q.get("option_mode") != "graphical"]
    expected_options = 4 * len(text_choice)
    actual_options = sum(len(q.get("options") or []) for q in choice)
    formula_count = int(quality["summary"].get("formula_count") or 0)
    formula_broken = int(quality["summary"].get("formula_broken") or 0)
    table_cells = table_filled = 0
    for page in pages:
        for table in page.get("tables") or []:
            rows = [list(table.get("headers") or [])]
            for row in table.get("rows_data") or []:
                rows.append(list(row or []))
            for r, row in enumerate(rows):
                for c, value in enumerate(row):
                    # 表头/行标签的空位（合并表头、左上角空白）不是缺失数据；
                    # 只统计需要填内容的格子，否则合法的合并表永远达不到 90%。
                    if not str(value or "").strip() and (r == 0 or c == 0):
                        continue
                    table_cells += 1
                    table_filled += bool(str(value or "").strip())
    coverage = float((layout.get("document") or {}).get("coverage", {}).get("ratio") or 0.0)
    manual = int((quality.get("repair") or {}).get("counts", {}).get("manual") or 0)
    return {
        "pages": len(pages),
        "lines": lines,
        "questions": len(questions),
        "figures": sum(len(p.get("figures") or []) for p in pages),
        "sections": len(exam.get("sections") or []),
        "choice_questions": len(choice),
        "options_expected": expected_options,
        "options_actual": actual_options,
        "options_complete": expected_options == 0 or actual_options >= expected_options,
        "formula_count": formula_count,
        "formula_broken": formula_broken,
        "formula_pass_rate": 1.0 if formula_count == 0 else 1.0 - formula_broken / formula_count,
        "table_cells": table_cells,
        "table_filled": table_filled,
        "table_fill_rate": 1.0 if table_cells == 0 else table_filled / table_cells,
        "coverage": coverage,
        "manual_actions": manual,
        "manual_per_page": manual / max(1, len(pages)),
        "quality_status": (quality.get("summary") or {}).get("status", "unknown"),
        "errors": (quality.get("summary") or {}).get("errors", 0),
        "warnings": (quality.get("summary") or {}).get("warnings", 0),
    }


def _judge(metrics: dict[str, Any], layout_rc: int, render_rc: int,
           exam_rc: int, thresholds: dict[str, float]) -> list[str]:
    failures: list[str] = []
    if layout_rc != 0:
        failures.append("layout_check")
    if render_rc != 0:
        failures.append("render_check")
    if exam_rc != 0:
        failures.append("exam_check")
    if metrics["quality_status"] == "error":
        failures.append("quality_error")
    if metrics["coverage"] < thresholds["coverage"]:
        failures.append("coverage")
    if metrics["formula_pass_rate"] < thresholds["formula"]:
        failures.append("formula")
    if metrics["table_fill_rate"] < thresholds["table"]:
        failures.append("table")
    if metrics["manual_per_page"] > thresholds["manual_per_page"]:
        failures.append("manual_actions")
    if not metrics["options_complete"]:
        failures.append("options")
    return failures


def evaluate_layout(layout_path: Path, out_dir: Path, thresholds: dict[str, float],
                    log: Path) -> dict[str, Any]:
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    exam = build_exam(layout, source=str(layout_path))
    quality = build_quality(layout, exam)
    stem = _slug(layout_path)
    exam_path = out_dir / f"{stem}.exam.json"
    quality_path = out_dir / f"{stem}.quality.json"
    exam_path.write_text(json.dumps(exam, ensure_ascii=False, indent=1), encoding="utf-8")
    quality_path.write_text(json.dumps(quality, ensure_ascii=False, indent=1), encoding="utf-8")

    check_layout = ROOT / "tools" / "check_layout_json.py"
    check_render = ROOT / "tools" / "check_render.py"
    check_exam = ROOT / "tools" / "check_exam_json.py"
    layout_rc = _run([sys.executable, str(check_layout), str(layout_path)], log, ROOT)
    render_rc = _run([sys.executable, str(check_render), str(layout_path)], log, ROOT)
    exam_rc = _run([sys.executable, str(check_exam), str(layout_path)], log, ROOT)
    metrics = _quality_summary(layout, exam, quality)
    failures = _judge(metrics, layout_rc, render_rc, exam_rc, thresholds)
    return {
        "input": str(layout_path),
        "layout": str(layout_path),
        "exam": str(exam_path),
        "quality": str(quality_path),
        "metrics": metrics,
        "failures": failures,
        "pass": not failures,
    }


def run_layout(pdf: Path, out_dir: Path, log: Path) -> tuple[Path | None, int]:
    layout_path = out_dir / f"{_slug(pdf)}.json"
    pipeline = ROOT / "src" / "ocr_pipeline_layout.py"
    rc = _run([sys.executable, str(pipeline), "--pdf", str(pdf),
               "--out", str(layout_path), "--vote", "1", "--workers", "16"], log, ROOT)
    return (layout_path if rc == 0 and layout_path.exists() else None), rc


def _aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(results)
    if not n:
        return {"exams": 0, "passed": 0, "failed": 0}
    def mean(key: str) -> float:
        return sum(r["metrics"][key] for r in results) / n
    return {
        "exams": n,
        "passed": sum(1 for r in results if r["pass"]),
        "failed": sum(1 for r in results if not r["pass"]),
        "mean_coverage": round(mean("coverage"), 4),
        "mean_formula_pass_rate": round(mean("formula_pass_rate"), 4),
        "mean_table_fill_rate": round(mean("table_fill_rate"), 4),
        "mean_manual_per_page": round(mean("manual_per_page"), 3),
        "total_questions": sum(r["metrics"]["questions"] for r in results),
        "total_options": sum(r["metrics"]["options_actual"] for r in results),
        "total_figures": sum(r["metrics"].get("figures", 0) for r in results),
    }


def write_report(out_dir: Path, results: list[dict[str, Any]],
                 thresholds: dict[str, float], elapsed: float) -> dict[str, Any]:
    agg = _aggregate(results)
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_s": round(elapsed, 1),
        "thresholds": thresholds,
        "summary": agg,
        "exams": results,
    }
    (out_dir / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = [
        "# 试卷验收汇总",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 试卷数：{agg['exams']}，通过 {agg['passed']}，失败 {agg['failed']}",
        f"- 平均覆盖率：{agg['mean_coverage']:.2%}",
        f"- 平均公式结构通过率：{agg['mean_formula_pass_rate']:.2%}",
        f"- 平均表格单元格填充率：{agg['mean_table_fill_rate']:.2%}",
        f"- 平均人工动作/页：{agg['mean_manual_per_page']:.2f}",
        "",
        "| 试卷 | 页 | 行 | 题 | 选项 | 覆盖率 | 公式 | 表格 | 人工/页 | 结果 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in results:
        m = r["metrics"]
        lines.append(
            f"| {Path(r['input']).name} | {m['pages']} | {m['lines']} | {m['questions']} | "
            f"{m['options_actual']}/{m['options_expected']} | {m['coverage']:.2%} | "
            f"{m['formula_pass_rate']:.2%} | {m['table_fill_rate']:.2%} | "
            f"{m['manual_per_page']:.2f} | {'PASS' if r['pass'] else 'FAIL: ' + ','.join(r['failures'])} |"
        )
    lines += ["", f"阈值：覆盖率 ≥ {thresholds['coverage']:.0%}，公式 ≥ {thresholds['formula']:.0%}，"
              f"表格 ≥ {thresholds['table']:.0%}，人工动作/页 ≤ {thresholds['manual_per_page']:.1f}。",
              "表格指标是“非空单元格填充率”，不是与人工标注对比的 F1。"]
    (out_dir / "acceptance.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量试卷验收（PDF 或 layout JSON）")
    parser.add_argument("inputs", nargs="+", help="PDF 文件或已有的 layout JSON")
    parser.add_argument("--out-dir", default=str(ROOT / "out" / "acceptance" / "current"))
    parser.add_argument("--reuse", action="store_true", help="输入为 layout JSON 时直接复用")
    parser.add_argument("--host", default="http://127.0.0.1:8080")
    parser.add_argument("--min-coverage", type=float, default=DEFAULT_THRESHOLDS["coverage"])
    parser.add_argument("--min-formula", type=float, default=DEFAULT_THRESHOLDS["formula"])
    parser.add_argument("--min-table", type=float, default=DEFAULT_THRESHOLDS["table"])
    parser.add_argument("--max-manual-per-page", type=float,
                        default=DEFAULT_THRESHOLDS["manual_per_page"])
    args = parser.parse_args(argv)
    thresholds = {
        "coverage": args.min_coverage,
        "formula": args.min_formula,
        "table": args.min_table,
        "manual_per_page": args.max_manual_per_page,
    }
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    inputs = [Path(item).expanduser().resolve() for item in args.inputs]
    missing = [str(p) for p in inputs if not p.exists()]
    if missing:
        print("输入不存在：" + ", ".join(missing), file=sys.stderr)
        return 3

    needs_ocr = any(p.suffix.lower() == ".pdf" for p in inputs)
    if needs_ocr and not args.reuse and not _server_alive(args.host):
        print(f"OCR 服务不可用：{args.host}；先启动 PaddleOCR-VL，或改用 --reuse 评估已有 JSON。",
              file=sys.stderr)
        return 2

    started = time.time()
    results: list[dict[str, Any]] = []
    for path in inputs:
        log = out_dir / f"{_slug(path)}.log"
        layout_path = path
        if path.suffix.lower() == ".pdf":
            print(f"[accept] OCR {path.name}", flush=True)
            layout_path, rc = run_layout(path, out_dir, log)
            if layout_path is None:
                results.append({
                    "input": str(path), "layout": None, "exam": None, "quality": None,
                    "metrics": {"pages": 0, "lines": 0, "questions": 0, "figures": 0,
                                "sections": 0,
                                "choice_questions": 0, "options_expected": 0,
                                "options_actual": 0, "options_complete": False,
                                "formula_count": 0, "formula_broken": 0,
                                "formula_pass_rate": 0.0, "table_cells": 0,
                                "table_filled": 0, "table_fill_rate": 0.0,
                                "coverage": 0.0, "manual_actions": 0,
                                "manual_per_page": 0.0, "quality_status": "error",
                                "errors": 1, "warnings": 0},
                    "failures": [f"layout_pipeline_exit_{rc}"], "pass": False,
                })
                continue
        result = evaluate_layout(layout_path, out_dir, thresholds, log)
        results.append(result)
        print(f"[accept] {'PASS' if result['pass'] else 'FAIL'} {path.name}: "
              f"{result['metrics']['questions']} 题, 覆盖率 {result['metrics']['coverage']:.2%}",
              flush=True)

    report = write_report(out_dir, results, thresholds, time.time() - started)
    print(f"[accept] 汇总：{report['summary']['passed']}/{report['summary']['exams']} 通过")
    print(f"[accept] 报告：{out_dir / 'acceptance.md'}")
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
