#!/usr/bin/env node
/**
 * build_exact_viewer.mjs —— 为"内嵌字体 + 逐字符位置"产物生成单文件阅读器。
 *
 * 为什么要单独一个生成器：项目现有的 layout 阅读器读的是 `lines[]`（每行一个盒子），
 * 而这里的产物是 `lines[].spans[].chars[]`（每个字符一个精确位置）。
 * 结构不同，硬塞进去会丢掉逐字符信息——而**逐字符位置正是这条路的价值所在**。
 *
 * 字体用**相对路径** ./fonts/xxx.ttf 引用（需经本地服务打开）；
 * 这样 HTML 本身很小，改字体不用重新生成数据。
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";

const ROOT = process.cwd();
const JSON_PATH = join(ROOT, "out", "paper.exact.paddleocrvl16.json");
const OUT_PATH = join(ROOT, "out", "viewer_exact_paddleocrvl16.html");

const doc = JSON.parse(readFileSync(JSON_PATH, "utf8"));

// PDF 提取的字体：**修复 OS/2 后浏览器可正常加载**（见 tools/fix_fonts.py）。
// 缺 OS/2 会被 OTS 拒绝；补上后 HYShuSongErKW / TimesNewRoman* 均生效
// （实测加载后宽度与 serif 回退不同）。用原版字体后字形与字宽都与原版一致。
const base64Cache = new Map();
function fontDataUri(file) {
  if (base64Cache.has(file)) return base64Cache.get(file);
  const b64 = readFileSync(join(ROOT, "out", "fonts", file)).toString("base64");
  const uri = `data:font/ttf;base64,${b64}`;
  base64Cache.set(file, uri);
  return uri;
}
const faces = Object.entries(doc.document.fonts).map(([slug, info]) => `
@font-face{font-family:"${slug}";src:url("${fontDataUri(info.file)}") format("truetype");font-display:block}`).join("");

/**
 * 页面缩放系数 k：把 PDF 点换算成 CSS px。
 *
 * ⚠️ 不能用 pt 当 CSS 单位直接写：CSS 的 `1pt = 1/72in`，而浏览器把 1in 当 96px，
 * 于是 `595.68pt` 会渲染成 794 CSS px；再乘 DPR 1.4102 只有 1119 设备像素，
 * 与原版 150dpi 的 1241 像素对不上（实测就是这样，截图 1119x1583）。
 * 正解：显式按 `k = dpi/72` 缩放，CSS px = PDF点 × k，然后 **DPR 固定 1**
 * —— 这样 595.68 × 2.0833 = 1241 CSS px，截图就是 1241 设备像素，与原版一一对应。
 */
const K = doc.document.dpi / 72;

/**
 * 字体映射。
 *
 * ⚠️ **不能用 PDF 提取的内嵌字体**：对照实验证明它们被浏览器拒绝——
 * 系统字体（times/arial/simhei）走同一条 base64 + FontFace 路径**全部成功**
 * （ok:true，且量出的宽度与 serif 回退不同），而 PDF 提取的字体
 * （原始 / 补过 post / 不同文件）**全部 NetworkError**。
 * 补 `OS/2` 也没救回来（且 fontTools 在 `os2.panose` 处抛类型错误）。
 *
 * 改为**系统字体 + 运行时逐字形水平校正**（见 __OCR_FIT_WIDTH__）：
 * 原版 PDF 里的拉丁与数学本来就是 **Times New Roman**，本机有同名字体，
 * 这一半能真正对上；CJK 用 SimSun 兜底，宽度靠 canvas 实测后 scaleX 校正。
 */

