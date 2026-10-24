/**
 * cdp_probe.mjs —— 用 Chrome DevTools Protocol 打开一个本地页面，等渲染完成，
 * 取运行时诊断量并按需整页截图。
 *
 * 为什么不用 `chrome --dump-dom` / `--screenshot`：
 *   - `--dump-dom` 只有序列化后的 DOM，拿不到 `window.__OCR_DIAG__` 这类运行时值；
 *   - `--screenshot` 只截视口，长文档看不全；
 *   - `--virtual-time-budget` 配 `--headless=new` 实测会挂住不返回。
 * CDP 这三个问题都没有。
 *
 * 用法：
 *     node cdp_probe.mjs <htmlPath|url> [--shot out.png] [--eval "expr"] [--width 1000]
 *
 * 退出码：0 成功；1 失败。
 */
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

/**
 * 找一个可用的 Chromium 内核浏览器。
 *
 * 为什么要自动找：早先这里写死了 playwright 的 `chromium_headless_shell-1234`
 * 完整路径——playwright 一升级版本号（本机是 -1228）默认参数就必然失败，
 * 而且报错是 spawn 的 ENOENT，看不出是"路径过期"。
 * 顺序：显式 --chrome > 环境变量 > 系统 Chrome/Edge > playwright 各版本（取最新）。
 *
 * 后三个来源都是"候选里挑存在的"，但显式给的路径**也要检查存在性**：
 * 否则用户传错一个路径，拿到的仍是未捕获的 `spawn ... ENOENT` 栈——
 * 那正是这个函数要消除的困惑。
 */
function checkExplicit(p, source) {
  if (existsSync(p)) return p;
  console.error(`${source} 指向的文件不存在：${p}\n`
    + "请确认路径，或改用 --chrome <路径> / CHROME_PATH 指定可用的 Chromium 内核浏览器。");
  process.exit(1);
}

function findChrome(explicit) {
  if (explicit) return checkExplicit(explicit, "--chrome");
  if (process.env.CHROME_PATH) return checkExplicit(process.env.CHROME_PATH, "环境变量 CHROME_PATH");

  const candidates = [
    `${process.env.PROGRAMFILES}\\Google\\Chrome\\Application\\chrome.exe`,
    `${process.env["PROGRAMFILES(X86)"]}\\Google\\Chrome\\Application\\chrome.exe`,
    `${process.env.LOCALAPPDATA}\\Google\\Chrome\\Application\\chrome.exe`,
    `${process.env["PROGRAMFILES(X86)"]}\\Microsoft\\Edge\\Application\\msedge.exe`,
    `${process.env.PROGRAMFILES}\\Microsoft\\Edge\\Application\\msedge.exe`,
  ];
  for (const c of candidates) if (c && existsSync(c)) return c;

  // playwright 下载的 chromium：版本号会变，按目录名倒序取最新
  const pwRoot = `${process.env.LOCALAPPDATA}\\ms-playwright`;
  if (existsSync(pwRoot)) {
    const dirs = readdirSync(pwRoot)
      .filter(d => /^chromium/.test(d))
      .sort()
      .reverse();
    for (const d of dirs) {
      for (const exe of ["chrome-win64\\chrome.exe",
                         "chrome-headless-shell-win64\\chrome-headless-shell.exe",
                         "chrome-win\\chrome.exe"]) {
        const p = join(pwRoot, d, exe);
        if (existsSync(p)) return p;
      }
    }
  }
  return null;
}

