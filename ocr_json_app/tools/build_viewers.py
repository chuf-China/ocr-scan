#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_viewers.py —— 为 out/ 下的**每一份**版式 JSON 生成单文件渲染，并刷新对比页。

为什么改成"发现式"：早先这里硬编码两条（ovisocr2 / paddleocr-vl），于是新引擎的产物
（如官方流程的 `paper.layout.paddleocrvl16.json`）**不会生成阅读器**——文件在，
却打不开，而且不报错。现在扫描 `out/paper.layout*.json`，阅读器名由
`paths.viewer_base_for(document.engine)` 推导。

用法：python build_viewers.py
"""
from __future__ import annotations

import glob
import json
import os
import sys

# 工具在 tools/ 或 verify/ 下：把 ../src 加入 sys.path，才能 import 两个流水线与共享层
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
import paths  # noqa: E402
import ocr_pipeline_layout as M  # noqa: E402


def discover() -> list[tuple[str, str]]:
    """扫出所有版式 JSON，并推回它该用的阅读器名。

    两个预设产物（ovisocr2 / paddleocr-vl）优先排前面——对比页与校验器依赖它们的顺序。

    ⚠️ 名称必须**可推导**，不能靠"生成时另外起一个名字"：早先转换器用 `--out
    paper.layout.paddleocrvl16.json`，而这里按引擎推导出 `viewer_layout_paddleocr-vl-1-6`，
    结果 HTML 里的数据文件名与实际 JSON 对不上，页面**静默渲染成 0 页**（浏览器没有
    任何报错）。所以规则是：先读 JSON 里 `document.viewer_base`（生成方写入），
    没有再退回按引擎名推导。
    """
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in ("ovisocr2", "paddleocr-vl"):
        path = paths.LAYOUT_JSON[key]
        if os.path.isfile(path):
            pairs.append((path, paths.VIEWER_BASE[key]))
            seen.add(os.path.abspath(path))
    for path in sorted(glob.glob(os.path.join(paths.OUT_DIR, "paper.layout*.json"))):
        if os.path.abspath(path) in seen:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh).get("document") or {}
        except (OSError, ValueError):
            continue
        base = doc.get("viewer_base") or paths.viewer_base_for(doc.get("engine", ""))
        pairs.append((path, base))
    return pairs


def main() -> int:
    pairs = discover()
    if not pairs:
        print("没有找到任何 paper.layout*.json")
        return 1
    for path, base in pairs:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        full, slim = M.materialize_viewer(paths.OUT_DIR, payload, os.path.basename(path),
                                          base_name=base)
        doc = payload["document"]
        stats = payload["stats"]
        tables = sum(len(l.get("tables") or [])
                     for p in payload["pages"] for l in p["lines"])
        print(f"{base:32s} engine={doc['engine']:24s} 页={stats['pages']} "
              f"行={stats['lines']} 公式={stats['lines_with_math']} "
              f"图={stats['figures']} 表={tables}")
        print(f"   -> {full}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
