#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Math exam structure: layout JSON -> exam.json / quality.json.

This module deliberately consumes the *layout* artifact rather than raw OCR
output.  Layout is the only layer that knows where a line sits on the page, so
it is also the only layer that can recover reading order across 2/3-column
papers.  The older block pipeline joined lines in a JSON array and therefore
lost that information as soon as a paper used more than one column.

The public API is pure (no file writes, no OCR):

    build_exam(layout, source=...) -> dict
    build_quality(layout, exam)    -> dict

Stable IDs are assigned from the page number and the line's position in the
layout array; downstream consumers must reference those IDs, never indexes.
"""
from __future__ import annotations

import re
from typing import Any

try:  # The module also runs from src/ directly in smoke tests.
    from ocr_pipeline_blocks import is_section_heading
    from formula_quality import audit_formulas
    from repair_plan import build_repair_plan
    from layout_regions import (ensure_regions, index_regions, region_stats,
                                validate_regions)
except Exception:  # pragma: no cover - fallback only when imported standalone
    def is_section_heading(text: str) -> bool:  # type: ignore
        return bool(re.match(r"^\s*[一二三四五六七八九十]{1,3}\s*[、．.]\s*\S", text))

    def audit_formulas(lines, engine=None, page_no=None):  # type: ignore
        return {"count": 0, "lines": 0, "broken": 0, "suspicious": 0,
                "samples": [], "ok": True}

    def build_repair_plan(issues):  # type: ignore
        return {"actions": [], "counts": {"auto_candidate": 0, "manual": 0, "blocked": 0},
                "total": 0, "auto_executed": False, "note": "fallback"}

    def ensure_regions(layout):  # type: ignore
        return 0

    def index_regions(pages):  # type: ignore
        return {"lines": {}, "figures": {}, "tables": {}}

    def region_stats(pages):  # type: ignore
        return {"count": 0, "by_content_type": {}, "by_source": {}}

    def validate_regions(pages):  # type: ignore
        return []


SECTION_RE = re.compile(r"^\s*([一二三四五六七八九十]{1,3})\s*[、．.]\s*(\S.*)$")
# `(?!\d)` is load-bearing: statistics rows such as `15.2 18.8 ...` would
# otherwise be read as question 15 plus the text `2 18.8 ...`.
QUESTION_RE = re.compile(r"^\s*(\d{1,3})\s*[.、．)）](?!\d)\s*(?=\S)")
SUBQ_RE = re.compile(r"^\s*[（(]\s*(\d{1,2})\s*[)）]\s*(.*)$")
OPTION_RE = re.compile(
    r"(?:^|(?<=\s))(?:(?:[（(]\s*([A-D])\s*[)）])|([A-D])\s*[.．])\s*")
PAGE_NUMBER_RE = re.compile(r"^\s*(?:第\s*)?\d{1,4}\s*(?:页)?\s*$")
PAGE_FOOTER_RE = re.compile(r"第\s*\d+\s*页|共\s*\d+\s*页")

# A stem that ends on one of these is almost certainly a truncated line.  The
# list is intentionally short: content heuristics are warnings/errors only when
# they are strong enough to survive without a language model.
DANGLING_STEM_SUFFIXES = (
    "则", "求", "若", "已知", "设", "得", "且", "及", "与", "或",
    "的", "是", "有", "由", "当", "将", "把", "使", "对于", "证明",
    "解", "计算",
)


def _text(line: dict) -> str:
    return (line.get("text") or "").strip()


def _line_id(page_no: int, index: int, line: dict) -> str:
    return line.get("id") or f"p{page_no}-l{index + 1}"


def _is_page_footer(text: str) -> bool:
    if not text:
        return False
    if PAGE_FOOTER_RE.search(text):
        return True
    return bool(PAGE_NUMBER_RE.fullmatch(text))


def _infer_columns(lines: list[dict], page_width: float) -> list[float]:
    """Infer column anchor x positions from line left edges.

    This is the fallback for layout files produced before columns were stored.
    A cluster must contain at least three lines; otherwise an isolated title or
    page number would be mistaken for a column.
    """
    lefts = sorted(round(float(l.get("left") or 0), 1) for l in lines if _text(l))
    if not lefts:
        return [0.0]
    clusters: list[list[float]] = [[lefts[0]]]
    gap_limit = max(32.0, float(page_width or 0) * 0.055)
    for x in lefts[1:]:
        if x - clusters[-1][-1] <= gap_limit:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    anchors = [sum(c) / len(c) for c in clusters if len(c) >= 3]
    if len(anchors) < 2:
        return [0.0]
    return anchors


def _page_anchors(page: dict) -> list[float]:
    cols = page.get("columns") or []
    if isinstance(cols, list) and len(cols) >= 1:
        anchors = []
        for col in cols:
            if isinstance(col, dict) and "left" in col:
                anchors.append(float(col["left"]))
            elif isinstance(col, (list, tuple)) and col:
                anchors.append(float(col[0]))
        if len(anchors) >= 1:
            return anchors
    return _infer_columns(page.get("lines") or [], float(page.get("width") or 0))


def _column_index(line: dict, anchors: list[float]) -> int:
    if len(anchors) <= 1:
        return 0
    center = float(line.get("left") or 0)
    return min(range(len(anchors)), key=lambda i: abs(center - anchors[i]))


def reading_lines(page: dict) -> list[dict]:
    """Return lines in column-major reading order (fallback: top-left)."""
    lines = list(page.get("lines") or [])
    if not lines:
        return []
    if all("reading_index" in l for l in lines):
        return sorted(lines, key=lambda l: int(l.get("reading_index") or 0))
    anchors = _page_anchors(page)
    return sorted(lines, key=lambda l: (
        _column_index(l, anchors),
        float(l.get("top") or 0),
        float(l.get("left") or 0),
    ))


def _split_options(text: str) -> list[tuple[str, str]]:
    matches = list(OPTION_RE.finditer(text))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((m.group(1) or m.group(2),
                    text[m.end():end].strip()))
    return out


def _is_choice_title(title: str) -> bool:
    return any(key in (title or "") for key in ("选择", "单选", "多选"))


def _add_option(question: dict, label: str, body: str, ref: str) -> dict:
    # 同一题出现第二个 (B) 通常是 OCR 把上一题/栏缝碎片带进来；
    # 用后出现的完整选项覆盖，而不是留下两个 B 让选项数变成 5。
    for opt in question["options"]:
        if opt.get("label") == label:
            opt["text"] = body
            if ref not in (opt.get("refs") or []):
                opt.setdefault("refs", []).append(ref)
            return opt
    opt = {
        "id": f"{question['id']}-opt-{label}",
        "label": label,
        "text": body,
        "refs": [ref],
    }
    question["options"].append(opt)
    return opt


def _section_from(text: str) -> tuple[str, str] | None:
    if not is_section_heading(text):
        return None
    m = SECTION_RE.match(text)
    if not m:
        return None
    return m.group(1), m.group(2).strip()


def _new_question(qid: str, number: int, section_id: str | None,
                  page_no: int) -> dict:
    return {
        "id": qid,
        "number": number,
        "section_id": section_id,
        "pages": [page_no],
        "stem": [],
        "options": [],
        "subquestions": [],
        "figures": [],
        "tables": [],
        "cross_page": False,
    }


def _append_text(bucket: list[dict], text: str, page_no: int, ref: str) -> None:
    bucket.append({"text": text, "page": page_no, "ref": ref})


def build_exam(layout: dict, source: str | None = None) -> dict:
    pages = layout.get("pages") or []
    # 区域层（T1）：新产物由版式流水线写入；旧产物在这里就地派生（幂等，不改磁盘）。
    # 之后所有回链都走区域 id，题目不再只能靠行 id 猜归属。
    ensure_regions(layout)
    region_of = index_regions(pages)
    doc = layout.get("document") or {}

    sections: list[dict] = []
    questions: list[dict] = []
    front_matter: list[dict] = []
    current_section: dict | None = None
    current_q: dict | None = None
    current_option: dict | None = None
    current_sub: dict | None = None
    q_seq = 0
    sec_seq = 0
    attached_refs: set[str] = set()
    # Numbered front matter (instructions) must not become questions when the
    # paper uses section headings.  Papers without section headings still use
    # numbered questions, so only suppress unscoped numbers when sections exist.
    has_sections = any(
        _section_from(_text(line))
        for page in pages
        for line in reading_lines(page)
    )

    for page in pages:
        page_no = int(page.get("page") or 1)
        # IDs are anchored to the layout array, not the reading order: a
        # consumer resolving `ref` must get the same line the layout showed.
        line_ids = {
            id(line): _line_id(page_no, idx, line)
            for idx, line in enumerate(page.get("lines") or [])
        }
        for line in reading_lines(page):
            text = _text(line)
            if not text or _is_page_footer(text):
                continue
            ref = line_ids[id(line)]
            sec = _section_from(text)
            qm = QUESTION_RE.match(text)
            if qm:
                number = int(qm.group(1))
                previous = int(questions[-1]["number"]) if questions else None
                # OCR sometimes prefixes a continuation fragment with a bogus
                # large number (`68. 则这个数列...`).  Real papers number
                # monotonically with small gaps; a jump of more than 5 is
                # treated as noise, and the prefix is dropped so the line can
                # continue the current question instead of creating Q68.
                plausible = (number <= 30 if previous is None
                             else number <= previous or number - previous <= 5)
                if not plausible:
                    text = text[qm.end():].strip()
                    qm = None
                # Same number immediately after the current question is also
                # OCR noise, not a second Q3: real duplicate numbers are a
                # quality error, but here the line is the continuation of the
                # stem (`3. \overrightarrow{CB} = ...`).  Strip the prefix and
                # let the normal continuation path append it.
                if (qm is not None and current_q is not None
                        and number == int(current_q["number"])):
                    text = text[qm.end():].strip()
                    qm = None
            if sec:
                sec_seq += 1
                current_section = {
                    "id": f"sec-{sec_seq:02d}",
                    "label": sec[0],
                    "title": sec[1],
                    "pages": [page_no],
                    "question_ids": [],
                }
                sections.append(current_section)
                current_q = None
                current_option = None
                current_sub = None
                attached_refs.add(ref)
                continue
            if qm and (current_section is not None or not has_sections):
                q_seq += 1
                current_q = _new_question(
                    f"q-{q_seq:03d}", int(qm.group(1)),
                    current_section["id"] if current_section else None, page_no)
                questions.append(current_q)
                if current_section is not None:
                    current_section["question_ids"].append(current_q["id"])
                rest = text[qm.end():].strip()
                current_option = None
                if rest:
                    in_choice = (current_section is None
                                 or _is_choice_title(current_section["title"]))
                    inline = _split_options(rest) if in_choice else []
                    if inline:
                        # 选项和题干同在一行：先保留 `(A)` 之前的题干，
                        # 再逐项挂到 options，不能把整行当续行。
                        first = OPTION_RE.search(rest)
                        head = rest[:first.start()].strip() if first else ""
                        if head:
                            _append_text(current_q["stem"], head, page_no, ref)
                        for label, body in inline:
                            current_option = _add_option(
                                current_q, label, body, ref)
                    else:
                        _append_text(current_q["stem"], rest, page_no, ref)
                attached_refs.add(ref)
                current_sub = None
                continue
            if current_q is None:
                front_matter.append({
                    "text": text,
                    "page": page_no,
                    "ref": ref,
                    "reason": "before_first_question",
                })
                attached_refs.add(ref)
                continue

            if page_no not in current_q["pages"]:
                current_q["pages"].append(page_no)
            in_choice_section = (current_section is None
                                 or _is_choice_title(current_section["title"]))
            # 解答题正文里的“交点为 D.”也会命中选项正则；非选择题不解析
            # 选项，否则 Q19 的 D 会被当成一个选项，把 Q20/Q21 全吞进去。
            opts = _split_options(text) if in_choice_section else []
            if opts:
                for label, body in opts:
                    current_option = _add_option(
                        current_q, label, body, ref)
                current_sub = None
                attached_refs.add(ref)
                continue
            sm = SUBQ_RE.match(text)
            if sm and current_option is None:
                sub = {
                    "label": f"({sm.group(1)})",
                    "text": sm.group(2).strip(),
                    "refs": [ref],
                }
                current_q["subquestions"].append(sub)
                current_sub = sub
                attached_refs.add(ref)
                continue

            # Continuation: attach to the most specific active bucket.
            if current_sub is not None:
                current_sub["text"] = (current_sub["text"] + "\n" + text).strip()
                current_sub["refs"].append(ref)
            elif current_option is not None:
                current_option["text"] = (current_option["text"] + "\n" + text).strip()
                current_option["refs"].append(ref)
            else:
                _append_text(current_q["stem"], text, page_no, ref)
            attached_refs.add(ref)

    for q in questions:
        q["cross_page"] = len(q["pages"]) > 1
        q["stem_text"] = "\n".join(x["text"] for x in q["stem"]).strip()

    # Figure/table ownership: nearest question on the same page, measured to
    # the question's own line boxes.  This is intentionally conservative:
    # a figure that cannot be placed within 80pt of any question stays
    # unassigned and is reported by quality.json.
    line_lookup: dict[str, dict] = {}
    page_lines: dict[int, list[dict]] = {}
    for page in pages:
        pno = int(page.get("page") or 1)
        page_lines[pno] = page.get("lines") or []
        for idx, line in enumerate(page_lines[pno]):
            line_lookup[_line_id(pno, idx, line)] = line

    unassigned_figures: list[dict] = []
    for page in pages:
        pno = int(page.get("page") or 1)
        for fig in page.get("figures") or []:
            pt = fig.get("pt") or [0, 0, 0, 0]
            fx0, fy0, fx1, fy1 = (float(pt[0]), float(pt[1]),
                                  float(pt[2]), float(pt[3]))
            fcx = (fx0 + fx1) / 2
            best: tuple[float, dict | None] = (1e9, None)
            for q in questions:
                if pno not in q["pages"]:
                    continue
                refs = []
                for bucket in (q["stem"], q["options"], q["subquestions"]):
                    for item in bucket:
                        for ref in ([item.get("ref")] if item.get("ref") else []) + list(item.get("refs") or []):
                            if ref:
                                refs.append(ref)
                for ref in refs:
                    line = line_lookup.get(ref)
                    if line is None:
                        continue
                    ly0 = float(line.get("top") or 0)
                    ly1 = float(line.get("bottom") or 0)
                    if ly1 < fy0:
                        vdist = fy0 - ly1
                    elif ly0 > fy1:
                        vdist = ly0 - fy1
                    else:
                        vdist = 0.0
                    lx0 = float(line.get("left") or 0)
                    lx1 = float(line.get("right") or 0)
                    if lx1 < fx0:
                        hdist = fx0 - lx1
                    elif lx0 > fx1:
                        hdist = lx0 - fx1
                    else:
                        hdist = 0.0
                    dist = vdist + hdist
                    if dist < best[0]:
                        best = (dist, q)
            fref = fig.get("id") or f"p{pno}-f{len(unassigned_figures) + 1}"
            item = {
                "ref": fref,
                "page": pno,
                "bbox_pt": [round(float(v), 2) for v in pt],
                "source": fig.get("source"),
                "distance_pt": round(best[0], 2) if best[1] is not None else None,
            }
            if best[1] is not None and best[0] <= 80:
                best[1]["figures"].append(item)
            else:
                unassigned_figures.append(item)

    # 图形选项常排成 2×2：第一行离题近，第二行与第一行只隔十几 pt。
    # 直接从已归属图向上/向下传播，避免第二行被当成“无主图”。
    def _adjacent_panel(a: dict, b: dict) -> bool:
        if a.get("page") != b.get("page"):
            return False
        ax0, ay0, ax1, ay1 = (float(v) for v in a["bbox_pt"])
        bx0, by0, bx1, by1 = (float(v) for v in b["bbox_pt"])
        overlap = min(ax1, bx1) - max(ax0, bx0)
        width = min(ax1 - ax0, bx1 - bx0)
        if width <= 0 or overlap / width < 0.6:
            return False
        gap = by0 - ay1 if by0 >= ay1 else ay0 - by1
        return -2.0 <= gap <= 30.0

    for q in questions:
        if not q["figures"]:
            continue
        for _ in range(8):
            found = [item for item in unassigned_figures
                     if item["page"] in q["pages"]
                     and any(_adjacent_panel(item, owned) for owned in q["figures"])]
            if not found:
                break
            q["figures"].extend(found)
            for item in found:
                unassigned_figures.remove(item)

    section_titles = {s["id"]: s.get("title") or "" for s in sections}
    for q in questions:
        title = section_titles.get(q.get("section_id"), "")
        # 图形选项卷会把 A/B/C/D 标签和图分开 OCR：选项被解析出来但正文为空，
        # 四个图各自挂在题上。此时不是“空选项”，而是图形选项。
        has_option_text = any((o.get("text") or "").strip() for o in q["options"])
        graphics = len(q.get("figures") or []) + len(q.get("tables") or [])
        empty_options = sum(1 for o in q["options"] if not (o.get("text") or "").strip())
        stem_text = q.get("stem_text") or ""
        graphical_stem = any(k in stem_text for k in ("图", "图象", "图像", "大致"))
        q["option_mode"] = (
            "graphical"
            if _is_choice_title(title)
            and graphics >= 2
            and (
                not has_option_text
                or (graphics >= 4 and empty_options >= 2 and graphical_stem)
                # 部分选项是图/表（如 2023 新高考 1 Q12：A/B 文字，C/D 是图），
                # 文字选项只有 2 个，不能当成缺选项。
                or (len(q["options"]) < 4 and graphics >= 2)
            )
            else "text"
        )

    # Table cells already parsed by layout belong to the line that carried them.
    for page in pages:
        pno = int(page.get("page") or 1)
        for idx, line in enumerate(page.get("lines") or []):
            for t_idx, table in enumerate(line.get("tables") or [], start=1):
                # 原生 pdfplumber 表格由 page["tables"] 统一归属，避免同一张表
                # 既挂在行上又挂在页上。旧产物没有 page["tables"]，仍走这里。
                if table.get("source") == "pdf-table-cells":
                    continue
                ref = _line_id(pno, idx, line)
                owner = next((q for q in questions
                              if any(r == ref
                                     for b in (q["stem"], q["options"], q["subquestions"])
                                     for item in b
                                     for r in ([item.get("ref")] if item.get("ref") else []) + list(item.get("refs") or []))),
                             None)
                item = {
                    "ref": f"{ref}-t{t_idx}",
                    "page": pno,
                    "line_ref": ref,
                    "headers": table.get("headers") or [],
                    "rows": table.get("rows") or [],
                    "source": table.get("source"),
                }
                if owner is not None:
                    owner["tables"].append(item)
                else:
                    unassigned_figures.append({"ref": item["ref"], "page": pno,
                                               "reason": "table_without_question"})

    # pdfplumber 原生表格：行已被裁片吸收时，行级 table 不存在；按 bbox 归属。
    unassigned_tables: list[dict] = []
    for page in pages:
        pno = int(page.get("page") or 1)
        for t_idx, table in enumerate(page.get("tables") or [], start=1):
            if table.get("source") != "pdf-table-cells":
                continue
            box = table.get("bbox_pt") or [0, 0, 0, 0]
            tx0, ty0, tx1, ty1 = (float(box[0]), float(box[1]),
                                  float(box[2]), float(box[3]))
            best: tuple[float, dict | None] = (1e9, None)
            for q in questions:
                if pno not in q["pages"]:
                    continue
                refs = []
                for bucket in (q["stem"], q["options"], q["subquestions"]):
                    for item in bucket:
                        for ref in ([item.get("ref")] if item.get("ref") else []) + list(item.get("refs") or []):
                            if ref:
                                refs.append(ref)
                for ref in refs:
                    line = line_lookup.get(ref)
                    if line is None:
                        continue
                    ly0 = float(line.get("top") or 0)
                    ly1 = float(line.get("bottom") or 0)
                    vdist = (ty0 - ly1 if ly1 < ty0 else
                             ly0 - ty1 if ly0 > ty1 else 0.0)
                    lx0 = float(line.get("left") or 0)
                    lx1 = float(line.get("right") or 0)
                    hdist = (tx0 - lx1 if lx1 < tx0 else
                             lx0 - tx1 if lx0 > tx1 else 0.0)
                    dist = vdist + hdist
                    if dist < best[0]:
                        best = (dist, q)
            item = {
                "ref": f"p{pno}-t{t_idx}",
                "page": pno,
                "bbox_pt": [round(float(v), 2) for v in box],
                "headers": table.get("headers") or [],
                "rows": table.get("rows_data") or table.get("rows") or [],
                "rows_count": table.get("rows"),
                "cols_count": table.get("cols"),
                "source": table.get("source"),
                "distance_pt": round(best[0], 2) if best[1] is not None else None,
            }
            if best[1] is not None and best[0] <= 80:
                best[1]["tables"].append(item)
            else:
                unassigned_tables.append(item)

    # 表格归属在 option_mode 初判之后才完成；这里用最终图/表数重算一次，
    # 避免“A/B 文字 + C/D 图”的题被当成缺选项。
    for q in questions:
        title = section_titles.get(q.get("section_id"), "")
        graphics = len(q.get("figures") or []) + len(q.get("tables") or [])
        empty_options = sum(1 for o in q["options"] if not (o.get("text") or "").strip())
        has_text = any((o.get("text") or "").strip() for o in q["options"])
        if (_is_choice_title(title) and graphics >= 2
                and (not has_text or len(q["options"]) < 4
                     or (graphics >= 4 and empty_options >= 2))):
            q["option_mode"] = "graphical"

    # 区域回链（T1）：行 refs → 区域 id。题目、选项、小问、卷首说明都能指到区域，
    # 图/表 item 直接记 region_id；将来 T2/T6 只重跑某个区域时不必重新解析整卷。
    def _region_refs(refs) -> list[str]:
        out: list[str] = []
        for ref in refs or []:
            rid = region_of["lines"].get(str(ref or ""))
            if rid and rid not in out:
                out.append(rid)
        return out

    for item in front_matter:
        item["region_refs"] = _region_refs([item.get("ref")])
    for q in questions:
        refs: list[str] = [str(item.get("ref") or "")
                           for item in q.get("stem") or []]
        for opt in q.get("options") or []:
            opt["region_refs"] = _region_refs(opt.get("refs"))
            refs.extend(str(r or "") for r in opt.get("refs") or [])
        for sub in q.get("subquestions") or []:
            sub["region_refs"] = _region_refs(sub.get("refs"))
            refs.extend(str(r or "") for r in sub.get("refs") or [])
        q["region_refs"] = _region_refs(refs)
        for fig in q.get("figures") or []:
            rid = region_of["figures"].get(str(fig.get("ref") or ""))
            if rid:
                fig["region_id"] = rid
        for table in q.get("tables") or []:
            # pdf-table-cells 表的 ref 就是 layout 里的 table id；行内表用 line_ref 找
            # 占位行所属的（表格）区域，两条路径都不冒充"行区域"。
            rid = (region_of["tables"].get(str(table.get("ref") or ""))
                   or region_of["lines"].get(str(table.get("line_ref") or "")))
            if rid:
                table["region_id"] = rid

    for fig in unassigned_figures:
        rid = region_of["figures"].get(str(fig.get("ref") or ""))
        if rid:
            fig["region_id"] = rid
    for table in unassigned_tables:
        rid = region_of["tables"].get(str(table.get("ref") or ""))
        if rid:
            table["region_id"] = rid

    return {
        "schema_version": 1,
        "document": {
            "title": doc.get("title") or "未命名试卷",
            "source_pdf": doc.get("source_pdf"),
            "source_layout": source,
            "subject": "数学",
            "pages": len(pages),
            "page_size_pt": doc.get("page_size_pt"),
            "engine": doc.get("engine"),
            "generated_at": doc.get("generated_at"),
        },
        "sections": sections,
        "questions": questions,
        "front_matter": front_matter,
        "unassigned": {
            "figures": unassigned_figures,
            "tables": unassigned_tables,
            "lines": [],
        },
        "stats": {
            "sections": len(sections),
            "questions": len(questions),
            "front_matter_lines": len(front_matter),
            "figures_assigned": sum(len(q["figures"]) for q in questions),
            "figures_unassigned": len(unassigned_figures),
            "tables_assigned": sum(len(q["tables"]) for q in questions),
            "tables_unassigned": len(unassigned_tables),
            "questions_cross_page": sum(1 for q in questions if q["cross_page"]),
            "options": sum(len(q["options"]) for q in questions),
            "subquestions": sum(len(q["subquestions"]) for q in questions),
            "regions": region_stats(pages)["count"],
            "question_region_refs": sum(len(q.get("region_refs") or [])
                                        for q in questions),
        },
    }


def _stem_truncation_reason(text: str, allow_fill_blank: bool = False) -> str | None:
    body = (text or "").strip()
    if not body:
        return "题干为空"
    if body[-1] in "=+-×÷*/<（(【[":
        return f"以 {body[-1]!r} 结尾"
    for suffix in DANGLING_STEM_SUFFIXES:
        if body.endswith(suffix):
            # 填空题的作答横线通常没有墨迹/字符，题干合法地停在“是/为”后面。
            if allow_fill_blank and suffix in ("是", "为"):
                continue
            return f"以“{suffix}”结尾"
    audit = audit_formulas([{"text": body}])
    if int(audit.get("broken") or 0):
        return "公式括号/定界符不完整"
    return None


def _issue(level: str, code: str, message: str, *, page: int | None = None,
           ref: str | None = None) -> dict:
    item: dict[str, Any] = {"level": level, "code": code, "message": message}
    if page is not None:
        item["page"] = page
    if ref:
        item["ref"] = ref
    return item


def build_quality(layout: dict, exam: dict) -> dict:
    pages = layout.get("pages") or []
    # 旧产物也要能出区域诊断：先补齐（幂等），再校验不变量。
    ensure_regions(layout)
    questions = exam.get("questions") or []
    issues: list[dict] = []

    numbers = [int(q.get("number") or 0) for q in questions]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    expected = set(range(1, (max(numbers) if numbers else 0) + 1))
    missing = sorted(expected - set(numbers))
    if missing:
        issues.append(_issue("error", "question_number_gap",
                             f"题号不连续，缺 {missing}"))
    if duplicates:
        issues.append(_issue("error", "question_number_duplicate",
                             f"题号重复：{duplicates}"))

    truncated_stems = 0
    truncated_samples: list[dict] = []
    for q in questions:
        title = ""
        sec = next((s for s in exam.get("sections") or []
                    if s.get("id") == q.get("section_id")), None)
        if sec:
            title = sec.get("title") or ""
        stem = (q.get("stem_text") or "").strip()
        if not stem:
            issues.append(_issue("error", "empty_stem",
                                 f"第 {q.get('number')} 题没有题干", ref=q.get("id")))
        elif not _is_choice_title(title):
            # Choice stems legitimately end in "则"/"若" before their options;
            # only non-choice stems use the dangling-suffix heuristic.
            # 填空题的作答横线通常没有墨迹，题干合法地停在“是/为”后面。
            reason = _stem_truncation_reason(stem, allow_fill_blank="填空" in title)
            if reason:
                truncated_stems += 1
                truncated_samples.append({"number": q.get("number"),
                                          "reason": reason,
                                          "tail": stem[-80:]})
                issues.append(_issue(
                    "error", "question_content_truncated",
                    f"第 {q.get('number')} 题题干疑似截断：{reason}",
                    ref=q.get("id")))
        if _is_choice_title(title):
            options = q.get("options") or []
            if q.get("option_mode") == "graphical":
                issues.append(_issue(
                    "warning", "choice_options_graphical",
                    f"第 {q.get('number')} 题的选项是图形（已归属 "
                    f"{len(q.get('figures') or [])} 张图），不做文字选项计数",
                    ref=q.get("id")))
                continue
            if len(options) < 4:
                issues.append(_issue("error", "choice_options_incomplete",
                                     f"第 {q.get('number')} 题（{title}）只有 "
                                     f"{len(options)} 个选项", ref=q.get("id")))
            labels = [o.get("label") for o in options]
            if labels and labels != ["A", "B", "C", "D"][:len(labels)]:
                issues.append(_issue("warning", "choice_labels_unexpected",
                                     f"第 {q.get('number')} 题选项标签为 {labels}",
                                     ref=q.get("id")))
            if any(not (o.get("text") or "").strip() for o in options):
                issues.append(_issue("error", "choice_option_empty",
                                     f"第 {q.get('number')} 题有空选项",
                                     ref=q.get("id")))

    empty_sections = [s for s in exam.get("sections") or []
                      if not (s.get("question_ids") or [])]
    for sec in empty_sections:
        issues.append(_issue("error", "section_empty",
                             f"大题“{sec.get('label')}、{sec.get('title')}”没有题目",
                             page=(sec.get("pages") or [None])[0]))

    unassigned_figs = (exam.get("unassigned") or {}).get("figures") or []
    for fig in unassigned_figs:
        issues.append(_issue("warning", "figure_unassigned",
                             f"图 {fig.get('ref')} 未归属到题目",
                             page=fig.get("page"), ref=fig.get("ref")))

    # 表格：原生 pdfplumber 框只给几何，文字仍来自逐单元格 OCR；空表头/空行
    # 不能静默通过，必须让质量门看见。
    table_total = 0
    for q in questions:
        for table in q.get("tables") or []:
            table_total += 1
            headers = table.get("headers") or []
            rows = table.get("rows") or []
            if not headers:
                issues.append(_issue("warning", "table_header_empty",
                                     f"第 {q.get('number')} 题表格没有表头",
                                     page=table.get("page"), ref=table.get("ref")))
            if not rows:
                issues.append(_issue("warning", "table_rows_empty",
                                     f"第 {q.get('number')} 题表格没有数据行",
                                     page=table.get("page"), ref=table.get("ref")))
            if any(not str(cell).strip() for row in [headers] + rows
                   for cell in row):
                issues.append(_issue("warning", "table_empty_cell",
                                     f"第 {q.get('number')} 题表格有空单元格"
                                     f"（{table.get('rows_count')}x{table.get('cols_count')}）",
                                     page=table.get("page"), ref=table.get("ref")))
    for table in (exam.get("unassigned") or {}).get("tables") or []:
        issues.append(_issue("warning", "table_unassigned",
                             f"表 {table.get('ref')} 未归属到题目",
                             page=table.get("page"), ref=table.get("ref")))

    # Geometry: horizontal overflow and same-column vertical overlaps.  These
    # are warnings because a legitimate multi-line title or a tall table cell
    # can look like an overlap at line-box level.
    overflow = 0
    overlaps = 0
    for page in pages:
        pno = int(page.get("page") or 1)
        width = float(page.get("width") or 0)
        height = float(page.get("height") or 0)
        by_col: dict[int, list[dict]] = {}
        anchors = _page_anchors(page)
        for line in page.get("lines") or []:
            left = float(line.get("left") or 0)
            right = float(line.get("right") or 0)
            top = float(line.get("top") or 0)
            bottom = float(line.get("bottom") or 0)
            if left < -2 or right > width + 2 or top < -2 or bottom > height + 2:
                overflow += 1
                issues.append(_issue("warning", "line_outside_page",
                                     f"行越出页面：{line.get('id')}",
                                     page=pno, ref=line.get("id")))
            col = (int(line["column"]) if "column" in line
                   else _column_index(line, anchors))
            by_col.setdefault(col, []).append(line)
        for col_lines in by_col.values():
            ordered = sorted(col_lines, key=lambda l: float(l.get("top") or 0))
            for a, b in zip(ordered, ordered[1:]):
                if float(b.get("top") or 0) < float(a.get("bottom") or 0) - 0.5:
                    overlaps += 1
                    issues.append(_issue("warning", "line_overlap",
                                         f"同栏行重叠：{a.get('id')} / {b.get('id')}",
                                         page=pno, ref=a.get("id")))

    # 页面覆盖率：行/图盒之外仍有成片墨迹，说明可能有漏行、未识别插图或旋转页。
    # 这是 warning 而不是 error：噪点/装饰线也可能触发；但必须在 quality 里可见，
    # 不能像旧版一样“页面看起来正常就当作没漏”。
    coverage_ratios: list[float] = []
    uncovered_total = 0
    for page in pages:
        pno = int(page.get("page") or 1)
        cov = page.get("coverage") or {}
        ratio = cov.get("ratio")
        if isinstance(ratio, (int, float)):
            coverage_ratios.append(float(ratio))
        regions = cov.get("uncovered_regions") or []
        if regions:
            uncovered_total += int(cov.get("uncovered_regions_total") or len(regions))
            issues.append(_issue(
                "warning", "page_uncovered_ink",
                f"第 {pno} 页有 {len(regions)} 处未覆盖墨迹"
                f"（覆盖率 {float(ratio or 0):.2%}），可能漏行/漏图",
                page=pno))
    coverage_ratio = min(coverage_ratios) if coverage_ratios else 1.0

    # 公式结构审计：先做纯 Python 的定界符/括号/环境检查，KaTeX 编译由
    # verify/verify_math.mjs 在内容层单独做。两者都不能少：前者能在生成时
    # 立即定位行，后者才证明真的可渲染。
    engine = (layout.get("document") or {}).get("engine")
    formula_total = 0
    formula_lines = 0
    formula_broken = 0
    formula_suspicious = 0
    formula_samples: list[dict[str, Any]] = []
    for page in pages:
        pno = int(page.get("page") or 1)
        audit = audit_formulas(reading_lines(page), engine=engine, page_no=pno)
        formula_total += int(audit.get("count") or 0)
        formula_lines += int(audit.get("lines") or 0)
        formula_broken += int(audit.get("broken") or 0)
        formula_suspicious += int(audit.get("suspicious") or 0)
        formula_samples.extend(audit.get("samples") or [])
    if formula_broken:
        issues.append(_issue(
            "error", "formula_structure_broken",
            f"{formula_broken} 条公式括号/定界符/环境不平衡，无法可靠渲染"))
    if formula_suspicious:
        issues.append(_issue(
            "warning", "formula_suspicious",
            f"{formula_suspicious} 条公式含可疑字符或超长，需要人工抽查"))

    # 表格单元格里的公式也要过同一道结构门；逐格 OCR 可能把 \(...\) 截断，
    # 不能让表格成为公式审计的盲区。
    table_formula_broken = 0
    for q in questions:
        for table in q.get("tables") or []:
            for row in [table.get("headers") or []] + list(table.get("rows") or []):
                for cell in row:
                    audit = audit_formulas([{"text": cell}], engine=engine)
                    table_formula_broken += int(audit.get("broken") or 0)
    if table_formula_broken:
        issues.append(_issue("error", "table_formula_broken",
                             f"{table_formula_broken} 个表格单元格公式结构损坏"))

    stats = exam.get("stats") or {}
    native = (layout.get("document") or {}).get("native_text") or {}
    if native.get("mismatched_lines") and native.get("matched_lines"):
        ratio = native["mismatched_lines"] / max(1, native["matched_lines"])
        if ratio > 0.35:
            issues.append(_issue("warning", "native_ocr_mismatch",
                                 f"原生文字层与 OCR 不一致率 {ratio:.0%}"))

    font_audit = (layout.get("document") or {}).get("font_audit") or {}
    garbled_lines = int(font_audit.get("garbled_lines") or 0)
    if not garbled_lines:
        for page in pages:
            for line in page.get("lines") or []:
                text = str(line.get("text") or "")
                if ("(cid:" in text or "\ufffd" in text
                        or any(ord(c) < 32 and c not in "\n\t" for c in text)):
                    garbled_lines += 1
    if garbled_lines:
        issues.append(_issue(
            "error", "garbled_text",
            f"{garbled_lines} 行含 (cid:)/替换符/控制符，不能当作正文"))

    # 区域层（T1）一致性：只做 warning，不新增硬门——区域是"证据视图"，
    # 它坏掉说明回链不可信，但不代表 OCR/结构结果错；新产物由
    # check_layout_json 以 error 级校验，避免区域层静默退化。
    region_problems = validate_regions(pages)
    region_ids = {str(r.get("id")) for page in pages
                  for r in (page.get("regions") or []) if isinstance(r, dict)}
    region_refs_unresolved = sum(
        1 for q in questions
        for rid in (q.get("region_refs") or []) if str(rid) not in region_ids)
    region_types = region_stats(pages)["by_content_type"]
    if region_problems:
        codes = sorted({p["code"] for p in region_problems})
        issues.append(_issue(
            "warning", "region_inconsistent",
            f"{len(region_problems)} 处区域层不一致：{', '.join(codes[:6])}"))
    if region_refs_unresolved:
        issues.append(_issue(
            "warning", "region_ref_unresolved",
            f"{region_refs_unresolved} 个 region_refs 指向不存在的区域"))

    errors = sum(1 for i in issues if i["level"] == "error")
    warnings = sum(1 for i in issues if i["level"] == "warning")
    return {
        "schema_version": 1,
        "document": exam.get("document"),
        "summary": {
            "status": "fail" if errors else ("warn" if warnings else "pass"),
            "errors": errors,
            "warnings": warnings,
            "questions": stats.get("questions", len(questions)),
            "sections": stats.get("sections", len(exam.get("sections") or [])),
            "figures_assigned": stats.get("figures_assigned", 0),
            "figures_unassigned": stats.get("figures_unassigned", 0),
            "missing_question_numbers": missing,
            "duplicate_question_numbers": duplicates,
            "content_truncated": truncated_stems,
            "content_truncated_samples": truncated_samples[:10],
            "empty_sections": len(empty_sections),
            "line_overflow": overflow,
            "line_overlaps": overlaps,
            "coverage_ratio": round(coverage_ratio, 5),
            "uncovered_regions": uncovered_total,
            "formula_count": formula_total,
            "formula_lines": formula_lines,
            "formula_broken": formula_broken,
            "formula_suspicious": formula_suspicious,
            "formula_samples": formula_samples[:20],
            "tables": table_total,
            "tables_unassigned": len((exam.get("unassigned") or {}).get("tables") or []),
            "garbled_lines": garbled_lines,
            "font_count": int(font_audit.get("font_count") or 0),
            "regions": sum(region_types.values()),
            "region_types": region_types,
            "region_problems": len(region_problems),
            "region_refs_unresolved": region_refs_unresolved,
        },
        "checks": {
            "question_numbers_complete": not missing,
            "question_numbers_unique": not duplicates,
            "all_questions_have_stem": all((q.get("stem_text") or "").strip()
                                           for q in questions),
            "all_questions_have_options": all(
                len(q.get("options") or []) >= 2
                for q in questions
                if "选择" in next((s.get("title") or "" for s in exam.get("sections") or []
                                   if s.get("id") == q.get("section_id")), "")),
            "content_consistency_ok": truncated_stems == 0 and not empty_sections,
            "text_clean_ok": garbled_lines == 0,
            "all_figures_assigned": not unassigned_figs,
            "all_tables_structured": all(
                (t.get("headers") or []) and (t.get("rows") or [])
                for q in questions for t in (q.get("tables") or [])),
            "geometry_ok": overflow == 0 and overlaps == 0,
            "page_coverage_ok": uncovered_total == 0,
            "formula_structure_ok": formula_broken == 0,
            "regions_consistent": not region_problems and not region_refs_unresolved,
        },
        "repair": build_repair_plan(issues),
        "issues": issues,
    }
