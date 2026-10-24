#!/usr/bin/env python3
"""check_exam_json.py —— 从版式 JSON 重建试卷结构并执行质量门。

这不是“能渲染就算过”的检查：题号、选项、跨页/跨栏归属、图归属、
重复题号、缺题都会在这里暴露。质量门 status=error 时退出 1，warn 只提示。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import exam_structure  # noqa: E402


def main(path: str) -> int:
    with open(path, "r", encoding="utf-8") as fh:
        layout = json.load(fh)
    exam = exam_structure.build_exam(layout)
    quality = exam_structure.build_quality(layout, exam)
    summary = quality["summary"]
    counts = {
        "questions": len(exam["questions"]),
        "sections": len(exam["sections"]),
        "figures": sum(len(q.get("figures") or []) for q in exam["questions"]),
        "unassigned_figures": len((exam.get("unassigned") or {}).get("figures") or []),
        "unassigned_lines": len((exam.get("unassigned") or {}).get("lines") or []),
    }

    print(f"题数 {counts['questions']}，大题 {counts['sections']}，"
          f"图 {counts['figures']}，未归属图 {counts['unassigned_figures']}，"
          f"未归属行 {counts['unassigned_lines']}")
    for issue in quality["issues"]:
        print(f"  {issue['level'].upper()}: [{issue['code']}] {issue['message']}")
    print(f"质量门：{summary['status']}（error={summary['errors']}, warn={summary['warnings']}）")
    return 0 if summary["status"] != "error" else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: check_exam_json.py <layout.json>")
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
