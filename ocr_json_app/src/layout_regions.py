#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""layout_regions.py —— 把 layout.json 的页面证据组织成一等「区域」（T1）。

为什么需要区域层
----------------
`layout.json` 平时是三份平行数组：`lines[]`（OCR / 原生字层出来的物理行）、
`figures[]`（矢量图 / 位图 / 覆盖兜底裁片）、`tables[]`（PDF 框线 + 逐格 OCR）。
它们的亲缘关系只存在于隐含约定里——下游 `exam.json` / `quality.json`、将来的公式专项
（按区域重渲高分辨率裁片）都只能靠坐标猜「这块墨迹属于哪一行、是正文还是公式」。

区域层把「一块可独立处理的版面证据」收成一个带稳定 id 的对象：:

    {id, page, bbox_pt, content_type, source, confidence, text, spans[], refs{}}

行 / 图 / 表都能通过 `refs` 回链，反向也能从对象上拿到 `region_id`。它**只增不改**：
`lines[] / figures[] / tables[]` 的原字段一个不动；旧产物没有 `regions` 时用
`ensure_regions()` 在内存里就地派生（不写回磁盘上的历史 JSON）。

不变量（`tools/check_layout_json.py` 逐条校验，见 docs/specs/t1-layout-regions.md）
-------------------------------------------------------------------------------
1. region id 形如 `p{page}-r{n}`，页内唯一；同一输入重复构建结果一致；
2. `bbox_pt = [left, top, right, bottom]` 四项有限、不倒置、落在页内；
3. `content_type ∈ REGION_CONTENT_TYPES`，`source ∈ REGION_SOURCES`；
4. 每个 region 至少有一个 ref，且每个 ref 都指向本页真实存在的行 / 图 / 表；
5. 行 / 图 / 表上的 `region_id` 与 region.refs 双向一致；
6. `confidence ∈ [0, 1]`，它是**粗粒度路由置信度**，不是概率校准值。

为什么 confidence 不是评测指标：它的唯一用途是给 T2/T6 这类「只复核可疑区域」的流程
排优先级。真正的准确率仍要人工标注（NEXT_SESSION §4 / §8），不能拿它充当公式正确率。

用法
----
::

    from layout_regions import build_regions, ensure_regions, index_regions
    page["regions"] = build_regions(page, page_no)    # 版式流水线生成时
    ensure_regions(layout)                            # 旧产物就地补齐（幂等）
    idx = index_regions(layout["pages"])              # id -> region id 反查

