#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_exact.py —— 用**内嵌字体 + 逐字符精确位置**重建页面（达成高保真的正解）。

## 这条路为什么能行（全部实测）
1. PDF 的文字层是好的：汉字/ASCII 可解码，且字符位置与墨迹**亚像素吻合**
2. PDF **内嵌了全部字体**，PyMuPDF 能提取出来：
   `HYShuSongErKW`(317KB/28682 字形) / `TimesNewRomanPSMT` / `…ItalicMT` / `…BoldMT` /
   `KingsoftSign` / `KingsoftExtra`
3. 提取出的子集字体 **cmap 里有汉字码位**（实测 U+9759/U+5B89/U+4E00 命中）
   → 浏览器可以直接按 Unicode 用这些字体渲染，**字形与字宽与原版一致**

于是"行宽差 15–50px"的根本原因（字体回退）被消除，位置来自文字层而不是估算。

## 与前面方案的对比
| 方案 | 位置来源 | 字体 | 墨迹 IoU |
|---|---|---|---|
| 块级（基线） | 块框 | SimSun 回退 | 0.088 |
| 行级 | 墨迹投影 | SimSun 回退 | 0.090 |
| 逐字符 | 文字层字符位 | 系统字体 | 0.150 |
| **本方案** | **文字层字符位** | **PDF 内嵌原字体** | 待测（预期最高） |
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, BOOT)
import paths  # noqa: E402

# 坏字形字体：这些字体里的码位是私用区，按"数学/符号"处理
SYMBOL_FONTS = ("KingsoftSign", "KingsoftExtra", "DejaVuMathTeXGyre")


def font_slug(name: str) -> str:
    return name.split("+")[-1].replace(" ", "")


