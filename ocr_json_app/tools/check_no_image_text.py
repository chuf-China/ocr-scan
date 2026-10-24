#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_no_image_text.py —— 口径③的可执行检查：**没有用裁片替代文字**。

判据（都可复核）：
  ① 正文全部是**文本节点**：`paper.exact.*.json` 里 `chars` 数量 > 0，
     且每页都有 `lines[].spans[].chars[]`（不是把整页当图）
  ② 图裁片只在**图块区域**：每个 `figures[]` 的矩形必须落在官方 `image/chart/figure`
     块框内（含 4px 容差）——即"图归图、字归字"
  ③ **没有任何图裁片与文本行重叠**：否则就是用图盖住了文字
  ④ 页面上不存在"整页图"：没有覆盖整页面积的 `figures[]`

用法：python tools/check_no_image_text.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BOOT)
import paths  # noqa: E402

FIG_LABELS = ("image", "chart", "figure")
TOL = 4.0


def main() -> int:
    ap = argparse.ArgumentParser(description="口径③：无裁片替代文字")
    ap.add_argument("--json", default=os.path.join(paths.OUT_DIR, "paper.exact.paddleocrvl16.json"))
    ap.add_argument("--raw-dir", default=os.path.join(paths.OUT_DIR, "paddleocr_raw"))
    args = ap.parse_args()

    d = json.load(open(args.json, encoding="utf-8"))
    scale = d["document"]["dpi"] / 72.0
    errs: list[str] = []

    n_chars = sum(len(sp["chars"]) for p in d["pages"] for ln in p["lines"] for sp in ln["spans"])
    n_figs = sum(len(p.get("figures", [])) for p in d["pages"])
    print(f"文本节点（字符）总数 = {n_chars}")
    print(f"图裁片数 = {n_figs}")

    # ① 每页必须有文本节点
    for p in d["pages"]:
        c = sum(len(sp["chars"]) for ln in p["lines"] for sp in ln["spans"])
        if c == 0:
            errs.append(f"第 {p['page']} 页没有任何文本节点（疑似整页当图）")
    print(f"每页文本节点数 = {[sum(len(sp['chars']) for ln in p['lines'] for sp in ln['spans']) for p in d['pages']]}")

    # ② 图裁片必须落在官方图块框内
    for p in d["pages"]:
        raw_p = os.path.join(args.raw_dir, f"page{p['page']:02d}_res.json")
        blocks = []
        if os.path.isfile(raw_p):
            res = json.load(open(raw_p, encoding="utf-8"))
            blocks = [(b["block_bbox"], (b.get("block_label") or ""))
                      for b in (res.get("parsing_res_list") or [])]
        for f in p.get("figures", []):
            # 转回像素坐标（raw bbox 是像素）
            fx0, fy0 = f["left"] * scale, f["top"] * scale
            fx1, fy1 = fx0 + f["width"] * scale, fy0 + f["height"] * scale
            inside = False
            for (bx0, by0, bx1, by1), lab in blocks:
                if lab not in FIG_LABELS:
                    continue
                if (fx0 >= bx0 - TOL and fy0 >= by0 - TOL
                        and fx1 <= bx1 + TOL and fy1 <= by1 + TOL):
                    inside = True
                    break
            if not inside:
                errs.append(f"第 {p['page']} 页图裁片 ({fx0:.0f},{fy0:.0f})-({fx1:.0f},{fy1:.0f}) "
                            f"不在任何图块框内")

        # ③ 图裁片不得与文本行重叠
        for f in p.get("figures", []):
            for ln in p["lines"]:
                for sp in ln["spans"]:
                    for c in sp["chars"]:
                        if (c["x"] < f["left"] + f["width"] and c["x"] + c["w"] > f["left"]
                                and c["y_top"] < f["top"] + f["height"]
                                and c["y_bot"] > f["top"]):
                            errs.append(f"第 {p['page']} 页字符 {c['c']!r} 被图裁片覆盖")
                            break
                    else:
                        continue
                    break
                else:
                    continue
                break

    # ④ 不得有整页图
    for p in d["pages"]:
        for f in p.get("figures", []):
            area = f["width"] * f["height"]
            page_area = p["width"] * p["height"]
            if area > 0.5 * page_area:
                errs.append(f"第 {p['page']} 页存在覆盖 {area/page_area:.0%} 页面的大图裁片")

    print(f"\n{'=' * 60}")
    if errs:
        print(f"✗ 口径③ 未通过，{len(errs)} 个问题：")
        for e in errs[:10]:
            print(f"   · {e}")
        return 1
    print("✓ 口径③ 通过：正文全部为文本节点（{n} 个），".format(n=n_chars))
    print(f"  {n_figs} 个图裁片全部落在官方图块框内、且不与任何字符重叠，无整页图")
    return 0


if __name__ == "__main__":
    sys.exit(main())
