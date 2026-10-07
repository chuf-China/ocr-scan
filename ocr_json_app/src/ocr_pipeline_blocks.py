#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ocr_pipeline_blocks —— 结构块流水线：PDF → 每页 Markdown → 结构化 JSON → 网页。

和 `ocr_pipeline_layout`（原版式）的分工：
    本模块按**语义块**输出（heading / question / paragraph / table），适合程序消费：
    抽题号、判题型、建题库。
    版式流水线按**行坐标**输出，适合还原原稿版式、复核、打印。

用法：
    python src/ocr_pipeline_blocks.py --pdf data/试卷.pdf --out out/paper.ocr.json
    python src/ocr_pipeline_blocks.py --pdf data/试卷.pdf --pages 3-4 --dpi 150
    python src/ocr_pipeline_blocks.py --serve-only --out out/paper.ocr.json

退出码：0 成功；2 引擎/依赖问题；3 输入问题；4 识别失败。
"""

from __future__ import annotations

import argparse
import concurrent.futures
import html
import io
import json
import paths
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ocr_common import (  # noqa: E402
    DEFAULT_DPI, DEFAULT_HOST, DEFAULT_MAX_TOKENS, DEFAULT_MODEL, DEFAULT_PORT,
    DEFAULT_TIMEOUT, OCR_PROMPT, check_health, default_prompt_for,
    die, import_pdfium, ocr_png, page_size, render_page, serve,
)

# ---------------------------------------------------------------- Markdown → 结构块

def strip_md(text: str) -> str:
    """去掉行内/块级 Markdown 标记，保留 LaTeX 与纯文本。"""
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"^\s{0,3}>\s?", "", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"\1", text)
    text = re.sub(r"^\s*[-*+]\s+", "", text)
    return normalize_punct(text.strip())


# 模型偶发的标点重复（实测出现过「则实数 a 的取值范围是 ___。。」）。
# 只做「重复标点」这一种确定性收敛，不改动任何数学内容。
REPEATED_PUNCT_RE = re.compile(r"([。，、；：])\1+")


def normalize_punct(text: str) -> str:
    return REPEATED_PUNCT_RE.sub(r"\1", text)


def split_table_html(s: str) -> list[str]:
    """按顶层 <table> 拆成 [文本, 表格, 文本, ...]。"""
    parts: list[str] = []
    pos = 0
    for match in re.finditer(r"<table\b.*?</table>", s, re.S | re.I):
        if match.start() > pos:
            parts.append(s[pos:match.start()])
        parts.append(match.group(0))
        pos = match.end()
    if pos < len(s):
        parts.append(s[pos:])
    return parts


def parse_table(table_html: str) -> dict:
    """HTML 表格 → {headers, rows}。"""
    rows = re.findall(r"<tr\b.*?</tr>", table_html, re.S | re.I)
    grid: list[list[str]] = []
    for row in rows:
        cells = re.findall(r"<t[hd]\b.*?</t[hd]>", row, re.S | re.I)
        grid.append([strip_md(html.unescape(re.sub(r"<[^>]+>", "", c))) for c in cells])
    if not grid:
        return {"headers": [], "rows": []}
    return {"headers": grid[0], "rows": grid[1:]}


def blocks_from_text(text: str, page_no: int) -> list[dict]:
    """把一段纯文本按空行/题目编号切成块。"""
    blocks: list[dict] = []
    for chunk in re.split(r"\n\s*\n", text):
        chunk = chunk.strip()
        if not chunk:
            continue
        lines = [ln for ln in chunk.split("\n") if ln.strip()]
        if not lines:
            continue

        first = lines[0]
        # 标题：Markdown # 或「一. 填空题」
        if first.startswith("#"):
            blocks.append({
                "page": page_no, "type": "heading", "level": len(first) - len(first.lstrip("#")),
                "text": strip_md(first),
            })
            rest = "\n".join(lines[1:]).strip()
            if rest:
                blocks.extend(blocks_from_text(rest, page_no))
            continue
        section = SECTION_RE.match(first)
        if section and len(first) < 120:
            blocks.append({
                "page": page_no, "type": "heading", "level": 2,
                "text": f"{section.group(1)}. {strip_md(section.group(2))}",
            })
            rest = "\n".join(lines[1:]).strip()
            if rest:
                blocks.extend(blocks_from_text(rest, page_no))
            continue

        # 题目：以编号开头 → question 块（含后续行）
        q = QUESTION_RE.match(first)
        if q:
            blocks.append({
                "page": page_no, "type": "question", "number": int(q.group(1)),
                "text": strip_md("\n".join(lines)),
            })
            continue

        blocks.append({
            "page": page_no, "type": "paragraph",
            "text": strip_md("\n".join(lines)),
        })
    return blocks


def markdown_to_blocks(md: str, page_no: int) -> list[dict]:
    """整页 Markdown → 结构块列表（表格单独成块）。"""
    blocks: list[dict] = []
    for part in split_table_html(md):
        if part.strip().lower().startswith("<table"):
            table = parse_table(part)
            if table["headers"] or table["rows"]:
                blocks.append({"page": page_no, "type": "table", **table})
            continue
        blocks.extend(blocks_from_text(part, page_no))
    return blocks


# ---------------------------------------------------------------- 网页阅读器

VIEWER_HTML_NAME = "viewer.html"


def build_viewer_html(title: str) -> str:
    """独立的网页阅读器：读取同目录的 JSON 并渲染。"""
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · OCR 阅读器</title>
<!-- KaTeX 本地自带（离线可用）；jsDelivr 只作为本地文件缺失时的兜底 -->
<link rel="stylesheet" href="./vendor/katex/katex.min.css"
      onerror="this.onerror=null;this.href='https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css';">
<script defer src="./vendor/katex/katex.min.js"
        onerror="var s=document.createElement('script');s.defer=true;s.src='https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js';document.head.appendChild(s);"></script>
<script defer src="./vendor/katex/auto-render.min.js"
        onerror="var s=document.createElement('script');s.defer=true;s.src='https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js';document.head.appendChild(s);"></script>
<style>
:root {{
  --bg: #f6f7f9; --card: #fff; --ink: #1f2328; --muted: #6b7280;
  --line: #e5e7eb; --accent: #2563eb; --accent-soft: #eff6ff; --warn: #b45309;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; background: var(--bg); color: var(--ink);
  font: 16px/1.85 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
}}
header {{
  position: sticky; top: 0; z-index: 10; background: rgba(255,255,255,.92);
  backdrop-filter: blur(8px); border-bottom: 1px solid var(--line);
  padding: 12px 20px; display: flex; gap: 16px; align-items: center; flex-wrap: wrap;
}}
header h1 {{ font-size: 17px; margin: 0; font-weight: 650; }}
header .meta {{ color: var(--muted); font-size: 13px; }}
header .grow {{ flex: 1; }}
input[type=search] {{
  border: 1px solid var(--line); border-radius: 8px; padding: 7px 11px;
  font-size: 14px; width: 220px; background: #fff;
}}
.layout {{ display: grid; grid-template-columns: 260px 1fr; gap: 20px;
  max-width: 1400px; margin: 20px auto; padding: 0 20px 60px; align-items: start; }}
nav {{
  position: sticky; top: 74px; max-height: calc(100vh - 100px); overflow: auto;
  background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 12px;
}}
nav a {{ display: block; padding: 5px 8px; border-radius: 7px; color: var(--ink);
  text-decoration: none; font-size: 13.5px; }}
nav a:hover {{ background: var(--accent-soft); color: var(--accent); }}
nav a.q {{ font-variant-numeric: tabular-nums; }}
nav .navtitle {{ font-size: 12px; text-transform: uppercase; letter-spacing: .08em;
  color: var(--muted); margin: 8px 8px 6px; }}
main {{ min-width: 0; }}
.card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px;
  padding: 18px 22px; margin-bottom: 14px; }}
.card h2 {{ font-size: 18px; margin: 0 0 10px; padding-bottom: 8px;
  border-bottom: 1px solid var(--line); }}
.blk {{ padding: 10px 12px; margin: 10px 0; border-radius: 9px; }}
.blk:hover {{ background: #fafbfc; }}
.blk.heading {{ font-weight: 650; font-size: 17px; background: var(--accent-soft);
  border-left: 3px solid var(--accent); }}
.blk.question {{ border: 1px solid var(--line); border-left: 3px solid #94a3b8; }}
.blk .qn {{ display: inline-block; min-width: 2.4em; font-weight: 700; color: var(--accent);
  font-variant-numeric: tabular-nums; }}
.blk.paragraph {{ color: #374151; }}
.blk.table {{ overflow-x: auto; border: 1px solid var(--line); }}
.blk .tag {{ float: right; font-size: 11px; color: var(--muted); background: #f3f4f6;
  border-radius: 99px; padding: 1px 9px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14.5px; }}
th, td {{ border: 1px solid var(--line); padding: 7px 10px; text-align: left; }}
th {{ background: #f9fafb; font-weight: 650; }}
.pagebadge {{ font-size: 12px; color: var(--muted); margin-left: 8px; }}
.warn {{ background: #fffbeb; border: 1px solid #fde68a; color: var(--warn);
  border-radius: 10px; padding: 10px 14px; margin: 0 0 14px; font-size: 14px; }}
mark {{ background: #fef08a; }}
.searchbar {{ display: flex; gap: 10px; align-items: center; }}
.pager {{ display: flex; gap: 8px; align-items: center; }}
.pager button {{ border: 1px solid var(--line); background: #fff; border-radius: 8px;
  padding: 6px 12px; cursor: pointer; font-size: 14px; }}
.pager button:disabled {{ opacity: .45; cursor: default; }}
.katex {{ font-size: 1.02em; }}
mjx-container, .katex-display {{ overflow-x: auto; overflow-y: hidden; }}
</style>
</head>
<body>
<header>
  <h1 id="title">{html.escape(title)}</h1>
  <span class="meta" id="meta">加载中…</span>
  <span class="grow"></span>
  <div class="searchbar">
    <input type="search" id="q" placeholder="搜索题目 / 文本…">
  </div>
</header>
<div class="layout">
  <nav id="toc"></nav>
  <main id="main"></main>
</div>
<script>
const DATA_URL = new URLSearchParams(location.search).get("json") || "./paper.ocr.json";
let DOC = null, FILTER = "";

function esc(s) {{
  return String(s).replace(/[&<>"']/g, c => (
    {{ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }}[c]));
}}

function withMath(elem) {{
  // 必须走 window.：KaTeX 的 auto-render 挂在全局对象上，
  // 手写里直接引用裸标识符会在非全局作用域下抛 ReferenceError。
  const fn = window.renderMathInElement;
  if (typeof fn !== "function") return false;
  try {{
    fn(elem, {{
      delimiters: [
        {{ left: "$$", right: "$$", display: true }},
        {{ left: "\\\\[", right: "\\\\]", display: true }},
        {{ left: "$", right: "$", display: false }},
        {{ left: "\\\\(", right: "\\\\)", display: false }}
      ],
      throwOnError: false
    }});
    return true;
  }} catch (e) {{
    console.warn("KaTeX 渲染失败：", e);
    return false;
  }}
}}

function blockHTML(b) {{
  if (b.type === "table") {{
    let t = "<table>";
    if (b.headers && b.headers.length) {{
      t += "<thead><tr>" + b.headers.map(h => `<th>${{esc(h)}}</th>`).join("") + "</tr></thead>";
    }}
    t += "<tbody>" + (b.rows || []).map(r =>
      "<tr>" + r.map(c => `<td>${{esc(c)}}</td>`).join("") + "</tr>").join("") + "</tbody>";
    return t + "</table>";
  }}
  const body = esc(b.text || "");
  if (b.type === "question") {{
    return `<span class="qn">${{b.number ?? ""}}.</span>${{body.replace(/^\\s*\\d{{1,2}}\\s*[.、]\\s*/, "")}}`;
  }}
  return body;
}}

function render() {{
  const main = document.getElementById("main");
  const toc = document.getElementById("toc");
  const needle = FILTER.trim().toLowerCase();
  main.innerHTML = ""; toc.innerHTML = "";

  if (DOC.warnings && DOC.warnings.length && !needle) {{
    const w = document.createElement("div");
    w.className = "warn";
    w.textContent = "⚠ " + DOC.warnings.join("；");
    main.appendChild(w);
  }}

  let tocHtml = `<div class="navtitle">目录</div>`;
  let shown = 0, mathOk = true, failed = 0;

  for (const page of DOC.pages) {{
    try {{
    const blocks = (page.blocks || []).filter(b => {{
      if (!needle) return true;
      const hay = [b.text, b.headers?.join(" "), b.rows?.flat().join(" ")]
        .filter(Boolean).join(" ").toLowerCase();
      return hay.includes(needle);
    }});
    if (!blocks.length) continue;
    shown++;

    const sec = document.createElement("section");
    sec.className = "card";
    sec.id = "page-" + page.page;
    sec.innerHTML = `<h2>第 ${{page.page}} 页<span class="pagebadge">`
      + `${{blocks.length}} 块 · ${{page.elapsed_s != null ? page.elapsed_s + "s" : ""}}`
      + `${{page.finish_reason === "length" ? " · ⚠被截断" : ""}}</span></h2>`;
    for (const b of blocks) {{
      const d = document.createElement("div");
      d.className = "blk " + b.type;
      d.innerHTML = blockHTML(b);
      sec.appendChild(d);
    }}
    main.appendChild(sec);
    if (!withMath(sec)) mathOk = false;

    const qs = blocks.filter(b => b.type === "question" && b.number != null);
    tocHtml += `<a href="#page-${{page.page}}">第 ${{page.page}} 页</a>`;
    for (const q of qs) {{
      tocHtml += `<a class="q" href="#page-${{page.page}}">　${{q.number}}. `
        + `${{esc((q.text || "").replace(/^\\s*\\d{{1,2}}\\s*[.、]\\s*/, "").slice(0, 16))}}</a>`;
    }}
    }} catch (e) {{
      failed++;
      console.error("第 " + page.page + " 页渲染失败：", e);
      const bad = document.createElement("section");
      bad.className = "card";
      bad.innerHTML = `<h2>第 ${{page.page}} 页<span class="pagebadge">渲染失败</span></h2>`
        + `<div class="warn">该页渲染出错：${{esc(e && e.message || e)}}</div>`;
      main.appendChild(bad);
    }}
  }}

  if (!shown) main.innerHTML = `<div class="card">没有匹配 “${{esc(FILTER)}}” 的内容。</div>`;
  else if (!mathOk) {{
    const note = document.createElement("div");
    note.className = "warn";
    note.textContent = "⚠ 公式排版库 KaTeX 未加载（离线？），已按 LaTeX 原文显示。";
    main.insertBefore(note, main.firstChild);
  }}
  if (failed) console.warn(failed + " 页渲染失败");
  toc.innerHTML = tocHtml;
}}

fetch(DATA_URL).then(r => {{
  if (!r.ok) throw new Error("HTTP " + r.status);
  return r.json();
}}).then(d => {{
  DOC = d;
  document.getElementById("title").textContent = d.document.title || "OCR 结果";
  document.getElementById("meta").textContent =
    `${{d.pages.length}} 页 · ${{d.document.engine}} · ${{d.document.dpi}} dpi`;
  if (d.document.source_pdf) {{
    document.getElementById("meta").textContent += ` · ${{d.document.source_pdf}}`;
  }}
  render();
  document.getElementById("q").addEventListener("input", e => {{
    FILTER = e.target.value; render();
  }});
}}).catch(err => {{
  document.getElementById("main").innerHTML =
    `<div class="card">加载 ${{esc(DATA_URL)}} 失败：${{esc(err.message)}}<br>`
    + `请用 <code>--serve</code> 启动，或用本地服务器托管该目录。</div>`;
}});
</script>
</body>
</html>
"""


