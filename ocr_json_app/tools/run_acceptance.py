#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_acceptance.py —— 三项口径的**一键复跑**验收。

把"渲染 → 截图 → 三项指标 + 参考指标"整条链固化下来，避免证据只能靠手工复现。
默认复用已有截图（快）；`--recapture` 会重新截图。

用法：
    python tools/run_acceptance.py                 # 复用 out/render/exact6 的截图
    python tools/run_acceptance.py --recapture     # 重新截图再测
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BOOT)
import paths  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIEWER = os.path.join(paths.OUT_DIR, "viewer_exact_paddleocrvl16.html")
SHOT_DIR = os.path.join(paths.OUT_DIR, "render", "origfont")
TEXT_DIR = os.path.join(paths.OUT_DIR, "render", "exact16", "text")


def run(label: str, cmd: list[str]) -> tuple[bool, str]:
    print(f"\n{'=' * 78}\n### {label}\n{'=' * 78}")
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    print(out.rstrip())
    return p.returncode == 0, out


def main() -> int:
    ap = argparse.ArgumentParser(description="三项口径一键验收")
    ap.add_argument("--recapture", action="store_true", help="重新截图")
    ap.add_argument("--port", type=int, default=9930)
    args = ap.parse_args()

    steps: list[tuple[str, bool]] = []

    # 0) 前置：字体必须已补 OS/2。
    #    缺 OS/2 时浏览器 OTS 会拒绝加载**整个字体**，字形悄悄回退到系统字体
    #    （实测：墨迹 IoU 0.3911→0.3516、可见字符命中 99.7%→98.8%），
    #    而且**不会有任何报错**——所以必须作为前置校验卡住。
    ok, out = run("前置：字体表完整性（缺 OS/2 会被浏览器拒绝）",
                  [sys.executable, "tools/fix_fonts.py", "--check"])
    # 判据：fix_fonts --check 在**有字体缺 OS/2** 时返回非 0（致命），
    # 只有可容忍的其它缺表（如 CFF 字体无 glyf/loca）时返回 0。
    steps.append(("前置：没有字体缺 OS/2（致命表）", ok))

    # 1) 数据 → 阅读器（顺带写出渲染版文本，供口径②用）
    ok, _ = run("生成精确重建阅读器", ["node", "tools/build_exact_viewer.mjs"])
    steps.append(("生成阅读器", ok))

    if args.recapture:
        shutil.rmtree(SHOT_DIR, ignore_errors=True)
        ok, _ = run("逐页截图（DPR=1，CSS px = pt × dpi/72）",
                    ["node", "tools/cdp_probe.mjs", VIEWER, "--width", "1000",
                     "--settle", "12000", "--dpr", "1", "--port", str(args.port),
                     "--shot-pages", SHOT_DIR, "--eval", "1"])
        steps.append(("逐页截图", ok))

    if not os.path.isdir(SHOT_DIR):
        print(f"\n缺截图目录 {SHOT_DIR}；请加 --recapture 重跑")
        return 1

    # 2) 口径①：行位置（相位相关位移）
    ok, out = run("口径① 行位置（相位相关逐行位移）",
                  [sys.executable, "tools/measure_shift.py", "--render-dir", SHOT_DIR])
    p1 = "100.00%" in out or "占 100.00%" in out
    steps.append(("① 行位置 ≤3pt 占 100%", p1 and ok))

    # 3) 口径②：文字一致率
    ok, out = run("口径② 文字一致率（字符多重集）",
                  [sys.executable, "tools/score_text.py", "--render-text", TEXT_DIR])
    p2 = "100.00%" in out and "差异字符 0" in out
    steps.append(("② 文字一致率 100%", p2 and ok))

    # 3b) 口径③：没有用裁片替代文字
    ok, out = run("口径③ 无裁片替代文字（文本节点 + 图裁片不覆盖字符）",
                  [sys.executable, "tools/check_no_image_text.py"])
    steps.append(("③ 无裁片替代文字", ok and "✓ 口径③ 通过" in out))

    # 4) 参考指标
    run("参考：墨迹 IoU / 邻域位置精度",
        [sys.executable, "tools/score_fidelity.py", "--render-dir", SHOT_DIR, "--dpi", "150"])
    run("参考：字形命中（四分类）",
        [sys.executable, "tools/check_exact.py", "--render-dir", SHOT_DIR])

    print(f"\n{'=' * 78}\n### 验收结论\n{'=' * 78}")
    for name, good in steps:
        print(f"  {'✓' if good else '✗'} {name}")
    allok = all(g for _, g in steps)
    print(f"\n{'全部通过' if allok else '未全部通过'}")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
