#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T2 公式规模普查（只读 JSON，不重跑 OCR）。

统计来源（全部为既有产物）：
  1. out/acceptance/native_final3/*.quality.json  -> summary.formula_count / formula_lines
  2. out/acceptance/release/*.quality.json        -> 同上
  3. out/exams/*.exam.json                        -> 逐行扫描 text 里的公式定界符
交叉校验 1/2 的 formula_lines 与 3 的独立计数是否同量级。
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../out
DOLLAR = re.compile(r"\$\$[\s\S]*?\$\$|\$[^$\n]*?\$")
PADDLE = re.compile(r"\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)")


def _lines_from(obj, out):
    """Collect every 'text' string under any nesting."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "text" and isinstance(v, str):
                out.append(v)
            else:
                _lines_from(v, out)
    elif isinstance(obj, list):
        for it in obj:
            _lines_from(it, out)


def scan_quality(pattern):
    rows = []
    for p in sorted(glob.glob(pattern)):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            rows.append({"file": os.path.basename(p), "error": repr(e)})
            continue
        s = d.get("summary") or {}
        if "formula_count" not in s:
            rows.append({"file": os.path.basename(p), "error": "no formula fields"})
            continue
        rows.append({
            "file": os.path.basename(p),
            "status": s.get("status"),
            "questions": s.get("questions"),
            "formula_count": s.get("formula_count"),
            "formula_lines": s.get("formula_lines"),
            "formula_broken": s.get("formula_broken"),
            "formula_suspicious": s.get("formula_suspicious"),
        })
    return rows


def scan_exam_json(pattern):
    rows = []
    for p in sorted(glob.glob(pattern)):
        try:
            d = json.load(open(p, encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            rows.append({"file": os.path.basename(p), "error": repr(e)})
            continue
        texts = []
        _lines_from(d, texts)
        dl = sum(len(DOLLAR.findall(t)) for t in texts)
        pd = sum(len(PADDLE.findall(t)) for t in texts)
        nlines = sum(1 for t in texts if DOLLAR.search(t) or PADDLE.search(t))
        rows.append({
            "file": os.path.basename(p),
            "lines_total": len(texts),
            "formula_lines": nlines,
            "formula_occurrences": dl + pd,
            "delims": {"dollar": dl, "paddle": pd},
        })
    return rows


def summarize(name, rows, count_key, line_key):
    ok = [r for r in rows if "error" not in r]
    bad = [r for r in rows if "error" in r]
    print(f"\n### {name}: {len(rows)} files, {len(ok)} parsed, {len(bad)} unparsed")
    for r in bad:
        print(f"  !! {r['file']}: {r['error']}")
    if not ok:
        return
    tot_c = sum(r.get(count_key) or 0 for r in ok)
    tot_l = sum(r.get(line_key) or 0 for r in ok)
    print(f"  {count_key} total = {tot_c}   ({len(ok)} 套, avg {tot_c/len(ok):.1f}/套)")
    print(f"  {line_key} total = {tot_l}   (avg {tot_l/len(ok):.1f}/套)")
    rk = sorted(ok, key=lambda r: -(r.get(line_key) or 0))
    print(f"  top 5 by {line_key}:")
    for r in rk[:5]:
        print(f"    {r['file'][:48]:50s} {line_key}={r.get(line_key)}  {count_key}={r.get(count_key)}")
    print(f"  bottom 3:")
    for r in rk[-3:]:
        print(f"    {r['file'][:48]:50s} {line_key}={r.get(line_key)}  {count_key}={r.get(count_key)}")
    broken = sum(r.get("formula_broken") or 0 for r in ok)
    susp = sum(r.get("formula_suspicious") or 0 for r in ok)
    if "formula_broken" in ok[0]:
        print(f"  formula_broken total = {broken}, formula_suspicious total = {susp}")
    # 50/100 条抽样所需套数
    if tot_l:
        print(f"  抽样 50 条需覆盖约 {min(len(ok), max(1, round(50/(tot_l/len(ok)))))} 套；"
              f"100 条约 {min(len(ok), max(1, round(100/(tot_l/len(ok)))))} 套")


def main():
    print(f"out/ = {ROOT}")
    a = scan_quality(os.path.join(ROOT, "acceptance", "native_final3", "*.quality.json"))
    summarize("native_final3 (23 套验收)", a, "formula_count", "formula_lines")

    b = scan_quality(os.path.join(ROOT, "acceptance", "release", "*.quality.json"))
    summarize("release", b, "formula_count", "formula_lines")

    c = scan_quality(os.path.join(ROOT, "exams", "*.quality.json"))
    summarize("exams/quality（旧口径）", c, "formula_count", "formula_lines")

    d = scan_exam_json(os.path.join(ROOT, "exams", "*.exam.json"))
    ok = [r for r in d if "error" not in r]
    print(f"\n### exams/*.exam.json 独立扫描: {len(d)} files, {len(ok)} parsed")
    if ok:
        tl = sum(r["formula_lines"] for r in ok)
        to = sum(r["formula_occurrences"] for r in ok)
        print(f"  含公式行 total = {tl}, 公式出现次数 total = {to} ({len(ok)} 套)")
        rk = sorted(ok, key=lambda r: -r["formula_lines"])
        print("  top 5:")
        for r in rk[:5]:
            print(f"    {r['file'][:46]:48s} lines={r['formula_lines']} occ={r['formula_occurrences']} {r['delims']}")
        print(f"  平均每套 {tl/len(ok):.1f} 含公式行；50 条约需 {min(len(ok), max(1, round(50/(tl/len(ok)))))} 套，"
              f"100 条约 {min(len(ok), max(1, round(100/(tl/len(ok)))))} 套")

    # 明细落盘
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "census.json")
    json.dump({"native_final3": a, "release": b, "exams_quality": c, "exams_exam_json": d},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"\n明细写入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
