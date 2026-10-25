#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""formula_review.py —— 公式区域复核（T2 阶段 1）的纯函数层。

为什么单独一层：T2 要把「公式区域 → 高分辨率裁片 → 公式模型 → LaTeX」这条链的
**判断逻辑**和**模型/渲染 I/O** 分开。判断逻辑必须能在没有 paddle、没有模型、
没有 PDF 的环境里跑单测；I/O 放在 `tools/review_formulas.py`（子进程 + pypdfium2）。

关键约束（见 `docs/specs/t2-formula-regions.md`）：

- 模型结果只写 `region["formulas"]` 证据块，**不改** `region.text` / `line.text`；
- 每条公式带来源（`pp-formulanet-plus-m`）、裁片来源（`pd-doclayout` / `heuristic`）、
  dpi 与 bbox，便于复算和人工复核；
- 与现有 OCR 的比较只做**内容级**归一化判断，不用“KaTeX 能渲染”当正确率。
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# 证据块来源：模型名小写化，下游按字符串分派。
EVIDENCE_SOURCE = "pp-formulanet-plus-m"
CROP_SOURCES = ("pd-doclayout", "heuristic")
AGREEMENTS = ("exact", "normalized", "partial", "differs", "no_current")

# 与 formula_quality 的定界符口径保持一致：$...$ 与 \(...\) / \[...\] 都要支持。
_SEGMENT_RES = (
    re.compile(r"\\\((.+?)\\\)", re.S),
    re.compile(r"\\\[(.+?)\\\]", re.S),
    re.compile(r"\$(.+?)\$", re.S),
)

# 只做**等价符号**的归一化；不要在这里“修公式”（那属于内容判断，不是归一化）。
_ALIASES = (
    (r"\\left\s*\\?\{", ""),   # \left\{ 的分段函数大括号：外层支架，不是内容
    (r"\\left\s*\.", ""),
    (r"\\right\s*\.", ""),
    (r"\\right\s*\\?\}", ""),
    (r"\\left(?![A-Za-z])", ""),
    (r"\\right(?![A-Za-z])", ""),
    (r"\\displaystyle(?![A-Za-z])", ""),
    (r"\\geqslant", r"\\geq"),
    (r"\\leqslant", r"\\leq"),
    (r"\\dfrac", r"\\frac"),
    (r"\\tfrac", r"\\frac"),
    (r"\\colon", ":"),          # \colon 与 : 是同一个符号
    (r"\^\\prime", "'"),        # f^{\prime} 与 f' 是同一个导数记号
    (r"\\,|\\;|\\!|\\ ", ""),
    (r"\\quad|\\qquad", ""),
)

# 环境/对齐相关的“写法差异”：`\begin{cases}` 与 `\left\{\begin{aligned}` 是同一个
# 分段函数，`x^{2}` 与 `x^2` 也是同一个公式；这类差异不算内容差异，只算写法差异。
_ENV_RE = re.compile(r"\\(?:begin|end)\{[A-Za-z*]+\}(?:\[[^\]]*\])?")
_BRACED_TOKEN_RE = re.compile(r"([\^_])\{([^{}\\]{1,3})\}")
# 单参数命令的等价写法：`\sqrt{3}` 与 `\sqrt3`、`\mathrm{ABC}` 与 `ABC` 是同一个东西。
# 只能用于**单参数**命令；`\frac{a}{b}` 的两个参数不能这样折叠。
_SINGLE_ARG_RE = re.compile(r"\\(sqrt)\{([^{}\\]{1,3})\}")
_WRAPPER_RE = re.compile(
    r"\\(?:mathrm|text|textrm|mbox|mathbb|mathsf|mathbf|mathcal|mathit|mathfrak)\{([^{}]*)\}")
# 只包一个命令的冗余花括号：`{\frac{x^{2}}{m}}` 与 `\frac{x^{2}}{m}` 是同一个东西。
_REDUNDANT_GROUP_RE = re.compile(r"\{(\\[A-Za-z]+(?:\[[^\]]*\])?(?:\{[^{}]*\})*)\}")
# 只包短字面量的花括号也是分组符，不是内容：`{:~}`/`{~-~}`/`{(}`。
_SHORT_GROUP_RE = re.compile(r"\{([^\\{}]{1,4})\}")


def extract_math_segments(text: str) -> list[str]:
    """从行文本里按出现顺序抽出公式片段（不含定界符）。"""
    if not text:
        return []
    found: list[tuple[int, str]] = []
    for pattern in _SEGMENT_RES:
        for match in pattern.finditer(text):
            found.append((match.start(), match.group(1)))
    found.sort(key=lambda item: item[0])
    return [body for _, body in found]


