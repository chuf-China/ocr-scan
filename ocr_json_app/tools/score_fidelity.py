#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""score_fidelity.py —— 把"扫描渲染版与原版 PDF 有多像"变成可算的数字。

用户目标：**渲染版至少达到原版的 99%**。要迭代就必须先能测量，所以这里定义三个维度。

## 维度一：视觉保真度（像素层）
把原版 PDF 每页栅格化（150dpi），把渲染版也栅格化成同尺寸，然后**只看墨迹**：

  · `ink_iou`  = |原墨 ∩ 渲染墨| / |原墨 ∪ 渲染墨|      —— 整体墨迹重合度
  · `ink_prec` = |交集| / |渲染墨|                       —— 渲染出来的东西有多少是原稿上真有的
  · `ink_rec`  = |交集| / |原墨|                         —— 原稿的墨有多少被渲染出来了

**为什么不用整页 SSIM**：A4 页面 ~95% 是白底，整页 SSIM 会被空白抬到 0.95 以上，
再差的重建也"看着像 99%"，对迭代没有区分度。只在墨迹集合上算才有意义。

## 维度二：结构保真度（行级）
逐行对比"原始墨迹投影出的行盒"与"渲染版里的行盒"，报告：

  · 行的召回率 / 精确率
  · 每行中心偏移（pt）的中位数与 P95
  · 判定阈值：中心偏移 ≤ 3pt 且宽度偏差 ≤ 15% 视为"到位"

## 维度三：字符保真度（文本层）
原版 PDF 的文字层（中文/ASCII 可解码）与渲染版提取出的文本做字符级比对。
数学符号是坏字形，不参与——单列出来只报"是否保住了 LaTeX 结构"。

用法：
    python tools/score_fidelity.py --pdf data/试卷.pdf --render <渲染版图片或JSON>
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
import paths  # noqa: E402

INK_THRESHOLD = 200


def to_gray(path_or_array) -> np.ndarray:
    if isinstance(path_or_array, np.ndarray):
        return path_or_array
    from PIL import Image
    return np.asarray(Image.open(path_or_array).convert("L"))


def ink_mask(gray: np.ndarray, thr: int = INK_THRESHOLD) -> np.ndarray:
    return gray < thr


def compare_ink(a: np.ndarray, b: np.ndarray, thr: int = INK_THRESHOLD) -> dict:
    """两页灰度图的**墨迹集合**比较。尺寸不一致时按最近邻缩放到 a 的尺寸。"""
    if a.shape != b.shape:
        from PIL import Image
        b = np.asarray(Image.fromarray(b).resize((a.shape[1], a.shape[0]),
                                                 Image.NEAREST))
    ma, mb = ink_mask(a, thr), ink_mask(b, thr)
    inter = int((ma & mb).sum())
    union = int((ma | mb).sum())
    sa, sb = int(ma.sum()), int(mb.sum())
    return {
        "ink_iou": round(inter / union, 4) if union else 1.0,
        "ink_prec": round(inter / sb, 4) if sb else 1.0,
        "ink_rec": round(inter / sa, 4) if sa else 1.0,
        "ink_px_orig": sa,
        "ink_px_render": sb,
        "ink_px_common": inter,
    }


def projection_lines(gray: np.ndarray, thr: int = INK_THRESHOLD) -> list[dict]:
    """用行墨迹投影出行盒（与 ocr_pipeline_layout.detect_lines 同一判据）。"""
    ink = ink_mask(gray, thr)
    rows = ink.sum(axis=1)
    runs, start = [], None
    for i, has in enumerate(rows > 0):
        if has and start is None:
            start = i
        elif not has and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(rows) - 1))
    out = []
    for top, bot in runs:
        cols = np.where(ink[top:bot + 1].any(axis=0))[0]
        if len(cols) == 0:
            continue
        h = bot - top + 1
        w = int(cols[-1] - cols[0] + 1)
        if h < 4 or int(ink[top:bot + 1, cols[0]:cols[-1] + 1].sum()) < 24:
            continue
        out.append({"top": top, "bottom": bot, "left": int(cols[0]),
                    "right": int(cols[-1]), "h": h, "w": w,
                    "cx": (int(cols[0]) + int(cols[-1])) / 2,
                    "cy": (top + bot) / 2})
    return out


