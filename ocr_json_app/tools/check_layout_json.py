#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_layout_json.py —— 校验「原版式」JSON：页面/行结构、坐标合法性、幻觉残留。

与 `check_ocr_json.py`（块结构）互补，本脚本针对 `ocr_pdf_layout.py` 的输出。

用法：
    python check_layout_json.py out/paper.layout.json
    python check_layout_json.py out/paper.layout.json --expect-lines 1-40

退出码：0 通过；1 有错误。
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys

DIGIT_RUN_RE = re.compile(r"\d{20,}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--expect-pages", default="1-6")
    ap.add_argument("--max-lines", type=int, default=60, help="单页行数上限（防炸裂）")
    args = ap.parse_args()

    with open(args.json_path, encoding="utf-8") as fh:
        doc = json.load(fh)

    errors: list[str] = []
    warnings: list[str] = []

    for key in ("document", "stats", "pages"):
        if key not in doc:
            errors.append(f"缺少顶层字段 {key}")
    if errors:
        print("\n".join("✗ " + e for e in errors))
        return 1

    pages = doc["pages"]
    if not pages:
        errors.append("pages 为空")

    m = re.fullmatch(r"(\d+)-(\d+)", args.expect_pages)
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

    stats = doc.get("stats") or {}
    if stats.get("lines") != total_lines:
        errors.append(f"stats.lines={stats.get('lines')} 与实际 {total_lines} 不符")
    if stats.get("pages") != len(pages):
        errors.append(f"stats.pages={stats.get('pages')} 与实际 {len(pages)} 不符")

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
