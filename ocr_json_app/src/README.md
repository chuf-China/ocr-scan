# src/ —— 库与流水线

| 模块 | 职责 |
|---|---|
| `ocr_common.py` | **共用底层**：PDF 渲染、OCR 引擎调用、本地网页服务、提示词选择 |
| `ocr_pipeline_layout.py` | **原版式流水线**：原生图形资源 + 逐行坐标 + 插图/表格裁片 + 原生表格单元格 → 单文件网页 |
| `ocr_pipeline_blocks.py` | **结构块流水线**：heading/question/paragraph/table（供程序消费） |
| `layout_regions.py` | **区域层**：把每页的行 / 表格 / 图收成一等区域（稳定 id、`bbox_pt`、`content_type`、`source`、`spans`、`refs`），与行/图/表双向回链；旧产物可幂等派生 |
| `exam_structure.py` | **试卷语义层**：从 layout JSON 重建题号/选项/小问/图/表归属，输出 `exam.json` + `quality.json` |
| `formula_quality.py` | **公式结构审计**：定界符、花括号、`\left`/`\right`、环境成对检查；不改写正文 |
| `formula_review.py` | **公式复核证据层**（T2）：区域裁片与检测框映射、LaTeX 等价归一化、`exact/normalized/partial/differs` 一致性、证据块只增不改、结构校验 |
| `table_review.py` | **表格复核证据层**（T3）：HTML 表格解析、单元格归一化、`exact/cell_mismatch/structure_differs` 一致性、证据块只增不改、结构校验 |
| `repair_plan.py` | **修复队列**：`quality.issues` → `auto_candidate` / `manual` / `blocked`，只路由不编造 |
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
聚成行，和 OCR 行做几何 + 文本比对。默认 `--text-source auto` 先走**有条件快路**：

- 只有纯文字行（无公式定界符、无 `(cid:)`/替换符/控制符、CJK ≥2 字且占比 ≥45%），
  且 `match_native_lines_to_pixels()` 的纵向/横向重叠过门、一对一匹配成功，才用
  `text_source=native-text` 跳过 OCR；公式行、乱码行、缩放/旋转页仍回 OCR。
- `native_text` / `native_quality` / `native_similarity` / `native_mismatch`：每行的候选
  原生文字、质量（`text` / `mixed` / `math` / `empty`）、匹配相似度、是否明显不一致；
- 页和文档的 `native_text` 统计：`chars`、`lines`、`matched_lines`、`mismatched_lines`、
  `native_only_lines`、`fallbacks`、`fast`；另有 `stats.native_text_fast_path`；
- `--text-source ocr` 可全量回退到旧行为。

数学卷的原生字层常把公式拆成 `x2`/`y0`，或使用私有字库；快路的安全门槛不能放宽，
公式正确性和不乱码优先。

行样式由 `annotate_line_styles()` 从 chars 取字号、粗斜体、颜色、衬线/无衬线，写入
`line.style`；`audit_fonts()` 统计字体、PUA、`(cid:)`、替换符和乱码行，结果写
`document.font_audit`。`exam_structure` 把乱码行升级为 `garbled_text` error。

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

## 内容一致性（S4）

`_stem_truncation_reason()` 检查非选择题题干是否以连接词、运算符或未闭合括号结尾；
`build_quality()` 同时检查空大题。结果是 `question_content_truncated` /
`section_empty` error，并写 `content_consistency_ok=false`。它是纯规则检测，不补字。

## 区域层（T1：layout regions）

`lines[]` 是物理行、`figures[]` / `tables[]` 是另外两个数组，三者亲缘关系原先只存在于
隐含约定里；下游只能靠坐标猜。`layout_regions.py` 把每页证据收成一等对象
`page.regions[]`（**只增不改**，`lines[]` 的语义与顺序一律不动）：

- 每个行 / 表格 / 图一个区域，id 为 `p<页>-r<序>`，字段 `page`、`bbox_pt`、
  `content_type`（`text` / `formula` / `table` / `figure`）、`source`、`confidence`、
  `text`、`spans[]`（裁剪后的字级样式：`bbox_pt` / `text` / `font` / `size` /
  `bold` / `italic` / `color` / `provenance`）、`refs`（`line_ids` / `figure_id` /
  `table_id`）。
