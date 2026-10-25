# T3 表格复核证据层（PP-StructureV3 表格子流水线）

状态：阶段 1（证据层）已落地。模型只产旁证，绝不覆盖表格正文。

## 1. 为什么需要这一层

现有表格有两类来源：

- `pdf-table-cells`：`PdfFontProbe.table_boxes()` 取 PDF 原生框线 + 逐格 OCR；
- `table-grid`：300dpi 位图按框线切格再 OCR。

两者都会在空单元格、错行、跨格合并上出错，而这些错误不会让版式检查失败——格子空着、
行错一位，坐标依然合法。质量门能报 `table_empty_cell`，却没有第二种读法来确认
"这格到底空不空"。表格模型提供的就是第二种读法。

## 2. 边界：只产证据

- **不改正文**：`table.headers`、`table.rows_data`、`table.text_available` 一律不动；
- 证据写 `table.reviews[]`（数组，同一张表可被多次/多模型复核）；
- 映射不到的裁片写 `page.unassigned_table_boxes[]`，不新造表格、不改判区域；
- 历史产物没有 `reviews` 时，校验器跳过（与区域层、公式证据同口径）。

## 3. 证据块 schema

```json
{
  "source": "paddle-table-v2",
  "pipeline": "table_recognition_v2",
  "models": {"structure": "SLANeXt_wired/SLANet_plus（pipeline 自动选择）", "text": "PP-OCRv5_server_rec"},
  "crop": {"bbox_pt": [113.54, 106.86, 481.92, 166.46], "dpi": 200},
  "html": "<html><body><table>…</table></body></html>",
  "cells": [{"text": "年份", "bbox_pt": [115.5, 108.8, 130.1, 120.0], "score": 0.9999}],
  "detect_score": 0.9999,
  "compare": {
    "rows": {"current": 3, "model": 3},
    "cols": {"current": 10, "model": 10},
    "matched": 30, "differing": 0,
    "only_current": 0, "only_model": 0,
    "match_rate": 1.0,
    "fill_rate": {"current": 1.0, "model": 1.0},
    "agreement": "exact",
    "samples": []
  },
  "review": {"needed": false, "reasons": []}
}
```

### 一致性口径（`compare.agreement`）

| 值 | 含义 | 典型修复动作 |
| --- | --- | --- |
| `exact` | 逐格内容全等（归一化后） | 无需动作 |
| `cell_mismatch` | 行列数相同，有格子内容不同 | 人工看 `samples`，按裁片裁决 |
| `structure_differs` | 行列数不同或合并结构不同 | 先修结构（框线/合并），再比内容 |
| `no_current` | 现有表没有可比内容 | 整表需要重建 |

`matched` / `differing` 只统计两边都存在的坐标；缺格单独计 `only_current` / `only_model`，
因为"模型少认一行"和"模型认错一格"是两种修复动作。

### 单元格归一化（只用于比较）

去定界符（`\(` `\)` `$`）、去 `\left`/`\right`/`\displaystyle`、全角转半角、
Unicode 数学符号转 LaTeX（`≤`→`\leq`、`∞`→`\infty`、`²`→`^{2}` …）、去空白、去尾部标点。

这样现有表格的 `\(90\leq x\leq100\)` 与模型的 `90≤x≤100` 被认作同一格；
而模型把 `\frac{16}{25}` 压成 `1625` 仍然算不同——那是真的丢结构。

## 4. 工具

```powershell
# 在 ocr_json_app 下（CPU，4 线程，模型在 ~/.paddlex/official_models）
python tools\review_tables.py out\eval\eval01_xicheng_2024ermo.layout.json
python tools\review_tables.py <layout.json> --only-issues <quality.json>
```

参数：`--pdf`（缺省 `document.source_pdf`）、`--out`（默认 `<输入>.tables.json`）、
`--pages`、`--crop-dpi 200`、`--pad-pt 2`、`--cpu-threads 4`、`--cli`、
`--only-issues`（只复核 `quality.json` 里 `table*` 类 issue 指向的表）、`--max-tables`。

流程：`ensure_regions` → 按页渲染 200dpi → 裁每张表 bbox（外扩 2pt）→
子进程 `paddleocr table_recognition_v2 --device cpu --use_layout_detection False` →
解析 `<table_id>_res.json` 的 `table_res_list[0]`（`pred_html`、`cell_box_list`、
`table_ocr_pred.rec_texts/rec_scores`）→ `build_table_evidence` → `attach_table_evidence`。

退出码：2 = 引擎/子进程，3 = 输入，4 = 没有可归属的表格结果。

### 为什么不直接跑整页 `pp_structurev3`

整页 `pp_structurev3` 会一次加载 14 个模型（表格分类、有线/无线单元格检测、
两种结构识别、文本检测/识别、版面检测、方向矫正、去扭曲、公式、图表，
权重合计约 1.6 GB）。在本机（16 GB 内存、无 GPU）实测会把系统拖死。
所以这里只裁表格 bbox、只跑 `table_recognition_v2`，并显式关闭版面检测、
方向矫正、去扭曲，CPU 线程压到 4。

## 5. 新增语料实测（2026-10-25，5 套 10–12 页原生电子卷）

| 试卷 | 页 | 表格 | 证据 | exact | cell_mismatch | structure_differs | 耗时 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| eval01 西城 2024 二模 | 10 | 1 | 1 | 1 | 0 | 0 | 14.1s |
| eval02 海淀 2024 一模 | 12 | 2 | 2 | 0 | 2 | 0 | 17.9s |
| eval03 深圳 2025 一模 | 12 | 4 | 4 | 1 | 3 | 0 | 24.8s |
| eval04 西城 2021 期末 | 12 | 5 | 5 | 2 | 3 | 0 | 30.9s |
| eval05 西城 2020 期末 | 12 | 3 | 3 | 0 | 3 | 0 | 22.0s |
| 合计 | 58 | 15 | 15 | 4 | 11 | 0 | ~110s |

### 模型失败模式（人工看裁片确认）

1. 分式被压平：`\frac{16}{25}` → `1625`、`\frac{\sqrt{3}}{3}` → `\sqrt33`；
2. `\infty` 被读成 `8`：`(-\infty,2)` → `(-8,2)`，语义完全改变；
3. 左列文字丢前缀：`0分（不满意）` → `分(不满意)`、`5分（一般）` → `般）`；
4. 两级表头（`老年人` 跨两列）被压成一行：列数与现有表格不一致，后面整行错位；
5. 偶发杂字：`b` → `bä`、`p` → `pä`。

### 结论

15 张表里现有 `pdf-table-cells` 表格没有一张是空的（`fill_rate=1.0` 占多数），
模型没有带来可测的内容提升；11 张 `cell_mismatch` 里模型丢的格子多于补的格子。
因此：**表格模型默认不开启，只作失败区域的可选复核路径**；验收标准里的表格
F1 仍需独立标注集才有意义。

## 6. 不变量

1. 证据只增不改，正文 `headers` / `rows_data` / `text_available` 不动；
2. 一致性只有 `exact | cell_mismatch | structure_differs | no_current` 四种；
3. 映射不到的裁片写 `page.unassigned_table_boxes[]`；
4. 表格推理只在 `formula-venv` 子进程里跑（CPU paddle），主环境不得 import paddle；
5. 裁片 dpi 不得低于 144（与公式证据同口径）；
6. `check_layout_json` 只对含 `reviews` 的产物硬校验，历史产物跳过。
