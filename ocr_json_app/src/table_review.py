#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""表格复核证据层（T3）：把表格模型输出挂成证据，不改正文。

为什么单独一层：现有表格来自 PDF 原生框线 + 逐格 OCR（`pdf-table-cells`）
或 300dpi 位图（`table-grid`），两者都会在空单元格、错行、跨格合并上出错。
表格模型可以复核这些区域，但它的输出不能直接覆盖正文——模型认错时会把
本来正确的单元格改坏，而用户要的是"哪一格可疑、以谁为准"的证据。因此这里
只产证据块（html/逐格文本/覆盖率/一致性），正文 `headers`、`rows_data`、
`text` 一律不动。

与公式复核（T2）保持同一套约定：
- 证据只增不改，`reviews` 是数组，同一张表可以被多次、多个模型复核；
- 一致性分 `exact | cell_mismatch | structure_differs | no_current`；
- 映射不到现有表格的框写 `unassigned_table_boxes`，不猜归属；
- 纯 stdlib，不 import paddle / pypdfium2，便于单测与主环境调用。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

# 证据来源（写进 evidence["source"]）。表模型不是 PDF 原生层，必须可区分。
TABLE_EVIDENCE_SOURCE = "paddle-table-v2"

# 一致性口径：exact=逐格全等；cell_mismatch=结构同、有格子不同；
# structure_differs=行列数不同或合并结构不同；no_current=现有表没有可比内容。
TABLE_AGREEMENTS = ("exact", "cell_mismatch", "structure_differs", "no_current")

# 允许的裁片 dpi 下限：低于 144 时表格线会糊，逐格文本不可信（与公式证据同口径）。
MIN_CROP_DPI = 144

_SPACE_RE = re.compile(r"\s+")
# 全角括号/逗号/冒号 + 常见误识别字符统一，只为比较"是不是同一格"。
_FULLWIDTH = str.maketrans(
    {
        "（": "(",
        "）": ")",
        "，": ",",
        "：": ":",
        "；": ";",
        "　": " ",
        "－": "-",
        "—": "-",
        "．": ".",
    }
)
# 现有表格常把单元格写成 `\(90\leq x\leq100\)`，表格模型直接吐 `90≤x≤100`；
# 统一到 LaTeX 写法才能比"内容"而不是比"排版习惯"。
_UNICODE_MATH = str.maketrans(
    {
        "≤": r"\leq",
        "≥": r"\geq",
        "⩽": r"\leq",
        "⩾": r"\geq",
        "≠": r"\neq",
        "∞": r"\infty",
        "−": "-",
        "×": r"\times",
        "÷": r"\div",
        "π": r"\pi",
        "√": r"\sqrt",
        "∈": r"\in",
        "∪": r"\cup",
        "∩": r"\cap",
        "≈": r"\approx",
        "→": r"\to",
        "∠": r"\angle",
        "⊥": r"\perp",
        "∥": r"\parallel",
        "²": "^{2}",
        "³": "^{3}",
        "⁴": "^{4}",
    }
)
# 定界符与只影响渲染的包装命令，比较时一律去掉。
_DELIM_RE = re.compile(r"\\[()\[\]]|\$")
_WRAP_RE = re.compile(r"\\(?:left|right|displaystyle)(?![A-Za-z])")


