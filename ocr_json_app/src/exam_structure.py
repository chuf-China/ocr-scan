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
except Exception:  # pragma: no cover - fallback only when imported standalone
    def is_section_heading(text: str) -> bool:  # type: ignore
        return bool(re.match(r"^\s*[一二三四五六七八九十]{1,3}\s*[、．.]\s*\S", text))

    def audit_formulas(lines, engine=None, page_no=None):  # type: ignore
        return {"count": 0, "lines": 0, "broken": 0, "suspicious": 0,
                "samples": [], "ok": True}


SECTION_RE = re.compile(r"^\s*([一二三四五六七八九十]{1,3})\s*[、．.]\s*(\S.*)$")
# `(?!\d)` is load-bearing: statistics rows such as `15.2 18.8 ...` would
# otherwise be read as question 15 plus the text `2 18.8 ...`.
QUESTION_RE = re.compile(r"^\s*(\d{1,3})\s*[.、．)）](?!\d)\s*(?=\S)")
SUBQ_RE = re.compile(r"^\s*[（(]\s*(\d{1,2})\s*[)）]\s*(.*)$")
OPTION_RE = re.compile(r"[（(]\s*([A-D])\s*[)）]")
PAGE_NUMBER_RE = re.compile(r"^\s*(?:第\s*)?\d{1,4}\s*(?:页)?\s*$")
PAGE_FOOTER_RE = re.compile(r"第\s*\d+\s*页|共\s*\d+\s*页")


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
        out.append((m.group(1), text[m.end():end].strip()))
    return out


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
            if qm:
                q_seq += 1
                current_q = _new_question(
                    f"q-{q_seq:03d}", int(qm.group(1)),
                    current_section["id"] if current_section else None, page_no)
                questions.append(current_q)
                if current_section is not None:
                    current_section["question_ids"].append(current_q["id"])
                rest = text[qm.end():].strip()
                if rest:
                    _append_text(current_q["stem"], rest, page_no, ref)
                attached_refs.add(ref)
                current_option = None
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
            opts = _split_options(text)
            if opts:
                for label, body in opts:
                    opt = {
                        "id": f"{current_q['id']}-opt-{label}",
                        "label": label,
                        "text": body,
                        "refs": [ref],
                    }
                    current_q["options"].append(opt)
                    current_option = opt
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

    # Table cells already parsed by layout belong to the line that carried them.
    for page in pages:
        pno = int(page.get("page") or 1)
        for idx, line in enumerate(page.get("lines") or []):
            for t_idx, table in enumerate(line.get("tables") or [], start=1):
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
            "lines": [],
        },
        "stats": {
            "sections": len(sections),
            "questions": len(questions),
            "front_matter_lines": len(front_matter),
            "figures_assigned": sum(len(q["figures"]) for q in questions),
            "figures_unassigned": len(unassigned_figures),
            "questions_cross_page": sum(1 for q in questions if q["cross_page"]),
            "options": sum(len(q["options"]) for q in questions),
            "subquestions": sum(len(q["subquestions"]) for q in questions),
        },
    }


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

    for q in questions:
        if not (q.get("stem_text") or "").strip():
            issues.append(_issue("error", "empty_stem",
                                 f"第 {q.get('number')} 题没有题干", ref=q.get("id")))
        title = ""
        sec = next((s for s in exam.get("sections") or []
                    if s.get("id") == q.get("section_id")), None)
        if sec:
            title = sec.get("title") or ""
        if "选择" in title:
            options = q.get("options") or []
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

    unassigned_figs = (exam.get("unassigned") or {}).get("figures") or []
    for fig in unassigned_figs:
        issues.append(_issue("warning", "figure_unassigned",
                             f"图 {fig.get('ref')} 未归属到题目",
                             page=fig.get("page"), ref=fig.get("ref")))

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

    stats = exam.get("stats") or {}
    native = (layout.get("document") or {}).get("native_text") or {}
    if native.get("mismatched_lines") and native.get("matched_lines"):
        ratio = native["mismatched_lines"] / max(1, native["matched_lines"])
        if ratio > 0.35:
            issues.append(_issue("warning", "native_ocr_mismatch",
                                 f"原生文字层与 OCR 不一致率 {ratio:.0%}"))

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
            "line_overflow": overflow,
            "line_overlaps": overlaps,
            "coverage_ratio": round(coverage_ratio, 5),
            "uncovered_regions": uncovered_total,
            "formula_count": formula_total,
            "formula_lines": formula_lines,
            "formula_broken": formula_broken,
            "formula_suspicious": formula_suspicious,
            "formula_samples": formula_samples[:20],
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
            "all_figures_assigned": not unassigned_figs,
            "geometry_ok": overflow == 0 and overlaps == 0,
            "page_coverage_ok": uncovered_total == 0,
            "formula_structure_ok": formula_broken == 0,
        },
        "issues": issues,
    }
