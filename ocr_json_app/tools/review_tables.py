#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""表格复核工具（T3）：用 PP-StructureV3 的表格子流水线复核现有表格。

为什么不让流水线默认跑：表格模型在空单元格、跨格合并上比"框线 + 逐格 OCR"
强，但它也会把本来正确的格子读错。直接替换正文会把安静的错误放大，所以这里
只产证据（`src/table_review.py` 的证据块），正文一律不动。

为什么要裁片而不是整页：整页跑 `pp_structurev3` 会一次加载 14 个模型（含公式、
图表、方向矫正，权重合计约 1.6 GB），在 16 GB 内存的机器上会把系统拖死。
这里只裁表格 bbox、只跑 `table_recognition_v2`（表格分类 + 单元格检测 +
结构识别 + 文本识别），并显式关掉版面检测、方向矫正、去扭曲。

用法（在 ocr_json_app 下）：
    python tools\\review_tables.py out\\eval\\eval01_xicheng_2024ermo.layout.json
    python tools\\review_tables.py <layout.json> --only-issues <quality.json>
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.dont_write_bytecode = True

from table_review import (  # noqa: E402
    TABLE_EVIDENCE_SOURCE,
    attach_table_evidence,
    build_table_evidence,
    native_table_rows,
    table_review_summary,
    validate_table_evidence,
)


def die(message: str, code: int) -> int:
    print("[table-review] %s" % message, file=sys.stderr)
    return code


def default_cli() -> str | None:
    """找 formula-venv 里的 paddleocr 可执行文件（不假设 PATH 里有）。"""
    env = os.environ.get("PADDLEOCR_CLI")
    candidates = [env] if env else []
    candidates.extend(
        [
            "C:/Users/Administrator/Desktop/ocr/formula-venv/Scripts/paddleocr.exe",
            os.path.join(os.path.dirname(ROOT), "formula-venv", "Scripts", "paddleocr.exe"),
        ]
    )
    for path in candidates:
        if path and os.path.exists(path):
            return path
    return None


def _targets(pages: list[dict], only_issues: set | None, max_tables: int) -> list[tuple[int, dict]]:
    targets: list[tuple[int, dict]] = []
    for page in pages:
        page_no = page.get("page")
        for table in page.get("tables") or []:
            if only_issues is not None and (page_no, table.get("id")) not in only_issues:
                continue
            if not table.get("bbox_pt"):
                continue
            if max_tables and len(targets) >= max_tables:
                return targets
            targets.append((page_no, table))
    return targets


def _issue_refs(quality: dict) -> tuple[set, set]:
    """从 quality.json 里挑出表格类 issue：返回 (表级, 页级) 两个集合。"""
    table_refs: set = set()
    page_refs: set = set()
    for issue in quality.get("issues") or []:
        code = str(issue.get("code") or "")
        if not code.startswith("table"):
            continue
        ref = issue.get("ref")
        page_no = issue.get("page")
        if isinstance(ref, str) and ref.startswith("p") and "-t" in ref:
            table_refs.add((page_no, ref))
        elif page_no is not None:
            page_refs.add(page_no)
    return table_refs, page_refs


def _render_and_crop(pdf_path: str, targets: list[tuple[int, dict]], dpi: int, pad_pt: float, workdir: str):
    """按页渲染一次、裁出每张表的 PNG；返回 [(table_id, png_path, crop_origin_pt)]。"""
    import pypdfium2 as pdfium  # 延迟导入：--help 与语法检查时不依赖渲染库

    scale = dpi / 72.0
    by_page: dict[int, list] = {}
    for page_no, table in targets:
        by_page.setdefault(page_no, []).append(table)
    crops = []
    pdf = pdfium.PdfDocument(pdf_path)
    try:
        for page_no in sorted(by_page):
            page = pdf[page_no - 1]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            for table in by_page[page_no]:
                x0, y0, x1, y1 = [float(v) for v in table["bbox_pt"]]
                left = int(round(x0 * scale - pad_pt * scale))
                top = int(round(y0 * scale - pad_pt * scale))
                right = int(round(x1 * scale + pad_pt * scale))
                bottom = int(round(y1 * scale + pad_pt * scale))
                left, top = max(0, left), max(0, top)
                right = min(image.width, right)
                bottom = min(image.height, bottom)
                if right <= left or bottom <= top:
                    continue
                name = "%s.png" % table.get("id")
                path = os.path.join(workdir, "crops", name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                image.crop((left, top, right, bottom)).save(path)
                crops.append(
                    (
                        table.get("id"),
                        path,
                        (left * 72.0 / dpi, top * 72.0 / dpi),
                    )
                )
    finally:
        pdf.close()
    return crops


def _run_pipeline(cli: str, crops_dir: str, rec_dir: str, cpu_threads: int, timeout_s: int) -> None:
    cmd = [
        cli,
        "table_recognition_v2",
        "-i",
        crops_dir,
        "--device",
        "cpu",
        "--use_layout_detection",
        "False",
        "--use_doc_orientation_classify",
        "False",
        "--use_doc_unwarping",
        "False",
        "--cpu_threads",
        str(cpu_threads),
        "--save_path",
        rec_dir,
    ]
    env = dict(os.environ)
    env.setdefault("OMP_NUM_THREADS", str(cpu_threads))
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        cmd,
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout_s,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-4:]
        raise RuntimeError("table_recognition_v2 失败（rc=%s）：%s" % (proc.returncode, " | ".join(tail)))


