# 数学试卷 OCR 发布标准

本文件是 S1–S6 之后的**发布门**，不是又一层识别模型。它回答两个问题：

1. 什么条件下可以对外说“这套流水线可用于数学试卷”；
2. 一次发布必须留下哪些可复核证据。

发布对象是 `ocr_json_app/` 的版式 + 试卷结构 + 质量层，不是某一套试卷的截图。

## 1. 发布门槛（全部满足才可发布）

### 1.1 代码与回归

在 `ocr_json_app/` 下执行，全部退出码为 0：

```powershell
$env:PYTHONIOENCODING = "utf-8"
python tools\run_all_checks.py          # 21/21
python tools\run_all_checks.py --fast   # 18/18
python tools\test_exam_structure.py     # 13/13
python tools\smoke_pipelines.py         # 116 通过 / 1 跳过（跳过项须说明原因）
```

- `run_all_checks.py` 包含三条既有样卷、结构块产物、公式编译和真浏览器渲染；
- 跳过不等于通过；如果本机没有 Python 3.11，`smoke_pipelines.py` 的 3.9 语法项会跳过，
  发布说明里必须写明“最低版本未实测”；
- 任一检查失败，先修复，不得用 `--advisory` 或放宽阈值绕过。

### 1.2 20 套验收集

```powershell
# 从已落盘的 20 套 layout JSON 重算结构/质量（不重新 OCR）
$layouts = Get-ChildItem out\acceptance\final25\*.json |
  Where-Object { $_.Name -notmatch '\.(exam|quality)\.json$' -and $_.Name -ne 'acceptance.json' }
python tools\run_exam_acceptance.py --reuse @($layouts.FullName) --out-dir out\acceptance\release
```

以 `data/exams/` 与 `data/exams/corpus/` 去重后的 20 套为准，报告必须满足：

| 指标 | 门槛 |
|---|---|
| 通过套数 | 20/20 |
| 页面覆盖率 | 每套 ≥ 99% |
| 公式结构通过率 | 每套 ≥ 95% |
| 表格单元格填充率 | 每套 ≥ 90% |
| 人工动作/页 | 每套 ≤ 1.0 |
| 文字选择题选项 | 必须 4×题数，且非空 |
| 质量门 | `pass` 或 `warn`；`fail` 不允许 |
| 版式/渲染/结构检查 | 全部退出码 0 |

`quality=warn` 只允许两类：纯图形选项（`choice_options_graphical`）和可解释的
未覆盖墨迹/原生字层差异；warning 必须在报告里逐条可见，不能静默忽略。

### 1.3 未见样本

发布前另取 **3 套未参与调参**的 PDF，用同一命令完整跑一遍：

```powershell
python tools\run_exam_acceptance.py data\exams\new3\*.pdf --out-dir out\acceptance\release_new3
```

要求 3/3 通过，且每套的 `layout/exam/quality` 三件套、日志、阅读器都在。  
当前基线见 `out/acceptance/new3_final2/`：3/3，平均覆盖率 99.96%。

## 2. 发布物

一次发布至少包含：

- 代码提交：S1–S6 的实现、测试、文档；
- `docs/specs/s1-*.md` … `s6-generalization.md` 与本文件；
- 20 套验收 `acceptance.json` / `acceptance.md`；
- 3 套未见样本的 `layout.json` / `exam.json` / `quality.json` / 单文件阅读器；
- 已知限制清单（见第 4 节）。

不要求把整批 OCR 产物提交进 Git；`out/` 下的演示产物按既有约定处理，验收报告单独保留。

## 3. 数据与结构约束

发布版必须保持：

- 版式 JSON `schema_version=1`，每行有稳定 `id`、`column`、`content_type`、`reading_index`，
  每页有 `reading_order` 和 `coverage`；
- 试卷层 `sections/questions/options/subquestions/figures` 的 refs 能回指版式行；
- 质量层 `quality.status` 只能是 `pass` / `warn` / `fail`；
- 修复队列只路由，不自动补题干/选项/表头/题号或改公式；
- 历史无 `schema_version` 产物只用于回归告警，不能作为发布样本。

## 4. 已知限制（发布说明必须保留）

- 公式指标是**结构可解析率**，不是公式内容准确率；公式语义错误仍需人工或上层模型。
- 表格指标是**非空单元格填充率**，不是与人工标注对比的 F1。
- 覆盖率只证明墨迹被行/图/表框覆盖，不证明每个字都识别正确。
- 扫描噪点、手写标注、极低分辨率、非数学排版仍可能产生 warning 或人工动作。
- 图形选项、跨栏图、跨页续题有专门判据，但不是“所有版式都见过”。
- llama.cpp 引擎即使 `--temp 0` 也不保证确定性；发布验收默认建议 `--vote > 1` 或对失败页重跑。

## 5. 当前基线（2026-10-25）

- 代码：P0–P3 原生 PDF 改造提交；
- 23 套（20 套历年 + 3 套未见 2022 卷）：`out/acceptance/native_final3/acceptance.md`，23/23；
  原生直出 34.22%，样式覆盖 100%，乱码行 0，平均覆盖率 99.98%，人工动作 0.15/页；
- 全量检查：21/21；单元测试 15/15；冒烟 121 通过 / 1 跳过。

基线只是“当时证据”，不是永久承诺；重跑后数字变化要更新本节或发布说明。

## 6. 不发布的情形

出现以下任一情况，停止发布：

- 20 套中有 `fail`，或覆盖率/公式/表格/人工动作任一越线；
- 新样本出现缺题、缺选项、整栏漏识别、跨栏文字互串；
- 需要靠 `--advisory`、手工改 JSON 或特例代码才能过门；
- 只能展示截图，拿不出 `layout/exam/quality` 与日志。