def write_viewer(out_dir: str, title: str) -> str:
    """写出 viewer.html，并把 vendor/katex 一并放到它旁边（保证离线可用）。"""
    import shutil

    path = os.path.join(out_dir, VIEWER_HTML_NAME)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(build_viewer_html(title))

    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    dst = os.path.join(out_dir, "vendor")
    if os.path.isdir(src):
        try:
            os.makedirs(dst, exist_ok=True)
            shutil.copytree(src, dst, dirs_exist_ok=True)
        except OSError as exc:
            print(f"[ocr] 警告：复制 vendor 资源失败（{exc}），"
                  f"阅读器将改用 CDN。", file=sys.stderr)
    return path


# ---------------------------------------------------------------- 主流程

def parse_pages(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(1, total + 1))
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+)\s*)?", spec)
    if not m:
        die(f"--pages 格式不对：{spec!r}（应形如 3 或 3-8）", 3)
    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else start
    if start < 1 or end > total or start > end:
        die(f"--pages {spec} 超出范围（本 PDF 共 {total} 页）", 3)
    return list(range(start, end + 1))


def main() -> int:
    # Windows 控制台默认 GBK，中文日志会乱码/抛 UnicodeEncodeError。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser(description="本地 OCR PDF → JSON → 网页")
    ap.add_argument("--pdf", help="输入 PDF（--serve-only 时可省略）")
    ap.add_argument("--out")
    ap.add_argument("--pages")
    ap.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    ap.add_argument("--prompt", default=None,
                    help="默认按模型自动选：paddleocr-vl → 'OCR:'，ovisocr2 → 整页解析指令")
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--serve", action="store_true", help="出 JSON 后接着起阅读器")
    ap.add_argument("--serve-only", action="store_true",
                    help="只起阅读器，读已有的 JSON（不跑 OCR）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    if not args.prompt:
        args.prompt = default_prompt_for(args.model)

    if args.serve_only:
        json_path = os.path.abspath(args.out or "") if args.out else ""
        if not json_path or not os.path.isfile(json_path):
            die("--serve-only 需要 --out 指向已存在的 JSON", 3)
        out_dir = os.path.dirname(json_path)
        with open(json_path, encoding="utf-8") as fh:
            title = (json.load(fh).get("document") or {}).get("title", "OCR 结果")
        viewer = write_viewer(out_dir, title)
        serve(json_path, viewer, args.port)
        return 0

    if not args.pdf:
        die("缺少 --pdf（或用 --serve-only --out 已有 JSON）", 3)
    if not os.path.isfile(args.pdf):
        die(f"找不到 PDF：{args.pdf}", 3)

    pdfium = import_pdfium()
    with open(args.pdf, "rb") as fh:
        if fh.read(5) != b"%PDF-":
            die(f"不是 PDF 文件：{args.pdf}", 3)

    doc = pdfium.PdfDocument(args.pdf)
    total = len(doc)
    pages = parse_pages(args.pages, total)
    print(f"[ocr] {args.pdf}：共 {total} 页，处理 {pages[0]}..{pages[-1]}（{len(pages)} 页）"
          f"，{args.dpi} dpi")

    check_health(args.host)

    out_path = args.out or os.path.join(
        paths.OUT_DIR, os.path.splitext(os.path.basename(args.pdf))[0] + ".ocr.json")
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    warnings: list[str] = []
    results: list[dict] = []
    lock = threading.Lock()
    t_start = time.time()

    # pypdfium2 底层是原生库，多线程同时渲染会 access violation（已实测崩溃），
    # 所以渲染必须串行；只有 HTTP 推理可以并发。
    render_lock = threading.Lock()

    def work(page_no: int) -> dict:
        idx = page_no - 1
        t0 = time.time()
        with render_lock:
            png = render_page(doc, idx, args.dpi)
            w, h = page_size(doc, idx)
        resp = ocr_png(args.host, args.model, png, args.prompt,
                       args.timeout, args.max_tokens)
        elapsed = round(time.time() - t0, 2)
        entry = {
            "page": page_no,
            "width": w,
            "height": h,
            "render_px": (round(w * args.dpi / 72), round(h * args.dpi / 72)),
            "markdown": resp["text"],
            "blocks": markdown_to_blocks(resp["text"], page_no),
            "elapsed_s": elapsed,
            "finish_reason": resp["finish_reason"],
            "usage": resp["usage"],
        }
        if not args.no_images:
            entry["image_png_base64"] = base64.b64encode(png).decode("ascii")
        with lock:
            print(f"[ocr] 第 {page_no} 页完成：{elapsed}s，"
                  f"{len(entry['blocks'])} 块，{len(resp['text'])} 字符"
                  f"{'（finish=length，可能被截断）' if resp['finish_reason'] == 'length' else ''}")
            if resp["finish_reason"] == "length":
                warnings.append(f"第 {page_no} 页被截断（finish_reason=length），建议降低 --dpi")
        return entry

    workers = min(max(1, len(pages)), 4)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(work, p): p for p in pages}
        for fut in concurrent.futures.as_completed(futures):
            page_no = futures[fut]
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                die(f"第 {page_no} 页识别失败：{exc}", 4)

    results.sort(key=lambda r: r["page"])
    all_blocks = [b for r in results for b in r["blocks"]]
    questions = [b for b in all_blocks if b["type"] == "question"]

    payload = {
        "document": {
            "title": os.path.splitext(os.path.basename(args.pdf))[0],
            "source_pdf": os.path.abspath(args.pdf),
            "pages_total": total,
            "pages_processed": len(results),
            "engine": f"llama.cpp/{args.model}",
            "dpi": args.dpi,
            "ocr_prompt": args.prompt,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "elapsed_s": round(time.time() - t_start, 2),
            "warnings": warnings,
        },
        "stats": {
            "blocks": len(all_blocks),
            "questions": len(questions),
            "question_numbers": [b["number"] for b in questions],
        },
        "pages": results,
    }

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    size_mb = os.path.getsize(out_path) / 1e6
    print(f"[ocr] 写出 {out_path}（{size_mb:.2f} MB，{len(results)} 页，"
          f"{len(all_blocks)} 块，{len(questions)} 道题号）")

    viewer = write_viewer(os.path.dirname(os.path.abspath(out_path)),
                          payload["document"]["title"])
    print(f"[ocr] 阅读器 {viewer}（JSON 需与它同目录，或放到 out/）")

    if args.serve:
        serve(os.path.abspath(out_path), viewer, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