def normalize_latex(value: str) -> str:
    """归一化：去定界符、去排版命令、折叠空白、去尾部标点。

    这是**字符级**归一化，只消除“同一公式的两种写法差异”，不改变符号本身；
    因此不能拿它当内容准确率，只能拿它判断“模型和现有 OCR 是否写了同样的东西”。
    """
    body = (value or "").strip()
    for left, right in (("\\(", "\\)"), ("\\[", "\\]"), ("$", "$")):
        if body.startswith(left) and body.endswith(right) and len(body) > len(left) + len(right):
            body = body[len(left):len(body) - len(right)]
    for pattern, repl in _ALIASES:
        body = re.sub(pattern, repl, body)
    body = _ENV_RE.sub("", body)
    body = _WRAPPER_RE.sub(r"\1", body)     # \mathrm{ABC} -> ABC，\text{则} -> 则
    body = body.replace("&", "")            # 对齐标记，不是内容
    body = body.replace("~", "")            # 不换行空格：\mathrm{~-~} -> -
    body = _SINGLE_ARG_RE.sub(r"\\\1\2", body)   # \sqrt{3} -> \sqrt3
    body = _BRACED_TOKEN_RE.sub(r"\1\2", body)  # x^{2} -> x^2，a_{1} -> a_1
    for _ in range(2):                          # {\frac{x^{2}}{m}} -> \frac{x^{2}}{m}
        body = _REDUNDANT_GROUP_RE.sub(r"\1", body)
    body = _SHORT_GROUP_RE.sub(r"\1", body)     # {:~} -> :~，{~-~} -> ~-~
    body = body.replace(" ", "").replace("\t", "").replace("\n", "")
    return body.rstrip(",.，。;；:：")


def classify_agreement(latex: str, current_segments: Iterable[str]) -> tuple[str, str | None]:
    """把模型的 LaTeX 和现有 OCR 的公式片段比较。

    返回 `(agreement, matched_segment)`，从强到弱：
    - `exact`：字符级完全相同；
    - `normalized`：只是等价写法不同（空格/花括号/`\\left`/`, \\geqslant` 等）；
    - `partial`：裁片只覆盖某段公式的一部分（检测框被裁短），一方的归一化结果
      是另一方的子串 —— 这通常不是模型错，而是裁片几何问题，必须与内容差异区分；
    - `differs`：归一化后仍不是子串关系，需要人看；
    - `no_current`：现有文本里没有公式片段可比。
    """
    model_norm = normalize_latex(latex)
    if not model_norm:
        return "no_current", None
    candidates = [seg for seg in current_segments if seg and seg.strip()]
    if not candidates:
        return "no_current", None
    for segment in candidates:
        if segment.strip() == (latex or "").strip():
            return "exact", segment
    for segment in candidates:
        if normalize_latex(segment) == model_norm:
            return "normalized", segment
    for segment in candidates:
        seg_norm = normalize_latex(segment)
        if len(model_norm) >= 4 and len(seg_norm) >= 4 and (
                model_norm in seg_norm or seg_norm in model_norm):
            return "partial", segment
    return "differs", None


def _bbox_ok(bbox: Any) -> bool:
    return (isinstance(bbox, (list, tuple)) and len(bbox) == 4
            and all(isinstance(v, (int, float)) for v in bbox)
            and bbox[0] < bbox[2] and bbox[1] < bbox[3])


