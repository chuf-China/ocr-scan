#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""import_paddleocr_blocks.py —— 把**官方 PaddleOCR-VL 的记录**转成本项目的 blocks JSON。

为什么要这一层（与 `import_paddleocr_json.py` 的区别）：

  · `import_paddleocr_json.py` 走"**绝对定位画布**"：每条文本要有自己的行盒，
    于是必须把官方按**块**给出的内容再折行、估字号、分配行位。这条路在一份
    155 个公式、块高从 13pt 到 132pt 不等的卷子上天然吃力——实测为了让"一个块"
    塞进"一个行盒"，字号被压到 8.9pt、还残留 1 处叠字。
  · 本脚本走"**文档流**"：官方 `parsing_res_list` 的 `block_label` 本来就是
    标题/段落/表格/公式的分类，直接映射成块，交给 `viewer.html` 按流式排版。
    不折行、不估字号、不分配行位——**没有那些失真的来源**。

官方给的两个东西都用上：
  · `parsing_res_list[].block_label` → 块类型（doc_title→heading、table→table…）
  · `parsing_res_list[].block_content` → 块文本（已是 Markdown/LaTeX/HTML 表格）

用法：
    python tools/import_paddleocr_blocks.py <dir_or_res.json> --out out/paper.ocr.paddleocrvl16.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BOOT)
import paths  # noqa: E402
import ocr_pipeline_blocks as B  # noqa: E402

LABEL_TO_TYPE = {
    "doc_title": ("heading", 1),
    "paragraph_title": ("heading", 2),
    "title": ("heading", 2),
    "text": ("paragraph", None),
    "abstract": ("paragraph", None),
    "reference": ("paragraph", None),
    "footnote": ("paragraph", None),
    "aside_text": ("paragraph", None),
    "number": ("paragraph", None),
    "header": ("paragraph", None),
    "footer": ("paragraph", None),
    "table": ("table", None),
    "formula": ("formula", None),
    "image": ("figure", None),
    "chart": ("figure", None),
    "figure": ("figure", None),
    "seal": ("figure", None),
}

QUESTION_RE = re.compile(r"^\s*(\d{1,3})\s*[.、．)）]\s*(?=\S)")
IMG_RE = re.compile(r'<img\b[^>]*src="([^"]+)"[^>]*>', re.I)


def blocks_from_record(res: dict, page_no: int) -> list[dict]:
    """一份 *_res.json → 该页的块列表（直接映射，不做正则猜测）。"""
    out: list[dict] = []
    for raw_i, blk in enumerate(res.get("parsing_res_list") or []):
        label = (blk.get("block_label") or "").strip()
        content = (blk.get("block_content") or "").strip()
        btype, level = LABEL_TO_TYPE.get(label, ("paragraph", None))
        bbox = blk.get("block_bbox") or []
        entry: dict = {"page": page_no, "type": btype, "label": label,
                       "order": blk.get("block_order"), "_raw_index": raw_i}
        if bbox:
            entry["bbox_px"] = [round(float(v), 1) for v in bbox[:4]]
        if btype == "heading":
            entry["level"] = level or 2
            entry["text"] = content
        elif btype == "table":
            table = B.parse_table(content) if "<table" in content.lower() else {"headers": [], "rows": []}
            if not (table["headers"] or table["rows"]):
                # 形状不可信的 HTML 表：不收录，退回纯文本（与本项目 _table_shape_ok 同思路）
                entry["type"] = "paragraph"
                entry["text"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", content)).strip()
            else:
                entry.update(table)
        elif btype == "figure":
            # blocks 这种消费形态只有 heading/question/paragraph/table 四类
            # （见 tools/check_ocr_json.py 的 valid_types），图不单独成类：
            # 记下 Markdown 引用的图片路径，正文留一个占位。
            imgs = IMG_RE.findall(content)
            entry["type"] = "paragraph"
            entry["image"] = imgs[0] if imgs else None
            entry["text"] = "（插图）"
        else:
            # 题干里的题号在**版面标签**为 text 时仍要抽出来，方便建题库
            m = QUESTION_RE.match(content)
            if m:
                entry["type"] = "question"
                entry["number"] = int(m.group(1))
                entry["text"] = content
            else:
                entry["text"] = content
        out.append(entry)
    # 阅读顺序：block_order 缺失的块（image/table/header… 官方给 None，实测 46 块里 5 块）
    # 退回 parsing_res_list 的原始次序，而不是甩到最后
    out.sort(key=lambda b: (b["order"] if b.get("order") is not None
                            else b.get("_raw_index", 10**6),
                            (b.get("bbox_px") or [0, 0])[1]))
    for b in out:
        b.pop("_raw_index", None)
        b.pop("order", None)
    return out


def collect(arg: str) -> list[str]:
    if os.path.isdir(arg):
        files = [os.path.join(arg, f) for f in sorted(os.listdir(arg))
                 if f.endswith("_res.json")]
        if not files:
            raise SystemExit(f"[blocks] 目录里没有 *_res.json：{arg}")
        return files
    if not os.path.isfile(arg):
        raise SystemExit(f"[blocks] 找不到文件：{arg}")
    return [arg]


def main() -> int:
    ap = argparse.ArgumentParser(description="官方 PaddleOCR-VL → 本项目 blocks JSON")
    ap.add_argument("input")
    ap.add_argument("--out", default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--viewer", action="store_true", help="顺带写出 viewer.html")
    args = ap.parse_args()

    files = collect(args.input)
    pages: list[dict] = []
    for i, path in enumerate(files):
        res = json.load(open(path, encoding="utf-8"))
        px_w = int(res.get("width") or 0)
        px_h = int(res.get("height") or 0)
        pages.append({
            "page": i + 1,
            "width": round(px_w / (150 / 72), 2),
            "height": round(px_h / (150 / 72), 2),
            "render_px": [px_w, px_h],
            "blocks": blocks_from_record(res, i + 1),
            "markdown": "\n\n".join(
                (b.get("text") or "") for b in blocks_from_record(res, i + 1)),
        })

    all_blocks = [b for p in pages for b in p["blocks"]]
    qnums = [b["number"] for b in all_blocks if b.get("number") is not None]
    payload = {
        "document": {
            "title": args.title or "PaddleOCR-VL 文档解析",
            "source_pdf": os.path.abspath(args.input),
            "pages_total": len(pages),
            "pages_processed": len(pages),
            "engine": "paddleocr-vl-1.6",
            "dpi": 150,
            "pipeline": "PP-DocLayoutV3 + PaddleOCR-VL-1.6-0.9B（块级/流式）",
            "warnings": [],
        },
        "stats": {
            "blocks": len(all_blocks),
            "questions": len(qnums),
            "question_numbers": qnums,
        },
        "pages": pages,
    }
    out = args.out or os.path.join(paths.OUT_DIR, "paper.ocr.paddleocrvl16.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print(f"[blocks] 写出 {out}")
    print(f"[blocks] {len(pages)} 页 / {len(all_blocks)} 块 / {len(qnums)} 道题号 {qnums}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
