#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""import_paddleocr_json.py —— 把**官方 PaddleOCR-VL 流程**的输出接进本项目。

为什么要这个转换器：本项目原先两个流水线都是"自己造版面分析"——
`ocr_pipeline_layout` 用像素投影猜文本行、用文字蒙版猜插图、用框线游程猜表格；
`ocr_pipeline_blocks` 用正则猜「一、填空题」和「1.」。而 PaddleOCR-VL 的官方流程
（PP-DocLayoutV3 版面分析 + PaddleOCR-VL-1.6 VLM 识别）**本来就输出这些结构**：

    parsing_res_list[]:
        block_label    doc_title / paragraph_title / text / table / formula / image / …
        block_content  该块的 Markdown/LaTeX 内容（文本、公式、HTML 表格）
        block_bbox     [x1, y1, x2, y2]  左上原点，页图**像素**坐标
        block_order    阅读顺序（版面分析直接给的，不用自己排）
        group_id       同一逻辑组的块（如跨栏合并）

所以转换器只做坐标换算与标签映射，**不再做任何结构猜测**。

用法：
    python tools/import_paddleocr_json.py <paddleocr_out.json> [--out out/paper.layout.paddleocr-vl.json]
    python tools/import_paddleocr_json.py <dir_with_*_res.json> --out ...   # 目录则按页序合并

退出码：0 成功；3 输入问题。
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import sys

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BOOT)
import paths  # noqa: E402

# 版面标签 → 本项目的块类型。官方标签比本项目的四种类型细，做一次收敛。
LABEL_TO_TYPE = {
    "doc_title": "heading",
    "paragraph_title": "heading",
    "title": "heading",
    "text": "text",
    "table": "table",
    "formula": "formula",
    "image": "figure",
    "chart": "figure",
    "figure": "figure",
    "seal": "figure",
    "abstract": "text",
    "reference": "text",
    "footnote": "text",
    "aside_text": "text",
    "number": "text",
    "header": "header",
    "footer": "footer",
}
HEADING_LEVEL = {"doc_title": 1, "paragraph_title": 2, "title": 2}

# 官方 PaddleOCR-VL 输出用的是 `$...$` / `$$...$$` 定界符。
# ⚠️ 不要照抄本项目 `_delims_for()` 里 "paddle → \(...\)" 的假设：那个假设只对
#    llama.cpp 时代的 `paddleocr-vl` 成立，官方流程输出的是美元符。
#    阅读器按引擎名选错定界符，页面上的公式会整片退化成 LaTeX 源码。
FORMULA_DELIMS = ("$", r"\(", r"\[")


def to_pdf_pt(bbox: list, scale_x: float, scale_y: float,
              page_h_pt: float) -> dict:
    """像素盒（左上原点）→ PDF 点（**左上原点**，与本项目一致）。

    pypdfium2 的 render(scale=dpi/72) 是线性缩放，所以 x/scale_x 即得点坐标；
    y 需要翻转到左上原点——本项目 JSON 里 top/bottom 都是"从上往下量"。
    """
    x1, y1, x2, y2 = [float(v) for v in bbox[:4]]
    left, right = x1 / scale_x, x2 / scale_x
    top, bottom = y1 / scale_y, y2 / scale_y
    return {
        "left": round(left, 2), "top": round(top, 2),
        "right": round(right, 2), "bottom": round(bottom, 2),
        "px": [round(x1), round(y1), round(x2), round(y2)],
        "w_pt": round(right - left, 2), "h_pt": round(bottom - top, 2),
    }


HTML_TABLE_RE = re.compile(r"<table\b.*?</table>", re.S | re.I)


def split_table_html(html: str) -> dict | None:
    """把官方给的 HTML 表格拆成 {headers, rows}（与本项目的 table 结构对齐）。"""
    m = HTML_TABLE_RE.search(html)
    if not m:
        return None
    grid: list[list[str]] = []
    for tr in re.findall(r"<tr\b.*?</tr>", m.group(0), re.S | re.I):
        cells = [re.sub(r"<[^>]+>", "", c) for c in
                 re.findall(r"<t[hd]\b.*?</t[hd]>", tr, re.S | re.I)]
        grid.append([c.strip() for c in cells])
    if not grid:
        return None
    return {"headers": grid[0], "rows": grid[1:], "source": "paddleocr-vl"}


def _font_sizes_in_box(chars: list[dict], box: dict) -> list[float]:
    """取落在块框内的文字层字符字号（与 ocr_pipeline_layout.enrich_size 同判据）。"""
    return [c["size"] for c in chars
            if box["top"] - 2 <= c["top"] and c["bottom"] <= box["bottom"] + 4]