/**
 * PUA → 真实 Unicode 符号映射。
 *
 * 为什么需要：`KingsoftSign` / `KingsoftExtra` 把数学符号放在**私用区**
 * （U+F03D 就是等号）。私用区任何系统字体都不认 → 渲染成豆腐块。
 * 实测 KingsoftSign 的 291 个字符里命中率只有 3.1%，是"缺失字形"的主体。
 *
 * 这张表怎么来的（可复核）：`tools/dump_pua.py` 把每个码位的字形画成
 * `out/pua_sheet.png` 按图形辨认；并用 `advance/em` 交叉验证——
 * Times New Roman 的 `=`/`+`/`−`/`<`/`>` 都是 0.549em、`(`/`)` 是 0.333em、
 * 小写希腊字母 0.384em、`{`/`}` 0.48em、`∈` 0.713em，与实测完全吻合。
 *
 * ⚠️ 没有把握的码位**不映射**（宁可留空，也不要映射错）：U+F047 / F0EF /
 * F0F0 / F067 / F06A / F0EC / F0ED / F0EE 是分式用的**拉伸括号**，字形随上下文变化。
 */
const PUA_MAP = {
  0xF03D: "=", 0xF028: "(", 0xF029: ")", 0xF02D: "\u2212", 0xF02B: "+",
  0xF03E: ">", 0xF03C: "<", 0xF0CE: "\u2208",
  0xF07B: "{", 0xF07D: "}", 0xF0D7: "\u00B7", 0xF0FC: "\u00B7",
  0xF0B3: "\u2265", 0xF0B1: "\u00B1", 0xF0A3: "\u2264", 0xF0A2: "\u2264",
  0xF0E6: "(", 0xF0F6: ")", 0xF0E7: "(", 0xF0F7: ")",
  0xF0E8: "(", 0xF0F8: ")",
  0xF072: "\u03C9", 0xF061: "\u03B1", 0xF077: "\u03BC",
  0xF05B: "[", 0xF05D: "]",
};

/** 映射出来的都是数学符号，用数学字体渲染。 */
const MATH_FAMILY = "'Cambria Math','Times New Roman',Times,serif";

function realChar(ch) {
  const cp = ch.codePointAt(0);
  return PUA_MAP[cp] !== undefined ? PUA_MAP[cp] : ch;
}

const css = `
html,body{margin:0;padding:0;background:#e9ecef;font-family:sans-serif}
.page{position:relative;background:#fff;margin:20px auto;box-shadow:0 2px 10px rgba(0,0,0,.18)}
.ch{position:absolute;white-space:pre;color:#000;line-height:1}
.fig{position:absolute;display:block}
.rule{position:absolute;background:#000}
`;

function pageHtml(p) {
  const parts = [];
  // 图/表裁片：**图不是文字**（原版 PDF 里它们本就是位图），用户口径③只禁止
  // "用裁片替代文字"。实测图块占原版墨迹 5.0%，不贴回去永远到不了 99%。
  // 文字仍然全部由下面的逐字符层提供（可选中、可搜索）。
  for (const f of (p.figures || [])) {
    parts.push(`<img class="fig" alt="第${p.page}页插图" `
      + `style="left:${(f.left * K).toFixed(2)}px;top:${(f.top * K).toFixed(2)}px;`
      + `width:${(f.width * K).toFixed(2)}px;height:${(f.height * K).toFixed(2)}px" `
      + `src="data:image/png;base64,${f.png_base64}">`);
  }
  // 细横线（分式横线/根号线）：这些在 PDF 里是**矢量图形**，文字层没有对应字符，
  // 逐字符渲染画不出来。用矩形补上——线就是矩形，这是像素级精确的画法。
  // 只画"当前渲染确实没覆盖"的那些（由 build_exact.py 的 filter_uncovered_rules 判定）。
  for (const r of (p.rules || [])) {
    parts.push(`<div class="rule" style="left:${(r.left * K).toFixed(2)}px;`
      + `top:${(r.top * K).toFixed(2)}px;width:${(r.width * K).toFixed(2)}px;`
      + `height:${Math.max(1, r.height * K).toFixed(2)}px"></div>`);
  }
  for (const ln of p.lines) {
    for (const sp of ln.spans) {
      // ⚠️ 字体族里必须用**单引号**：整个 style 在 `style="..."` 里，
      // 里面再出现双引号会把属性截断，导致 font-family 整条失效
      // （实测：位置全对，但所有字符都回退渲染）。
      // 用**原版内嵌字体**（修复 OS/2 后可加载）——字形与字宽都与原版一致
      const fam = `'${sp.font}',serif`;
      for (const c of sp.chars) {
        // PUA 码位换成真实 Unicode 符号（换出来了就用数学字体渲染）
        const shown = realChar(c.c);
        const useFam = shown !== c.c ? MATH_FAMILY : fam;
        // 每个字符独立绝对定位：位置直接来自 PDF 文字层，按 k 缩放到 CSS px
        parts.push(`<span class="ch" style="left:${(c.x * K).toFixed(2)}px;`
          + `top:${(c.y_top * K).toFixed(2)}px;`
          + `height:${((c.y_bot - c.y_top) * K).toFixed(2)}px;font-family:${useFam};`
          + `font-size:${(sp.size * K).toFixed(2)}px" data-c="${shown.replace(/"/g, "&quot;")}"`
          + ` data-w="${(c.w * K).toFixed(2)}">`
          + escapeHtml(shown) + `</span>`);
      }
    }
  }
  return `<div class="page" data-page="${p.page}" style="width:${(p.width * K).toFixed(2)}px;`
    + `height:${(p.height * K).toFixed(2)}px">` + parts.join("") + `</div>`;
}

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

