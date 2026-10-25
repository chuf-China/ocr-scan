#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""review_formulas.py —— T2 阶段 1：按公式区域复核公式内容（不改正文）。

流程（全程串行，不占 8080，不进显存）：

1. 读已有 `layout.json` 与它对应的原始 PDF（`--pdf` 优先，否则用
   `document.source_pdf`）；
2. 逐页用 `PP-DocLayout_plus-L` 出公式框（200dpi 检测图 → pt）；
3. 把公式框映射到 `content_type=formula` 的区域（映射不上就记为未归属，不猜）；
4. 从**原始 PDF** 以 300dpi 重渲页面并裁单条公式（不放大 150dpi 截图）；
5. 子进程调 `formula-venv` 的 `paddleocr formula_recognition`（一次传目录，模型只
   初始化一次）；
6. 证据写进 `region["formulas"]`：`latex` / `crop` / `compare` / `review`，**不动**
   `region.text` 与 `line.text`；输出到新文件，不覆盖输入。

为什么是“证据”而不是“改文本”：内容级 A/B 还没做（见
`docs/specs/t2-formula-regions.md`），未经人工标注证明更好之前，任何模型输出都
不能静默替换现有 OCR 结果。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import formula_review as fr  # noqa: E402
from layout_regions import ensure_regions  # noqa: E402


def die(msg: str, code: int = 3) -> "NoReturn":  # noqa: F821
    print(f"[formula] {msg}", file=sys.stderr)
    raise SystemExit(code)


def parse_pages(spec: str | None, total: int) -> list[int]:
    if not spec:
        return list(range(1, total + 1))
    picked: set[int] = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            start, _, end = chunk.partition("-")
            picked.update(range(int(start), int(end) + 1))
        else:
            picked.add(int(chunk))
    return [p for p in sorted(picked) if 1 <= p <= total]


def find_result(path_dir: str, stem: str) -> dict | None:
    """CLI 在 save_path 下落 `<stem>_res.json`；找不到返回 None（不猜）。"""
    for pattern in (f"{stem}_res.json", f"{stem}.json", "*_res.json"):
        hits = sorted(glob.glob(os.path.join(path_dir, pattern)))
        if hits:
            with open(hits[-1], encoding="utf-8") as fh:
                return json.load(fh)
    return None


def run_cli(cli: str, args: list[str], timeout: int) -> subprocess.CompletedProcess:
    cmd = [cli] + args
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        die(f"找不到公式环境命令行：{cli}（用 --cli 指定 paddleocr.exe）", 2)
    except subprocess.TimeoutExpired:
        die(f"子进程超时（{timeout}s）：{' '.join(cmd[:3])} …", 2)


