#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""measure_shift.py —— 口径①的**直接判据**：逐行测"渲染相对原版的位移"。

## 为什么用相位相关，而不是质心差
质心差会被**墨迹分布差异**污染：分式行里分子/分母/横线在原版与渲染版中占比不同，
质心自然会偏——实测页 1 第 3 行质心差 **12.36pt**，而相位相关给出的真实位移是
**-0.48pt**。渲染版用的字体（SimSun）与原版（HYShuSongErKW）笔画粗细不同，
质心法会把"像不像"误当成"位置对不对"。

相位相关（`skimage.registration.phase_cross_correlation` 的等价的 FFT 实现）
只找"整体平移多少最匹配"，与笔画粗细无关，是位置口径的正确判据。

输出：逐行位移（upx/dy）、中位/P90/P95、以及"≤3pt 的行占比"。
用法：
    python tools/measure_shift.py --render-dir out/render/final
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BOOT)
import paths  # noqa: E402

INK = 200


def line_boxes(gray: np.ndarray) -> list[tuple[int, int]]:
    """按行墨迹投影切出行区间（与项目 detect_lines 同一判据）。"""
    rows = (gray < INK).sum(axis=1)
    out, start = [], None
    for i, v in enumerate(rows > 0):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - 1)); start = None
    if start is not None:
        out.append((start, len(rows) - 1))
    return [(a, b) for a, b in out if b - a + 1 >= 4]


def phase_shift(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """a→b 的整体平移（像素）与归一化峰值（越高越可信）。"""
    f = np.fft.rfft2(a)
    g = np.fft.rfft2(b)
    R = f * np.conj(g)
    R /= (np.abs(R) + 1e-9)
    cc = np.fft.irfft2(R, s=a.shape)
    iy, ix = np.unravel_index(np.argmax(cc), cc.shape)
    if iy > a.shape[0] // 2:
        iy -= a.shape[0]
    if ix > a.shape[1] // 2:
        ix -= a.shape[1]
    peak = float(cc.max() / max(1e-9, a.sum() / a.size))
    return float(ix), float(iy), peak


def main() -> int:
    ap = argparse.ArgumentParser(description="逐行位移（位置口径的直接判据）")
    ap.add_argument("--pdf", default=paths.DEFAULT_PDF)
    ap.add_argument("--render-dir", default=os.path.join(paths.OUT_DIR, "render", "final"))
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--tol-pt", type=float, default=3.0)
    args = ap.parse_args()

    import pypdfium2 as pdfium
    from PIL import Image

    scale = args.dpi / 72.0
    doc = pdfium.PdfDocument(args.pdf)
    print(f"{'页':>3} {'行':>4} {'≤3pt':>6} {'占比':>8} {'中位pt':>8} {'P90':>7} {'P95':>7}")
    all_dev: list[float] = []
    weak = 0
    for pi in range(len(doc)):
        og = np.asarray(doc[pi].render(scale=scale).to_pil().convert("L"))
        rp = os.path.join(args.render_dir, f"page{pi + 1:02d}.png")
        if not os.path.isfile(rp):
            print(f"{pi + 1:>3}  (缺渲染版)")
            continue
        rg = np.asarray(Image.open(rp).convert("L"))
        if rg.shape != og.shape:
            rg = np.asarray(Image.fromarray(rg).resize((og.shape[1], og.shape[0]),
                                                       Image.LANCZOS))
        devs = []
        # 用**原版**的行切分作为基准行（原版是基准，渲染版去对齐它）
        for a, b in line_boxes(og):
            # 上下各留 6px 余量，让相位相关有搜索空间（否则真位移会被裁掉）
            a2, b2 = max(0, a - 6), min(og.shape[0], b + 7)
            band_o = (og[a2:b2] < INK).astype(float)
            band_r = (rg[a2:b2] < INK).astype(float)
            if band_o.sum() < 20 or band_r.sum() < 20:
                continue
            ix, iy, peak = phase_shift(band_o, band_r)
            # 峰值过低说明"这一行根本没对上"，不计入位移统计（单独报）
            if peak < 0.05:
                weak += 1
                continue
            devs.append((ix * ix + iy * iy) ** 0.5 / scale)
        if not devs:
            continue
        d = sorted(devs)
        n_ok = sum(1 for v in d if v <= args.tol_pt)
        all_dev.extend(d)
        print(f"{pi + 1:>3} {len(d):>4} {n_ok:>6} {n_ok/len(d):>7.1%} "
              f"{d[len(d)//2]:>8.2f} {d[int(len(d)*0.9)]:>7.2f} {d[int(len(d)*0.95)]:>7.2f}")

    if all_dev:
        d = sorted(all_dev)
        n_ok = sum(1 for v in d if v <= args.tol_pt)
        print(f"\n全卷 {len(d)} 行（另 {weak} 行因匹配峰值过低未计入）：**位移 ≤{args.tol_pt}pt 的占 {n_ok/len(d):.2%}**"
              f"（目标 99%）")
        print(f"位移 中位 {d[len(d)//2]:.2f}pt  P90 {d[int(len(d)*0.9)]:.2f}pt  "
              f"P95 {d[int(len(d)*0.95)]:.2f}pt  最大 {d[-1]:.2f}pt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