- **每条物理行恰好属于一个区域**：表格的占位行（`（表格）`）归表格区域，不再额外
  生成行区域；`source` 区分原生文字（`native`）、OCR（`ocr`）、原生矢量图
  （`pdf-vector`）、位图（`pdf-image`）、PDF 表格（`pdf-table`）、覆盖补图
  （`coverage-region`）与框线表格（`table-grid`）；`pdf-vector+labels` 这类复合
  来源会规范化成 `pdf-vector`。
- `confidence` 是**文档化的粗粒度启发式**（原生匹配相似度、投票一致性），只用于
  分诊与排序，不是校准过的概率，不能当门禁阈值。
- 反向链接：区域会写 `line.region_id` / `figure.region_id` / `table.region_id`；
  旧产物（没有 `regions`）由 `ensure_regions()` 就地补齐，**幂等**——第二次调用
  返回 0。试卷层在 `build_exam()` 开头调用它，因此 `region_refs` 对历史产物同样可用
  （前提是行有稳定 id：缺 id 时按流水线约定补 `p<页>-l<序>`）。

```python
from layout_regions import build_regions, ensure_regions, validate_regions

page["regions"] = build_regions(page, page_no)   # 流水线生成时
ensure_regions(layout)                           # 旧产物派生（内存，不写盘）
validate_regions(layout["pages"])                # [] 表示区域层自洽
```

`validate_regions()` 检查 id 格式/唯一、bbox 在页内且不倒置、枚举、`confidence`
区间、`spans` 字级字段、`refs` 是否指向本页真实对象、以及行/图/表的 `region_id`
反向存在。`tools/check_layout_json.py` 只对**含 `regions` 的产物**把它升级为硬错误
（历史演示产物没有该字段则跳过），并核对 `stats.regions` / `document.regions.count`。

## 公式复核证据（T2：formula_review）

`formula_review.py` 只做**证据**，不改正文：`formula_quality.py` 判断“现有公式写法是否
结构完整”，它判断“公式模型独立识别出来的 LaTeX 与现有 OCR 是不是同一个东西”。

```python
from formula_review import (extract_math_segments, normalize_latex, classify_agreement,
                            map_box_to_region, build_evidence, attach_evidence,
                            validate_formula_evidence)

segs = extract_math_segments(line_text)          # 按顺序抽出 \(...\) / \[...\] / $...$
agreement, matched = classify_agreement(latex, segs)   # exact/normalized/partial/differs/no_current
evidence = build_evidence(latex, bbox_pt, 300, "pd-doclayout", score, segs)
attach_evidence(page, entries)                   # 只 append 到 formula 区域的 formulas[]
```

- `normalize_latex()` 只消除**等价写法**（`x^{2}`/`x^2`、`\left\{...\right.`/
  `\begin{cases}`、`\geqslant`/`\geq`、`\sqrt{3}`/`\sqrt3`、`\mathrm{AB}`/`AB`、
  `&` 与 `\displaystyle` 等），不做“修公式”；因此 `normalized` 不等于正确。
- `partial` 表示一方的归一化结果是另一方子串：裁片只覆盖公式的一段（检测框被裁短），
  这类要回去查裁片几何，而不是判模型错；`differs` 才是内容不一致。
- `map_box_to_region()` 只映射 `content_type=formula` 的区域，中心命中优先，其次
  交叠占框面积 ≥0.35；映射不上时返回 `None`（写进 `page.unassigned_formula_boxes[]`），
  **不猜归属**。
- `validate_formula_evidence()` 检查证据块自身（来源枚举、裁片 dpi≥144、一致性枚举、
  LaTeX 非空、`formulas` 只出现在公式区域）；`tools/check_layout_json.py` 对含证据的
  产物把它升级为硬错误。