def detect_formula_boxes(cli: str, png: str, out_dir: str, model: str,
                         timeout: int) -> list[dict]:
    proc = run_cli(cli, ["layout_detection", "-i", png, "--device", "cpu",
                         "--model_name", model, "--save_path", out_dir], timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        die(f"版面检测失败 rc={proc.returncode}：{' / '.join(tail)}", 2)
    data = find_result(out_dir, os.path.splitext(os.path.basename(png))[0])
    if not data:
        die(f"版面检测没有结果 JSON（{out_dir}）", 2)
    return (data.get("res") or data).get("boxes") or []


def recognize_crops(cli: str, crop_dir: str, out_dir: str, timeout: int) -> dict[str, str]:
    proc = run_cli(cli, ["formula_recognition", "-i", crop_dir, "--device", "cpu",
                         "--save_path", out_dir], timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        die(f"公式识别失败 rc={proc.returncode}：{' / '.join(tail)}", 2)
    results: dict[str, str] = {}
    for path in sorted(glob.glob(os.path.join(out_dir, "*_res.json"))):
        stem = os.path.basename(path)[: -len("_res.json")]
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        latex = ((data.get("res") or data) or {}).get("rec_formula")
        results[stem] = (latex or "").strip()
    return results


def default_cli() -> str:
    """默认找与仓库同级放置的 formula-venv（本机布局：ocr/formula-venv）。

    只做“候选路径存在性检查”，不扫描磁盘；找不到由调用方用 --cli 显式指定。
    """
    candidates = [os.path.join(os.path.dirname(os.path.dirname(ROOT)),
                               "formula-venv", "Scripts", "paddleocr.exe"),
                  os.path.join(os.path.dirname(ROOT),
                               "formula-venv", "Scripts", "paddleocr.exe")]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="T2 公式区域复核（证据模式，不改正文）")
    parser.add_argument("layout_json", help="已有 layout.json")
    parser.add_argument("--pdf", help="原始 PDF；缺省取 document.source_pdf")
    parser.add_argument("--out", help="输出 layout.json；缺省 <输入>.formula.json")
    parser.add_argument("--pages", help="只处理这些页，如 1-2,5")
    parser.add_argument("--crop-dpi", type=int, default=300)
    parser.add_argument("--detect-dpi", type=int, default=200)
    parser.add_argument("--model", default="PP-DocLayout_plus-L", help="版面检测模型")
    parser.add_argument("--cli", default=default_cli(),
                        help="formula-venv 的 paddleocr.exe")
    parser.add_argument("--keep-crops", help="把裁片留在该目录（默认用临时目录后删除）")
    parser.add_argument("--max-formulas", type=int, default=0,
                        help="每页最多跑多少条公式（0=不限，便于小样本试跑）")
    args = parser.parse_args()

    t0 = time.time()
    with open(args.layout_json, encoding="utf-8") as fh:
        layout = json.load(fh)
    pages = layout.get("pages") or []
    if not pages:
        die("layout.json 没有 pages", 3)
    # T1 之前的产物没有 regions：就地派生（幂等），这样老产物也能直接复核。
    ensured = ensure_regions(layout)
    if ensured:
        print(f"[formula] 旧产物缺 regions，已派生 {ensured} 处", flush=True)

    pdf_path = args.pdf or (layout.get("document") or {}).get("source_pdf")
    if not pdf_path or not os.path.isfile(pdf_path):
        die(f"找不到原始 PDF：{pdf_path}", 3)
    out_path = args.out or (os.path.splitext(args.layout_json)[0] + ".formula.json")
    if os.path.abspath(out_path) == os.path.abspath(args.layout_json):
        die("输出路径不能等于输入路径（不要覆盖原 layout）", 3)
    if not os.path.isfile(args.cli):
        die(f"找不到公式环境命令行：{args.cli}", 2)

    import pypdfium2 as pdfium  # 延迟导入：没有该依赖时也能跑 --help

    wanted = set(parse_pages(args.pages, len(pages)))
    pdf = pdfium.PdfDocument(pdf_path)
    tmp_root = args.keep_crops or tempfile.mkdtemp(prefix="t2review_")
    detect_scale = args.detect_dpi / 72.0
    crop_scale = args.crop_dpi / 72.0
    crop_root = os.path.join(tmp_root, "crops")
    os.makedirs(crop_root, exist_ok=True)

    total_boxes = total_attached = 0
    page_stats: list[dict] = []
    for page in pages:
        pno = page.get("page")
        if pno not in wanted:
            continue
        detect_dir = os.path.join(tmp_root, f"detect_p{pno}")
        os.makedirs(detect_dir, exist_ok=True)
        doc_page = pdf[pno - 1]
        detect_png = os.path.join(tmp_root, f"detect_p{pno}.png")
        doc_page.render(scale=detect_scale).to_pil().convert("RGB").save(detect_png)

        boxes = detect_formula_boxes(args.cli, detect_png, detect_dir,
                                     args.model, timeout=600)
        form_boxes = [b for b in boxes if b.get("label") == "formula"]
        if args.max_formulas:
            form_boxes = form_boxes[: args.max_formulas]
        page_render = doc_page.render(scale=crop_scale).to_pil().convert("RGB")
        entries: list[dict] = []
        unassigned: list[dict] = []
        pad = 2.0
        for idx, box in enumerate(form_boxes, start=1):
            coords = box.get("coordinate") or []
            if len(coords) != 4:
                continue
            x0, y0, x1, y1 = (float(v) * 72.0 / args.detect_dpi for v in coords)
            box_pt = [x0, y0, x1, y1]
            region_id = fr.map_box_to_region(box_pt, page.get("regions") or [])
            if region_id is None:
                unassigned.append({"bbox_pt": box_pt, "score": box.get("score")})
                continue
            px = (max(0, int(round((x0 - pad) * crop_scale))),
                  max(0, int(round((y0 - pad) * crop_scale))),
                  int(round((x1 + pad) * crop_scale)),
                  int(round((y1 + pad) * crop_scale)))
            stem = f"p{pno:03d}_f{idx:02d}"
            page_render.crop(px).save(os.path.join(crop_root, f"{stem}.png"))
            entries.append({"region_id": region_id, "box_pt": box_pt,
                            "score": box.get("score"), "stem": stem})
        total_boxes += len(form_boxes)
        total_attached += len(entries)
        page_stats.append({"page": pno, "formulas": len(form_boxes),
                           "attached": len(entries), "unassigned": len(unassigned)})
        attached = fr.attach_evidence(page, [], unassigned)
        print(f"[formula] 第 {pno} 页：公式框 {len(form_boxes)}，"
              f"可归属 {len(entries)}，未归属 {attached['unassigned']}", flush=True)
        page["_t2_pending"] = entries  # 识别结果回来后再落盘，避免半成品留在 JSON

    if not total_attached:
        die("没有可归属的公式框（检查 layout 是否有 content_type=formula 的区域）", 4)

    rec_dir = os.path.join(tmp_root, "rec")
    os.makedirs(rec_dir, exist_ok=True)
    print(f"[formula] 公式识别 {total_attached} 条（一次进程）……", flush=True)
    latex_map = recognize_crops(args.cli, crop_root, rec_dir, timeout=3600)

    for page in pages:
        entries = page.pop("_t2_pending", None)
        if not entries:
            continue
        segments_by_region: dict[str, list[str]] = {}
        for region in page.get("regions") or []:
            segments_by_region[region.get("id")] = fr.extract_math_segments(
                region.get("text") or "")
        prepared = []
        for entry in entries:
            latex = latex_map.get(entry["stem"], "")
            segments = segments_by_region.get(entry["region_id"]) or []
            prepared.append(dict(
                fr.build_evidence(latex, entry["box_pt"], args.crop_dpi,
                                  "pd-doclayout", entry.get("score"), segments),
                region_id=entry["region_id"]))
        fr.attach_evidence(page, prepared)

    summary = fr.review_summary(pages)
    summary.update({"crop_dpi": args.crop_dpi, "detect_dpi": args.detect_dpi,
                    "detect_model": args.model, "pdf": os.path.abspath(pdf_path),
                    "elapsed_s": round(time.time() - t0, 1)})
    problems = fr.validate_formula_evidence(pages)
    summary["problems"] = len(problems)
    if problems:
        summary["problem_samples"] = problems[:5]

    doc = layout.setdefault("document", {})
    doc["formula_review"] = summary
    stats = layout.setdefault("stats", {})
    stats["formula_review_blocks"] = summary["formulas"]
    stats["formula_review_regions"] = summary["regions_reviewed"]

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(layout, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"[formula] 完成：{summary['formulas']} 条证据 / "
          f"{summary['regions_reviewed']} 个区域；一致性 {summary['agreement']}；"
          f"{summary['elapsed_s']}s → {out_path}")
    if not args.keep_crops:
        import shutil
        shutil.rmtree(tmp_root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
