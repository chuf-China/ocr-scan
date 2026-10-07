#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ocr_ab.py —— 两个本地 VLM-OCR 的量化 A/B：逐页整页 + 易幻觉小图。

量化指标（客观、可复现）：
  chars / cjk      输出规模
  digit_runs       20 位以上连续数字（幻觉数字串的特征）
  repeat_spam      最长重复片段次数（`.\n\n.\n\n.` 这类）
  img_tags         `<img bbox_...>` 占位符数量（插图被识别的次数）
  table_tags       `<table>` / 表格行标记
  latin_in_cjk     数字/逗号串夹在中文里（如 `1,000.001,000.00`）
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import time

import os
import sys

# 工具在 tools/ 或 verify/ 下：把 ../src 加入 sys.path，才能 import 两个流水线与共享层
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
import paths  # noqa: E402
from ocr_common import import_pdfium, ocr_png  # noqa: E402

PDF = paths.DEFAULT_PDF
PROMPT_OVIS = (
    "Extract all readable content from the image in natural human reading order and "
    "output the result as a single Markdown document. For charts or images, represent "
    'them using an HTML image tag: <img src="images/bbox_{left}_{top}_{right}_{bottom}.jpg" />, '
    "where left, top, right, bottom are bounding box coordinates scaled to [0, 1000). "
    "Format formulas as LaTeX. Format tables as HTML: <table>...</table>. "
    "Transcribe all other text as standard Markdown. "
    "Preserve the original text without translation or paraphrasing."
)
PROMPT_PADDLE = "OCR:"

MODELS = {
    "ovisocr2": ("http://127.0.0.1:8081", PROMPT_OVIS),
    "paddleocr-vl": ("http://127.0.0.1:8080", PROMPT_PADDLE),
}

def metrics(text: str) -> dict:
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    body = text.strip()
    longest = 0
    if len(body) > 20:
        for n in (1, 2, 3):
            for i in range(0, min(len(body) - n, 200)):
                window = body[i:i + n]
                if len(window) == n:
                    longest = max(longest, body.count(window))
    return {
        "chars": len(text),
        "cjk": cjk,
        "digit_runs": len(re.findall(r"\d{20,}", text)),
        "repeat_spam": longest if longest > 20 else 0,
        "img_tags": len(re.findall(r"<img\b", text, re.I)),
        "table_tags": len(re.findall(r"<table\b|\|.*\|", text, re.I)),
        "number_gibberish": len(re.findall(r"[\d,]{7,}\d", text)),
    }

def render(pno: int, dpi: int):
    doc = import_pdfium().PdfDocument(PDF)
    return doc[pno - 1].render(scale=dpi / 72).to_pil().convert("RGB")

def call(model: str, png: bytes, timeout: int = 300) -> dict:
    host, prompt = MODELS[model]
    t0 = time.time()
    try:
        r = ocr_png(host, model, png, prompt, timeout, 8192)
        return {"text": r["text"], "finish": r["finish_reason"],
                "elapsed": round(time.time() - t0, 2)}
    except Exception as exc:  # noqa: BLE001
        return {"text": f"<ERROR {exc}>", "finish": "error", "elapsed": round(time.time() - t0, 2)}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=list(MODELS))
    ap.add_argument("--pages", default="1-6")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeats", type=int, default=1)
    args = ap.parse_args()

    a, _, b = args.pages.partition("-")
    pages = list(range(int(a), int(b or a) + 1))
    report = {"model": args.model, "dpi": args.dpi, "pages": []}

    for pno in pages:
        pil = render(pno, args.dpi)
        buf = io.BytesIO(); pil.save(buf, "PNG", optimize=True)
        png = buf.getvalue()
        runs = [call(args.model, png) for _ in range(args.repeats)]
        best = runs[0]
        entry = {"page": pno, "png_kb": len(png) // 1024,
                 "elapsed": best["elapsed"], "finish": best["finish"],
                 "metrics": metrics(best["text"]), "text": best["text"],
                 "repeat_metrics": [metrics(r["text"]) for r in runs]}
        report["pages"].append(entry)
        m = entry["metrics"]
        print(f"{args.model:14s} p{pno}: {best['elapsed']:5.1f}s chars={m['chars']:5d} "
              f"cjk={m['cjk']:4d} digitRuns={m['digit_runs']} spam={m['repeat_spam']:4d} "
              f"img={m['img_tags']} tbl={m['table_tags']} numJunk={m['number_gibberish']}")

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    print(f"写出 {args.out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
