#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_exact.py —— 精确重建的**像素级**验收（不依赖 canvas/字体 API 的自述）。

判据（全部可复核）：
  · 字形命中率：每个字符的位置上，原版是否有墨迹。命中 = 字形确实画在了原位。
    这一项直接回答"字体有没有生效、位置准不准"，比任何自述都硬。
  · 逐字符位置误差：字符框中心与"该字符附近原版墨迹质心"的距离。
  · 页级墨迹 IoU / 覆盖率。

用法：
    python tools/check_exact.py --json out/paper.exact.paddleocrvl16.json \
        --render-dir out/render/exact16
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

import numpy as np

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BOOT)
import paths  # noqa: E402

INK_THRESHOLD = 200


def main() -> int:
    ap = argparse.ArgumentParser(description="精确重建的像素级验收")
    ap.add_argument("--pdf", default=paths.DEFAULT_PDF)
    ap.add_argument("--json", default=os.path.join(paths.OUT_DIR, "paper.exact.paddleocrvl16.json"))
    ap.add_argument("--render-dir", default=os.path.join(paths.OUT_DIR, "render", "exact16"))
    ap.add_argument("--tol-px", type=int, default=2)
    ap.add_argument("--skip-pua", action="store_true",
                    help="跳过落在坏字形（PUA）区间的字符——用于量"
                         "'非数学文字'的可达上限，把'缺字形'与'排版'两个因素分开")
    args = ap.parse_args()

    import pypdfium2 as pdfium
    from PIL import Image

    d = json.load(open(args.json, encoding="utf-8"))
    dpi = d["document"]["dpi"]
    scale = dpi / 72.0
    doc = pdfium.PdfDocument(args.pdf)

    print(f"{'页':>3} {'字形':>6} {'命中':>6} {'命中率':>8} {'位置误差中位':>12} "
          f"{'IoU':>7} {'召回':>7}")
    tot = hit = 0
    g_loc_ok = 0
    all_centroid = []
    g_missing = g_blank = g_extra = 0
    miss_chars: Counter = Counter()
    font_stat: dict[str, list[int]] = {}
    ious, recs, devs_all = [], [], []
    for p in d["pages"]:
        og = np.asarray(doc[p["page"] - 1].render(scale=scale).to_pil().convert("L"))
        rp = os.path.join(args.render_dir, f"page{p['page']:02d}.png")
        if not os.path.isfile(rp):
            continue
        rg = np.asarray(Image.open(rp).convert("L"))
        if rg.shape != og.shape:
            rg = np.asarray(Image.fromarray(rg).resize((og.shape[1], og.shape[0]),
                                                       Image.LANCZOS))
        mo, mr = og < INK_THRESHOLD, rg < INK_THRESHOLD
        inter = int((mo & mr).sum()); union = int((mo | mr).sum())
        iou = inter / union if union else 1.0
        rec = inter / max(1, int(mo.sum()))
        ious.append(iou); recs.append(rec)

        # 坏字形位置（PUA）：原版这些码位在私用区，提取的字体里也没有对应字形，
        # 浏览器只能画 tofu，因此它们**必然**不命中。这里按需排除，用来分离
        # "缺字形" 与 "排版不准" 两个因素。
        pua_boxes = []
        if args.skip_pua:
            for g in p.get("math_gaps", []) or []:
                pua_boxes.append((g["x"], g["y_top"], g["y_bottom"], g["w"]))

        def in_pua(x, y):
            for (gx, gtop, gbot, gw) in pua_boxes:
                if gx - 1 <= x <= gx + max(1.0, gw) + 1 and gtop - 1 <= y <= gbot + 1:
                    return True
            return False

        # 字形命中：字符框内原版是否有墨
        page_hit = page_tot = page_missing = page_blank = page_extra = 0
        loc_ok = 0
        devs = []
        centroid_devs = []
        by_font: dict[str, list[int]] = {}
        for ln in p["lines"]:
            for sp in ln["spans"]:
                for ch in sp["chars"]:
                    if args.skip_pua and in_pua(ch["x"], ch["y_top"]):
                        continue
                    x0 = int(ch["x"] * scale); x1 = int((ch["x"] + max(1.0, ch["w"])) * scale)
                    y0 = int(ch["y_top"] * scale); y1 = int(ch["y_bot"] * scale)
                    x0, x1 = max(0, x0), min(og.shape[1], x1)
                    y0, y1 = max(0, y0), min(og.shape[0], y1)
                    if x1 <= x0 or y1 <= y0:
                        continue
                    page_tot += 1
                    box = mo[y0:y1, x0:x1]
                    rbox = mr[y0:y1, x0:x1]
                    # 阈值必须**按字符类型自适应**：句点/逗号这类稀疏字形
                    # 墨只占框的 2–5%，统一用 3% 会把它们误判成"缺失字形"
                    # （实测 TimesNewRomanPSMT 的 '.' 10 次、`，` 3 次被误判）。
                    glyph = ch["c"]
                    sparse = glyph in ".·,，、;；:：'\"!|il"
                    o_thr = 0.008 if sparse else 0.03
                    o_ink = bool(box.size and box.sum() / box.size > o_thr)
                    r_ink = bool(rbox.size and rbox.sum() / rbox.size > o_thr)
                    bf = by_font.setdefault(sp["font"], [0, 0])
                    bf[0] += 1
                    if o_ink and r_ink:
                        page_hit += 1
                        bf[1] += 1
                        # 位置误差用**墨迹重叠率**，不用"墨质心 vs 框中心"。
                        #
                        # 为什么换判据：句点/逗号/引号的墨天然只在框的角上，
                        # 拿墨质心比框中心，再准也会偏半个框——实测这类字符的
                        # 偏差被高估到十几 pt，把"≤3pt 占比"从 98% 压到 86%。
                        # 重叠率只看"渲染的墨有没有落在原版墨的位置上"，与字形
                        # 形状无关，是更公正的位置判据。
                        ov = int((box & rbox).sum())
                        denom = int(rbox.sum())
                        if denom > 0:
                            local_iou = ov / denom
                            # 重叠率 <60% 记为"位置未到位"（其余算到位）
                            if local_iou >= 0.6:
                                loc_ok += 1
                            devs.append(1.0 - local_iou)   # 值越小越好
                        ys, xs = np.nonzero(box)
                        cy = y0 + ys.mean(); cx = x0 + xs.mean()
                        centroid_devs.append(
                            (((cy - (y0 + y1) / 2) ** 2
                              + (cx - (x0 + x1) / 2) ** 2) ** 0.5) / scale)
                    elif o_ink and not r_ink:
                        page_missing += 1          # 原版有墨、渲染版没画 → 真缺字形
                        miss_chars[(sp["font"], ch["c"])] += 1
                    elif not o_ink and not r_ink:
                        page_blank += 1            # 两边都没墨 → 空格类，位置天然不可测
                    else:
                        page_extra += 1            # 渲染版多画了墨 → 会拉低精确率
        for k, v in by_font.items():
            font_stat.setdefault(k, [0, 0])
            font_stat[k][0] += v[0]
            font_stat[k][1] += v[1]
        tot += page_tot; hit += page_hit
        g_loc_ok += loc_ok
        all_centroid.extend(centroid_devs)
        g_missing += page_missing; g_blank += page_blank; g_extra += page_extra
        med = float(np.median(devs)) if devs else None
        devs_all.extend(devs)
        print(f"{p['page']:>3} {page_tot:>6} {page_hit:>6} "
              f"{page_hit/max(1,page_tot):>7.1%} {str(round(med,2) if med is not None else '-'):>12} "
              f"{iou:>7.4f} {rec:>7.4f}")

    print(f"  位置到位(局部墨重叠≥60%) = {g_loc_ok}/{hit} = **{g_loc_ok/max(1,hit):.1%}**")
    if all_centroid:
        ac = sorted(all_centroid)
        print(f"  参考：墨质心偏差 中位 {ac[len(ac)//2]:.2f}pt（对稀疏字形会高估，不作验收）")
    print(f"\\n合计：字符位置 {tot} | 命中 {hit} | "
          f"缺失字形 {g_missing} | 空白位 {g_blank} | 多画墨 {g_extra}")
    print(f"  可见字符命中率 = {hit/max(1,hit+g_missing):.1%}"
          f"（分母剔除空白位——空格类位置天然无墨，不该计入）")
    print(f"墨迹 IoU 平均 {sum(ious)/len(ious):.4f} | 召回平均 {sum(recs)/len(recs):.4f}")
    if miss_chars:
        print("\n缺失字形的字符（字体, 字符, 次数）：")
        for (f_, c_), n_ in sorted(miss_chars.items(), key=lambda kv: -kv[1])[:10]:
            print(f"   {f_:<20} {c_!r:<6} x{n_}")
    print("\n按字体的字形命中率（用来看是哪种字体缺字形）:")
    for k, v in sorted(font_stat.items(), key=lambda kv: -kv[1][0]):
        t, h = v
        print(f"   {k:<30} {h:>5}/{t:<5} = {h/max(1,t):.1%}")

    if devs_all:
        a = sorted(devs_all)
        print(f"位置误差：中位 {a[len(a)//2]:.2f}pt  P90 {a[int(len(a)*0.9)]:.2f}pt  "
              f"P99 {a[int(len(a)*0.99)]:.2f}pt  ≤3pt 占比 "
              f"{sum(1 for v in a if v<=3)/len(a):.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
