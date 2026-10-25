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
import difflib
import html as html_lib
import io
import json
import os
import re
import statistics
import sys
import threading
import time
import unicodedata

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402
from layout_regions import build_regions, region_stats  # noqa: E402
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
        # pdfplumber 可能把一个字形报成多字符（连字）；蒙版只取首个码点。
        cp = ord(ch[0]) if ch else 0
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
        # 先按“第一条压到的正文行”直接收底边。中文公式行常比图框低 1~3pt，
        # 逐边贪心会在大片选项文字上反复试探后放弃（实测 2024 全国理 Q7 的
        # 四个图象选项）；直接把底边提到该行上方，既不切图主体，也不压字。
        capped_bottom = box[3]
        for l in lines:
            if (l["top"] >= box[1] + min_h and l["top"] < capped_bottom
                    and min(l["right"], box[2]) - max(l["left"], box[0]) > 0):
                capped_bottom = min(capped_bottom, l["top"] - 0.5)
        if capped_bottom < box[3] and capped_bottom - box[1] >= min_h:
            box[3] = capped_bottom
        if overlap(box) <= 0 and box != list(fig["pt"]):
            new_px = [max(0, int(round(box[0] * kx))), max(0, int(round(box[1] * ky))),
                      int(round(box[2] * kx)), int(round(box[3] * ky))]
            crop = _crop_figure_px(pil, new_px, page_w, page_h)
            if crop:
                fig.update(crop)
                fig["clipped_to_avoid_text"] = True
                changed += 1
            continue
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
            # 小面积“图”其实是公式分数线/根号等矢量碎片，又和正文纠缠：
            # 直接丢弃，别让它盖住题干（实测 2023 新高考 2 Q7 的 40x52pt 碎片）。
            if (fig["pt"][2] - fig["pt"][0]) * (fig["pt"][3] - fig["pt"][1]) < 8000:
                fig["drop"] = True
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
        covered = 0.0
        for fig in figures:
            fx0, fy0, fx1, fy1 = fig["pt"]
            ix = min(line["right"], fx1) - max(line["left"], fx0)
            iy = min(line["bottom"], fy1) - max(line["top"], fy0)
            if ix > 0 and iy > 0:
                covered += ix * iy
        # 同一行可以横跨两张并排插图（实测统计图下轴标签 `0.002 -- 指标`
        # 同时压住两张图）。逐图都不到 50%，但并集达到 76%，仍是图内文字。
        #
        # 但不能把题干一起丢掉：P1 扩框后，长题干可能整行落在标签扩框里。
        # 图内重复标注都是短行，且不会以 `7.` / `12、` 开头；长行/题号行必须保留。
        text = line.get("text", "").strip()
        question_like = bool(re.match(r"^\d{1,3}\s*[.、．)）]", text))
        # 公式/选项行也可能紧贴参考图（如“已知图象，则 f(x)=…”的四个选项），
        # 不能因为落在扩框里就当图内标注丢掉；真正的图内标注没有等号/选项标签。
        formula_or_option = ("=" in text or "\\frac" in text
                             or bool(re.search(r"\([A-D]\)", text)))
        if (covered / area >= 0.5 and len(text) <= 24
                and not question_like and not formula_or_option):
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
    # ⓪ Markdown/管道表格：PaddleOCR-VL 对框线表常吐 `a | b\n---|---\n1 | 2`。
    #    这是已带结构的信息，优先按 `|` 拆，不要把整行当成一段正文。
    pipe_rows = [ln.strip() for ln in body.split("\n") if "|" in ln]
    if len(pipe_rows) >= 2:
        grid: list[list[str]] = []
        for ln in pipe_rows:
            if re.fullmatch(r"\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*", ln):
                continue
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) >= 2:
                grid.append(cells)
        if len(grid) >= 2 and _table_shape_ok(grid):
            return {"headers": grid[0], "rows": grid[1:],
                    "source": "pipe-table"}
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
    #    2026-10 新增 10 页卷语料后又踩到两次：大题标题「二、填空题共5小题，每小题5分，
    #    共25分。」和命题说明「【命题说明】本题改编自2006年北京卷第20题」都被判成表，
    #    整行文字被吞成「（表格）」，标题直接丢失。再加两道闸：带中文句读的行不是表行
    #    （真表是"标签 数字"密集排列，不写句子）；成对内容必须覆盖整行大半（散文里的
    #    标签只是零星出现，覆盖率低）。
    pairs = re.findall(r"([\u4e00-\u9fff]{2,4}|[A-Za-z]学校)(\d{1,3})", body)
    labels = re.findall(r"[\u4e00-\u9fff]{2,4}", body)
    covered = sum(len(label) + len(number) for label, number in pairs)
    has_prose_punct = bool(re.search(r"[，。、；：！？]", body))
    if not has_prose_punct and len(pairs) >= 2 and len(labels) >= 3 \
            and covered >= 0.6 * len(body):
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


def ensure_python_version(min_version: tuple[int, int] = (3, 9)) -> None:
    """把"解释器太老"变成一条能读懂的提示。

    历史上这个文件用过 PEP 701 的嵌套同引号 f-string（3.12+ 才支持），在 3.11 下直接
    `SyntaxError`，报错指向一行看不懂的 f-string，排查成本很高。现在语法已经降到 3.9+，
    这层守卫只是防止以后又不小心引入高版本语法。
    """
    if sys.version_info < min_version:
        die(f"需要 Python {min_version[0]}.{min_version[1]}+，"
            f"当前是 {sys.version.split()[0]}（{sys.executable}）", 2)


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


