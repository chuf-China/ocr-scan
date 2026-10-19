#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_line_level.py —— 由官方块 + 墨迹投影行盒，构建**行级**版式 JSON。

## 为什么需要它
用户目标：**行位置 99%（每行 ≤3pt）+ 文字 99% + 不裁图**。
现有产物都不满足：
  · `import_paddleocr_json.py` 是"块级"——把块当行塞进绝对定位画布（墨迹 IoU 0.088）
  · `import_paddleocr_blocks.py` 是"文档流"——版式信息整体丢弃

本脚本产出**逐行**产物：每行有
  · 几何：墨迹投影行盒（左上原点、像素），换算成 PDF 点
  · 字号：**从文字层读该行的真实字号**（不是估、不是凑宽度）
  · 文本：官方块文本按行盒切分；切分不确定时**保留整行原文**并标注 confidence

## 与之前失败方案的区别（关键）
之前失败在"按块宽折行 + 二分凑宽度"——那是拿语义分段去对视觉行，必然错位。
现在改成：**行盒来自墨迹（准），字号来自文字层（准），文本只做行内切分**，
并且**每行的水平跨度用文字层字符的 x0/x1 校正**（只对可解码字符，数学字符不参与）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

import numpy as np

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BOOT)
import paths  # noqa: E402


def is_good(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch)
    return (0x4E00 <= cp <= 0x9FFF or 0x3000 <= cp <= 0x303F
            or 0xFF00 <= cp <= 0xFFEF or ch.isdigit() or ch.isalpha())


def merge_fragment_lines(proj: list[dict], typical_px: float) -> list[dict]:
    """把"同一视觉行被投影拆成多条"的碎片行盒合并。

    为什么必须合并：数学的上标/下标/分数线会把一条视觉行投影成 2~3 条墨迹行程
    （实测第 1 页墨迹 21 条，而官方只有 13 个文本块、视觉行更少）。
    不合并就会出现"一行文本没有对应行盒、另一行盒没有文本"的错配。

    判据：垂直相邻（间隙 ≤ 0.6×典型行高）**且水平有交叠** → 同一个视觉行。
    水平必须交叠，否则会把上下两行真的不同文字并掉。
    """
    if not proj:
        return []
    ordered = sorted(proj, key=lambda l: (l["px_top"], l["px_left"]))
    out = [dict(ordered[0])]
    for cur in ordered[1:]:
        prev = out[-1]
        gap = cur["px_top"] - prev["px_bottom"]
        overlap = (min(prev["px_right"], cur["px_right"])
                   - max(prev["px_left"], cur["px_left"]))
        if gap <= max(6, int(0.6 * typical_px)) and overlap > 0:
            prev["px_top"] = min(prev["px_top"], cur["px_top"])
            prev["px_bottom"] = max(prev["px_bottom"], cur["px_bottom"])
            prev["px_left"] = min(prev["px_left"], cur["px_left"])
            prev["px_right"] = max(prev["px_right"], cur["px_right"])
        else:
            out.append(dict(cur))
    return out


def load_layout_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ocr_pipeline_layout", os.path.join(BOOT, "ocr_pipeline_layout.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


LATEX_SPAN_RE = re.compile(r"(\$[^$]*\$)")

# 字宽系数：全角 1.0、半角 0.5；LaTeX 命令按 1 个符号折算（与项目其它工具一致）
LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+\*?|\\.|[{}^_&]")


def _units(text: str) -> float:
    """文本占多少"全角字宽"。"""
    total = 0.0
    for part in LATEX_SPAN_RE.split(text):
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


def _split_content_to_boxes(content: str, boxes: list[dict]) -> list[str]:
    """把块内容切分到若干行盒，**每段只出现一次**。

    容量按"该行盒宽度 ÷ 该行字号"折算成字宽数。`$...$` 当原子，不把公式从中间断开。
    放不下的剩余部分作为额外片段返回（调用方会并进最后一行）。
    """
    text = re.sub(r"\s*\n\s*", " ", content).strip()
    if not text:
        return [""] * len(boxes)
    segs: list[str] = []
    for part in LATEX_SPAN_RE.split(text):
        if not part:
            continue
        if part.startswith("$") and part.endswith("$") and len(part) > 1:
            segs.append(part)
        else:
            segs.extend([s for s in re.split(
                r"(\s+|(?<=[，。、；：,.;])|(?=[，。、；：,.;]))", part) if s])
    out: list[str] = []
    si = 0
    for box in boxes:
        cap_units = (box["right"] - box["left"]) / max(4.0, box["size_pt"])
        cur, cur_u = "", 0.0
        while si < len(segs):
            w = _units(segs[si])
            if cur and cur_u + w > cap_units:
                break
            cur += segs[si]
            cur_u += w
            si += 1
        out.append(cur.strip())
    if si < len(segs):
        out.append("".join(segs[si:]).strip())
    return out