def compare_lines(orig: np.ndarray, rend: np.ndarray, scale: float,
                  tol_pt: float = 3.0, w_tol: float = 0.15) -> dict:
    """行级结构比较。

    **两遍匹配**，因为"原始行"和"渲染行"不是一一对应：
      第一遍：贪心按"中心距 + 宽度差"配对（近邻优先），得到主对应；
      第二遍：把原版里没配上的行，只要与某条渲染行**垂直区间真重叠**，
              也算配对（阈值放宽）——渲染把一行拆成两行时属这种情况。
    早先只做一遍贪心，会出现"召回 0.50 但到位 0"这种自相矛盾的输出：
    配上的行全是被贪心错配的，偏移自然很大。
    """
    lo, lr = projection_lines(orig), projection_lines(rend)
    if not lo or not lr:
        return {"orig_lines": len(lo), "render_lines": len(lr),
                "matched": 0, "recall": 0.0, "precision": 0.0,
                "center_dev_pt_median": None, "center_dev_pt_p95": None,
                "within_tol": 0, "within_tol_ratio": 0.0}

    used: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    # 第一遍：候选按代价排序，贪心取用（比"逐行各取最近"更稳）
    cand = []
    for i, a in enumerate(lo):
        for j, b in enumerate(lr):
            dy = abs(a["cy"] - b["cy"])
            dx = abs(a["cx"] - b["cx"])
            wd = abs(a["w"] - b["w"]) / max(1, a["w"])
            cand.append((dy + 0.25 * dx + 40 * wd, i, j))
    cand.sort()
    taken_o: set[int] = set()
    for cost, i, j in cand:
        if i in taken_o or j in used:
            continue
        taken_o.add(i)
        used.add(j)
        pairs.append((i, j, cost))
    # 第二遍：未配对的原版行，与未使用的渲染行有垂直重叠即算配上
    for i, a in enumerate(lo):
        if i in taken_o:
            continue
        for j, b in enumerate(lr):
            if j in used:
                continue
            ov = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
            if ov > 0.3 * min(a["h"], b["h"]):
                taken_o.add(i)
                used.add(j)
                pairs.append((i, j, float("inf")))
                break

    devs, width_devs, within = [], [], 0
    for i, j, _ in pairs:
        a, b = lo[i], lr[j]
        dev = ((a["cy"] - b["cy"]) ** 2 + (a["cx"] - b["cx"]) ** 2) ** 0.5 / scale
        wdev = abs(a["w"] - b["w"]) / max(1, a["w"])
        devs.append(dev)
        width_devs.append(wdev)
        if dev <= tol_pt and wdev <= w_tol:
            within += 1
    devs_sorted = sorted(devs)
    return {
        "orig_lines": len(lo),
        "render_lines": len(lr),
        "matched": len(pairs),
        "recall": round(len(pairs) / len(lo), 4),
        "precision": round(len(pairs) / len(lr), 4),
        "center_dev_pt_median": round(devs_sorted[len(devs_sorted) // 2], 2) if devs else None,
        "center_dev_pt_p95": round(devs_sorted[int(len(devs_sorted) * 0.95)], 2) if devs else None,
        "width_dev_median": round(sorted(width_devs)[len(width_devs) // 2], 4) if width_devs else None,
        "within_tol": within,
        "within_tol_ratio": round(within / len(lo), 4),
    }


def text_of_pdf_page(pdf_path: str, page_no: int) -> str:
    """原版文字层里**能解码**的字符（数学坏字形排除）。"""
    try:
        import pdfplumber
    except ImportError:
        return ""
    with pdfplumber.open(pdf_path) as doc:
        if page_no - 1 >= len(doc.pages):
            return ""
        out = []
        for c in doc.pages[page_no - 1].chars:
            ch = c.get("text") or ""
            if not ch:
                continue
            cp = ord(ch)
            if (0x4E00 <= cp <= 0x9FFF or 0x3000 <= cp <= 0x303F
                    or 0xFF00 <= cp <= 0xFFEF or ch.isdigit() or ch.isalpha()):
                out.append(ch)
        return "".join(out)


def line_position_accuracy(orig: np.ndarray, rend: np.ndarray, tol_px: int = 6,
                           thr: int = INK_THRESHOLD) -> dict:
    """**位置口径的直接度量**：行内原版墨迹有多少落在渲染版墨迹的 ±tol_px 邻域内。

    为什么用"邻域覆盖"而不是"墨迹 IoU"或"质心偏差"：
      · 墨迹 IoU 同时被**字形粗细**污染（字体不同 → 笔画轻重不同），反映不了位置；
      · 质心偏差对**稀疏字形**（句点、逗号）会高估——它们的墨天然在框的角上。
    邻域覆盖只看"原版的墨在渲染版对应位置附近有没有东西"，与字形形状无关。

    tol_px 默认 6：150dpi 下 3pt = 6.25px，与验收口径"≤3pt"对齐。
    """
    from scipy import ndimage
    if orig.shape != rend.shape:
        from PIL import Image
        rend = np.asarray(Image.fromarray(rend).resize(
            (orig.shape[1], orig.shape[0]), Image.LANCZOS))
    mo, mr = orig < thr, rend < thr
    dil = ndimage.binary_dilation(mr, iterations=tol_px)
    covered = int((mo & dil).sum())
    total = int(mo.sum())
    return {"tol_px": tol_px, "orig_ink_px": total, "covered_px": covered,
            "position_accuracy": round(covered / total, 4) if total else 1.0}


def main() -> int:
    ap = argparse.ArgumentParser(description="扫描渲染版 vs 原版 PDF 的保真度评分")
    ap.add_argument("--pdf", default=paths.DEFAULT_PDF)
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--pages", default=None, help="如 1-2，默认全部")
    ap.add_argument("--render-dir", default=None,
                    help="渲染版的逐页 PNG 目录（pageNN.png）")
    ap.add_argument("--render-json", default=None,
                    help="或用本项目 blocks JSON（按 bbox 画块框来做墨迹近似）")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    import pypdfium2 as pdfium
    from PIL import Image

    doc = pdfium.PdfDocument(args.pdf)
    total = len(doc)
    if args.pages:
        m = args.pages.split("-")
        lo = int(m[0]); hi = int(m[1]) if len(m) > 1 else lo
    else:
        lo, hi = 1, total

    report = {"pdf": os.path.abspath(args.pdf), "dpi": args.dpi,
              "scale": args.dpi / 72.0, "pages": []}
    scores = []
    pos_scores = []
    for pno in range(lo, hi + 1):
        pil = doc[pno - 1].render(scale=args.dpi / 72).to_pil().convert("RGB")
        ogray = np.asarray(pil.convert("L"))
        row = {"page": pno, "size": list(pil.size)}

        # 渲染版：优先用逐页 PNG；否则用 blocks JSON 的 bbox 近似
        rend_gray = None
        if args.render_dir:
            for pat in (f"page{pno:02d}.png", f"page{pno}.png", f"p{pno}.png",
                        f"page_{pno}.png"):
                p = os.path.join(args.render_dir, pat)
                if os.path.isfile(p):
                    rend_gray = to_gray(p)
                    break
        if rend_gray is None and args.render_json:
            base = os.path.splitext(os.path.basename(args.render_json))[0]
            cand = os.path.join(paths.OUT_DIR, "render", f"{base}_p{pno}.png")
            if os.path.isfile(cand):
                rend_gray = to_gray(cand)

        if rend_gray is None:
            row["note"] = "缺渲染版图片，只统计原版"
            row["orig_ink_px"] = int(ink_mask(ogray).sum())
            row["orig_lines"] = len(projection_lines(ogray))
            report["pages"].append(row)
            continue

        row["ink"] = compare_ink(ogray, rend_gray)
        row["lines"] = compare_lines(ogray, rend_gray, args.dpi / 72.0)
        # 位置口径的直接度量（±6px ≈ 3pt @150dpi）
        row["position"] = line_position_accuracy(ogray, rend_gray)
        report["pages"].append(row)
        scores.append(row["ink"]["ink_iou"])
        pos_scores.append(row["position"]["position_accuracy"])

    print(f"文件：{report['pdf']}")
    print(f"{'页':>3} {'墨IoU':>7} {'精确':>7} {'召回':>7} | "
          f"{'原行':>4} {'渲行':>4} {'到位':>5} {'到位率':>7} {'位置精度':>8} "
          f"{'中心偏移中位':>12} {'P95':>6}")
    for row in report["pages"]:
        if "ink" not in row:
            print(f"{row['page']:>3}  (缺渲染版)")
            continue
        i, l = row["ink"], row["lines"]
        pos = row.get("position", {}).get("position_accuracy", 0.0)
        print(f"{row['page']:>3} {i['ink_iou']:>7.4f} {i['ink_prec']:>7.4f} "
              f"{i['ink_rec']:>7.4f} | {l['orig_lines']:>4} {l['render_lines']:>4} "
              f"{l['within_tol']:>5} {l['within_tol_ratio']:>7.4f} {pos:>8.4f} "
              f"{str(l['center_dev_pt_median']):>12} {str(l['center_dev_pt_p95']):>6}")
    if scores:
        print(f"\n平均墨迹 IoU = {sum(scores)/len(scores):.4f}")
    if pos_scores:
        print(f"**位置精度（原版墨迹落在渲染墨迹 ±6px≈3pt 内的比例）"
              f"= {sum(pos_scores)/len(pos_scores):.4f}**")
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=1)
        print(f"明细写出：{args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