def _gutters_from_chars(chars: list[dict], scale: float,
                        gray_shape: tuple[int, int],
                        ink: np.ndarray) -> list[int]:
    """用 PDF 文字层的字符盒直接找栏间中缝。

    只保留**图像上真的有墨**的字符：这份卷子的文字层里还混着不可见的离群
    字符（x 到页面最右但图像上空白），它们会把版心撑大，必须先过滤。
    """
    h, w = gray_shape
    y0, y1 = int(h * 0.03), int(h * 0.86)
    # 标点会被排到栏缝里（“A、B”的顿号实测伸进中缝 7 pt）。它只有一两个
    # 像素宽，却足以让整条缝看起来“有字”。找栏缝时先忽略标点；正文渲染
    # 仍保留它们。
    punctuation = set("、，。；：！？,.;:!?·…—")
    boxes: list[tuple[int, int]] = []
    for c in chars:
        if (c.get("text") or "").strip() in punctuation:
            continue
        x0, x1 = int(c["x0"] * scale), int(c["x1"] * scale) + 1
        ty0, ty1 = int(c["top"] * scale), int(c["bottom"] * scale) + 1
        if x1 <= x0 or ty1 <= y0 or ty0 >= y1:
            continue
        x0, x1 = max(0, x0), min(w, x1)
        ty0, ty1 = max(0, ty0), min(h, ty1)
        if x1 <= x0 or ty1 <= ty0:
            continue
        if ink[ty0:ty1, x0:x1].sum() == 0:
            continue
        boxes.append((x0, x1))
    if len(boxes) < 20:
        return []
    content_left = min(b[0] for b in boxes)
    content_right = max(b[1] for b in boxes)
    if content_right - content_left < w * 0.20:
        return []
    covered = np.zeros(w, dtype=bool)
    for x0, x1 in boxes:
        covered[x0:x1] = True
    quiet = ~covered

    runs: list[tuple[int, int]] = []
    start = None
    for i, has in enumerate(quiet):
        if has and start is None:
            start = i
        elif not has and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, w - 1))

    # 字符盒的左右留白不计入墨迹，三栏卷中间那道缝实测只有 4–5 pt；
    # 用 12 px 会漏掉它，导致中栏与右栏被当成一栏合并。整页高度都没有字符
    # 的窄缝仍然是结构缝，8 px（约 3.8 pt）足够排除字间空格。
    min_width = max(8, int(w * 0.0025))
    candidates: list[tuple[int, int, int, int]] = []
    for left, right in runs:
        width = right - left + 1
        if width < min_width:
            continue
        if left <= content_left + min_width or right >= content_right - min_width:
            continue
        left_chars = sum(1 for x0, x1 in boxes if x1 <= left)
        right_chars = sum(1 for x0, x1 in boxes if x0 >= right)
        if left_chars >= 5 and right_chars >= 5:
            candidates.append((left, right, min(left_chars, right_chars), width))
    # 同一结构缝在文字稀疏的一侧会碎成多条窄缝（栏间 785pt 后 20pt 又有
    # 一条）。按 80pt 聚类，取**最宽**的那条：真实栏缝是整列空白，宽度
    # 通常 30–80pt；栏内短行形成的空隙只有 8–15pt。只看两侧字数会在
    # “左栏很短、右栏很长”时把栏内空隙排到真缝前面（2023 北京第 2 页）。
    merge_px = max(40, int(80 * scale))
    clusters: list[list[tuple[int, int, int, int]]] = []
    for cand in candidates:
        if clusters and cand[0] - clusters[-1][-1][1] <= merge_px:
            clusters[-1].append(cand)
        else:
            clusters.append([cand])
    best = [max(cluster, key=lambda item: (item[3], item[2]))
            for cluster in clusters]
    threshold = max(8, int(len(boxes) * 0.01))
    best = [cand for cand in best if cand[2] >= threshold]
    # A3 数学卷实测最多 3 栏；留 2 条缝。页边距/稀疏区的假缝文字量远低于
    # 真缝，按 min(左右字数) 取前两条，而不是把检测到的每条缝都切一刀。
    best.sort(key=lambda item: (item[3], item[2]), reverse=True)
    gutters = [((left + right) // 2) for left, right, _score, _width in best[:2]]
    return sorted(gutters)


def detect_column_gutters(gray: np.ndarray,
                          lines: list[dict] | None = None,
                          chars: list[dict] | None = None,
                          scale: float = 150 / 72) -> list[int]:
    """检测横向试卷的栏间中缝，返回中缝中心 px 列表；单栏返回空列表。

    不假设栏数，也不假设中缝在页面正中：实测 A3 卷有 2 栏，也有 3 栏，
    且版心可能明显偏左。做法是找「整列几乎没有墨」的竖带，并只保留
    **版心内部、两侧都有大量文字** 的竖带：右页边距处也有空白竖带，
    但它右侧没有文字，会被 content_left/content_right 排除。
    """
    h, w = gray.shape[:2]
    if w < h * 1.18:
        return []
    if chars:
        ink_for_chars = gray < INK_THRESHOLD
        return _gutters_from_chars(chars, scale, gray.shape[:2], ink_for_chars)
    ink = gray < INK_THRESHOLD
    y0, y1 = int(h * 0.03), int(h * 0.86)
    body = ink[y0:y1]
    if body.size == 0:
        return []
    body_h = y1 - y0
    col_ink = body.sum(axis=0)
    if lines:
        # 用行盒的分位数定版心：页眉/页码是少量通栏离群行，不能把版心
        # 撑到页面最右，否则真正的右页边距会被误当成栏间中缝。
        lefts = sorted(int(l["px_left"]) for l in lines)
        rights = sorted(int(l["px_right"]) for l in lines)
        n = len(lines)
        content_left = lefts[int(0.10 * (n - 1))]
        content_right = rights[int(0.90 * (n - 1))]
    else:
        # 用「多行都有墨」的列定版心，过滤扫描边缘的孤立竖线
        active = np.where(col_ink > max(5, int(body_h * 0.01)))[0]
        if len(active) < 10:
            return []
        content_left, content_right = int(active[0]), int(active[-1])
    if content_right - content_left < w * 0.20:
        return []

    quiet = col_ink <= max(2, int(body_h * 0.002))
    runs: list[tuple[int, int]] = []
    start = None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(quiet) - 1))

    min_width = max(10, int(w * 0.004))
    gutters: list[int] = []
    for left, right in runs:
        width = right - left + 1
        if width < min_width:
            continue
        # 只认版心内部的中缝；贴版心左右边缘的空白带是页边距
        if left <= content_left + min_width or right >= content_right - min_width:
            continue
        lstrip = body[:, max(0, left - 60):left]
        rstrip = body[:, right + 1:min(w, right + 61)]
        if lstrip.size == 0 or rstrip.size == 0:
            continue
        left_cov = float((lstrip.sum(axis=1) > 0).mean())
        right_cov = float((rstrip.sum(axis=1) > 0).mean())
        # 中缝两侧都必须有足够多的行有墨，排除公式内部偶然的窄空隙
        if left_cov >= 0.04 and right_cov >= 0.04:
            gutters.append((left + right) // 2)
    return sorted(gutters)


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

NATIVE_FIGURE_DPI = 300
NATIVE_FIGURE_GAP_PT = 14.0
NATIVE_FIGURE_MIN_PT = 40.0
NATIVE_FIGURE_MIN_AREA_PT = 1600.0
NATIVE_FIGURE_LABEL_PAD_PT = 12.0


def _cluster_rects(rects: list[tuple[float, float, float, float]],
                   gap: float) -> list[list[float]]:
    """把互相靠近的矢量对象并成簇，返回 [x0, top, x1, bottom]。

    坐标轴、曲线、刻度线常常各自是一个独立 path，必须按邻近关系合并，否则每根线都是
    一个"太窄"的框。零宽/零高直线（坐标轴、表格线）先补 0.5pt，避免在过滤和相交测试里
    被当成空对象丢掉。
    """
    boxes: list[list[float]] = []
    for x0, top, x1, bottom in rects:
        x0, x1 = sorted((float(x0), float(x1)))
        top, bottom = sorted((float(top), float(bottom)))
        if x1 - x0 < 0.1:
            x0 -= 0.5
            x1 += 0.5
        if bottom - top < 0.1:
            top -= 0.5
            bottom += 0.5
        boxes.append([x0, top, x1, bottom])
    if not boxes:
        return []

    parent = list(range(len(boxes)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(len(boxes)):
        for j in range(i):
            a, b = boxes[i], boxes[j]
            if not (a[2] + gap < b[0] or b[2] + gap < a[0]
                    or a[3] + gap < b[1] or b[3] + gap < a[1]):
                union(i, j)

    groups: dict[int, list[list[float]]] = {}
    for i, box in enumerate(boxes):
        groups.setdefault(find(i), []).append(box)
    clustered: list[list[float]] = []
    for group in groups.values():
        clustered.append([min(b[0] for b in group), min(b[1] for b in group),
                          max(b[2] for b in group), max(b[3] for b in group)])
    return clustered


def _merge_native_boxes(boxes: list[dict]) -> list[dict]:
    """合并原生图片与矢量簇里互相重叠的框，保留来源信息。"""
    merged: list[dict] = []
    for box in boxes:
        x0, top, x1, bottom = box["pt"]
        target = None
        for old in merged:
            ox0, otop, ox1, obottom = old["pt"]
            ix = min(x1, ox1) - max(x0, ox0)
            iy = min(bottom, obottom) - max(top, otop)
            if ix <= 0 or iy <= 0:
                continue
            inter = ix * iy
            small = max(1e-6, min((x1 - x0) * (bottom - top),
                                  (ox1 - ox0) * (obottom - otop)))
            if inter / small >= 0.5:
                target = old
                break
        if target is None:
            merged.append({"pt": list(box["pt"]), "sources": [box["source"]]})
            continue
        tx0, ttop, tx1, tbottom = target["pt"]
        target["pt"] = [min(tx0, x0), min(ttop, top),
                        max(tx1, x1), max(tbottom, bottom)]
        if box["source"] not in target["sources"]:
            target["sources"].append(box["source"])
    return merged


def _is_page_background(box: tuple[float, float, float, float],
                        page_w: float, page_h: float) -> bool:
    """整页扫描图/页边框不是插图，不能当图区涂掉。"""
    x0, y0, x1, y1 = box
    return ((x1 - x0) >= page_w * 0.85 and (y1 - y0) >= page_h * 0.85)


def _is_page_furniture(box: list[float] | tuple[float, float, float, float],
                       page_w: float, page_h: float, max_pt: float = 90.0,
                       band: float = 0.10) -> bool:
    """页眉/页脚的小图块是页面装饰，不是插图。

    实测 2024 北京西城二模：每页右上角一个 54×54pt 的二维码图片被当成插图，
    既进 figures 又被判“未归属题目”（9 条 figure_unassigned warning），把真正
    该看的“少图”信号淹掉了。这类装饰（二维码、校徽、防伪印章、水印条）只满足
    “小 + 贴页眉/页脚”，与正文插图在几何上不重合，所以按几何判据过滤，不做
    内容识别。被滤掉的框仍参与行投影掩膜，避免它变成“未覆盖墨迹”。
    """
    try:
        x0, y0, x1, y1 = [float(v) for v in box]
    except (TypeError, ValueError):
        return False
    if (x1 - x0) > max_pt or (y1 - y0) > max_pt:
        return False
    return y1 <= page_h * band or y0 >= page_h * (1.0 - band)


def _expand_box_with_labels(box: list[float], chars: list[dict],
                            pad: float = 10.0,
                            far_pad: float = 26.0) -> tuple[list[float], int]:
    """把图簇 bbox 扩展到紧邻的刻度/图例/轴标题字符。

    散点图的点、坐标轴和刻度文字是不同 PDF 对象；只按矢量路径聚类会把
    “0”“1”“x”“y”切在框外。普通字号只吸收 ``pad`` 内；轴标题/刻度字号
    更小（<=9.5pt），允许到 ``far_pad``——竖排的“正确率”常比图框高出一截。
    后续 ``clip_figures_to_avoid_text`` 仍会把真正压住正文的边界收回来。
    """
    x0, top, x1, bottom = box
    absorbed = 0
    # 只吸收“短标注”字符。整行公式/题干即使字号小、落在扩框范围内，也不能
    # 被算成图上标注，否则会把正文行一起吞进图区（实测 2024 全国理 Q8）。
    groups: dict[float, list[dict]] = {}
    for ch in chars:
        try:
            groups.setdefault(round(float(ch["top"]), 1), []).append(ch)
        except (KeyError, TypeError, ValueError):
            continue
    long_ids: set[int] = set()
    for group in groups.values():
        try:
            span = max(float(c["x1"]) for c in group) - min(float(c["x0"]) for c in group)
            joined = "".join(str(c.get("text") or "") for c in group).strip()
        except (KeyError, TypeError, ValueError):
            continue
        # 选项标签 (A)/(B)/(C)/(D) 是正文，不是图内标注。
        if re.fullmatch(r"(?:\([A-D]\)\s*)+", joined):
            long_ids.update(id(c) for c in group)
            continue
        if span > 70 or len(group) > 14:
            long_ids.update(id(c) for c in group)
    for ch in chars:
        if id(ch) in long_ids:
            continue
        if str(ch.get("text") or "").strip() in ("(A)", "(B)", "(C)", "(D)"):
            # 选项标签不是图内标注；把 (C)/(D) 吸进图框会让裁片压住下一题。
            continue
        try:
            cx = (float(ch["x0"]) + float(ch["x1"])) / 2
            cy = (float(ch["top"]) + float(ch["bottom"])) / 2
            size = float(ch.get("size") or 10.0)
        except (KeyError, TypeError, ValueError):
            continue
        limit = far_pad if size <= 9.5 else pad
        if not (x0 - limit <= cx <= x1 + limit
                and top - limit <= cy <= bottom + limit):
            continue
        nx0 = min(x0, float(ch["x0"]))
        ntop = min(top, float(ch["top"]))
        nx1 = max(x1, float(ch["x1"]))
        nbottom = max(bottom, float(ch["bottom"]))
        if (nx0, ntop, nx1, nbottom) != (x0, top, x1, bottom):
            absorbed += 1
        x0, top, x1, bottom = nx0, ntop, nx1, nbottom
    return [x0, top, x1, bottom], absorbed


def _native_figure_boxes(page, exclude_boxes: list[dict] | None = None) -> list[dict]:
    """从 pdfplumber 页面里找真正的图形资源：嵌入图片、矢量图簇。

    为什么先做这一步：像素掩膜只能靠"这一块文字很少"猜图形，遇到"图 + 文字"混排
    （本卷 Q11 的曲线图、Q17 的四棱锥）就完全失效——图被并进正文行，最终整块变成
    一个 141.6pt 高的行盒，渲染时文字被撑开、压到下一节标题。PDF 自己知道哪里是
    `image`、哪里是 `curve/line/rect`，直接用这些资源比猜可靠得多。

    表格由 ``exclude_boxes``（pdfplumber 已确认的表格 bbox）排除，不再用
    “长直线 + 字符多”猜表格——散点图的网格线/刻度也满足这个条件，会被整块丢掉。
    """
    width, height = page.width, page.height
    boxes: list[dict] = []
    exclude = [b["bbox_pt"] for b in (exclude_boxes or [])
               if b.get("bbox_pt") and len(b["bbox_pt"]) == 4]

    for image in page.images:
        x0, top = float(image["x0"]), float(image["top"])
        x1, bottom = float(image["x1"]), float(image["bottom"])
        w, h = x1 - x0, bottom - top
        if w < 24 or h < 24 or w * h < NATIVE_FIGURE_MIN_AREA_PT:
            continue
        if _is_page_background((x0, top, x1, bottom), width, height):
            # 整页扫描图只是页面的像素背景，不是插图；涂白它会把整页正文都抹掉。
            continue
        boxes.append({"pt": [x0, top, x1, bottom], "source": "pdf-image"})

    vector_rects = []
    for obj in list(page.curves) + list(page.lines) + list(page.rects):
        vector_rects.append((obj["x0"], obj["top"], obj["x1"], obj["bottom"]))

    chars = page.chars
    clusters = _cluster_rects(vector_rects, NATIVE_FIGURE_GAP_PT)
    for x0, top, x1, bottom in clusters:
        w, h = x1 - x0, bottom - top
        if w < NATIVE_FIGURE_MIN_PT or h < NATIVE_FIGURE_MIN_PT:
            continue
        if w * h < NATIVE_FIGURE_MIN_AREA_PT:
            continue
        if _is_page_background((x0, top, x1, bottom), width, height):
            continue
        if w > width * 0.92 and h > height * 0.60:
            continue
        # pdfplumber 已确认的表格不重复当图；散点图没有表格 bbox，不会被误删。
        if any(min(x1, bx1) - max(x0, bx0) > 0
               and min(bottom, by1) - max(top, by0) > 0
               and (min(x1, bx1) - max(x0, bx0))
               * (min(bottom, by1) - max(top, by0))
               >= 0.45 * max(1.0, (x1 - x0) * (bottom - top))
               for bx0, by0, bx1, by1 in exclude):
            continue
        expanded, absorbed = _expand_box_with_labels(
            [x0, top, x1, bottom], chars, NATIVE_FIGURE_LABEL_PAD_PT)
        boxes.append({
            "pt": [round(v, 2) for v in expanded],
            "source": "pdf-vector" + ("+labels" if absorbed else ""),
            "vector_count": len(clusters),
            "label_count": absorbed,
        })

    return _merge_native_boxes(boxes)


def _mask_figure_areas(gray, boxes: list[dict], scale: float,
                       margin_pt: float = 2.0):
    """把原生图形区涂白后再做行投影。

    这是修复"图把文字行撑高"的关键：图形墨迹不再参与行投影，Q11 那段文字会恢复成
    5 条独立行，Q17 也会恢复成 4 条；图形本身用原生资源单独裁回来。
    """
    if not boxes:
        return gray
    out = gray.copy()
    h, w = out.shape[:2]
    margin = max(1, int(round(margin_pt * scale)))
    for box in boxes:
        x0, top, x1, bottom = box["pt"]
        l = max(0, int(round(x0 * scale)) - margin)
        t = max(0, int(round(top * scale)) - margin)
        r = min(w, int(round(x1 * scale)) + margin)
        b = min(h, int(round(bottom * scale)) + margin)
        if r > l and b > t:
            out[t:b, l:r] = 255
    return out


def crop_native_figures(page_obj, boxes: list[dict], page_w: float, page_h: float,
                        base_dpi: int, keep_images: bool) -> list[dict]:
    """按原生 bbox 以 300 dpi 裁图，避免把 150 dpi 的整页图放大后糊掉。"""
    if not boxes:
        return []
    entries: list[dict] = []
    kb = base_dpi / 72.0
    for box in boxes:
        x0, top, x1, bottom = box["pt"]
        entries.append({
            "pt": [round(x0, 2), round(top, 2), round(x1, 2), round(bottom, 2)],
            "px": [max(0, int(round(x0 * kb))), max(0, int(round(top * kb))),
                   int(round(x1 * kb)), int(round(bottom * kb))],
            "w_pt": round(x1 - x0, 2),
            "h_pt": round(bottom - top, 2),
            "source": "+".join(box["sources"]),
        })
    if not keep_images:
        return entries

    try:
        scale = NATIVE_FIGURE_DPI / 72.0
        hi = page_obj.render(scale=scale).to_pil().convert("RGB")
        kx, ky = hi.width / page_w, hi.height / page_h
        for entry in entries:
            x0, top, x1, bottom = entry["pt"]
            px_box = [max(0, int(round(x0 * kx))), max(0, int(round(top * ky))),
                      min(hi.width, int(round(x1 * kx))),
                      min(hi.height, int(round(bottom * ky)))]
            if px_box[2] - px_box[0] < 24 or px_box[3] - px_box[1] < 24:
                continue
            buf = io.BytesIO()
            hi.crop(tuple(px_box)).save(buf, "PNG", optimize=True)
            entry["png_base64"] = base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        log(f"原生图形裁片失败，退回像素裁片（{exc}）")
        return []
    return [e for e in entries if "png_base64" in e]


def merge_native_figures(figures: list[dict], native: list[dict]) -> list[dict]:
    """原生图形优先；与像素检测重叠时只保留一张，避免页面上叠两张半截图。"""
    for nf in native:
        nx0, ntop, nx1, nbottom = nf["pt"]
        n_area = max(1e-6, (nx1 - nx0) * (nbottom - ntop))
        hit = None
        for i, fig in enumerate(figures):
            fx0, ftop, fx1, fbottom = fig["pt"]
            ix = min(nx1, fx1) - max(nx0, fx0)
            iy = min(nbottom, fbottom) - max(ntop, ftop)
            if ix > 0 and iy > 0 and ix * iy >= 800:
                hit = i
                break
        if hit is None:
            figures.append(nf)
            continue
        old = figures[hit]
        ox0, otop, ox1, obottom = old["pt"]
        o_area = max(1e-6, (ox1 - ox0) * (obottom - otop))
        if n_area >= o_area * 0.6:
            figures[hit] = nf
    for i, fig in enumerate(figures):
        fig["index"] = i
    return figures


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

    def figure_boxes(self, page_no: int,
                     exclude_boxes: list[dict] | None = None) -> list[dict]:
        """返回该页原生图形框（图片 / 矢量簇），没有 pdfplumber 时返回空。

        ``exclude_boxes`` 是已确认表格的 bbox，避免表格被同时当图和表。
        """
        if not self.pages or page_no > len(self.pages):
            return []
        try:
            return _native_figure_boxes(self.pages[page_no - 1],
                                        exclude_boxes=exclude_boxes)
        except Exception as exc:  # noqa: BLE001
            if not getattr(self, "_figure_warned", False):
                log(f"原生图形提取不可用，退回像素检测（{exc}）")
                self._figure_warned = True
            return []

    def table_boxes(self, page_no: int) -> list[dict]:
        """返回该页原生表格的几何与单元格框。

        只把单元格几乎都有文字的候选当表格：矢量图（几何图、坐标系）也会被
        pdfplumber 的框线算法切成 5x6 的“空表”，不能把图当表；表格是否真的
        有内容，交给后面的单元格 OCR 验证。
        """
        if not self.pages or page_no > len(self.pages):
            return []
        page = self.pages[page_no - 1]
        try:
            found = page.find_tables()
        except Exception:  # noqa: BLE001
            return []
        out: list[dict] = []
        for table in found:
            try:
                data = table.extract()
            except Exception:  # noqa: BLE001
                continue
            if not data or not data[0]:
                continue
            n_rows, n_cols = len(data), len(data[0])
            cells_total = n_rows * n_cols
            filled = sum(1 for row in data for cell in row
                         if str(cell or "").strip())
            if cells_total < 4 or filled < 0.6 * cells_total:
                continue
            bbox = [round(float(v), 2) for v in table.bbox]
            cell_boxes: list[list[list[float] | None]] = []
            for row in table.rows:
                line_boxes: list[list[float] | None] = []
                for cell in row.cells:
                    line_boxes.append(
                        [round(float(v), 2) for v in cell] if cell else None)
                cell_boxes.append(line_boxes)
            out.append({
                "bbox_pt": bbox,
                "rows": n_rows,
                "cols": n_cols,
                "cell_boxes": cell_boxes,
            })
        return out

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


# ---------------------------------------------------------------- 原生文字层
#
# 这一层不替代 OCR，只把 pdfplumber 的字符坐标变成「行」和「质量标记」：
# - 原生字符可靠 → 记录成候选，OCR 空行时补位；
# - 原生字符是私有字库/乱码 → 标记为 math，不碰 OCR 结果；
# - 两种来源的坐标互相校验，原生有行而像素没有、或反过来，都留证据。
# 纯扫描件没有字符，这一层自动退化为空，不影响主流程。

NATIVE_TEXT_MIN_RATIO = 0.85
NATIVE_TEXT_GOOD_PUNCT = set(
    "，。；：、！？（）《》“”‘’【】—…·,.;:!?()[]{}<>/\\-+*=<>%$#@&_|~^`'\""
)


def _is_private_char(ch: str) -> bool:
    cp = ord(ch)
    return (0xE000 <= cp <= 0xF8FF or 0xF0000 <= cp <= 0x10FFFF
            or cp == 0xFFFD or (cp < 32 and ch not in "\n\t\r"))


_MATH_SYMBOLS = set(
    "×÷±≤≥≠≈∞∑∏∫√∠⊥∥°′″∪∩∈∉⊂⊃⊆⊇→←↔⇒⇔∀∃∂∇∼≅≡≪≫"
    "−⊕⊗○△□"
)


def _looks_math_text(text: str) -> bool:
    """原生行里出现数学符号（含希腊字母/上下标）时不能当作普通正文。

    PDF 原生文字层会把公式拆成普通字符，``x2``、``y0`` 正是上下标丢失后的样子；
    只靠 PUA 判断会把整行误标成可安全采用的正文。
    """
    for ch in text:
        if ch in _MATH_SYMBOLS:
            return True
        cp = ord(ch)
        if 0x0370 <= cp <= 0x03FF:      # 希腊字母
            return True
        if 0x2070 <= cp <= 0x209F:      # 上标/下标
            return True
        if 0x2200 <= cp <= 0x22FF:      # 数学运算符
            return True
        if 0x2A00 <= cp <= 0x2AFF:      # 补充数学运算符（⩽ ⩾ 等）
            return True
        if cp in (0x00B2, 0x00B3, 0x00B9):  # ² ³ ¹
            return True
    return False


def native_text_quality(text: str) -> tuple[str, float]:
    """给一行原生文字打质量标记：``text`` / ``mixed`` / ``math`` / ``empty``。

    数学卷的公式字符常来自私有字库：``ord(ch)`` 落在 PUA，或直接是乱码。
    这类行不能拿来替换 OCR；但普通中文/数字/字母可以。
    """
    chars = [ch for ch in text if not ch.isspace()]
    if not chars:
        return "empty", 0.0
    good = bad = 0
    for ch in chars:
        if _is_private_char(ch):
            bad += 1
        elif "\u4e00" <= ch <= "\u9fff":
            good += 1
        elif ch.isascii():
            if ch.isalnum() or ch in NATIVE_TEXT_GOOD_PUNCT:
                good += 1
            else:
                bad += 1
        elif ch in NATIVE_TEXT_GOOD_PUNCT:
            good += 1
        elif unicodedata.category(ch)[0] in "LN":
            good += 1
        else:
            bad += 1
    ratio = good / max(1, good + bad)
    if _looks_math_text(text):
        return "math", round(ratio, 4)
    if ratio >= NATIVE_TEXT_MIN_RATIO:
        return "text", round(ratio, 4)
    if ratio < 0.55:
        return "math", round(ratio, 4)
    return "mixed", round(ratio, 4)


def _same_text_line(a: dict, b: dict) -> bool:
    """两个字符是否属于同一文本行（允许上下标造成的基线偏移）。"""
    ah, bh = a["bottom"] - a["top"], b["bottom"] - b["top"]
    if ah <= 0 or bh <= 0:
        return False
    overlap = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
    min_h = min(ah, bh)
    if overlap / min_h < 0.28:
        return False
    ac, bc = (a["top"] + a["bottom"]) / 2, (b["top"] + b["bottom"]) / 2
    return abs(ac - bc) <= max(a.get("size", 0), b.get("size", 0)) * 0.75


def native_lines_from_chars(chars: list[dict],
                            exclude_boxes: list[dict] | None = None) -> list[dict]:
    """把字符级坐标聚成原生文本行；图形区里的标注不参与。

    只做"行"粒度，不做列切分：这些卷子是单栏，选项也常在同一视觉行；
    后续按行盒和 OCR 行做重叠匹配即可。
    """
    boxes = [b["pt"] for b in (exclude_boxes or []) if b.get("pt")]
    items: list[dict] = []
    for ch in chars:
        text = (ch.get("text") or "").strip()
        if not text:
            continue
        try:
            top, bottom = float(ch["top"]), float(ch["bottom"])
            left, right = float(ch["x0"]), float(ch["x1"])
            size = float(ch.get("size") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if bottom - top <= 0 or size <= 0:
            continue
        cx, cy = (left + right) / 2, (top + bottom) / 2
        if any(x0 <= cx <= x1 and top <= cy <= bottom
               for x0, top, x1, bottom in boxes):
            continue
        items.append({"text": text, "top": top, "bottom": bottom,
                      "x0": left, "x1": right, "size": size})

    items.sort(key=lambda c: (c["top"], c["x0"]))
    groups: list[list[dict]] = []
    for item in items:
        target = None
        for group in groups:
            probe = {"top": group[0]["top"], "bottom": group[0]["bottom"],
                     "size": group[0]["size"]}
            if _same_text_line(item, probe):
                target = group
                break
        if target is None:
            groups.append([item])
        else:
            target.append(item)

    lines: list[dict] = []
    for group in groups:
        group.sort(key=lambda c: c["x0"])
        size = statistics.median([c["size"] for c in group])
        parts: list[str] = []
        prev = None
        for c in group:
            if prev is not None:
                gap = c["x0"] - prev["x1"]
                # 中文之间不插空格（会改变字面），字母/数字之间按字宽补空格。
                if (gap > size * 0.35 and
                        (prev["text"].isascii() or c["text"].isascii())):
                    parts.append(" ")
            parts.append(c["text"])
            prev = c
        text = "".join(parts).strip()
        if not text:
            continue
        quality, score = native_text_quality(text)
        lines.append({
            "text": text,
            "top": round(min(c["top"] for c in group), 2),
            "bottom": round(max(c["bottom"] for c in group), 2),
            "left": round(min(c["x0"] for c in group), 2),
            "right": round(max(c["x1"] for c in group), 2),
            "size_pt": round(size, 2),
            "quality": quality,
            "quality_score": score,
        })
    lines.sort(key=lambda l: (l["top"], l["left"]))
    return lines


FORMULA_TEXT_MARKERS = set("$\\^_{}=±≤≥≠≈∑∫√")


def _looks_formula_text(text: str) -> bool:
    """原生行是否含公式/数学排版标记。

    这类行即使字符都是“好字符”，原生层也可能把上下标压成普通数字
    （``x2`` 而不是 ``x^2``），所以仍交给 OCR；只对纯文字行走原生快路。
    """
    return any(ch in FORMULA_TEXT_MARKERS for ch in (text or ""))


def _color_hex(value) -> str:
    """pdfplumber 的 non_stroking_color → #RRGGBB（无法判断时返回空串）。"""
    if value is None:
        return ""
    try:
        vals = list(value) if isinstance(value, (list, tuple)) else [float(value)]
    except (TypeError, ValueError):
        return ""
    vals = [max(0.0, min(1.0, float(v))) for v in vals]
    if len(vals) == 1:
        r = g = b = vals[0]
    elif len(vals) == 3:
        r, g, b = vals
    elif len(vals) == 4:
        c, m, y, k = vals
        r, g, b = (1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k)
    else:
        return ""
    return "#%02X%02X%02X" % (round(r * 255), round(g * 255), round(b * 255))


def _font_family(fontname: str) -> str:
    """把 PDF 字体名映射成浏览器可用的字体栈（不依赖本机装了什么）。"""
    name = (fontname or "").lower()
    if any(k in name for k in ("hei", "sans", "arial", "yahei", "gothic")):
        return '"Arial","Microsoft YaHei",sans-serif'
    return '"Times New Roman","SimSun","Songti SC",serif'


def audit_fonts(chars: list[dict], lines: list[dict]) -> dict:
    """统计原生字体与明显乱码字符（PUA / cid / U+FFFD）。"""
    from collections import Counter
    fonts = Counter(str(ch.get("fontname") or "") for ch in chars)
    fonts.pop("", None)
    pua = cid = replacement = 0
    for ch in chars:
        text = str(ch.get("text") or "")
        if "(cid:" in text:
            cid += 1
        if "\ufffd" in text:
            replacement += 1
        if any(0xE000 <= ord(c) <= 0xF8FF for c in text):
            pua += 1
    garbled = 0
    garbled_samples: list[dict] = []
    for line in lines:
        text = str(line.get("text") or "")
        if ("(cid:" in text or "\ufffd" in text
                or any(ord(c) < 32 and c not in "\n\t" for c in text)):
            garbled += 1
            if len(garbled_samples) < 5:
                garbled_samples.append({"ref": line.get("id"), "text": text[:120]})
    return {
        "fonts": dict(fonts.most_common(12)),
        "font_count": len(fonts),
        "pua_chars": pua,
        "cid_chars": cid,
        "replacement_chars": replacement,
        "garbled_lines": garbled,
        "garbled_samples": garbled_samples,
    }


def annotate_line_styles(lines: list[dict], chars: list[dict]) -> int:
    """给每条正文行补 ``style``：字体族、粗体/斜体、颜色。

    原生 PDF 的字体信息比 OCR 可靠；这里只做**样式提示**，不改文本和字号。
    字符中心落在行盒内的参与统计，按字符数取主导字体。
    """
    if not chars:
        return 0
    styled = 0
    for line in lines:
        x0, x1 = float(line["left"]), float(line["right"])
        y0, y1 = float(line["top"]), float(line["bottom"])
        buckets: dict[str, list[dict]] = {}
        for ch in chars:
            try:
                cx = (float(ch["x0"]) + float(ch["x1"])) / 2
                cy = (float(ch["top"]) + float(ch["bottom"])) / 2
            except (KeyError, TypeError, ValueError):
                continue
            if x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1:
                font = str(ch.get("fontname") or "")
                if font:
                    buckets.setdefault(font, []).append(ch)
        if not buckets:
            continue
        font, hits = max(buckets.items(), key=lambda kv: len(kv[1]))
        lower = font.lower()
        style = {
            "font_family": _font_family(font),
            "bold": any(k in lower for k in ("bold", "black", "heavy", "semibold")),
            "italic": any(k in lower for k in ("italic", "oblique")),
        }
        color = _color_hex(hits[0].get("non_stroking_color"))
        if color:
            style["color"] = color
        line["style"] = style
        styled += 1
    return styled


def _native_fast_safe(text: str) -> bool:
    """原生快路只接收“看起来就是普通中文”的行。

    数学行的原生文字常常是 ``B(0;(cid:0)1)``、``(C)jOPj...`` 这种退化形式，
    质量分仍可能是 text；直接跳过 OCR 会把公式变成乱码。中文占比低、带
    ``(cid:)``/替换符/控制符的一律交回 OCR。
    """
    raw = text or ""
    if "(cid:" in raw or "\ufffd" in raw or any(ord(c) < 32 for c in raw):
        return False
    visible = re.sub(r"\s+", "", raw)
    if len(visible) < 2:
        return False
    cjk = len(re.findall(r"[\u4e00-\u9fff]", visible))
    return cjk >= 2 and cjk / len(visible) >= 0.45


def match_native_lines_to_pixels(lines: list[dict], native_lines: list[dict],
                                 scale: float,
                                 report: dict | None = None) -> dict[int, dict]:
    """把可靠原生行匹配到像素行，供 OCR 前的原生快路使用。

    只返回 ``quality == "text"``、不含公式标记且通过 ``_native_fast_safe``
    的行；匹配是严格一对一，宁可漏匹配，不把一个原生行同时贴到两个像素行。

    两条防丢字闸门（2026-10-25，实测 eval01 第 4 页分式行）：

    - **高度覆盖**：原生行必须覆盖像素行高度的 55% 以上。分式 `产销率=销量/产量`
      在字层是“分子”“分母”两条原生行，像素行检测把它合成一条 24.5pt 高的行；
      分母 `产量` 只有 10.4pt 高，旧规则（按较小的行高算重叠）会当成 100% 命中，
      于是整行被替换成 `产量`、跳过 OCR，分子整段消失且无任何告警。
    - **箱内兄弟行**：像素行里还有别的可用原生行、却没被任何像素行认领时，这条
      快路一样不成立（宁可回 OCR）。只认领一条 = 剩下的内容静默丢失。
    """
    reject = {"short_coverage": 0, "sibling_line": 0, "candidates": 0}
    best: dict[int, tuple[float, dict]] = {}
    used: set[int] = set()
    for nl in native_lines:
        if nl.get("quality") != "text":
            continue
        ntext = _norm_native_text(nl.get("text", ""))
        if (len(ntext) < 2 or _looks_formula_text(nl.get("text", ""))
                or not _native_fast_safe(nl.get("text", ""))):
            continue
        nx0, nx1 = float(nl["left"]) * scale, float(nl["right"]) * scale
        ny0, ny1 = float(nl["top"]) * scale, float(nl["bottom"]) * scale
        nh = max(1.0, ny1 - ny0)
        for idx, line in enumerate(lines):
            if idx in used:
                continue
            oy0, oy1 = float(line["px_top"]), float(line["px_bottom"])
            oh = max(1.0, oy1 - oy0)
            v = min(ny1, oy1) - max(ny0, oy0)
            if v / min(nh, oh) < 0.45:
                continue
            # 高度覆盖闸门：小字行不能整替代一条被合并的高行。
            if v / oh < 0.55:
                reject["short_coverage"] += 1
                continue
            ox0, ox1 = float(line["px_left"]), float(line["px_right"])
            h = min(nx1, ox1) - max(nx0, ox0)
            ow = max(1.0, ox1 - ox0)
            if h / max(1.0, min(nx1 - nx0, ow)) < 0.25:
                continue
            score = v / min(nh, oh) + h / max(1.0, nx1 - nx0)
            prev = best.get(idx)
            if prev is None or score > prev[0]:
                best[idx] = (score, nl)
    out: dict[int, dict] = {}
    picked: set[int] = set()
    for idx, (_score, nl) in sorted(best.items(), key=lambda kv: -kv[1][0]):
        key = id(nl)
        if key in picked:
            continue
        picked.add(key)
        out[idx] = nl
    # 箱内兄弟行闸门：认领之后，像素行里若还有未被认领的可信原生行 → 回 OCR。
    claimed = {id(nl) for nl in out.values()}
    for idx in list(out):
        line = lines[idx]
        oy0, oy1 = float(line["px_top"]), float(line["px_bottom"])
        ox0, ox1 = float(line["px_left"]), float(line["px_right"])
        for nl in native_lines:
            if id(nl) in claimed or nl.get("quality") != "text":
                continue
            if not _native_fast_safe(nl.get("text", "")):
                continue
            ncy = (float(nl["top"]) + float(nl["bottom"])) / 2 * scale
            if not (oy0 <= ncy <= oy1):
                continue
            nx0, nx1 = float(nl["left"]) * scale, float(nl["right"]) * scale
            h = min(nx1, ox1) - max(nx0, ox0)
            if h / max(1.0, ox1 - ox0) < 0.25:
                continue
            del out[idx]
            reject["sibling_line"] += 1
            break
    reject["candidates"] = len(out)
    if report is not None:
        report.update(reject)
    return out


def _norm_native_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


_QUESTION_PREFIX_RE = re.compile(r"^\s*(\d{1,3})\s*[.、．]")
def _restore_question_prefix(text: str, native_text: str) -> str:
    """OCR 把题号切掉时，用几何匹配到的原生行补回题号。

    实测 2025 上海第 2 页第 20 题：原生行是 `20. 设椭圆 Γ:`，OCR 从公式中间
    开始，输出 `设椭圆 ...`。题号丢失后会被当成上一题的续行，结构层只能报
    “缺 20”，读者也看不出这里是新题。只在 OCR 文本不以题号/小问号开头、
    且原生行明确以题号开头时补，正文一个字不改。
    """
    if not text or not native_text:
        return text
    stripped = text.lstrip()
    if _QUESTION_PREFIX_RE.match(stripped) or stripped.startswith(("(", "（")):
        return text
    match = _QUESTION_PREFIX_RE.match(str(native_text).lstrip())
    if not match:
        return text
    return f"{match.group(1)}. {stripped}"


def _gutter_question_prefix(line: dict, chars: list[dict]) -> str | None:
    """从栏缝里的原生字符补回被裁掉的题号。

    题号常压在栏边界左侧（实测 2024 北京 Q20 的 `20.` 在 x=420，正文从
    x=440 才开始）。逐栏裁图会把这个题号裁到上一栏，OCR 只剩正文，结构层
    只能报缺号。这里只取紧贴行左边界、同一水平线上的 `数字 + 点`，不碰正文。
    """
    text = str(line.get("text") or "").lstrip()
    if not text or _QUESTION_PREFIX_RE.match(text) or text.startswith(("(", "（")):
        return None
    # pdfplumber 的字符坐标已经是 pt，与行坐标同一坐标系，不能再乘 render scale。
    left, top, bottom = float(line["left"]), float(line["top"]), float(line["bottom"])
    x_lo, x_hi = left - 30, left
    candidates: list[dict] = []
    for c in chars or []:
        if float(c.get("x1", 0)) < x_lo or float(c.get("x1", 0)) > x_hi:
            continue
        overlap = min(float(c.get("bottom", 0)), bottom) - max(float(c.get("top", 0)), top)
        if overlap < max(1.0, (bottom - top) * 0.5):
            continue
        candidates.append(c)
    if not candidates:
        return None
    candidates.sort(key=lambda c: float(c.get("x0", 0)))
    token = "".join(str(c.get("text") or "") for c in candidates).strip()
    match = re.match(r"^(\d{1,3})\s*[.、．]$", token)
    return match.group(1) if match else None


def annotate_native_text(ocr_lines: list[dict], native_lines: list[dict]) -> dict:
    """把原生行按几何匹配到 OCR 行，返回质量摘要并把候选挂回 OCR 行。"""
    used: set[int] = set()
    matched = mismatched = 0
    native_only: list[dict] = []
    mismatch_samples: list[dict] = []
    for nl in native_lines:
        best_idx, best_score = None, 0.0
        ncy = (nl["top"] + nl["bottom"]) / 2
        nh = max(0.1, nl["bottom"] - nl["top"])
        for idx, ol in enumerate(ocr_lines):
            if idx in used:
                continue
            otop, obottom = float(ol.get("top") or 0), float(ol.get("bottom") or 0)
            oh = max(0.1, obottom - otop)
            v = min(nl["bottom"], obottom) - max(nl["top"], otop)
            if v / min(nh, oh) < 0.30:
                continue
            ocy = (otop + obottom) / 2
            if abs(ncy - ocy) > max(nh, oh) * 0.75:
                continue
            left = max(nl["left"], float(ol.get("left") or 0))
            right = min(nl["right"], float(ol.get("right") or 0))
            h = max(0.0, right - left)
            if h / max(1.0, min(nl["right"] - nl["left"],
                                float(ol.get("right") or 0) - float(ol.get("left") or 0))) < 0.10:
                continue
            score = v / min(nh, oh) + h / max(1.0, nl["right"] - nl["left"])
            if score > best_score:
                best_idx, best_score = idx, score
        if best_idx is None:
            native_only.append({"text": nl["text"], "top": nl["top"],
                                "quality": nl["quality"]})
            continue
        used.add(best_idx)
        ol = ocr_lines[best_idx]
        ocr_text = (_norm_native_text(ol.get("_ocr", {}).get("text", "")))
        ntext = _norm_native_text(nl["text"])
        similarity = None
        if ocr_text and ntext:
            similarity = round(difflib.SequenceMatcher(None, ntext, ocr_text).ratio(), 4)
        ol["native_text"] = nl["text"]
        ol["native_left"] = nl["left"]
        ol["native_top"] = nl["top"]
        ol["native_quality"] = nl["quality"]
        ol["native_similarity"] = similarity
        matched += 1
        if (nl["quality"] == "text" and similarity is not None
                and similarity < 0.55 and len(ntext) >= 4 and len(ocr_text) >= 4):
            mismatched += 1
            ol["native_mismatch"] = True
            if len(mismatch_samples) < 5:
                mismatch_samples.append({
                    "ocr": str(ol.get("_ocr", {}).get("text", ""))[:80],
                    "native": nl["text"][:80],
                    "similarity": similarity,
                })

    ocr_only = [idx for idx in range(len(ocr_lines)) if idx not in used]
    return {
        "available": True,
        "lines": len(native_lines),
        "reliable_lines": sum(1 for nl in native_lines if nl["quality"] == "text"),
        "matched": matched,
        "mismatched": mismatched,
        "native_only": len(native_only),
        "ocr_only": len(ocr_only),
        "native_only_samples": native_only[:8],
        "mismatch_samples": mismatch_samples,
    }


# ---------------------------------------------------------------- 逐行 OCR

def crop_line(pil, line: dict, extra_left: int = 0) -> bytes:
    """裁一行给 OCR。

    两处不是“随便加点边”：
    - 栏首行的题号可能悬在栏缝左侧（实测 2025 全国 1 第 16 题 `16.` 比栏起点
      左 12pt），逐栏切图时被切掉；栏首额外向左带 40px 空白，题号才回得来。
    - 150dpi 下小字行只有 ~50px 高，模型会漏字/出乱码；低于 72px 时放大 2 倍
      再送 OCR（300dpi 实测能把 `��֪����` 恢复成 `已知数列`）。
    """
    box = (
        max(0, line["px_left"] - X_PAD_PX - max(0, extra_left)),
        max(0, line["px_top"] - Y_PAD_PX),
        min(pil.width, line["px_right"] + 1 + X_PAD_PX),
        min(pil.height, line["px_bottom"] + 1 + Y_PAD_PX),
    )
    crop = pil.crop(box)
    if crop.height < 72:
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    buf = io.BytesIO()
    crop.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def ocr_line(host: str, model: str, png: bytes, prompt: str,
             timeout: int, max_tokens: int) -> dict:
    return ocr_png(host, model, png, prompt, timeout, max_tokens)


def crop_box_png(pil, px_box: list[float]) -> bytes:
    """按像素盒裁一小块 PNG，供表格单元格单独 OCR。"""
    x0, y0, x1, y1 = (int(round(v)) for v in px_box)
    x0 = max(0, min(pil.width - 1, x0) - 2)
    y0 = max(0, min(pil.height - 1, y0) - 2)
    x1 = max(x0 + 1, min(pil.width, x1 + 2))
    y1 = max(y0 + 1, min(pil.height, y1 + 2))
    buf = io.BytesIO()
    pil.crop((x0, y0, x1, y1)).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def ocr_table_cells(host: str, model: str, pil, cell_boxes: list[list[list[float] | None]],
                    scale: float, prompt: str, timeout: int, max_tokens: int,
                    workers: int) -> list[list[str]]:
    """逐单元格 OCR 原生表格。

    为什么不直接用 pdfplumber 的 cell text：数学卷常用私有中文字库，extract() 会
    吐 `(cid:0)`/乱码；单元格裁图走同一 OCR 引擎，至少与正文同一套识别口径。
    """
    flat: list[tuple[int, int, bytes]] = []
    for r, row in enumerate(cell_boxes):
        for c, box in enumerate(row):
            if not box:
                continue
            px = [box[0] * scale, box[1] * scale, box[2] * scale, box[3] * scale]
            flat.append((r, c, crop_box_png(pil, px)))
    out = [["" for _ in row] for row in cell_boxes]
    if not flat:
        return out

    def work(item):
        r, c, png = item
        try:
            res = ocr_png(host, model, png, prompt, timeout, max_tokens)
            return r, c, (res.get("text") or "").strip()
        except Exception:  # noqa: BLE001
            return r, c, ""

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for r, c, text in pool.map(work, flat):
            out[r][c] = text
    return out


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


def _column_of(line: dict, column_bounds: list[tuple[int, int]], scale: float) -> int:
    """按行中心点落在哪个像素栏，返回从 0 开始的栏号。"""
    center = (line["left"] + line["right"]) / 2 * scale
    for idx, (left, right) in enumerate(column_bounds):
        if left <= center < right:
            return idx
    return 0


def _line_content_type(line: dict) -> str:
    """行级内容类型：table / formula / text。

    这是给下游路由用的粗分类，不替代公式识别；题干里只要出现 LaTeX 定界符或
    数学符号就标 formula，避免把公式行当普通正文再送一遍文本处理。
    """
    if line.get("tables"):
        return "table"
    text = line.get("text") or ""
    if ("$" in text or "\\(" in text or "\\[" in text
            or _looks_math_text(text)):
        return "formula"
    return "text"


def _line_px_box(line: dict, scale: float) -> tuple[int, int, int, int] | None:
    """行盒的像素坐标；兼容旧产物（只有 pt 坐标）。"""
    try:
        if "px_left" in line:
            return (int(line["px_left"]), int(line["px_top"]),
                    int(line["px_right"]), int(line["px_bottom"]))
        return (int(float(line["left"]) * scale), int(float(line["top"]) * scale),
                int(float(line["right"]) * scale), int(float(line["bottom"]) * scale))
    except (KeyError, TypeError, ValueError):
        return None


def audit_page_coverage(gray: np.ndarray, lines: list[dict], figures: list[dict],
                        scale: float, typical_h_px: float,
                        decor: list[dict] | None = None) -> dict:
    """检查“有墨但没被任何行/图盒覆盖”的区域。

    行检测是投影启发式，失败时不会抛错，只会静默漏行；这是最危险的错误。
    这里把已检测的行盒和插图盒画成覆盖掩膜，再找剩余的成片墨迹。这样即使
    OCR 完全没看到某一行，也能在 quality 里留下证据。

    ``decor`` 是已判定为页面装饰的框（页眉二维码/校徽）。它们既不是正文也
    不该被补成插图，所以计入覆盖、但不进 uncovered_regions——否则装饰会把
    "补回漏检插图"的兜底逻辑引回来，等于白过滤。

    只统计成片的竖向行程（过滤单像素噪点），并按页比例给出覆盖率。这个函数
    不依赖 OCR 结果，因此也能发现“整页没切成行”的旋转/扫描页。
    """
    ink = gray < INK_THRESHOLD
    covered = np.zeros(ink.shape, dtype=bool)
    pad = 2

    for line in lines:
        box = _line_px_box(line, scale)
        if not box:
            continue
        x0, y0, x1, y1 = box
        x0 = max(0, x0 - pad)
        y0 = max(0, y0 - pad)
        x1 = min(ink.shape[1], x1 + pad)
        y1 = min(ink.shape[0], y1 + pad)
        if x1 > x0 and y1 > y0:
            covered[y0:y1, x0:x1] = True

    for fig in figures:
        px = fig.get("px") or []
        if len(px) != 4:
            continue        # 图表坐标轴标题、柱顶标注常落在矢量框外几个点到二十几个点处
        # （实测坐标轴“指标”和柱线都在框外）。覆盖率把插图外扩 28pt 当作
        # 图的一部分，否则会把图内文字误报成漏行。
        margin = int(round(28 * scale))
        x0, y0, x1, y1 = (max(0, int(px[0]) - margin), max(0, int(px[1]) - margin),
                          min(ink.shape[1], int(px[2]) + margin),
                          min(ink.shape[0], int(px[3]) + margin))
        if x1 > x0 and y1 > y0:
            covered[y0:y1, x0:x1] = True

    # 页面装饰（页眉二维码等）：计入覆盖，但不参与 uncovered 统计。
    for box in decor or []:
        pt = box.get("pt") or box.get("bbox_pt") or []
        if len(pt) != 4:
            continue
        x0, y0, x1, y1 = (max(0, int(round(float(pt[0]) * scale))),
                          max(0, int(round(float(pt[1]) * scale))),
                          min(ink.shape[1], int(round(float(pt[2]) * scale)) + 2),
                          min(ink.shape[0], int(round(float(pt[3]) * scale)) + 2))
        if x1 > x0 and y1 > y0:
            covered[y0:y1, x0:x1] = True

    ink_total = int(ink.sum())
    if ink_total == 0:
        return {"ink_pixels": 0, "covered_ink_pixels": 0,
                "ratio": 1.0, "uncovered_regions": []}

    uncovered = ink & ~covered
    covered_total = int((ink & covered).sum())
    width = ink.shape[1]
    min_row_ink = max(3, int(width * 0.0015))
    min_height = max(4, int(round(typical_h_px * 0.45)))
    row_active = uncovered.sum(axis=1) >= min_row_ink

    # 把相距 1~2px 的行程合并，避免同一行被字间空白切成多个碎片。
    runs: list[list[int]] = []
    for run in _runs_from_projection(row_active):
        if runs and run[0] - runs[-1][1] <= 2:
            runs[-1][1] = run[1]
        else:
            runs.append(list(run))

    regions: list[dict] = []
    for start, end in runs:
        height = end - start + 1
        if height < min_height:
            continue
        cols = np.where(uncovered[start:end + 1].any(axis=0))[0]
        if len(cols) == 0:
            continue
        x0, x1 = int(cols[0]), int(cols[-1]) + 1
        count = int(uncovered[start:end + 1, x0:x1].sum())
        if count < 30:
            continue
        regions.append({
            "px": [x0, start, x1, end + 1],
            "pt": [round(x0 / scale, 2), round(start / scale, 2),
                   round(x1 / scale, 2), round((end + 1) / scale, 2)],
            "w_pt": round((x1 - x0) / scale, 2),
            "h_pt": round(height / scale, 2),
            "ink": count,
        })

    # 只保留最严重的 20 个，防止坏页把 JSON 撑爆；计数仍然保留真实值。
    regions.sort(key=lambda r: (r["h_pt"] * r["w_pt"], r["ink"]), reverse=True)
    kept = regions[:20]
    return {
        "ink_pixels": ink_total,
        "covered_ink_pixels": covered_total,
        "ratio": round(covered_total / ink_total, 5) if ink_total else 1.0,
        "uncovered_regions": kept,
        "uncovered_regions_total": len(regions),
    }


def figures_from_uncovered(pil, coverage: dict, existing: list[dict], dpi: int) -> list[dict]:
    """把“有墨但没被任何行/图盒覆盖”的成片区域补成插图裁片。

    原生图形资源偶尔会漏掉一整张函数图象（实测 2024 全国理 Q7 的参考图没有
    vector/image 记录），覆盖率审计能看见它，却只剩一个 warning。对面积足够、
    又不与已有插图重叠的区域直接裁片，比把整张图静默丢掉更安全。
    """
    extras: list[dict] = []
    for region in coverage.get("uncovered_regions", []):
        w_pt = float(region.get("w_pt") or 0)
        h_pt = float(region.get("h_pt") or 0)
        if w_pt < 36 or h_pt < 28 or int(region.get("ink") or 0) < 250:
            continue
        px = region.get("px") or []
        if len(px) != 4:
            continue
        x0, y0, x1, y1 = (float(v) for v in px)
        overlap = False
        for fig in existing:
            fpx = fig.get("px") or []
            if len(fpx) != 4:
                continue
            ix = max(0.0, min(x1, float(fpx[2])) - max(x0, float(fpx[0])))
            iy = max(0.0, min(y1, float(fpx[3])) - max(y0, float(fpx[1])))
            if ix * iy > 0.35 * max(1.0, (x1 - x0) * (y1 - y0)):
                overlap = True
                break
        if overlap:
            continue
        png = crop_box_png(pil, [x0, y0, x1, y1])
        extras.append({
            "pt": list(region.get("pt") or []),
            "px": [int(x0), int(y0), int(x1), int(y1)],
            "w_pt": round(w_pt, 2),
            "h_pt": round(h_pt, 2),
            "source": "coverage-region",
            "png_base64": base64.b64encode(png).decode("ascii"),
        })
    return extras


def classify_page(gray: np.ndarray, lines: list[dict], gutters: list[int],
                  chars: list[dict], page_w: float, page_h: float) -> dict:
    """页面类型/方向/文字层标记；给下游决定“空白页、图像页还是正常试卷页”。"""
    ink = int((gray < INK_THRESHOLD).sum())
    area = max(1, gray.shape[0] * gray.shape[1])
    ratio = ink / area
    if not lines and ratio < 0.0002:
        kind = "blank"
    elif not lines:
        kind = "no-lines"
    else:
        kind = "text"
    orientation = "landscape" if page_w > page_h * 1.18 else "portrait"
    return {
        "kind": kind,
        "orientation": orientation,
        "columns": len(gutters) + 1,
        "has_text_layer": bool(chars),
        "ink_ratio": round(ratio, 5),
    }


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
    native_total = {"chars": 0, "lines": 0, "reliable": 0, "matched": 0,
                    "mismatched": 0, "native_only": 0, "ocr_only": 0,
                    "fallbacks": 0, "fast": 0, "fast_rejected": 0}
    coverage_total = {"ink_pixels": 0, "covered_ink_pixels": 0,
                      "uncovered_regions": 0, "blank_pages": 0}
    styled_total = 0
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
            try:
                rotation = int(page_obj.get_rotation())
            except Exception:  # noqa: BLE001 - 旧版 pdfium 没有这个接口
                rotation = 0

        chars = (font_probe.char_boxes(pno)
                 if font_probe is not None and not args.no_figures else [])
        native_tables = (font_probe.table_boxes(pno)
                         if font_probe is not None and not args.no_figures else [])
        native_boxes = (font_probe.figure_boxes(pno, exclude_boxes=native_tables)
                        if font_probe is not None and not args.no_figures else [])
        # 页眉二维码/校徽/印章：几何上“小且贴页眉页脚”，当装饰过滤，不当插图。
        page_furniture = [b for b in native_boxes if _is_page_furniture(b["pt"], pw, ph)]
        if page_furniture:
            native_boxes = [b for b in native_boxes if b not in page_furniture]
            log(f"第 {pno} 页过滤页眉页脚装饰图 {len(page_furniture)} 个")
        # 装饰框仍要参与行投影掩膜，否则二维码的墨迹会变成“未覆盖墨迹”。
        gray_for_lines = _mask_figure_areas(gray, native_boxes + page_furniture, scale)
        if native_boxes:
            log(f"第 {pno} 页原生图形资源 {len(native_boxes)} 个，已从行投影中剔除")
        gutters = detect_column_gutters(gray, chars=chars, scale=scale)
        column_bounds = [(0, gray.shape[1])]
        if gutters:
            column_bounds = []
            start = 0
            for gg in gutters:
                column_bounds.append((start, gg))
                start = gg
            column_bounds.append((start, gray.shape[1]))
            log(f"第 {pno} 页检测到 {len(gutters) + 1} 栏"
                f"（中缝 x={gutters}），逐栏检测行")

        mask = (None if args.no_figures
                else text_coverage_mask(chars, gray_for_lines.shape, scale))
        raw_lines: list[dict] = []
        extra_lines: list[dict] = []
        figure_regions: list[dict] = []
        typical_heights: list[float] = []

        # 关键：双栏/三栏页必须先按栏切图，再在每栏内部做行投影。
        # 若整页行投影后再按 x 拆行，一栏的段间距会被另一栏的文字填满，
        # 多行块会粘成一条超高行（实测 2024 北京 Q20 整块被吞掉）。
        for col_left, col_right in column_bounds:
            col_gray = gray_for_lines[:, col_left:col_right]
            col_raw, col_typical = detect_lines(col_gray)
            typical_heights.append(col_typical)
            for line in col_raw:
                line["px_left"] += col_left
                line["px_right"] += col_left
            raw_lines.extend(col_raw)

            if args.no_figures:
                continue
            col_mask = (mask[:, col_left:col_right]
                        if mask is not None else None)
            col_extra, col_figures = split_and_classify_regions(
                col_gray, col_typical, col_mask)
            for line in col_extra:
                line["px_left"] += col_left
                line["px_right"] += col_left
            for fig in col_figures:
                box = fig.get("px") or []
                if len(box) == 4:
                    fig["px"] = [box[0] + col_left, box[1],
                                 box[2] + col_left, box[3]]
            extra_lines.extend(col_extra)
            figure_regions.extend(col_figures)

            # 表格：PaddleOCR-VL 会把表格压成逐格换行文本、结构不可恢复，
            # 所以按"长直线游程"识别表格区域，把整块当图裁出来（内容与结构都保真）。
            table_regions = detect_table_grids(col_gray, col_typical)
            for tr in table_regions:
                px = tr.get("px") or []
                if len(px) == 4:
                    tr["px"] = [px[0] + col_left, px[1],
                                px[2] + col_left, px[3]]
            figure_regions.extend(table_regions)
            if table_regions:
                cells = ", ".join("{}x{}格".format(t["rows"], t["cols"])
                                  for t in table_regions)
                log(f"第 {pno} 页识别到 {len(table_regions)} 个表格候选区（{cells}）")

        # pdfplumber 找到的表格框与单元格：裁片仍交给 collect_figures，单元格文字
        # 单独 OCR。不要用 pdfplumber 的 extract() 文本——数学卷中文字库常被抽成 (cid:0)。
        for hint in native_tables:
            box = hint["bbox_pt"]
            figure_regions.append({
                "px": [max(0, int(box[0] * scale) - 4),
                       max(0, int(box[1] * scale) - 4),
                       min(pil.width, int(box[2] * scale) + 5),
                       min(pil.height, int(box[3] * scale) + 5)],
                "kind": "table-grid",
                "source": "pdf-table",
                "rows": hint["rows"], "cols": hint["cols"],
            })

        typical_h_px = float(np.median(typical_heights)) if typical_heights else 24.0
        noise = [l for l in raw_lines if _is_noise(l)]
        lines = [l for l in raw_lines if not _is_noise(l)] + extra_lines
        before = len(lines)
        lines = dedupe_lines(lines)
        if before != len(lines):
            log(f"第 {pno} 页合并 {before - len(lines)} 条重复行（行投影与高块重切同时命中）")
        used = "raster-projection"
        sizes_hint = font_probe.char_boxes(pno) if font_probe is not None else None
        native_lines = []
        # 原生文字层也必须逐栏建行。整页建行会把同一水平线上的 Q20 与 Q21
        # 拼成一行（实测 2024 北京第 2 页），题号 `21.` 因此被算到 Q20 的
        # 原生前缀里，下一栏的 Q21 永远补不回题号。
        for col_left, col_right in column_bounds:
            col_chars = [c for c in (sizes_hint or [])
                         if col_left / scale <= (c["x0"] + c["x1"]) / 2 < col_right / scale]
            native_lines.extend(native_lines_from_chars(
                col_chars, native_boxes if not args.no_figures else []))

        # 原生快路：可靠的纯文字行不再逐行 OCR；公式行仍走 OCR，避免把
        # 上下标压成普通数字。没有文字层/质量不够时自动退回全量 OCR。
        native_fast: dict[int, dict] = {}
        if args.text_source == "auto" and native_lines:
            fast_report: dict = {}
            native_fast = match_native_lines_to_pixels(lines, native_lines, scale,
                                                       report=fast_report)
            for idx in native_fast:
                lines[idx]["_native_fast"] = True
            native_total["fast_rejected"] += (fast_report.get("short_coverage", 0)
                                              + fast_report.get("sibling_line", 0))
            if fast_report.get("short_coverage") or fast_report.get("sibling_line"):
                log(f"第 {pno} 页原生快路拒绝 {fast_report['short_coverage']} 条矮行匹配、"
                    f"{fast_report['sibling_line']} 条箱内多行（回 OCR，防丢字）")
            if native_fast:
                log(f"第 {pno} 页原生快路 {len(native_fast)} 行（跳过 OCR）")

        # 兜底：整页只有一条超长行时不硬拆，直接整页 OCR。
        # 注意 0 行不能走这里：空白页/纯图页没有行是正常分类，硬造一条整页行
        # 会调用 OCR 并把空白页误报成“整页解析失败”。
        if args.single_line_fallback and len(lines) == 1:
            lines = [{"px_top": 0, "px_bottom": gray.shape[0] - 1,
                      "px_left": 0, "px_right": gray.shape[1] - 1,
                      "px_height": gray.shape[0], "ink": int((gray < INK_THRESHOLD).sum())}]
            used = "whole-page-fallback"
            native_fast = {}
            warnings.append(f"第 {pno} 页行检测只得到 1 行，退回整页 OCR")

        # 页顶栏首行向左多带一点，救回悬在栏缝里的题号（2025 全国 1 第 16 题）。
        # 只限页顶：栏中部的节标题若也带左邻栏文字，会变成 `时三、填空题`，
        # 直接漏掉整节。
        col_starts = [b[0] for b in column_bounds]
        page_top_px = min((l["px_top"] for l in lines), default=0)
        task_png = [
            None if idx in native_fast else crop_line(pil, l, 40 if (
                col_starts and l["px_top"] <= page_top_px + 8
                and min(abs(l["px_left"] - s) for s in col_starts) <= 16) else 0)
            for idx, l in enumerate(lines)
        ]

        results: list[dict | None] = [None] * len(lines)
        for idx, nl in native_fast.items():
            results[idx] = {"text": nl["text"], "finish_reason": "native-text",
                            "votes": 0, "disagreement": False,
                            "elapsed_s": 0.0, "native_fast": True}

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

        pending = [idx for idx in range(len(lines)) if idx not in native_fast]
        workers = min(max(1, len(pending)), args.workers)
        if pending:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(work, pending))

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

        # 原生表格：逐单元格 OCR。行盒只用于把表挂到行上（正文那一行会被
        # `drop_lines_inside_figures` 丢掉），表本身写进 page["tables"]，不依赖行数组。
        table_by_line: dict[int, dict] = {}
        tables_out: list[dict] = []
        for hint in native_tables:
            box = hint["bbox_pt"]
            best_idx, best_area = -1, 0.0
            for idx, line in enumerate(lines):
                ix = min(line["right"], box[2]) - max(line["left"], box[0])
                iy = min(line["bottom"], box[3]) - max(line["top"], box[1])
                if ix <= 0 or iy <= 0:
                    continue
                area = ix * iy
                if area > best_area:
                    best_idx, best_area = idx, area
            cells = ocr_table_cells(
                args.host, args.model, pil, hint["cell_boxes"], scale,
                args.prompt, args.timeout, args.max_tokens,
                min(max(1, args.workers), max(1, hint["rows"] * hint["cols"])))
            table = {
                # 稳定 id：下游 exam/quality/区域层都靠它回指表格，不能靠数组下标。
                "id": f"p{pno}-t{len(tables_out) + 1}",
                "page": pno,
                "source": "pdf-table-cells",
                "bbox_pt": box,
                "rows": hint["rows"],
                "cols": hint["cols"],
                "headers": cells[0] if cells else [],
                "rows_data": cells[1:] if len(cells) > 1 else [],
                "text_available": any(any(c for c in row) for row in cells),
            }
            tables_out.append(table)
            if best_idx >= 0:
                table_by_line[best_idx] = table
        if tables_out:
            log(f"第 {pno} 页原生表格 {len(tables_out)} 个，已逐单元格 OCR")

        native_info = (annotate_native_text(lines, native_lines) if native_lines
                       else {"available": bool(sizes_hint), "lines": 0,
                             "reliable_lines": 0, "matched": 0, "mismatched": 0,
                             "native_only": 0, "ocr_only": 0,
                             "native_only_samples": []})
        native_info["chars"] = len(sizes_hint or [])
        if native_info["available"]:
            log(f"第 {pno} 页原生文字 {native_info['lines']} 行"
                f"（可靠 {native_info['reliable_lines']}），匹配 OCR "
                f"{native_info['matched']} 行，原生独有 {native_info['native_only']}，"
                f"OCR 独有 {native_info['ocr_only']}")
        native_fallbacks = 0
        native_fast_used = 0

        page_lines = []
        dropped = 0
        raw_texts = [line["_ocr"]["text"] for line in lines]
        for idx, line in enumerate(lines):
            res = line.pop("_ocr")
            # 表格单独成结构（不能让它被压成一行文本）
            tables, cleaned = extract_tables(clean_ocr_text(res["text"]), res["text"])
            if idx in table_by_line:
                tables = [table_by_line[idx]]
                cleaned = "（表格）"
            text_source = "ocr"
            if line.get("_native_fast") and (cleaned or tables):
                text_source = "native-text"
                native_fast_used += 1
            elif (not cleaned and not tables
                    and line.get("native_quality") == "text"
                    and line.get("native_text")):
                native_cleaned = clean_ocr_text(line["native_text"])
                if native_cleaned and not _is_hallucination(native_cleaned):
                    cleaned = native_cleaned
                    text_source = "native-text"
                    native_fallbacks += 1
            if not cleaned and not tables:
                continue
            if not cleaned and tables:
                cleaned = "（表格）"
            if text_source == "ocr" and line.get("native_mismatch"):
                native_left = line.get("native_left")
                ocr_left = line.get("left")
                # 原生行常跨栏合并：第 1 栏的 `1.` 与第 2 栏末尾的 OCR 行
                # 几何上会匹配到同一条。只有原生行左边界就在 OCR 行附近，
                # 题号才属于这一行。
                if (native_left is not None and ocr_left is not None
                        and abs(float(native_left) - float(ocr_left)) <= 24):
                    cleaned = _restore_question_prefix(cleaned, line.get("native_text"))
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
                "text_source": text_source,
                "native_text": line.get("native_text"),
                "native_quality": line.get("native_quality"),
                "native_similarity": line.get("native_similarity"),
                "native_mismatch": line.get("native_mismatch", False),
                "tables": tables,
                "finish_reason": res["finish_reason"],
                "votes": res.get("votes"),
                "disagreement": bool(res.get("disagreement")),
                "elapsed_s": res["elapsed_s"],
            })
        if dropped:
            log(f"第 {pno} 页丢弃 {dropped} 条图形区幻觉行（超长数字串/孤立字母标签）")
        native_info["fast"] = native_fast_used
        styled_lines = annotate_line_styles(page_lines, chars)
        native_info["styled"] = styled_lines
        if styled_lines:
            log(f"第 {pno} 页恢复 {styled_lines} 行原生字体样式")
        font_audit = audit_fonts(chars, page_lines)
        if font_audit["garbled_lines"]:
            log(f"第 {pno} 页发现 {font_audit['garbled_lines']} 行疑似乱码")

        # 插图：像素检测出的图形区 → 裁片 → 原位贴回前端
        figures = collect_figures(pil, figure_regions, pw, ph,
                                  keep_images=not args.no_images) if figure_regions else []
        if native_boxes:
            native_figures = crop_native_figures(
                page_obj, native_boxes, pw, ph, args.dpi,
                keep_images=not args.no_images)
            figures = merge_native_figures(figures, native_figures)
            if len(figures) != len(native_figures):
                log(f"第 {pno} 页原生图形与像素检测合并后共 {len(figures)} 张")
        if figures:
            page_lines, in_fig = drop_lines_inside_figures(page_lines, figures)
            if in_fig:
                log(f"第 {pno} 页丢弃 {in_fig} 条落在插图内的正文行"
                    f"（裁片已含图上标注，再叠一份就是重复）")
            # 裁片不能压住正文：把伸进题干的边界收回来
            clipped = clip_figures_to_avoid_text(pil, figures, page_lines, pw, ph)
            dropped_figs = [f for f in figures if f.get("drop")]
            if dropped_figs:
                figures = [f for f in figures if not f.get("drop")]
                log(f"第 {pno} 页丢弃 {len(dropped_figs)} 个压住正文的矢量碎片")
            if clipped:
                log(f"第 {pno} 页收缩 {clipped} 张裁片边界（避免压住题干）")
            sizes_text = ", ".join("{:.0f}x{:.0f}pt".format(f["w_pt"], f["h_pt"])
                                   for f in figures)
            log(f"第 {pno} 页还原插图 {len(figures)} 张（{sizes_text}）")

        # 原生字层常保留题号，而 OCR 裁图可能把栏首题号切掉（静安 Q6 的 `6.`）。
        # 只在行首没有题号时用原生行首补，避免给续行硬塞数字。
        native_prefixes = 0
        for line in page_lines:
            if re.match(r"^\s*\d{1,3}\s*[.、．)）]", line.get("text") or ""):
                continue
            native_text = line.get("native_text") or ""
            qm_native = re.match(r"^\s*(\d{1,3})\s*[.、．)）](?!\d)\s*", native_text)
            if qm_native:
                line["text"] = f"{qm_native.group(1)}. {line['text'].lstrip()}"
                line["native_prefix_restored"] = True
                native_prefixes += 1
        if native_prefixes:
            log(f"第 {pno} 页从原生字层补回 {native_prefixes} 个题号")

        restored_prefixes = 0
        for line in page_lines:
            prefix = _gutter_question_prefix(line, chars)
            if prefix:
                line["text"] = f"{prefix}. {line['text'].lstrip()}"
                line["question_prefix_restored"] = True
                restored_prefixes += 1
        if restored_prefixes:
            log(f"第 {pno} 页从栏缝补回 {restored_prefixes} 个题号")

        coverage = audit_page_coverage(gray, page_lines, figures, scale, typical_h_px,
                                       decor=page_furniture)
        fallback_figures = figures_from_uncovered(
            pil, coverage, figures, args.dpi)
        if fallback_figures:
            figures.extend(fallback_figures)
            coverage = audit_page_coverage(
                gray, page_lines, figures, scale, typical_h_px, decor=page_furniture)
            log(f"第 {pno} 页补回 {len(fallback_figures)} 张漏检插图"
                f"（覆盖率 {coverage['ratio']:.3%}）")
        classification = classify_page(gray, page_lines, gutters, chars, pw, ph)
        classification["rotation"] = rotation
        if coverage.get("uncovered_regions"):
            uncovered_count = coverage.get(
                "uncovered_regions_total", len(coverage["uncovered_regions"]))
            log(f"第 {pno} 页覆盖率 {coverage['ratio']:.3%}，"
                f"发现 {uncovered_count} 处未覆盖墨迹区域（已写入 quality）")
            warnings.append(f"第 {pno} 页覆盖率 {coverage['ratio']:.3%}，"
                            f"有 {uncovered_count} 处未覆盖墨迹")

        # 行序 = 页面自上而下。检测是按行墨迹投影出的行程顺序，理论上已是自上而下，
        # 但并叠下划线、过滤空行之后仍可能错序（渲染时表现为第 4 题跑到第 2 题上面）。
        # 显式排序，版面顺序不能依赖上游的偶然性。
        # 横向多栏卷仍用同一口径：viewer 按坐标绝对定位，行序只影响文本复制顺序；
        # 若改成“先栏后行”，check_layout_json 的“自上而下”硬门会失败。
        page_lines.sort(key=lambda l: (l["top"], l["left"]))

        # 稳定 ID / 阅读顺序 / 内容类型：下游 exam/quality 不靠数组下标猜身份。
        # 行数组仍保持自上而下，兼容 check_layout_json 的硬门；栏感知顺序另存。
        columns_pt = [{"left": round(a / scale, 2), "right": round(b / scale, 2)}
                      for a, b in column_bounds]
        for idx, line in enumerate(page_lines, start=1):
            line["id"] = f"p{pno}-l{idx}"
            line["column"] = _column_of(line, column_bounds, scale)
            line["content_type"] = _line_content_type(line)
        reading = sorted(page_lines, key=lambda l: (l["column"], l["top"], l["left"]))
        for idx, line in enumerate(reading):
            line["reading_index"] = idx
        for idx, fig in enumerate(figures, start=1):
            fig["id"] = f"p{pno}-f{idx}"
            fig["page"] = pno
            fig["content_type"] = ("table" if fig.get("source") == "table-grid"
                                   else "figure")
        page_reading_ids = [l["id"] for l in reading]

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
            "columns": columns_pt,
            "reading_order": page_reading_ids,
            "page_type": classification,
            "coverage": coverage,
            "lines": page_lines,
            "figures": figures,
            "tables": tables_out,
            "markdown": "\n".join(l["text"] for l in reading),
            "elapsed_s": round(time.time() - t_page, 2),
            "native_text": native_info,
            "font_audit": font_audit,
            # 被挡掉、没有当成插图的页眉页脚装饰（二维码/校徽/水印），
            # 留下来是为了让“少图”告警可以证明自己不是漏图。
            "page_furniture": page_furniture,
        }
        # 区域层（T1）：行 / 图 / 表收成带稳定 id 的语义区域。必须在行与图表的
        # id、content_type 都定好之后、append 之前生成；lines/figures/tables 原字段不动。
        entry["regions"] = build_regions(entry, pno)
        coverage_total["ink_pixels"] += coverage.get("ink_pixels", 0)
        coverage_total["covered_ink_pixels"] += coverage.get("covered_ink_pixels", 0)
        coverage_total["uncovered_regions"] += coverage.get(
            "uncovered_regions_total", len(coverage.get("uncovered_regions") or []))
        if classification.get("kind") == "blank":
            coverage_total["blank_pages"] += 1
        native_total["chars"] += native_info.get("chars", 0)
        native_total["lines"] += native_info.get("lines", 0)
        native_total["reliable"] += native_info.get("reliable_lines", 0)
        native_total["matched"] += native_info.get("matched", 0)
        native_total["mismatched"] += native_info.get("mismatched", 0)
        native_total["native_only"] += native_info.get("native_only", 0)
        native_total["ocr_only"] += native_info.get("ocr_only", 0)
        native_total["fallbacks"] += native_fallbacks
        native_total["fast"] += native_fast_used
        styled_total += styled_lines
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
    font_audit_total = {"fonts": {}, "font_count": 0, "pua_chars": 0,
                        "cid_chars": 0, "replacement_chars": 0,
                        "garbled_lines": 0, "garbled_samples": []}
    font_counts: dict[str, int] = {}
    for p in out_pages:
        fa = p.get("font_audit") or {}
        for name, count in (fa.get("fonts") or {}).items():
            font_counts[name] = font_counts.get(name, 0) + int(count)
        for key in ("pua_chars", "cid_chars", "replacement_chars", "garbled_lines"):
            font_audit_total[key] += int(fa.get(key) or 0)
        if len(font_audit_total["garbled_samples"]) < 10:
            font_audit_total["garbled_samples"].extend(
                (fa.get("garbled_samples") or [])[:10])
    font_audit_total["fonts"] = dict(sorted(font_counts.items(),
                                             key=lambda kv: -kv[1])[:20])
    font_audit_total["font_count"] = len(font_counts)
    # 区域层汇总：文档级记录总数与分布，验收报告不用逐年重算。
    regions_total = region_stats(out_pages)
    payload = {
        "schema_version": 1,
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
            "coverage": {
                "ink_pixels": coverage_total["ink_pixels"],
                "covered_ink_pixels": coverage_total["covered_ink_pixels"],
                "ratio": (round(coverage_total["covered_ink_pixels"]
                                 / coverage_total["ink_pixels"], 5)
                          if coverage_total["ink_pixels"] else 1.0),
                "uncovered_regions": coverage_total["uncovered_regions"],
                "blank_pages": coverage_total["blank_pages"],
            },
            "native_text": {
                "available": native_total["chars"] > 0,
                "chars": native_total["chars"],
                "lines": native_total["lines"],
                "reliable_lines": native_total["reliable"],
                "matched_lines": native_total["matched"],
                "mismatched_lines": native_total["mismatched"],
                "native_only_lines": native_total["native_only"],
                "ocr_only_lines": native_total["ocr_only"],
                "fallbacks": native_total["fallbacks"],
                "fast_path_lines": native_total["fast"],
                "styled_lines": styled_total,
            },
            "font_audit": font_audit_total,
            "regions": regions_total,
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
            "native_text_lines": native_total["lines"],
            "native_text_matched": native_total["matched"],
            "native_text_mismatched": native_total["mismatched"],
            "native_text_fallbacks": native_total["fallbacks"],
            "native_text_fast_rejected": native_total["fast_rejected"],
            "native_text_fast_path": native_total["fast"],
            "styled_lines": styled_total,
            "font_count": font_audit_total["font_count"],
            "garbled_lines": font_audit_total["garbled_lines"],
            "regions": regions_total["count"],
            "region_types": regions_total["by_content_type"],
            "page_furniture": sum(len(p.get("page_furniture") or []) for p in out_pages),
            "text_source_counts": {
                src: sum(1 for l in all_lines if l.get("text_source") == src)
                for src in ("ocr", "native-text")
            },
        },
        "pages": out_pages,
    }
    return payload


