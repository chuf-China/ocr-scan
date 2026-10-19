#!/usr/bin/env node
/**
 * fftest.mjs —— 浏览器端加载测试：修复后的字体能否被 FontFace 接受并生效。
 *
 * 为什么要在浏览器里再测一遍：OTS 通过只是必要条件，浏览器还有自己的策略
 * （同名族、data URI、font-display 等）。判据必须量**宽度是否与 serif 回退不同**，
 * 不能只看 load() 是否 resolve（实测系统字体在本 headless 环境里
 * `document.fonts.load()` 对系统字体返回 0 条，只看返回值会误判）。
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const FONTS = [
  "HYShuSongErKW", "TimesNewRomanPSMT", "TimesNewRomanPS-ItalicMT",
  "TimesNewRomanPS-BoldMT", "KingsoftSign", "KingsoftExtra",
];
const FD = join(process.cwd(), "out", "fonts");
const data = {};
for (const f of FONTS) {
  data[f] = "data:font/ttf;base64," + readFileSync(join(FD, `${f}.ttf`)).toString("base64");
}

const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>ff</title></head><body>
<script>
window.__OCR_RENDERED__ = true;
window.__T__ = function () {
  var data = ${JSON.stringify(data)};
  var jobs = Object.keys(data).map(function (k) {
    return new FontFace(k, 'url(' + data[k] + ')').load().then(function (f) {
      document.fonts.add(f);
      var c = document.createElement('canvas').getContext('2d');
      // 判据：加载后宽度必须与 serif 回退不同，才说明字体真的生效
      c.font = '60px "' + k + '", serif';
      var w1 = c.measureText('V1v3.4\\u9759\\u03B1').width;
      c.font = '60px serif';
      var w2 = c.measureText('V1v3.4\\u9759\\u03B1').width;
      return { k: k, ok: true, w: +w1.toFixed(1), serif: +w2.toFixed(1),
               distinct: Math.abs(w1 - w2) > 1 };
    }).catch(function (e) { return { k: k, ok: false, err: String(e) }; });
  });
  return Promise.all(jobs);
};
</script></body></html>`;
writeFileSync(join(process.cwd(), "out", "fftest.html"), html);
console.log("写出 out/fftest.html", (Buffer.byteLength(html) / 1048576).toFixed(1), "MB");
