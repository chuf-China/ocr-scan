#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把质量门的问题翻译成可审计的修复队列（S5）。

这里只做“分类和路由”，不改正文、不猜缺失内容：

- auto_candidate：证据足够、动作幂等、失败可回退，允许调用方重试
  （目前只有空表格单元格的逐格重识别）；
- manual：需要人看原图/上下文，自动补字会制造幻觉，必须保留原样；
- blocked：没有可执行的修复动作，只能回到输入或人工处理。

`quality.json` 里的 `repair` 字段就是这份队列。修复动作的执行由调用方决定，
本模块不依赖 OCR 服务，保证纯函数、可测试。
"""
from __future__ import annotations

from typing import Any

# issue.code -> (action, mode, reason)
_ACTIONS: dict[str, tuple[str, str, str]] = {
    "table_empty_cell": (
        "reocr_table_cell", "auto_candidate",
        "空单元格通常是裁切/超时导致，可只重跑该格 OCR；不改变表格结构"),
    "table_empty_header": (
        "manual_table_header", "manual", "表头缺失不能猜，需对照原图补"),
    "table_unassigned": (
        "attach_table", "manual", "表格归属需要人工确认题号"),
    "figure_unassigned": (
        "attach_figure", "manual", "插图归属需要人工确认题号"),
    "page_uncovered_ink": (
        "review_uncovered_region", "manual",
        "未覆盖墨迹可能是插图标注/表格框线/漏行，需看原图定位"),
    "native_ocr_mismatch": (
        "review_native_text", "manual",
        "原生字层与 OCR 不一致，数学公式以 OCR 为准，人工抽检"),
    "choice_options_incomplete": (
        "review_question_region", "manual",
        "选项缺失可能是裁切/换行，不能自动补选项"),
    "question_content_truncated": (
        "review_question_tail", "manual",
        "题干疑似截断，不能自动补字"),
    "question_number_duplicate": (
        "review_question_numbers", "manual", "题号重复需人工判断哪一个是真分节"),
    "question_number_missing": (
        "review_question_numbers", "manual", "缺号可能是漏行，也可能是试卷本身跳号"),
    "formula_structure_broken": (
        "review_formula", "manual", "公式结构错误不能自动改写"),
    "section_empty": (
        "review_section", "manual", "空大题需确认标题是否误识别或题目是否漏行"),
    "line_overflow": (
        "review_line_box", "manual", "越界行需要回到原图确认裁切"),
    "line_overlap": (
        "review_line_box", "manual", "叠字行需要回到原图确认"),
    "page_blank": (
        "blocked_blank_page", "blocked", "空白页没有可修复内容"),
}


def _action_for(issue: dict[str, Any]) -> dict[str, Any]:
    code = str(issue.get("code") or "unknown")
    action, mode, reason = _ACTIONS.get(
        code, ("manual_review", "manual", "未登记的问题类型，默认人工复核"))
    return {
        "code": code,
        "action": action,
        "mode": mode,
        "reason": reason,
        "target": issue.get("ref") or issue.get("page"),
        "message": issue.get("message") or "",
    }


def build_repair_plan(issues: list[dict[str, Any]]) -> dict[str, Any]:
    """把 quality.issues 转成稳定、去重的修复队列。"""
    actions: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues or []:
        item = _action_for(issue)
        key = (item["code"], item["action"], str(item["target"]))
        if key in seen:
            continue
        seen.add(key)
        actions.append(item)
    counts = {"auto_candidate": 0, "manual": 0, "blocked": 0}
    for item in actions:
        counts[item["mode"]] = counts.get(item["mode"], 0) + 1
    return {
        "actions": actions,
        "counts": counts,
        "total": len(actions),
        "auto_executed": False,
        "note": "auto_candidate 只是允许重试，执行前仍应保留原 JSON；manual 不得自动补内容",
    }
