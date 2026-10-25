#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T2 取证：PP-FormulaNet_plus-M 能否完全离线载入（只载入，不做推理）。

做法：先给 socket 打补丁，任何网络连接尝试都会被记录并直接抛异常。
若 pipeline 构建成功且 connections == 0，即证明“构建阶段零联网”。
不启动 llama-server、不占用 GPU（device=cpu）、不推理、不写 out/ 以外的文件。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
import traceback

ATTEMPTS = []
_ORIG_CONNECT = socket.socket.connect
_ORIG_CONN2 = socket.create_connection


def _guard_connect(self, address):
    ATTEMPTS.append(("socket.connect", repr(address)))
    raise OSError(f"[T2-PROBE] network blocked: {address!r}")


def _guard_create_connection(address, *a, **k):
    ATTEMPTS.append(("create_connection", repr(address)))
    raise OSError(f"[T2-PROBE] network blocked: {address!r}")


socket.socket.connect = _guard_connect
socket.create_connection = _guard_create_connection

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ["FLAGS_use_mkldnn"] = "0"


def main() -> int:
    t0 = time.time()
    report = {"steps": [], "connections": ATTEMPTS}
    try:
        import paddle

        report["paddle_version"] = paddle.__version__
        report["compiled_with_cuda"] = bool(paddle.device.is_compiled_with_cuda())
        report["steps"].append(["import paddle", round(time.time() - t0, 1)])

        from paddleocr import FormulaRecognitionPipeline

        t1 = time.time()
        pipe = FormulaRecognitionPipeline(
            formula_recognition_model_name="PP-FormulaNet_plus-M",
            layout_detection_model_name="PP-DocLayout_plus-L",
            doc_orientation_classify_model_name="PP-LCNet_x1_0_doc_ori",
            doc_unwarping_model_name="UVDoc",
            device="cpu",
        )
        report["steps"].append(["build FormulaRecognitionPipeline (cpu)", round(time.time() - t1, 1)])
        report["pipeline_repr"] = type(pipe).__name__
        report["ok"] = True
    except Exception as e:  # noqa: BLE001
        report["ok"] = False
        report["error"] = f"{type(e).__name__}: {e}"
        report["traceback"] = traceback.format_exc()[-3000:]
    report["connections"] = ATTEMPTS
    report["total_s"] = round(time.time() - t0, 1)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "load_probe.json")
    json.dump(report, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("=== T2 load probe ===")
    print(json.dumps({k: v for k, v in report.items() if k != "traceback"},
                     ensure_ascii=False, indent=1))
    if not report["ok"]:
        print("--- traceback tail ---")
        print(report.get("traceback", ""))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
