#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""paths —— 统一的路径解析。

**为什么需要**：早先脚本里写死了 `data/静安区…pdf`、`out/paper.layout.json` 这类相对路径，
于是**必须在项目根目录下执行**；从别处调用（`python tools/build_viewers.py`、在其他 cwd 里
跑 CI）就会找不到文件。这里把"项目根"定义为**本文件的上上层**，所有默认路径都基于它推导，
与当前工作目录无关。
"""

from __future__ import annotations

import os
import re

# src/paths.py → src → 项目根
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
OUT_DIR = os.path.join(PROJECT_ROOT, "out")
VENDOR_DIR = os.path.join(PROJECT_ROOT, "vendor")
VIEWER_DIR = os.path.join(PROJECT_ROOT, "layout_viewer")
SRC_DIR = os.path.join(PROJECT_ROOT, "src")

# 本项目的样例试卷（换文档时改这里，或给工具传 --pdf）
DEFAULT_PDF = os.path.join(DATA_DIR, "静安区2026届高三一模数学试卷.pdf")

# 两份版式产物：引擎不同，**必须分文件**（同名会互相覆盖渲染，
# 于是"两份 JSON 只有一份能看"——实测踩过）
LAYOUT_JSON = {
    "paddleocr-vl": os.path.join(OUT_DIR, "paper.layout.paddle.json"),
    "ovisocr2": os.path.join(OUT_DIR, "paper.layout.json"),
}
# 每个引擎对应的阅读器文件名
VIEWER_BASE = {
    "paddleocr-vl": "viewer_layout_paddleocrvl",
    "ovisocr2": "viewer_layout",
}
BLOCKS_JSON = os.path.join(OUT_DIR, "paper.ocr.json")
COMPARE_DIR = os.path.join(OUT_DIR, "compare")


def _slug(text: str) -> str:
    """把 `llama.cpp/paddleocr-vl` 这类 engine 串变成文件名安全的短标识。

    `llama.cpp/` 前缀是本项目的固定约定，先剥掉再归一化，否则每个 slug 都会拖着
    `llamacpp-` 这么一截。
    """
    text = (text or "").strip()
    if text.lower().startswith("llama.cpp/"):
        text = text.split("/", 1)[1]
    text = text.lower().replace(":", "-")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def _engine_key(engine: str) -> str | None:
    """把 engine 串归一化成预设的两个 key，认不出就返回 None。

    ⚠️ `paddleocr-vl` 必须**严格相等**才算命中。早先这里写的是
    `slug.startswith("paddleocr-vl-")`，于是官方 1.6 流程（engine 名
    `paddleocr-vl-1.6`）会落到 `paper.layout.paddle.json`——把 llama.cpp 时代
    `paddleocr-vl` 的产物覆盖掉。两个引擎只是名字像，产物不能互相覆盖。
    """
    slug = _slug(engine)
    if slug == "ovisocr2":
        return "ovisocr2"
    if slug == "paddleocr-vl":
        return "paddleocr-vl"
    return None


def layout_json_for(model: str) -> str:
    """按模型/引擎挑版式 JSON 的默认输出路径。

    ⚠️ 认不出的引擎**单独命名**，不能回落到 ovisocr2：早先这里是
    `"paddle" in model else ovisocr2`，于是任何既不含 "paddle" 的模型（如
    `qwen3-vl:8b`、`AuditAid/PaddleOCR-VL-1.6-0.9B`）都会静默写进
    `paper.layout.json`，把已有的 OvisOCR2 产物覆盖掉——而 `run_all_checks.py`
    还在拿那份产物做校验，覆盖后校验"通过"但内容已经换了引擎。
    """
    key = _engine_key(model)
    if key:
        return LAYOUT_JSON[key]
    return os.path.join(OUT_DIR, f"paper.layout.{_slug(model) or 'unknown'}.json")


def viewer_base_for(engine: str) -> str:
    key = _engine_key(engine)
    if key:
        return VIEWER_BASE[key]
    return f"viewer_layout_{_slug(engine) or 'unknown'}"
