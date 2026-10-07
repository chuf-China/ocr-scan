#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ocr_pipeline_layout —— 逐行扫描 PDF，输出**带原版式坐标**的 JSON，并渲染成原格式网页。

和 `ocr_pipeline_blocks` 的区别：
    旧版按"结构块"输出（题号/段落/表格），前端是卡片流——**版式信息丢了**。
    本版按**行**输出，每行带页面坐标与字号，前端按 A4 画布绝对定位渲染，
    读起来就是试卷原样（含原分行、缩进、居中标题、填空下划线占位）。

流程（每页）：
    ① pypdfium2 渲染成图（150 dpi）
    ② 像素投影检测**文本行盒**（真正有墨的行；把填空下划线之类 2~3px 的碎行并进相邻行）
    ③ 逐行裁图 → 本地 VLM-OCR → 该行的文字（含 LaTeX 公式）
    ④ 坐标换回 PDF 点（A4 = 595.3×841.9pt），附带行高
    ⑤ 6 页按顺序拼成一份 JSON；前端按序竖排，A4 比例，和原卷一样一页接一页

为什么逐行而不是整页：整页 OCR 会把 5 行合成一段，再想还原原分行就得靠猜；
逐行裁图天然保留原分行，每行质量也更高（实测 0.1~0.3s/行）。

用法：
    python src/ocr_pipeline_layout.py --pdf data/试卷.pdf --out out/paper.layout.json
    python src/ocr_pipeline_layout.py --pdf data/试卷.pdf --pages 1-2 --dpi 200
    python src/ocr_pipeline_layout.py --serve-only --out out/paper.layout.json

依赖：pypdfium2, numpy, Pillow（渲染与投影）；本地 OCR 引擎（默认 :8081）。
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import html as html_lib
import io
import json
import os
import re
import statistics
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402
from ocr_common import (  # noqa: E402
    DEFAULT_DPI, DEFAULT_HOST, DEFAULT_MAX_TOKENS, DEFAULT_MODEL, DEFAULT_PORT,
    DEFAULT_TIMEOUT, OCR_PROMPT, check_health, default_prompt_for,
    import_pdfium, ocr_png, serve,
)

INK_THRESHOLD = 200          # 灰度小于它算墨
UNDERLINE_GAP_PX = 4         # 薄行程距上一行多近才算它的下划线
X_PAD_PX = 8                 # 裁图左右留白，避免蹭掉边缘笔画
Y_PAD_PX = 3                 # 裁图上下留白（下划线占位符依赖它）


DIGIT_RUN_RE = re.compile(r"\d{20,}")
ISOLATED_LABEL_RE = re.compile(r"^[A-Za-z](?:_\{?[0-9A-Za-z]\}?)?\.??$")
# 模型给出的插图占位符：bbox 四个值相对**页宽/页高**归一化到 0~1000。
# 原始提示词就是这么约定的（见 ocr_common.OCR_PROMPT），所以可以换算回真实区域。
IMG_PLACEHOLDER_RE = re.compile(
    r'<img\b[^>]*?bbox_(\d+)_(\d+)_(\d+)_(\d+)[^>]*?>', re.I)


def extract_img_placeholders(text: str) -> tuple[list[dict], str]:
    """从 OCR 文本里取出 `<img ... bbox_l_t_r_b ...>` 占位符，返回 (框列表, 去掉占位符的文本)。

    bbox 是 0~1000 归一化坐标，除以 1000 再乘页宽/页高即得真实区域。必须在清理文本
    **之前**调用——清理会把标签删掉，坐标也就没了。
    """
    boxes: list[dict] = []
    for m in IMG_PLACEHOLDER_RE.finditer(text):
        l, t, r, b = (int(m.group(i)) for i in range(1, 5))
        if r <= l or b <= t:
            continue
        boxes.append({
            "norm": [l, t, r, b],
            "rel": [l / 1000.0, t / 1000.0, r / 1000.0, b / 1000.0],
        })
    return boxes, IMG_PLACEHOLDER_RE.sub("", text)


def text_coverage_mask(chars: list[dict], gray_shape: tuple[int, int],
                       scale: float) -> np.ndarray | None:
    """把"文字层里能正确解码的字符"画成覆盖蒙版。

    这份卷子的数学符号是坏的私有字形（前景码不在 Unicode 可用区），但中文与 ASCII 是
    好的。图（几何图、坐标图）在这些位置**没有**可用字符，于是蒙版能把图和文字分开。
    返回 None 表示没有可用的文字层（纯扫描件）。
    """
    if not chars:
        return None
    mask = np.zeros(gray_shape, dtype=bool)
    h, w = gray_shape
    kept = 0
    for c in chars:
        ch = c.get("text") or ""
        cp = ord(ch) if ch else 0
        good = (0x4E00 <= cp <= 0x9FFF or 0x3000 <= cp <= 0x303F
                or 0xFF00 <= cp <= 0xFFEF or ch.isdigit() or ch.isalpha())
        if not good:
            continue
        x0 = max(0, int(c["x0"] * scale))
        x1 = min(w, int(c["x1"] * scale) + 1)
        y0 = max(0, int(c["top"] * scale))
        y1 = min(h, int(c["bottom"] * scale) + 1)
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = True
            kept += 1
    return mask if kept else None


def _ink_text_ratio(ink: np.ndarray, mask: np.ndarray | None,
                    top: int, bottom: int, left: int, right: int) -> float | None:
    """区域内的墨迹里，有多大比例落在文字蒙版上。None = 没有蒙版可用。"""
    if mask is None:
        return None
    sub_ink = ink[top:bottom + 1, left:right + 1]
    total = int(sub_ink.sum())
    if total == 0:
        return None
    return float((sub_ink & mask[top:bottom + 1, left:right + 1]).sum()) / total


def split_and_classify_regions(gray: np.ndarray, typical_h: float,
                               mask: np.ndarray | None) -> tuple[list[dict], list[dict]]:
    """把"异常高的内容块"拆开，分成**正文行组**与**插图区**两类。

    为什么需要：行墨迹投影对正文很准，但遇到插图会和文字并成大块。实测第 1 页把
    Q8 的 `cases` 公式和插图并成 110px 块、第 4 页把立体图和 Q19 的题干并成 308px 块。

    判据用**墨迹落在文字蒙版上的比例**（见 text_coverage_mask）：
      · 比例高 → 正文行组，交给 OCR 逐行排版（表格也走这条路，文字层字符全在）；
      · 比例低 → 插图区（几何图/坐标图的线条不在文字层里）。
    没有文字层时 mask 为 None，一律判正文，行为与旧版一致。
    """
    ink = gray < INK_THRESHOLD
    row_ink = ink.sum(axis=1)
    runs: list[list[int]] = []
    start = None
    for i, has in enumerate(row_ink > 0):
        if has and start is None:
            start = i
        elif not has and start is not None:
            runs.append([start, i - 1])
            start = None
    if start is not None:
        runs.append([start, len(row_ink) - 1])

    text_lines: list[dict] = []
    figures: list[dict] = []
    gap_min = max(18, int(typical_h * 0.9))

    for top, bottom in runs:
        h = bottom - top + 1
        if h < typical_h * 1.7:
            continue                        # 正常单行，已在 lines 里
        band = ink[top:bottom + 1]
        cols = np.where(band.any(axis=0))[0]
        if len(cols) == 0:
            continue
        left, right = int(cols[0]), int(cols[-1])

        # 竖向：按横向空白带切段（字距不会这么宽，图形元素之间会）
        pieces: list[tuple[int, int]] = []
        run_start, empty_since = top, None
        for y in range(top, bottom + 1):
            if band[y - top].any():
                if empty_since is not None and (y - empty_since) >= gap_min:
                    pieces.append((run_start, empty_since - 1))
                    run_start = y
                empty_since = None
            elif empty_since is None:
                empty_since = y
        pieces.append((run_start, bottom))

        for pt, pb in pieces:
            if pb < pt:
                continue
            sub = ink[pt:pb + 1]
            sub_cols = np.where(sub.any(axis=0))[0]
            if len(sub_cols) == 0:
                continue
            pl, pr = int(sub_cols[0]), int(sub_cols[-1])
            ratio = _ink_text_ratio(ink, mask, pt, pb, pl, pr)
            # 阈值 0.20 是**实测**定的。第 1~6 页所有异常高块的 mask 比例实测为：
            #   真插图: 半圆图 0.0 / 坐标图 0.0 / 立体图 0.0 / 频率分布直方图 0.247
            #   正文档: cases 公式 0.545、表格 0.247、题组行 0.58 ~ 0.91
            # 直方图带刻度文字（所以不是 0，但仍远低于任何题组行），取 0.20 落在空档里。
            if ratio is not None and ratio < 0.20:
                figures.append({"px": [max(0, pl - 6), max(0, pt - 6),
                                       min(gray.shape[1], pr + 7),
                                       min(gray.shape[0], pb + 7)]})
            else:
                text_lines.append({
                    "px_top": pt, "px_bottom": pb,
                    "px_left": pl, "px_right": pr,
                    "px_height": pb - pt + 1,
                    "ink": int(sub[:, pl:pr + 1].sum()),
                    "underline": False,
                    "typical_h": typical_h,
                })
    return text_lines, figures


