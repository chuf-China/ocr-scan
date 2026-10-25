#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_layout_json.py —— 校验「原版式」JSON：页面/行结构、坐标合法性、幻觉残留。

与 `check_ocr_json.py`（块结构）互补，本脚本针对 `src/ocr_pipeline_layout.py` 的输出。

用法：
    python tools/check_layout_json.py out/paper.layout.json
    python tools/check_layout_json.py out/paper.layout.json --expect-pages 1-2
    python tools/check_layout_json.py out/paper.layout.json --max-lines 120

退出码：0 通过；1 有错误。
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
from layout_regions import region_stats as summarize_regions  # noqa: E402
from layout_regions import validate_regions  # noqa: E402
from formula_review import validate_formula_evidence  # noqa: E402
from table_review import validate_table_evidence  # noqa: E402

DIGIT_RUN_RE = re.compile(r"\d{20,}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--expect-pages", default=None,
                    help="默认按实际页数校验；需要固定范围时写 1-6")
    ap.add_argument("--max-lines", type=int, default=120,
                    help="单页行数上限（A3 三栏数学卷实测 79 行）")
    args = ap.parse_args()

    with open(args.json_path, encoding="utf-8") as fh:
        doc = json.load(fh)

    errors: list[str] = []
    warnings: list[str] = []

    for key in ("document", "stats", "pages"):
        if key not in doc:
            errors.append(f"缺少顶层字段 {key}")
    if doc.get("schema_version") not in (None, 1):
        errors.append(f"未知 schema_version：{doc.get('schema_version')}")
    if errors:
        print("\n".join("✗ " + e for e in errors))
        return 1

    pages = doc["pages"]
    if not pages:
        errors.append("pages 为空")

    m = re.fullmatch(r"(\d+)-(\d+)", args.expect_pages or "")
    lo, hi = (int(m.group(1)), int(m.group(2))) if m else (1, len(pages))
    got = [p.get("page") for p in pages]
    if got != sorted(got):
        errors.append(f"页序不是升序：{got}")
    if set(got) != set(range(lo, hi + 1)):
        errors.append(f"页号应为 {lo}..{hi}，实际 {got}")

    total_lines = 0
    for p in pages:
        pno = p.get("page")
        w, h = p.get("width"), p.get("height")
        if not w or not h:
            errors.append(f"第 {pno} 页缺少页面尺寸")
            continue
        lines = p.get("lines") or []
        total_lines += len(lines)
        if not lines:
            warnings.append(f"第 {pno} 页没有任何行")
        if len(lines) > args.max_lines:
            errors.append(f"第 {pno} 页行数异常（{len(lines)} > {args.max_lines}）")

        tops = [l.get("top") for l in lines]
        if tops != sorted(tops):
            errors.append(f"第 {pno} 页行序不是自上而下")

        for i, l in enumerate(lines):
            tag = f"第 {pno} 页第 {i+1} 行"
            t = (l.get("text") or "").strip()
            if not t:
                errors.append(f"{tag} 文本为空")
            # 坐标必须在页内
            for k, limit in (("top", h), ("bottom", h), ("left", w), ("right", w)):
                v = l.get(k)
                if v is None or not (-2 <= v <= limit + 2):
                    errors.append(f"{tag} 坐标 {k}={v} 越出页面（{limit}）")
            if l.get("bottom", 0) < l.get("top", 0):
                errors.append(f"{tag} bottom < top")
            if l.get("right", 0) < l.get("left", 0):
                errors.append(f"{tag} right < left")
            size = l.get("size_pt")
            if not size or not (3 <= size <= 40):
                errors.append(f"{tag} 字号异常：{size}")
            # 幻觉残留
            if DIGIT_RUN_RE.search(t):
                errors.append(f"{tag} 仍含超长数字串（幻觉残留）")
            if len(t) > 400:
                errors.append(f"{tag} 文本异常长（{len(t)} 字符）")
            if re.search(r"<table\b|<img\b", t, re.I):
                warnings.append(f"{tag} 仍含 HTML 占位符")

    # Schema v1: stable IDs, column metadata, and reading order are optional on
    # historical artifacts but must be self-consistent when present.
    for p in pages:
        pno = p.get("page")
        lines = p.get("lines") or []
        ids = [l.get("id") for l in lines if l.get("id")]
        if ids and len(ids) != len(set(ids)):
            errors.append(f"第 {pno} 页行 ID 重复")
        order = p.get("reading_order")
        if order:
            known = {l.get("id") for l in lines if l.get("id")}
            if len(order) != len(known) or set(order) != known:
                errors.append(f"第 {pno} 页 reading_order 与行 ID 不一致")
        cols = p.get("columns") or []
        if cols:
            prev_right = -1.0
            for col in cols:
                if not isinstance(col, dict):
                    errors.append(f"第 {pno} 页 columns 项不是对象")
                    continue
                left, right = col.get("left"), col.get("right")
                if left is None or right is None or left >= right:
                    errors.append(f"第 {pno} 页 column 区间非法：{col}")
                elif left < prev_right - 0.5:
                    errors.append(f"第 {pno} 页 columns 未按 x 升序")
                prev_right = right if right is not None else prev_right
        for line in lines:
            if line.get("column") is not None and cols:
                if not (0 <= int(line["column"]) < len(cols)):
                    errors.append(f"第 {pno} 页行 column 越界：{line.get('id')}")
        ptype = p.get("page_type")
        if ptype:
            if ptype.get("kind") not in ("blank", "no-lines", "text"):
                errors.append(f"第 {pno} 页未知 page_type.kind：{ptype.get('kind')}")
            if ptype.get("rotation") not in (0, 90, 180, 270, None):
                errors.append(f"第 {pno} 页 rotation 非 0/90/180/270：{ptype.get('rotation')}")
        cov = p.get("coverage")
        if cov:
            ratio = cov.get("ratio")
            if not isinstance(ratio, (int, float)) or not (0 <= ratio <= 1):
                errors.append(f"第 {pno} 页 coverage.ratio 非法：{ratio}")
            ink = cov.get("ink_pixels")
            covered = cov.get("covered_ink_pixels")
            if not isinstance(ink, int) or not isinstance(covered, int):
                errors.append(f"第 {pno} 页 coverage 像素计数不是整数")
            elif covered > ink + 1:
                errors.append(f"第 {pno} 页 covered_ink_pixels > ink_pixels")
            regions = cov.get("uncovered_regions") or []
            total_regions = cov.get("uncovered_regions_total")
            if total_regions is not None:
                if not isinstance(total_regions, int) or total_regions < len(regions):
                    errors.append(f"第 {pno} 页 uncovered_regions_total 非法：{total_regions}")
            for reg in regions:
                pt = reg.get("pt") or []
                if len(pt) != 4:
                    errors.append(f"第 {pno} 页 uncovered region 缺 pt")
                    continue
                x0, y0, x1, y1 = pt
                if not (0 <= x0 < x1 <= float(p.get("width") or 0) + 2
                        and 0 <= y0 < y1 <= float(p.get("height") or 0) + 2):
                    errors.append(f"第 {pno} 页 uncovered region 越出页面：{pt}")
    for p in pages:
        pno = p.get("page")
        fig_ids = [f.get("id") for f in (p.get("figures") or []) if f.get("id")]
        if fig_ids and len(fig_ids) != len(set(fig_ids)):
            errors.append(f"第 {pno} 页图 ID 重复")

    stats = doc.get("stats") or {}
    if stats.get("lines") != total_lines:
        errors.append(f"stats.lines={stats.get('lines')} 与实际 {total_lines} 不符")
    if stats.get("pages") != len(pages):
        errors.append(f"stats.pages={stats.get('pages')} 与实际 {len(pages)} 不符")
    pages_total = (doc.get("document") or {}).get("pages_total")
    if pages_total is not None:
        if not isinstance(pages_total, int) or pages_total < len(pages):
            errors.append(f"document.pages_total={pages_total} 小于已处理页数 {len(pages)}")

    # 区域层（T1）：存在才校验。历史产物没有 regions 时跳过；新产物必须干净，
    # 所以不一致一律 error（否则区域回链会静默退化，下游又只能靠坐标猜）。
    has_regions = any(isinstance(p.get("regions"), list) and p.get("regions")
                      for p in pages)
    if has_regions:
        for problem in validate_regions(pages):
            where = (f"第 {problem.get('page')} 页区域 {problem['region_id']}"
                     if problem.get("region_id") else f"第 {problem.get('page')} 页区域")
            errors.append(f"{where} {problem['code']}：{problem['message']}")
        actual_regions = summarize_regions(pages)
        declared = stats.get("regions")
        if declared is not None and declared != actual_regions["count"]:
            errors.append(f"stats.regions={declared} 与实际区域数 "
                          f"{actual_regions['count']} 不符")
        doc_regions = (doc.get("document") or {}).get("regions")
        if isinstance(doc_regions, dict) and doc_regions.get("count") is not None:
            if doc_regions.get("count") != actual_regions["count"]:
                errors.append(f"document.regions.count={doc_regions.get('count')} "
                              f"与实际区域数 {actual_regions['count']} 不符")

    # 公式复核证据（T2）：存在才校验。证据是**只增不改**的旁证，正文仍以 line.text 为准，
    # 所以这里只查证据块自身（来源/裁片 dpi/一致性枚举/未归属框），并核对汇总计数。
    evidence_total = 0
    for p in pages:
        pno = p.get("page")
        for region in (p.get("regions") or []):
            formulas = region.get("formulas")
            if isinstance(formulas, list):
                evidence_total += len(formulas)
        for problem in validate_formula_evidence([p]):
            where = (f"第 {pno} 页区域 {problem['region_id']}"
                     if problem.get("region_id") else f"第 {pno} 页")
            errors.append(f"{where} 公式证据 {problem['code']}：{problem['message']}")
    if evidence_total:
        declared_blocks = stats.get("formula_review_blocks")
        if declared_blocks is not None and declared_blocks != evidence_total:
            errors.append(f"stats.formula_review_blocks={declared_blocks} 与实际证据数 "
                          f"{evidence_total} 不符")
        review = (doc.get("document") or {}).get("formula_review")
        if isinstance(review, dict) and review.get("formulas") is not None:
            if review["formulas"] != evidence_total:
                errors.append(f"document.formula_review.formulas={review['formulas']} "
                              f"与实际证据数 {evidence_total} 不符")

    # 表格复核证据（T3）：同样存在才校验。表格模型只产旁证，正文 headers/rows_data
    # 不动；这里查证据块自身、逐格计数自洽，并核对汇总计数。
    table_evidence = 0
    for p in pages:
        pno = p.get("page")
        for table in (p.get("tables") or []):
            reviews = table.get("reviews")
            if isinstance(reviews, list):
                table_evidence += len(reviews)
        for problem in validate_table_evidence([p]):
            where = (f"第 {pno} 页表格 {problem['table_id']}"
                     if problem.get("table_id") else f"第 {pno} 页")
            errors.append(f"{where} 表格证据 {problem['code']}：{problem['message']}")
    if table_evidence:
        declared_tables = stats.get("table_review_blocks")
        if declared_tables is not None and declared_tables != table_evidence:
            errors.append(f"stats.table_review_blocks={declared_tables} 与实际证据数 "
                          f"{table_evidence} 不符")
        table_review = (doc.get("document") or {}).get("table_review")
        if isinstance(table_review, dict) and table_review.get("blocks") is not None:
            if table_review["blocks"] != table_evidence:
                errors.append(f"document.table_review.blocks={table_review['blocks']} "
                              f"与实际证据数 {table_evidence} 不符")

    # 插图：坐标必须落在页内，载图必须能解码且非空白
    fig_total = 0
    for p in pages:
        pno = p.get("page")
        w, h = p.get("width"), p.get("height")
        for fig in (p.get("figures") or []):
            fig_total += 1
            tag = f"第 {pno} 页插图 {fig.get('index')}"
            x0, y0, x1, y1 = fig.get("pt") or [None] * 4
            if None in (x0, y0, x1, y1):
                errors.append(f"{tag} 缺少 pt 坐标")
                continue
            if not (0 <= x0 < x1 <= w + 2 and 0 <= y0 < y1 <= h + 2):
                errors.append(f"{tag} 坐标越出页面：({x0},{y0})-({x1},{y1})")
            b64 = fig.get("png_base64")
            if b64 is None:
                warnings.append(f"{tag} 未内嵌图片（--no-images）")
                continue
            try:
                raw = base64.b64decode(b64, validate=True)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{tag} base64 解码失败：{exc}")
                continue
            if len(raw) < 500:
                errors.append(f"{tag} 图片过小（{len(raw)} 字节），可能是空白")
            if not raw.startswith(b"\x89PNG"):
                errors.append(f"{tag} 不是 PNG")
    if stats.get("figures") is not None and stats["figures"] != fig_total:
        errors.append(f"stats.figures={stats['figures']} 与实际 {fig_total} 不符")

    print(f"文件：{args.json_path}")
    print(f"页数：{len(pages)}  行数：{total_lines}  含公式：{stats.get('lines_with_math')}  "
          f"插图：{fig_total}")
    print(f"页尺寸：{pages[0].get('width')} x {pages[0].get('height')} pt  "
          f"dpi：{doc['document'].get('dpi')}")
    print("每页行数：" + "  ".join(f"p{p['page']}={len(p['lines'])}" for p in pages))
    print("每页插图：" + "  ".join(f"p{p['page']}={len(p.get('figures') or [])}" for p in pages))
    for w in list(doc["document"].get("warnings") or []) + warnings:
        print("⚠ " + w)
    for e in errors:
        print("✗ " + e)
    if errors:
        print(f"\n结果：失败（{len(errors)} 个错误）")
        return 1
    print("\n结果：通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
