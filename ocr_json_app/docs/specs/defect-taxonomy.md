# 缺陷分类与责任归属（六类现象 × 责任方）

> 目的：把"看起来坏了"和"该谁改"分开登记。现象用来验收，责任方用来派活。
> 实现：`src/defect_taxonomy.py`；产物：`quality.json` 的 `summary.defects`。
> 状态：2026-10-25 落地（表格复核之后）。

## 为什么需要这一层

同一现象会跨层出现，按现象直接修必然变成打补丁：

| 现象 | 实测过的三种成因 | 该改哪层 |
|---|---|---|
| 少文字 | 大题标题被 `table_from_text` 当表格吞掉（eval01/02/04） | 版式规则 |
| | OCR 丢行首数字（eval05 `.若复数z 3-i,`） | 识别能力 |
| | 题号正则不认「（1）」式写法 | 结构规则 |
| 少图 | 页眉二维码被当插图（eval01 9 条 warning） | 装饰过滤 |
| | 图检出了但离所有题目行超过 80pt | 语义归属 |
| | 矢量簇漏检，靠覆盖率兜底补回 | 版式检出 |

所以本仓库固定两套分类，不混用：

- **验收分类（六类现象）**：乱码 / 重叠 / 少图 / 少文字 / 多余 / 结构内容错。
  回答"这卷能不能交付"，也是给人看的语言。
- **责任方（三个）**：`program` 架构程序 / `model` 模型 / `input` 输入。
  回答"这个缺陷该谁改"，是派活的依据。修复层（`LAYERS`）在 `program`
  内部再细分为版式 / 文字 / 结构 / 语义 / 契约。

## 六类现象

| key | 名称 | 判据（用户可见） |
|---|---|---|
| `garbled` | 乱码 | 文字/公式渲染成错字符：替换符、`(cid:`、控制符、私有字库方块 |
| `overlap` | 重叠 | 行与行、表与正文、图与正文互相压住 |
| `missing_figure` | 少图 | 该有的图没出现：未检出、被裁掉、归属不到题目 |
| `missing_text` | 少文字 | 该有的字没出现：整行丢失、标题被吞、题号/选项缺、选项为空 |
| `extra` | 多余 | 不该有的出现：幻觉数字、重复行、页眉装饰被当成内容 |
| `semantic` | 结构内容错 | 字和图都在，但归属/顺序/结构/公式内容错 |

"多余"和"结构内容错"是上一轮讨论后补上的两类。前者不显眼但真实存在（幻觉
数字串、重复片段，`check_layout_json` 与结构块检查早就在查）；后者才是真正
伤"可编辑语义题库"的部分——公式值错、表格两级表头被压平、题号归属错，渲染
出来全都"看着正常"。

## 三个责任方与判定规则

| key | 名称 | 怎么判（可操作） |
|---|---|---|
| `program` | 架构程序 | 同一份识别输出，改我们的代码/几何/规则/门禁就能变好 |
| `model` | 模型 | 代码按既定判据工作正常，是 OCR/VLM 输出本身错（认错/丢字/幻觉） |
| `input` | 输入 | PDF 字库或扫描质量本身坏，识别与解析都救不回来，只能如实报 |

判定顺序（不要拍脑袋）：

1. 先看 **文本在不在**。`layout.json` 里找不到该行 → 模型/输入层；行在而结构
   层没认 → 程序（结构规则）。
2. 再看 **几何对不对**。框在但位置/裁切错 → 程序（版式几何）。
3. 最后看 **内容对不对**。框和归属都对、只是内容错 → 模型（识别能力）。
4. 有原生文字层时，比对 `native_text` 与 OCR 文本：原生层就可疑 → 输入。
5. `mixed` 只是登记时的临时状态，必须写清"怎么区分"（见下），不允许长期悬空。

`mixed` 的三个 code 的判定依据：

- `question_number_gap`：该题号的行是否存在于 layout。存在=程序，不存在=模型。
- `question_content_truncated`：被表格占位行顶替=程序，原文本身截断=模型。
- `choice_options_incomplete`：一行多标签没拆开=程序，原文缺字=模型。

## 登记表（code → 现象 → 责任方 → 层）

`src/defect_taxonomy.py` 的 `ISSUE_CODES` 是唯一权威。当前 26 个 code 分布：

