#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe PP-StructureV3 on one cropped table image (timing + output shape)."""
from __future__ import annotations

import json
import logging
import sys
import time

logging.disable(logging.WARNING)

from paddleocr import PPStructureV3


def main() -> int:
    img = sys.argv[1]
    t0 = time.time()
    pipeline = PPStructureV3(device="cpu")
    build = time.time() - t0
    t1 = time.time()
    results = list(pipeline.predict(img))
    infer = time.time() - t1
    data = results[0].json
    if isinstance(data, str):
        data = json.loads(data)
    tables = data.get("table_res_list") or []
    print("init %.1fs infer %.1fs tables %d" % (build, infer, len(tables)))
    if tables:
        table = tables[0]
        print("cells %d" % len(table.get("cell_box_list") or []))
        print("html", (table.get("pred_html") or "")[:400])
        reg = table.get("table_ocr_pred") or {}
        print("ocr_texts", (reg.get("rec_texts") or [])[:12])
    leftovers = [r for r in data.keys() if r not in ("table_res_list",)]
    print("other keys", leftovers[:12])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
