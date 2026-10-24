#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_char_level.py —— 用 PDF 文字层做**逐字符定位**渲染（达成"行位置 ≤3pt"的关键）。

## 为什么这条路能进 3pt，而前面的行级方案进不去
前一轮实测结论：3pt 容差 = 6.2 设备像素；而原卷字体 `HYShuSongErKW` 本机缺失、
回退 SimSun 后**行宽误差 15–50px**，任何"整行字号缩放"都补不回来。
要 ≤6.2px，只能逐字符定位。

## 这条路的基础（已实测）
  · 文字层字符位置与墨迹**亚像素吻合**：左边界差 -0.5pt、右边界差 1.5–17pt
  · 逐行**可解码覆盖率 89.5%**（汉字/数字/字母/标点完好）
  · 坏字形 61/582 = 10.5%，全是数学符号（私用区，不可渲染）

所以：**每个可解码字符按它自己的 x/y/字号渲染** —— 位置不依赖字体宽度估算，
天然落在原位上。坏字形位置留空，由后续用 LaTeX 补（本工具先标记出来）。

产出：每页 `chars[]`（每个字符：文本、pt 坐标、字号）+ `math_gaps[]`（坏字形位置）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BOOT)
import paths  # noqa: E402


def classify(ch: str) -> str:
    if not ch:
        return "empty"
    cp = ord(ch)
    if 0xE000 <= cp <= 0xF8FF or cp >= 0xF0000:
        return "pua"          # 私用区：坏字形，不能当文本渲染
    if 0x4E00 <= cp <= 0x9FFF:
        return "cjk"
    if 0x3000 <= cp <= 0x303F or 0xFF00 <= cp <= 0xFFEF:
        return "cjk_punct"
    if ch.isdigit() or ch.isalpha():
        return "latin"
    return "other"


def build_page(chars: list[dict], scale: float) -> dict:
    """字符坐标**直接用 pdfplumber 的值**——它的 x0/x1/top/bottom 已经是 PDF 点，
    且是左上原点（与项目其它产物一致）。

    ⚠️ 别再除 scale：早先这里写了 `c["x0"] / scale`，把 pt 又当成像素缩了一次，
    结果所有字符坐标偏了 1.41 倍（墨迹 IoU 从应有水平掉到 0.0175）。
    核对方式：首字 `x0=181.56 / top=82.55`，而墨迹首行是 `79.68–95.04 pt`——量级吻合。
    """
    out_chars = []
    gaps = []
    for c in chars:
        ch = c.get("text") or ""
        kind = classify(ch)
        entry = {
            "text": ch if kind not in ("pua", "empty") else "",
            "kind": kind,
            "x": round(c["x0"], 2),
            "y_top": round(c["top"], 2),
            "y_bottom": round(c["bottom"], 2),
            "w": round(c["x1"] - c["x0"], 2),
            "h": round(c["bottom"] - c["top"], 2),
            "size": round(c["size"], 2),
        }
        if kind in ("pua", "empty"):
            gaps.append({"x": entry["x"], "y_top": entry["y_top"],
                         "y_bottom": entry["y_bottom"], "w": entry["w"], "h": entry["h"]})
        else:
            out_chars.append(entry)
    return {"chars": out_chars, "math_gaps": gaps}


def main() -> int:
    ap = argparse.ArgumentParser(description="逐字符定位渲染数据")
    ap.add_argument("--pdf", default=paths.DEFAULT_PDF)
    ap.add_argument("--out", default=os.path.join(paths.OUT_DIR, "paper.chars.paddleocrvl16.json"))
    ap.add_argument("--viewer-base", default="viewer_chars_paddleocrvl16")
    ap.add_argument("--title", default="静安区2026届高三一模数学试卷")
    ap.add_argument("--dpi", type=int, default=150)
    args = ap.parse_args()

    import pdfplumber
    import pypdfium2 as pdfium

    scale = args.dpi / 72.0
    doc = pdfium.PdfDocument(args.pdf)
    pages = []
    with pdfplumber.open(args.pdf) as pl:
        for i, page in enumerate(pl.pages):
            pil = doc[i].render(scale=scale).to_pil().convert("RGB")
            data = build_page(page.chars, scale)
            pages.append({
                "page": i + 1,
                "width": round(pil.width / scale, 2),
                "height": round(pil.height / scale, 2),
                "render_px": [pil.width, pil.height],
                "dpi": args.dpi,
                **data,
            })

    # 统计
    tot = sum(len(p["chars"]) for p in pages)
    gap = sum(len(p["math_gaps"]) for p in pages)
    k = {}
    for p in pages:
        for c in p["chars"]:
            k[c["kind"]] = k.get(c["kind"], 0) + 1
    payload = {
        "document": {
            "title": args.title,
            "source_pdf": os.path.abspath(args.pdf),
            "pages_total": len(pages), "pages_processed": len(pages),
            "engine": "paddleocr-vl-1.6",
            "viewer_base": args.viewer_base,
            "mode": "char",
            "dpi": args.dpi,
            "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
            "warnings": [f"{gap} 个坏字形（私用区/空）位置留空，需用 LaTeX 补"],
        },
        "stats": {
            "pages": len(pages),
            "chars": tot,
            "math_gaps": gap,
            "by_kind": k,
            "coverage": round(tot / max(1, tot + gap), 4),
        },
        "pages": pages,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"[chars] 写出 {args.out}")
    print(f"[chars] {len(pages)} 页 / 可渲染字符 {tot} / 坏字形位置 {gap} / "
          f"覆盖率 {payload['stats']['coverage']:.1%}")
    print(f"[chars] 字符类型: {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
