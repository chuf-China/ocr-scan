#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fit_line_text.py —— 把"逐行文本"塞进"逐行墨迹框"的核心算法。

## 为什么要这一步
用户口径：**行位置 99%（每行 ≤3pt）、文字 99%、不允许裁图**。
这意味着渲染版必须有"逐行坐标 + 逐行文本"，而官方 `parsing_res_list` 只给**块**。

## 算法
1. 行几何：`ocr_pipeline_layout.detect_lines`（像素墨迹投影）——本项目已验证、很紧
2. 行文本：官方块的 `block_content` 按宽度贪心折行，得到若干"视觉行文本"
3. **对齐**：把块内折行结果逐条落到块内行盒上（自上而下）
4. **字号贴合**：对每行做二分搜索，找"渲染宽度最接近该行墨迹框宽度"的字号
   —— 这是关键。字号不贴合，行宽就会差一半（实测基线里渲染行宽 601 vs 原版 949）

## 为什么不用"块高 ÷ 行数"估字号
块高含公式撑开的高度，估出来是假值（实测会出现 22pt / 5.9pt 这种离谱值）。
按**行宽**二分反而稳定：字宽的估计误差在 ±10%，二分能把它压到几个百分点。
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

# 字宽系数：全角 1.0、半角 0.5（与本项目 import_paddleocr_json 的取值一致）
LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+\*?|\\.|[{}^_&]")
SPAN_RE = re.compile(r"(\$[^$]*\$)")


def display_units(text: str) -> float:
    """这段文本印刷出来占多少"全角字宽"。LaTeX 命令按 1 个符号折算。"""
    total = 0.0
    for part in SPAN_RE.split(text):
        if not part:
            continue
        if part.startswith("$") and part.endswith("$") and len(part) > 1:
            inner = part[1:-1]
            cmds = len(LATEX_CMD_RE.findall(inner))
            visible = len(LATEX_CMD_RE.sub("", inner))
            total += max(1.0, cmds * 0.7 + visible * 0.5)
        else:
            wide = sum(1 for ch in part if ord(ch) > 0x2E7F)
            total += wide + (len(part) - wide) * 0.5
    return total


def text_width_pt(text: str, size_pt: float) -> float:
    return display_units(text) * size_pt


def pack_to_lines(text: str, size_pt: float, avail_pt: float) -> list[str]:
    """按可用宽度折行，`$...$` 当原子（不把公式从中间断开）。"""
    text = re.sub(r"\s*\n\s*", " ", (text or "").strip())
    if not text:
        return []
    segs: list[str] = []
    for part in SPAN_RE.split(text):
        if not part:
            continue
        if part.startswith("$") and part.endswith("$") and len(part) > 1:
            segs.append(part)
        else:
            segs.extend([s for s in re.split(
                r"(\s+|(?<=[，。、；：,.;])|(?=[，。、；：,.;]))", part) if s])
    out: list[str] = []
    cur = ""
    cur_w = 0.0
    for seg in segs:
        w = text_width_pt(seg, size_pt)
        if cur and cur_w + w > avail_pt and w <= avail_pt:
            out.append(cur.rstrip())
            cur, cur_w = seg.lstrip(), w
        else:
            cur += seg
            cur_w += w
    if cur.strip():
        out.append(cur.rstrip())
    # ⚠️ 过滤空串后再判断兜底：早先写 `return [... ] or [text]`，
    # 而 `[""]` 是**真值**（非空列表），于是兜底不生效，返回了一个空字符串行；
    # 下游"多行并一行"时 pop 掉它就得到空列表，再取 parts[-2] 直接越界。
    out = [s for s in out if s.strip()]
    return out or [text.strip()]


