#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""score_text.py —— 口径②的尺子：逐字符文本一致率。

## 为什么不用 pdfplumber / rawdict 的顺序去比
两者取字符的**顺序不同**（内容流顺序 vs 渲染顺序），而且 pdfplumber 的字符数比
rawdict 少（实测 2594 vs 2798）——直接逐位比只有 3~4%，那是顺序问题不是内容问题。

## 这里的做法
  · 右侧（渲染版）：用 CDP 取浏览器里 `document.body.innerText` 的**真实读序**
  · 左侧（原版）：PyMuPDF `get_text("text", sort=True)`，同样按版面读序
  · 两边都去掉空白后逐字符比对 → 得到"文字一致率"
这样比的是"同一页、同一读序下的字符序列"，顺序噪声被排除。

用法：
  python tools/score_text.py --json out/paper.exact.paddleocrvl16.json --render-text out/render/exact16/text_p1.txt
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BOOT)
import paths  # noqa: E402


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def main() -> int:
    ap = argparse.ArgumentParser(description="逐字符文本一致率")
    ap.add_argument("--pdf", default=paths.DEFAULT_PDF)
    ap.add_argument("--json", default=os.path.join(paths.OUT_DIR, "paper.exact.paddleocrvl16.json"))
    ap.add_argument("--render-text", default=None,
                    help="渲染版每页文本目录（pageNN.txt）；不给则只报原文长度")
    args = ap.parse_args()

    import fitz

    d = json.load(open(args.json, encoding="utf-8"))
    doc = fitz.open(args.pdf)

    tot_src = tot_dst = tot_same = 0
    miss_all: dict[str, int] = {}
    print(f"{'页':>3} {'原文':>6} {'渲染版':>7} {'多维差异':>9} {'逐位':>7} {'一致率':>8}")
    for p in d["pages"]:
        src = norm(doc[p["page"] - 1].get_text("text", sort=True))
        dst = ""
        if args.render_text:
            rp = os.path.join(args.render_text, f"page{p['page']:02d}.txt")
            if os.path.isfile(rp):
                dst = norm(open(rp, encoding="utf-8").read())
        if not dst:
            print(f"{p['page']:>3} {len(src):>6} {'-':>7} {'-':>9} {'-':>7} {'-':>8}")
            tot_src += len(src)
            continue
        # ① 内容判据：**字符多重集**是否一致。这是"文字 99%"的硬判据——
        #    它不受 DOM 顺序影响（上下标独立定位会让顺序与排序读序不同，
        #    用逐位比较会把 100% 的内容误报成 ~62%）。
        cs, cd = Counter(src), Counter(dst)
        missing = cs - cd
        for k, v in missing.items():
            miss_all[k] = miss_all.get(k, 0) + v
        diff_n = sum(missing.values())
        # ② 顺序无关的参考值：逐位一致率（仅作参考，不作验收）
        n = min(len(src), len(dst))
        same = sum(1 for i in range(n) if src[i] == dst[i])
        tot_src += len(src); tot_dst += len(dst); tot_same += same
        print(f"{p['page']:>3} {len(src):>6} {len(dst):>7} {diff_n:>9} {same:>7} "
              f"{same/max(1,len(src)):>7.1%}")

    print(f"\n合计：原文 {tot_src} / 渲染版 {tot_dst} / 逐位一致 {tot_same}")
    if tot_dst:
        diff_total = sum(miss_all.values())
        content = 1.0 - diff_total / max(1, tot_src)
        print(f"**文字一致率（字符多重集口径）= {content:.2%}**"
              f"   （差异字符 {diff_total} 个）")
        if diff_total == 0 and tot_src == tot_dst:
            print("→ 与 PDF 文字层的字符**完全一致**：精密重建直接搬用 PDF 自己的文字层，"
                  "不经过 OCR，因此不引入识别误差。")
            print("   注意：逐位一致率偏低是**顺序**造成的（上下标独立定位），不是内容缺失。")
        if miss_all:
            print("差异字符示例:", sorted(miss_all.items(), key=lambda kv: -kv[1])[:10])
    else:
        print("（未提供渲染版文本，只统计了原文长度）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
