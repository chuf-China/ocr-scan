#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ocr_common —— 两个流水线共用的底层能力：PDF 渲染、OCR 引擎调用、本地网页服务。

为什么单独一层：早先 `ocr_pdf_layout.py`（原版式）是**从 `ocr_pdf_to_json.py`（结构块）
里 import 的**，于是"扫描 PDF 的库"依赖了"搜索排序格式的程序"。两者只是共用渲染与 HTTP，
把这部分抽出来，任何一方都可以独立存在与测试。

对外边界：
    * `ocr_common`      —— 渲染 / OCR 调用 / 网页服务
    * `ocr_pipeline_blocks` —— 结构块流水线（题干、选项、表格，便于程序消费）
    * `ocr_pipeline_layout` —— 原版式流水线（逐行坐标 + 插图 + 表格裁片）
"""

from __future__ import annotations

import io
import json
import os
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 首选引擎：PaddleOCR-VL-1.6（端口 8080，官方前缀 "OCR:"）。
# 备选：OvisOCR2（端口 8081，整页解析指令）。选择理由见 README 的对比表。
DEFAULT_MODEL = "paddleocr-vl"
DEFAULT_HOST = "http://127.0.0.1:8080"
DEFAULT_DPI = 150
DEFAULT_TIMEOUT = 300
DEFAULT_MAX_TOKENS = 8192
DEFAULT_PORT = 8765

PADDLE_PROMPT = "OCR:"

# OvisOCR2 的整页解析指令（与 doc-ocr 技能保持一致）。
OCR_PROMPT = (
    "Extract all readable content from the image in natural human reading order and "
    "output the result as a single Markdown document. For charts or images, represent "
    'them using an HTML image tag: <img src="images/bbox_{left}_{top}_{right}_{bottom}.jpg" />, '
    "where left, top, right, bottom are bounding box coordinates scaled to [0, 1000). "
    "Format formulas as LaTeX. Format tables as HTML: <table>...</table>. "
    "Transcribe all other text as standard Markdown. "
    "Preserve the original text without translation or paraphrasing."
)


def default_prompt_for(model: str) -> str:
    """提示词按模型固定。

    PaddleOCR-VL 认官方任务前缀 `OCR:`（实测 0.1~0.3s/行）；给它自然语言长提示词会
    **退化**——实测重复输出 `<br>` 达 23 秒。OvisOCR2 则相反，用它的整页解析指令最稳。
    """
    return PADDLE_PROMPT if "paddle" in (model or "").lower() else OCR_PROMPT


def die(msg: str, code: int, prefix: str = "ocr") -> None:
    print(f"[{prefix}] 错误: {msg}", file=sys.stderr)
    raise SystemExit(code)


# ---------------------------------------------------------------- PDF 渲染

def import_pdfium():
    try:
        import pypdfium2  # noqa: PLC0415
    except ImportError:
        die("缺少 pypdfium2（PDF 渲染库）。安装：\n"
            "    python -m pip install pypdfium2 Pillow", 2)
    return pypdfium2


def render_page(doc, index: int, dpi: int) -> bytes:
    """把第 index 页（0 起）渲染成 PNG 字节。"""
    page = doc[index]
    bitmap = page.render(scale=dpi / 72)
    image = bitmap.to_pil().convert("RGB")
    buf = io.BytesIO()
    image.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def page_size(doc, index: int) -> tuple[int, int]:
    width, height = doc[index].get_size()
    return round(width), round(height)


# ---------------------------------------------------------------- OCR 调用

def ocr_png(host: str, model: str, png: bytes, prompt: str,
            timeout: int, max_tokens: int) -> dict:
    """调用 OpenAI 兼容的 /v1/chat/completions，返回 {text, finish_reason, usage}。"""
    import base64

    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": max_tokens,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,"
                                   + base64.b64encode(png).decode("ascii")
                        },
                    },
                ],
            }
        ],
    }
    req = urllib.request.Request(
        f"{host}/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer none"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"OCR 服务返回 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"连不上 OCR 服务 {host}：{exc.reason}。请先启动引擎：\n"
            f"    pwsh -NoProfile -File $HOME\\.dsh-llama\\start-ocr-server.ps1"
        ) from exc

    choices = data.get("choices") or []
    text = ((choices[0].get("message") or {}).get("content") if choices else "") or ""
    text = text.strip()
    if not text:
        raise RuntimeError("OCR 服务返回空结果（模型是否支持图像？mmproj 加载了吗？）")
    return {
        "text": text,
        "finish_reason": choices[0].get("finish_reason"),
        "usage": data.get("usage"),
    }


def check_health(host: str, timeout: int = 10) -> None:
    try:
        with urllib.request.urlopen(f"{host}/health", timeout=timeout) as resp:
            payload = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        die(f"OCR 引擎未就绪（{host}）：{exc}\n"
            f"  启动：pwsh -NoProfile -File $HOME\\.dsh-llama\\start-ocr-server.ps1 -Ctx 16384",
            2)
    if str(payload.get("status", "")).lower() not in ("ok", "ready"):
        print(f"[ocr] 警告：引擎 health 状态为 {payload.get('status')!r}", file=sys.stderr)


# ---------------------------------------------------------------- 本地网页服务

class ViewerHandler(BaseHTTPRequestHandler):
    """只读地托管 JSON、阅读器 HTML 与本地 vendor 静态资源。

    路由按**看护目录内的相对路径**解析。早先"任何以 .json 结尾的路径都返回同一份 JSON"，
    于是 `/paper.layout.paddle.json` 会静默返回 `/paper.layout.json` 的内容——
    两个引擎的对比页看起来"数值完全一样"，其实是同一份数据（已实测踩到，属静默错误结论）。
    """

    json_path = ""
    html_path = ""

    MIME = {
        ".html": "text/html; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".woff2": "font/woff2",
        ".woff": "font/woff",
        ".ttf": "font/ttf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".svg": "image/svg+xml",
    }

    def _send(self, path: str, ctype: str) -> None:
        try:
            with open(path, "rb") as fh:
                payload = fh.read()
        except OSError:
            self.send_error(404, "not found")
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _resolve(self, route: str) -> str | None:
        """把 URL 路径映射到看护目录内的文件；越界返回 None。"""
        import urllib.parse

        route = urllib.parse.unquote(route)
        base = os.path.dirname(os.path.abspath(self.html_path))
        target = os.path.abspath(os.path.join(base, route.lstrip("/")))
        if not (target == base or target.startswith(base + os.sep)):
            return None                      # 阻止 ../ 目录穿越
        return target if os.path.isfile(target) else ""

    def do_GET(self) -> None:  # noqa: N802
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html", "/viewer.html"):
            self._send(self.html_path, self.MIME[".html"])
            return
        target = self._resolve(route)
        if target is None:
            self.send_error(403, "forbidden")
            return
        if not target:
            self.send_error(404, "not found")
            return
        self._send(target, self.MIME.get(
            os.path.splitext(target)[1].lower(), "application/octet-stream"))

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("  [http] " + (fmt % args) + "\n")


def serve(json_path: str, html_path: str, port: int) -> None:
    ViewerHandler.json_path = json_path
    ViewerHandler.html_path = html_path
    httpd = ThreadingHTTPServer(("127.0.0.1", port), ViewerHandler)
    url = f"http://127.0.0.1:{port}/"
    print(f"[ocr] 网页阅读器已启动：{url}")
    print("[ocr] Ctrl+C 结束。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[ocr] 已停止。")