本模块只依赖标准库：版式流水线与 `exam_structure` 共用它，不能反过来依赖流水线
（见 AGENTS.md 第 6 条 import 分层）。
"""
from __future__ import annotations

from typing import Any

# 区域的语义类型。先只做规则化判定，不引入版面模型：公式看定界符 / 公式字符，
# 表格看原生框线，图看原生图形资源，其余算正文。
REGION_CONTENT_TYPES = ("text", "formula", "table", "figure")

# 区域证据来源。名字直接对应「这块内容怎么来的」，便于质量报告里区分
# 原生字层直出 / OCR / PDF 原生图形 / 覆盖兜底，不能混用。
REGION_SOURCES = (
    "native",           # 原生文字层直出（line.text_source == "native-text"）
    "ocr",              # 逐行 OCR
    "pdf-vector",       # PDF 原生矢量图形（插图）
    "pdf-image",        # PDF 内嵌位图（插图）
    "pdf-table",        # PDF 原生框线表格（逐格 OCR 填充）
    "coverage-region",  # 覆盖审计发现的未覆盖墨迹区（兜底插图）
    "table-grid",       # 无原生框线、由像素投影 / 框线游程发现的表格
)

# span 的来源沿用同一套枚举：一个 region 里将来可能既有 native 也有 ocr 的 span
# （例如原生字层行里混进一个 OCR 补出来的公式片段），带来源才能决定要不要复核。
SPAN_PROVENANCE = REGION_SOURCES

# 覆盖兜底区域的可信度低于原生图形：它只是"这里还有墨迹没被解释"，边界靠墨迹聚合。
_CONF_FIGURE = {
    "pdf-vector": 1.0,
    "pdf-image": 1.0,
    "table-grid": 0.9,
    "coverage-region": 0.7,
}

# 与 `_looks_math_text()` 同类的最小符号集：只用于**没有 content_type 的历史产物**
# 兜底，新产物一律以版式流水线写好的 content_type 为准（避免两套判据打架）。
_MATH_MARKERS = ("$", "\\(", "\\[")
_MATH_SYMBOLS = set("√≤≥≠≈≡≌∽∈∉⊂⊃⊆⊇∪∩∞∑∏∫∮°′″·±×÷⊥∥∠△▲□■○●◇◆→←↔⇔⇒⇐")
_SUPERSCRIPTS = set("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ")


def _is_finite(value: Any) -> bool:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return False
    return num == num and num not in (float("inf"), float("-inf"))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    return num if _is_finite(num) else default


def _clamp01(value: Any, default: float = 0.0) -> float:
    num = _num(value, default)
    if num < 0.0:
        return 0.0
    if num > 1.0:
        return 1.0
    return num


def valid_bbox(value: Any) -> list[float] | None:
    """规范化 `[left, top, right, bottom]`；不合法返回 None。

    只做结构性校验（四项有限、不倒置），不判断是否在页内——页尺寸属于页面对象，
    由调用方（流水线写入前 / `check_layout_json`）结合 width、height 判断。
    """
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    if not all(_is_finite(v) for v in value):
        return None
    x0, y0, x1, y1 = (round(float(v), 2) for v in value)
    if x1 < x0 or y1 < y0:
        return None
    return [x0, y0, x1, y1]


def _line_bbox(line: dict) -> list[float] | None:
    if not isinstance(line, dict):
        return None
    return valid_bbox([line.get("left"), line.get("top"),
                       line.get("right"), line.get("bottom")])


def _looks_math(text: str) -> bool:
    """历史产物兜底：公式字符 / 上下标 / 希腊字母命中即算公式。

    比流水线里的 `_looks_math_text()` 更宽松（不数密度），仅用于给缺 content_type
    的老 JSON 猜类型；新产物不会走这里。
    """
    if not text:
        return False
    if any(ch in _MATH_SYMBOLS or ch in _SUPERSCRIPTS for ch in text):
        return True
    for ch in text:
        code = ord(ch)
        if 0x0370 <= code <= 0x03FF:      # 希腊字母
            return True
        if 0x2200 <= code <= 0x22FF:      # 运算符
            return True
    return False


def _text_content_type(text: Any, has_tables: bool = False) -> str:
    if has_tables:
        return "table"
    raw = str(text or "")
    if any(mark in raw for mark in _MATH_MARKERS):
        return "formula"
    return "formula" if _looks_math(raw) else "text"


def _line_source(line: dict) -> str:
    return "native" if str(line.get("text_source") or "") == "native-text" else "ocr"


def _line_confidence(line: dict, source: str) -> float:
    """行区域的粗粒度置信度（见模块注释 6）。

    优先级：原生字层匹配相似度 > 多票一致 > 多票分歧 > 单票。
    `--vote 1` 的默认值不是基准（AGENTS.md 已知陷阱），所以单票只给 0.6。
    """
    if source == "native":
        if line.get("native_similarity") is not None:
            return round(_clamp01(line.get("native_similarity")), 3)
        return 0.95
    votes = _num(line.get("votes"), 1.0)
    if votes >= 2:
        return 0.7 if line.get("disagreement") else 0.9
    return 0.6


def _line_span(line: dict, bbox: list[float], source: str) -> dict:
    """行级单 span：T1 不拆字，字符级切片留给 T2 / build_char_level（不在此模块）。"""
    span: dict[str, Any] = {
        "bbox_pt": list(bbox),
        "text": str(line.get("text") or ""),
        "provenance": source,
    }
    style = line.get("style") or {}
    if isinstance(style, dict):
        if style.get("font_family"):
            span["font"] = str(style["font_family"])
        if style.get("bold") is not None:
            span["bold"] = bool(style["bold"])
        if style.get("italic") is not None:
            span["italic"] = bool(style["italic"])
        if style.get("color"):
            span["color"] = str(style["color"])
    if line.get("size_pt") is not None and _is_finite(line.get("size_pt")):
        span["size"] = round(_num(line.get("size_pt")), 2)
    return span


def figure_source(fig: dict) -> str:
    """把 `fig["source"]` 规范到 REGION_SOURCES。

    实测取值：`pdf-vector`、`pdf-vector+labels`（吸收标注后）、`pdf-image`、
    `table-grid`、`coverage-region`，合并时可能是 `"+".join(sources)`。
    解析时按枚举顺序取第一个命中的来源；未知来源归为 `coverage-region`
    （它本来就是"墨迹兜底"的语义，比谎称矢量图更接近事实）。
    """
    raw = str((fig or {}).get("source") or "").strip()
    for part in raw.split("+"):
        part = part.strip()
        if part in REGION_SOURCES:
            return part
    if raw.startswith("pdf-vector"):
        return "pdf-vector"
    if raw.startswith("pdf-image"):
        return "pdf-image"
    return "coverage-region"


def _figure_bbox(fig: dict) -> list[float] | None:
    """图盒用 `pt`（点）而不是 `px`（渲染像素）：区域坐标一律是 pt。"""
    if not isinstance(fig, dict):
        return None
    return valid_bbox(fig.get("pt"))


def build_regions(page: dict, page_no: int) -> list[dict]:
    """从一页的 `lines / figures / tables` 派生区域数组。

    分配顺序固定：正文行（含"被表格占位的那一行"）→ 无归属行的表格 → 图。
    这样同一页重复构建得到同样的 id；表格行不会同时拥有"行区域"和"表格区域"
    （占位行 `（表格）` 归表格区域所有，双向链接唯一）。
    会就地给行 / 图 / 表补 `region_id`（缺行 id 的老产物还会按流水线约定补 `p{p}-l{n}`），
    这些字段都是**新增**，不改任何既有值。
    """
    page = page if isinstance(page, dict) else {}
    regions: list[dict] = []

    def add(region: dict) -> dict:
        region["id"] = "p{}-r{}".format(page_no, len(regions) + 1)
        regions.append(region)
        return region

    def base_region(bbox: list[float], content_type: str, source: str,
                    confidence: float, text: str, spans: list[dict],
                    refs: dict) -> dict:
        return {
            "page": page_no,
            "bbox_pt": bbox,
            "content_type": content_type,
            "source": source,
            "confidence": round(_clamp01(confidence), 3),
            "text": text,
            "spans": spans,
            "refs": refs,
        }

    lines = [ln for ln in (page.get("lines") or []) if isinstance(ln, dict)]
    tables = [tb for tb in (page.get("tables") or []) if isinstance(tb, dict)]
    figures = [fg for fg in (page.get("figures") or []) if isinstance(fg, dict)]

    # 表格占位行：table_by_line 让某一行代表整张表；它属于表格区域，不单独立行区域。
    table_owner: dict[int, dict] = {}
    for table in tables:
        for line in lines:
            if any(t is table for t in (line.get("tables") or [])):
                table_owner[id(table)] = line
                break

    line_of_region: dict[str, dict] = {}
    for idx, line in enumerate(lines, start=1):
        bbox = _line_bbox(line)
        if bbox is None:
            continue
        line_id = str(line.get("id") or "p{}-l{}".format(page_no, idx))
        line["id"] = line_id
        owner_table = None
        for table in tables:
            owner = table_owner.get(id(table))
            if owner is line:
                owner_table = table
                break
        if owner_table is not None and id(owner_table) in table_owner:
            source = ("pdf-table"
                      if str(owner_table.get("source") or "") == "pdf-table-cells"
                      else "table-grid")
            region = add(base_region(
                bbox, "table", source,
                0.8 if owner_table.get("text_available") else 0.5,
                str(line.get("text") or ""),
                [_line_span(line, bbox, "ocr")],
                {"line_ids": [line_id], "figure_id": None,
                 "table_id": str(owner_table.get("id") or "")},
            ))
            owner_table["region_id"] = region["id"]
            line["region_id"] = region["id"]
            line_of_region[line_id] = line
            continue

        has_tables = bool(line.get("tables"))
        content_type = str(line.get("content_type") or "").strip()
        if content_type not in REGION_CONTENT_TYPES:
            content_type = _text_content_type(line.get("text"), has_tables)
        source = _line_source(line)
        region = add(base_region(
            bbox, content_type, source, _line_confidence(line, source),
            str(line.get("text") or ""),
            [_line_span(line, bbox, source)],
            {"line_ids": [line_id], "figure_id": None, "table_id": None},
        ))
        line["region_id"] = region["id"]
        line_of_region[line_id] = line

    # 没有归属行的表格（正常流水线不会出现，历史产物可能有）单独成区域。
    for idx, table in enumerate(tables, start=1):
        if table.get("region_id"):
            continue
        bbox = valid_bbox(table.get("bbox_pt"))
        if bbox is None:
            continue
        table_id = str(table.get("id") or "p{}-t{}".format(page_no, idx))
        table["id"] = table_id
        source = ("pdf-table"
                  if str(table.get("source") or "") == "pdf-table-cells"
                  else "table-grid")
        region = add(base_region(
            bbox, "table", source,
            0.8 if table.get("text_available") else 0.5, "", [],
            {"line_ids": [], "figure_id": None, "table_id": table_id},
        ))
        table["region_id"] = region["id"]

    for idx, fig in enumerate(figures, start=1):
        bbox = _figure_bbox(fig)
        if bbox is None:
            continue
        fig_id = str(fig.get("id") or "p{}-f{}".format(page_no, idx))
        fig["id"] = fig_id
        source = figure_source(fig)
        content_type = str(fig.get("content_type") or "").strip()
        if content_type not in ("table", "figure"):
            content_type = "table" if source == "table-grid" else "figure"
        confidence = _CONF_FIGURE.get(source, 0.7)
        region = add(base_region(
            bbox, content_type, source, confidence, "", [],
            {"line_ids": [], "figure_id": fig_id, "table_id": None},
        ))
        fig["region_id"] = region["id"]

    return regions


def _relink(page: dict, regions: list[dict]) -> None:
    """已有 regions 时只补反向链接，不重排 id、不改 region 内容。"""
    for region in regions:
        if not isinstance(region, dict):
            continue
        refs = region.get("refs") or {}
        rid = region.get("id")
        for line in page.get("lines") or []:
            if (isinstance(line, dict) and line.get("id")
                    and line.get("id") in (refs.get("line_ids") or [])
                    and not line.get("region_id")):
                line["region_id"] = rid
        for fig in page.get("figures") or []:
            if (isinstance(fig, dict) and fig.get("id") == refs.get("figure_id")
                    and not fig.get("region_id")):
                fig["region_id"] = rid
        for table in page.get("tables") or []:
            if (isinstance(table, dict) and table.get("id") == refs.get("table_id")
                    and not table.get("region_id")):
                table["region_id"] = rid


def ensure_regions(layout: dict) -> int:
    """给缺 `regions` 的页就地派生区域，返回**新增**的区域数（幂等）。

    旧产物（`schema_version` 缺失或早于区域层）不需要重跑 OCR 就能拿到区域视图；
    已有区域数组的页只补缺失的反向链接，绝不重排 / 覆盖已有区域。
    """
    added = 0
    if not isinstance(layout, dict):
        return 0
    for page_no, page in enumerate(layout.get("pages") or [], start=1):
        if not isinstance(page, dict):
            continue
        regions = page.get("regions")
        if isinstance(regions, list) and regions:
            _relink(page, regions)
            continue
        page["regions"] = build_regions(page, page_no)
        added += len(page["regions"])
    return added


def index_regions(pages: list[dict] | None) -> dict[str, dict[str, str]]:
    """行 / 图 / 表 id → region id 的反查表（每个对象只归第一个声明它的区域）。"""
    lines: dict[str, str] = {}
    figures: dict[str, str] = {}
    tables: dict[str, str] = {}
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        for region in page.get("regions") or []:
            if not isinstance(region, dict):
                continue
            rid = str(region.get("id") or "")
            refs = region.get("refs") or {}
            for line_id in refs.get("line_ids") or []:
                line_id = str(line_id or "")
                if line_id and line_id not in lines:
                    lines[line_id] = rid
            figure_id = str(refs.get("figure_id") or "")
            if figure_id and figure_id not in figures:
                figures[figure_id] = rid
            table_id = str(refs.get("table_id") or "")
            if table_id and table_id not in tables:
                tables[table_id] = rid
    return {"lines": lines, "figures": figures, "tables": tables}


def region_stats(pages: list[dict] | None) -> dict:
    """区域计数（按类型 / 来源），给 document 级汇总与验收报告用。"""
    by_type = {name: 0 for name in REGION_CONTENT_TYPES}
    by_source = {name: 0 for name in REGION_SOURCES}
    count = 0
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        for region in page.get("regions") or []:
            if not isinstance(region, dict):
                continue
            count += 1
            if region.get("content_type") in by_type:
                by_type[region["content_type"]] += 1
            if region.get("source") in by_source:
                by_source[region["source"]] += 1
    return {"count": count, "by_content_type": by_type, "by_source": by_source}


def validate_regions(pages: list[dict] | None) -> list[dict]:
    """校验区域层的不变量，返回问题列表 `{page, region_id, code, message}`。

    调用前应先 `ensure_regions(layout)`：本函数不替你补数据，只判断事实。
    旧产物没有 `regions` 时返回空列表（"存在才校验"，与 check_layout_json 一致）。
    """
    problems: list[dict] = []
    seen_ids: set[str] = set()

    def problem(page_no: Any, region_id: str, code: str, message: str) -> None:
        problems.append({"page": page_no, "region_id": region_id,
                         "code": code, "message": message})

    for page_no, page in enumerate(pages or [], start=1):
        if not isinstance(page, dict):
            continue
        regions = page.get("regions")
        if not isinstance(regions, list):
            continue
        line_ids: set[str] = set()
        for line in page.get("lines") or []:
            if isinstance(line, dict) and line.get("id"):
                line_ids.add(str(line["id"]))
        figure_ids = {str(f.get("id")) for f in (page.get("figures") or [])
                      if isinstance(f, dict) and f.get("id")}
        table_ids = {str(t.get("id")) for t in (page.get("tables") or [])
                     if isinstance(t, dict) and t.get("id")}
        width = _num(page.get("width"), 0.0)
        height = _num(page.get("height"), 0.0)
        region_ids: set[str] = set()

        for region in regions:
            if not isinstance(region, dict):
                problem(page_no, "", "region_not_object",
                        "regions[] 里有非对象元素")
                continue
            rid = str(region.get("id") or "")
            if rid in seen_ids:
                problem(page_no, rid, "region_id_duplicate",
                        "region id 重复：{}".format(rid))
            seen_ids.add(rid)
            region_ids.add(rid)
            if not rid.startswith("p{}-r".format(page_no)) or rid in ("", "p{}-r".format(page_no)):
                problem(page_no, rid, "region_id_format",
                        "region id 应为 p{}-rN，实际 {!r}".format(page_no, rid))
            if region.get("page") != page_no:
                problem(page_no, rid, "region_page_mismatch",
                        "region.page={!r} 与所在页 {} 不一致".format(
                            region.get("page"), page_no))
            bbox = valid_bbox(region.get("bbox_pt"))
            if bbox is None:
                problem(page_no, rid, "region_bbox_invalid",
                        "region bbox_pt 不是合法的 [l, t, r, b]")
            else:
                x0, y0, x1, y1 = bbox
                if width and height and (x0 < -2 or y0 < -2
                                         or x1 > width + 2 or y1 > height + 2):
                    problem(page_no, rid, "region_bbox_outside_page",
                            "region bbox {} 超出页 {}x{}".format(bbox, width, height))
            if region.get("content_type") not in REGION_CONTENT_TYPES:
                problem(page_no, rid, "region_content_type",
                        "content_type={!r} 不在 {}".format(
                            region.get("content_type"), REGION_CONTENT_TYPES))
            if region.get("source") not in REGION_SOURCES:
                problem(page_no, rid, "region_source",
                        "source={!r} 不在 {}".format(
                            region.get("source"), REGION_SOURCES))
            confidence = region.get("confidence")
            if confidence is not None and not (0.0 <= _num(confidence, -1.0) <= 1.0):
                problem(page_no, rid, "region_confidence",
                        "confidence={!r} 不在 [0, 1]".format(confidence))
            if not isinstance(region.get("text"), str):
                problem(page_no, rid, "region_text_type", "text 必须是字符串")
            spans = region.get("spans")
            if not isinstance(spans, list):
                problem(page_no, rid, "region_spans_type", "spans 必须是数组")
                spans = []
            for si, span in enumerate(spans):
                if not isinstance(span, dict):
                    problem(page_no, rid, "region_span_not_object",
                            "spans[{}] 不是对象".format(si))
                    continue
                if valid_bbox(span.get("bbox_pt")) is None:
                    problem(page_no, rid, "region_span_bbox",
                            "spans[{}].bbox_pt 非法".format(si))
                if not isinstance(span.get("text"), str):
                    problem(page_no, rid, "region_span_text",
                            "spans[{}].text 必须是字符串".format(si))
                if span.get("provenance") not in SPAN_PROVENANCE:
                    problem(page_no, rid, "region_span_provenance",
                            "spans[{}].provenance={!r} 不在枚举内".format(
                                si, span.get("provenance")))
                if "size" in span and not (3.0 <= _num(span.get("size"), -1.0) <= 40.0):
                    problem(page_no, rid, "region_span_size",
                            "spans[{}].size={!r} 不在 3–40pt".format(
                                si, span.get("size")))
            refs = region.get("refs")
            if not isinstance(refs, dict):
                problem(page_no, rid, "region_refs_type", "refs 必须是对象")
                refs = {}
            ref_line_ids = [str(v) for v in (refs.get("line_ids") or [])]
            ref_figure = str(refs.get("figure_id") or "")
            ref_table = str(refs.get("table_id") or "")
            if not ref_line_ids and not ref_figure and not ref_table:
                problem(page_no, rid, "region_unreferenced",
                        "region 既没有行也没有图/表引用")
            for line_id in ref_line_ids:
                if line_id not in line_ids:
                    problem(page_no, rid, "region_line_ref_missing",
                            "refs.line_ids 指向本页不存在的行 {!r}".format(line_id))
            if ref_figure and ref_figure not in figure_ids:
                problem(page_no, rid, "region_figure_ref_missing",
                        "refs.figure_id={!r} 不在本页 figures[]".format(ref_figure))
            if ref_table and ref_table not in table_ids:
                problem(page_no, rid, "region_table_ref_missing",
                        "refs.table_id={!r} 不在本页 tables[]".format(ref_table))

        # 反向链接：行 / 图 / 表上的 region_id 必须指回本页真实区域。
        for line in page.get("lines") or []:
            if not isinstance(line, dict):
                continue
            rid = line.get("region_id")
            if rid is not None and rid not in region_ids:
                problem(page_no, str(line.get("id") or ""), "line_region_missing",
                        "行 {!r} 的 region_id={!r} 不在本页区域里".format(
                            line.get("id"), rid))
        for fig in page.get("figures") or []:
            if not isinstance(fig, dict):
                continue
            rid = fig.get("region_id")
            if rid is not None and rid not in region_ids:
                problem(page_no, str(fig.get("id") or ""), "figure_region_missing",
                        "图 {!r} 的 region_id={!r} 不在本页区域里".format(
                            fig.get("id"), rid))
        for table in page.get("tables") or []:
            if not isinstance(table, dict):
                continue
            rid = table.get("region_id")
            if rid is not None and rid not in region_ids:
                problem(page_no, str(table.get("id") or ""), "table_region_missing",
                        "表 {!r} 的 region_id={!r} 不在本页区域里".format(
                            table.get("id"), rid))

    return problems