def _crop_figure_px(pil, px_box: list[int], page_w: float, page_h: float) -> dict | None:
    """按像素盒裁出插图，返回 base64 PNG、像素与点坐标。"""
    px_l, px_t, px_r, px_b = (max(0, px_box[0]), max(0, px_box[1]),
                              min(pil.width, px_box[2]), min(pil.height, px_box[3]))
    if px_r - px_l < 24 or px_b - px_t < 24:
        return None
    sx, sy = pil.width / page_w, pil.height / page_h
    crop = pil.crop((px_l, px_t, px_r, px_b))
    buf = io.BytesIO()
    crop.save(buf, "PNG", optimize=True)
    return {
        "px": [px_l, px_t, px_r, px_b],
        "pt": [round(px_l / sx, 2), round(px_t / sy, 2),
               round(px_r / sx, 2), round(px_b / sy, 2)],
        "w_pt": round((px_r - px_l) / sx, 2),
        "h_pt": round((px_b - px_t) / sy, 2),
        "png_base64": base64.b64encode(buf.getvalue()).decode("ascii"),
    }


def clip_figures_to_avoid_text(pil, figures: list[dict], lines: list[dict],
                               page_w: float, page_h: float,
                               min_w: float = 40.0, min_h: float = 28.0) -> int:
    """把插图裁片的边界**收**到完全不压正文行。

    为什么必须做：插图区与"异常高块"会被并成一个大块，裁片可能伸进题干里——实测第 2 页
    `fig1` 被并成 126x205pt，把 Q12 的题干盖住 30~36%。裁片是**从原稿裁的图**，盖在文字上
    就是"吃字"。

    ⚠️ 判据是**交叠为零**，不是"交叠占该行比例小于某阈值"：题干行很长（约 415pt），
    裁片压掉最左边 46pt 只占该行 11%，按比例看"很小"，但压掉的正是题号「12.」和首字
    ——实测就这么漏过一次。插图和正文在视觉上不该有任何重叠。

    做法：逐边贪心内收（4pt/轮），每轮挑"内收后总交叠面积最小"的一边，直到交叠为 0 或
    到最小尺寸（此时若仍交叠则放弃收缩，保留原样并标注）。改了边界就**按新边界重裁**。
    返回被收缩的裁片数量。
    """
    changed = 0
    for fig in figures:
        if "png_base64" not in fig:
            continue
        kx = pil.width / page_w
        ky = pil.height / page_h

        def overlap(box: list[float]) -> float:
            bx0, by0, bx1, by1 = box
            if bx1 - bx0 < min_w or by1 - by0 < min_h:
                return float("inf")
            total = 0.0
            for l in lines:
                ix = min(l["right"], bx1) - max(l["left"], bx0)
                iy = min(l["bottom"], by1) - max(l["top"], by0)
                if ix > 0 and iy > 0:
                    total += ix * iy
            return total

        box = list(fig["pt"])
        if overlap(box) <= 0:
            continue
        for _ in range(60):
            cur = overlap(box)
            if cur <= 0:
                break
            best = None
            for side in range(4):
                cand = list(box)
                cand[side] += 4 if side in (0, 1) else -4
                score = overlap(cand)
                if best is None or score < best[0]:
                    best = (score, cand)
            if best is None or best[0] >= cur:
                break
            box = best[1]
        if overlap(box) > 0:
            fig["clip_failed"] = True       # 收不到零交叠（图与文字交缠），留给人工看
            continue
        new_px = [max(0, int(round(box[0] * kx))), max(0, int(round(box[1] * ky))),
                  int(round(box[2] * kx)), int(round(box[3] * ky))]
        crop = _crop_figure_px(pil, new_px, page_w, page_h)
        if crop:
            fig.update(crop)
            fig["clipped_to_avoid_text"] = True
            changed += 1
    return changed


def collect_figures(pil, regions: list[dict], page_w: float, page_h: float,
                    keep_images: bool) -> list[dict]:
    """把区域裁成 PNG 并记录坐标（像素 + PDF 点）。

    区域可能来自两类检测，且会互相重叠：文字蒙版判定出的图形区、表格框线判定出的表格区
    （第 4 页立体图的若干长棱线会被当成框线，于是同一块同时被两类命中）。
    重叠时**合并**成一个裁片，避免页面上出现两张半透明叠着的半截图。
    """
    merged: list[dict] = []
    for region in regions:
        box = list(region["px"])
        kind = region.get("kind", "raster-diagram")
        target = None
        for m in merged:
            x0, y0, x1, y1 = box
            mx0, my0, mx1, my1 = m["px"]
            ix = min(x1, mx1) - max(x0, mx0)
            iy = min(y1, my1) - max(y0, my0)
            if ix <= 0 or iy <= 0:
                continue
            inter = ix * iy
            union = ((x1 - x0) * (y1 - y0) + (mx1 - mx0) * (my1 - my0) - inter)
            # IoU 阈值要低（0.2）：两个检测器对同一块内容给出的框常有明显偏移，
            # 用"交叠占较小框的比例"判会让它们各自成一项，页面上出现两张叠着的半截图
            # （实测第 2 页直方图被裁成 137x76 与 55x147 两张）。
            if union > 0 and inter / union >= 0.2:
                target = m
                break
        if target is None:
            merged.append({"px": box, "kinds": [kind]})
        else:
            x0, y0, x1, y1 = target["px"]
            target["px"] = [min(x0, box[0]), min(y0, box[1]),
                            max(x1, box[2]), max(y1, box[3])]
            if kind not in target["kinds"]:
                target["kinds"].append(kind)

    figures: list[dict] = []
    for region in merged:
        crop = _crop_figure_px(pil, region["px"], page_w, page_h)
        if crop is None:
            continue
        entry = {"index": len(figures),
                 "source": "+".join(region["kinds"]), **crop}
        if not keep_images:
            entry.pop("png_base64", None)
        figures.append(entry)

    # 去掉"被更大裁片基本包含"的重复项，并把**显著交叠**的块并成一块。
    # 同一块内容常被两类检测各裁一次，框还互相错开（实测第 2 页半圆图被裁成 126x74 与
    # 55x147 两张，交叠仅 1943px²）；不并的话页面上会出现两张叠着的半截图。
    order = sorted(figures, key=lambda f: -(f["w_pt"] * f["h_pt"]))
    kept: list[dict] = []
    for fig in order:
        merged_into = None
        for big in kept:
            bx0, by0, bx1, by1 = big["pt"]
            fx0, fy0, fx1, fy1 = fig["pt"]
            ix = min(bx1, fx1) - max(bx0, fx0)
            iy = min(by1, fy1) - max(by0, fy0)
            if ix <= 0 or iy <= 0:
                continue
            inter = ix * iy
            area = max(1e-6, (fx1 - fx0) * (fy1 - fy0))
            if inter / area >= 0.6 or inter >= 800:
                merged_into = big
                break
        if merged_into is None:
            kept.append(fig)
            continue
        bx0, by0, bx1, by1 = merged_into["pt"]
        fx0, fy0, fx1, fy1 = fig["pt"]
        merged_into["source"] = "+".join(sorted(
            set(merged_into["source"].split("+")) | set(fig["source"].split("+"))))
        # 重裁：坐标并起来之后必须重新出图，否则裁片仍旧只覆盖原来的范围
        crop = _crop_figure_px(
            pil, [int(round(min(bx0, fx0) * pil.width / page_w)),
                  int(round(min(by0, fy0) * pil.height / page_h)),
                  int(round(max(bx1, fx1) * pil.width / page_w)),
                  int(round(max(by1, fy1) * pil.height / page_h))],
            page_w, page_h)
        if crop:
            merged_into.update(crop)
    for i, fig in enumerate(kept):
        fig["index"] = i
    return kept


