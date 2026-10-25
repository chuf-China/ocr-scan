# 下一会话交接：原生 PDF 试卷识别（P0–P3 之后）

> 交接日期：2026-10-25  
> 仓库：`C:\Users\Administrator\Desktop\ocr\ocr scan`  
> 应用：`ocr_json_app`  
> 当前 HEAD：`7d0e7ac T1 区域层：layout regions、题目回链与校验`（本次会话两个提交：
> `26cd581 添加下一会话交接文档`、`7d0e7ac T1 区域层`）  
> 本地领先 `origin/main` 13 个提交，**尚未推送**。

新会话请先读本文件，再读：

1. `AGENTS.md`（仓库硬约束）
2. `ocr_json_app/README.md`（流程、命令、排障）
3. `ocr_json_app/docs/specs/native-pdf-quality.md`（P0–P3 规格）
4. `ocr_json_app/docs/specs/release-standard.md`（发布门）
5. `C:\Users\Administrator\Desktop\ocr\gpt.md`（用户认可的总体方案，368 行）

不要重新做 S1–S6 或 P0–P3；它们已经提交。下一步是补 `gpt.md` 里**尚未落地**的部分。

---

## 0. 2026-10-25 本次会话更新（T1 已完成，T2 取证件已出）

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
  `rec_formula`），必须自己包 `$...$`；整行 bbox 会干扰单公式模型，建议用已在盘的
  PP-DocLayout_plus-L **只做公式裁片几何**。唯一未实测：CPU 单裁片推理延迟/质量
  （需用户授权跑一次）。
- **仍等用户表态**（见 §9）：目标口径、是否接受公式模型的延迟/显存代价、验收集是否扩到
  10–15 页原生电子卷。未答前不要启动 T2 模型集成。

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
- 全量检查 **21/21**；单元测试 **15/15**；冒烟 **121 通过 / 1 跳过**（本机无 Python 3.11）

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

**T1 region schema** 和 **T2 公式专项**。  
MinerU、PP-StructureV3、LLM 结构重建都不是第一优先级；在 8GB 上同时上会把系统变慢且更难评测。

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
python tools\test_exam_structure.py     # 当前 15/15
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

## 9. 需要用户确认的决策

1. 目标是**视觉还原**还是**可编辑题库/语义理解**？
   - 视觉还原：优先原生 PDF exact 重建；
   - 语义题库：优先公式内容 + 结构模型。
2. 是否接受公式专项模型带来额外延迟/显存，换取内容准确率？
3. 验收集是否要从 2 页 A3 扩展到 10–15 页原生电子卷？

在这三个问题没有答案前，不要同时上 MinerU + PP-StructureV3 + LLM + 公式双模型。

---

## 10. 一句话交接

**当前系统是“可审计的版式 + 试卷结构”低风险子集；下一步不是继续调规则，而是补 `gpt.md` 的 region schema 和公式专项，并用内容级评测证明收益。**
