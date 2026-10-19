#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dump_pua.py —— 导出 KingsoftSign/KingsoftExtra 的 PUA 字形图与度量。

目的：这些 PUA 码位（U+F03D 等）就是数学符号（等号、括号、加减号…），
但它们是**私用区**，任何系统字体都不认。要渲染它们，必须知道**每个 PUA 码位
对应哪个真实 Unicode 符号**。本工具把字形画成图片并给出 advance 宽度，
用来人工识别（并作为映射表的证据）。

输出：out/pua/<字体>_<码位>.png + 控制台表格（advance 宽高比）
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
import paths  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="导出 PUA 字形图")
    ap.add_argument("--json", default=os.path.join(paths.OUT_DIR, "paper.exact.paddleocrvl16.json"))
    ap.add_argument("--fonts-dir", default=os.path.join(paths.OUT_DIR, "fonts", "raw"))
    ap.add_argument("--out-dir", default=os.path.join(paths.OUT_DIR, "pua"))
    ap.add_argument("--sheet", default=os.path.join(paths.OUT_DIR, "pua_sheet.png"))
    args = ap.parse_args()

    from fontTools.ttLib import TTFont
    from PIL import Image, ImageDraw, ImageFont

    d = json.load(open(args.json, encoding="utf-8"))
    scale = d["document"]["dpi"] / 72.0

    # 统计每个 PUA 码位被用在哪些上下文（前后一个字符），便于人工判断
    ctx: dict[str, dict] = {}
    for p in d["pages"]:
        for ln in p["lines"]:
            flat = [(sp["font"], c["c"]) for sp in ln["spans"] for c in sp["chars"]]
            for i, (f, c) in enumerate(flat):
                if ord(c) >= 0xE000:
                    e = ctx.setdefault(c, {"n": 0, "fonts": {}, "ctx": []})
                    e["n"] += 1
                    e["fonts"][f] = e["fonts"].get(f, 0) + 1
                    if len(e["ctx"]) < 4:
                        prev = flat[i - 1][1] if i > 0 else ""
                        nxt = flat[i + 1][1] if i + 1 < len(flat) else ""
                        e["ctx"].append(f"{prev}[{c}]{nxt}")

    # 字形度量
    metrics: dict[str, dict] = {}
    for fn in ("KingsoftSign.ttf", "KingsoftExtra.ttf"):
        fp = os.path.join(args.fonts_dir, fn)
        if not os.path.isfile(fp):
            continue
        tt = TTFont(fp, fontNumber=0, lazy=True)
        cm = tt["cmap"].getBestCmap() or {}
        hmtx = tt["hmtx"].metrics
        upm = tt["head"].unitsPerEm
        for cp, gname in cm.items():
            if cp < 0xE000:
                continue
            adv = hmtx.get(gname, (0, 0))[0]
            metrics[f"{fn[:-4]}:{cp:04X}"] = {
                "glyph": gname, "advance": adv, "upm": upm,
                "adv_em": round(adv / upm, 3),
            }

    print(f"{'字符':<8}{'码位':<9}{'次数':<6}{'字体':<20}{'advance/em':<11}上下文")
    for c, e in sorted(ctx.items(), key=lambda kv: -kv[1]["n"]):
        fs = ",".join(f"{k}x{v}" for k, v in e["fonts"].items())
        key = f"KingsoftSign:{ord(c):04X}"
        m = metrics.get(key) or metrics.get(f"KingsoftExtra:{ord(c):04X}") or {}
        adv = f"{m.get('adv_em', '?')}"
        print(f"{c!r:<8}U+{ord(c):04X}   {e['n']:<6}{fs:<20}{adv:<11}{' | '.join(e['ctx'])}")

    # 画字形图（人工识别用）
    os.makedirs(args.out_dir, exist_ok=True)
    sheet_cols, cell = 12, 64
    items = [(c, e) for c, e in sorted(ctx.items(), key=lambda kv: -kv[1]["n"])]
    rows = (len(items) + sheet_cols - 1) // sheet_cols
    sheet = Image.new("RGB", (sheet_cols * cell, max(1, rows) * (cell + 18)), "white")
    dr = ImageDraw.Draw(sheet)
    for i, (c, e) in enumerate(items):
        cx = (i % sheet_cols) * cell
        cy = (i // sheet_cols) * (cell + 18)
        try:
            f = ImageFont.truetype(os.path.join(args.fonts_dir, "KingsoftSign.ttf"), 44)
            dr.text((cx + 8, cy + 6), c, fill="black", font=f)
        except Exception:  # noqa: BLE001
            pass
        dr.text((cx + 2, cy + cell - 2), f"{ord(c):04X}", fill="gray")
    sheet.save(args.sheet)
    print(f"\n字形图：{args.sheet}（{len(items)} 个码位，按出现次数排序）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