HTML_TABLE_RE = re.compile(r"<table\b.*?</table>", re.S | re.I)


def drop_lines_inside_figures(page_lines: list[dict], figures: list[dict]) -> tuple[list[dict], int]:
    """丢掉"基本落在插图裁片里"的正文行。

    为什么必须丢：插图裁片是**从原稿整块裁下来的图**，里面本来就有图上的字母标注
    （`A₁ D₁ B₁ …`、`F₁ O F₂ x`）。而 OCR 对插图区也会把那串字母读成一行文字，
    于是渲染时**图上有一套、文字层又叠一套**——实测第 4、5 页各有一行的盒子 100% 落在
    插图框内（`\\(A_{1}\\)\\n\\n\\(D_{1}\\)…` 与 `\\(F_1\\) A\\n\\n\\(O\\)…`）。
    裁片已经把标注画出来了，文字层再来一份就是重复。
    """
    if not figures:
        return page_lines, 0
    kept: list[dict] = []
    dropped = 0
    for line in page_lines:
        area = max(1e-6, (line["right"] - line["left"]) * (line["bottom"] - line["top"]))
        inside = False
        for fig in figures:
            fx0, fy0, fx1, fy1 = fig["pt"]
            ix = min(line["right"], fx1) - max(line["left"], fx0)
            iy = min(line["bottom"], fy1) - max(line["top"], fy0)
            if ix > 0 and iy > 0 and (ix * iy) / area >= 0.5:
                inside = True
                break
        if inside:
            dropped += 1
        else:
            kept.append(line)
    return kept, dropped


def _collapse_runs(idx: list[int], tol: int = 3) -> list[int]:
    out: list[int] = []
    for i in idx:
        if not out or i - out[-1] > tol:
            out.append(i)
        else:
            out[-1] = (out[-1] + i) // 2
    return out


def detect_table_grids(gray: np.ndarray, typical_h: float,
                       min_run: int = 70, min_gap: int = 6) -> list[dict]:
    """从像素图上识别**表格框线**，返回表格区域的像素盒。

    为什么要单独认表格：PaddleOCR-VL（现为首选引擎）在官方前缀下把表格**逐格输出成换行
    文本**（`学科\\n语文\\n数学\\nA学校\\n1\\n2…`），行列结构从文本里不可恢复；加提示词会
    让它退化（实测重复输出 `<br>` 达 23 秒）。既然结构拿不回来，就直接把表格区域当图裁出来——
    内容与结构 100% 保真，且与 OCR 引擎无关。

    ⚠️ 判据必须是**局部最长连续游程**，不能用"占整页宽度比例"：第 17 题的表格只有 320px 宽，
    而页面宽 1241px，占宽比例最高才 0.26——用全页比例永远检不出来（这个坑先踩过一次）。
    表格框线的特征是一条**长且连续**的深色游程；普通文字行的游程很短（字与字之间有间隙）。
    """
    ink = gray < INK_THRESHOLD
    h, w = ink.shape
    if h < 40 or w < 40:
        return []

    def longest_runs(arr2d: np.ndarray, axis: int) -> list[int]:
        """逐条扫描线求最长连续 True 游程长度。"""
        out: list[int] = []
        for line in arr2d:
            best = cur = 0
            for v in line:
                cur = cur + 1 if v else 0
                if cur > best:
                    best = cur
            out.append(best)
        return out

    h_runs = longest_runs(ink, 1)                 # 每行的最长横向游程
    v_runs = longest_runs(ink.T, 1)               # 每列的最长纵向游程
    # 框线必须比"表格的最小格子"长；且**不能接近整页**——跨整页的长线与别的长线一相交，
    # 就会把整页圈成一个"格"（实测第 3 页裁片被撑到 147x322pt，把标题、题干、选项全圈进去，
    # 既重复又遮挡正文）。
    h_rules = _collapse_runs(
        [i for i, r in enumerate(h_runs)
         if min_run <= r <= max(min_run, 0.85 * w)], tol=4)
    v_rules = _collapse_runs(
        [i for i, r in enumerate(v_runs)
         if min_run <= r <= max(min_run, 0.85 * h)], tol=4)
    if len(h_rules) < 2 or len(v_rules) < 2:
        return []

    # 表格的框线必须**成组**：把"离前一条线特别远"的线当成杂散线剔掉。
    # 用中位间距的倍数判定，**不能**用"贪心连续扩簇"——真表格首列常比其余列宽得多
    # （实测竖线 [174,274,373,470] 的间距是 100/99/97，而页边线在 174 左边很远处；
    #  贪心在遇到大间距时会停住，结果只留下 [174]，表格反而检不出来）。
    def densest_band(rules: list[int]) -> list[int]:
        """取"最密的一段连续线"作为表格的框线带。

        ⚠️ 两个都踩过的坑：
          ① 用整体中位间距判杂散线不行——离群的大间距会把中位数抬高，反而把页边线留下
             （H=[202,589,654,730,796,862] 的中位是 66，按 2.5 倍就容下了 387）；
          ② 必须与"最近一条保留的线"比，否则剔掉一条后参照物跑回更早位置，该剔的没剔。
        改为：对每个起点贪心扩簇（步长上限 = 25 分位间距 ×2），取最大的一簇。
        """
        if len(rules) < 2:
            return rules
        gaps = [b - a for a, b in zip(rules, rules[1:])]
        step = statistics.quantiles(gaps, n=4)[0] if len(gaps) >= 4 else min(gaps)
        step_limit = max(step * 2.0, 30)
        best: list[int] = []
        for i in range(len(rules)):
            band = [rules[i]]
            for cur in rules[i + 1:]:
                if cur - band[-1] <= step_limit:
                    band.append(cur)
                else:
                    break
            if len(band) > len(best):
                best = band
        return best

    h_band, v_band = densest_band(h_rules), densest_band(v_rules)
    if len(h_band) < 2 or len(v_band) < 2:
        return []

    # 表格盒 = 框线带的极值。**不要**再用"框线实际墨迹跨度"去收紧：
    # 表格里的文字也会被 `any(axis)` 当成墨迹，于是又把盒子按行/列推回整块内容区，
    # 反而把题干和标题重新圈进来（实测 p3 从 142x131pt 被推回 147x322pt）。
    left, right = v_band[0], v_band[-1]
    top, bottom = h_band[0], h_band[-1]

    width, height = right - left, bottom - top
    if width < 60 or height < 24:
        return []
    if width > 0.45 * w or height > 0.45 * h:
        return []
    n_rows = len(h_band) - 1
    n_cols = len(v_band) - 1
    # 至少 2 列：只有一条竖线的"表"与图表的坐标轴无法区分（实测第 2 页直方图的 y 轴 + 网格
    # 被误判成 1x3 的表格）。真表格都是多列的。
    if n_rows < 1 or n_cols < 2:
        return []

    # 框线必须**几乎横贯/纵贯**这个区域。
    # ⚠️ 只按"游程 ≥ 固定像素"不够：汉字的竖笔画也有 70~140px 的游程，实测第 2 页由
    # 「设/当/…」的笔画凑出了 194/234/265/297 四条"竖线"，凭空生成了一个窄高的假表格，
    # 再与半圆图一合并，裁片就变成 L 形包围盒，把 Q12 题干盖住 30%。
    # 真表格的框线必然接近整表跨度（本题第 17 题的框是 296x273px）。
    span_w = right - left
    span_h = bottom - top
    h_need = max(min_run, int(0.7 * span_w))
    v_need = max(min_run, int(0.7 * span_h))
    h_ok = sum(1 for i in range(top, bottom + 1) if h_runs[i] >= h_need)
    v_ok = sum(1 for i in range(left, right + 1) if v_runs[i] >= v_need)
    if h_ok < 2 or v_ok < 2:
        return []
    n_rows = len(h_band) - 1
    n_cols = len(v_band) - 1
    if n_rows < 1 or n_cols < 2:
        return []
    grids: list[dict] = []
    grids.append({
        "px": [max(0, left - 5), max(0, top - 5),
               min(w, right + 6), min(h, bottom + 6)],
        "rows": n_rows, "cols": n_cols, "kind": "table-grid",
    })
    return grids