def _overlap_area(a: list[float], b: list[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def map_box_to_region(box_pt: list[float], regions: Iterable[dict],
                      min_cover: float = 0.35) -> str | None:
    """把一条公式框映射到 `content_type=formula` 的区域。

    规则：公式框中心落在区域内的优先；否则取与区域 bbox 交叠面积占框面积比例
    最大且 ≥ `min_cover` 的区域。映射失败返回 None（不猜归属）。
    """
    if not _bbox_ok(box_pt):
        return None
    cx = (box_pt[0] + box_pt[2]) / 2.0
    cy = (box_pt[1] + box_pt[3]) / 2.0
    area = (box_pt[2] - box_pt[0]) * (box_pt[3] - box_pt[1])
    best_id = None
    best_cover = 0.0
    for region in regions or []:
        if region.get("content_type") != "formula":
            continue
        bbox = region.get("bbox_pt")
        if not _bbox_ok(bbox):
            continue
        if bbox[0] <= cx <= bbox[2] and bbox[1] <= cy <= bbox[3]:
            return region.get("id")
        cover = _overlap_area(bbox, box_pt) / area if area > 0 else 0.0
        if cover > best_cover:
            best_cover, best_id = cover, region.get("id")
    return best_id if best_cover >= min_cover else None


def build_evidence(latex: str, box_pt: list[float], dpi: int, crop_source: str,
                   score: float | None = None,
                   current_segments: Iterable[str] | None = None) -> dict:
    """构造单条公式证据块；`compare` 记录与现有 OCR 的关系，不做覆盖决策。"""
    segments = [seg for seg in (current_segments or []) if seg and seg.strip()]
    agreement, matched = classify_agreement(latex, segments)
    reasons = []
    if not latex:
        reasons.append("model_output_missing")
    if agreement == "differs":
        reasons.append("differs_from_current")
    if latex and not normalize_latex(latex):
        reasons.append("empty_after_normalize")
    # 一个区域（整行）可能有多段公式，而裁片只覆盖其中一段：`differs` 时把现有片段
    # 全部留在证据里，人工复核才知道“比的是哪几段”，而不是看到空 current 无从下判。
    current = matched if matched is not None else None
    if current is None and segments:
        current = "; ".join(seg.strip() for seg in segments[:4])[:200]
    return {
        "source": EVIDENCE_SOURCE,
        "model": "PP-FormulaNet_plus-M",
        "crop": {"bbox_pt": [round(v, 2) for v in box_pt],
                 "dpi": int(dpi), "crop_source": crop_source},
        "latex": latex,
        "text_marked": f"${latex}$" if latex else "",
        "detect_score": round(score, 4) if isinstance(score, (int, float)) else None,
        "review": {"needed": bool(reasons), "reasons": reasons},
        "compare": {"current": current, "agreement": agreement,
                    "candidates": len(segments)},
    }


def attach_evidence(page: dict, entries: Iterable[dict],
                    unassigned: Iterable[dict] | None = None) -> dict:
    """把证据块挂到区域的 `formulas[]`，并在页上记录未映射的公式框。

    区域文本/行文本保持不动 —— T2 阶段 1 只做“证据”，不改正文。
    """
    regions = page.get("regions") or []
    by_id = {region.get("id"): region for region in regions}
    attached = 0
    for entry in entries:
        region = by_id.get(entry.get("region_id"))
        if region is None or region.get("content_type") != "formula":
            continue
        evidence = {k: v for k, v in entry.items() if k != "region_id"}
        region.setdefault("formulas", []).append(evidence)
        attached += 1
    leftovers = []
    for item in unassigned or []:
        leftovers.append({"bbox_pt": [round(v, 2) for v in item["bbox_pt"]],
                          "detect_score": (round(item["score"], 4)
                                           if isinstance(item.get("score"), (int, float))
                                           else None)})
    if leftovers:
        page["unassigned_formula_boxes"] = leftovers
    return {"attached": attached, "unassigned": len(leftovers)}


def review_summary(pages: Iterable[dict]) -> dict:
    """按页统计公式证据块；只做诊断，不参与质量硬门。"""
    formulas = 0
    regions_reviewed = set()
    agreements = {key: 0 for key in AGREEMENTS}
    reasons: dict[str, int] = {}
    unassigned = 0
    for page in pages or []:
        unassigned += len(page.get("unassigned_formula_boxes") or [])
        for region in page.get("regions") or []:
            blocks = region.get("formulas") or []
            if not blocks:
                continue
            regions_reviewed.add(region.get("id"))
            formulas += len(blocks)
            for block in blocks:
                agreement = (block.get("compare") or {}).get("agreement")
                if agreement in agreements:
                    agreements[agreement] += 1
                for reason in (block.get("review") or {}).get("reasons") or []:
                    reasons[reason] = reasons.get(reason, 0) + 1
    return {"formulas": formulas, "regions_reviewed": len(regions_reviewed),
            "agreement": agreements, "review_reasons": reasons,
            "unassigned_boxes": unassigned}


def validate_formula_evidence(pages: Iterable[dict]) -> list[dict]:
    """校验证据块结构；没有 `formulas` 的产物返回空列表（老产物跳过）。"""
    problems: list[dict] = []
    for page in pages or []:
        pno = page.get("page")
        for region in page.get("regions") or []:
            blocks = region.get("formulas")
            if blocks is None:
                continue
            rid = region.get("id")

            def bad(code: str, message: str) -> None:
                problems.append({"page": pno, "region_id": rid,
                                 "code": code, "message": message})

            if not isinstance(blocks, list):
                bad("formulas_not_list", "formulas 不是数组")
                continue
            if blocks and region.get("content_type") != "formula":
                bad("formulas_on_non_formula", "非 formula 区域带公式证据")
            for block in blocks:
                if not isinstance(block, dict):
                    bad("formula_not_object", "公式证据不是对象")
                    continue
                latex = block.get("latex")
                if not isinstance(latex, str):
                    bad("formula_latex_missing", "缺少 latex 字符串")
                elif not latex.strip() and not block.get("review"):
                    bad("formula_empty_no_review", "空 latex 却没有 review 说明")
                if block.get("source") != EVIDENCE_SOURCE:
                    bad("formula_source", f"source 不是 {EVIDENCE_SOURCE}：{block.get('source')}")
                crop = block.get("crop") or {}
                if not _bbox_ok(crop.get("bbox_pt")):
                    bad("formula_bbox", "裁片 bbox 非法")
                if not isinstance(crop.get("dpi"), int) or crop["dpi"] < 144:
                    bad("formula_dpi", "裁片 dpi 非法")
                if crop.get("crop_source") not in CROP_SOURCES:
                    bad("formula_crop_source", f"crop_source 非法：{crop.get('crop_source')}")
                compare = block.get("compare") or {}
                if compare.get("agreement") not in AGREEMENTS:
                    bad("formula_agreement", f"agreement 非法：{compare.get('agreement')}")
                review = block.get("review")
                if not isinstance(review, dict) or "needed" not in review:
                    bad("formula_review", "review 块缺 needed")
    return problems