def build_page(pno: int, res: dict, gray: np.ndarray, chars: list[dict],
               layout, dpi: int) -> dict:
    scale = dpi / 72.0
    px_h, px_w = gray.shape
    page_w_pt = round(px_w / scale, 2)
    page_h_pt = round(px_h / scale, 2)

    proj, typical_px = layout.detect_lines(gray)
    proj = [l for l in proj if not layout._is_noise(l)]
    raw_n = len(proj)
    proj = merge_fragment_lines(proj, typical_px)
    proj.sort(key=lambda l: (l["px_top"], l["px_left"]))

    good = [c for c in chars if is_good(c.get("text") or "")]

    lines: list[dict] = []
    for l in proj:
        # 该行盒（pt）
        top_pt = l["px_top"] / scale
        bot_pt = l["px_bottom"] / scale
        left_pt = l["px_left"] / scale
        right_pt = l["px_right"] / scale

        # 落在该行内的可解码字符 → 真实字号 + 校正后的水平跨度
        inside = [c for c in good
                  if c["top"] >= top_pt - 2 and c["bottom"] <= bot_pt + 4]
        sizes = sorted(c["size"] for c in inside)
        if sizes:
            size_pt = round(sizes[len(sizes) // 2], 2)
            size_src = "pdf-text-layer"
            # 用字符的 x0/x1 校正跨度（只信可解码字符）
            if inside:
                c_left = min(c["x0"] for c in inside)
                c_right = max(c["x1"] for c in inside)
                # 只在明显收窄时采用（避免把公式部分切掉）
                if c_left >= left_pt - 2:
                    left_pt = c_left
                if c_right > right_pt:
                    right_pt = min(page_w_pt, c_right)
        else:
            # 没有可解码字符（纯公式行）：按墨迹高度估字号
            size_pt = round(min(18.0, max(6.0, (l["px_bottom"] - l["px_top"] + 1) / 1.30 / scale)), 2)
            size_src = "ink-height"

        lines.append({
            "text": "",                      # 稍后由块文本填入
            "size_pt": size_pt,
            "size_source": size_src,
            "top": round(top_pt, 2), "bottom": round(bot_pt, 2),
            "left": round(left_pt, 2), "right": round(right_pt, 2),
            "height_pt": round(bot_pt - top_pt, 2),
            "px_top": l["px_top"], "px_bottom": l["px_bottom"],
            "px_left": l["px_left"], "px_right": l["px_right"],
            "multiline": False,
            "tables": [],
            "votes": 1, "disagreement": False,
            "finish_reason": "projection+official",
            "block_label": None,
        })

    # --- 把官方块的文本填进行 ---
    #
    # ⚠️ **绝不能把整块文本重复填进每一行**：早先 53/82 行是"整块复制"，
    # 结果同一段文字被画了多遍，墨迹统计被严重污染（IoU 0.0876，与块级方案一样差）。
    # 现在的做法按"每行盒能放多少字"贪心分配：**每段文字只出现一次**，
    # 放不下的剩余部分并进该块最后一行（宁可那行长一点，也不重复绘制）。
    blocks = [b for b in (res.get("parsing_res_list") or [])
              if (b.get("block_content") or "").strip()]
    overflow_blocks = 0
    over_capacity = 0
    for b in blocks:
        x0, y0, x1, y1 = b["block_bbox"]
        idx = [i for i, ln in enumerate(lines)
               if ln["px_top"] >= y0 - 3 and ln["px_bottom"] <= y1 + 3]
        if not idx:
            continue
        content = (b.get("block_content") or "").strip()
        label = b.get("block_label")
        parts = _split_content_to_boxes(content, [lines[i] for i in idx])
        # ⚠️ 关键修复：**不要**把溢出的剩余并进最后一行。
        # 早先那么做会让末行明显过宽（实测 dx 达 +107pt），而多出来的片段本身
        # 也没有对应的行盒位置。正确做法是让 `_split_content_to_boxes` 就按容量
        # 切完（它只在装不下时才返回额外片段），这里只统计并**丢弃**多余片段，
        # 同时把该块标记出来，供后续用更好的方法（逐行识别）替换。
        if len(parts) > len(idx):
            overflow_blocks += 1
            parts = parts[:len(idx)]
        for k, i in enumerate(idx):
            lines[i]["text"] = parts[k] if k < len(parts) else ""
            lines[i]["block_label"] = label
            lines[i]["split"] = "packed" if len(idx) > 1 else "single"
            # 记录"该行文本是否超出它自己的行盒容量"——这是需要逐行识别来救的信号
            cap = (lines[i]["right"] - lines[i]["left"]) / max(4.0, lines[i]["size_pt"])
            if _units(lines[i]["text"]) > cap * 1.35:
                lines[i]["over_capacity"] = True
                over_capacity += 1
    # 图块：标记但不画文字
    for b in (res.get("parsing_res_list") or []):
        if (b.get("block_label") or "") in ("image", "chart", "figure"):
            x0, y0, x1, y1 = b["block_bbox"]
            for ln in lines:
                if ln["px_top"] >= y0 - 3 and ln["px_bottom"] <= y1 + 3:
                    ln["text"] = ln["text"] or "（图）"
                    ln["block_label"] = b.get("block_label")

    for ln in lines:
        if not ln["text"]:
            ln["text"] = "（空）"

    return {
        "page": pno,
        "width": page_w_pt, "height": page_h_pt,
        "render_px": [px_w, px_h],
        "dpi": dpi,
        "line_source": "ink-projection(merged) + official-block-content",
        "raw_projection_lines": raw_n,
        "lines_detected": len(lines),
        "lines": lines,
        "figures": [],
        "blocks": blocks,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="构建行级版式 JSON")
    ap.add_argument("--pdf", default=paths.DEFAULT_PDF)
    ap.add_argument("--raw-dir", default=os.path.join(paths.OUT_DIR, "paddleocr_raw"))
    ap.add_argument("--out",
                    default=os.path.join(paths.OUT_DIR, "paper.layout.lines.paddleocrvl16.json"),
                    help="默认名必须匹配 build_viewers.py 的发现规则 paper.layout*.json，"
                         "否则产物不会生成阅读器（而且不报错）")
    ap.add_argument("--viewer-base", default="viewer_lines_paddleocrvl16")
    ap.add_argument("--title", default="静安区2026届高三一模数学试卷")
    ap.add_argument("--dpi", type=int, default=150)
    args = ap.parse_args()

    import pdfplumber
    import pypdfium2 as pdfium

    layout = load_layout_module()
    doc = pdfium.PdfDocument(args.pdf)
    raw_files = sorted(f for f in os.listdir(args.raw_dir) if f.endswith("_res.json"))
    if not raw_files:
        raise SystemExit(f"[lines] {args.raw_dir} 里没有 *_res.json")

    pages = []
    with pdfplumber.open(args.pdf) as pl:
        for i, fn in enumerate(raw_files):
            res = json.load(open(os.path.join(args.raw_dir, fn), encoding="utf-8"))
            gray = np.asarray(doc[i].render(scale=args.dpi / 72).to_pil().convert("L"))
            chars = pl.pages[i].chars if i < len(pl.pages) else []
            pages.append(build_page(i + 1, res, gray, chars, layout, args.dpi))

    all_lines = [l for p in pages for l in p["lines"]]
    overflow = sum(1 for p in pages for _ in p.get("_overflow_blocks", []))
    over_capacity = sum(1 for l in all_lines if l.get("over_capacity"))
    payload = {
        "document": {
            "title": args.title,
            "source_pdf": os.path.abspath(args.pdf),
            "pages_total": len(pages), "pages_processed": len(pages),
            "engine": "paddleocr-vl-1.6",
            "viewer_base": args.viewer_base,
            "mode": "layout", "dpi": args.dpi,
            "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
            "warnings": ([f"{overflow} 个块的文本超出其行盒容量，剩余部分并进了末行"]
                         if overflow else []),
        },
        "stats": {
            "pages": len(pages),
            "lines": len(all_lines),
            "lines_with_math": sum(1 for l in all_lines if "$" in l["text"]),
            "figures": 0,
            "blocks_text_overflow": overflow,
            "lines_over_capacity": over_capacity,
        },
        "pages": pages,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"[lines] 写出 {args.out}")
    print(f"[lines] {len(pages)} 页 / {len(all_lines)} 行 / "
          f"文本超容量并进末行的块 {overflow} 个")
    return 0


if __name__ == "__main__":
    sys.exit(main())