def _load_result(rec_dir: str, table_id: str) -> dict | None:
    for suffix in ("_res.json", ".json"):
        path = os.path.join(rec_dir, "%s%s" % (table_id, suffix))
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            tables = data.get("table_res_list") or []
            return tables[0] if tables else None
    return None


def _cells_payload(result: dict, origin_pt: tuple[float, float], dpi: int, keep: bool) -> tuple[list, str, float | None]:
    html = result.get("pred_html") or ""
    boxes = result.get("cell_box_list") or []
    ocr = result.get("table_ocr_pred") or {}
    texts = ocr.get("rec_texts") or []
    scores = ocr.get("rec_scores") or []
    cells = []
    for index, box in enumerate(boxes):
        try:
            x0, y0, x1, y1 = [float(v) for v in box]
        except (TypeError, ValueError):
            continue
        text = texts[index] if index < len(texts) else ""
        score = float(scores[index]) if index < len(scores) else None
        cells.append(
            {
                "text": text,
                "bbox_pt": [
                    round(origin_pt[0] + x0 * 72.0 / dpi, 2),
                    round(origin_pt[1] + y0 * 72.0 / dpi, 2),
                    round(origin_pt[0] + x1 * 72.0 / dpi, 2),
                    round(origin_pt[1] + y1 * 72.0 / dpi, 2),
                ],
                "score": round(score, 4) if score is not None else None,
            }
        )
    detect_score = None
    for value in scores:
        detect_score = float(value)
        break
    return cells, html, detect_score


