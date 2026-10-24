#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_exam_json.py —— layout JSON -> exam.json + quality.json.

Usage:
    python tools/build_exam_json.py out/exams/paper.layout.json
    python tools/build_exam_json.py out/exams/paper.layout.json \
        --exam-out out/exams/paper.exam.json --quality-out out/exams/paper.quality.json

Exit code is 0 when the artifacts are written.  `--strict` returns 1 when
quality.summary.status == "fail"; use it in acceptance runs where a structural
failure must block publishing.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import exam_structure as es  # noqa: E402


def _default_paths(layout_path: str) -> tuple[str, str]:
    base = layout_path
    if base.endswith(".layout.json"):
        base = base[:-len(".layout.json")]
    elif base.endswith(".json"):
        base = base[:-len(".json")]
    return base + ".exam.json", base + ".quality.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="构建 exam.json / quality.json")
    ap.add_argument("layout_json")
    ap.add_argument("--exam-out", default=None)
    ap.add_argument("--quality-out", default=None)
    ap.add_argument("--strict", action="store_true",
                    help="quality.status=fail 时返回 1")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    exam_out, quality_out = _default_paths(args.layout_json)
    exam_out = args.exam_out or exam_out
    quality_out = args.quality_out or quality_out

    with open(args.layout_json, encoding="utf-8") as fh:
        layout = json.load(fh)
    exam = es.build_exam(layout, source=os.path.abspath(args.layout_json))
    quality = es.build_quality(layout, exam)

    for path, payload in ((exam_out, exam), (quality_out, quality)):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")

    summary = quality["summary"]
    if not args.quiet:
        print(f"exam:    {exam_out}")
        print(f"quality: {quality_out}")
        print(f"status={summary['status']} sections={summary['sections']} "
              f"questions={summary['questions']} figures="
              f"{summary['figures_assigned']}/{summary['figures_assigned'] + summary['figures_unassigned']}")
        if summary.get("missing_question_numbers"):
            print("missing numbers:", summary["missing_question_numbers"])
        if summary.get("duplicate_question_numbers"):
            print("duplicate numbers:", summary["duplicate_question_numbers"])
        for issue in quality.get("issues") or []:
            print(f"  {issue['level']}: {issue['code']}: {issue['message']}")

    if args.strict and summary["status"] == "fail":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