def _cell_suspicious(cell: str) -> bool:
    """单元格内容是否像"编出来的"：长数字串、数字串重复、年份列反复出现。"""
    if re.search(r"\d{8,}", cell):
        return True
    if re.search(r"[A-Za-z,]{6,}", cell) and re.search(r"(\d{1,4}[,.]){3,}", cell):
        return True
    if re.search(r"(20\d\d年.{0,4}){3,}", cell):
        return True
    return False


def _table_shape_ok(grid: list[list[str]]) -> bool:
    """判断一个解析出来的"表"是否可信。

    必须筛：模型对正文也常吐 `<table>`，实测第 1 页吐过 **100 列**的表（表头是 1..100）、
    第 3 页吐过"项目/2018年/2019年/2020年 收入 1,000.00"的**财务表**、还有"序号/题目/答案"
    列 100 行同一个日期的表。把这种当真表渲染，比显示成乱码还糟——乱码一眼可辨，
    结构化的假表会被当成原文。真实表格很小：本题的学科分布表是 3 列 4 行。
    """
    if not grid:
        return False
    cols = max(len(r) for r in grid)
    if not (1 <= cols <= 8) or len(grid) > 20:
        return False
    head = [c for c in grid[0] if c]
    # ① 表头整行都是"年份"或整行都是纯数字（1..100 那种）→ 幻觉
    if len(head) >= 3 and all(re.fullmatch(r"20\d\d年?", c) for c in head):
        return False
    if len(head) >= 5 and all(re.fullmatch(r"\d{1,4}", c) for c in head):
        return False
    # ② 单元格内容可疑
    for row in grid:
        for cell in row:
            if len(cell) > 40 or _cell_suspicious(cell):
                return False
    # ③ 同一行内容整行重复出现 3 次以上（如 100 行都是同一个日期）→ 幻觉
    rowtexts = ["|".join(r) for r in grid]
    for row in rowtexts[1:]:
        if row.strip("|") and rowtexts[1:].count(row) >= 3:
            return False
    # ④ 表头含长中文片段（>6 字）→ 那是散文被硬切成的伪表（实测"光线所在直线交抛物线"）
    if any(len(c) > 6 and re.search(r"[\u4e00-\u9fff]", c) for c in head):
        return False
    # ⑤ 表头夹杂大量空单元 → 伪表
    if sum(1 for c in grid[0] if not c) >= 4:
        return False
    # ⑥ 同一"数字串"在整表里反复出现（实测财务表 1,000.00 出现 6 次）→ 幻觉
    numbers = re.findall(r"\d[\d,]*\.\d+|\d{4,}", " ".join(
        c for row in grid for c in row))
    if numbers and max(numbers.count(n) for n in set(numbers)) >= 3:
        return False
    # ⑦ 整行单元格几乎都含千分位金额格式
    money_rows = sum(1 for row in grid
                     if sum(1 for c in row if re.search(r"\d,\d{3}\.\d{2}", c)) >= 2)
    if money_rows >= 2:
        return False
    # ⑧ "数字阶梯"：行里出现连续递增整数（实测伪表 $x$/$y$ 的 1,2 / 2,3 / … / 9,10）。
    #    真表不会这么巧。
    ladder = 0
    for row in grid[1:]:
        nums = [int(c) for c in row if re.fullmatch(r"\d{1,3}", c)]
        if len(nums) >= 2 and nums == list(range(nums[0], nums[0] + len(nums))):
            ladder += 1
    if ladder >= 3:
        return False
    # ⑨ "滑窗重复"伪表：某行与下一行是两个错开一位的重复片段
    #    （实测 `命题1/命题2/命题3` 然后是 `命题2/命题3/命题4`——模型在表格上复读了文本幻觉）
    for i in range(len(grid) - 1):
        if len(grid[i]) >= 2 and grid[i][1:] == grid[i + 1][:-1]:
            return False
    return True


def parse_html_tables(text: str) -> tuple[list[dict], str]:
    """把文本里的 <table> 解析成 {headers, rows}，并从文本里摘掉。

    之前 `clean_ocr_text` 直接**删掉**表格标签，结果第 17 题的表格被压成一行
    `学科语文数学A学校12B学校11`——比识别错更糟。表格要单独成结构，由前端画成真表格。
    形状不可信的（见 _table_shape_ok）不收录，同时**不销毁文本**，让它按普通行渲染。
    """
    tables: list[dict] = []

    def take(match: re.Match) -> str:
        block = match.group(0)
        grid: list[list[str]] = []
        for tr in re.findall(r"<tr\b.*?</tr>", block, re.S | re.I):
            cells = [re.sub(r"<[^>]+>", "", c) for c in
                     re.findall(r"<t[hd]\b.*?</t[hd]>", tr, re.S | re.I)]
            cells = [html_lib.unescape(c).strip() for c in cells]
            if cells:
                grid.append(cells)
        if grid and _table_shape_ok(grid):
            tables.append({"headers": grid[0], "rows": grid[1:],
                           "source": "html-table"})
            return ""                      # 只摘掉真正采纳的表
        return match.group(0)              # 不可信的留着，当普通文本处理

    return tables, HTML_TABLE_RE.sub(take, text)


def table_from_text(text: str) -> dict | None:
    """从"表格形状的一行"里恢复 {headers, rows}。

    OCR 有时不吐 HTML，只吐空格/换行分隔的表格文本（实测第 17 题：
    `学科语文数学A学校12B学校11`）。这里做保守的形状推断，判据不满足就返回 None，
    绝不把普通公式行误判成表。
    """
    body = text.strip()
    if not body:
        return None
    # ① 多行且各行列数一致 → 表格
    lines = [ln.strip() for ln in body.split("\n") if ln.strip()]
    if len(lines) >= 3:
        cells = [re.split(r"\s{2,}|\t", ln) for ln in lines]
        if len(cells[0]) >= 2 and all(len(c) == len(cells[0]) for c in cells) \
                and _table_shape_ok(cells):
            return {"headers": cells[0], "rows": cells[1:], "source": "text-grid"}
    # ② 单行"中文标签 + 数字"成对出现：学科语文数学A学校12B学校11
    #    ⚠️ 这条启发式**很危险**：任何"汉字+数字"的散文都会命中——实测把第 17 题(3) 的
    #    "求事件…抽到的2人…恰好有1名…"误判成表格，因此额外要求：标签必须是**不同**的，
    #    且成对数量与标签数相当（真表每行一个标签，散文里标签是重复句式）。
    pairs = re.findall(r"([\u4e00-\u9fff]{2,4}|[A-Za-z]学校)(\d{1,3})", body)
    labels = re.findall(r"[\u4e00-\u9fff]{2,4}", body)
    if len(pairs) >= 2 and len(labels) >= 3:
        pair_labels = [l for l, _ in pairs]
        if len(set(pair_labels)) == len(pair_labels) and len(pairs) >= len(labels) - 1:
            headers = labels[:2] + [""]
            rows = [[l, n] for l, n in pairs]
            if _table_shape_ok([headers] + rows):
                return {"headers": headers, "rows": rows, "source": "label-number"}
    return None


def extract_tables(cleaned: str, original: str) -> tuple[list[dict], str]:
    """优先用原始 OCR 文本里的 <table>；没有就尝试从清理后的文本恢复表格形状。"""
    tables, rest = parse_html_tables(original)
    if tables:
        return tables, clean_ocr_text(rest)
    guess = table_from_text(cleaned)
    if guess:
        return [guess], ""
    return [], cleaned


