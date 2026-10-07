#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""crop_png.py —— 从整页截图裁一块并放大，用于看清局部（判断"行叠字"必须放大看）。

用法：python crop_png.py in.png out.png x y w h [scale]
"""
import sys

from PIL import Image

src, dst = sys.argv[1], sys.argv[2]
x, y, w, h = (int(v) for v in sys.argv[3:7])
scale = float(sys.argv[7]) if len(sys.argv) > 7 else 2.0

im = Image.open(src)
im = im.crop((x, y, x + w, y + h))
im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
im.save(dst)
print(f"saved {dst} {im.size}  (crop {x},{y} {w}x{h} x{scale})")
