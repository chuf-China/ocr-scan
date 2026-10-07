/**
 * verify_frontend.mjs —— 用最小 DOM 桩真实执行 viewer.html 里的渲染脚本，
 * 断言渲染结果（而不是只做语法检查）。
 *
 * 用法（路径相对**项目根**解析，与当前工作目录无关）：
 *     node verify/verify_frontend.mjs                       # 默认路径
 *     node verify/verify_frontend.mjs out/viewer.html out/paper.ocr.json
 *
 * 退出码：0 通过；1 失败。
 */
import { existsSync, readFileSync } from "node:fs";
import { dirname, isAbsolute, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const abs = (p) => (isAbsolute(p) ? p : resolve(ROOT, p));

const viewerPath = abs(process.argv[2] || "out/viewer.html");
const jsonPath = abs(process.argv[3] || "out/paper.ocr.json");
for (const p of [viewerPath, jsonPath]) {
  if (!existsSync(p)) {
    console.error(`找不到 ${p}`);
    process.exit(1);
  }
}

const html = readFileSync(viewerPath, "utf8");
const doc = JSON.parse(readFileSync(jsonPath, "utf8"));

const failures = [];
const checks = [];
function check(name, ok, detail = "") {
  checks.push({ name, ok, detail });
  if (!ok) failures.push(`${name}${detail ? " — " + detail : ""}`);
}

// ---------------------------------------------------------------- 最小 DOM 桩
class El {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this._html = "";
    this.textContent = "";
    this.className = "";
    this.id = "";
    this._attrs = {};
    this._childClasses = [];
  }
  set innerHTML(v) {
    this._html = String(v);
    if (this._onHtml) this._onHtml(this._html);
  }
  get innerHTML() {
    return this._html;
  }
  appendChild(c) {
    this.children.push(c);
    this._childClasses.push(c.className || "");
    return c;
  }
  setAttribute(k, v) {
    this._attrs[k] = v;
  }
  querySelectorAll() {
    return [];
  }
  addEventListener() {}
}

const mathCalls = [];
const byId = new Map();
for (const id of ["title", "meta", "toc", "main", "q"]) byId.set(id, new El());
// 记录布局容器最终内容（含所有子块），便于断言
const main = byId.get("main");
main._onHtml = () => {};           // 章节卡片走 appendChild，innerHTML 只用于清空
const toc = byId.get("toc");

let capturedSectionHtml = [];
const origCreate = () => new El();

const listeners = {};
const documentStub = {
  getElementById: (id) => byId.get(id) ?? null,
  createElement: (tag) => new El(tag),
};
const windowStub = {
  renderMathInElement: (el, opts) => mathCalls.push({ el, opts }),
};

// ---------------------------------------------------------------- 执行脚本
const inline = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)]
  .map((m) => m[1]).join("\n");
check("找到内联渲染脚本", inline.length > 200, `${inline.length} 字符`);

let fetchUrl = null;
const fetchStub = async (url) => {
  fetchUrl = String(url);
  const route = String(url).split("?")[0];
  if (route.endsWith(".json")) {
    return { ok: true, status: 200, json: async () => doc };
  }
  return { ok: false, status: 404, json: async () => ({}) };
};

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const run = new AsyncFunction("document", "window", "fetch", "location", "URLSearchParams", inline);

await run(documentStub, windowStub, fetchStub, { search: "" }, URLSearchParams);
await new Promise((r) => setTimeout(r, 50));   // 等 fetch().then 链跑完

// ---------------------------------------------------------------- 断言
check("走了 JSON 请求", fetchUrl === "./paper.ocr.json", `实际 ${fetchUrl}`);
check("标题被写入 header", byId.get("title").textContent === doc.document.title,
  `实际 ${byId.get("title").textContent}`);

const meta = byId.get("meta").textContent;
check("副标题含页数", meta.includes(String(doc.pages.length)), meta);
check("副标题含引擎", meta.includes(doc.document.engine), meta);

const cards = main.children;
check("每页渲染一张卡片", cards.length === doc.pages.length,
  `实际 ${cards.length}，期望 ${doc.pages.length}`);

const allSectionHtml = cards.map((c) => c._html).join("\n");
const allBlockHtml = cards.map((c) =>
  c.children.map((b) => b._html).join("")).join("\n");
const totalBlocks = doc.pages.reduce((n, p) => n + p.blocks.length, 0);
check("所有块都被渲染", cards.reduce((n, c) => n + c.children.length, 0) === totalBlocks,
  `期望 ${totalBlocks}`);

check("块类型 class 保留",
  ["blk heading", "blk question", "blk paragraph", "blk table"]
    .every((c) => cards.some((card) => card._childClasses.includes(c))),
  cards.map((c) => [...new Set(c._childClasses)].join(",")).join(" | "));

const qCount = doc.stats.questions;
const renderedQ = (allBlockHtml.match(/class="qn"/g) || []).length;
check("题干编号块全部渲染", renderedQ === qCount, `渲染 ${renderedQ}，期望 ${qCount}`);

const tableDoc = doc.pages.some((p) => p.blocks.some((b) => b.type === "table"));
if (tableDoc) {
  check("表格渲染成 <table>", allBlockHtml.includes("<table>") &&
    allBlockHtml.includes("<thead>") && allBlockHtml.includes("<td>"), "");
}

check("KaTeX 被调用渲染公式", mathCalls.length > 0, `${mathCalls.length} 次`);
if (mathCalls.length) {
  const delims = mathCalls[0].opts.delimiters.map((d) => d.left + d.right);
  check("行内 $...$ 定界符可用", delims.includes("$$"), delims.join(" "));
}

const tocHtml = toc._html;
check("目录渲染了页面锚点", (tocHtml.match(/href="#page-/g) || []).length >= doc.pages.length,
  `${(tocHtml.match(/href="#page-/g) || []).length} 个锚点`);

check("数学未被 HTML 转义破坏", allBlockHtml.includes("$") && !allBlockHtml.includes("&amp;lt;"),
  "检查 LaTeX 原样透传");

// ---------------------------------------------------------------- 报告
for (const c of checks) {
  console.log(`${c.ok ? "✓" : "✗"} ${c.name}${c.detail && !c.ok ? ": " + c.detail : ""}`);
}
console.log(failures.length
  ? `\n结果：失败（${failures.length}/${checks.length} 项）`
  : `\n结果：通过（${checks.length} 项断言）`);
process.exit(failures.length ? 1 : 0);
