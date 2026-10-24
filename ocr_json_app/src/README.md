# src/ —— 库与流水线

| 模块 | 职责 |
|---|---|
| `ocr_common.py` | **共用底层**：PDF 渲染、OCR 引擎调用、本地网页服务、提示词选择 |
| `ocr_pipeline_layout.py` | **原版式流水线**：原生图形资源 + 逐行坐标 + 插图/表格裁片 + 原生表格单元格 → 单文件网页 |
| `ocr_pipeline_blocks.py` | **结构块流水线**：heading/question/paragraph/table（供程序消费） |
| `exam_structure.py` | **试卷语义层**：从 layout JSON 重建题号/选项/小问/图/表归属，输出 `exam.json` + `quality.json` |
| `formula_quality.py` | **公式结构审计**：定界符、花括号、`\left`/`\right`、环境成对检查；不改写正文 |
| `paths.py` | 项目根与所有默认路径（脚本不依赖当前工作目录） |

层级是单向的：`paths` / `ocr_common` 不 import 任何流水线；两条流水线只依赖它们。
早先 `ocr_pipeline_layout` 是从 `ocr_pipeline_blocks` 里 import 的（那时两者只有渲染与 HTTP 共用），
于是"扫描 PDF 的库"依赖了"搜索排序格式的程序"——现在共用部分抽到了 `ocr_common`。

```powershell
python src\ocr_pipeline_layout.py --pdf data\试卷.pdf --out out\paper.layout.paddle.json
python src\ocr_pipeline_blocks.py --pdf data\试卷.pdf --out out\paper.ocr.json
python src\ocr_pipeline_layout.py --serve-only --out out\paper.layout.paddle.json --port 8780
```

两者都提供了 `main()`，可直接当脚本跑。详细设计取舍见 ../README.md。

## 原生文字层（layout 流水线）

PDF 本身有文字层时，`ocr_pipeline_layout.py` 会从 `pdfplumber` 的 chars 取原生字符，按 y
聚成行，和 OCR 行做几何 + 文本比对；结果只作为**旁路**，不直接覆盖 OCR：

- `native_text` / `native_quality` / `native_similarity` / `native_mismatch`：每行的候选原生文字、
  质量（`text` / `mixed` / `math` / `empty`）、匹配相似度、是否明显不一致；
- 页和文档的 `native_text` 统计：`chars`、`lines`、`matched_lines`、`mismatched_lines`、
  `native_only_lines`、`fallbacks`；
- 只有 OCR 行文字为空、且原生行质量是 `text` 时才用 `native-text` 兜底；公式行仍走 OCR。

数学卷的原生字层常把公式拆成 `x2`/`y0`，或使用私有字库；原生文字只用于质量诊断和
“OCR 缺字”兜底，不能拿它替换公式。

## 页面分类与覆盖率（S1）

每页有 `page_type`（`blank` / `no-lines` / `text`、横竖方向、栏数、是否有文字层、
PDF rotation）和 `coverage`。覆盖审计把行盒与插图盒画成掩膜，找出仍有成片墨迹的
`uncovered_regions`；文档级汇总在 `document.coverage`。它不依赖 OCR，能发现漏行、
漏图、旋转页和整页扫描背景。

覆盖率用 `audit_page_coverage()`；页面分类用 `classify_page()`。`exam_structure`
把未覆盖区域转成 `page_uncovered_ink` warning，`checks.page_coverage_ok` 为 0 才表示
没有未覆盖区域。图上字母标注紧贴裁片盒、表格框线、扫描噪点都可能触发 warning；
先看 pt 坐标，再决定扩图或补行。

## 公式结构（S2）

`formula_quality.py` 按 `document.engine` 选 `\(...\)` 或 `$...$`，检查每条公式的
花括号、`\left`/`\right`、`\begin{...}`/`\end{...}` 和空公式/替换字符。它只报告
issue，不改正文；结构错误由 `exam_structure.build_quality()` 升级为 `error`，
真实 KaTeX 编译仍由 `verify/verify_math.mjs` 单独验证。

## 表格结构（S3）

`PdfFontProbe.table_boxes()` 取 `pdfplumber.find_tables()` 的 bbox、行列数和单元格
bbox；`ocr_table_cells()` 按单元格裁片逐格 OCR。每页写 `tables[]`（`headers` /
`rows_data` / `text_available`），同时保留 `table-grid` 裁片兜底。**不使用**
`pdfplumber.extract()` 的文本，因为数学卷中文字库常被抽成 `(cid:0)`。

`exam_structure` 按表格 bbox 到题目行的距离归属，写 `question.tables` 与
`unassigned.tables`；行级 `source=pdf-table-cells` 的表格不重复归属。
`build_quality()` 检查空表头/空行/空单元格/未归属表，并审计表格单元格里的公式结构。

## 试卷结构层（exam_structure）

layout JSON 的 `lines[]` 仍是自上而下的物理行序，不能直接当阅读顺序。`exam_structure.py`
用 `column`/`reading_order` 先排栏内顺序，再识别大题、题号、选项、小问；行/图/题目都有稳定
`id`，归属通过 `refs` 回溯。`build_quality()` 检查缺号/重号、空题干、选择题选项不足 A–D、
图未归属、几何越界和原生/OCR 不一致率；`status=error` 时 `tools/check_exam_json.py` 退出 1。

```powershell
python tools\build_exam_json.py out\exams\2023beijing.layout.json
python tools\check_exam_json.py out\exams\2023beijing.layout.json
```
