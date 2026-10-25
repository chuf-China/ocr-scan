#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缺陷分类与责任归属（六类现象 × 责任方）。

为什么要单独一层：
    同一个现象会跨层出现——"少文字"既可能是模型把行首数字吞了，也可能是我们
    自己的 table_from_text 把大题标题当成表格吞了，还可能是题号正则不认某种
    写法。按现象直接建修复队列，最后一定会变成"每次针对一个问题打补丁"。
    所以这里把两件事分开登记：

    1. 现象（六类，用户第一眼能看见的"坏"）：乱码 / 重叠 / 少图 / 少文字 /
       多余 / 结构内容错。它回答"这卷能不能交付"。
    2. 责任方（谁能修）：program（我们自己的代码、几何、规则、门禁）、
       model（OCR/VLM 的识别输出）、input（PDF 字库/扫描质量，本仓库不改，
       只能如实报）。它回答"这个缺陷该谁改"。

    每个 issue code 只能属于一个现象、一个主责任方；确实跨层的写 note，
    并给出"怎么判"的可操作判据，避免拍脑袋。

新增 code 时必须在这里登记，否则 `classify()` 会把它列进 unknown——
quality.json 的 summary.defects.unknown 非空就说明有人绕过了这张表。
"""
from __future__ import annotations

from typing import Dict, Iterable, List

# 六个现象类（按用户可见程度排序，不是严重度）。
DEFECT_CLASSES: Dict[str, Dict[str, str]] = {
    "garbled": {
        "label": "乱码",
        "criterion": "文字/公式渲染成错字符：替换符、(cid:)、控制符、私有字库方块",
    },
    "overlap": {
        "label": "重叠",
        "criterion": "行与行、表与正文、图与正文互相压住",
    },
    "missing_figure": {
        "label": "少图",
        "criterion": "该有的图没出现：未检出、被裁掉、归属不到题目",
    },
    "missing_text": {
        "label": "少文字",
        "criterion": "该有的字没出现：整行丢失、标题被吞、题号/选项缺、选项为空",
    },
    "extra": {
        "label": "多余",
        "criterion": "不该有的东西出现：幻觉数字、重复行、页眉装饰被当成内容",
    },
    "semantic": {
        "label": "结构内容错",
        "criterion": "字和图都在，但归属/顺序/结构/公式内容错",
    },
}

# 三个责任方。"input" 不是甩锅：扫描件本身糊、PDF 用了私有字库、
# 原始卷就缺页——这类我们无法在不造假的前提下修好，只能如实报出来。
OWNERS: Dict[str, Dict[str, str]] = {
    "program": {
        "label": "架构程序",
        "rule": "同一份识别输出，改我们的代码/几何/规则/门禁就能变好",
    },
    "model": {
        "label": "模型",
        "rule": "代码按既定判据工作正常，是 OCR/VLM 的输出本身错（认错/丢字/幻觉）",
    },
    "input": {
        "label": "输入",
        "rule": "PDF 字库或扫描质量本身坏，识别与解析都无法挽救，只能报",
    },
}

# 修复层：缺陷真正该在哪一层动手（与 OWNERS 正交，program 才需要层）。
LAYERS: Dict[str, str] = {
    "layout": "版式几何（行/图/表的检出、裁切、归属）",
    "text": "文字内容（OCR 输出、字体、字库）",
    "structure": "试卷结构（题号、大题、选项、小问）",
    "semantics": "语义归属（图表归题、跨页续接）",
    "schema": "产物契约（JSON 字段与证据块）",
}

# code -> (现象类, 主责任方, 修复层, 判定说明)
ISSUE_CODES: Dict[str, tuple] = {
    # —— 乱码 ——
    "garbled_text": (
        "garbled", "model", "text",
        "行文本含 (cid:/U+FFFD/控制符：先看 font_audit 是否命中 PUA/缺字，"
        "命中则属输入字库；否则是模型输出了坏字符",
    ),
    # —— 重叠 ——
    "line_overlap": (
        "overlap", "program", "layout",
        "JSON 行盒本身相交（同栏按 top 排序相邻行），属行盒几何，程序可修",
    ),
    "page_uncovered_ink": (
        "missing_text", "mixed", "layout",
        "有墨迹没被任何行/图覆盖：可能是行未检出（程序），也可能是裁切漏了；"
        "先看该区域是否落在页眉页脚装饰上再定责",
    ),
    # —— 少图 ——
    "figure_unassigned": (
        "missing_figure", "program", "semantics",
        "图检出了但离所有题目 ref 行都超过 80pt；先排除页眉二维码等装饰（应归多余）",
    ),
    # —— 少文字 ——
    "empty_stem": (
        "missing_text", "model", "text",
        "题号行之后没有任何题干文字：多为模型对整行输出空，程序无法凭空补字",
    ),
    "question_content_truncated": (
        "missing_text", "mixed", "text",
        "题干结尾停在半句/公式未闭合：模型截断（text 层）或被表格占位行顶替"
        "（structure 层），用 layout 行原文即可区分",
    ),
    "choice_options_incomplete": (
        "missing_text", "program", "structure",
        "选项不足 4 个：实测多是选项行未被拆开（一行多标签）或分栏归属错，"
        "结构层可修；若原文行确实缺字则改判模型",
    ),
    "choice_option_empty": (
        "missing_text", "model", "text",
        "选项标签在、正文为空：模型对该片段输出空",
    ),
    "table_empty_cell": (
        "missing_text", "mixed", "structure",
        "空单元格：原生表逐格 OCR 失败（model）或该格本来就空（正常），"
        "先看 PDF 该格有无文字再定责",
    ),
    "table_header_empty": (
        "missing_text", "program", "structure",
        "表格没有表头：表头行被并进数据行或 bbox 偏上，属表的行列解析",
    ),
    "table_rows_empty": (
        "missing_text", "program", "structure",
        "表格没有数据行：同上，行列解析问题",
    ),
    "table_unassigned": (
        "semantic", "program", "semantics",
        "表检出了但没归到题目（距离阈值/题目 ref 缺失）",
    ),
    # —— 多余 ——
    "native_ocr_mismatch": (
        "extra", "mixed", "text",
        "原生文字层与 OCR 结果差异大：两边谁错需看原文；"
        "原生层乱码则属输入，OCR 多字则属模型",
    ),
    # —— 结构内容错 ——
    "question_number_gap": (
        "semantic", "mixed", "structure",
        "题号不连续：该题号的行存在=结构规则没认（程序）；行不存在=模型丢行"
        "（模型）。用 layout 里以数字/括号数字开头的行是否被认领来判",
    ),
    "question_number_duplicate": (
        "semantic", "program", "structure",
        "题号重复：多为页眉/答案区/重复扫描被当成题目，结构规则问题",
    ),
    "choice_labels_unexpected": (
        "semantic", "program", "structure",
        "选项标签不是从 A 开始的连续字母：标签解析或跨行拼接问题",
    ),
    "choice_options_graphical": (
        "semantic", "program", "semantics",
        "选项本身是图形（已归属图片），不做文字计数；这是门禁分类，不是缺陷",
    ),
    "section_empty": (
        "semantic", "program", "structure",
        "大题下没有题：小标题重复/答案区被当正文，结构层可修",
    ),
    "section_heading_missing": (
        "semantic", "model", "text",
        "大题小标题整行被 OCR 吞掉，只能按上一大题收口推断；文字确实不在识别输出里",
    ),
    "formula_structure_broken": (
        "semantic", "model", "text",
        "公式定界符/括号/环境不平衡，无法可靠渲染：来自 OCR 输出的 LaTeX 本身坏",
    ),
    "formula_suspicious": (
        "semantic", "model", "text",
        "公式可解析但可疑（如孤立运算符）：模型输出问题，只报不改写",
    ),
    "table_formula_broken": (
        "semantic", "model", "text",
        "表格单元格里的公式不平衡，同 formula_structure_broken",
    ),
    # —— 产物契约（schema 自检，恒为程序） ——
    "region_inconsistent": (
        "semantic", "program", "schema",
        "区域层自洽检查失败（id/bbox/refs/双向回链），纯产物契约问题",
    ),
    "region_ref_unresolved": (
        "semantic", "program", "schema",
        "题目 region_refs 指向不存在的区域，纯产物契约问题",
    ),
    "line_outside_page": (
        "overlap", "program", "layout",
        "行盒越出纸张（容差 2pt），几何问题",
    ),
}


def code_info(code: str) -> dict | None:
    """返回一个 code 的登记信息；未登记返回 None。"""
    entry = ISSUE_CODES.get(code)
    if not entry:
        return None
    klass, owner, layer, note = entry
    return {"code": code, "class": klass, "owner": owner, "layer": layer, "note": note}


def classify(issues: Iterable[dict]) -> dict:
    """把 build_quality 的 issues 汇总成 现象×责任方 两张计数表。

    返回 {"by_class": {...}, "by_owner": {...}, "unknown": [code...]}。
    by_class 每项含 count / errors / warnings / by_owner；
    未登记的 code 进 unknown，不猜归属。
    """
    by_class: Dict[str, dict] = {}
    by_owner: Dict[str, int] = {name: 0 for name in OWNERS}
    by_owner["mixed"] = 0
    unknown: List[str] = []

    for issue in issues or []:
        code = str(issue.get("code") or "")
        level = str(issue.get("level") or "")
        info = code_info(code)
        if not info:
            if code and code not in unknown:
                unknown.append(code)
            continue
        klass = info["class"]
        owner = info["owner"]
        bucket = by_class.setdefault(klass, {
            "count": 0, "errors": 0, "warnings": 0,
            "by_owner": dict.fromkeys(list(OWNERS) + ["mixed"], 0),
        })
        bucket["count"] += 1
        if level == "error":
            bucket["errors"] += 1
        elif level == "warning":
            bucket["warnings"] += 1
        bucket["by_owner"][owner] = bucket["by_owner"].get(owner, 0) + 1
        by_owner[owner] = by_owner.get(owner, 0) + 1

    ordered = {k: by_class[k] for k in DEFECT_CLASSES if k in by_class}
    return {"by_class": ordered, "by_owner": by_owner, "unknown": sorted(unknown)}


def describe_defects(summary: dict) -> str:
    """一行中文摘要，给人看（例如 issue 报告结尾）。"""
    defects = (summary or {}).get("defects") or {}
    parts = []
    for klass, label in ((k, DEFECT_CLASSES[k]["label"]) for k in DEFECT_CLASSES):
        bucket = (defects.get("by_class") or {}).get(klass)
        if not bucket:
            continue
        owners = {k: v for k, v in bucket["by_owner"].items() if v}
        owner_text = "、".join(
            f"{OWNERS[k]['label'] if k in OWNERS else '混合'}{v}"
            for k, v in owners.items())
        parts.append(f"{label}{bucket['count']}（{owner_text}）")
    return "；".join(parts)