| 现象 | code | 责任方 | 层 |
|---|---|---|---|
| 乱码 | `garbled_text` | mixed→模型优先 | 文字 |
| 重叠 | `line_overlap` | 程序 | 版式 |
| | `line_outside_page` | 程序 | 版式 |
| 少图 | `figure_unassigned` | 程序 | 语义 |
| 少文字 | `empty_stem` | 模型 | 文字 |
| | `question_content_truncated` | mixed | 文字 |
| | `choice_options_incomplete` | 程序 | 结构 |
| | `choice_option_empty` | 模型 | 文字 |
| | `table_empty_cell` | mixed | 结构 |
| | `table_header_empty` / `table_rows_empty` | 程序 | 结构 |
| | `page_uncovered_ink` | mixed | 版式 |
| 多余 | `native_ocr_mismatch` | mixed | 文字 |
| 结构内容错 | `question_number_gap` | mixed | 结构 |
| | `question_number_duplicate` | 程序 | 结构 |
| | `choice_labels_unexpected` | 程序 | 结构 |
| | `choice_options_graphical` | 程序 | 语义 |
| | `section_empty` | 程序 | 结构 |
| | `section_heading_missing` | 模型 | 文字 |
| | `formula_structure_broken` / `formula_suspicious` | 模型 | 文字 |
| | `table_formula_broken` | 模型 | 文字 |
| | `table_unassigned` | 程序 | 语义 |
| | `region_inconsistent` / `region_ref_unresolved` | 程序 | 契约 |

## 产物契约（只增不改）

`build_quality()` 在 `summary.defects` 增加：

```json
"defects": {
  "by_class": {
    "missing_figure": {"count": 1, "errors": 0, "warnings": 1,
                       "by_owner": {"program": 1, "model": 0, "input": 0, "mixed": 0}}
  },
  "by_owner": {"program": 1, "model": 0, "input": 0, "mixed": 1},
  "unknown": []
}
```

- `by_owner` 只统计 `count` 之和，`mixed` 单列，不摊给任何一方。
- `unknown` 非空表示有人加了 issue code 却没登记责任；`checks` 里另有
  `defect_codes_registered` 同步反映。
- 不影响 `status`（status 仍只看 errors/warnings 数量），因此不改变既有门禁。

## 一个已完成的例子：页眉二维码

现象：eval01 有 9 条 `figure_unassigned`，看起来是"少图"。

归因：15 个图里 10 个是 54×54pt、位置 (542,28) 的 `pdf-image`——页眉二维码。
它们既不是正文插图，也不该被"补回漏检插图"的兜底逻辑重新加回来。

修法（程序/版式层，一处通用判据）：`_is_page_furniture()` 只按几何判"小
（≤90pt）+ 贴页眉/页脚带（10%）"，命中即当装饰：不进 `figures`，计入覆盖率
（否则会被兜底补成插图），写进 `page.page_furniture[]` 与 `stats.page_furniture`
留证。

结果（eval01 整卷重跑）：figures 15 → 5，warnings 9 → 1（剩 1 条是真未归属
图），coverage 99.258% 不变，`check_layout_json` / `check_render` /
`check_exam_json` 全通过。跨语料验证：29 份 PDF、144 个原生图框里只命中这 10
个二维码，0 误伤。

## 不做什么

- 不把 `defects` 当修复队列：它是分类证据，修复动作仍走 `repair_plan.py`。
- 不给 `mixed` 开特例：能判定就必须判定，判定依据写进 code 的 note。
- 不因为某类缺陷出现次数下降就删 code：code 是契约，只增不改。
- 不按单卷调阈值：任何判定规则的改动必须在 28 套语料上重算。

## 验收

- `tools/smoke_pipelines.py` 的 `test_defect_taxonomy()`：六类/三责任方齐全、
  登记项字段合法、汇总计数、未登记 code 进 unknown、`exam_structure` 里
  `_issue()` 用到的 code 全部已登记。
- `_is_page_furniture` 四条几何断言（页眉二维码/页脚图标为装饰，正文中间小图
  与页眉大图不是）。
- 语料级：任一套 `check_exam_json` 输出 `质量门` 行后，`summary.defects` 的
  `by_owner` 之和 + `mixed` 应等于 errors+warnings。
