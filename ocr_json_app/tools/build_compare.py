#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_compare.py —— 生成两个 OCR 引擎的并排对比页 `out/compare.html`。

为什么放在 out/ 里：对比页要 fetch 两份 JSON 并内嵌两个 iframe。浏览器对 `file://`
下的 fetch 会按 CORS 拒绝，所以必须经本地服务打开；而 `ocr_pdf_layout.py --serve-only`
的服务根就是 JSON 所在目录，因此把页面放进 out/ 后相对路径全部命中。

用法：
    python build_compare.py                 # 默认 out/
    python build_compare.py --out other
"""

from __future__ import annotations

import argparse
import os
import re
import sys

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>两个 OCR 的 JSON 渲染对比 · __TITLE__</title>
<style>
:root{--bg:#eef0f4;--card:#fff;--line:#d6dbe1;--ink:#16181c;--muted:#6b7280;
  --a:#0f766e;--b:#7c3aed}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--ink);
  font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  display:flex;flex-direction:column}
header{background:#fff;border-bottom:1px solid var(--line);padding:10px 18px;
  display:flex;gap:14px;align-items:center;flex-wrap:wrap}
header h1{font-size:16px;margin:0;font-weight:650;white-space:nowrap}
.seg{display:flex;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.seg button{border:0;background:#fff;padding:6px 13px;font-size:13.5px;cursor:pointer;color:var(--muted)}
.seg button[aria-pressed=true]{background:#111827;color:#fff}
.chip{font-size:12px;font-weight:600;border-radius:999px;padding:3px 10px;color:#fff}
.chip.a{background:var(--a)} .chip.b{background:var(--b)}
.grow{flex:1}
main{flex:1;display:flex;min-height:0}
.pane{flex:1;min-width:0;display:flex;flex-direction:column;border-right:1px solid var(--line)}
.pane:last-child{border-right:0}
.pane .bar{padding:6px 12px;background:#f8fafc;border-bottom:1px solid var(--line);
  display:flex;gap:9px;align-items:center;font-size:12.5px;color:var(--muted)}
.pane iframe{flex:1;width:100%;border:0;background:#fff}
.stats{background:#fff;border-top:1px solid var(--line);padding:10px 18px;overflow:auto;max-height:40vh}
.stats h2{font-size:13.5px;margin:0 0 8px;font-weight:650}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{border:1px solid var(--line);padding:4px 8px;text-align:left}
th{background:#f8fafc;font-weight:650}
td.num{text-align:right;font-variant-numeric:tabular-nums}
.best{background:#ecfdf5;font-weight:650}
.note{color:var(--muted);font-size:12px;margin:8px 0 0}
</style>
</head>
<body data-mode="single">
<header>
  <h1>两个 OCR 的 JSON 渲染对比</h1>
  <span class="chip a">OvisOCR2</span>
  <span class="chip b">PaddleOCR-VL-1.6</span>
  <span class="grow"></span>
  <div class="seg" role="group" aria-label="布局">
    <button id="m-single" aria-pressed="true">单栏（切换引擎）</button>
    <button id="m-split" aria-pressed="false">并排对比</button>
  </div>
  <div class="seg" role="group" aria-label="选择引擎">
    <button id="e-ovis" aria-pressed="true">看 OvisOCR2</button>
    <button id="e-paddle" aria-pressed="false">看 PaddleOCR-VL</button>
  </div>
</header>

<main>
  <section class="pane" id="pane-ovis">
    <div class="bar"><span class="chip a">OvisOCR2</span>
      <span>paper.layout.json</span></div>
    <iframe id="if-ovis" src="./viewer_layout.html" title="OvisOCR2 渲染"></iframe>
  </section>
  <section class="pane" id="pane-paddle">
    <div class="bar"><span class="chip b">PaddleOCR-VL-1.6</span>
      <span>paper.layout.paddle.json</span></div>
    <iframe id="if-paddle" src="./viewer_layout_paddleocrvl.html" title="PaddleOCR-VL 渲染"></iframe>
  </section>
</main>

<section class="stats">
  <h2>量化对比（同一份 PDF、同一套行盒、同一台机器、每行 3 次投票）</h2>
  <table id="cmp">
    <thead><tr><th>指标</th><th>OvisOCR2</th><th>PaddleOCR-VL-1.6</th></tr></thead>
    <tbody><tr><td colspan="3">加载中…</td></tr></tbody>
  </table>
  <p class="note" id="note"></p>
</section>

<script>
const FILES = { ovis: "./paper.layout.json", paddle: "./paper.layout.paddle.json" };
let selected = "ovis";

function show(which) {
  selected = which;
  document.getElementById("e-ovis").setAttribute("aria-pressed", String(which === "ovis"));
  document.getElementById("e-paddle").setAttribute("aria-pressed", String(which === "paddle"));
  if (document.body.dataset.mode === "split") return;
  document.getElementById("pane-ovis").style.display = which === "ovis" ? "flex" : "none";
  document.getElementById("pane-paddle").style.display = which === "paddle" ? "flex" : "none";
}
function setMode(mode) {
  document.body.dataset.mode = mode;
  document.getElementById("m-single").setAttribute("aria-pressed", String(mode === "single"));
  document.getElementById("m-split").setAttribute("aria-pressed", String(mode === "split"));
  if (mode === "split") {
    document.getElementById("pane-ovis").style.display = "flex";
    document.getElementById("pane-paddle").style.display = "flex";
  } else show(selected);
}
document.getElementById("m-single").onclick = () => setMode("single");
document.getElementById("m-split").onclick = () => setMode("split");
document.getElementById("e-ovis").onclick = () => { setMode("single"); show("ovis"); };
document.getElementById("e-paddle").onclick = () => { setMode("single"); show("paddle"); };
setMode("single");

const countTables = d => (d.pages || []).reduce((n, p) => n + (p.lines || [])
  .reduce((m, l) => m + ((l.tables || []).length), 0), 0);
const countChars = d => (d.pages || []).reduce((n, p) => n + (p.markdown || "").length, 0);

const METRICS = [
  ["页数", d => d.stats.pages],
  ["行数", d => d.stats.lines],
  ["含公式行", d => d.stats.lines_with_math],
  ["还原插图", d => d.stats.figures],
  ["解析出的表格", countTables],
  ["投票不一致行", d => d.stats.disagreed_lines ?? "—"],
  ["正文字符数", countChars],
  ["公式定界符", d => (d.document.engine || "").includes("paddle") ? "\\\\(...\\\\)" : "$...$"],
  ["插图 / 表格输出形式",
   d => (d.document.engine || "").includes("paddle") ? "裁片 / 纯文本" : "裁片 / HTML 表格"],
];

Promise.all(Object.entries(FILES).map(([k, url]) =>
  fetch(url).then(r => r.json()).then(d => [k, d]).catch(() => [k, null]))
).then(rs => {
  const docs = Object.fromEntries(rs);
  document.querySelector("#cmp tbody").innerHTML = METRICS.map(([label, fn]) => {
    const a = docs.ovis ? fn(docs.ovis) : "（失败）";
    const b = docs.paddle ? fn(docs.paddle) : "（失败）";
    const num = typeof a === "number" && typeof b === "number" && a !== b;
    const clsA = num && a > b && label !== "投票不一致行" ? "best" : "";
    const clsB = num && b > a && label !== "投票不一致行" ? "best" : "";
    return `<tr><td>${label}</td><td class="num ${clsA}">${a}</td>` +
           `<td class="num ${clsB}">${b}</td></tr>`;
  }).join("");

  const ta = docs.ovis ? countTables(docs.ovis) : 0;
  const tb = docs.paddle ? countTables(docs.paddle) : 0;
  document.getElementById("note").textContent =
    `硬差异：OvisOCR2 把第 17 题的学科分布表输出为 HTML <table>（含 rowspan/colspan），` +
    `因此还原出 ${ta} 张真表格；PaddleOCR-VL 在官方前缀 "OCR:" 下把表格逐格输出为换行文本` +
    `（学科\\n语文\\n数学\\nA学校\\n1\\n2…），行列结构不可恢复，只解析出 ${tb} 张。` +
    `实测加强制提示词（"请把表格转成 HTML"）会让它退化并重复输出 <br> 达 23 秒，故不采用。`;
}).catch(e => {
  document.querySelector("#cmp tbody").innerHTML =
    `<tr><td colspan="3">加载失败：${e.message}（请用 --serve-only 打开本页）</td></tr>`;
});
</script>
</body>
</html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "out"))
    ap.add_argument("--title", default="静安区2026届高三一模数学试卷")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "compare.html")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(PAGE.replace("__TITLE__", args.title))
    print(f"写出 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
