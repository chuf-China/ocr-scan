#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成公式内容级 A/B 复核材料（裁片对照表 + 明细表）。

T2 的 KaTeX/结构门只能证明"定界符成对、能编译"，不能证明公式内容识别正确。
本工具把 `review_formulas.py` 的公式证据按类别抽样，从原始 PDF 以 300dpi 重渲
每个公式的裁片，拼成带序号的对照图，再输出一份明细表（现有 OCR 片段 vs 模型
LaTeX）。人工只需看图 + 表，逐条判 h/o/b（模型更好 / 现有更好 / 相同），
就能得到内容级准确率，而不是拿"能渲染"冒充正确。

只读产物：不写 layout/quality，不改任何 JSON，不启动 OCR 引擎。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.dont_write_bytecode = True

import layout_regions  # noqa: E402
import formula_review  # noqa: E402

CLASS_ORDER = ("differs", "partial", "normalized", "exact")
DEFAULT_QUOTA = {"differs": 24, "partial": 12, "normalized": 8, "exact": 4}
PRIORITY = {"differs": 4, "partial": 3, "normalized": 2, "exact": 1, "no_current": 0}


def die(message: str, code: int) -> None:
    print("[ab] " + message, file=sys.stderr)
    raise SystemExit(code)


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def collect_entries(layout: dict) -> list[dict]:
    """从公式证据里抽出可对照的条目（只取有裁片、有输出的公式）。"""
    entries: list[dict] = []
    for page in layout.get("pages") or []:
        page_no = page.get("page")
        for region in page.get("regions") or []:
            if region.get("content_type") != "formula":
                continue
            for evidence in region.get("formulas") or []:
                latex = (evidence.get("latex") or "").strip()
                crop = evidence.get("crop") or {}
                bbox = crop.get("bbox_pt")
                if not latex or not layout_regions.valid_bbox(bbox):
                    continue
                compare = evidence.get("compare") or {}
                entries.append(
                    {
                        "page": page_no,
                        "region_id": region.get("id"),
                        "agreement": compare.get("agreement") or "no_current",
                        "current": (compare.get("current") or "").strip(),
                        "latex": latex,
                        "bbox_pt": layout_regions.valid_bbox(bbox),
                        "dpi": crop.get("dpi") or 300,
                        "crop_source": crop.get("crop_source") or "",
                        "score": evidence.get("detect_score"),
                        "region_text": (region.get("text") or "").strip(),
                    }
                )
    return entries


def sample(entries: list[dict], quota: dict, seed: int) -> list[dict]:
    """按类别配额抽样，保持页序稳定，便于人工按顺序复核。"""
    rng = random.Random(seed)
    picked: list[dict] = []
    for cls in CLASS_ORDER:
        pool = [item for item in entries if item["agreement"] == cls]
        rng.shuffle(pool)
        want = quota.get(cls, 0)
        picked.extend(pool[:want])
    picked.sort(key=lambda item: (item["page"], item["bbox_pt"][1], item["bbox_pt"][0]))
    return picked


def render_crops(pdf_path: str, items: list[dict], out_dir: str, pad_pt: float) -> list[str]:
    """按条目从原始 PDF 重渲 300dpi 裁片；同一页只渲一次。"""
    import pypdfium2 as pdfium  # 局部导入：只有本工具需要

    os.makedirs(out_dir, exist_ok=True)
    doc = pdfium.PdfDocument(pdf_path)
    pages: dict[int, Any] = {}
    paths: list[str] = []
    try:
        for index, item in enumerate(items, start=1):
            page_no = int(item["page"])
            if page_no not in pages:
                page = doc[page_no - 1]
                dpi = int(item.get("dpi") or 300)
                pages[page_no] = (page, page.render(scale=dpi / 72.0).to_pil())
            page, image = pages[page_no]
            scale = (item.get("dpi") or 300) / 72.0
            x0, y0, x1, y1 = item["bbox_pt"]
            left = max(0, int(round((x0 - pad_pt) * scale)))
            top = max(0, int(round((y0 - pad_pt) * scale)))
            right = min(image.width, int(round((x1 + pad_pt) * scale)))
            bottom = min(image.height, int(round((y1 + pad_pt) * scale)))
            if right - left < 8 or bottom - top < 8:
                die(f"裁片过小：第 {page_no} 页 {item['region_id']}", 3)
            crop = image.crop((left, top, right, bottom))
            path = os.path.join(out_dir, f"ab{index:03d}_p{page_no}_f{index:03d}.png")
            crop.save(path)
            paths.append(path)
    finally:
        doc.close()
    return paths