class _TableHTMLParser(HTMLParser):
    """把 `<table><tr><td>..` 解析成二维行文本。

    只认 tr/td/th，忽略 tbody/colspan 细节：表格模型对合并单元格本来就用
    重复单元格表达，这里保留重复，交给一致性比较而不是猜网格。
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001 - HTMLParser 签名
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(_collapse("".join(self._cell)))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _collapse(text: str) -> str:
    return _SPACE_RE.sub(" ", (text or "").replace("\u00a0", " ")).strip()


def normalize_cell(text: str) -> str:
    """单元格比较口径：去定界符/包装命令、全角转半角、Unicode 数学符号转 LaTeX。

    只用于判断"两格是否同一内容"，不用于写回正文——保留原文才能让人核对。
    这样 `\\(90\\leqx\\leq100\\)` 与 `90≤x≤100` 会被认作同一格，而模型把
    `\\frac{16}{25}` 压成 `1625` 仍然算不同（那是真的丢结构）。
    """
    value = _collapse(text)
    value = _DELIM_RE.sub("", value)
    value = _WRAP_RE.sub("", value)
    value = value.translate(_UNICODE_MATH).translate(_FULLWIDTH)
    value = value.replace(" ", "")
    return value.strip(".,;:。，；：、")


def parse_html_table(html: str) -> list[list[str]]:
    """解析表格模型输出的 HTML 表格为二维文本（失败返回空列表）。"""
    if not html or "<t" not in html.lower():
        return []
    parser = _TableHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - 第三方模型 HTML 可能不闭合，解析失败即无结论
        return []
    return [row for row in parser.rows if any(cell for cell in row)]


def native_table_rows(table: dict) -> list[list[str]]:
    """现有表格的二维文本：`headers` 当第一行，`rows_data` 依次在后。"""
    rows: list[list[str]] = []
    headers = table.get("headers") or []
    if headers:
        rows.append([_collapse(str(cell)) for cell in headers])
    for row in table.get("rows_data") or []:
        rows.append([_collapse(str(cell)) for cell in row])
    return rows


def _grid(rows: list[list[str]]) -> tuple[int, int]:
    width = max((len(row) for row in rows), default=0)
    return len(rows), width


def compare_tables(current_rows: list[list[str]], model_rows: list[list[str]]) -> dict:
    """逐格比较两张表，返回可用于人工复核的计数与一致性。

    `matched`/`differing` 只统计两边都存在的坐标；缺格单独计
    `only_current`/`only_model`，因为"模型少认一行"和"模型认错一格"是两种
    不同的修复动作，不能混成一个数字。
    """
    current_rows = [[normalize_cell(cell) for cell in row] for row in current_rows]
    model_rows = [[normalize_cell(cell) for cell in row] for row in model_rows]
    cur_r, cur_c = _grid(current_rows)
    mod_r, mod_c = _grid(model_rows)
    matched = differing = only_current = only_model = 0
    samples: list[dict] = []
    for r in range(max(cur_r, mod_r)):
        cur_row = current_rows[r] if r < cur_r else []
        mod_row = model_rows[r] if r < mod_r else []
        for c in range(max(len(cur_row), len(mod_row))):
            cur = cur_row[c] if c < len(cur_row) else None
            mod = mod_row[c] if c < len(mod_row) else None
            if cur is None or mod is None:
                if cur:
                    only_current += 1
                if mod:
                    only_model += 1
                continue
            if cur == mod:
                matched += 1
            else:
                differing += 1
                if len(samples) < 8:
                    samples.append({"row": r, "col": c, "current": cur, "model": mod})
    total = matched + differing
    fill_current = _fill_ratio(current_rows)
    fill_model = _fill_ratio(model_rows)
    if not current_rows and not model_rows:
        agreement = "no_current"
    elif (cur_r, cur_c) != (mod_r, mod_c):
        agreement = "structure_differs"
    elif differing == 0:
        agreement = "exact"
    elif not total:
        agreement = "no_current"
    else:
        agreement = "cell_mismatch"
    return {
        "rows": {"current": cur_r, "model": mod_r},
        "cols": {"current": cur_c, "model": mod_c},
        "matched": matched,
        "differing": differing,
        "only_current": only_current,
        "only_model": only_model,
        "match_rate": round(matched / total, 4) if total else 0.0,
        "fill_rate": {"current": fill_current, "model": fill_model},
        "agreement": agreement,
        "samples": samples,
    }


def _fill_ratio(rows: list[list[str]]) -> float:
    cells = [cell for row in rows for cell in row]
    if not cells:
        return 0.0
    filled = sum(1 for cell in cells if cell)
    return round(filled / len(cells), 4)


def map_box_to_table(box_pt, tables: list[dict], min_cover: float = 0.35) -> str | None:
    """把一个检测框映射到现有表格 id：中心命中优先，其次覆盖率高者。

    返回 None 表示"这个框不属于任何现有表格"，调用方应记进
    `unassigned_table_boxes`，而不是新造一张表——坐标不是语义。
    """
    box = _as_box(box_pt)
    if box is None or not tables:
        return None
    cx = (box[0] + box[2]) / 2.0
    cy = (box[1] + box[3]) / 2.0
    for table in tables:
        bbox = _as_box(table.get("bbox_pt"))
        if bbox is None:
            continue
        if bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]:
            return table.get("id")
    best = None
    best_cover = min_cover
    area = max((box[2] - box[0]) * (box[3] - box[1]), 1e-6)
    for table in tables:
        bbox = _as_box(table.get("bbox_pt"))
        if bbox is None:
            continue
        overlap = _overlap(box, bbox)
        cover = overlap / area
        if cover >= best_cover:
            best = table.get("id")
            best_cover = cover
    return best


def _as_box(value) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        box = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    if box[2] <= box[0] or box[3] <= box[1]:
        return None
    return box


def _overlap(a: list[float], b: list[float]) -> float:
    width = min(a[2], b[2]) - max(a[0], b[0])
    height = min(a[3], b[3]) - max(a[1], b[1])
    if width <= 0 or height <= 0:
        return 0.0
    return width * height


def build_table_evidence(
    html: str,
    cells: list[dict],
    bbox_pt,
    dpi: int,
    *,
    structure_model: str | None = None,
    text_model: str | None = None,
    pipeline: str = "table_recognition_v2",
    score: float | None = None,
    current_rows: list[list[str]] | None = None,
) -> dict:
    """组装一条表格证据块；一致性来自 `current_rows` 与模型 HTML 的逐格比较。"""
    model_rows = parse_html_table(html)
    compare = compare_tables(current_rows or [], model_rows)
    reasons: list[str] = []
    if not model_rows:
        reasons.append("model_output_missing")
    elif compare["agreement"] == "structure_differs":
        reasons.append("structure_differs_from_current")
    elif compare["agreement"] == "cell_mismatch":
        reasons.append("cells_differ_from_current")
    elif compare["agreement"] == "no_current":
        reasons.append("no_current_table")
    return {
        "source": TABLE_EVIDENCE_SOURCE,
        "pipeline": pipeline,
        "models": {
            "structure": structure_model,
            "text": text_model,
        },
        "crop": {"bbox_pt": _as_box(bbox_pt), "dpi": int(dpi)},
        "html": html or "",
        "cells": cells,
        "detect_score": score,
        "compare": compare,
        "review": {"needed": bool(reasons), "reasons": reasons},
    }


def attach_table_evidence(page: dict, entries: list[dict], unassigned: list | None = None) -> dict:
    """把证据按 table id 挂到 `page["tables"][i]["reviews"]`，正文不动。"""
    attached = 0
    by_id = {table.get("id"): table for table in page.get("tables") or [] if table.get("id")}
    leftovers = list(unassigned or [])
    for entry in entries:
        table_id = entry.get("table_id")
        table = by_id.get(table_id)
        if table is None:
            residue = dict(entry)
            residue.pop("table_id", None)
            leftovers.append(residue)
            continue
        table.setdefault("reviews", []).append(entry)
        attached += 1
    if leftovers:
        page.setdefault("unassigned_table_boxes", []).extend(leftovers)
    return {"attached": attached, "unassigned": len(leftovers)}


def table_review_summary(pages: list[dict]) -> dict:
    """全卷证据汇总：多少表被复核、一致性分布、仍缺的证据。"""
    tables = 0
    reviewed = 0
    blocks = 0
    agreements: dict[str, int] = {name: 0 for name in TABLE_AGREEMENTS}
    reasons: dict[str, int] = {}
    unassigned = 0
    for page in pages:
        unassigned += len(page.get("unassigned_table_boxes") or [])
        for table in page.get("tables") or []:
            tables += 1
            reviews = table.get("reviews") or []
            if not reviews:
                continue
            reviewed += 1
            blocks += len(reviews)
            for review in reviews:
                agreement = (review.get("compare") or {}).get("agreement")
                if agreement in agreements:
                    agreements[agreement] += 1
                for reason in (review.get("review") or {}).get("reasons") or []:
                    reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "tables": tables,
        "tables_reviewed": reviewed,
        "blocks": blocks,
        "agreement": agreements,
        "review_reasons": reasons,
        "unassigned_boxes": unassigned,
    }


def validate_table_evidence(pages: list[dict]) -> list[dict]:
    """结构校验：证据块自身必须自洽，否则下游会拿错数字下结论。

    没有 `reviews` 的历史产物返回空列表——与区域层、公式证据的"只对含证据
    产物硬校验"约定一致。
    """
    problems: list[dict] = []
    for page in pages:
        page_no = page.get("page")
        for table in page.get("tables") or []:
            reviews = table.get("reviews")
            if reviews is None:
                continue
            table_id = table.get("id")
            if not isinstance(reviews, list):
                problems.append(_problem(page_no, table_id, "reviews_not_list", "reviews 必须是数组"))
                continue
            for review in reviews:
                if not isinstance(review, dict):
                    problems.append(_problem(page_no, table_id, "review_not_object", "证据必须是对象"))
                    continue
                if review.get("source") != TABLE_EVIDENCE_SOURCE:
                    problems.append(
                        _problem(page_no, table_id, "review_source", "证据来源必须是 %s" % TABLE_EVIDENCE_SOURCE)
                    )
                if not isinstance(review.get("html"), str) or not review.get("html"):
                    problems.append(_problem(page_no, table_id, "review_html_missing", "证据缺少 html"))
                crop = review.get("crop") or {}
                if _as_box(crop.get("bbox_pt")) is None:
                    problems.append(_problem(page_no, table_id, "review_bbox", "裁片 bbox_pt 非法"))
                dpi = crop.get("dpi")
                if not isinstance(dpi, int) or dpi < MIN_CROP_DPI:
                    problems.append(_problem(page_no, table_id, "review_dpi", "裁片 dpi 非法"))
                compare = review.get("compare") or {}
                if compare.get("agreement") not in TABLE_AGREEMENTS:
                    problems.append(_problem(page_no, table_id, "review_agreement", "一致性取值非法"))
                if not isinstance(compare.get("matched"), int) or not isinstance(compare.get("differing"), int):
                    problems.append(_problem(page_no, table_id, "review_counts", "逐格计数缺失"))
                for cell in review.get("cells") or []:
                    if _as_box(cell.get("bbox_pt")) is None:
                        problems.append(_problem(page_no, table_id, "review_cell_bbox", "单元格坐标非法"))
                        break
    return problems


def _problem(page_no, table_id, code: str, message: str) -> dict:
    return {"page": page_no, "table_id": table_id, "code": code, "message": message}
