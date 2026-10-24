#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""analyze_hits.py —— 定位"字形未命中"的到底是什么字符，并把**无墨字符**分离出来。

背景：`check_exact.py` 报 HYShuSongErKW 命中率 86.4%（213 个未命中）。
两种可能，含义完全不同：
  · **无墨字符**（空格、零宽、全角空格）→ 本就不该在原版上留下墨迹，应从分母剔除
  · **真缺字形**（豆腐块/回退渲染）→ 是缺陷，必须修

本工具按字符类别统计命中率，并打印未命中字符的样本（含 Unicode 名称），
让"剔除"与"修复"的边界有据可依。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
from collections import Counter

import numpy as np

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BOOT)
import paths  # noqa: E402

INK = 200


def main() -> int:
    ap = argparse.ArgumentParser(description="分离无墨字符与真缺字形")
    ap.add_argument("--pdf", default=paths.DEFAULT_PDF)
    ap.add_argument("--json", default=os.path.join(paths.OUT_DIR, "paper.exact.paddleocrvl16.json"))
    ap.add_argument("--render-dir", default=os.path.join(paths.OUT_DIR, "render", "exact16"))
    args = ap.parse_args()

    import pypdfium2 as pdfium
    from PIL import Image

    d = json.load(open(args.json, encoding="utf-8"))
    scale = d["document"]["dpi"] / 72.0
    doc = pdfium.PdfDocument(args.pdf)

    miss = Counter()
    hit = Counter()
    miss_font = Counter()
    # 分类：空白类 / 其它
    def cls(ch: str) -> str:
        if ch.isspace() or ch == "":
            return "空白"
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Zs", "Zl", "Zp"):
            return "控制/格式/分隔"
        return f"可见({cat})"

    for p in d["pages"]:
        og = np.asarray(doc[p["page"] - 1].render(scale=scale).to_pil().convert("L"))
        rp = os.path.join(args.render_dir, f"page{p['page']:02d}.png")
        if not os.path.isfile(rp):
            continue
        rg = np.asarray(Image.open(rp).convert("L"))
        if rg.shape != og.shape:
            rg = np.asarray(Image.fromarray(rg).resize((og.shape[1], og.shape[0]),
                                                       Image.LANCZOS))
        mo = og < INK
        # 渲染版该位置是否有墨（用于区分"没画"与"画了但没墨"）
        for ln in p["lines"]:
            for sp in ln["spans"]:
                for ch in sp["chars"]:
                    x0 = max(0, int(ch["x"] * scale))
                    x1 = min(og.shape[1], int((ch["x"] + max(1.0, ch["w"])) * scale))
                    y0 = max(0, int(ch["y_top"] * scale))
                    y1 = min(og.shape[0], int(ch["y_bot"] * scale))
                    if x1 <= x0 or y1 <= y0:
                        continue
                    box = mo[y0:y1, x0:x1]
                    if box.sum() / box.size > 0.03:
                        hit[cls(ch["c"])] += 1
                    else:
                        miss[cls(ch["c"])] += 1
                        miss_font[(sp["font"], ch["c"])] += 1

    print("按字符类别的命中情况：")
    for k in sorted(set(hit) | set(miss)):
        h, m = hit[k], miss[k]
        print(f"   {k:<16} 命中 {h:>5} / 未命中 {m:>4} = {h/(h+m):.1%}")
    print(f"\n未命中合计 {sum(miss.values())}")
    print("\n未命中最多的字符（字体, 字符, Unicode 名称, 类别）：")
    for (f, ch), n in miss_font.most_common(14):
        name = unicodedata.name(ch, "<无名称>")
        print(f"   {f:<20} {ch!r:<8} U+{ord(ch):04X} {unicodedata.category(ch):<3} "
              f"x{n:<4} {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
