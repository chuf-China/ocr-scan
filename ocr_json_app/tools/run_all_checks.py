#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_all_checks.py —— 一键跑完全部校验（CI 入口）。

分三层，越靠下越接近"用户真正看到的东西"：

  ⓪ 代码层  smoke_pipelines  —— 真的 import 两个流水线跑纯函数
  ① 结构层  check_layout_json / check_ocr_json —— JSON 自身是否合法
  ② 渲染层  check_render —— 裁片是否压正文、行是否叠字、是否越界
            cdp_probe    —— 真浏览器里渲染出了多少节点、叠字/越界统计
  ③ 内容层  verify_math / verify_frontend —— 公式是否真能编译、前端断言

为什么要有 ⓪ 这一层：① ② ③ **全部只看已经生成好的产物**。实测漏过这种事故——
`ocr_pipeline_blocks.py` 里 `SECTION_RE` 从未定义（是函数里的第一个判断，必然 NameError），
而当时 9 项校验里有 **7 项通过，且这 7 项全是"产物校验"**——另外 2 项浏览器校验还因为
硬编码的 Chrome 路径失效而失败，**没有任何一项 import 过流水线代码**。
**只校验产物，永远发现不了代码已经坏了。**

用法：
    python tools/run_all_checks.py              # 全部
    python tools/run_all_checks.py --fast       # 跳过浏览器（②里的 cdp_probe）
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys

# 校验器自己 import src/paths.py 会写出 src/__pycache__，让 `git status` 平白多一条改动
# （仓库里还 tracked 过一个 .pyc）。校验工具不该产生副作用，所以关掉字节码写入。
sys.dont_write_bytecode = True

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BOOT)
import paths  # noqa: E402

ROOT = paths.PROJECT_ROOT
NODE = os.environ.get("NODE_BIN", "node")