const args = process.argv.slice(2);
const target = args[0];
if (!target) { console.error("用法: node cdp_probe.mjs <htmlPath|url> [--shot p.png]"); process.exit(1); }
const opt = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : dflt;
};
const shotPath = opt("--shot", null);
const shotPagesDir = opt("--shot-pages", null);
// 逐页截图的设备像素比：A4 页 CSS 宽 880、150dpi 原版宽 1241 → 1241/880 = 1.4102
const dpr = Number(opt("--dpr", 1));
const evalExpr = opt("--eval", "window.__OCR_DIAG__");
const showConsole = args.includes("--console");
const width = Number(opt("--width", 1000));
const chromePath = findChrome(opt("--chrome", null));
// 调试端口默认**按进程取唯一值**，不用写死的 9333：连续跑两次探测（run_all_checks 里
// 两个阅读器各一次）时，上一次的 Chrome 可能还没退干净，仍占着 9333 —— 这一次就
// "调试端口未就绪"，表现为偶发失败（实测踩到过一次 10/10 变 9/10）。
// **调试端口要动态挑空闲的**。
//
// 早先用 `9300 + (pid % 500)` 算：多个 Chrome 实例或上一次没退干净的残留进程
// 会占住同一个端口，这一次就"调试端口未就绪"直接失败——表现为
// `run_all_checks` 偶发 16/17（实测出现过两次，重跑又 17/17）。
// 现在让操作系统分配一个空闲端口（绑定 :0 再读回），从根上避免碰撞。
async function pickFreePort() {
  const { createServer } = await import("node:net");
  return new Promise((resolve, reject) => {
    const srv = createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const p = srv.address().port;
      srv.close(() => resolve(p));
    });
  });
}

const portArg = opt("--port", null);
const port = portArg ? Number(portArg) : await pickFreePort();

if (!chromePath) {
  console.error("找不到 Chromium 内核浏览器。请用 --chrome <路径> 指定，"
    + "或设置环境变量 CHROME_PATH（也可以装 Chrome / Edge / playwright）。");
  process.exit(1);
}

const url = /^https?:|^file:/.test(target) ? target : pathToFileURL(target).href;

const child = spawn(chromePath, [
  "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${process.env.TEMP}\\cdp-probe-${Date.now()}`,
  `--window-size=${width},1400`,
  "about:blank",
], { stdio: "ignore" });

// spawn 失败会以 'error' 事件抛出；不接住它就是一条看不懂的 ENOENT 栈
child.on("error", (err) => {
  console.error(`启动浏览器失败（${chromePath}）：${err.message}\n`
    + "请用 --chrome <路径> 或 CHROME_PATH 指定一个可用的 Chromium 内核浏览器。");
  process.exit(1);
});

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function getWsUrl() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (r.ok) return (await r.json()).webSocketDebuggerUrl;
    } catch { /* 还没起来 */ }
    if (child.exitCode !== null) {
      throw new Error(`浏览器刚启动就退出了（exit ${child.exitCode}，调试端口 ${port}）`);
    }
    await sleep(150);
  }
  throw new Error(`Chrome 调试端口 ${port} 未就绪（9s 内）。`
    + "若同时在跑别的 cdp_probe，用 --port 指定不同端口。");
}

let msgId = 0;
const pending = new Map();
let ws;

function send(method, params = {}, sessionId) {
  const id = ++msgId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify(sessionId ? { id, method, params, sessionId } : { id, method, params }));
  });
}

