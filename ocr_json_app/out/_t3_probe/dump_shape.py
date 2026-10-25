#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dump the full JSON shape of a table-structure result for one crop."""
from __future__ import annotations

import json
import logging
import sys

logging.disable(logging.WARNING)

from paddleocr import TableStructureRecognition


def walk(value, depth: int = 0) -> None:
    pad = "  " * depth
    if isinstance(value, dict):
        for key, inner in value.items():
            if isinstance(inner, (dict, list)):
                size = len(inner)
                print("%s%s: %s[%d]" % (pad, key, type(inner).__name__, size))
                if depth < 3:
                    walk(inner, depth + 1)
            else:
                print("%s%s = %r" % (pad, key, str(inner)[:120]))
    elif isinstance(value, list):
        if value:
            print("%s[0] =" % pad)
            walk(value[0], depth + 1)


def main() -> int:
    img = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else "SLANet_plus"
    pipeline = TableStructureRecognition(model_name=model, device="cpu")
    results = list(pipeline.predict(img))
    data = results[0].json
    if isinstance(data, str):
        data = json.loads(data)
    walk(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