def _is_hallucination(text: str) -> bool:
    """判断一行是不是"对着插图/表格区域编出来的"内容。

    实测三种失败模式：
      ① 超长数字串（`1234567891011...` 几百字符）；
      ② 把图里字母当成行内容（`D₁ B₁ C E F B`）；
      ③ 短片段无限重复（同一行出现 12285 字符的 `.\n\n.\n\n.…`）。
    这些都不是正文，判据只针对"明显病态"，不会误伤正常的数学行。
    """
    body = text.strip()
    if not body:
        return True
    if DIGIT_RUN_RE.search(body):
        return True
    # 未被采纳的表格标记（形状不可信的假表）不能当正文渲染，否则页面上会出现
    # 一长串 `<td>1</td><td>2</td>…`。
    if len(re.findall(r"</t[dh]>", body, re.I)) >= 8:
        return True
    if len(body) > 400:
        return True
    # ③ 重复：任取 3 字符窗口，出现次数远超其可能——正常中文行不可能这样
    if len(body) > 60:
        window = body[:3] if len(body) >= 3 else body
        if body.count(window) > max(6, len(body) // 12):
            return True
    # ② 形如 `$F_1$$A$$y$$D$$O$` / `D₁ B₁ C E F B`：拆开后几乎都是孤立单字母
    tokens = [t for t in re.split(r"[\s\n]+|(?<=\S)(?=\$)", body) if t.strip()]
    if 2 <= len(tokens) <= 14:
        labels = [t for t in tokens if ISOLATED_LABEL_RE.match(t.strip("$\\{} \t"))]
        if len(labels) == len(tokens):
            return True
    # ④ 某个片段**紧邻重复**（如 `命题1命题2命题3命题1命题2命题3…`）。
    #    必须显式找"连续重复串"：垃圾常跟在正常题干后面（`13. 在三维空间中…命题1命题2命题3命题1…`），
    #    所以不能只看整行前缀的周期性。也不能用"整行窗口计数"——数学行里 `$f(x` 出现 4 次、
    #    HTML 表格里 `<tr>` 出现 5 次都是正常的，那样会把正确内容全丢掉（已实测踩过）。
    for start in range(0, min(len(body), 140)):
        for length in range(3, 25):
            end = start + length
            if end + length * 2 > len(body):
                break                      # 起点之后连两次完整的重复都放不下
            pattern = body[start:end]
            if not pattern.strip():
                continue
            # 纯"数字+运算符"的重复是**正常的**数学式（实测 `14+14+14+18+18=78` 被误判），
            # 只有重复内容里含真实文字（汉字/字母）才算垃圾。
            if sum(1 for c in pattern if c.isalpha() or "\u4e00" <= c <= "\u9fff") < 2:
                continue
            repeats = 1
            pos = end
            while pos + length <= len(body) and body[pos:pos + length] == pattern:
                repeats += 1
                pos += length
            # 第 3 次允许只重复一部分（垃圾常被截断）
            tail = body[pos:pos + length]
            partial = len(tail) >= length * 0.5 and sum(
                1 for a, b in zip(tail, pattern) if a == b) >= length * 0.7
            if repeats >= 3 or (repeats == 2 and partial):
                return True
    return False


def clean_ocr_text(text: str) -> str:
    """清掉 OCR 顺手带出来的 Markdown 装饰、图占位符与图形区幻觉。

    注意：这里**不再处理表格**。表格要走 `extract_tables()` 单独成结构——早先直接在
    这里删掉 `<table>` 标签，第 17 题的表格被压成一行 `学科语文数学A学校12B学校11`，
    比识别错更糟。插图同理，由图形区检测负责（`<img>` 占位符坐标不可信，见 README）。
    """
    text = re.sub(r"<img\b[^>]*>", "", text)
    lines = []
    for raw in text.split("\n"):
        line = re.sub(r"^[ \t]*#{1,6}[ \t]*", "", raw).rstrip()
        if DIGIT_RUN_RE.search(line):
            # 幻觉数字串：切掉它，保留同一行里可能的正常文字
            line = DIGIT_RUN_RE.sub("", line).strip()
            if not line:
                continue
        lines.append(line)
    out = "\n".join(lines).strip()
    out = re.sub(r"[ \t]{3,}", "  ", out)
    return out


def log(msg: str) -> None:
    print(f"[layout] {msg}", flush=True)


def die(msg: str, code: int) -> None:
    print(f"[layout] 错误: {msg}", file=sys.stderr)
    raise SystemExit(code)


# ---------------------------------------------------------------- 行检测

def _runs_from_projection(ink_rows: np.ndarray) -> list[list[int]]:
    """ink_rows 为布尔数组 → [start, end] 行程列表。"""
    runs: list[list[int]] = []
    start = None
    for i, has_ink in enumerate(ink_rows):
        if has_ink and start is None:
            start = i
        elif not has_ink and start is not None:
            runs.append([start, i - 1])
            start = None
    if start is not None:
        runs.append([start, len(ink_rows) - 1])
    return runs


def _merge_small_runs(runs: list[list[int]], min_h: int, gap: int) -> list[list[int]]:
    """把"薄行程"并进紧邻的上一个行程。

    填空下划线（"_____"）在投影里是一条 2~4px 高的独立薄带：单独裁图送去 OCR，
    模型会对着一条直线胡编（实测输出满屏 "## 1"）。它本来就属于上一行的填空符，
    所以并进上一行即可。

    ⚠️ 关键是**只做一次、且只看薄行程本身**：早先的写法对"上一行也薄"也触发合并，
    于是一串行程会级联成一条，整页塌成几行（实测第 4 页 24 行塌成 9 行）。
    """
    merged: list[list[int]] = []
    for run in runs:
        run_h = run[1] - run[0] + 1
        if merged and run_h < min_h and (run[0] - merged[-1][1] - 1) <= gap:
            merged[-1][1] = max(merged[-1][1], run[1])   # 薄带并进上一行程
        else:
            merged.append(list(run))
    return merged


def dedupe_lines(lines: list[dict]) -> list[dict]:
    """合并重复行盒。

    常规行来自行墨迹投影，补充行组来自"异常高块"的重新切分，两者会在同一处同时命中，
    于是同一行出现两次（实测第 2 页第 12/15/16 题各重复一次、第 5 页第 20 题重复一次）。
    纯按 y 去重会误伤真正相邻的两行，所以按**面积交叠比例**判定：短边被长边覆盖超过
    55% 即视为同一行，保留较大的那个盒。
    """
    ordered = sorted(lines, key=lambda l: (l["px_top"], l["px_left"]))
    kept: list[dict] = []
    for line in ordered:
        dup = None
        for i in range(len(kept) - 1, max(-1, len(kept) - 4), -1):
            other = kept[i]
            iy = (min(line["px_bottom"], other["px_bottom"])
                  - max(line["px_top"], other["px_top"]))
            ix = (min(line["px_right"], other["px_right"])
                  - max(line["px_left"], other["px_left"]))
            if iy <= 0 or ix <= 0:
                continue
            inter = iy * ix
            area_line = (line["px_bottom"] - line["px_top"] + 1) * (line["px_right"] - line["px_left"] + 1)
            area_other = (other["px_bottom"] - other["px_top"] + 1) * (other["px_right"] - other["px_left"] + 1)
            if inter > 0.55 * min(area_line, area_other):
                dup = i
                break
        if dup is None:
            kept.append(line)
        else:
            # 保留更大的盒（通常是把该行完整包住的补充组），只补上缺失的 x 范围
            other = kept[dup]
            other["px_left"] = min(other["px_left"], line["px_left"])
            other["px_right"] = max(other["px_right"], line["px_right"])
            other["px_top"] = min(other["px_top"], line["px_top"])
            other["px_bottom"] = max(other["px_bottom"], line["px_bottom"])
    return kept


def majority_vote(texts: list[str]) -> tuple[str, int]:
    """把同一张图的多次识别结果投票，返回 (得票最多的文本, 票数)。

    为什么要投票：引擎跑的是 `--temp 0`，但它**并不是确定性的**——实测同图同参 3 次
    全对，而另一次整卷跑到第 3 页时，一张只含表格的裁图被输出成了
    `项目2018年2019年2020年收入1,000.001,000.001,000.00…` 这种完全无关的财务表。
    这类幻觉是低频随机的，多跑一次投票就能压掉（0.1~0.4s/行的代价可以接受）。
    先按完全相同分组；都不相同时退化为相似度投票（用长度接近度近似）。
    """
    cleaned = [t.strip() for t in texts if t and t.strip()]
    if not cleaned:
        return "", 0
    counts: dict[str, int] = {}
    for t in cleaned:
        counts[t] = counts.get(t, 0) + 1
    best, votes = max(counts.items(), key=lambda kv: kv[1])
    if votes > 1 or len(cleaned) == 1:
        return best, votes
    # 全不相同：选与其它结果长度最接近的那个（幻觉通常长度异常）
    def score(t: str) -> tuple[int, int]:
        near = sum(1 for u in cleaned if abs(len(u) - len(t)) <= max(4, len(t) * 0.15))
        return near, -abs(len(t) - int(statistics.median(len(u) for u in cleaned)))
    best = max(cleaned, key=score)
    return best, 1


def detect_lines(gray: np.ndarray) -> tuple[list[dict], float]:
    """像素投影检测**文本行**，返回 (行盒列表, 该页典型行高 px)。

    投影法在这里比"按 PDF 词盒基线聚类"稳得多。词盒法的坑：数学公式是坏的私有字形，
    每个字形自成一个小盒，基线略有差异就会被聚成各自的"行"——实测第 1 页裂成 44 条
    碎片（`$x^{2}$` 和 `$y^{2}$` 各占一条），而页面真实行数只有 24。

    处理：
      ① 行墨迹投影 → 行程
      ② 薄行程（填空下划线、分式横线）按"又薄又宽"判定并**回填 x 范围**（下划线可能比
         本行文字更宽，不回填就会在渲染时被截掉）
      ③ 不做盒内切分：切分容易切歪（把 `\\frac{x^2}{9}` 的分子/分数线/分母切成三个盒）。
         一个盒 = 一个**行组**，OCR 出来的换行在渲染时就按换行排。
    """
    ink = gray < INK_THRESHOLD
    lines, typical = _lines_from_ink(ink)
    return lines, typical


def _row_cols(ink: np.ndarray, top: int, bottom: int) -> tuple[int, int] | None:
    band = ink[top:bottom + 1]
    cols = np.where(band.any(axis=0))[0]
    if len(cols) == 0:
        return None
    return int(cols[0]), int(cols[-1])


def _lines_from_ink(ink: np.ndarray) -> tuple[list[dict], float]:
    row_ink = ink.sum(axis=1)
    runs: list[list[int]] = []
    start = None
    for i, has in enumerate(row_ink > 0):
        if has and start is None:
            start = i
        elif not has and start is not None:
            runs.append([start, i - 1])
            start = None
    if start is not None:
        runs.append([start, len(row_ink) - 1])

    # 典型行高：取够高的行程的中位数（薄行程是下划线/横线，不参与）
    tall = [r[1] - r[0] + 1 for r in runs if r[1] - r[0] + 1 >= 8]
    typical = statistics.median(tall) if tall else 20.0
    thin_limit = max(4.0, typical * 0.40)

    out: list[dict] = []
    for run in runs:
        top, bottom = run
        span = _row_cols(ink, top, bottom)
        if span is None:
            continue
        left, right = span
        h = bottom - top + 1
        w = right - left + 1
        is_thin = h <= thin_limit and w >= h * 4

        if is_thin and out and (top - out[-1]["_pb"] - 1) <= UNDERLINE_GAP_PX:
            # 下划线/横线：并进上一行，但只扩 x 范围，不动 y 范围
            out[-1]["_pl"] = min(out[-1]["_pl"], left)
            out[-1]["_pr"] = max(out[-1]["_pr"], right)
            out[-1]["underline"] = True
            continue
        if is_thin:
            continue                     # 孤立横线（如分隔线），不是文字

        out.append({"_pt": top, "_pb": bottom, "_pl": left, "_pr": right,
                    "underline": False})

    # 不做盒内切分：投影偶尔会把"上下一小段"并在一个盒里（如 cases 的两行分支、
    # 公式块）。切分很容易切歪——实测把 `\frac{x^2}{9}` 的分子、分数线、分母切成了
    # 三个盒。改成一个盒 = 一个**行组**：OCR 出来的多行文本在渲染时按换行原样排，
    # 反而更贴近原稿（原文的换行就是这些换行）。
    result: list[dict] = []
    for line in out:
        top, bottom, left, right = line["_pt"], line["_pb"], line["_pl"], line["_pr"]
        result.append({
            "px_top": top, "px_bottom": bottom,
            "px_left": left, "px_right": right,
            "px_height": bottom - top + 1,
            "ink": int(ink[top:bottom + 1, left:right + 1].sum()),
            "underline": line.get("underline", False),
            "typical_h": typical,
        })
    return result, typical


def _is_noise(line: dict) -> bool:
    """过滤几乎无墨的碎行。"""
    return line["ink"] < 24 or line["px_height"] < 4


# ---------------------------------------------------------------- 字号（可选增强）

class PdfFontProbe:
    """用 pdfplumber 读 PDF 文字层的字号。

    注意：这份卷子的数学符号来自 Kingsoft 私有字库，前景码是坏的（不在 Unicode
    可用区），但**中文正文字符是好的**。所以字号只作为渲染时的参考值，文字一律用
    OCR 的结果——坐标来自像素投影，不依赖文字层，没有文字层的纯扫描件一样能用。
    """

    def __init__(self, pdf_path: str):
        self.pages = None
        try:
            import pdfplumber
            self._pdf = pdfplumber.open(pdf_path)
            self.pages = self._pdf.pages
        except Exception as exc:  # noqa: BLE001
            log(f"pdfplumber 不可用，字号改用行高估算（{exc}）")

    def char_boxes(self, page_no: int) -> list[dict]:
        if not self.pages or page_no > len(self.pages):
            return []
        return self.pages[page_no - 1].chars

    def close(self) -> None:
        if self.pages:
            try:
                self._pdf.close()
            except Exception:  # noqa: BLE001
                pass


def enrich_size(lines: list[dict], chars: list[dict], scale: float) -> None:
    """把像素行盒映射到 PDF 点，并用落在行内的字符估计字号。"""
    for line in lines:
        line["top"] = round(line["px_top"] / scale, 2)
        line["bottom"] = round(line["px_bottom"] / scale, 2)
        line["left"] = round(line["px_left"] / scale, 2)
        line["right"] = round(line["px_right"] / scale, 2)
        line["height_pt"] = round(line["px_height"] / scale, 2)

        sizes = [c["size"] for c in chars
                 if line["top"] - 2 <= c["top"] and c["bottom"] <= line["bottom"] + 4]
        if sizes:
            sizes.sort()
            line["size_pt"] = round(sizes[len(sizes) // 2], 2)
            line["size_source"] = "pdf-text-layer"
        else:
            # 无文字层（或该行没有文字层字符，如插图里的孤立字母）：按行高估。
            # 中文字面高约行高的 0.82。
            line["size_pt"] = round(line["height_pt"] * 0.82, 2)
            line["size_source"] = "estimated"


def clamp_sizes(lines: list[dict]) -> None:
    """把字号钳到本页正文尺度的合理范围。

    没有这一层，插图（立体图、坐标图）里的孤立字母会被当成"一行"，行高按图形高度算，
    于是渲染出巨大的 `D₁`、`F₁`、`E`、`F` 压在页面上（实测第 4、5 页）。字号是渲染属性，
    钳制不会改动文字，只把离谱的视觉尺寸拉回正文量级。
    """
    sizes = sorted(l["size_pt"] for l in lines if l.get("size_pt"))
    if not sizes:
        return
    median = sizes[len(sizes) // 2]
    lo, hi = max(5.0, median * 0.55), median * 1.60
    for line in lines:
        size = line.get("size_pt")
        if size and (size < lo or size > hi):
            line["size_pt"] = round(min(max(size, lo), hi), 2)
            line["size_clamped"] = True


# ---------------------------------------------------------------- 逐行 OCR

def crop_line(pil, line: dict) -> bytes:
    box = (
        max(0, line["px_left"] - X_PAD_PX),
        max(0, line["px_top"] - Y_PAD_PX),
        min(pil.width, line["px_right"] + 1 + X_PAD_PX),
        min(pil.height, line["px_bottom"] + 1 + Y_PAD_PX),
    )
    buf = io.BytesIO()
    pil.crop(box).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def ocr_line(host: str, model: str, png: bytes, prompt: str,
             timeout: int, max_tokens: int) -> dict:
    return ocr_png(host, model, png, prompt, timeout, max_tokens)


# ---------------------------------------------------------------- 主流程

def parse_pages(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(1, total + 1))
    import re
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", spec)
    if not m:
        die(f"--pages 格式不对：{spec!r}", 3)
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else start
    if start < 1 or end > total or start > end:
        die(f"--pages {spec} 超出范围（共 {total} 页）", 3)
    return list(range(start, end + 1))


def build(args) -> dict:
    pdfium = import_pdfium()
    doc = pdfium.PdfDocument(args.pdf)
    total = len(doc)
    pages = parse_pages(args.pages, total)

    scale = args.dpi / 72.0
    check_health(args.host)
    font_probe = None if args.no_pdf_font_metrics else PdfFontProbe(args.pdf)

    log(f"{os.path.basename(args.pdf)}：{total} 页，处理 {pages[0]}..{pages[-1]}，{args.dpi} dpi")

    out_pages: list[dict] = []
    warnings: list[str] = []
    t_start = time.time()

    # pypdfium2 是原生库，多线程渲染会 access violation → 渲染串行，OCR 并发
    render_lock = threading.Lock()

    for pno in pages:
        t_page = time.time()
        with render_lock:
            page_obj = doc[pno - 1]
            pil = page_obj.render(scale=scale).to_pil().convert("RGB")
            gray = np.asarray(pil.convert("L"))
            pw, ph = page_obj.get_size()

        raw_lines, typical_h_px = detect_lines(gray)

        # 异常高的内容块：按 PDF 文字层字符数拆成「正文行组」与「插图区」。
        # 没有文字层时 has_pdf_text 返回 -1，block 一律判正文（与旧版一致）。
        if args.no_figures:
            extra_lines, figure_regions = [], []
        else:
            chars = font_probe.char_boxes(pno) if font_probe is not None else []
            mask = text_coverage_mask(chars, gray.shape, scale)
            extra_lines, figure_regions = split_and_classify_regions(
                gray, typical_h_px, mask)
            # 表格：PaddleOCR-VL 会把表格压成逐格换行文本、结构不可恢复，
            # 所以按"长直线游程"识别表格区域，把整块当图裁出来（内容与结构都保真）。
            table_regions = detect_table_grids(gray, typical_h_px)
            for tr in table_regions:
                figure_regions.append(tr)
            if table_regions:
                log(f"第 {pno} 页识别到 {len(table_regions)} 个表格候选区"
                    + f"（{', '.join(f'{t['rows']}x{t['cols']}格' for t in table_regions)}）")

        noise = [l for l in raw_lines if _is_noise(l)]
        lines = [l for l in raw_lines if not _is_noise(l)] + extra_lines
        before = len(lines)
        lines = dedupe_lines(lines)
        if before != len(lines):
            log(f"第 {pno} 页合并 {before - len(lines)} 条重复行（行投影与高块重切同时命中）")
        used = "raster-projection"
        sizes_hint = font_probe.char_boxes(pno) if font_probe is not None else None

        # 兜底：整页只有一条超长行时不硬拆，直接整页 OCR
        if args.single_line_fallback and len(lines) <= 1:
            lines = [{"px_top": 0, "px_bottom": gray.shape[0] - 1,
                      "px_left": 0, "px_right": gray.shape[1] - 1,
                      "px_height": gray.shape[0], "ink": int((gray < INK_THRESHOLD).sum())}]
            used = "whole-page-fallback"
            warnings.append(f"第 {pno} 页行检测只得到 1 行，退回整页 OCR")

        task_png = [crop_line(pil, l) for l in lines]

        results: list[dict | None] = [None] * len(lines)

        def work(idx: int) -> None:
            t0 = time.time()
            texts: list[str] = []
            finishes: list[str] = []
            for _ in range(max(1, args.vote)):
                try:
                    resp = ocr_line(args.host, args.model, task_png[idx], args.prompt,
                                    args.timeout, args.max_tokens)
                    texts.append(resp["text"].strip())
                    finishes.append(resp["finish_reason"] or "")
                except Exception as exc:  # noqa: BLE001
                    finishes.append(f"error: {exc}")
            if texts:
                text, votes = majority_vote(texts)
                finish = finishes[0] if votes > 1 else (
                    finishes[0] if len(texts) == 1 else "vote:" + str(votes))
            else:
                text, finish = "", finishes[0] if finishes else "error"
            results[idx] = {"text": text, "finish_reason": finish,
                            "votes": len(texts),
                            "disagreement": len(set(texts)) > 1,
                            "elapsed_s": round(time.time() - t0, 2)}

        workers = min(max(1, len(lines)), args.workers)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(work, range(len(lines))))

        # 先把 OCR 结果挂到行上，再算坐标/字号（enrich_size 负责填 top/bottom/left/right）
        for line, res in zip(lines, results):
            line["_ocr"] = res or {"text": "", "finish_reason": "error", "elapsed_s": 0}
        if sizes_hint is not None:
            enrich_size(lines, sizes_hint, scale)
        else:
            for line in lines:
                line["top"] = round(line["px_top"] / scale, 2)
                line["bottom"] = round(line["px_bottom"] / scale, 2)
                line["left"] = round(line["px_left"] / scale, 2)
                line["right"] = round(line["px_right"] / scale, 2)
                line["height_pt"] = round(line["px_height"] / scale, 2)
                line["size_pt"] = round(line["height_pt"] * 0.82, 2)
                line["size_source"] = "estimated"
        clamp_sizes(lines)

        page_lines = []
        dropped = 0
        raw_texts = [line["_ocr"]["text"] for line in lines]
        for line in lines:
            res = line.pop("_ocr")
            # 表格单独成结构（不能让它被压成一行文本）
            tables, cleaned = extract_tables(clean_ocr_text(res["text"]), res["text"])
            if not cleaned and not tables:
                continue
            if not cleaned and tables:
                cleaned = "（表格）"
            # 图形区幻觉（超长数字串、孤立字母标签、片段重复）不当作正文行
            if cleaned and _is_hallucination(cleaned):
                dropped += 1
                continue
            page_lines.append({
                "px_top": line["px_top"], "px_bottom": line["px_bottom"],
                "px_left": line["px_left"], "px_right": line["px_right"],
                "top": line["top"], "bottom": line["bottom"],
                "left": line["left"], "right": line["right"],
                "height_pt": line["height_pt"],
                "size_pt": line.get("size_pt"),
                "size_source": line.get("size_source"),
                "size_clamped": bool(line.get("size_clamped")),
                "typical_h_pt": round(line.get("typical_h", line["px_height"]) / scale, 2),
                "multiline": bool(line.get("typical_h")
                                  and line["px_height"] > line["typical_h"] * 1.25),
                "text": cleaned,
                "tables": tables,
                "finish_reason": res["finish_reason"],
                "votes": res.get("votes"),
                "disagreement": bool(res.get("disagreement")),
                "elapsed_s": res["elapsed_s"],
            })
        if dropped:
            log(f"第 {pno} 页丢弃 {dropped} 条图形区幻觉行（超长数字串/孤立字母标签）")

        # 插图：像素检测出的图形区 → 裁片 → 原位贴回前端
        figures = collect_figures(pil, figure_regions, pw, ph,
                                  keep_images=not args.no_images) if figure_regions else []
        if figures:
            page_lines, in_fig = drop_lines_inside_figures(page_lines, figures)
            if in_fig:
                log(f"第 {pno} 页丢弃 {in_fig} 条落在插图内的正文行"
                    f"（裁片已含图上标注，再叠一份就是重复）")
            # 裁片不能压住正文：把伸进题干的边界收回来
            clipped = clip_figures_to_avoid_text(pil, figures, page_lines, pw, ph)
            if clipped:
                log(f"第 {pno} 页收缩 {clipped} 张裁片边界（避免压住题干）")
            log(f"第 {pno} 页还原插图 {len(figures)} 张"
                f"（{', '.join(f'{f['w_pt']:.0f}x{f['h_pt']:.0f}pt' for f in figures)}）")

        # 行序 = 页面自上而下。检测是按行墨迹投影出的行程顺序，理论上已是自上而下，
        # 但并叠下划线、过滤空行之后仍可能错序（渲染时表现为第 4 题跑到第 2 题上面）。
        # 显式排序，版面顺序不能依赖上游的偶然性。
        page_lines.sort(key=lambda l: (l["top"], l["left"]))

        empty = sum(1 for l in raw_lines if _is_noise(l))
        bad = [l for l in page_lines if str(l["finish_reason"]).startswith("error")]
        if bad:
            warnings.append(f"第 {pno} 页有 {len(bad)} 行 OCR 失败/为空")
        if empty:
            log(f"第 {pno} 页合并了 {empty} 条碎行（下划线/噪点）")

        entry = {
            "page": pno,
            "width": round(pw, 2),
            "height": round(ph, 2),
            "render_px": [pil.width, pil.height],
            "dpi": args.dpi,
            "lines_detected": len(lines),
            "line_source": used,
            "lines": page_lines,
            "figures": figures,
            "markdown": "\n".join(l["text"] for l in page_lines),
            "elapsed_s": round(time.time() - t_page, 2),
        }
        if not args.no_images:
            buf = io.BytesIO()
            pil.save(buf, "JPEG", quality=72, optimize=True)
            entry["image_jpeg_base64"] = base64.b64encode(buf.getvalue()).decode("ascii")
        out_pages.append(entry)
        log(f"第 {pno} 页完成：{len(page_lines)} 行"
            f"{f'、{len(figures)} 图' if figures else ''}，{entry['elapsed_s']}s")

    if font_probe is not None:
        font_probe.close()

    all_lines = [l for p in out_pages for l in p["lines"]]
    all_figures = [f for p in out_pages for f in p.get("figures", [])]
    with_math = sum(1 for l in all_lines
                    if "$" in l["text"] or "\\(" in l["text"] or "\\[" in l["text"])
    payload = {
        "document": {
            "title": os.path.splitext(os.path.basename(args.pdf))[0],
            "source_pdf": os.path.abspath(args.pdf),
            "pages_total": total,
            "pages_processed": len(out_pages),
            "engine": f"llama.cpp/{args.model}",
            "mode": "layout",
            "dpi": args.dpi,
            "page_size_pt": [out_pages[0]["width"], out_pages[0]["height"]] if out_pages else None,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "elapsed_s": round(time.time() - t_start, 2),
            "warnings": warnings,
        },
        "stats": {
            "pages": len(out_pages),
            "lines": len(all_lines),
            "lines_with_math": with_math,
            "figures": len(all_figures),
            "vote": args.vote,
            "disagreed_lines": sum(1 for l in all_lines if l.get("disagreement")),
            "size_from_text_layer": sum(1 for l in all_lines
                                        if l.get("size_source") == "pdf-text-layer"),
        },
        "pages": out_pages,
    }
    return payload


LAYOUT_VIEWER = "viewer_layout.html"     # 生成的阅读器文件名（各引擎加后缀，见 paths）


def _delims_for(engine: str) -> list[dict]:
    """按引擎选公式定界符。

    OvisOCR2 输出 `$...$` / `$$...$$`；PaddleOCR-VL 输出 `\\(...\\)` / `\\[...\\]`。
    实测把 Paddle 的产物拿去用只认 `$` 的阅读器打开，"含公式"计数是 **0**、整页都是源码。
    """
    if "paddle" in (engine or "").lower():
        return [
            {"left": "\\[", "right": "\\]", "display": True},
            {"left": "\\(", "right": "\\)", "display": False},
        ]
    return [
        {"left": "$$", "right": "$$", "display": True},
        {"left": "$", "right": "$", "display": False},
        {"left": "\\[", "right": "\\]", "display": True},
        {"left": "\\(", "right": "\\)", "display": False},
    ]


def materialize_viewer(target_dir: str, payload: dict, data_name: str,
                       base_name: str = "viewer_layout") -> tuple[str, str]:
    """渲染出**单文件**阅读器：{base_name}.html（数据内嵌）＋ 薄版（外链 JSON）。

    内嵌版用 file:// 双击就能看，不依赖本地服务；薄版的 JSON 可以被其它程序直接消费。
    数据放在主脚本之前的 <script> 里，保证主脚本执行时读得到。

    JSON 里的 `<` 统一转义成 \\u003c：既避免 `</script>` 提前截断文档，
    又不改变解析后的值（`\\u003c` 在 JSON 字符串里等价于 `<`）。
    """
    import shutil

    src_dir = paths.VIEWER_DIR
    template = open(os.path.join(src_dir, "viewer_template.html"), encoding="utf-8").read()

    doc = payload.get("document", {}) or {}
    title = doc.get("title", "OCR 结果")
    engine = doc.get("engine", "?")
    delims_js = json.dumps(_delims_for(engine), ensure_ascii=False)

    data_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    data_json = data_json.replace("<", "\\u003c").replace("\u2028", "\\u2028") \
                         .replace("\u2029", "\\u2029")
    data_script = (
        "<script>/* OCR 版式数据（单文件内嵌） */\n"
        f"window.__OCR_LAYOUT__ = {data_json};</script>"
    )

    def render(data: str, json_url: str) -> str:
        return (template
                .replace("__TITLE__", title.replace("<", "&lt;"))
                .replace("__ENGINE__", html_lib.escape(engine))
                .replace("__DELIMS__", delims_js)
                .replace("__JSON_URL__", json_url)
                .replace("__DATA_SCRIPT__", data))

    os.makedirs(target_dir, exist_ok=True)
    full_path = os.path.join(target_dir, f"{base_name}.html")
    with open(full_path, "w", encoding="utf-8") as fh:
        fh.write(render(data_script, f"./{data_name}"))

    # 薄版：数据外链，便于自检/被别的程序复用
    slim_path = os.path.join(target_dir, f"{base_name}_slim.html")
    with open(slim_path, "w", encoding="utf-8") as fh:
        fh.write(render("", f"./{data_name}"))

    vendor_src = paths.VENDOR_DIR
    if os.path.isdir(vendor_src):
        shutil.copytree(vendor_src, os.path.join(target_dir, "vendor"),
                        dirs_exist_ok=True)
        shutil.copy2(os.path.join(vendor_src, "katex", "katex.min.js"),
                     os.path.join(target_dir, "katex.min.js"))
        shutil.copy2(os.path.join(vendor_src, "katex", "katex.min.css"),
                     os.path.join(target_dir, "katex.min.css"))
    return full_path, slim_path


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="逐行 OCR → 带版式坐标的 JSON → 原格式网页")
    ap.add_argument("--pdf")
    ap.add_argument("--out")
    ap.add_argument("--pages")
    ap.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--prompt", default=None,
                    help="默认按模型自动选：paddleocr-vl → 'OCR:'，ovisocr2 → 整页解析指令")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--workers", type=int, default=4, help="并发 OCR 行数（引擎 n_slots=4）")
    ap.add_argument("--vote", type=int, default=1,
                    help="每行识别几次并投票（默认 1；3 可压掉引擎的随机幻觉，代价是 3 倍耗时）")
    ap.add_argument("--no-images", action="store_true", help="不写入页图与插图（JSON 更小）")
    ap.add_argument("--no-figures", action="store_true", help="不还原插图裁片")
    ap.add_argument("--no-pdf-font-metrics", action="store_true",
                    help="不用 pdfplumber 读字号（纯扫描件更省事）")
    ap.add_argument("--single-line-fallback", action="store_true", default=True)
    ap.add_argument("--no-single-line-fallback", dest="single_line_fallback",
                    action="store_false")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--serve-only", action="store_true")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    if not args.prompt:
        args.prompt = default_prompt_for(args.model)

    if args.serve_only:
        out = os.path.abspath(args.out or "")
        if not out or not os.path.isfile(out):
            die("--serve-only 需要 --out 指向已存在的 JSON", 3)
        out_dir = os.path.dirname(out)
        with open(out, encoding="utf-8") as fh:
            payload = json.load(fh)
        engine = ((payload.get("document") or {}).get("engine") or "")
        base = paths.viewer_base_for(engine)
        html_path = os.path.join(out_dir, f"{base}.html")
        if not os.path.isfile(html_path):
            html_path, _ = materialize_viewer(out_dir, payload, os.path.basename(out),
                                              base_name=base)
        log(f"阅读器 {html_path}")
        serve(out, html_path, args.port)
        return 0

    if not args.pdf:
        die("缺少 --pdf", 3)
    if not os.path.isfile(args.pdf):
        die(f"找不到 PDF：{args.pdf}", 3)

    payload = build(args)

    out_path = args.out or paths.layout_json_for(args.model)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    log(f"写出 {out_path}（{os.path.getsize(out_path)/1e6:.2f} MB，"
        f"{payload['stats']['pages']} 页，{payload['stats']['lines']} 行，"
        f"含公式 {payload['stats']['lines_with_math']} 行）")

    # 每个引擎的产物单独命名，否则第二次运行会覆盖第一份阅读器
    # （实测：跑完 OvisOCR2 再跑 PaddleOCR-VL，viewer_layout.html 就被换成了 Paddle 的，
    #  于是"两份 JSON 只有一份能看"）。
    default_name = paths.viewer_base_for(args.model)
    full, slim = materialize_viewer(os.path.dirname(os.path.abspath(out_path)),
                                    payload, os.path.basename(out_path),
                                    base_name=default_name)
    log(f"单文件阅读器 {full}（{os.path.getsize(full)/1e6:.2f} MB，"
        f"数据内嵌，file:// 可直接打开）")
    log(f"薄版阅读器 {slim}（外链 {os.path.basename(out_path)}，需本地服务）")
    if args.serve:
        serve(os.path.abspath(out_path), slim, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