try {
  const wsUrl = await getWsUrl();
  ws = new WebSocket(wsUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });

  const events = [];
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
    } else if (msg.method) {
      events.push(msg);
      if (showConsole) {
        if (msg.method === "Runtime.consoleAPICalled") {
          const text = (msg.params.args || [])
            .map((a) => a.value ?? a.description ?? a.type).join(" ");
          console.error(`[console.${msg.params.type}] ${text}`);
        } else if (msg.method === "Runtime.exceptionThrown") {
          const d = msg.params.exceptionDetails;
          console.error(`[exception] ${d.text} ${d.exception?.description || ""}`);
        }
      }
    }
  };

  // 建一个页目标并 attach
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  const { sessionId } = await send("Target.attachToTarget", { targetId, flatten: true });
  await send("Page.enable", {}, sessionId);
  await send("Runtime.enable", {}, sessionId);
  await send("Page.navigate", { url }, sessionId);

  // 等渲染标记（页面自己设的），最多 30s
  let rendered = false;
  for (let i = 0; i < 120; i++) {
    await sleep(250);
    const r = await send("Runtime.evaluate", {
      expression: "window.__OCR_RENDERED__ === true", returnByValue: true }, sessionId);
    if (r.result && r.result.value === true) { rendered = true; break; }
  }
  if (!rendered) console.error("警告：30s 内未见 __OCR_RENDERED__，继续取值");

  // __OCR_RENDERED__ 是首屏渲染完成的标记，但 KaTeX 重排、图片解码、以及
  // 窗口尺寸变化触发的重排都在它之后。实测不等就取值只能数到一半的节点
  // （74 行只数到 37、4 张图只数到 1），所以必须再静置一会儿。
  // 只做"等待 + 轮询节点数稳定"，不用 awaitPromise（实测那段会挂住不返回）。
  const settleMs = Number(opt("--settle", 4000));
  await sleep(settleMs);
  let last = -1;
  for (let i = 0; i < 20; i++) {
    const r = await send("Runtime.evaluate", {
      expression: "document.querySelectorAll('*').length", returnByValue: true }, sessionId);
    const n = r.result?.value ?? -1;
    if (n === last) break;
    last = n;
    await sleep(250);
  }
  const value = await send("Runtime.evaluate", {
    expression: evalExpr, returnByValue: true, awaitPromise: true }, sessionId);
  console.log(JSON.stringify(value.result?.value ?? null, null, 1));

  // 逐页截图：按每个 `.page` 元素的 boundingRect 分别 clip 保存。
  // 为什么需要：保真度评分要拿"渲染出来的每一页"与原版 PDF 的对应页做墨迹比对，
  // 整页长截图需要再裁，而裁剪边界（页间距、页眉）容易错。直接按元素 rect 截最准。
  //
  // ⚠️ `--dpr` 很关键：默认 deviceScaleFactor=1 时，A4 页在 CSS 里是 880x1244，
  // 而 150dpi 的原版页是 1241x1754。**必须按 1.4102 截图**才能让两边同分辨率；
  // 靠事后缩放对比会让墨迹阈值比较失真（实测墨迹像素差 2.9 倍，IoU 假性偏低）。
  if (shotPagesDir) {
    mkdirSync(shotPagesDir, { recursive: true });
    if (dpr !== 1) {
      await send("Emulation.setDeviceMetricsOverride",
        { width, height: 1400, deviceScaleFactor: dpr, mobile: false }, sessionId);
      await sleep(600);
    }
    const rects = await send("Runtime.evaluate", {
      expression: `JSON.stringify([...document.querySelectorAll('.page')].map((el,i)=>{
        const r = el.getBoundingClientRect();
        return {i:i+1, x:r.left+scrollX, y:r.top+scrollY, w:r.width, h:r.height};}))`,
      returnByValue: true }, sessionId);
    const list = JSON.parse(rects.result?.value || "[]");
    for (const p of list) {
      const shot = await send("Page.captureScreenshot", {
        format: "png", captureBeyondViewport: true,
        clip: { x: p.x, y: p.y, width: p.w, height: p.h, scale: 1 },
      }, sessionId);
      const out = `${shotPagesDir}/page${String(p.i).padStart(2, "0")}.png`;
      writeFileSync(out, Buffer.from(shot.data, "base64"));
      console.error(`逐页截图: ${out} (${Math.round(p.w * dpr)}x${Math.round(p.h * dpr)})`);
    }
    if (dpr !== 1) {
      await send("Emulation.clearDeviceMetricsOverride", {}, sessionId);
    }
  }

  if (shotPath) {
    const m = await send("Page.getLayoutMetrics", {}, sessionId);
    const cs = m.cssContentSize || m.contentSize;
    await send("Emulation.setDeviceMetricsOverride",
      { width, height: Math.min(Math.ceil(cs.height), 20000), deviceScaleFactor: 1, mobile: false },
      sessionId);
    await sleep(400);
    const shot = await send("Page.captureScreenshot",
      { format: "png", captureBeyondViewport: true }, sessionId);
    writeFileSync(shotPath, Buffer.from(shot.data, "base64"));
    console.error(`整页截图: ${shotPath} (${width}x${Math.ceil(cs.height)})`);
  }
} catch (e) {
  console.error("失败:", e.message);
  process.exitCode = 1;
} finally {
  try { ws?.close(); } catch {}
  child.kill();
}
