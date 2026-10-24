#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Formula structure audit for layout/exam JSON.

This is a *structural* check, not a TeX implementation.  It catches the failure
mode that matters for exam output: a formula delimiter opened but never
closed, an unbalanced ``\\left``/``\\right`` pair, or an unclosed environment.
KaTeX rendering is still verified by ``verify/verify_math.mjs``; keeping the two
checks separate means this module stays pure Python and can run on every build
without a browser or Node.

The audit is intentionally conservative.  It reports suspicious formulas but
never edits output text: silently "fixing" a formula would hide the OCR error
instead of surfacing it.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

FORMULA_PATTERNS = {
    "paddle": re.compile(r"\\\[([\s\S]*?)\\\]|\\\(([\s\S]*?)\\\)"),
    "dollar": re.compile(r"\$\$([\s\S]*?)\$\$|\$([^$\n]*?)\$"),
}
LEFT_RE = re.compile(r"\\left(?![A-Za-z])")
RIGHT_RE = re.compile(r"\\right(?![A-Za-z])")


def formula_pattern(engine: str | None) -> re.Pattern[str]:
    return (FORMULA_PATTERNS["paddle"] if "paddle" in (engine or "").lower()
            else FORMULA_PATTERNS["dollar"])


def extract_formulas(text: str, engine: str | None = None) -> list[dict[str, Any]]:
    """Return inline/display formulas in *text* with their span offsets."""
    out: list[dict[str, Any]] = []
    for m in formula_pattern(engine).finditer(text or ""):
        body = (m.group(1) if m.group(1) is not None else m.group(2) or "").strip()
        out.append({
            "body": body,
            "display": m.group(1) is not None,
            "span": [m.start(), m.end()],
        })
    return out


def _brace_balance(body: str) -> tuple[int, int]:
    """Count ``{``/``}`` outside escaped braces."""
    opens = closes = 0
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            opens += 1
        elif ch == "}":
            closes += 1
        i += 1
    return opens, closes


def _environment_balance(body: str) -> bool:
    stack: list[str] = []
    for m in re.finditer(r"\\(begin|end)\{([^{}]+)\}", body):
        kind, name = m.group(1), m.group(2).strip()
        if kind == "begin":
            stack.append(name)
        elif not stack or stack.pop() != name:
            return False
    return not stack


def latex_issues(body: str) -> list[str]:
    """Return issue codes for one formula body.  Empty list means structurally OK."""
    normalized = (body or "").strip()
    if not normalized:
        return ["empty"]
    issues: list[str] = []
    opens, closes = _brace_balance(normalized)
    if opens != closes:
        issues.append("unbalanced_braces")
    if len(LEFT_RE.findall(normalized)) != len(RIGHT_RE.findall(normalized)):
        issues.append("unbalanced_left_right")
    if not _environment_balance(normalized):
        issues.append("unbalanced_environment")
    if "\ufffd" in normalized:
        issues.append("replacement_character")
    if len(normalized) > 600:
        issues.append("too_long")
    return issues


def audit_formulas(
    lines: Iterable[dict[str, Any]],
    engine: str | None = None,
    page_no: int | None = None,
) -> dict[str, Any]:
    """Audit all formulas in a sequence of layout lines."""
    count = line_count = broken = suspicious = 0
    samples: list[dict[str, Any]] = []
    for line in lines:
        text = str(line.get("text") or "")
        formulas = extract_formulas(text, engine)
        if not formulas:
            continue
        line_count += 1
        for f in formulas:
            count += 1
            issues = latex_issues(f["body"])
            if not issues:
                continue
            if any(code in ("empty", "unbalanced_braces",
                            "unbalanced_left_right", "unbalanced_environment",
                            "replacement_character") for code in issues):
                broken += 1
            else:
                suspicious += 1
            if len(samples) < 20:
                samples.append({
                    "page": page_no if page_no is not None else line.get("page"),
                    "line_id": line.get("id"),
                    "body": f["body"][:200],
                    "issues": issues,
                })
    return {
        "count": count,
        "lines": line_count,
        "broken": broken,
        "suspicious": suspicious,
        "samples": samples,
        "ok": broken == 0,
    }