def layout_artifacts() -> list[tuple[str, str]]:
    """所有版式产物（engine, path），**发现式**。

    为什么不能只写死两个预设引擎：新引擎的产物（如官方流程的
    `paper.layout.paddleocrvl16.json`）就永远进不了回归——文件在、却没人校验，
    与"只校验产物导致代码坏了也没人发现"是同一类盲区。
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key in ("paddleocr-vl", "ovisocr2"):
        path = paths.LAYOUT_JSON[key]
        if os.path.isfile(path):
            out.append((key, path))
            seen.add(os.path.abspath(path))
    for path in sorted(glob.glob(os.path.join(paths.OUT_DIR, "paper.layout*.json"))):
        if os.path.abspath(path) in seen:
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                engine = (json.load(fh).get("document") or {}).get("engine", "?")
        except (OSError, ValueError):
            engine = "?"
        out.append((engine, path))
    return out


def run(label: str, cmd: list[str], capture: bool = False) -> tuple[bool, str]:
    """跑一条校验。capture=True 时实时透传输出，同时返回文本供解析。"""
    print(f"\n{'=' * 78}\n### {label}\n{'=' * 78}")
    if capture:
        proc = subprocess.Popen(cmd, cwd=ROOT, text=True, encoding="utf-8",
                                errors="replace", stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        chunks: list[str] = []
        for line in proc.stdout or []:
            chunks.append(line)
            print(line, end="")
        proc.wait()
        output = "".join(chunks)
        ok = proc.returncode == 0
        print(f"--> {'通过' if ok else f'失败（exit {proc.returncode}）'}")
        return ok, output
    r = subprocess.run(cmd, cwd=ROOT, text=True, encoding="utf-8", errors="replace")
    ok = r.returncode == 0
    print(f"--> {'通过' if ok else f'失败（exit {r.returncode}）'}")
    return ok, ""


SKIP_RE = re.compile(r"^\s*skip\s+(.*)$")
SUMMARY_RE = re.compile(r"(\d+)\s*个通过\s*/\s*(\d+)\s*个失败(?:\s*/\s*(\d+)\s*个跳过)?")


def parse_browser_stats(output: str) -> dict | None:
    """从 cdp_probe 的输出里取出 --eval 的 JSON 结果。

    浏览器检查以前只看退出码；`overlap:1` 时 cdp_probe 仍然正常退出，门禁于是全绿。
    把 eval 结果解析出来，叠字/越界才真正能让 CI 失败。
    """
    m = re.search(r"(\{[\s\S]*\})\s*$", output)
    if not m:
        return None
    try:
        value = json.loads(m.group(1))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def run_browser(label: str, cmd: list[str]) -> tuple[bool, str]:
    ok, output = run(label, cmd, capture=True)
    stats = parse_browser_stats(output)
    if not stats:
        return False, "未取得浏览器几何数据"
    note_parts = []
    overlap = int(stats.get("overlap") or 0)
    overflow = int(stats.get("overflow") or 0)
    if overlap:
        note_parts.append(f"浏览器叠字 {overlap} 行")
    if overflow:
        note_parts.append(f"浏览器越界 {overflow} 行")
    if note_parts:
        # 官方 v0.1 的 line-only 产物（paddleocr-vl-1.6）不是本仓库的版式切分结果，
        # 其行盒高度由上游固定；这里记录警告，但不把上游既有瑕疵算进本仓库门禁。
        # llama.cpp 两条路线（本仓库输出）仍必须为 0，绝不放过。
        official_lines = str(stats.get("engine") or "").endswith("paddleocr-vl-1.6")
        if official_lines:
            note_parts.append("官方 line-only 产物，仅告警")
        else:
            ok = False
    return ok, "；".join(note_parts)


def run_smoke(py: str) -> tuple[bool, str]:
    """跑 ⓪ 层，并把"跳过了几项"摘要出来。

    为什么要单独处理：冒烟里有两组（22 项）依赖 numpy，缺 numpy 时会**跳过而不是失败**，
    退出码仍是 0。若只报"通过"，CI 机器缺 numpy 时等于代码层门禁悄悄缩水了一半，
    而汇总表上完全看不出来。
    """
    ok, output = run("[代码] 流水线冒烟", [py, "tools/smoke_pipelines.py"],
                     capture=True)
    skipped = [m.group(1).strip() for m in
               (SKIP_RE.match(line) for line in output.splitlines()) if m]
    m = SUMMARY_RE.search(output)
    note = ""
    if m:
        note = f"{m.group(1)} 通过 / {m.group(2)} 失败"
        if m.group(3):
            note += f" / {m.group(3)} 跳过"
    if skipped:
        note += f"（跳过：{'; '.join(skipped)}）"
    if note:
        print(f"    ↳ 冒烟明细：{note}")
    return ok, note


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fast", action="store_true", help="跳过需要浏览器的检查")
    args = ap.parse_args()

    py = sys.executable
    results: list[tuple[str, bool, str]] = []

    # ⓪ 代码层：真的 import 流水线跑纯函数。放在最前面——代码坏了，
    #    后面所有"产物校验"全绿也没有意义。
    ok, note = run_smoke(py)
    results.append(("[代码] 流水线冒烟", ok, note))

    def add(label: str, cmd: list[str]) -> None:
        ok, msg = run(label, cmd)
        results.append((label, ok, msg))

    for engine, path in layout_artifacts():
        add(f"[结构] 版式 JSON · {engine}",
            [py, "tools/check_layout_json.py", path])
        add(f"[渲染] 裁片/叠字/越界 · {engine}",
            [py, "tools/check_render.py", path])

    # 结构块产物也**发现式**：`paper.ocr.json` 与官方流程的
    # `paper.ocr.paddleocrvl16.json` 都该进回归，否则新产物又是"文件在、没人校验"。
    blocks_paths: list[str] = []
    if os.path.isfile(paths.BLOCKS_JSON):
        blocks_paths.append(paths.BLOCKS_JSON)
    for path in sorted(glob.glob(os.path.join(paths.OUT_DIR, "paper.ocr*.json"))):
        if os.path.abspath(path) not in {os.path.abspath(p) for p in blocks_paths}:
            blocks_paths.append(path)
    for path in blocks_paths:
        tag = os.path.basename(path)
        add(f"[结构] 结构块 JSON · {tag}",
            [py, "tools/check_ocr_json.py", path])
        add(f"[内容] 公式可编译 · {tag}",
            [NODE, "verify/verify_math.mjs", path,
             "vendor/katex/katex.min.js"])

    # 前端断言：viewer.html 的渲染脚本用最小 DOM 桩真跑一遍（默认数据是 paper.ocr.json）
    add("[内容] 前端断言",
        [NODE, "verify/verify_frontend.mjs",
         os.path.join(paths.OUT_DIR, "viewer.html"), paths.BLOCKS_JSON])

    # 浏览器实渲也**发现式**：所有已生成的 layout 阅读器都要过一遍真渲染，
    # 否则新引擎的阅读器只被"结构校验"覆盖，而结构校验看不到浏览器里的实际节点数。
    viewers: list[str] = []
    for base in (paths.VIEWER_BASE["paddleocr-vl"], paths.VIEWER_BASE["ovisocr2"]):
        viewers.append(base)
    for path in sorted(glob.glob(os.path.join(paths.OUT_DIR, "viewer_layout*.html"))):
        base = os.path.splitext(os.path.basename(path))[0]
        if base.endswith("_slim") or base in viewers:
            continue
        viewers.append(base)
    for base in viewers:
        html = os.path.join(paths.OUT_DIR, f"{base}.html")
        if not os.path.isfile(html):
            continue
        if args.fast:
            continue
        ok, note = run_browser(f"[渲染] 浏览器实渲 · {base}",
            [NODE, "tools/cdp_probe.mjs", html, "--width", "1000",
             "--settle", "3000", "--eval",
             "({pages:document.querySelectorAll('.page').length,"
             "failed:document.querySelectorAll('.empty').length,"
             "lines:document.querySelectorAll('.ln').length,"
             "figs:document.querySelectorAll('img.fig').length,"
             "katex:document.querySelectorAll('.katex').length,"
             "engine:DOC.document.engine,"
             "overlap:window.__OCR_DIAG__.overlapping_lines,"
             "overflow:window.__OCR_DIAG__.overflowing_lines})"])
        results.append((f"[渲染] 浏览器实渲 · {base}", ok, note))

    print(f"\n{'=' * 78}\n### 汇总\n{'=' * 78}")
    for label, ok, note in results:
        print(f"  {'✓' if ok else '✗'} {label}" + (f"  ·  {note}" if note else ""))
    bad = [label for label, ok, _ in results if not ok]
    print(f"\n{len(results) - len(bad)}/{len(results)} 通过")
    if any(note for _, _, note in results):
        print("（带明细的行说明该步内部的通过/跳过数，跳过不等于通过）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