- 编排与真实推理在 `tools/review_formulas.py`：先 `ensure_regions()`，再 PP-DocLayout_plus-L
  出公式框、按 300dpi 重渲裁片、一次子进程批量跑 PP-FormulaNet_plus-M（CPU，不占显存）。

## 表格复核证据（T3：table_review）

`table_review.py` 与 `formula_review.py` 同构，只做**证据**：现有表格是“PDF 框线 + 逐格
OCR”，它判断“表格模型独立识别出的 HTML/逐格文本与现有表格是不是同一张表”。

```python
from table_review import (parse_html_table, normalize_cell, compare_tables,
                          map_box_to_table, build_table_evidence, attach_table_evidence,
                          validate_table_evidence)

rows = parse_html_table(pred_html)               # tr/td/th/<br> → 二维单元格文本
cmp = compare_tables(native_table_rows(table), rows)   # exact/cell_mismatch/structure_differs
evidence = build_table_evidence(html, cells, bbox_pt, 200, structure_model="SLANeXt_wired",
                                text_model="PP-OCRv5_server_rec", current_rows=current)
attach_table_evidence(page, entries)             # 只 append 到 tables[].reviews[]
```

- `normalize_cell()` 必须先跑：去 `$`/`\(...\)` 定界符与 `\left`/`\displaystyle` 包装、
  Unicode 数学符号（`≤`/`≥`/`∞`/`²`/`√`/`∈`…）转 LaTeX、全角转半角、去空白与尾标点；
  不做归一化会把 `\(a\leq b\)` 与 `a≤b` 判成不一致。
- `compare_tables()` 的 `matched`/`differing` 只统计两边都有的坐标，缺格单独计入
  `only_current`/`only_model`；`fill_rate` 是模型侧非空率，不是 F1。
- `map_box_to_table()` 中心命中优先，其次交叠占框面积 ≥0.35；映射不上返回 `None`
  （写进 `page.unassigned_table_boxes[]`），不猜归属。
- `validate_table_evidence()` 检查来源枚举、裁片 dpi≥144、一致性枚举、HTML 非空、
  单元格 bbox 合法；`tools/check_layout_json.py` 对含 `reviews` 的产物把它升级为硬错误。
- 编排在 `tools/review_tables.py`：按 `page.tables` bbox 裁片，一次子进程跑
  `table_recognition_v2`（CPU、4 线程、关版面检测/方向矫正/去扭曲）。**禁止整页跑
  PP-StructureV3**（14 模型约 1.6 GB 权重，16 GB 内存会卡死）。

## 试卷结构层（exam_structure）

layout JSON 的 `lines[]` 仍是自上而下的物理行序，不能直接当阅读顺序。`exam_structure.py`
用 `column`/`reading_order` 先排栏内顺序，再识别大题、题号、选项、小问；行/图/题目都有稳定
`id`，归属通过 `refs` 回溯。T1 之后题干/选项/小问/图/表还带区域回链（`region_refs` /
`region_id`），区域 id 与页面 `page.regions[]` 一一对应。`build_quality()` 检查缺号/重号、
空题干、选择题选项不足 A–D、图未归属、几何越界、原生/OCR 不一致率和乱码行；
`quality.checks.regions_consistent` 只是区域自洽证据（warning 级），不替代上述硬门；
`status=fail` 时 `tools/check_exam_json.py` 退出 1。

题号写法有两类：`12.` 和行首「（12）」，小问也从「（1）」数起，所以 `（n）` 只有在 `n`
大于当前最后一题时才是新题号（详见 `docs/specs/section-and-question-detection.md`）。
大题标题里的「共 N 小题」用来收口：收满后再出现题号说明小标题整行被吞，此时开
`inferred` 大题并报 `section_heading_missing` warning；同一标签 + 同一 `共 N 小题` 的标题
第二次出现视为文末参考答案区，其后不再建题。OCR 丢行首数字/整页打散属真实缺陷，
只能如实报 `question_number_gap`，不许为过门放宽规则。

```powershell
python tools\build_exam_json.py out\exams\2023beijing.layout.json
python tools\check_exam_json.py out\exams\2023beijing.layout.json
```
