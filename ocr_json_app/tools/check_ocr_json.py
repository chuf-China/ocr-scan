#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_ocr_json.py —— 校验 OCR 产出的 JSON：结构、页码、题号连续性、截断、乱码。

用法：
    python check_ocr_json.py out/paper.ocr.json
    python check_ocr_json.py out/paper.ocr.json --expect-questions 1-21

退出码：0 全部通过（可能有警告）；1 有错误。
"""

from __future__ import annotations

import argparse
import json
import re
import sys

REQUIRED_DOC = ("title", "source_pdf", "pages_total", "pages_processed", "engine", "dpi")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--expect-questions", default="1-21", help="期望题号范围，如 1-21")
    args = ap.parse_args()

    with open(args.json_path, encoding="utf-8") as fh:
        doc = json.load(fh)

    errors: list[str] = []
    warnings: list[str] = []

    # 1) 顶层结构
    for key in ("document", "stats", "pages"):
        if key not in doc:
            errors.append(f"缺少顶层字段 {key}")
    if errors:
        print("\n".join("✗ " + e for e in errors))
        return 1

    for key in REQUIRED_DOC:
        if key not in doc["document"]:
            errors.append(f"document 缺少字段 {key}")

    pages = doc["pages"]
    if not pages:
        errors.append("pages 为空")
    if doc["document"].get("pages_processed") != len(pages):
        errors.append("pages_processed 与实际页数不一致")

    # 2) 页码连续且唯一
    numbers = [p.get("page") for p in pages]
    if numbers != sorted(numbers):
        errors.append(f"页码未排序：{numbers}")
    if len(set(numbers)) != len(numbers):
        errors.append(f"页码重复：{numbers}")
    expected_pages = doc["document"].get("pages_total")
    if expected_pages and max(numbers) > expected_pages:
        errors.append(f"页码超出 pages_total={expected_pages}")

    # 3) 每页必须有正文；检查截断
    valid_types = {"heading", "question", "paragraph", "table"}
    for p in pages:
        pno = p.get("page")
        if not (p.get("markdown") or "").strip():
            errors.append(f"第 {pno} 页 markdown 为空")
        if not p.get("blocks"):
            errors.append(f"第 {pno} 页 blocks 为空")
        if p.get("finish_reason") == "length":
            errors.append(f"第 {pno} 页被截断（finish_reason=length）")
        for b in p.get("blocks", []):
            if b.get("type") not in valid_types:
                errors.append(f"第 {pno} 页出现未知块类型 {b.get('type')!r}")
            if b.get("type") == "question" and not isinstance(b.get("number"), int):
                errors.append(f"第 {pno} 页 question 块缺少数字题号")
            if b.get("type") == "table" and not (b.get("headers") or b.get("rows")):
                errors.append(f"第 {pno} 页 table 块为空")

    # 4) 题号覆盖
    qnums: list[int] = []
    for p in pages:
        qnums += [b["number"] for b in p.get("blocks", [])
                  if b.get("type") == "question" and isinstance(b.get("number"), int)]
    m = re.fullmatch(r"(\d+)-(\d+)", args.expect_questions)
    lo, hi = (int(m.group(1)), int(m.group(2))) if m else (1, 21)
    expected = set(range(lo, hi + 1))
    missing = sorted(expected - set(qnums))
    dupes = sorted({n for n in qnums if qnums.count(n) > 1})
    if missing:
        errors.append(f"缺少题号：{missing}")
    if dupes:
        warnings.append(f"题号重复：{dupes}")
    extra = sorted(set(qnums) - expected)
    if extra:
        warnings.append(f"出现范围外题号：{extra}")

    # 5) 乱码 / 替换字符
    all_text = "\n".join(p.get("markdown", "") for p in pages)
    if "\ufffd" in all_text:
        errors.append("正文含 U+FFFD 替换字符（乱码）")
    cid = re.findall(r"\(cid:\d+\)", all_text)
    if cid:
        errors.append(f"正文含 cid 占位符 {cid[:5]}")

    # 6) 数学保真抽查：关键 LaTeX 片段是否还在
    spot = {
        r"\frac": "分式",
        r"\sqrt": "根号",
        r"\overrightarrow": "向量",
        r"\log": "对数",
        r"\sin": "三角函数",
        r"\mathbb": "空心字体",
    }
    for token, label in spot.items():
        if token not in all_text:
            warnings.append(f"未检测到 {label}（{token}）——请人工确认对应页面")

    print(f"文件：{args.json_path}")
    print(f"页数：{len(pages)}/{expected_pages}  块：{doc['stats'].get('blocks')}  "
          f"题号：{sorted(set(qnums))}")
    print(f"引擎：{doc['document'].get('engine')}  dpi：{doc['document'].get('dpi')}  "
          f"耗时：{doc['document'].get('elapsed_s')}s")
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
