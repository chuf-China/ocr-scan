#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compare_ocr.py —— 两个本地 VLM-OCR 在同一批图上做 A/B 对比。

为什么这样比才有意义：**行盒来自像素投影，与模型无关**。所以把同一批裁图分别喂给两个
模型，差异就只来自模型本身，而不是版面检测。

用法：
    python compare_ocr.py --both                  # 逐页整页对比（另起引擎，见 --engine）
    python compare_ocr.py --regions               # 只比几个关键区域
    python compare_ocr.py --model ovisocr2        # 只跑一个

引擎端口：ovisocr2 → 8081，paddleocr-vl → 8080（启动脚本按 --Model 自动选端口）。
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time

import numpy as np

import os
import sys

# 工具在 tools/ 或 verify/ 下：把 ../src 加入 sys.path，才能 import 两个流水线与共享层
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
import paths  # noqa: E402
from ocr_common import import_pdfium, ocr_png  # noqa: E402

PDF = paths.DEFAULT_PDF
DPI = 150
OVIS = ("ovisocr2", "http://127.0.0.1:8081")
PADDLE = ("paddleocr-vl", "http://127.0.0.1:8080")

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

def render(pno: int, dpi: int = DPI):
    pdfium = import_pdfium()
    doc = pdfium.PdfDocument(PDF)
    page = doc[pno - 1]
    pil = page.render(scale=dpi / 72).to_pil().convert("RGB")
    return pil

def png_bytes(pil, box=None) -> bytes:
    img = pil.crop(box) if box else pil
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()

def run_one(model: str, host: str, prompt: str, png: bytes, timeout: int = 300) -> dict:
    t0 = time.time()
    try:
        r = ocr_png(host, model, png, prompt, timeout, 8192)
        return {"text": r["text"], "finish": r["finish_reason"],
                "usage": r.get("usage"), "elapsed": round(time.time() - t0, 2)}
    except Exception as exc:  # noqa: BLE001
        return {"text": f"<ERROR {exc}>", "finish": "error", "elapsed": round(time.time() - t0, 2)}

# 关键区域：**从 JSON 的 pt 坐标换算**成像素盒，避免手填 pt/px 混用。
# 结构：label → (页, 文本包含的关键字列表 或 None=整页范围, y 方向外扩 px)
REGION_SPECS: list[tuple[str, int, list[str] | None, int]] = [
    ("第14题 直方图 + 选项", 2, ["如图，已知某频率分布直方图", "众数"], 10),
    ("第13题（HTML 占位符污染）", 2, ["13. 在三维空间中", "垂直于同一条直线"], 10),
    ("第17题 表格区", 3, ["志愿者学科分布", "语文", "学科"], 12),
    ("第8题 cases 公式", 1, ["8. 已知函数"], 8),
    ("第19题 立体图 + 题干", 4, ["19. 已知正四棱柱", "证明："], 10),
    ("第20题 坐标图 + 题干", 5, ["20. 在平面直角坐标系", "求双曲线"], 12),
]

def region_boxes() -> list[tuple[str, int, list[int]]]:
    """按关键字在 JSON 里找到相关行，取它们的包围盒（像素）后适度外扩。"""
    with open(paths.LAYOUT_JSON["ovisocr2"], encoding="utf-8") as fh:
        doc = json.load(fh)
    by_page = {p["page"]: p for p in doc["pages"]}
    out: list[tuple[str, int, list[int]]] = []
    for label, pno, keys, pad in REGION_SPECS:
        page = by_page.get(pno)
        if not page:
            continue
        sx = page["render_px"][0] / page["width"]
        sy = page["render_px"][1] / page["height"]
        hits = [l for l in page["lines"]
                if keys is None or any(k in l["text"] for k in keys)]
        if not hits:
            continue
        top = min(int(l["top"] * sy) for l in hits) - pad
        bottom = max(int(l["bottom"] * sy) for l in hits) + pad
        left = min(int(l["left"] * sx) for l in hits) - 30
        right = max(int(l["right"] * sx) for l in hits) + 30
        out.append((label, pno, [max(0, left), max(0, top), right, bottom]))
    return out

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=None, help="只跑指定模型")
    ap.add_argument("--regions", action="store_true", help="只比关键区域")
    ap.add_argument("--pages", default="1-6")
    ap.add_argument("--out", default=os.path.join(paths.COMPARE_DIR, "ocr_compare.json"))
    args = ap.parse_args()

    targets = []
    if args.model == "ovisocr2":
        targets = [OVIS]
    elif args.model == "paddleocr-vl":
        targets = [PADDLE]
    else:
        targets = [OVIS] if not args.model else [OVIS, PADDLE]

    report: dict = {"config": {"dpi": DPI, "pdf": PDF}, "models": {}, "regions": [], "pages": []}

    if args.regions:
        for label, pno, box in region_boxes():
            pil = render(pno)
            png = png_bytes(pil, box)
            entry = {"label": label, "page": pno, "box": box, "bytes": len(png), "results": {}}
            for model, host in targets:
                prompt = PROMPT_PADDLE if "paddle" in model else PROMPT_OVIS
                entry["results"][model] = run_one(model, host, prompt, png)
            report["regions"].append(entry)
            print(f"\n{'='*70}\n### {label}  (p{pno}, box={box}, {len(png)//1024}KB)")
            for model, res in entry["results"].items():
                print(f"\n--- {model}  ({res['elapsed']}s, finish={res['finish']}) ---")
                print(res["text"][:1500])
    else:
        a, _, b = args.pages.partition("-")
        pages = list(range(int(a), int(b or a) + 1))
        for pno in pages:
            pil = render(pno)
            png = png_bytes(pil)
            entry = {"page": pno, "bytes": len(png), "results": {}}
            for model, host in targets:
                prompt = PROMPT_PADDLE if "paddle" in model else PROMPT_OVIS
                entry["results"][model] = run_one(model, host, prompt, png)
            report["pages"].append(entry)
            print(f"\n{'='*70}\n### 第 {pno} 页整页（{len(png)//1024}KB）")
            for model, res in entry["results"].items():
                txt = res["text"]
                print(f"--- {model}: {res['elapsed']}s finish={res['finish']} chars={len(txt)}")
                print(txt[:900])

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    print(f"\n写出 {args.out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
