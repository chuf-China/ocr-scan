#!/usr/bin/env python3
"""check_exam_json.py —— 从版式 JSON 重建试卷结构并执行质量门。

这不是“能渲染就算过”的检查：题号、选项、跨页/跨栏归属、图归属、
重复题号、缺题都会在这里暴露。质量门 status=fail 时退出 1，warn 只提示。
没有 schema_version 的历史演示产物可加 --advisory：仍打印 fail，但退出 0。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import exam_structure  # noqa: E402


def main(path: str, advisory: bool = False) -> int:
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
        "tables": sum(len(q.get("tables") or []) for q in exam["questions"]),
        "unassigned_tables": len((exam.get("unassigned") or {}).get("tables") or []),
        "unassigned_lines": len((exam.get("unassigned") or {}).get("lines") or []),
    }

    print(f"题数 {counts['questions']}，大题 {counts['sections']}，"
          f"图 {counts['figures']}，未归属图 {counts['unassigned_figures']}，"
          f"表 {counts['tables']}，未归属表 {counts['unassigned_tables']}，"
          f"未归属行 {counts['unassigned_lines']}")
    for issue in quality["issues"]:
        print(f"  {issue['level'].upper()}: [{issue['code']}] {issue['message']}")
    repair = quality.get("repair") or {}
    rc = repair.get("counts") or {}
    suffix = "；历史产物仅告警（无 schema_version）" if advisory and summary["status"] == "fail" else ""
    print(f"质量门：{summary['status']}（error={summary['errors']}, warn={summary['warnings']}）"
          f"；修复队列 auto={rc.get('auto_candidate', 0)}, manual={rc.get('manual', 0)}{suffix}")
    if summary["status"] in ("pass", "warn"):
        return 0
    return 0 if advisory else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print("用法: check_exam_json.py <layout.json> [--advisory]")
        raise SystemExit(2)
    raise SystemExit(main(args[0], advisory="--advisory" in sys.argv[1:]))
