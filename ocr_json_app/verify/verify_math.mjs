/**
 * verify_math.mjs —— 用真实 KaTeX 逐条编译 JSON 里的公式，验证数学能真渲染出来。
 *
 * 用法（路径都相对**项目根**解析，与当前工作目录无关）：
 *     node verify/verify_math.mjs                       # 用默认路径
 *     node verify/verify_math.mjs out/paper.ocr.json vendor/katex/katex.min.js
 *
 * 退出码：0 全部可编译；1 有失败。
 */
import { existsSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, isAbsolute, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// 项目根 = 本文件的上一层（verify/ → 根）
const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const abs = (p) => (isAbsolute(p) ? p : resolve(ROOT, p));

const jsonPath = abs(process.argv[2] || "out/paper.ocr.json");
const katexPath = abs(process.argv[3] || "vendor/katex/katex.min.js");

if (!existsSync(jsonPath)) {
  console.error(`找不到 ${jsonPath}`);
  process.exit(1);
}
const require = createRequire(import.meta.url);
const katex = require(katexPath);

const doc = JSON.parse(readFileSync(jsonPath, "utf8"));

// 公式定界符按引擎不同：OvisOCR2 用 $...$，PaddleOCR-VL 用 \(...\)
const engine = (doc.document?.engine || "").toLowerCase();
const re = engine.includes("paddle")
  ? /\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)/g
  : /\$\$([\s\S]+?)\$\$|\$([^$\n]+?)\$/g;

const text = doc.pages.map((p) => p.markdown).join("\n\n");
const formulas = [];
let m;
while ((m = re.exec(text))) {
  const body = (m[1] ?? m[2] ?? "").trim();
  if (body) formulas.push({ body, display: m[1] !== undefined });
}

let ok = 0;
const failures = [];
for (const f of formulas) {
  try {
    katex.renderToString(f.body, { displayMode: f.display, throwOnError: true, strict: false });
    ok++;
  } catch (e) {
    failures.push({ ...f, message: e.message });
  }
}

console.log(`文件：${jsonPath}`);
console.log(`引擎：${doc.document?.engine}  公式总数：${formulas.length}`
  + `（行内 ${formulas.filter(f => !f.display).length}，行间 ${formulas.filter(f => f.display).length}）`);
console.log(`可编译：${ok}`);
if (failures.length) {
  console.log(`\n失败 ${failures.length} 条：`);
  for (const f of failures.slice(0, 20)) {
    console.log(`  ✗ ${f.body}`);
    console.log(`     ${f.message.split("\n")[0]}`);
  }
}
console.log(failures.length ? "\n结果：失败" : "\n结果：通过（所有公式均可渲染）");
process.exit(failures.length ? 1 : 0);