def enrich_sizes_from_pdf(pages: list[dict], pdf_path: str) -> dict:
    """用 PDF 文字层给每个块填**真实字号**。

    为什么必须做：官方 `parsing_res_list` 不带字号，而用"块高 × 系数"估字号是错的
    ——一个块可能含 5 行文字，块高是 5 行之和，估出来的字号会到 40~80pt（实测被
    结构校验器直接判为异常）。这份卷子的文字层是好的（中文/ASCII 可解码），
    所以直接读文字层字号，与本项目 `enrich_size` 采用同一判据。
    """
    try:
        import pdfplumber
    except ImportError:
        return {"available": False, "filled": 0,
                "reason": "未安装 pdfplumber（pip install pdfplumber）"}
    filled = 0
    with pdfplumber.open(pdf_path) as doc:
        for p in pages:
            idx = p["page"] - 1
            if idx >= len(doc.pages):
                continue
            chars = doc.pages[idx].chars
            for line in p["lines"]:
                # enrich_size 的口径在 PDF 点坐标下比较；这里 line 的 top/bottom 已是点
                sizes = _font_sizes_in_box(chars, line)
                if sizes:
                    sizes.sort()
                    line["size_pt"] = round(sizes[len(sizes) // 2], 2)
                    line["size_source"] = "pdf-text-layer"
                    filled += 1
    return {"available": True, "filled": filled}


def drop_lines_inside_figures(lines: list[dict], figures: list[dict]) -> int:
    """丢掉"基本落在图块内"的文字行。

    为什么必须丢：图块区域里本来就有图上标注（`A₁ D₁ B₁`、坐标刻度），VLM 有时会把
    它们也当文本块输出，于是页面上**图里一套、文字层再叠一套**。旧流水线
    `ocr_pipeline_layout.drop_lines_inside_figures` 用同一判据（≥50% 面积交叠）。
    更关键的是：`tools/check_render.py` 会查"插图压正文"，这层不丢就会直接失败
    （实测第 2/4/5 页共 4 处、交叠最高 14213pt²）。
    """
    if not figures:
        return 0
    kept: list[dict] = []
    dropped = 0
    for line in lines:
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
    lines[:] = kept
    return dropped


def add_figure_crops(pages: list[dict], pdf_path: str, dpi: int) -> int:
    """给图块补真正的裁片（png_base64），并写入每页原图（image_jpeg_base64）。

    为什么要裁片：官方 `block_content` 对图块就是空的——它只说"这块是图"。而本项目
    的阅读器是靠**裁片**把原图贴回页面的（矢量还原不在范围内）。

    为什么要整页 JPEG：阅读器顶部的**「原始扫描」视图**直接读 `page.image_jpeg_base64`，
    没有它那个视图就是空白。实测漏了这一步时阅读器只有 0.18MB（旧产物 0.90MB）。
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return 0
    made = 0
    doc = pdfium.PdfDocument(pdf_path)
    for p in pages:
        idx = p["page"] - 1
        if idx >= len(doc):
            continue
        pil = doc[idx].render(scale=dpi / 72).to_pil().convert("RGB")
        # 整页原图：「原始扫描」视图用；JPEG q72 与 ocr_pipeline_layout 取值一致
        pbuf = io.BytesIO()
        pil.save(pbuf, "JPEG", quality=72, optimize=True)
        p["image_jpeg_base64"] = base64.b64encode(pbuf.getvalue()).decode("ascii")
        for fig in p.get("figures") or []:
            x0, y0, x1, y1 = fig["px"]
            box = (max(0, int(x0)), max(0, int(y0)),
                   min(pil.width, int(x1)), min(pil.height, int(y1)))
            if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                continue
            buf = io.BytesIO()
            pil.crop(box).save(buf, "PNG", optimize=True)
            fig["png_base64"] = base64.b64encode(buf.getvalue()).decode("ascii")
            fig["has_crop"] = True
            made += 1
    return made


LATEX_CMD_RE = re.compile(r"\\[a-zA-Z]+\*?|\\.|[{}^_&]|\\[\[\]()]")


def _display_len(text: str) -> float:
    """估算"这段文本印刷出来占几个字宽"。

    LaTeX 命令（`\\frac`、`\\mathbb`、`\\geq`）在源码里占好几个字符，但排出来通常
    只有 1~2 个符号宽。直接按字符数算会严重高估公式的宽度，进而把字号压得过小
    （实测出现过 6.88pt）。这里把 `$...$` 内部按"可见符号数"折算。
    """
    out = 0.0
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "$":
            j = text.find("$", i + 1)
            if j < 0:
                out += _plain_len(text[i:])
                break
            inner = text[i + 1:j]
            # 命令当 1 个符号，其余字符各算 1（上下标略不计）
            cmds = len(LATEX_CMD_RE.findall(inner))
            visible = len(re.sub(r"\\[a-zA-Z]+\*?|\\.", "", inner))
            out += max(1.0, cmds * 0.8 + visible * 0.55)
            i = j + 1
        else:
            j = text.find("$", i)
            if j < 0:
                j = n
            out += _plain_len(text[i:j])
            i = j
    return out


def _plain_len(text: str) -> float:
    """非公式部分的宽度（全角 1.0 / 半角 0.55）。"""
    wide = sum(1 for ch in text if ord(ch) > 0x2E7F)
    return wide + (len(text) - wide) * 0.55


def _text_width_pt(text: str, size_pt: float) -> float:
    """估文本渲染宽度（pt），用于折行与宽度复核。"""
    return _display_len(text) * size_pt


def _pack_lines(text: str, size_pt: float, avail_pt: float) -> list[str]:
    """按可用宽度折行，**公式整体不拆开**。

    为什么必须折：阅读器的 `.ln` 是 `white-space: nowrap`（原设计"一次 OCR 调用 =
    一个行盒"），官方流程给的是**整块文本**，不折行就一路撑出纸外。

    为什么公式要当原子：实测按字符切会把 `$...$` 从中间断开
    （`...  (x+2` / `\\geq 0\\} $ ...`），两条行都渲染不出公式，比不折更糟。
    所以先把文本切成"公式段 / 普通段"，普通段再按空白与标点细分。
    """
    text = re.sub(r"\s*\n\s*", " ", text).strip()
    if not text:
        return []
    # 切段：$...$ 整体成段；其余按空白/中文标点切，便于换行时落在自然边界
    segs: list[str] = []
    for part in re.split(r"(\$[^$]*\$)", text):
        if not part:
            continue
        if part.startswith("$") and part.endswith("$") and len(part) > 1:
            segs.append(part)
        else:
            segs.extend([s for s in re.split(r"(\s+|(?<=[，。、；：,.;])|(?=[，。、；：,.;]))", part) if s])
    parts: list[str] = []
    cur = ""
    cur_w = 0.0
    for seg in segs:
        w = _text_width_pt(seg, size_pt)
        if cur and cur_w + w > avail_pt and _text_width_pt(seg, size_pt) <= avail_pt:
            parts.append(cur.rstrip())
            cur, cur_w = seg.lstrip(), w
        else:
            cur += seg
            cur_w += w
    if cur.strip():
        parts.append(cur.rstrip())
    return [p for p in parts if p.strip()] or [text]


# 块内排版的水平留白：块框宽度略有富余（检测框通常比文字宽一点），
# 直接用整块宽估行会偏乐观，留 6% 更稳。
AVAIL_WIDTH_RATIO = 0.94
# 一行文本占的竖直空间相对字号的倍数（中文正文行距约 1.15~1.25 倍字身）
LINE_PITCH_RATIO = 1.20
# 字号下限：再小就不必缩了（缩到看不清不如让它略溢出）
MIN_SIZE_PT = 5.0

# 竖向特别高的公式：`cases`/`matrix`/`aligned` 这类环境在**行内**渲染时会明显超出
# 行高的 1.2 倍（实测 `\begin{cases}` 那一行渲染高 26.1px，而声明行盒只有 20.6px，
# 于是压住下一行——全篇就剩这 1 处叠字）。给含这些结构的行多留一点竖直空间。
TALL_MATH_RE = re.compile(r"\\begin\{|\\dfrac|\\binom|\\substack|\\stackrel|\\overbrace|\\underbrace")
TALL_MATH_PITCH = 1.7


def _pitch_ratio(text: str) -> float:
    return TALL_MATH_PITCH if TALL_MATH_RE.search(text) else LINE_PITCH_RATIO


def _fit_block_text(text: str, size_hint: float, avail_pt: float,
                    max_lines: int, size_floor: float) -> tuple[list[str], float, float]:
    """求"这块文本要几行、字号该多大"。

    返回 (折行结果, 最终字号, 行距pt)。**整块用同一个字号**——同段落里一行大一行小
    是明显的伪影（实测出现过 8.36 / 6.94 / 10.45 混排），代价只是可能少塞几个字。

    迭代逻辑：
      ① 字号太大 → 行数超过行位上限（`max_lines`，来自文字层行聚类）→ 缩小重排；
      ② 逐行宽度复核：仍有一行放不下就再缩（不缩的话浏览器会自行折行，
         折出来的块高是声明行盒的 2~3 倍，那就是压住下一行的叠字）。
    """
    size = max(size_floor, size_hint)
    parts = _pack_lines(text, size, avail_pt)
    for _ in range(8):
        if len(parts) <= max_lines or size <= size_floor + 1e-6:
            break
        size = max(size_floor, size * max(0.7, (max_lines / len(parts)) ** 0.6))
        new_parts = _pack_lines(text, size, avail_pt)
        if len(new_parts) >= len(parts) and size <= size_floor + 1e-6:
            break
        parts = new_parts

    # 逐行宽度复核（整块统一缩放，不做单行微调）。
    # ⚠️ 下限是 `size_floor`（真实字号的 ~0.85），**不是 MIN_SIZE_PT**：
    # 早先这里一直缩到 5pt，把含 `cases` 的长公式行压成 5.89pt——放是放下了，
    # 但完全不可读。宁可这一行竖向略挤出一点，也不能把字缩到看不清。
    for _ in range(6):
        widest = max((_text_width_pt(p, size) for p in parts), default=0.0)
        if widest <= avail_pt * 0.98 or size <= size_floor + 1e-6:
            break
        size = max(size_floor, size * (avail_pt * 0.96 / widest))
        parts = _pack_lines(text, size, avail_pt)

    # 每行的行距按其内容定：含 cases/matrix 这类高公式的行多留竖直空间
    pitches = [round(size * _pitch_ratio(p), 2) for p in parts]
    return parts, round(size, 2), pitches


def _is_good_char(ch: str) -> bool:
    """该字符是否"能解码"（这份 PDF 的数学符号是坏私有字形，位置也不可靠）。"""
    if not ch:
        return False
    cp = ord(ch)
    return (0x4E00 <= cp <= 0x9FFF or 0x3000 <= cp <= 0x303F
            or 0xFF00 <= cp <= 0xFFEF or ch.isdigit() or ch.isalpha())


def _glyph_lines(chars: list[dict], box_pt: list[float], typical_h: float) -> int:
    """数一个块里有几个**视觉行**——只用能解码的字符，不看它们的 x 顺序。

    为什么要这一层：块高 ÷ 行高估不准（数学块的高度是公式撑起来的），像素投影
    会把一个含公式的视觉行拆成多个墨迹行程（实测 Q12 得 6 行、块里只有 1~2 行）。
    而汉字/ASCII 的字符盒是准的——它们足以数出"这块有几行"。
    """
    x0, y0, x1, y1 = box_pt
    inside = [c for c in chars
              if x0 - 2 <= c["x0"] and c["x1"] <= x1 + 2
              and y0 - 2 <= c["top"] and c["bottom"] <= y1 + 4
              and _is_good_char(c.get("text") or "")]
    inside.sort(key=lambda c: c["top"])
    clusters: list[dict] = []             # [{sum, n, mean}]
    for c in inside:
        mid = (c["top"] + c["bottom"]) / 2
        hit = None
        for cl in clusters:
            if abs(mid - cl["mean"]) < typical_h * 0.5:
                hit = cl
                break
        if hit is None:
            clusters.append({"sum": mid, "n": 1, "mean": mid})
        else:
            hit["sum"] += mid
            hit["n"] += 1
            hit["mean"] = hit["sum"] / hit["n"]
    return len(clusters)


def attach_blocks_projected_lines(block_lists: list[list[dict]], pdf_path: str,
                                  dpi: int) -> int:
    """给每个块挂上"块内真实文本行盒"（像素投影，来自本项目已验证的 detect_lines）。

    为什么用投影而不是文字层：实测同一块内数学坏字形的字符盒与文字行错开、
    x 次序还会倒退（392→258），按 y 聚行会得到 3 行而块只有 1 行。像素墨迹投影
    在这份 PDF 上是本项目原本就依赖的判据（旧产物 77 行就是这么来的，这里 6 页 82 行）。

    ⚠️ 必须在构建 `lines[]` **之前**调用——行盒是折行的行位来源。
    """
    try:
        import numpy as np
        import pypdfium2 as pdfium
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ocr_pipeline_layout", os.path.join(BOOT, "ocr_pipeline_layout.py"))
        layout = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(layout)
    except Exception as exc:  # noqa: BLE001
        # 不要静默吞掉：这一步失效会直接退化成"整块一个 nowrap 行盒"，
        # 表现为水平越界，但完全没有报错——最难查的那种。
        print(f"[import] 警告：块内行盒展开不可用（{type(exc).__name__}: {exc}），"
              f"退回整块单行", file=sys.stderr)
        return 0

    scale = dpi / 72.0
    doc = pdfium.PdfDocument(pdf_path)
    try:
        import pdfplumber
        plumber = pdfplumber.open(pdf_path)
    except Exception:  # noqa: BLE001
        plumber = None
    attached = 0
    for pno, blocks in enumerate(block_lists, start=1):
        idx = pno - 1
        if idx >= len(doc):
            continue
        gray = np.asarray(doc[idx].render(scale=scale).to_pil().convert("L"))
        proj, _ = layout.detect_lines(gray)
        proj = [l for l in proj if not layout._is_noise(l)]
        chars = plumber.pages[idx].chars if plumber and idx < len(plumber.pages) else []
        # 典型行高：用该页能解码字符的中位字号 × 1.3
        sizes = sorted(c["size"] for c in chars if _is_good_char(c.get("text") or ""))
        typical_pt = (sizes[len(sizes) // 2] * 1.3) if sizes else 14.0
        for b in blocks:
            x0, y0, x1, y1 = b["px"]
            inside = [l for l in proj
                      if l["px_top"] >= y0 - 3 and l["px_bottom"] <= y1 + 3]
            inside.sort(key=lambda l: l["px_top"])
            if inside:
                b["_line_boxes"] = [[l["px_left"], l["px_top"], l["px_right"], l["px_bottom"]]
                                    for l in inside]
                attached += 1
            # 文字层数出的**真实视觉行数**与**真实字号**：都比估算可靠
            if chars:
                n = _glyph_lines(chars, [b["left"], b["top"], b["right"], b["bottom"]],
                                 typical_pt)
                if n:
                    b["_glyph_lines"] = n
                bsizes = sorted(
                    c["size"] for c in chars
                    if b["left"] - 2 <= c["x0"] and c["x1"] <= b["right"] + 2
                    and b["top"] - 2 <= c["top"] and c["bottom"] <= b["bottom"] + 4
                    and _is_good_char(c.get("text") or ""))
                if bsizes:
                    b["_font_pt"] = round(bsizes[len(bsizes) // 2], 2)
    if plumber is not None:
        plumber.close()
    return attached


def parse_blocks(res: dict, page_no: int, dpi: int) -> tuple[list[dict], float, float, float]:
    """一份 *_res.json → (块列表, 页宽pt, 页高pt, scale)。

    ⚠️ 拆出这一步是必须的：行盒展开（`attach_projected_lines`）要挂在**块**上，
    而 `lines[]` 是从块构建的。早先两者写在同一个函数里、且顺序颠倒，结果
    `_line_boxes` 永远来不及被用上——表现是"折行逻辑一点没生效"，而且不报错。
    """
    scale = dpi / 72.0
    px_w, px_h = int(res.get("width") or 0), int(res.get("height") or 0)
    page_w_pt = round(px_w / scale, 2)
    page_h_pt = round(px_h / scale, 2)

    blocks: list[dict] = []
    for blk in res.get("parsing_res_list") or []:
        label = (blk.get("block_label") or "").strip()
        content = blk.get("block_content") or ""
        bbox = blk.get("block_bbox") or []
        if len(bbox) < 4:
            continue
        geo = to_pdf_pt(bbox, scale, scale, page_h_pt)
        btype = LABEL_TO_TYPE.get(label, "text")
        entry = {
            "index": int(blk.get("block_id", len(blocks))),
            "order": blk.get("block_order"),
            # ⚠️ `block_order` **不是每块都有**：官方对 figure_title / vision_footnote /
            # image / chart / table / header / footer / footnote / aside_text 这些标签
            # 会给 None（实测 46 块里 5 块：4 个 image + 1 个 table）。
            # 早先排序写 `order if ... else 10**6`，等于把这些块**一律甩到最后**，
            # 与官方阅读顺序不符。正确做法是退回它们在 parsing_res_list 里的原始次序
            # —— 那份列表本身就是按阅读顺序排好的。
            "_raw_index": len(blocks),
            "group_id": blk.get("group_id"),
            "type": btype,
            "label": label,
            "text": content,
            **geo,
        }
        if label in HEADING_LEVEL:
            entry["level"] = HEADING_LEVEL[label]
        if label == "table":
            table = split_table_html(content)
            if table:
                entry["tables"] = [table]
                # 表格的 HTML 不能留在 `text` 里：阅读器会把它当普通文本画出来，
                # `check_layout_json.py` 也会报"仍含 HTML 占位符"。结构已经进 tables[]，
                # text 只留可读的纯文本形态。
                entry["text"] = re.sub(r"<[^>]+>", " ", content)
                entry["text"] = re.sub(r"\s+", " ", entry["text"]).strip() or "（表格）"
        if blk.get("block_polygon_points"):
            entry["polygon_px"] = blk["block_polygon_points"]
        # 图/图表块常常没有 block_content（识别结果就是"这块是图"）。
        # 留空会被结构校验器判"文本为空"，也给不出可读的占位。
        if not entry["text"].strip():
            entry["text"] = f"（{label or btype}）"
        blocks.append(entry)

    # 阅读顺序：官方给的 block_order；缺 order 的块退回**原始列表次序**
    # （parsing_res_list 本身就按阅读顺序排，不能用 10**6 甩到最后）
    blocks.sort(key=lambda b: (b["order"] if b.get("order") is not None
                               else b.get("_raw_index", 10**6),
                               b["top"], b["left"]))
    return blocks, page_w_pt, page_h_pt, scale


def page_from_result(res: dict, page_no: int, dpi: int,
                     blocks: list[dict] | None = None) -> dict:
    """一份 *_res.json → 本项目 pages[] 里的一页。

    输出**同时**给两套结构，因为它们喂的是项目里两个消费者：
      · `lines[]` / `figures[]` —— `layout_viewer/viewer_template.html` 与
        `tools/check_layout_json.py` 认的是这套（绝对定位画布 + 几何校验）；
      · `blocks[]` —— 带官方版面标签（doc_title/paragraph_title/table/…）与阅读顺序，
        给结构块消费者用。
    两者是同一批数据的两种投影，`stats.lines` 必须等于 `lines` 的总数（校验器会查）。

    `blocks` 可由调用方传入（已挂好 `_line_boxes`）；不传则现场解析（无行盒可用）。
    """
    if blocks is None:
        blocks, page_w_pt, page_h_pt, scale = parse_blocks(res, page_no, dpi)
    else:
        scale = dpi / 72.0
        px_w, px_h = int(res.get("width") or 0), int(res.get("height") or 0)
        page_w_pt = round(px_w / scale, 2)
        page_h_pt = round(px_h / scale, 2)

    # ---- lines[]：给阅读器与几何校验器 ----
    #
    # 这里做的是"块 → 视觉行"的展开。为什么必须展开：阅读器的 `.ln` 是
    # `white-space: nowrap`（原设计里"一次 OCR 调用 = 一个行盒"），而官方流程给的是
    # **整块文本**（一个块可能含 5 个视觉行 + 内联公式）。不展开就是"把整段塞进一个
    # nowrap 盒子"——实测长块会一路撑出纸外（13 处水平越界、2 处叠字）。
    #
    # 行数（`n_lines`）与每行竖直位置由**像素投影**给出：那是本项目原有、且在这份
    # PDF 上验证过的行检测（`detect_lines`）。文字层**不能**用来做这件事——实测同一
    # 块内数学坏字形的盒子与文字行错开、x 次序还会倒退。
    lines: list[dict] = []
    figures: list[dict] = []
    for i, b in enumerate(blocks):
        boxes = b.get("_line_boxes") or [b["px"]]
        n_geom = max(1, len(boxes))
        # 字号提示值：按"块高 ÷ 几何行数"估（多行块的块高是若干行之和，
        # 直接用块高当字号会得到 40~80pt 的假值）。随后由 enrich_sizes_from_pdf
        # 用文字层真实字号覆盖，再被 _fit_block_text 按可用空间收缩。
        # 字号：**优先用文字层量到的真实字号**（这份 PDF 的中文/ASCII 字符可解码），
        # 量不到才退回"块高 ÷ 几何行数"。早先一直用后者，得到的是 22pt 这种假值
        # （块高含公式撑开的高度），据此折行会把行数放大一倍以上 → 大量叠字。
        size_hint = b.get("_font_pt") or min(22.0, max(4.0, (b["h_pt"] / n_geom) * 0.82))
        size_hint = min(22.0, max(4.0, size_hint))
        # 行数上限取"文字层数出的真实视觉行数"与"块高能容纳的行数"的较大者
        geom_cap = max(1, int(b["h_pt"] / max(6.0, size_hint * LINE_PITCH_RATIO)))
        max_lines = max(b.get("_glyph_lines") or 0, geom_cap, n_geom)
        # 字号下限：真实字号的 0.8（缩到这个程度还能看，再小不如让它略溢出）
        size_floor = max(MIN_SIZE_PT, size_hint * 0.85)
        parts, size_pt, pitches = _fit_block_text(
            b["text"], size_hint, b["w_pt"] * AVAIL_WIDTH_RATIO, max_lines, size_floor)
        n_lines = max(1, len(parts))

        # 竖直排布：
        #   · 行数与投影几何行数一致 **且不含高公式** → 逐行落到真实行盒（最贴近原稿）
        #   · 否则从块顶按**逐行行距**摊开
        # 为什么含高公式时不信投影行盒：`\begin{cases}` 那行渲染高 26.1px（声明盒 20.6px），
        # 投影行盒只反映**墨迹**高度，容不下它 → 压住下一行（实测全篇就剩这一处叠字）。
        if n_lines == n_geom and not TALL_MATH_RE.search(b["text"]):
            out_boxes = boxes
        else:
            k_px = dpi / 72.0
            out_boxes = []
            y_pt = b["top"]
            for k in range(n_lines):
                pitch = pitches[k] if k < len(pitches) else pitches[-1]
                out_boxes.append([b["px"][0], int(round(y_pt * k_px)), b["px"][2],
                                  int(round((y_pt + pitch) * k_px))])
                y_pt += pitch
        for k, part in enumerate(parts):
            bx = out_boxes[min(k, len(out_boxes) - 1)]
            geo = to_pdf_pt([bx[0], bx[1], bx[2], bx[3]],
                            dpi / 72.0, dpi / 72.0, page_h_pt)
            lines.append({
                "px_top": bx[1], "px_bottom": bx[3],
                "px_left": bx[0], "px_right": bx[2],
                "top": geo["top"], "bottom": geo["bottom"],
                "left": geo["left"], "right": geo["right"],
                "height_pt": geo["h_pt"],
                "size_pt": size_pt,
                # 字号来自文字层（按块中位字符尺寸量得）还是按块高估算，如实标注
                "size_source": ("pdf-text-layer" if b.get("_font_pt")
                                else "estimated-from-block-height"),
                "size_clamped": False,
                "typical_h_pt": geo["h_pt"],
                # 折过行的必须走 `pre-wrap`：否则 nowrap 会把长文本再次撑出纸外
                "multiline": n_lines > 1,
                "text": part,
                "tables": b.get("tables", []) if k == 0 else [],
                "block_label": b["label"],
                "block_order": b["order"],
                "finish_reason": "paddleocr-vl",
                "votes": 1,
                "disagreement": False,
            })
        if b["type"] == "figure":
            figures.append({
                "index": len(figures),
                "source": f"paddleocr-vl:{b['label']}",
                "px": b["px"],
                "pt": [b["left"], b["top"], b["right"], b["bottom"]],
                "w_pt": b["w_pt"], "h_pt": b["h_pt"],
                "has_crop": False,
            })

    markdown = "\n\n".join(b["text"] for b in blocks if b["text"].strip())
    det = (res.get("layout_det_res") or {}).get("boxes") or []

    # ⚠️ `lines[]` 必须**按几何位置**自上而下排，不能沿用官方阅读顺序：
    #    阅读顺序是给"读文档"用的（可能为了语义把下面一块提到前面），而阅读器是
    #    绝对定位画布、`check_layout_json.py` 也会直接断言"行序不是自上而下"。
    #    两种排序各有用途 → blocks[] 保阅读顺序，lines[] 保几何顺序。
    lines.sort(key=lambda l: (l["top"], l["left"]))
    for i, figure in enumerate(figures):
        figure["index"] = i
    drop_lines_inside_figures(lines, figures)
    return {
        "page": page_no,
        "width": page_w_pt,
        "height": page_h_pt,
        "render_px": [px_w, px_h],
        "dpi": dpi,
        "line_source": "paddleocr-vl-parsing_res_list",
        "lines_detected": len(lines),
        "lines": lines,
        "figures": figures,
        "blocks": blocks,
        "layout_boxes": [
            {"label": bx.get("label"), "score": round(float(bx.get("score") or 0), 4),
             "pt": [round(float(v) / scale, 2) for v in (bx.get("coordinate") or [])][:4],
             "order": bx.get("order")}
            for bx in det
        ],
        "markdown": markdown,
    }


def collect_inputs(arg: str) -> list[str]:
    if os.path.isdir(arg):
        files = [os.path.join(arg, f) for f in sorted(os.listdir(arg))
                 if f.endswith("_res.json")]
        if not files:
            raise SystemExit(f"[import] 目录里没有 *_res.json：{arg}")
        return files
    if not os.path.isfile(arg):
        raise SystemExit(f"[import] 找不到文件：{arg}")
    return [arg]


def main() -> int:
    ap = argparse.ArgumentParser(description="PaddleOCR-VL 官方输出 → 本项目 JSON")
    ap.add_argument("input", help="paddleocr doc_parser 产出的 *_res.json，或其所在目录")
    ap.add_argument("--out", default=None,
                    help="输出路径（默认按引擎名推导，见 paths.layout_json_for）")
    ap.add_argument("--title", default=None)
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--source-pdf", default=None,
                    help="用于从文字层读字号；默认用项目的样例 PDF，传 '' 可关闭")
    ap.add_argument("--pages", default=None, help="只取这些页，如 1-3")
    ap.add_argument("--viewer-base", default=None,
                    help="阅读器文件名（不含 .html）；默认按引擎名推导。"
                         "写进产物，避免与 --out 起的名字错位导致页面静默 0 页")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    files = collect_inputs(args.input)
    parsed = [json.load(open(f, encoding="utf-8")) for f in files]
    # ① 先解析块（此时还没挂行盒）
    block_lists = [parse_blocks(res, i + 1, args.dpi)[0]
                   for i, res in enumerate(parsed)]

    # 字号：优先读文字层（渲染参考值，不影响文字与坐标）
    pdf = args.source_pdf
    if pdf is None:
        pdf = paths.DEFAULT_PDF if os.path.isfile(paths.DEFAULT_PDF) else ""
    font_info = {"available": False, "filled": 0, "reason": "未提供 --source-pdf"}
    crops_made = 0
    lines_attached = 0
    lines_used_font = 0
    if pdf:
        if os.path.isfile(pdf):
            # ② 挂"块内真实行盒"——**必须**在构建 lines[] 之前
            lines_attached = attach_blocks_projected_lines(block_lists, pdf, args.dpi)
        else:
            font_info = {"available": False, "filled": 0,
                         "reason": f"找不到 PDF：{pdf}"}

    # ③ 再构建页（此时 _line_boxes / _glyph_lines / _font_pt 都已就位，
    #    折行与字号才有可靠依据）
    pages = [page_from_result(res, i + 1, args.dpi, blocks=block_lists[i])
             for i, res in enumerate(parsed)]
    lines_used_font = sum(1 for p in pages for l in p["lines"]
                          if l.get("size_source") == "pdf-text-layer")

    if pdf and os.path.isfile(pdf):
        # ⚠️ 不再逐行调用 enrich_sizes_from_pdf：字号已**按块**用文字层量过
        # （attach 阶段的 `_font_pt`），逐行重设会打散"整块统一字号"，
        # 出现一行 8.36、下一行 10.45 的混排，并给宽度/行高带来新的不一致。
        font_info = {"available": True, "filled": lines_used_font,
                     "reason": ""}
        # 图块裁片：官方不给图内容，得自己按 bbox 从原页裁（阅读器靠它把图贴回去）
        crops_made = add_figure_crops(pages, pdf, args.dpi)

    if args.pages:
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", args.pages.strip())
        if not m:
            raise SystemExit(f"[import] --pages 格式不对：{args.pages!r}")
        lo = int(m.group(1))
        hi = int(m.group(2) or m.group(1))
        pages = [p for p in pages if lo <= p["page"] <= hi]
        for i, p in enumerate(pages):
            p["page"] = i + 1

    engine = "paddleocr-vl-1.6"
    # 内部用的 `_line_boxes` / `_glyph_lines` / `_font_pt` / `_raw_index` 不进产物
    # （lines[] 已把行展开，阅读顺序也已排好）
    for p in pages:
        for b in p["blocks"]:
            for k in ("_line_boxes", "_glyph_lines", "_font_pt", "_raw_index"):
                b.pop(k, None)
    # 阅读器名：显式传优先，否则按引擎推导。写进产物，让发现式的 build_viewers 用同一个名字。
    viewer_base = args.viewer_base or paths.viewer_base_for(engine)
    all_lines = [l for p in pages for l in p["lines"]]
    all_blocks = [b for p in pages for b in p["blocks"]]
    payload = {
        "document": {
            "title": args.title or "PaddleOCR-VL 解析结果",
            "source_pdf": os.path.abspath(args.input),
            "pages_total": len(pages),
            "pages_processed": len(pages),
            "engine": engine,
            "mode": "layout",
            "dpi": args.dpi,
            "page_size_pt": [pages[0]["width"], pages[0]["height"]] if pages else None,
            "pipeline": "PP-DocLayoutV3 + PaddleOCR-VL-1.6-0.9B",
            # 生成方把"阅读器名"写进产物本身：`build_viewers.py` 是发现式的，
            # 靠引擎名反推名字会与 --out 起的名字错开，导致 HTML 里的数据文件名对不上、
            # 页面静默 0 页。写进来就只有一个事实来源。
            "viewer_base": viewer_base,
            "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
            "size_source": ("pdf-text-layer" if font_info.get("filled")
                            else font_info.get("reason", "estimated-from-block-height")),
            "warnings": [] if font_info.get("filled") else [
                "字号未经文字层校正（" + str(font_info.get("reason")) + "），"
                "渲染字号为按行高估算值"],
        },
        "stats": {
            "pages": len(pages),
            # ⚠️ `lines` 必须等于 lines[] 总数（check_layout_json 会核对）；
            #    块数放在 `blocks` 里单独统计，不要拿它冒充行数。
            "lines": len(all_lines),
            "lines_with_math": sum(1 for l in all_lines
                                   if any(d in l["text"] for d in FORMULA_DELIMS)),
            "figures": sum(1 for p in pages for _ in p["figures"]),
            "blocks": len(all_blocks),
            "tables": sum(1 for b in all_blocks if b["type"] == "table"),
            "headings": sum(1 for b in all_blocks if b["type"] == "heading"),
        },
        "pages": pages,
    }

    out = args.out or paths.layout_json_for(engine)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    if not args.quiet:
        st = payload["stats"]
        print(f"[import] 写出 {out}")
        print(f"[import] {st['pages']} 页 / {st['blocks']} 块 "
              f"(标题 {st['headings']} · 表格 {st['tables']} · 图 {st['figures']} · "
              f"含公式 {st['lines_with_math']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
