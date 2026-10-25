#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语料级缺陷审计：把每个 issue 归到「六类现象 × 责任方」并汇总。

为什么单独一个工具：
    单卷的 quality.json 只说明"这卷哪条 issue"，不说明"这批语料里谁该干活"。
    按现象直接修会来回打补丁；这个工具把 28 套语料一起算，给出一张
    「程序多少条 / 模型多少条 / 输入多少条」的表，修复顺序按它排。

    **只读**：不跑 OCR、不跑公式/表格模型、不改任何 JSON。输入是已落盘的
    layout JSON（含旧产物，缺 regions 会就地派生到内存，不写回）。

用法（在 ocr_json_app/ 下）：
    python tools/audit_defects.py out/acceptance/native_final3/*.layout.json out/eval/*.layout.json
    python tools/audit_defects.py --json out/audit.json out/**/*.layout.json

退出码：0 = 审计完成（即使有 fail 也是 0，审计不是门禁）；2 = 输入为空。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.dont_write_bytecode = True

import defect_taxonomy as dt  # noqa: E402
import exam_structure as es  # noqa: E402


def iter_inputs(patterns: list[str]) -> list[str]:
    """展开参数里的通配符（Windows 的 shell 不展开），去重保序。"""
    out: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        for path in sorted(glob.glob(pattern)) or [pattern]:
            real = os.path.abspath(path)
            if real in seen or not real.endswith(".json") or not os.path.isfile(real):
                continue
            seen.add(real)
            out.append(real)
    return out


def audit(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as fh:
            layout = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"  跳过 {os.path.basename(path)}：{exc}")
        return None
    if not layout.get("pages"):
        return None  # acceptance.json 之类的汇总文件不是 layout
    exam = es.build_exam(layout, source=path)
    quality = es.build_quality(layout, exam)
    summary = quality.get("summary") or {}
    return {
        "name": os.path.basename(path),
        "pages": len(layout.get("pages") or []),
        "status": summary.get("status"),
        "errors": summary.get("errors", 0),
        "warnings": summary.get("warnings", 0),
        "defects": summary.get("defects") or {},
        "issues": quality.get("issues") or [],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按「六类现象 × 责任方」审计一批 layout JSON（只读、不跑模型）")
    parser.add_argument("inputs", nargs="+", help="layout JSON 路径或通配符")
    parser.add_argument("--json", dest="json_out", help="把汇总与逐卷明细写到该文件")
    parser.add_argument("--top", type=int, default=12, help="每种责任方列出前 N 个 code")
    args = parser.parse_args()

    paths = iter_inputs(args.inputs)
    if not paths:
        print("没有可审计的 layout JSON")
        return 2

    rows = [r for r in (audit(p) for p in paths) if r]
    totals = {k: 0 for k in list(dt.OWNERS) + ["mixed"]}
    by_class_total: dict[str, dict[str, int]] = {}
    code_owner: dict[str, dict] = {}
    status_count: dict[str, int] = {}
    unknown: dict[str, int] = {}

    for row in rows:
        status_count[row["status"]] = status_count.get(row["status"], 0) + 1
        defects = row["defects"]
        for owner, count in (defects.get("by_owner") or {}).items():
            totals[owner] = totals.get(owner, 0) + int(count or 0)
        for klass, bucket in (defects.get("by_class") or {}).items():
            slot = by_class_total.setdefault(
                klass, {k: 0 for k in list(dt.OWNERS) + ["mixed"]})
            for owner, count in (bucket.get("by_owner") or {}).items():
                slot[owner] = slot.get(owner, 0) + int(count or 0)
        for code in defects.get("unknown") or []:
            unknown[code] = unknown.get(code, 0) + 1
        for issue in row["issues"]:
            info = dt.code_info(str(issue.get("code") or ""))
            if not info:
                continue
            slot = code_owner.setdefault(info["code"], {
                "class": info["class"], "owner": info["owner"],
                "layer": info["layer"], "count": 0, "papers": 0})
            slot["count"] += 1

    # 每个 code 出现在几套卷里（一次修完覆盖多少卷）
    for row in rows:
        seen = {str(i.get("code") or "") for i in row["issues"]}
        for code in seen:
            if code in code_owner:
                code_owner[code]["papers"] += 1

    print("=" * 78)
    print(f"语料级缺陷审计：{len(rows)} 套 layout")
    print("=" * 78)
    print("状态：" + "，".join(f"{k} {v}" for k, v in sorted(status_count.items())))
    print(f"\n责任方合计：程序 {totals['program']}，模型 {totals['model']}，"
          f"输入 {totals['input']}，待判定 {totals['mixed']}")

    print("\n按现象")
    print(f"{'现象':<8}{'程序':>6}{'模型':>6}{'输入':>6}{'待判':>6}")
    for klass, meta in dt.DEFECT_CLASSES.items():
        slot = by_class_total.get(klass)
        if not slot:
            continue
        print(f"{meta['label']:<8}{slot['program']:>6}{slot['model']:>6}"
              f"{slot['input']:>6}{slot['mixed']:>6}")

    for owner in ("program", "model", "input", "mixed"):
        items = sorted((c for c in code_owner.values() if c["owner"] == owner),
                       key=lambda c: -c["count"])[:args.top]
        if not items:
            continue
        label = dt.OWNERS.get(owner, {"label": "待判定"})["label"]
        print(f"\n{label}（前 {len(items)} 个 code）")
        for item in items:
            code = next(c for c, v in code_owner.items() if v is item)
            print(f"  {item['count']:>4} 次 / {item['papers']:>2} 套  {code:<28}"
                  f"{dt.DEFECT_CLASSES[item['class']]['label']} · {dt.LAYERS[item['layer']]}")

    if unknown:
        print("\n未登记 code（需补 defect_taxonomy.ISSUE_CODES）：")
        for code, count in sorted(unknown.items(), key=lambda kv: -kv[1]):
            print(f"  {count:>4} 次  {code}")

    if args.json_out:
        payload = {
            "papers": len(rows),
            "status": status_count,
            "totals": totals,
            "by_class": by_class_total,
            "codes": code_owner,
            "unknown": unknown,
            "rows": [{k: v for k, v in row.items() if k != "issues"} for row in rows],
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        print(f"\n写出 {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