const html = `<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>${escapeHtml(doc.document.title)} — 精确重建</title>
<style>${faces}${css}</style></head>
<body>
${doc.pages.map(pageHtml).join("\n")}
<script>window.__OCR_RENDERED__ = true;
// 页面一加载就自动做逐字形水平校正（不等外部调用）——
// 否则用截图工具直接抓页面时校正根本没跑，量出来的还是未校正的数据。
window.__OCR_FIT_DONE__ = false;
window.addEventListener("load", function () {
  try {
    window.__OCR_FIT_STAT__ = window.__OCR_FIT_WIDTH__();
  } catch (e) {
    window.__OCR_FIT_STAT__ = { error: String(e) };
  }
  window.__OCR_FIT_DONE__ = true;
});
// 自检：把"字体是否真的提供了某个码位的字形"量出来写进 title。
// 判据：同一段文本在"指定字体"与"serif 回退"下宽度相同 → 说明该字体没有这个字形
// （浏览器回退到了 serif），也就是会显示 tofu。
window.__OCR_FONTDIAG__ = function () {
  var c = document.createElement('canvas').getContext('2d');
  var out = { status: document.fonts.status, fams: {} };
  var fams = ${JSON.stringify(Object.keys(doc.document.fonts))};
  for (var i = 0; i < fams.length; i++) {
    var f = fams[i];
    var spec = '40px "' + f + '", serif';
    var plain = '40px serif';
    c.font = spec; var han = c.measureText('\u9759').width;
    c.font = spec; var pua = c.measureText('\uF028').width;
    c.font = plain; var puaFallback = c.measureText('\uF028').width;
    out.fams[f] = {
      han: +han.toFixed(1),
      pua: +pua.toFixed(1),
      puaSerif: +puaFallback.toFixed(1),
      puaHasGlyph: Math.abs(pua - puaFallback) > 0.5
    };
  }
  return out;
};
// 量"某个字符在页面上实际渲染出来的盒子"，与预期位置对比——
// 这能区分"位置错" "字号错" "根本没画出来"三种情况。
window.__OCR_BOXDIAG__ = function (n) {
  var els = [].slice.call(document.querySelectorAll('.ch')).slice(0, n || 8);
  var pr = document.querySelector('.page').getBoundingClientRect();
  return els.map(function (el) {
    var r = el.getBoundingClientRect();
    return {
      c: el.dataset.c,
      fam: el.style.fontFamily,
      fs: el.style.fontSize,
      cssLeft: el.style.left,
      cssTop: el.style.top,
      w: +r.width.toFixed(2), h: +r.height.toFixed(2),
      leftRel: +(r.left - pr.left).toFixed(2), topRel: +(r.top - pr.top).toFixed(2)
    };
  });
};

/**
 * 用 FontFace API 注入字体二进制（base64 数据 URI），并**核对是否真的生效**。
 *
 * 为什么不只用 @font-face：实测这些从 PDF 提取的子集字体在 @font-face 下
 * 全部加载失败（document.fonts.load 报 NetworkError），但同一段二进制
 * 走 FontFace API 可能被接受。是否成功必须**量出来**，不能假定。
 */
window.__OCR_INSTALL_FONTS__ = function (fonts) {
  var jobs = Object.keys(fonts).map(function (slug) {
    try {
      var ff = new FontFace(slug, 'url(' + fonts[slug] + ')');
      return ff.load().then(function (f) {
        document.fonts.add(f);
        return { slug: slug, ok: true };
      }).catch(function (e) {
        return { slug: slug, ok: false, err: String(e) };
      });
    } catch (e) {
      return Promise.resolve({ slug: slug, ok: false, err: String(e) });
    }
  });
  return Promise.all(jobs);
};

/**
 * 逐字形**水平校正**：把每个字符的水平缩放调到"渲染宽度 = PDF 记录宽度"。
 *
 * 这是"行位置 ≤3pt"的最后一块拼图：垂直位置已由文字层给出（中位 1pt），
 * 剩余误差来自**字形宽度差异**（字体回退）。这里用 canvas 量出真实宽度，
 * 再用 CSS transform 的 scaleX 精确拉到目标宽度——比改字号更准（只动宽度、
 * 不动高度与基线）。
 */
window.__OCR_FIT_WIDTH__ = function () {
  var c = document.createElement('canvas').getContext('2d');
  var els = [].slice.call(document.querySelectorAll('.ch'));
  var fixed = 0, skipped = 0, worst = 0;
  for (var i = 0; i < els.length; i++) {
    var el = els[i];
    var target = parseFloat(el.dataset.w || '0');
    var txt = el.textContent;
    if (!(target > 0.5) || !txt) { skipped++; continue; }
    var fs = parseFloat(el.style.fontSize);
    c.font = fs + 'px ' + el.style.fontFamily;
    var natural = c.measureText(txt).width;
    if (!(natural > 0.5)) { skipped++; continue; }
    var ratio = target / natural;
    var dev = Math.abs(ratio - 1);
    if (dev > worst) worst = dev;
    // 只在 0.7–1.4 倍内校正：超出说明该字符与记录宽度本就不匹配
    // （例如空格、组合字符），强拉会明显变形
    if (ratio > 1.4 || ratio < 0.7) { skipped++; continue; }
    el.style.transformOrigin = 'left top';
    el.style.transform = 'scaleX(' + ratio.toFixed(4) + ')';
    fixed++;
  }
  return { total: els.length, fixed: fixed, skipped: skipped,
           worstRatioDeviation: +worst.toFixed(3) };
};

</script>
</body></html>`;

writeFileSync(OUT_PATH, html);
const kb = (Buffer.byteLength(html) / 1024).toFixed(0);
console.log(`写出 ${OUT_PATH} (${kb} KB, ${doc.pages.length} 页, ${doc.stats.chars} 字符)`);

// 顺带写出"渲染版每页文本"（读序 = DOM 顺序），供 tools/score_text.py 做口径②验收。
//
// ⚠️ 不要用 CDP 的 --eval 去取 textContent：实测大字符串会被截断
// （6 页各只剩 123 字节），算出来的"文本一致率"会假性暴跌到 15%。
// 数据本来就来自产物，直接写文件最可靠。
const textDir = join(ROOT, "out", "render", "exact16", "text");
mkdirSync(textDir, { recursive: true });
for (const p of doc.pages) {
  const txt = p.lines.map(ln => ln.spans.map(sp => sp.chars.map(c => c.c).join("")).join("")).join("");
  writeFileSync(join(textDir, `page${String(p.page).padStart(2, "0")}.txt`), txt);
}
console.log(`同时写出渲染版文本 → ${textDir}`);
