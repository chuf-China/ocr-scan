#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_render.py —— 版式渲染的**结构级**校验：裁片不得压正文、几何不得越界。

为什么需要它：只统计"行与行重叠"会漏掉真问题——插图裁片盖在文字上时，两个 `.ln` 盒子
并不相交，重叠统计反而是 0。本脚本直接查三件事：

  1. 每张插图裁片与每条正文行的交叠面积（必须为 0）；
  2. 正文行之间的垂直重叠（超过 40% 判为叠字）；
  3. 行/裁片是否越出纸张边界。

用法：python check_render.py out/paper.layout.paddle.json
退出码：0 通过（可能有警告）；1 有错误。
"""
from __future__ import annotations

import argparse
import json
import sys


def overlap_area(a: dict, b: dict, keys=("left", "top", "right", "bottom")) -> float:
    ix = min(a[keys[2]], b[keys[2]]) - max(a[keys[0]], b[keys[0]])
    iy = min(a[keys[3]], b[keys[3]]) - max(a[keys[1]], b[keys[1]])
    return ix * iy if ix > 0 and iy > 0 else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path")
    ap.add_argument("--overlap-ratio", type=float, default=0.40,
                    help="行与行垂直重叠超过该比例判为叠字")
    args = ap.parse_args()

    with open(args.json_path, encoding="utf-8") as fh:
        doc = json.load(fh)

    errors: list[str] = []
    warnings: list[str] = []
    fig_hits = 0

    for p in doc["pages"]:
        pno = p["page"]
        pw, ph = p["width"], p["height"]
        lines = p["lines"]
        figs = p.get("figures") or []

        # ① 裁片 × 正文行：必须零交叠
        for f in figs:
            box = {"left": f["pt"][0], "top": f["pt"][1],
                   "right": f["pt"][2], "bottom": f["pt"][3]}
            for i, l in enumerate(lines):
                a = overlap_area(box, l)
                if a > 1.0:
                    fig_hits += 1
                    errors.append(
                        f"第 {pno} 页 插图 {f['index']} 压住正文行 {i}（交叠 {a:.0f}pt²）："
                        f"{l['text'][:34]}")

        # ② 行与行垂直重叠
        ordered = sorted(lines, key=lambda l: l["top"])
        for prev, cur in zip(ordered, ordered[1:]):
            ov = prev["bottom"] - cur["top"]
            hmin = min(prev["bottom"] - prev["top"], cur["bottom"] - cur["top"])
            if ov > 0 and hmin > 0 and ov / hmin > args.overlap_ratio:
                errors.append(f"第 {pno} 页 行叠字 {ov:.1f}pt：{prev['text'][:24]} / {cur['text'][:24]}")

        # ③ 越界
        for i, l in enumerate(lines):
            if l["left"] < -2 or l["right"] > pw + 2 or l["top"] < -2 or l["bottom"] > ph + 2:
                warnings.append(f"第 {pno} 页 行 {i} 越出纸张：{l['text'][:30]}")
        for f in figs:
            if f["pt"][2] > pw + 2 or f["pt"][3] > ph + 2:
                warnings.append(f"第 {pno} 页 插图 {f['index']} 越出纸张")

    print(f"文件：{args.json_path}")
    print(f"引擎：{doc['document'].get('engine')}  页数：{len(doc['pages'])}  "
          f"行数：{sum(len(p['lines']) for p in doc['pages'])}  "
          f"插图：{sum(len(p.get('figures') or []) for p in doc['pages'])}")
    print(f"插图压正文：{fig_hits} 处")
    for w in warnings:
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