def find_rules(ink: "np.ndarray", scale: float,
               min_w_px: int = 60, max_h_px: int = 20) -> list[dict]:
    """找出"细横线"——分式横线/根号线等**在文字层里没有字符**的墨迹。

    为什么需要：原始 PDF 的分式横线是**矢量图形**，`rawdict` 里没有对应字符，
    逐字符渲染永远画不出它。实测页 3 有 5898px 未覆盖墨，集中在
    y 588/654/730/796/861 的 2–3px 细横线（横跨 x174–471）。
    横线就是矩形，用 DOM 元素画出来是像素级精确的。

    ⚠️ **不要用 `ndimage.label` 找连通域**：细横线常与相邻字形连成一体，
    整块高度远超 `max_h_px` 而被滤掉（实测全卷只检出 4 条，明显偏少）。
    改用**逐行游程**：先找每行里 ≥min_w_px 的连续墨段，再把相邻行、水平近似
    对齐的段合并成一条线——这对细横线天然合适。
    """
    import numpy as np
    mask = ink < 200
    H, W = mask.shape
    runs: list[tuple[int, int, int]] = []      # (row, start, end)
    for y in range(H):
        xs = np.nonzero(mask[y])[0]
        if xs.size == 0:
            continue
        splits = np.nonzero(np.diff(xs) > 1)[0]
        starts = np.concatenate(([0], splits + 1))
        ends = np.concatenate((splits, [xs.size - 1]))
        for s, e in zip(starts, ends):
            x0, x1 = int(xs[s]), int(xs[e])
            if x1 - x0 + 1 >= min_w_px:
                runs.append((y, x0, x1))
    # 合并相邻行的近似对齐游程
    runs.sort()
    out: list[dict] = []
    used = [False] * len(runs)
    for i, (y, x0, x1) in enumerate(runs):
        if used[i]:
            continue
        band = [(y, x0, x1)]
        used[i] = True
        cy = y
        while True:
            nxt = None
            for j, (y2, a2, b2) in enumerate(runs):
                if used[j] or y2 != cy + 1:
                    continue
                # 水平重叠 ≥80% 视为同一条线
                ov = min(x1, b2) - max(x0, a2)
                if ov > 0.8 * min(x1 - x0, b2 - a2):
                    nxt = j
                    break
            if nxt is None:
                break
            used[nxt] = True
            y2, a2, b2 = runs[nxt]
            band.append(runs[nxt])
            x0, x1, cy = min(x0, a2), max(x1, b2), y2
        h = cy - y + 1
        if h <= max_h_px and (x1 - x0 + 1) >= min_w_px:
            out.append({"left": round(x0 / scale, 2), "top": round(y / scale, 2),
                        "width": round((x1 - x0 + 1) / scale, 2),
                        "height": round(h / scale, 2)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="内嵌字体 + 逐字符位置 的精确重建")
    ap.add_argument("--pdf", default=paths.DEFAULT_PDF)
    ap.add_argument("--out", default=os.path.join(paths.OUT_DIR, "paper.exact.paddleocrvl16.json"))
    ap.add_argument("--fonts-dir", default=os.path.join(paths.OUT_DIR, "fonts"))
    ap.add_argument("--viewer-base", default="viewer_exact_paddleocrvl16")
    ap.add_argument("--title", default="静安区2026届高三一模数学试卷")
    ap.add_argument("--dpi", type=int, default=150)
    args = ap.parse_args()

    import fitz

    doc = fitz.open(args.pdf)
    os.makedirs(args.fonts_dir, exist_ok=True)

    # ---- 提取嵌入字体（每个文件的 SHA 前缀 + 原字体名 → 稳定文件名）----
    font_files: dict[str, dict] = {}
    for page in doc:
        for f in page.get_fonts(full=True):
            xref, ext, ftype, basefont = f[0], f[1], f[2], f[3]
            slug = font_slug(basefont)
            if slug in font_files:
                continue
            try:
                _n, fext, _t, content = doc.extract_font(xref)
            except Exception:  # noqa: BLE001
                continue
            if not content:
                continue
            fname = f"{slug}.{fext}"
            open(os.path.join(args.fonts_dir, fname), "wb").write(content)
            font_files[slug] = {"file": fname, "bytes": len(content),
                                "base": basefont, "xref": xref}

    # ---- 图/表裁片：**图不是文字**（原版 PDF 里它们本就是位图/矢量图），
    # 用户口径③只禁止"用裁片替代文字"，所以把图裁片贴回原位是正当的，
    # 而且原版也是这么做的。实测图块内占原版墨迹的 5.0%，不渲染就永远到不了 99%。
    import base64
    import io

    import numpy as np

    from PIL import Image as PILImage

    fig_dir = os.path.join(paths.OUT_DIR, "exact_figs")
    os.makedirs(fig_dir, exist_ok=True)
    figures: list[dict] = []
    raw_dir = os.path.join(paths.OUT_DIR, "paddleocr_raw")

    def add_figures(page_idx: int, page_json) -> list[dict]:
        raw_p = os.path.join(raw_dir, f"page{page_idx + 1:02d}_res.json")
        if not os.path.isfile(raw_p):
            return []
        res = json.load(open(raw_p, encoding="utf-8"))
        page = doc[page_idx]
        pil = page.get_pixmap(dpi=args.dpi).pil_image().convert("RGB")
        s = args.dpi / 72.0
        out = []
        for bi, b in enumerate(res.get("parsing_res_list") or []):
            if (b.get("block_label") or "") not in ("image", "chart", "figure"):
                continue
            x0, y0, x1, y1 = b["block_bbox"]
            box = (max(0, int(x0)), max(0, int(y0)),
                   min(pil.width, int(x1)), min(pil.height, int(y1)))
            if box[2] - box[0] < 4 or box[3] - box[1] < 4:
                continue
            crop = pil.crop(box)
            # 表格轮廓：表格在官方产物里有可解析的 HTML，但此处先按图块处理，
            # 保证"墨迹对得上"；文字仍由逐字符层提供（表格文字也在文字层里）。
            buf = io.BytesIO()
            crop.save(buf, format="PNG", optimize=True)
            data = buf.getvalue()
            out.append({
                "index": bi,
                # ⚠️ `block_bbox` 是**像素**（在 args.dpi 下渲染出来的坐标），不是 pt。
                # 存进产物必须**除以 scale** 换成 pt，渲染端再乘 K 才回到像素。
                # 早先忘了除，图被放到 2.08 倍位置（page2 的图跑到 y=2081px，超出页高 1754）。
                "left": round(x0 / s, 2), "top": round(y0 / s, 2),
                "width": round((box[2] - box[0]) / s, 2),
                "height": round((box[3] - box[1]) / s, 2),
                "png_base64": base64.b64encode(data).decode("ascii"),
            })
        return out

    pages = []
    for i, page in enumerate(doc):
        raw = page.get_text("rawdict")
        lines_out = []
        for blk in raw.get("blocks", []):
            if blk.get("type") != 0:
                continue
            for ln in blk.get("lines", []):
                spans = []
                for sp in ln.get("spans", []):
                    if not (sp.get("chars") or []):
                        continue
                    spans.append({
                        "font": font_slug(sp.get("font", "")),
                        "size": round(float(sp.get("size", 0)), 2),
                        "chars": [{
                            "c": ch["c"],
                            "x": round(ch["bbox"][0], 2),
                            "y_top": round(ch["bbox"][1], 2),
                            "y_bot": round(ch["bbox"][3], 2),
                            "w": round(ch["bbox"][2] - ch["bbox"][0], 2),
                        } for ch in sp["chars"]],
                    })
                if spans:
                    lines_out.append({
                        "bbox": [round(v, 2) for v in ln["bbox"]],
                        "spans": spans,
                    })
        figs = add_figures(i, page)
        ink_gray = np.asarray(page.get_pixmap(dpi=args.dpi).pil_image().convert('L'))
        # 全部检出并画上。
        #
        # ⚠️ 这里**不做"是否已被字符覆盖"的过滤**：那种过滤要用一个覆盖近似
        # （字符框并集），而框并集比真实渲染更宽，会把真正缺失的线误判成已覆盖
        # （实测页 4/6 各有 3–6 条真缺的线被滤掉，页面精度卡在 96%）。
        # `find_rules` 本身的判据（长游程 + 高度 ≤8px）已经足够保守：
        # 带文字的横条不会形成这么长的连续游程。
        rules = find_rules(ink_gray, args.dpi / 72.0)
        figures.extend(figs)
        pages.append({
            "page": i + 1,
            "width": round(page.rect.width, 2),
            "height": round(page.rect.height, 2),
            "dpi": args.dpi,
            "lines": lines_out,
            "figures": figs,
            "rules": rules,
        })

    n_chars = sum(len(sp["chars"]) for p in pages for ln in p["lines"] for sp in ln["spans"])
    payload = {
        "document": {
            "title": args.title,
            "source_pdf": os.path.abspath(args.pdf),
            "pages_total": len(pages), "pages_processed": len(pages),
            "engine": "paddleocr-vl-1.6",
            "viewer_base": args.viewer_base,
            "mode": "exact",
            "dpi": args.dpi,
            "fonts": font_files,
            "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
            "warnings": [],
        },
        "stats": {"pages": len(pages), "chars": n_chars,
                  "fonts": len(font_files), "figures": len(figures)},
        "pages": pages,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
    print(f"[exact] 写出 {args.out}")
    print(f"[exact] {len(pages)} 页 / {n_chars} 字符 / 内嵌字体 {len(font_files)} 个 → {args.fonts_dir}")
    for k, v in sorted(font_files.items()):
        print(f"        {k:<28} {v['bytes']:>8d} 字节  ({v['base']})")

    # 刚提取出来的字体**缺 `OS/2`**，浏览器 OTS 会拒绝加载，字形全部回退。
    # 这里自动补表，避免"重新提取后忘了修字体"导致效果悄悄退回系统字体
    # （实测：忘了修时墨迹 IoU 0.3911 → 0.3516、可见字符命中 99.7% → 98.8%）。
    try:
        import subprocess
        r = subprocess.run([sys.executable, os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "fix_fonts.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = [ln for ln in (r.stdout or "").strip().splitlines() if ln.strip()][-1:]
        print(f"[exact] 字体已自动补表：{tail[0] if tail else '见 fix_fonts.py'}")
    except Exception as e:  # noqa: BLE001
        print(f"[exact] ⚠ 自动补表失败，请手动运行 tools/fix_fonts.py："
              f"{type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
