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


def layout_json_for(model: str) -> str:
    return LAYOUT_JSON["paddleocr-vl" if "paddle" in (model or "").lower() else "ovisocr2"]


def viewer_base_for(engine: str) -> str:
    return VIEWER_BASE["paddleocr-vl" if "paddle" in (engine or "").lower() else "ovisocr2"]