def build_sheets(items: list[dict], crops: list[str], out_dir: str, per_sheet: int) -> list[str]:
    """把裁片拼成带序号的对照图；每张图不超过 per_sheet 行。"""
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 26)
    except Exception:  # 字体缺失不影响出图，退回默认位图字体
        font = ImageFont.load_default()

    label_w = 96
    margin = 12
    max_w = 1100
    paths: list[str] = []
    sheets = [items[i : i + per_sheet] for i in range(0, len(items), per_sheet)]
    for sheet_no, chunk in enumerate(sheets, start=1):
        tiles = []
        for item in chunk:
            path = crops[items.index(item)]
            with Image.open(path) as raw:
                image = raw.convert("RGB")
            if image.width > max_w:
                ratio = max_w / float(image.width)
                image = image.resize((max_w, max(1, int(image.height * ratio))))
            tiles.append(image)
        height = sum(tile.height + margin for tile in tiles) + margin
        sheet = Image.new("RGB", (label_w + max_w + margin, height), "white")
        draw = ImageDraw.Draw(sheet)
        y = margin
        start = (sheet_no - 1) * per_sheet + 1
        for offset, tile in enumerate(tiles):
            index = start + offset
            draw.text((8, y + 4), f"#{index:03d}", fill="black", font=font)
            sheet.paste(tile, (label_w, y))
            draw.line([(0, y + tile.height + margin // 2), (sheet.width, y + tile.height + margin // 2)], fill="#DDDDDD")
            y += tile.height + margin
        path = os.path.join(out_dir, f"sheet{sheet_no:02d}.png")
        sheet.save(path)
        paths.append(path)
    return paths


def esc(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def write_markdown(items: list[dict], out_dir: str, meta: dict) -> str:
    lines = [
        "# 公式内容级 A/B 复核表",
        "",
        f"- layout：`{meta['layout']}`",
        f"- 公式证据：`{meta['formula']}`",
        f"- 总证据 {meta['total']} 条，抽样 {len(items)} 条（配额 differs/partial/normalized/exact）",
        "",
        "判定口径：对照裁片，比较「现有 OCR 片段（current）」与「模型 LaTeX（model）」，",
        "逐条填 h = 模型更好 / o = 现有更好 / b = 相同（都好或都错）。",
        "",
        "| # | 页/区域 | 一致性 | current（现有 OCR） | model（公式模型） | score | 判定 |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, item in enumerate(items, start=1):
        score = "" if item.get("score") is None else f"{float(item['score']):.2f}"
        lines.append(
            "| {i} | p{p}/{r} | {ag} | `{cur}` | `{mod}` | {sc} |  |".format(
                i=index,
                p=item["page"],
                r=item["region_id"],
                ag=item["agreement"],
                cur=esc(item["current"])[:300],
                mod=esc(item["latex"])[:300],
                sc=score,
            )
        )
    lines.append("")
    path = os.path.join(out_dir, "review.md")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="生成公式内容级 A/B 复核材料（裁片对照图 + 明细表）")
    parser.add_argument("layout_json", help="带 formulas 证据的 layout JSON（review_formulas 的输出）")
    parser.add_argument("--pdf", help="原始 PDF；默认用 document.source_pdf")
    parser.add_argument("--out", help="输出目录；默认 <输入目录>/ab")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--per-sheet", type=int, default=8)
    parser.add_argument("--pad-pt", type=float, default=4.0)
    parser.add_argument("--differs", type=int, default=DEFAULT_QUOTA["differs"])
    parser.add_argument("--partial", type=int, default=DEFAULT_QUOTA["partial"])
    parser.add_argument("--normalized", type=int, default=DEFAULT_QUOTA["normalized"])
    parser.add_argument("--exact", type=int, default=DEFAULT_QUOTA["exact"])
    args = parser.parse_args()

    layout = load_json(args.layout_json)
    pdf_path = args.pdf or (layout.get("document") or {}).get("source_pdf")
    if not pdf_path or not os.path.isfile(pdf_path):
        die(f"找不到原始 PDF：{pdf_path}", 3)

    entries = collect_entries(layout)
    if not entries:
        die("没有任何公式证据（先跑 review_formulas.py）", 4)
    quota = {
        "differs": max(0, args.differs),
        "partial": max(0, args.partial),
        "normalized": max(0, args.normalized),
        "exact": max(0, args.exact),
    }
    items = sample(entries, quota, args.seed)
    out_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(args.layout_json)), "ab")

    crops = render_crops(pdf_path, items, os.path.join(out_dir, "crops"), args.pad_pt)
    sheets = build_sheets(items, crops, out_dir, max(1, args.per_sheet))
    markdown = write_markdown(
        items,
        out_dir,
        {"layout": os.path.abspath(args.layout_json), "formula": os.path.basename(args.layout_json), "total": len(entries)},
    )
    with open(os.path.join(out_dir, "review.json"), "w", encoding="utf-8") as handle:
        json.dump({"meta": {"total": len(entries), "sampled": len(items)}, "items": items}, handle, ensure_ascii=False, indent=2)

    print(f"[ab] 证据 {len(entries)} 条，抽样 {len(items)} 条：{counts(items)}")
    print(f"[ab] 对照图 {len(sheets)} 张：{os.path.join(out_dir, 'sheet*.png')}")
    print(f"[ab] 明细表：{markdown}")
    return 0


def counts(items: list[dict]) -> dict:
    result: dict[str, int] = {}
    for item in items:
        result[item["agreement"]] = result.get(item["agreement"], 0) + 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
