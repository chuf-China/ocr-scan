# S3：表格结构（不把表格压成一行）

## 目标

有文字层的数学试卷里，表格（分布列、列联表、统计数据、噪声表）不能只作为
一张插图或一段换行文本存在。S3 从 PDF 的原生表格框线取几何，逐单元格 OCR，
输出可按行/列消费的 `tables[]`，并归属到题目。

S3 只负责“表格几何 + 单元格文本 + 归属”，不负责合并单元格的复杂排版
（rowspan/colspan）和无框线表格；这两类在 `quality` 中以 warning 暴露，留给 S4/S6。

## 输入

- `ocr_pipeline_layout.py` 的 `PdfFontProbe`：`pdfplumber` 页面；
- `page.find_tables()`：表格 bbox、行列数和单元格 bbox；
- 页面渲染图 `PIL.Image`：按单元格 bbox 裁片，逐格 OCR。

不使用 `pdfplumber` 的 `extract()` 文本：数学卷中文字库常被抽成
`(cid:0)`，看起来“有文字”其实不可用。PDF 只提供**框线几何**，文字仍由
PaddleOCR-VL 逐单元格识别。

## 版面 JSON 字段

每页新增 `tables`：

```json
{
  "tables": [
    {
      "source": "pdf-table-cells",
      "bbox_pt": [490.7, 113.3, 724.8, 175.5],
      "rows": 4,
      "cols": 3,
      "headers": ["声源", "与声源的距离 /m", "声压级 / dB"],
      "rows_data": [
        ["燃油汽车", "10", "60～90"],
        ["混合动力汽车", "10", "50～60"],
        ["电动汽车", "10", "40"]
      ],
      "text_available": true
    }
  ]
}
```

- `rows`/`cols` 是 PDF 框线的网格数（含表头行）；
- `headers` 是第一行，`rows_data` 是其余行；
- 表格同时作为 `figure_regions` 的 `table-grid` 裁片保留原图，避免逐格 OCR 失败时
  前端没有兜底；
- 表格文本行的 `text` 为 `（表格）`，且原行会被插图裁片吸收；**表格对象不能依赖
  这一行是否还在**。

## 试卷结构 JSON

`exam_structure.build_exam()` 生成：

```json
{
  "questions": [
    {
      "number": 10,
      "tables": [
        {
          "ref": "p1-t1",
          "page": 1,
          "bbox_pt": [490.7, 113.3, 724.8, 175.5],
          "headers": ["声源", "..."],
          "rows": [["燃油汽车", "10", "60～90"]],
          "rows_count": 4,
          "cols_count": 3,
          "source": "pdf-table-cells",
          "distance_pt": 11.23
        }
      ]
    }
  ],
  "unassigned": {"figures": [], "tables": [], "lines": []},
  "stats": {"tables_assigned": 1, "tables_unassigned": 0}
}
```

归属规则与插图一致：表格 bbox 到题目所有行/选项/小问的距离取最近题，
阈值 80pt；超过阈值进 `unassigned.tables`，不静默塞给上一题。页级表格只由
`page["tables"]` 归属，行级 `tables` 中 `source=pdf-table-cells` 的项跳过，避免重复。

## 质量门

`quality.json` 新增：

- `summary.tables` / `summary.tables_unassigned`；
- `checks.all_tables_structured`：每张表必须有非空 `headers` 和至少一行；
- `table_header_empty` / `table_rows_empty` / `table_empty_cell` warning；
- `table_unassigned` warning；
- 表格单元格文本中的公式仍走 S2 的 `formula_quality` 可见性（S3 至少不把
  `\(...\)` 截断；逐格 OCR 空串必须 warning）。

S3 不把表格 warning 直接升级为 error：空单元格可能是原卷合并单元格；
但 `table_header_empty` + 无数据行意味着结构不可用，S6 展示层不得声称表格已完成。

## 验收

1. 三套 A3 验收卷各检出 1 张表格：
   - 2023 新高考Ⅰ Q10 噪声表：4×3，表头/3 行数据完整；
   - 2023 北京 Q18 价格变化表：3×2，表头/2 行数据完整；
   - 2023 全国乙理 Q17 伸缩率表：3×11，表头/2 行数据完整。
2. 表格归属题号正确，`tables_unassigned=0`，页面不出现重复表。
3. 表格裁片仍在原位，`check_render` 不因表格裁片压正文报错。
4. `check_exam_json.py` 能打印表数；旧产物无 `page["tables"]` 时仍可读。
5. `smoke_pipelines.py` 覆盖合成页的页级表格归属与行级去重。

## 非目标

- 不解析 rowspan/colspan；
- 不识别无框线表格（仍按插图/文本处理，S1 覆盖率负责暴露）；
- 不把表格文本强行拼成 Markdown（S6 展示层再做）；
- 不替换公式模型（S2）。

## 实现状态（2026-10）

- `PdfFontProbe.table_boxes()`、`ocr_table_cells()` 已接入 `ocr_pipeline_layout.build()`；
  每页写 `tables[]`，并保留 `table-grid` 裁片。
- `exam_structure.build_exam()` 已写 `question.tables` / `unassigned.tables`；
  `build_quality()` 写表数、空表/空单元格/未归属表 warning 和
  `table_formula_broken` error。
- 三套 A3 验收卷各检出 1 张表格（4×3 / 3×2 / 3×11），单元格文字完整，
  `tables_unassigned=0`，`all_tables_structured=true`。
- `tools/test_exam_structure.py` 增加页级表格归属与行级去重测试；
  `check_exam_json.py` 打印表数。
