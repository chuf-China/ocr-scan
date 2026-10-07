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
import { writeFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

const args = process.argv.slice(2);
const target = args[0];
if (!target) { console.error("用法: node cdp_probe.mjs <htmlPath|url> [--shot p.png]"); process.exit(1); }
const opt = (name, dflt) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : dflt;
};
const shotPath = opt("--shot", null);
const evalExpr = opt("--eval", "window.__OCR_DIAG__");
const showConsole = args.includes("--console");
const width = Number(opt("--width", 1000));
const chromePath = opt("--chrome",
  `${process.env.LOCALAPPDATA}\\ms-playwright\\chromium_headless_shell-1234\\chrome-headless-shell-win64\\chrome-headless-shell.exe`);
const port = Number(opt("--port", 9333));

const url = /^https?:|^file:/.test(target) ? target : pathToFileURL(target).href;

const child = spawn(chromePath, [
  "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${process.env.TEMP}\\cdp-probe-${Date.now()}`,
  `--window-size=${width},1400`,
  "about:blank",
], { stdio: "ignore" });

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function getWsUrl() {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (r.ok) return (await r.json()).webSocketDebuggerUrl;
    } catch { /* 还没起来 */ }
    await sleep(150);
  }
  throw new Error("Chrome 调试端口未就绪");
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