LAYOUT_VIEWER = "viewer_layout.html"     # 生成的阅读器文件名（各引擎加后缀，见 paths）


def _delims_for(engine: str) -> list[dict]:
    """按引擎选公式定界符。

    OvisOCR2 输出 `$...$` / `$$...$$`；llama.cpp 时代的 PaddleOCR-VL 输出 `\\(...\\)`。

    ⚠️ 但**官方 PaddleOCR-VL 流程**（PP-DocLayoutV3 + PaddleOCR-VL-1.6，见
    `tools/import_paddleocr_json.py`）输出的也是 `$...$`——它的 engine 名里同样带
    "paddle"。单靠名字已经分不出这两者，而选错的后果是**整页公式退化成 LaTeX 源码**
    （README 排障表里那条"公式显示成源码"）。所以这里改成：名字里带 paddle 时**两种
    都给**，顺序上先长后短（`$$` 在 `$` 前）。KaTeX 的 auto-render 按顺序匹配，
    多给一组不影响正确性，只把误判的代价消掉。
    """
    all_delims = [
        {"left": "$$", "right": "$$", "display": True},
        {"left": "$", "right": "$", "display": False},
        {"left": "\\[", "right": "\\]", "display": True},
        {"left": "\\(", "right": "\\)", "display": False},
    ]
    if "paddle" in (engine or "").lower():
        # 美元符优先（官方流程的产物），`\(` 兜底（llama.cpp 时代的产物）
        return all_delims
    return all_delims


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
    ensure_python_version()

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
    ap.add_argument("--text-source", choices=("auto", "ocr"), default="auto",
                    help="auto=可靠原生文字行跳过 OCR；ocr=全部走 OCR")
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
