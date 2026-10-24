#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_fonts.py —— 修复从 PDF 提取的字体，使其能被浏览器（OTS）接受。

## 为什么必须修
PDF 里内嵌的子集字体常缺 `OS/2` 与 `post` 表。Chrome 的 OTS 校验可能因此
**拒绝加载整个字体**，表现为"@font-face 明明写了、文件也能取到，但字形全部回退"。

## 关键：不要手搓表对象
早先版本直接 `os2.panose = [0] * 10`、`post.glyphOrder = [...]`，
把 fontTools 的**对象字段**替换成了普通 list，导致
`AttributeError: 'list' object has no attribute '__dict__'`（保存失败）。
正确做法是用 fontTools 自己的构造器：`Panose()`、`newTable()`，并先
`tt.getGlyphOrder()` 触发字形序列表解压。

## 用法
    python tools/fix_fonts.py            # 就地修复 out/fonts/*.ttf（原件备份到 raw/）
    python tools/fix_fonts.py --check    # 只报告每个字体缺哪些表，不改文件
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

# 浏览器（OTS/FreeType）对 TrueType 的常规要求
REQUIRED = ("head", "hhea", "hmtx", "cmap", "glyf", "loca", "maxp", "name")
COMMONLY_EXPECTED = ("post", "OS/2")