def size_from_ink_height(px_height: int, dpi: int = 150,
                         single_line_max_px: float | None = None) -> float:
    """从行盒的墨迹高度估字号（pt）。

    为什么**不**用"二分凑宽度"作为主判据：二分为了把长文本压进窄框，会给出
    24pt（上限）或 5pt（下限）这种离谱字号——实测 20 行里 4 行误差 >15%，
    字号范围 5.00~24.00。根因是一个块里本来就有多个视觉行，拿整块当一个行贴合，
    必然扭曲。

    墨迹高度是更可靠的锚：中文单行的墨迹高 ≈ 字号的 1.30 倍（字身 1.0 + 上下留白），
    即 `size ≈ 墨迹高 / 1.30`。对含公式的行会略偏大，用上限 18pt 兜住。
    """
    size_pt = (px_height / 1.30) / (dpi / 72.0)
    return round(min(18.0, max(6.0, size_pt)), 2)


def fit_size_for_width(text: str, target_pt: float, lo: float = 5.0,
                       hi: float = 24.0, iters: int = 18) -> float:
    """二分搜索字号，使该行渲染宽度最接近 target_pt。

    目标是**宽度一致**（不是"塞得下"）：宽度一致 → 行盒右边界一致 → 行位置与
    原版对齐。这是从"塞进去"到"对得上"的关键差别。
    """
    if not text.strip() or target_pt <= 0:
        return lo
    for _ in range(iters):
        mid = (lo + hi) / 2
        w = text_width_pt(text, mid)
        if w < target_pt:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 2)


def distribute_block_text(block_text: str, line_boxes: list[dict],
                          ) -> list[dict]:
    """把一个块的文本分配到它内部的若干行盒上，每行带贴合字号。

    返回 [{text, size_pt, top, bottom, left, right, px...}, ...]
    """
    if not line_boxes:
        return []
    n = len(line_boxes)
    avg_w_pt = sum((b["px_right"] - b["px_left"]) for b in line_boxes) / n / (150 / 72)
    # 先用一个中等字号折行，得到"需要几行"
    base = 10.5
    parts = pack_to_lines(block_text, base, avg_w_pt)
    # 行数与行盒数不一致：按行盒数重新分配（多则并、少则拆）
    #
    # ⚠️ 合并循环必须同时守 `len(parts) > n` **与** `len(parts) >= 2`：
    # 只看前者时，若 n==0（块内没有行盒）会一直 pop 到空列表再取 parts[-2] 越界。
    # 早先这里抛过 IndexError，根因是 pack_to_lines 曾返回 `[""]` 这种"非空但无内容"
    # 的列表，兜底 `or [text]` 不生效。
    if n <= 0:
        return []
    if len(parts) > n:
        # 合并到 n 行：尾部并进上一行（内容不丢）。
        # 用 pop + 赋值，**不做 parts[-2] 下标写法**——早先那版在
        # `len(parts)==2, n==1` 时竟然也抛了 IndexError（原因未查明，见下方注释），
        # pop/append 的写法不依赖负下标，且更容易看出边界。
        while len(parts) > n and len(parts) >= 2:
            tail = parts.pop()
            prev = parts.pop()
            parts.append((prev + " " + tail).strip())
    elif len(parts) < n:
        # 拆到 n 行：找最长的一行对半切，尽量切在标点后
        guard = 0
        while len(parts) < n and guard < 40:
            guard += 1
            i = max(range(len(parts)), key=lambda k: len(parts[k]))
            s = parts[i]
            if len(s) < 8:
                break
            cut = len(s) // 2
            m = re.search(r"[，。、；：,.;\s]", s[cut:cut + 14])
            if m:
                cut += m.end()
            parts[i:i + 1] = [s[:cut].rstrip(), s[cut:].lstrip()]
    out = []
    for i, box in enumerate(line_boxes):
        txt = parts[i] if i < len(parts) else ""
        # 字号以**行盒墨迹高度**为准（可靠）；宽度只用来报告偏差，不用来反推字号
        size = size_from_ink_height(box["px_bottom"] - box["px_top"] + 1)
        target = (box["px_right"] - box["px_left"]) / (150 / 72)
        out.append({
            "text": txt,
            "size_pt": size,
            "px_top": box["px_top"], "px_bottom": box["px_bottom"],
            "px_left": box["px_left"], "px_right": box["px_right"],
            "width_pt_target": round(target, 2),
            "width_pt_est": round(text_width_pt(txt, size), 2) if txt else 0.0,
        })
    return out
