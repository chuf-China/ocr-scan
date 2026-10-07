#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_all_checks.py —— 一键跑完全部校验（CI 入口）。

分三层，越靠下越接近"用户真正看到的东西"：

  ① 结构层  check_layout_json / check_ocr_json —— JSON 自身是否合法
  ② 渲染层  check_render —— 裁片是否压正文、行是否叠字、是否越界
            cdp_probe    —— 真浏览器里渲染出了多少节点、叠字/越界统计
  ③ 内容层  verify_math / verify_frontend —— 公式是否真能编译、前端断言

为什么要有这一层：只看某一层会漏。实测"插图裁片盖住题干"时，①通过、③通过，
行与行的重叠统计也还是 0（裁片不是 `.ln`，不在统计口径里）——只有 ② 的
`check_render` 才抓得到。

用法：
    python tools/run_all_checks.py              # 全部
    python tools/run_all_checks.py --fast       # 跳过浏览器（②里的 cdp_probe）
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BOOT)
import paths  # noqa: E402

ROOT = paths.PROJECT_ROOT
NODE = os.environ.get("NODE_BIN", "node")


def run(label: str, cmd: list[str]) -> bool:
    print(f"\n{'=' * 78}\n### {label}\n{'=' * 78}")
    r = subprocess.run(cmd, cwd=ROOT, text=True, encoding="utf-8", errors="replace")
    ok = r.returncode == 0
    print(f"--> {'通过' if ok else f'失败（exit {r.returncode}）'}")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="跳过需要浏览器的检查")
    args = ap.parse_args()

    py = sys.executable
    results: list[tuple[str, bool]] = []

    for engine, path in (("paddleocr-vl", paths.LAYOUT_JSON["paddleocr-vl"]),
                         ("ovisocr2", paths.LAYOUT_JSON["ovisocr2"])):
        if not os.path.isfile(path):
            print(f"跳过（未生成）：{path}")
            continue
        results.append((f"[结构] 版式 JSON · {engine}",
                        run(f"[结构] 版式 JSON · {engine}",
                            [py, "tools/check_layout_json.py", path])))
        results.append((f"[渲染] 裁片/叠字/越界 · {engine}",
                        run(f"[渲染] 裁片/叠字/越界 · {engine}",
                            [py, "tools/check_render.py", path])))

    if os.path.isfile(paths.BLOCKS_JSON):
        results.append(("[结构] 结构块 JSON",
                        run("[结构] 结构块 JSON",
                            [py, "tools/check_ocr_json.py", paths.BLOCKS_JSON])))
        results.append(("[内容] 公式可编译",
                        run("[内容] 公式可编译",
                            [NODE, "verify/verify_math.mjs", paths.BLOCKS_JSON,
                             "vendor/katex/katex.min.js"])))
        results.append(("[内容] 前端断言",
                        run("[内容] 前端断言",
                            [NODE, "verify/verify_frontend.mjs",
                             os.path.join(paths.OUT_DIR, "viewer.html"),
                             paths.BLOCKS_JSON])))

    for base in (paths.VIEWER_BASE["paddleocr-vl"], paths.VIEWER_BASE["ovisocr2"]):
        html = os.path.join(paths.OUT_DIR, f"{base}.html")
        if not os.path.isfile(html):
            continue
        if args.fast:
            continue
        results.append((f"[渲染] 浏览器实渲 · {base}",
                        run(f"[渲染] 浏览器实渲 · {base}",
                            [NODE, "tools/cdp_probe.mjs", html, "--width", "1000",
                             "--settle", "3000", "--eval",
                             "({pages:document.querySelectorAll('.page').length,"
                             "failed:document.querySelectorAll('.empty').length,"
                             "lines:document.querySelectorAll('.ln').length,"
                             "figs:document.querySelectorAll('img.fig').length,"
                             "katex:document.querySelectorAll('.katex').length,"
                             "overlap:window.__OCR_DIAG__.overlapping_lines,"
                             "overflow:window.__OCR_DIAG__.overflowing_lines})"])))

    print(f"\n{'=' * 78}\n### 汇总\n{'=' * 78}")
    for label, ok in results:
        print(f"  {'✓' if ok else '✗'} {label}")
    bad = [l for l, ok in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} 通过")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
