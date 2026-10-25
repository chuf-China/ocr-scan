#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe lightweight table structure recognition (SLANet_plus) on one crop.

Deliberately avoids PP-StructureV3: that pipeline loads 14 models (~1.6 GB of
weights) at once and hung this 16 GB machine. Here we load one small model,
cap CPU threads, and print RSS so the caller can guard against memory spikes.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time

os.environ.setdefault("OMP_NUM_THREADS", "4")

logging.disable(logging.WARNING)

from paddleocr import TableStructureRecognition


def rss_mb() -> int:
    try:
        with open("/proc/self/status", "r", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PMC()
        counters.cb = ctypes.sizeof(PMC)
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        h = k32.GetCurrentProcess()
        if psapi.GetProcessMemoryInfo(h, ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize) // (1024 * 1024)
    except Exception:  # noqa: BLE001 - best effort diagnostics only
        pass
    return -1


def main() -> int:
    img = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else "SLANet_plus"
    print("rss_before_mb", rss_mb(), flush=True)
    t0 = time.time()
    pipeline = TableStructureRecognition(model_name=model, device="cpu")
    build = time.time() - t0
    print("build %.1fs rss_mb %d" % (build, rss_mb()), flush=True)
    t1 = time.time()
    results = list(pipeline.predict(img))
    infer = time.time() - t1
    print("infer %.1fs rss_mb %d" % (infer, rss_mb()), flush=True)
    data = results[0].json
    if isinstance(data, str):
        data = json.loads(data)
    print("keys", list(data.keys())[:10], flush=True)
    for key in ("structure", "cell_bbox", "html", "pred_html"):
        if key in data:
            value = data[key]
            shown = value if isinstance(value, str) else repr(value)[:400]
            print(key, str(shown)[:500], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
