# 下一会话交接：原生 PDF 试卷识别（P0–P3 之后）

> 交接日期：2026-10-25  
> 仓库：`C:\Users\Administrator\Desktop\ocr\ocr scan`  
> 应用：`ocr_json_app`  
> 当前 HEAD：以 `git log -1 --oneline` 为准（T1 区域层 `7d0e7ac`、T2 证据链
> `edc8af7`、T2 归一化 `9357e0a`、A/B 工具 `00f6d6b` 之后的提交）  
> 本地领先 `origin/main`（`0be35f4`）若干提交（以 `git rev-list --count origin/main..HEAD`
> 为准），**尚未推送**。

新会话请先读本文件，再读：

1. `AGENTS.md`（仓库硬约束）
2. `ocr_json_app/README.md`（流程、命令、排障）
3. `ocr_json_app/docs/specs/native-pdf-quality.md`（P0–P3 规格）
4. `ocr_json_app/docs/specs/release-standard.md`（发布门）
5. `ocr_json_app/docs/specs/t2-formula-regions.md`（T2 公式区域专项，已实测未接入）
6. `C:\Users\Administrator\Desktop\ocr\gpt.md`（用户认可的总体方案，368 行）

不要重新做 S1–S6 或 P0–P3；它们已经提交。下一步是补 `gpt.md` 里**尚未落地**的部分。

---

## 0. 2026-10-25 本次会话更新（T1 完成，T2 阶段 1 已落地并实测）

- **T1 区域层已实现并提交**（`7d0e7ac`）：新增 `src/layout_regions.py`，每页 `page.regions[]`
  一等区域（`p<页>-r<序>`、`bbox_pt`、`content_type`、`source`、`confidence`、`spans`、`refs`），
  与行/图/表双向回链；原生表格补 `p<页>-t<序>` 与 `page`；文档级 `document.regions`、
  `stats.regions` / `stats.region_types`；`exam.json` 的题干/选项/小问/图/表带
  `region_refs` / `region_id`；`quality.checks.regions_consistent` 只作 warning；
  `check_layout_json` 对含 `regions` 的新产物硬校验；`ensure_regions()` 幂等派生旧产物。
  规范见 `ocr_json_app/docs/specs/t1-layout-regions.md`，AGENTS.md 新增硬约束 23。
- 校验基线（本次实测）：单元 **21/21**（新增 6 个区域用例）、冒烟 **131 通过 / 1 跳过**、
  `run_all_checks.py` **21/21**、`--fast` **18/18**。
- 23 套既有 layout 重建 exam/quality：**15 pass / 8 warn / 0 fail**，与旧质量文件的
  `Counter({'pass': 15, 'warn': 8})` 完全一致；`run_exam_acceptance.py --reuse` 跑 23 套
  **23/23 通过**（覆盖率 99.74–100%），报告在 `out/acceptance/t1_reuse/acceptance.md`。
- 端到端：起 `llama-server`（paddleocr-vl，8080，`-Ctx 16384`）跑样例卷 6 页 → 77 行 /
  28 含公式行 / 5 图；结构门、渲染门通过；`status=warn`（第 17 题表格 4x3 有空单元格），
  `stats.regions=83`（text 49 / formula 28 / table 2 / figure 4）。跑完已停引擎。