def add_os2(tt) -> None:
    """按 head/hhea 推导一个最小可用的 OS/2 v4。

    ⚠️ `panose` 必须用 `Panose()` 对象赋给 `tt["OS/2"].panose`，
    **不能**直接 `os2.panose = [0]*10`——那会让 fontTools 在保存时抛
    `AttributeError: 'list' object has no attribute '__dict__'`。
    """
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables.O_S_2f_2 import Panose
    head, hhea = tt["head"], tt["hhea"]
    os2 = newTable("OS/2")
    os2.version = 4
    os2.xAvgCharWidth = max(1, head.unitsPerEm // 2)
    os2.usWeightClass = 400
    os2.usWidthClass = 5
    os2.fsType = 0
    os2.ySubscriptXSize = head.unitsPerEm // 2
    os2.ySubscriptYSize = head.unitsPerEm // 2
    os2.ySubscriptXOffset = 0
    os2.ySubscriptYOffset = head.unitsPerEm // 5
    os2.ySuperscriptXSize = head.unitsPerEm // 2
    os2.ySuperscriptYSize = head.unitsPerEm // 2
    os2.ySuperscriptXOffset = 0
    os2.ySuperscriptYOffset = head.unitsPerEm // 3
    os2.yStrikeoutSize = max(1, head.unitsPerEm // 20)
    os2.yStrikeoutPosition = head.unitsPerEm // 4
    os2.sFamilyClass = 0
    os2.panose = Panose()                     # ← 必须是对象
    os2.ulUnicodeRange1 = 0xFFFFFFFF
    os2.ulUnicodeRange2 = 0xFFFFFFFF
    os2.ulUnicodeRange3 = 0xFFFFFFFF
    os2.ulUnicodeRange4 = 0xFFFFFFFF
    os2.achVendID = "NONE"
    os2.fsSelection = 0x40
    os2.usFirstCharIndex = 0x20
    os2.usLastCharIndex = 0xFFFF
    os2.sTypoAscender = hhea.ascent
    os2.sTypoDescender = hhea.descent
    os2.sTypoLineGap = hhea.lineGap
    os2.usWinAscent = max(0, hhea.ascent)
    os2.usWinDescent = abs(min(0, hhea.descent))
    os2.ulCodePageRange1 = 0x00000001
    os2.ulCodePageRange2 = 0
    os2.sxHeight = head.unitsPerEm // 2
    os2.sCapHeight = head.unitsPerEm * 7 // 10
    os2.usDefaultChar = 0
    os2.usBreakChar = 32
    os2.usMaxContext = 1
    tt["OS/2"] = os2


def add_post(tt) -> None:
    """补 post v3。

    ⚠️ **不要**设 `post.glyphOrder`：`newTable("post")` 内部会取 `getGlyphOrder()`，
    因此调用前必须先 `tt.getGlyphOrder()` 触发解压，否则报
    `TTLibError: illegal use of getGlyphOrder()`；而手动赋 list 又会在保存时
    抛 `'list' object has no attribute '__dict__'`。post v3 不需要字形名。
    """
    from fontTools.ttLib import newTable
    post = newTable("post")
    post.formatType = 3.0
    post.italicAngle = 0.0
    post.underlinePosition = -100
    post.underlineThickness = 50
    post.isFixedPitch = 0
    post.minMemType42 = 0
    post.maxMemType42 = 0
    post.minMemType1 = 0
    post.maxMemType1 = 0
    tt["post"] = post


def add_windows_name(tt) -> None:
    """补 Windows(3,1,0x409) 的 name 记录：Chrome 对只有 Mac 记录的字体不友好。"""
    if "name" not in tt:
        return
    name = tt["name"]
    have = {(r.nameID, r.platformID) for r in name.names}
    for nid in (1, 2, 3, 4, 5, 6):
        if (nid, 3) in have:
            continue
        src = None
        for r in name.names:
            if r.nameID == nid and r.platformID == 1:
                src = r.toUnicode()
                break
        if src is None and nid == 2:
            src = "Regular"
        if src is None:
            continue
        name.setName(src, nid, 3, 1, 0x409)


def missing_tables(path: str) -> list[str]:
    from fontTools.ttLib import TTFont
    tt = TTFont(path, fontNumber=0, lazy=True)
    keys = set(tt.keys())
    return [t for t in (REQUIRED + COMMONLY_EXPECTED) if t not in keys]


def main() -> int:
    ap = argparse.ArgumentParser(description="修复 PDF 提取的字体以便浏览器加载")
    ap.add_argument("--fonts-dir", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "out", "fonts"))
    ap.add_argument("--check", action="store_true", help="只报告缺表，不改文件")
    args = ap.parse_args()

    from fontTools.ttLib import TTFont

    raw_dir = os.path.join(args.fonts_dir, "raw")
    raw_files = sorted(f for f in os.listdir(raw_dir)) if os.path.isdir(raw_dir) else []

    if args.check:
        # ⚠️ 只检查 `out/fonts/*.ttf`（阅读器实际加载的修复稿）。
        # **不能**把 `raw/` 里的原件也算进来——原件本来就缺 OS/2，
        # 算进来会让检查永远报"缺表"（实测踩过）。
        print(f"{'字体':<34} 缺失的表")
        targets = [os.path.join(args.fonts_dir, f) for f in sorted(os.listdir(args.fonts_dir))
                   if f.lower().endswith((".ttf", ".otf"))]
        fatal: list[str] = []       # 缺 OS/2 → 浏览器 OTS 拒绝加载整个字体
        tolerable: list[str] = []   # 其它（如 CFF 字体本来就没有 glyf/loca）
        for p in targets:
            miss = missing_tables(p)
            if "OS/2" in miss:
                fatal.append(os.path.basename(p))
            elif miss:
                tolerable.append(os.path.basename(p))
            print(f"   {os.path.basename(p):<34} {miss or '（无）'}")
        if fatal:
            print(f"\n结论：{len(fatal)} 个字体缺 OS/2（**致命**，浏览器会拒绝加载）：{fatal}")
            print("      运行 `python tools/fix_fonts.py` 补表后重试")
        elif tolerable:
            print(f"\n结论：无字体缺 OS/2（可加载）；{len(tolerable)} 个字体有其它缺表"
                  f"（多为 OpenType/CFF 字体，无 glyf/loca 属正常）：{tolerable}")
        else:
            print("\n结论：全部字体表完整")
        print(f"（原件备份在 {raw_dir}，它们本来就缺 OS/2，不参与检查）")
        return 0 if not fatal else 1

    os.makedirs(raw_dir, exist_ok=True)
    fixed = 0
    for fn in sorted(os.listdir(args.fonts_dir)):
        if not fn.lower().endswith((".ttf", ".otf")):
            continue
        src = os.path.join(args.fonts_dir, fn)
        # 若 raw/ 里已有原件，说明本文件是修复稿；否则先备份原件
        if not os.path.isfile(os.path.join(raw_dir, fn)):
            shutil.copy2(src, os.path.join(raw_dir, fn))
        else:
            shutil.copy2(os.path.join(raw_dir, fn), src)   # 从原件重来，保证可重复
        try:
            tt = TTFont(src, fontNumber=0)
            tt.getGlyphOrder()        # ← 必须先解压，见 add_post 注释
            added = []
            if "OS/2" not in tt:
                add_os2(tt); added.append("OS/2")
            if "post" not in tt:
                add_post(tt); added.append("post")
            add_windows_name(tt)
            tt.save(src)
            print(f"   {fn:<34} 补 {','.join(added) or '（无需）':<12} "
                  f"→ {os.path.getsize(src):>9d} 字节")
            fixed += 1
        except Exception as e:  # noqa: BLE001
            print(f"   {fn:<34} 修复失败: {type(e).__name__}: {e}")
    print(f"\n共处理 {fixed} 个字体（原件备份在 {raw_dir}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
