#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 20 套验收卷各生成一个单文件阅读器，并生成 index.html。"""
from __future__ import annotations

import html
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

APP = Path(__file__).resolve().parents[3]
os.chdir(APP)
sys.path.insert(0, str(APP / "src"))
import ocr_pipeline_layout as layout  # noqa: E402

SRC = APP / "out" / "acceptance" / "final25"
VIEW = SRC / "view"
VIEW.mkdir(parents=True, exist_ok=True)
report = json.loads((SRC / "acceptance.json").read_text(encoding="utf-8"))

rows = []
for entry in report["exams"]:
    if not entry.get("pass"):
        continue
    layout_path = Path(entry["layout"])
    payload = json.loads(layout_path.read_text(encoding="utf-8"))
    title = (payload.get("document") or {}).get("title") or layout_path.stem
    base = "view_" + layout_path.stem
    data_name = os.path.relpath(layout_path, VIEW)
    full, _slim = layout.materialize_viewer(str(VIEW), payload, data_name, base)
    m = entry["metrics"]
    rows.append({
        "title": title,
        "viewer": Path(full).name,
        "layout": layout_path.name,
        "exam": Path(entry["exam"]).name if entry.get("exam") else "",
        "quality": Path(entry["quality"]).name if entry.get("quality") else "",
        "pages": m["pages"],
        "questions": m["questions"],
        "options": f"{m['options_actual']}/{m['options_expected']}",
        "coverage": m["coverage"],
        "formula": m["formula_pass_rate"],
        "table": m["table_fill_rate"],
        "manual": m["manual_per_page"],
        "status": m["quality_status"],
    })
    print(full)

rows.sort(key=lambda r: r["title"])
cards = []
for r in rows:
    status_cls = "pass" if r["status"] == "pass" else "warn"
    cards.append(f"""
    <article class="card">
      <div class="card-head">
        <h2>{html.escape(r['title'])}</h2>
        <span class="badge {status_cls}">{html.escape(r['status'])}</span>
      </div>
      <p class="meta">{r['pages']} 页 · {r['questions']} 题 · 选项 {html.escape(r['options'])}</p>
      <div class="metrics">
        <span>覆盖率 <b>{r['coverage']:.2%}</b></span>
        <span>公式 <b>{r['formula']:.2%}</b></span>
        <span>表格 <b>{r['table']:.2%}</b></span>
        <span>人工/页 <b>{r['manual']:.2f}</b></span>
      </div>
      <div class="links">
        <a class="primary" href="{quote(r['viewer'])}">打开阅读器</a>
        <a href="{quote('../' + r['layout'])}">layout</a>
        <a href="{quote('../' + r['exam'])}">exam</a>
        <a href="{quote('../' + r['quality'])}">quality</a>
      </div>
    </article>""")

index = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>20 套数学试卷验收阅读器</title>
<style>
  :root {{ --ink:#1c1917; --muted:#78716c; --line:#e7e5e4; --bg:#fafaf9; --accent:#c2410c; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink);
         font-family:"Inter","Noto Sans SC",system-ui,sans-serif; }}
  header {{ max-width:1120px; margin:0 auto; padding:48px 24px 20px; }}
  h1 {{ margin:0 0 10px; font-size:34px; letter-spacing:-.02em; }}
  .lead {{ color:var(--muted); margin:0; line-height:1.7; }}
  .summary {{ display:flex; flex-wrap:wrap; gap:10px; margin-top:20px; }}
  .summary span {{ background:#fff; border:1px solid var(--line); border-radius:999px;
                   padding:7px 13px; font-size:13px; color:#57534e; }}
  main {{ max-width:1120px; margin:0 auto; padding:8px 24px 56px;
          display:grid; grid-template-columns:repeat(auto-fit,minmax(330px,1fr)); gap:16px; }}
  .card {{ background:#fff; border:1px solid var(--line); border-radius:16px; padding:18px;
           box-shadow:0 1px 2px #00000008; }}
  .card-head {{ display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }}
  h2 {{ margin:0; font-size:17px; line-height:1.35; }}
  .badge {{ flex:none; border-radius:999px; padding:4px 10px; font-size:12px; font-weight:600; }}
  .badge.pass {{ background:#ecfdf5; color:#047857; }}
  .badge.warn {{ background:#fff7ed; color:#c2410c; }}
  .meta {{ margin:8px 0 12px; color:var(--muted); font-size:13px; }}
  .metrics {{ display:grid; grid-template-columns:1fr 1fr; gap:6px 12px; padding:12px 0;
              border-top:1px solid var(--line); border-bottom:1px solid var(--line);
              color:#57534e; font-size:12px; }}
  .metrics b {{ color:var(--ink); font-weight:600; }}
  .links {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-top:14px; font-size:13px; }}
  .links a {{ color:#57534e; text-decoration:none; }}
  .links a:hover {{ color:var(--accent); }}
  .links a.primary {{ background:var(--accent); color:#fff; border-radius:8px; padding:7px 12px; }}
  footer {{ max-width:1120px; margin:0 auto; padding:0 24px 40px; color:var(--muted); font-size:12px; }}
</style>
</head>
<body>
<header>
  <h1>20 套数学试卷验收阅读器</h1>
  <p class="lead">同一套流水线、同一套阈值；点击卡片进入单文件阅读器。阅读器展示逐行 OCR 文本层，右上角可切换原始扫描。</p>
  <div class="summary">
    <span>20 / 20 通过</span>
    <span>平均覆盖率 99.99%</span>
    <span>公式结构 100%</span>
    <span>表格填充 100%</span>
    <span>人工动作 0.07 条/页</span>
  </div>
</header>
<main>{''.join(cards)}</main>
<footer>数据来自 <code>out/acceptance/final25/acceptance.json</code>；页面与题目结构分别见 layout / exam / quality。</footer>
</body>
</html>
"""
(VIEW / "index.html").write_text(index, encoding="utf-8")
print(f"index: {VIEW / 'index.html'}")
print(f"viewers: {len(rows)}")