- **T2 取证结论**（`out/_t2_probe/report.md`，零副作用）：PP-FormulaNet_plus-M 权重已在
  `C:\Users\Administrator\.paddlex\official_models\`（4 个模型 = `formula_recognition.yaml`
  全部 SubModules，748.8 MiB）；paddle 3.2.1 是 **CPU 版**，socket 级实测零联网即可构建；
  不占显存、不抢 8080，但会抢 CPU。主环境与 `formula-venv` 都**没有 fitz**；UniMERNet 全机
  不存在；PP-StructureV3 还缺 12 个模型（需用户批准下载）。模型输出是裸 LaTeX（键
  `rec_formula`），必须自己包 `$...$`。
- **§9 三个决策已由用户确认**（见 §9）：目标两者都要、分阶段；接受公式模型代价但只跑
  公式区域；验收集扩 3–5 套 10–15 页原生电子卷。
- **CPU 裁片实测**（`out/_t2_probe/`，用户已授权“只跑公式区域”）：
  - 整行裁片（300dpi 重渲 + 3pt）：一行多公式（选项行 `(A)…(D)`）算式对但分隔符退化
    成「麵」→ **不可用**；单条内联公式 + 中文（第 8 题）内容**完全正确** → 可用。
  - **PP-DocLayout_plus-L 能给出逐条公式框**（200dpi 检测页 1：83 框 / 31 个 `formula`，
    含行内小公式；检测 1216 ms/页）。用这些框裁单条公式跑 `formula_recognition`
    （目录批量、模型只初始化一次）：分段函数 2743 ms **内容正确**、行内小公式 1345 ms 正确。
  - 成本：初始化 ≈4 s/进程、单条公式 1.3–2.7 s、≈31 条/页 → ≈1–1.5 分钟/页，2 页卷
    ≈2–3 分钟。**坑**：`layout_detection --save_path` 会为可视化去抓
    `~/.paddlex/fonts/PingFang-SC-Regular.ttf`（本次真联网下载并已缓存），集成时必须
    避免隐式下载（不落可视化 PNG 或先确认字体缓存）。
  - 结论：裁片按公式框（`crop_source="pd-doclayout"`），不用整行、也不用字符比例猜；
    设计见 `ocr_json_app/docs/specs/t2-formula-regions.md`。**阶段 1 已接入**（证据层，
    见上文 T2 阶段 1 条目）；阶段 2 才讨论"是否把公式模型的 LaTeX 升级成正文来源"。
- **语料已到位**（用户授权"你去搜索"）：`data/eval/` 有 5 套 10–12 页原生电子数学卷 +
  `SOURCES.md`（公开直链、逐页字符数、文字层证据、失败记录）。本机原有语料仍是最多 6 页
  A4，其余 2 页 A3；这 5 套用于扩验收，**不替代** `data/exams/` 的 23 套回归。
- **T2 阶段 1（公式区域证据链）已实现并提交**：`src/formula_review.py`（纯函数层）、
  `tools/review_formulas.py`（PP-DocLayout_plus-L 出逐条公式框 → 300dpi 重渲裁片 →
  一次 `PP-FormulaNet_plus-M` 子进程批量识别 → 证据写 `regions[].formulas[]`）、
  `tools/build_formula_ab.py`（按一致性配额抽样、拼裁片对照图 + `review.md`）、
  `check_layout_json` 对含 `formulas` 产物硬校验、冒烟 `[13/13]`。AGENTS.md 新增约束 24。
  **只写证据，不改 `line.text` / `region.text`**。
- **5 套新语料实测**（详见 `docs/specs/t2-formula-regions.md`）：58 页 / 1186 行 / 674 含公式行
  → 1145 条证据、601 个命中区域、312 个未归属框，公式复核共约 32 分钟 CPU。
  一致性：exact 141 / normalized 509 / partial 285 / differs 207 / no_current 3。
- **内容级 A/B（eval01 抽 48 条，目视 16 条）**：相同 12 / 现有更好 3 / 模型更好 1。
  模型在裁片内部基本正确（含 `\boldsymbol`/分段函数），但框贴边会混入 `\mid`/`\left(`，
  或只覆盖一行里的一条公式 → **不能替代整行 OCR，只能当证据/复核与修复候选**。
- **关键发现（下一步要修的是结构层，不是公式模型）**：这 5 套教师版在行首用「（1）（2）」
  当题号，`QUESTION_RE` 要求数字在行首且后跟 `.、．)）`，于是内容门 **5/5 fail**
  （`section_empty` / `question_number_gap` / `choice_options_incomplete`）。「（1）」在同卷
  也当小问编号，**放宽正则会把小问误判成题号**，必须按大题上下文区分。
  在修完结构层之前，10–15 页卷无法用内容门做验收。
- 校验基线（本次实测）：单元 **21/21**、冒烟 **161 通过 / 1 跳过**、`run_all_checks.py`
  **21/21**、`--fast` **18/18**。

---

## 0.1 2026-10-25 第二轮：结构层题号修复 + 公式内容级基线（用户指令“全做”）

1. **结构层「（1）（2）」题号已修**（`docs/specs/section-and-question-detection.md`）：
   - 题号两类写法：`12.` 与行首「（12）」；`（n）` 只有在 `n` 大于当前最后一题
     （允许 1–5 跳跃）时才算新题号，比它小按小问处理 —— 这样同一份卷里「（1）」既能是
     第 1 题、也能是第 15 题的小问；
   - 大题标题「共 N 小题」用于收口：收满后再出现题号 → 开 `inferred` 大题并报
     `section_heading_missing` warning（不猜标题、不继承选择题选项要求）；
   - 同一标签 + 同一「共 N 小题」的标题第二次出现 → 判为文末参考答案区，其后不再建题
     （否则答案区重复的 一/二/三 会变成空大题）；
   - 选项正则放宽为「标签前可以是汉字」（`（A）充分（B）必要` 同行两选项）。
   - 效果：`data/eval/` 5 套内容门 **0/5 → 2/5 通过**（eval01、eval02 = warn）；另外 3 套
     是真 OCR 缺陷（eval03 第 1 页文本打散、eval05 行首数字丢失、eval04 一条公式
     `\(\underline{\text{\(\sqrt{30}\)}}\)` 嵌套定界符不平衡）。
   - 回归：23 套历年卷仍 **15 pass / 8 warn / 0 fail**；单元 **26/26**、冒烟 **161/1**、
     `run_all_checks` **21/21**、`--fast` **18/18**。
2. **公式内容级人工标注基线（80 条）已完成**：跨 5 套各抽 16 条（differs 8 / partial 4 /
   normalized 3 / exact 1，seed 42），对照 300dpi 裁片逐条目视，判定记在
   `ocr_json_app/data/eval/formula_ab_judgments.json`：
   - 相同 **44**、现有更好 **18**、模型更好 **18**；含正确内容：现有 **62/80**、模型
     **62/80**（均 77.5%）；
   - 模型独有正确项集中在分段函数与被截断的整行片段（`MN=\frac{1}{2}BC` 被现有认成
     `\frac{1}{8}`）；模型错例集中在边缘支架符号（`\mid`/`\left[`/引号/前导逗号）与
     个别字符误识（`±`↔`+`、`|·|`↔`floor`、`>`↔`≥`）。
   - 结论：**没有可测的内容准确率净收益 → 不默认开启替换**；保留为复核/修复候选。
     T2 阶段 2（把模型 LaTeX 升级为正文来源）仍不启动。
3. 本地提交与 `origin/main` 的同步状态以 `git log --oneline origin/main..HEAD` 为准。

---

## 0.2 2026-10-25 第三轮：下载模型 + T3 表格复核证据层（用户指令“下载模型并全量搭建并测试”）

1. **PP-StructureV3 的模型已全部下载并离线验证**：`~/.paddlex/official_models` 从 4 个增到
   14 个（合计约 2.4 GB），与 paddlex 的 `PPStructureV3` 全部 SubModules 对齐；在
   `formula-venv` 里给 `socket.socket.connect` 打补丁实测构建成功、`connections: []`，
   即下载完成后不再联网。
2. **整页跑 PP-StructureV3 会把机器拖死（已发生一次）**：14 个模型约 1.6 GB 权重，
   本机 16 GB 内存在 1654×2339 页图上直接卡死约半小时。此后改为**只用表格识别子模型**
   `table_recognition_v2`，只裁 `page.tables[].bbox_pt`，CPU 线程压到 4，并关闭
   版面检测/方向矫正/去扭曲。此结论已写进 AGENTS.md 硬约束 26 与
   `docs/specs/t3-table-review.md`。
3. **T3 证据层已实现**：`src/table_review.py`（HTML 表格解析、单元格归一化、
   `exact/cell_mismatch/structure_differs/no_current` 四类一致性、证据块、结构校验）+
   `tools/review_tables.py`（裁片 → CPU 子进程 → 证据写 `tables[].reviews[]`）+
   `check_layout_json` 对含 `reviews` 产物硬校验 + 冒烟 `[14/14]`。**只写证据，不改
   `headers` / `rows_data`**。
4. **5 套新语料 15 张表实测**：exact 4 / cell_mismatch 11 / structure_differs 0，约 110 秒
   CPU。剩余差异都是模型自身错（分式压平成 `1625`、`\infty` 读成 `8`、两级表头并成一行、
   左列前缀丢失），没有一处比“框线 + 逐格 OCR”更准 → **与 T2 同口径：默认不开启，
   只作失败区域的可选复核**。规范见 `docs/specs/t3-table-review.md`，AGENTS.md 约束 26。
5. 校验基线（本次实测）：单元 **26/26**、冒烟 **180 通过 / 1 跳过**、
   `run_all_checks.py` **21/21**、`--fast` **18/18**。
6. 推送仍未完成：本会话 `git push` 因 GitHub 凭据需要交互而无法非交互执行，需用户在自己
   终端跑 `git push origin main`。

---

## 1. 当前完成状态

### 验收结果

- `out/acceptance/native_final3/acceptance.md`：**23/23 通过**
  - 20 套历年真题 + 3 套未见 2022 卷（新高考 1、北京、全国甲理）
  - 平均覆盖率 **99.98%**
  - 公式结构 **100%**，表格填充 **100%**
  - 人工动作 **0.15 条/页**
  - 原生文字直出 **34.22%**，样式覆盖 **100%**，乱码 **0 行**
- `out/acceptance/native_final3/view/index.html`：23 套单文件阅读器总览
- `out/acceptance/native_final3/view/pages_2024quanguoli/page01.png`：字体/散点图实渲样例
- 全量检查 **21/21**；单元测试 **26/26**；冒烟 **180 通过 / 1 跳过**（本机无 Python 3.11）

### 已实现的能力

| 层 | 位置 | 状态 |
|---|---|---|
| P0 原生文字快路 | `src/ocr_pipeline_layout.py` | `--text-source auto`；只对安全纯文字行用 `native-text`，公式/乱码行回 OCR |
| P1 原生矢量图 | `src/ocr_pipeline_layout.py` | 图片/矢量簇、表格排除、标签扩框、14pt 合并、300dpi、未覆盖墨迹兜底裁图 |
| P2 行样式 | `src/ocr_pipeline_layout.py` + `layout_viewer/viewer_template.html` | 字号/粗体/斜体/颜色/衬线；阅读器在 `fitLineWidth()` 前应用 |
| P3 字体审计 | `src/ocr_pipeline_layout.py` + `src/exam_structure.py` | PUA、`(cid:)`、U+FFFD、控制符进 `font_audit` 与硬 error |
| 试卷结构 | `src/exam_structure.py` | `sections/questions/options/subquestions`、栏序、跨页、图表归属、质量门 |
| 表格 | `src/ocr_pipeline_layout.py` + `src/exam_structure.py` | pdfplumber 框线 + 逐格 OCR；原生表归属 |
| 质量/修复队列 | `src/exam_structure.py`（quality）+ `src/repair_plan.py` | issue → auto/manual/blocked，不自动编造内容 |
| 验收工具 | `tools/run_exam_acceptance.py` | PDF 全跑或 layout 重算；页级/逐套指标 |
| 区域层（T1） | `src/layout_regions.py` | `page.regions[]` 一等区域、双向回链、`ensure_regions` 幂等、`check_layout_json` 硬校验 |
| 公式证据（T2 阶段 1） | `src/formula_review.py` + `tools/review_formulas.py` | 逐条公式框裁片 → CPU 模型 LaTeX 证据写 `regions[].formulas[]`，不改正文 |
| 表格证据（T3） | `src/table_review.py` + `tools/review_tables.py` | 表格 bbox 裁片 → CPU `table_recognition_v2` HTML/逐格证据写 `tables[].reviews[]`，不改正文 |

### 已提交的关键点

- `348c4a2` 试卷结构层
- `6258232` 页面分类/覆盖率
- `a05484b` 公式结构审计
- `cb83fb6` 表格结构
- `caade57` 内容一致性
- `bbd2dc3` 质量修复队列
- `81c018b` 20 套真题验收（S6）
- `a2563cc` 栏首题号/小字行裁片
- `7d5783e` 发布标准
- `741b3f1` P0–P3 原生 PDF

---

## 2. 当前架构的真实边界

当前是：

```text
PDF → 页面渲染 → 栏检测 → 行检测 → 逐行 OCR / 原生文字快路
    → 行级坐标 JSON → 正则+几何重建题目 → 质量门
```

它已经能稳定复现版式、出可审计的 `layout/exam/quality`，但**不是“任意数学试卷理解”**：

- 原生文字只覆盖纯文字行，公式行仍整行 OCR；原生占比 34% 是刻意取舍。
- 样式是**行级中位样式**，不是 span/字符级；一行内混排粗体、斜体、公式会丢细节。
- 公式门只验证定界符/括号/环境，不验证公式内容。
- 表格是“框线 + 逐格 OCR”，复杂合并单元格没有专项兜底。
- 图是 300dpi 位图，矢量语义丢失。
- 题号/选项/小问仍靠正则和几何规则；歧义没有第二判据。
- 23 套主要是 2 页 A3 / 6 页 A4；`gpt.md` 目标是 10–15 页原生电子卷，分布不同。

**不要继续在 line-level 流水线上堆特例。** 下一步应改表示层。

---

## 3. 与 `gpt.md` 的差距（按优先级）

| `gpt.md` 要求 | 当前 | 下一步 |
|---|---|---|
| PyMuPDF 原生文字/字体/矢量路径 | 用 pdfplumber + pypdfium2；当前环境没有 `fitz` | 先验证安装与 8GB 开销，再考虑 `rawdict`/drawings 替代或补充 |
| 区域路由 text/formula/table/figure | 仍是逐行路线 | **T1：region schema** |
| 公式专项 PP-FormulaNet / UniMERNet | 未接入，只有结构门 | **T2：公式区域 + 内容级 A/B** |
| 复杂表格 PP-StructureV3 | 未接入 | T3：只对原生表格失败区域兜底 |
| MinerU 疑难页备用 | 未接入 | T4：只对漏行/乱序/结构冲突页调用 |
| 规则 + 结构化 LLM 重建题目 | 正则/几何为主 | T5：只处理歧义，不常驻 |
| 证据驱动定向复核 | 有质量门，但常整卷重跑 | T6：按 issue 只复查区域 |
| 三份 JSON + Viewer | 已实现 | 保持 |

### 先做哪两个

**T1 region schema** 和 **T2 公式专项**（2026-10-25 均已完成阶段 1：T1 已提交
`7d0e7ac`，T2 证据链已提交 `edc8af7`/`9357e0a`/`00f6d6b` 并在 5 套新语料上实测）。  
MinerU、PP-StructureV3、LLM 结构重建仍不是第一优先级；在 8GB 上同时上会把系统变慢且更难评测。

现在的优先顺序改为：

1. ~~结构层题号写法~~（**2026-10-25 已修**，见 §0.1）：`（1）（2）` 式题号已识别，
   小标题被吞时开 `inferred` 大题，答案区重复标题不再造空大题；5 套新语料内容门
   0/5 → 2/5 通过，剩下 3 套是真实 OCR 缺陷；
2. ~~T2 内容级人工标注基线~~（**2026-10-25 已完成 80 条**，见 §0.1）：模型 62/80、
   现有 62/80，无可测净收益 → 不默认开启替换，保留为复核/修复候选；
3. **下一步候选**：① 对 3 套失败的新语料做定向复核（eval05 小页检测 dpi、eval03
   首页打散、eval04 嵌套定界符公式），② T3/T4/T5/T6 仍按触发条件后置（T3 需先批准
   下载 12 个模型，且本机无 UniMERNet），③ 是否把 `data/eval/` 扩到 15 页题型混合。

---

## 4. 下一会话的具体工作

### T1：区域对象（先做，不改 OCR 行为）

目标：在 `layout.json` 增加一等区域，不破坏现有 `lines[]` 和 23 套回归。

建议最小字段：

```json
{
  "id": "p1-r12",
  "page": 1,
  "bbox_pt": [x0, y0, x1, y1],
  "content_type": "text | formula | table | figure",
  "source": "native | ocr | pdf-vector | pdf-table | coverage-region",
  "confidence": 0.0,
  "text": "...",
  "spans": [
    {"bbox_pt": [..], "text": "...", "font": "...", "size": 12.0,
     "bold": false, "italic": false, "color": "#000000",
     "provenance": "native | ocr"}
  ],
  "refs": {"line_ids": ["p1-l3"], "figure_id": null, "table_id": null}
}
```

要求：

- 区域 ID 稳定，题目通过 `refs` 回链，不复制字符串；
- `content_type` 判定先规则化：公式标记/公式字符密度、表格 bbox、图 bbox、纯文本；
- 保留原有 `lines[]`，新增字段只增不改，旧产物的 `schema_version` 兼容；
- 写单元测试：文本/公式/表格/图各一例，跨栏一例；
- 重跑 23 套，必须仍 23/23。

### T2：公式专项（最大缺口）

先做**公式区域**，不要整页跑公式模型。

1. 从 T1 region 里取 `content_type=formula`；
2. 按原始 PDF bbox 以 250–300dpi 重新渲染裁片，不用 150dpi 截图放大；
3. 在**隔离环境**试 `PP-FormulaNet_plus-M`；之前有 `C:\Users\Administrator\Desktop\ocr\formula-venv`，先验证是否可用，不要假设；
4. 与现有 OCR 做 **公式内容级 A/B**：人工标注 50–100 条（上下标、分式、根号、矩阵、分段函数）；
5. 只有内容准确率确实提升才接入；`gpt.md` 建议 UniMERNet 只复核失败公式，不要双跑所有公式；
6. 保留 `native_result / primary_result / review_result / final` 和来源，不静默覆盖。

**不要**用“KaTeX 能渲染”当公式正确率。当前 100% 只是结构通过率。

### T3：复杂表格兜底

- 原生框线失败、合并单元格、位图表格才调用 PP-StructureV3；
- 不要整卷重跑；
- 表格验收要报 **F1**，不是“单元格非空率”。

### T4：MinerU 备用

只在以下情况调用：

- 覆盖率 <99% 且有未覆盖墨迹；
- 阅读顺序异常/整栏漏行；
- 图文/表格结构冲突。

普通页不跑。每次记录“为什么升级到 MinerU”。

### T5/T6：结构 LLM、定向复核

- 结构 LLM 只处理规则无法确定的歧义，且输出必须可回链到 region；
- 质量门 issue 触发时，只重跑对应 bbox，不重扫整页；
- 8GB 下不要常驻多个 VLM。

---

## 5. 命令

### 起 OCR 服务（一次只起一个）

```powershell
pwsh -NoProfile -File "$HOME\.dsh-llama\start-ocr-server.ps1" -Model paddleocr-vl -Ctx 16384
# 用完
Get-Process llama-server | Stop-Process
```

端口 `8080`；不要同时起 Paddle 和 Ovis。

### 23 套原生验收（必须传 PDF，不能传旧 layout）

```powershell
cd "C:\Users\Administrator\Desktop\ocr\ocr scan\ocr_json_app"
$env:PYTHONIOENCODING='utf-8'
$files=@(Get-ChildItem data\exams -Recurse -Filter *.pdf) + @(Get-Item 'data\静安区2026届高三一模数学试卷.pdf')
$seen=@{}; $inputs=@()
foreach($f in $files){
  $h=(Get-FileHash $f.FullName -Algorithm SHA256).Hash
  if(-not $seen.ContainsKey($h)){ $seen[$h]=1; $inputs += $f.FullName }
}
python tools\run_exam_acceptance.py @inputs --out-dir out\acceptance\native_next --host http://127.0.0.1:8080
```

**注意**：`run_exam_acceptance.py` 也接受 `*.layout.json`；传 layout 会复用旧 OCR，不能证明原生路径。最终验收必须传 PDF。

### 检查

```powershell
$env:PYTHONIOENCODING='utf-8'
python tools\run_all_checks.py          # 当前基线 21/21
python tools\run_all_checks.py --fast   # 当前基线 18/18
python tools\test_exam_structure.py     # 当前 21/21（T1 区域层 +6）
```

### 阅读器

```powershell
node tools\cdp_probe.mjs out\acceptance\native_final3\view\index.html `
  --shot out\acceptance\native_final3\view\index.png --width 1400
```

单页高分辨率：

```powershell
node tools\cdp_probe.mjs out\acceptance\native_final3\view\view_2024全国理_...html `
  --shot-pages out\acceptance\pages_check --dpr 2 --width 1400
```

---

## 6. 验收标准（下一阶段）

T1：

- 23/23 不回归；
- region 字段通过 schema/单元测试；
- 题目 `refs` 能回链到 region 与原始行。

T2：

- 公式内容准确率有独立人工标注基线；
- 新公式路径相对现有 OCR 有可复现提升，才允许默认开启；
- 不提升就保留为可选复核路径，不强行接入。

T3–T6：

- 只在触发条件下运行；
- 每次运行记录来源、耗时、显存峰值；
- 不降低现有 23/23、21/21、15/15。

---

## 7. 硬约束 / 不要做

1. 不要 `git add -A`；`out/acceptance/`、`data/exams/` 等大量产物默认不提交。
2. 不要为了过验收写单卷特例；失败样本先复现，再改通用判据。
3. 不要让模型自动补题干/选项/题号/公式；只能检测、路由、复核。
4. 不要用 `--advisory` 掩盖新产物的缺题/缺选项。
5. 不要同时常驻多个模型；8GB 按需加载、顺序执行。
6. Python 3.9 兼容；不要写 3.12-only 语法。
7. 改行为必须同步 `AGENTS.md`、`ocr_json_app/README.md`、`src/README.md`/`tools/README.md` 和相关 specs。
8. 提交前跑全量检查；提交信息保持短句中文。

---

## 8. 已知坑

- `out/paper.layout*.json` 是没有 `schema_version` 的历史演示产物；内容门只 `--advisory`，不能当新产物基线。
- `run_exam_acceptance.py` 传 layout JSON 会“通过”，但没重跑 OCR；这是之前踩过的假验收。
- 去重必须用 SHA256；`data/exams` 与 `corpus` 有重复卷，且 `静安区...pdf` 在 `data/` 下，不在 `exams/`。
- `cdp_probe` 对 index.html 会等 `__OCR_RENDERED__` 30 秒后继续，属正常。
- 2025 全国 1 第 16 题曾因栏首题号悬在栏缝外丢失；修复是页顶栏首行向左多带 40px，**不要对栏中部行也加**。
- 原生公式行不能直接采用：PDF 字层常把 `x^2` 拆成 `x2`；P0 的 `_native_fast_safe` 是故意保守的。
- 当前 Python 环境没有 `fitz`（PyMuPDF）；要按 `gpt.md` 用 PyMuPDF，先解决安装/许可/显存，不要假设已可用。
- `formula-venv` 是之前的实验环境，模型/版本未锁；复用前先验证。

---

## 9. 用户已确认的决策（2026-10-25）

1. **目标两者都要，分阶段**：先可编辑语义层（公式内容 + 结构），再视觉对齐。
2. **接受公式专项模型的延迟/CPU 代价，但只对公式区域推理**：不重跑整卷、不逐行跑、
   不替代现有 OCR。
3. **验收集扩，先补 3–5 套 10–15 页原生电子卷**（需用户提供 PDF，禁止联网下载）。
   → 2026-10-25 已由用户追加授权"你去搜索"，`data/eval/` 已落 5 套 10–12 页公开原生电子卷；
   接下来是否继续扩到 15 页、以及换不换题型，等用户定。

推论：可以进入 T2 阶段 1（按区域裁片 + 内容级 A/B），设计见
`ocr_json_app/docs/specs/t2-formula-regions.md`；**模型结果不得静默覆盖现有 OCR 文本**，
也不能用“KaTeX 能渲染”当内容准确率。T3（PP-StructureV3）仍有 12 个模型需用户批准
下载，T4（MinerU）/T5（LLM）保持未启动。

---

## 10. 一句话交接

**当前系统是“可审计的版式 + 试卷结构”低风险子集；T1 区域层、T2 公式证据链（含 80 条
内容级基线）与结构层「（1）」题号都已落地，下一步是 3 套新语料的定向复核
（eval03 首页、eval04 嵌套定界符、eval05 小页检测），之后才轮到 T3/T4/T5/T6。**
