# T1 —— layout 一等区域（regions）

状态：已完成（2026-10，T1）。前置：S1–S6 与 P0–P3（见 `release-standard.md`、
`native-pdf-quality.md`）。T1 **不改 OCR 行为**：不碰提示词、投票、行检测、裁片与
`lines[]` 顺序，只在 layout 里增加一层引用结构，并让试卷层能回链。

## 为什么

`layout.json` 里每页有三份并列证据：`lines[]`（物理行）、`figures[]`（插图）、
`tables[]`（原生表格）。三者的关系此前只存在于隐性约定里：

- 表格占位行 `（表格）` 和表格对象之间只靠坐标重合；
- 插图归属要到 `exam_structure.py` 里算曼哈顿距离；
- 公式专项（T2）要按区域重渲裁片，但“区域”没有稳定 id，只能临时按行盒聚类。

下游（exam.json、quality.json、未来的公式模型、人工复核 UI）因此只能靠坐标猜，
一换页/一改行检测就要重新对齐。T1 把这些证据收成带稳定 id 的一等对象。

## Schema

每页新增 `page.regions[]`（`lines[]` / `figures[]` / `tables[]` 保持原样，只增不改）：

| 字段 | 含义 |
|---|---|
| `id` | `p<页>-r<序>`，页内从 1 递增；同一输入稳定，可跨产物引用 |
| `page` | 页码（从 1 起，与 `page.page` 一致） |
| `bbox_pt` | `[x0, y0, x1, y1]`，PDF pt，与行/图/表坐标同口径 |
| `content_type` | `text` / `formula` / `table` / `figure` |
| `source` | `native` / `ocr` / `pdf-vector` / `pdf-image` / `pdf-table` / `coverage-region` / `table-grid` |
| `confidence` | 0–1 的**粗粒度启发式**（见下），不是校准概率 |
| `text` | 区域的可见文本；图/纯图形区域为空字符串 |
| `spans[]` | 字级样式证据（行区域一项；图区域为空数组） |
| `refs` | `{line_ids: [...], figure_id: str|None, table_id: str|None}`，指向本页真实对象 |

`spans[]` 每项：

| 字段 | 含义 |
|---|---|
| `bbox_pt` | 该 span 的 bbox（当前实现为整行，预留字级切分） |
| `text` | 文本 |
| `font` | `serif` / `sans-serif`（缺省 `None`，来自 `line.style.font_family`） |
| `size` | 字号 pt（`line.size_pt`） |
| `bold` / `italic` | 布尔；无样式信息时 `False` |
| `color` | `#RRGGBB`，无颜色证据时缺省 |
| `provenance` | 该 span 的证据来源，取值同 `source` |

`source` 的语义：`native` = PDF 字层直出；`ocr` = 模型识别；`pdf-vector` =
原生矢量图（含 `pdf-vector+labels` 这类复合来源，规范化为 `pdf-vector`）；
`pdf-image` = 原生位图；`pdf-table` = `pdfplumber.find_tables()` 的框线表；
`coverage-region` = 覆盖审计补出的区域；`table-grid` = 框线网格裁片。

`confidence` 规则（文档化的近似值，只用于分诊/排序）：

- 行区域：有 `native_similarity` 时取其值；否则 `votes>=2` 且无 disagreement → 0.9，
  有 disagreement → 0.7，单票 → 0.6；
- 表格区域：`text_available` → 0.8，否则 0.5；
- 图区域：`pdf-vector` / `pdf-image` → 1.0，`table-grid` → 0.9，`coverage-region` → 0.7。

## 不变量

1. **每条物理行恰好属于一个区域。** 表格占位行归表格区域，不再额外生成行区域；
   因此 `sum(len(r.refs.line_ids)) == len(page.lines)`（行区域 1 行、表格区域 1 行）。
2. **双向回链。** 区域写 `refs`，行/图/表写 `region_id`；`validate_regions()` 检查
   两个方向都能解析。
3. **id 稳定。** 同一输入重复构建得到同一组 id；旧产物由 `ensure_regions()` 派生时
   按同样的行/图/表顺序编号。
4. **不改变既有字段语义。** `lines[]` 仍是自上而下的物理行序，`check_layout_json`
   的行序口径不变；区域不参与阅读顺序计算。
5. **旧产物可读。** 没有 `regions` 的 layout 仍能过结构校验；试卷层就地派生（内存，
   不写回磁盘），派生是幂等的。

## 接线

- `src/layout_regions.py`：纯 stdlib，不 import 任何流水线。导出 `build_regions` /
  `ensure_regions` / `index_regions` / `region_stats` / `validate_regions` /
  `valid_bbox` / `figure_source`。
- `src/ocr_pipeline_layout.py`：原生表格补 `id`（`p<页>-t<序>`）与 `page`；页面
  payload 增加 `regions`；`document.regions`、`stats.regions`、`stats.region_types`
  写文档级汇总。
- `src/exam_structure.py`：`build_exam()` 开头 `ensure_regions()` + `index_regions()`；
  题干/选项/小问/前言带 `region_refs`，图/表 item 带 `region_id`，`stats.regions`
  计数；`build_quality()` 增加 `regions_consistent` 检查与
  `region_inconsistent` / `region_ref_unresolved` warning（**不升级为 error**）。
- `tools/check_layout_json.py`：含 `regions` 的产物走 `validate_regions()` 硬校验，
  并核对 `stats.regions` / `document.regions.count`；没有该字段的历史产物跳过。
- `tools/smoke_pipelines.py`：`test_layout_regions()` 覆盖类型齐全、id 唯一/前缀、
  双向回链、`source` 规范化、`confidence` 区间、`ensure_regions` 幂等。
- `tools/test_exam_structure.py`：`LayoutRegionTests` 覆盖文本、公式、表格占位行、
  图源规范化、跨栏身份保持、旧产物派生与质量门。

## 示例

```json
{
  "id": "p1-r2",
  "page": 1,
  "bbox_pt": [50.0, 80.0, 300.0, 92.0],
  "content_type": "formula",
  "source": "ocr",
  "confidence": 0.9,
  "text": "1. 已知 $x^2$ 则",
  "spans": [{
    "bbox_pt": [50.0, 80.0, 300.0, 92.0],
    "text": "1. 已知 $x^2$ 则",
    "font": "serif", "size": 10.0, "bold": true, "italic": false,
    "color": "#000000", "provenance": "ocr"
  }],
  "refs": {"line_ids": ["p1-l2"], "figure_id": null, "table_id": null}
}
```

## 验收

- 单测 `tools/test_exam_structure.py`：21 个（含 6 个区域用例），全过；
- 冒烟 `tools/smoke_pipelines.py`：131 通过 / 1 跳过；
- `tools/check_layout_json.py` 对含 `regions` 的新产物无 error；
- 对 `out/acceptance/native_final3/*.layout.json` 重建 exam/quality 不新增 fail；
- 全量 `tools/run_all_checks.py` 与 `--fast` 通过（数字见 `release-standard.md`）。

## 不做的事（留给 T2 及之后）

- 不用区域改写正文、公式或行序；
- 不引入新的 OCR/版面模型（区域只是现有证据的重新组织）；
- 不把 `confidence` 当门禁阈值；
- 不做字级 span 切分（当前一行一个 span，字段已留好）。
