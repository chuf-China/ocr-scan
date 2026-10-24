#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for src/exam_structure.py (pure, no OCR, no files written)."""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.dont_write_bytecode = True

import exam_structure as es  # noqa: E402


def line(text: str, left: float, top: float, **extra) -> dict:
    item = {
        "text": text,
        "left": left,
        "right": left + max(80.0, len(text) * 6.0),
        "top": top,
        "bottom": top + 12.0,
        "height_pt": 12.0,
        "size_pt": 10.0,
        "tables": [],
    }
    item.update(extra)
    return item


class ExamStructureTests(unittest.TestCase):
    def test_reading_order_and_question_grouping(self) -> None:
        page = {
            "page": 1,
            "width": 1000.0,
            "height": 800.0,
            "lines": [
                line("一、单选题", 50, 50),
                line("1. 第一题", 50, 80),
                line("(A) 1", 70, 100),
                line("(B) 2", 70, 120),
                line("3. 右栏题", 500, 70),
                line("(A) 5", 520, 90),
                line("(B) 6", 520, 110),
                line("10.2 20.3 30.4", 50, 140),  # statistics, not question 10
                line("2. 第二题", 50, 160),
                line("(A) 3", 70, 180),
                line("(B) 4", 70, 200),
            ],
            "figures": [{"id": "p1-f1", "pt": [480, 60, 560, 130],
                         "source": "raster-projection"}],
        }
        layout = {"document": {"title": "测试卷"}, "pages": [page]}
        exam = es.build_exam(layout)
        # Column-major order deliberately puts the whole left column first.
        self.assertEqual([q["number"] for q in exam["questions"]], [1, 2, 3])
        q1 = exam["questions"][0]
        self.assertEqual([o["label"] for o in q1["options"]], ["A", "B"])
        self.assertEqual(exam["sections"][0]["title"], "单选题")
        # The figure is closest to q3 in the right column.
        self.assertEqual([f["ref"] for f in exam["questions"][2]["figures"]], ["p1-f1"])

    def test_cross_page_continuation(self) -> None:
        p1 = {
            "page": 1, "width": 1000, "height": 800,
            "lines": [
                line("一、解答题", 50, 50),
                line("4. 已知函数", 50, 80),
                line("(1) 求导", 70, 100),
                line("(2) 求最值", 70, 120),
            ],
            "figures": [],
        }
        p2 = {
            "page": 2, "width": 1000, "height": 800,
            "lines": [
                line("并证明。", 50, 50),
                line("5. 下一题", 50, 80),
                line("(1) 证明", 70, 100),
            ],
            "figures": [],
        }
        exam = es.build_exam({"document": {}, "pages": [p1, p2]})
        q4 = exam["questions"][0]
        self.assertEqual(q4["pages"], [1, 2])
        self.assertTrue(q4["cross_page"])
        self.assertIn("并证明", q4["subquestions"][1]["text"])

    def test_numbered_front_matter_is_not_question(self) -> None:
        page = {
            "page": 1, "width": 1000, "height": 800,
            "lines": [
                line("注意事项：", 50, 40),
                line("1. 答卷前，考生务必将姓名填写在答题卡上。", 50, 60),
                line("2. 回答选择题时，选出每小题答案。", 50, 80),
                line("一、选择题", 50, 110),
                line("1. 已知集合", 50, 140),
                line("(A) 1", 70, 160),
                line("(B) 2", 70, 180),
            ],
            "figures": [],
            "tables": [],
        }
        exam = es.build_exam({"document": {}, "pages": [page]})
        self.assertEqual([q["number"] for q in exam["questions"]], [1])
        self.assertTrue(any("答卷前" in item["text"] for item in exam["front_matter"]))

    def test_content_truncation_and_empty_section(self) -> None:
        page = {
            "page": 1, "width": 1000, "height": 800,
            "lines": [
                line("一、解答题", 50, 40),
                line("1. 已知函数，则", 50, 70),
                line("(A) 1", 70, 90),
                line("(B) 2", 70, 110),
                line("二、填空题", 50, 150),
            ],
            "figures": [], "tables": [],
        }
        layout = {"document": {}, "pages": [page]}
        exam = es.build_exam(layout)
        quality = es.build_quality(layout, exam)
        codes = [i["code"] for i in quality["issues"]]
        self.assertIn("question_content_truncated", codes)
        self.assertIn("section_empty", codes)
        self.assertFalse(quality["checks"]["content_consistency_ok"])

    def test_repair_plan_routes_safe_and_manual(self) -> None:
        from repair_plan import build_repair_plan
        plan = build_repair_plan([
            {"code": "table_empty_cell", "message": "空单元格", "page": 1},
            {"code": "question_content_truncated", "message": "截断", "ref": "q-001"},
            {"code": "table_empty_cell", "message": "同一页同一格", "page": 1},
        ])
        self.assertEqual(plan["counts"], {"auto_candidate": 1, "manual": 1, "blocked": 0})
        self.assertEqual(plan["actions"][0]["action"], "reocr_table_cell")
        self.assertEqual(plan["actions"][1]["action"], "review_question_tail")
        self.assertFalse(plan["auto_executed"])

    def test_native_table_ownership(self) -> None:
        page = {
            "page": 1, "width": 1000, "height": 800,
            "lines": [
                line("一、单选题", 50, 50),
                line("1. 第一题", 50, 80),
                line("(A) 1", 70, 100),
                line("(B) 2", 70, 120),
                line("2. 第二题", 50, 160),
                line("（表格）", 50, 180, tables=[{
                    "source": "pdf-table-cells", "rows": 2, "cols": 2,
                    "headers": ["a", "b"], "rows_data": [["1", "2"]],
                }]),
            ],
            "figures": [],
            "tables": [{
                "source": "pdf-table-cells",
                "bbox_pt": [50, 175, 300, 230],
                "rows": 2, "cols": 2,
                "headers": ["项目", "数值"],
                "rows_data": [["甲", "3"]],
                "text_available": True,
            }],
        }
        layout = {"document": {}, "pages": [page]}
        exam = es.build_exam(layout)
        quality = es.build_quality(layout, exam)
        q1, q2 = exam["questions"]
        self.assertEqual(q1["tables"], [])
        self.assertEqual(len(q2["tables"]), 1)
        self.assertEqual(q2["tables"][0]["headers"], ["项目", "数值"])
        self.assertEqual(q2["tables"][0]["rows"], [["甲", "3"]])
        self.assertEqual(quality["summary"]["tables"], 1)
        self.assertEqual(quality["summary"]["tables_unassigned"], 0)

    def test_quality_flags_missing_number(self) -> None:
        page = {
            "page": 1, "width": 1000, "height": 800,
            "lines": [
                line("一、单选题", 50, 50),
                line("1. 第一题", 50, 80),
                line("(A) 1", 70, 100),
                line("(B) 2", 70, 120),
                line("3. 第三题", 50, 160),
                line("(A) 3", 70, 180),
                line("(B) 4", 70, 200),
            ],
            "figures": [],
        }
        layout = {"document": {}, "pages": [page]}
        exam = es.build_exam(layout)
        quality = es.build_quality(layout, exam)
        self.assertEqual(quality["summary"]["missing_question_numbers"], [2])
        self.assertEqual(quality["summary"]["status"], "fail")
        self.assertFalse(quality["checks"]["question_numbers_complete"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
