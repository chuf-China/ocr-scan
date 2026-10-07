#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_viewers.py —— 为两份版式 JSON 各生成一份单文件渲染，并刷新对比页。

用法：python build_viewers.py
"""
from __future__ import annotations

import json
import os
import sys

# 工具在 tools/ 或 verify/ 下：把 ../src 加入 sys.path，才能 import 两个流水线与共享层
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
import paths  # noqa: E402
import ocr_pipeline_layout as M  # noqa: E402

PAIRS = [
    (paths.LAYOUT_JSON["ovisocr2"], paths.VIEWER_BASE["ovisocr2"]),
    (paths.LAYOUT_JSON["paddleocr-vl"], paths.VIEWER_BASE["paddleocr-vl"]),
]

def main() -> int:
    for path, base in PAIRS:
        if not os.path.isfile(path):
            print(f"跳过（不存在）：{path}")
            continue
        payload = json.load(open(path, encoding="utf-8"))
        full, slim = M.materialize_viewer(paths.OUT_DIR, payload, os.path.basename(path),
                                          base_name=base)
        doc = payload["document"]
        stats = payload["stats"]
        tables = sum(len(l.get("tables") or [])
                     for p in payload["pages"] for l in p["lines"])
        print(f"{base:28s} engine={doc['engine']:24s} 页={stats['pages']} "
              f"行={stats['lines']} 公式={stats['lines_with_math']} "
              f"图={stats['figures']} 表={tables}")
        print(f"   -> {full}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
