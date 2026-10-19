#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""render_chars.py —— 把逐字符数据渲染成 PNG（离线测量用）。

用 PIL 直接画：位置来自 PDF 文字层（亚像素准），字体用系统回退字体。
这是"逐字符定位"能达到的**墨迹上限**测量——如果它的墨迹 IoU 都不高，
说明瓶颈在字形差异（字体缺失），不在排版。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BOOT)
import paths  # noqa: E402

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simsun.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\times.ttf",
]


def load_font(size_px: int):
    for p in FONT_CANDIDATES:
        if os.path.isfile(p):
            try:
                return ImageFont.truetype(p, size_px)
            except Exception:  # noqa: BLE001
                continue
    return ImageFont.load_default()


def main() -> int:
    ap = argparse.ArgumentParser(description="逐字符渲染成 PNG")
    ap.add_argument("--json", default=os.path.join(paths.OUT_DIR, "paper.chars.paddleocrvl16.json"))
    ap.add_argument("--out-dir", default=os.path.join(paths.OUT_DIR, "render", "chars16"))
    args = ap.parse_args()

    d = json.load(open(args.json, encoding="utf-8"))
    os.makedirs(args.out_dir, exist_ok=True)
    dpi = d["document"]["dpi"]
    scale = dpi / 72.0
    cache: dict[int, object] = {}

    for p in d["pages"]:
        w, h = p["render_px"]
        img = Image.new("L", (w, h), 255)
        dr = ImageDraw.Draw(img)
        for c in p["chars"]:
            if not c["text"]:
                continue
            # 字号换算成像素；字号是 pt → px = size * scale
            size_px = max(4, int(round(c["size"] * scale)))
            if size_px not in cache:
                cache[size_px] = load_font(size_px)
            f = cache[size_px]
            # 基线定位：PIL 的 anchor="ls" 表示左下角，对应 PDF 的 baseline
            x = c["x"] * scale
            y = c["y_bottom"] * scale
            try:
                dr.text((x, y), c["text"], fill=0, font=f, anchor="ls")
            except Exception:  # noqa: BLE001
                dr.text((x, y), c["text"], fill=0, font=f)
        out = os.path.join(args.out_dir, f"page{p['page']:02d}.png")
        img.save(out)
        print(f"  {out}  {w}x{h}  字符 {len(p['chars'])}")
    print(f"[render] 输出目录 {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