def main() -> int:
    parser = argparse.ArgumentParser(description="用表格模型复核现有表格，产出证据（不改正文）")
    parser.add_argument("layout_json", help="版式 JSON（含 pages[].tables）")
    parser.add_argument("--pdf", help="原始 PDF；缺省用 document.source_pdf")
    parser.add_argument("--out", help="输出 JSON，默认 <输入>.tables.json")
    parser.add_argument("--pages", help="只处理这些页码，如 1,3,5")
    parser.add_argument("--crop-dpi", type=int, default=200, help="裁片 dpi（默认 200）")
    parser.add_argument("--pad-pt", type=float, default=2.0, help="裁片外扩 pt（默认 2）")
    parser.add_argument("--cpu-threads", type=int, default=4, help="paddle CPU 线程数（默认 4，压住内存峰值）")
    parser.add_argument("--timeout-s", type=int, default=900, help="子进程超时（默认 900s）")
    parser.add_argument("--cli", help="paddleocr 可执行文件；缺省自动探测 formula-venv")
    parser.add_argument("--only-issues", help="只复核 quality.json 里 table* 类 issue 指向的表格")
    parser.add_argument("--max-tables", type=int, default=0, help="最多复核多少张表（0 = 不限）")
    parser.add_argument("--keep-crops", help="保留裁片目录（默认临时目录用后删除）")
    args = parser.parse_args()

    with open(args.layout_json, "r", encoding="utf-8") as fh:
        layout = json.load(fh)
    pages = layout.get("pages") or []
    if not pages:
        return die("layout 里没有 pages：%s" % args.layout_json, 3)

    from layout_regions import ensure_regions  # noqa: E402 - 与流水线/试卷层共用

    ensure_regions(layout)

    pdf_path = args.pdf or (layout.get("document") or {}).get("source_pdf")
    if not pdf_path or not os.path.exists(pdf_path):
        return die("找不到原始 PDF：%s（用 --pdf 指定）" % pdf_path, 3)

    only_issues = None
    if args.only_issues:
        with open(args.only_issues, "r", encoding="utf-8") as fh:
            quality = json.load(fh)
        table_refs, page_refs = _issue_refs(quality)
        only_issues = set(table_refs)
        wanted_pages = {page for page, _ in table_refs} | page_refs
        if wanted_pages:
            only_issues |= {(page, table.get("id")) for page in wanted_pages for table in _tables_of(pages, page)}
        print("[table-review] 命中表格 issue：表级 %d / 页级 %d" % (len(table_refs), len(page_refs)), flush=True)

    targets = _targets(pages, only_issues, args.max_tables)
    if args.pages:
        wanted = {int(part) for part in args.pages.split(",") if part.strip().isdigit()}
        targets = [item for item in targets if item[0] in wanted]
    if not targets:
        return die("没有可复核的表格（先确认 layout 里有 tables[].bbox_pt）", 4)

    cli = args.cli or default_cli()
    if not cli or not os.path.exists(cli):
        return die("找不到 paddleocr 可执行文件（用 --cli 指定）", 2)

    workdir = args.keep_crops or tempfile.mkdtemp(prefix="table_review_")
    os.makedirs(workdir, exist_ok=True)
    crops_dir = os.path.join(workdir, "crops")
    rec_dir = os.path.join(workdir, "rec")
    os.makedirs(rec_dir, exist_ok=True)

    started = time.time()
    try:
        crops = _render_and_crop(pdf_path, targets, args.crop_dpi, args.pad_pt, workdir)
        if not crops:
            return die("裁片全部失败（检查 bbox_pt 与 PDF 是否同一份）", 4)
        print(
            "[table-review] %d 张表 / %d 个裁片，开始识别（CPU %d 线程）……"
            % (len(targets), len(crops), args.cpu_threads),
            flush=True,
        )
        _run_pipeline(cli, crops_dir, rec_dir, args.cpu_threads, args.timeout_s)
        by_id = {table.get("id"): table for _, table in targets}
        entries = []
        missing = []
        for table_id, _png, origin in crops:
            result = _load_result(rec_dir, str(table_id))
            if not result:
                missing.append(table_id)
                continue
            cells, html, score = _cells_payload(result, origin, args.crop_dpi, bool(args.keep_crops))
            entry = build_table_evidence(
                html,
                cells,
                by_id[table_id].get("bbox_pt"),
                args.crop_dpi,
                structure_model="SLANeXt_wired/SLANet_plus（pipeline 自动选择）",
                text_model="PP-OCRv5_server_rec",
                score=score,
                current_rows=native_table_rows(by_id[table_id]),
            )
            entry["table_id"] = table_id
            entries.append(entry)
    finally:
        if not args.keep_crops:
            shutil.rmtree(workdir, ignore_errors=True)

    if not entries:
        return die("表格模型没有返回任何结果（%d 张全部失败）" % len(crops), 4)

    out_doc = copy.deepcopy(layout)
    out_pages = out_doc.get("pages") or []
    attached = {"attached": 0, "unassigned": 0}
    for page in out_pages:
        page_entries = [entry for entry in entries if entry["table_id"] in {t.get("id") for t in page.get("tables") or []}]
        if not page_entries:
            continue
        result = attach_table_evidence(page, page_entries)
        attached["attached"] += result["attached"]
        attached["unassigned"] += result["unassigned"]

    summary = table_review_summary(out_pages)
    summary.update(
        {
            "source": TABLE_EVIDENCE_SOURCE,
            "pipeline": "table_recognition_v2",
            "crop_dpi": args.crop_dpi,
            "pdf": pdf_path,
            "elapsed_s": round(time.time() - started, 1),
            "problems": validate_table_evidence(out_pages),
            "failed_tables": missing,
        }
    )
    out_doc.setdefault("document", {})["table_review"] = summary
    stats = out_doc.setdefault("stats", {})
    stats["table_review_blocks"] = summary["blocks"]
    stats["table_review_tables"] = summary["tables_reviewed"]

    out_path = args.out or (os.path.splitext(args.layout_json)[0] + ".tables.json")
    if os.path.abspath(out_path) == os.path.abspath(args.layout_json):
        return die("--out 不能等于输入文件", 3)
    parent = os.path.dirname(os.path.abspath(out_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(out_doc, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    print(
        "[table-review] 完成：%d 条证据 / %d 张表；一致性 %s；%d 个未归属；%.1fs → %s"
        % (
            summary["blocks"],
            summary["tables_reviewed"],
            json.dumps(summary["agreement"], ensure_ascii=False),
            summary["unassigned_boxes"],
            summary["elapsed_s"],
            out_path,
        ),
        flush=True,
    )
    return 0


def _tables_of(pages: list[dict], page_no) -> list[dict]:
    for page in pages:
        if page.get("page") == page_no:
            return list(page.get("tables") or [])
    return []


if __name__ == "__main__":
    raise SystemExit(main())
