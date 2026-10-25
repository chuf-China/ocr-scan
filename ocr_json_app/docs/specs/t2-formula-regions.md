# T2 —— 公式区域专项（公式内容级复核）

状态：**设计 + 实测已完成，未接入流水线**（2026-10，T2 阶段 1）。前置：T1 区域层
（`docs/specs/t1-layout-regions.md`）与 P0–P3（`native-pdf-quality.md`）。

本规格只描述阶段 1：**按区域重渲公式裁片 → PP-FormulaNet_plus-M 出 LaTeX → 内容级
A/B**。阶段 2（视觉对齐）不在这里展开。

## 用户已确认的口径（NEXT_SESSION §9）

1. **两者都要，分阶段**：先可编辑语义层（公式内容 + 结构），再视觉对齐。
2. **接受公式模型的额外代价，但只跑公式区域**：只对 `content_type=formula` 的区域
   推理，不重跑整卷、不逐行跑、不代替现有 OCR。
3. **验收集要扩，先补 3–5 套 10–15 页原生电子卷**。

## 为什么不能整行一刀切（实测）

2026-10-25 用 `2022北京卷` 第 1 页两条公式行，按原始 PDF 以 300dpi 重渲裁片
（pypdfium2，外扩 3pt），`formula-venv` 的 `paddleocr formula_recognition --device cpu`：

| 裁片 | 输入 | 输出 | 判定 |
|---|---|---|---|
| 选项行（4 个公式） | `(A) [-5,3] (B) [-3,5] (C) [-6,4] (D) [-4,6]` | `\begin{array}{l}(A)[-5,3]\quad 麵 \quad(B)...` | 算式对，分隔符退化成「麵」→ **不可用** |
| 中英混排行（1 个公式） | `8. 若 (2x-1)^4=...，则 a0+a2+a4= （ ）` | `8.\ 若 \ (2x-1)^{4}=a_{4}x^{4}+...=\ \left(\begin{array}{cc}&\\&\end{array}\right)` | 公式内容完全正确 → **可用** |

结论：

- 模型吃的是**整张图**，一行里有几段公式它不知道；一行多公式会被拼成一个 array。
- 单条内联公式 + 少量中文（含 `\ ` 分隔）质量足够，可作为复核路径。
- 所以裁片粒度必须是**公式区域/span**，不能是整行；T1 的 `regions[].content_type=formula`
  是裁片入口，但**区域当前等于整行**，真正的公式 span 几何还没有。

### 补充实测：PP-DocLayout_plus-L 能给到单条公式框（2026-10-25）

对同一页按 200dpi 跑 `paddleocr layout_detection --model_name PP-DocLayout_plus-L
--device cpu`：

- 结果 **83 个框 / 31 个 `formula`**（另有 text 45、image 2、paragraph_title 3、
  doc_title 1、number 1），检测耗时 **1216 ms**，总 22 s（含约 18 s 的一次性字体下载，
  见下）；
- `formula` 框**包含行内公式**（例如选项行被切成多个 20–90pt 宽的小框），不是只给
  行间大公式。这消除了“只能按行 OCR 文本比例猜 span”的退化路径；
- 框坐标是检测图的像素，除以 `dpi/72` 得 pt，再从原始 PDF 以 300dpi 重渲裁片；
- 用这些框裁两条公式跑 `formula_recognition`（目录批量输入，模型只初始化一次）：
  - 分段函数（548x147px）：`f(x)=\left\{\begin{aligned}&-ax+1,&x<a,\\&(x-2)^{2},&x\geqslant a.\end{aligned}\right.` → **内容正确**，2743 ms；
  - 行内小公式（81x105px）：`\frac{\pi}{3},` → 正确，1345 ms。
- **坑**：`layout_detection` 带 `--save_path` 会为了画可视化框去 `~/.paddlex/fonts/`
  抓 `PingFang-SC-Regular.ttf`（本次真的联网下载了，之后缓存命中）。集成时必须：
  要么不落可视化 PNG，要么先确认字体在缓存里、并把这一点写进运行前置检查；
  检测结果 JSON 不受影响。

## 设计

### 1. 公式 span 几何从哪来

三条候选，按优先级：

1. **原生字层**（仅纯文字行可用）：`pdfplumber` 的 char 坐标能直接给出公式字符的
   bbox；但公式行本来就走 OCR（`_native_fast_safe` 故意拒绝），实际拿不到。
2. **PP-DocLayout_plus-L**（已在盘，`PP-DocLayout_plus-L` 属于
   `.paddlex\official_models\`，label 含 `formula`；已实测能给出**逐条公式框**，
   含行内公式）：只用于**裁片几何**，正文文本仍以现有 `lines[]` 为准。必须记录来源
   （`crop_source="pd-doclayout"`），不能把它的文本混进正文，也不能把它的坐标系
   当语义（AGENTS 硬约束：坐标不是语义、引擎产物不得混名）。
3. 退化路径：对该行 OCR 文本按 `$...$` / `\(...\)` 分段，按字符比例切行盒
   （近似，只用于没有 DocLayout 的环境，必须在结果里标 `crop_source="heuristic"`）。

阶段 1 只做 **2 + 1 的失败回退**，并把用的哪条写进结果。

### 2. 推理通道

- 独立子进程：`formula-venv\Scripts\python.exe -m ...` 或
  `formula-venv\Scripts\paddleocr.exe formula_recognition -i <png> --device cpu`；
  主环境（3.12）不能 import paddle，**禁止**在主进程内联推理。
- 一个进程内批量处理一张卷的所有裁片（模型初始化 ≈4 s，单条公式裁片 1.3–2.7 s）；
  不要一张裁片起一个进程（每张会多付 4 s）。CLI 的 `-i` 可以直接传目录。
- CPU 串行：与 `llama-server` 抢 CPU，必须排在 OCR 之后，或 `OMP_NUM_THREADS=2`。
- 离线：模型缓存已存在，`official_models.py` 只在目录缺失时联网；不要为 T2 引入
  任何需要下载/密钥的依赖。PP-StructureV3（T3）缺 12 个模型、UniMERNet 全机不存在，
  都不在本阶段范围内。

### 3. 输出与来源（不静默覆盖）

每个公式区域多一个 `formula` 证据块，**不改** `line.text` / `region.text`：

```json
{
  "source": "pp-formulanet-plus-m",
  "model": "PP-FormulaNet_plus-M",
  "crop": {"bbox_pt": [..], "dpi": 300, "crop_source": "pd-doclayout"},
  "latex": "\\frac{a}{b}",
  "text_marked": "$\\frac{a}{b}$",
  "confidence": 0.0,
  "review": {"needed": true, "reasons": ["delimiter_unwrapped"]},
  "compare": {"current": "...", "agreement": "exact|normalized|differs"}
}
```

不变量：

- 只增字段；`lines[]` / `regions[]` / `exam.json` 现有字段语义不变；
- `native`（字层）/ `primary`（现有 OCR）/ `review`（公式模型）/ `final`（人工确认）
  四层来源必须能区分，**不得**直接写 `final`；
- 模型输出是裸 LaTeX（键 `rec_formula`），必须自己包定界符，否则
  `formula_quality` 数到 0、阅读器不渲染；
- 与现有 OCR 文本的比较用**内容级**判断（结构解析 + 归一化 token 差异），不用
  “KaTeX 能渲染”当正确率（AGENTS 硬约束 20）。

### 4. 内容级 A/B 协议（决定是否默认开启）

1. 从 3–5 套卷里抽 50–100 条公式区域，人工标注标准 LaTeX（类别覆盖：上下标、分式、
   根号、矩阵、分段函数、带中文的内联公式）。
2. 对同一区域跑三条：现有 OCR 文本、公式模型（区域裁片）、公式模型（整行裁片做对照）。
3. 指标：表达式**完全匹配**率、归一化匹配率、CDM（字符级距离），按类别分组。
4. 只有“区域裁片”在内容级显著优于现有 OCR，才允许默认开启；否则只保留为可选复核
   路径（`--formula-review`），并在报告里写清“未提升”。

### 5. 预算（实测，CPU）

| 项 | 数值 |
|---|---|
| 模型初始化 | ≈4 s / 进程 |
| 版面检测（PP-DocLayout_plus-L） | 1216 ms / 页（200dpi A3）+ 一次性初始化 |
| 单条公式裁片推理 | 1.3–2.7 s（同进程目录批量；公式越复杂越慢） |
| 整行裁片推理 | 2.9–3.2 s（对照，不推荐） |
| 每页公式框 | 31 条（2022北京卷 p1，200dpi 检测）→ ≈1–1.5 分钟/页 |
| 单卷（2 页 A3） | ≈2–3 分钟 |
| 显存 | 0（paddle 3.2.1 是 CPU 版） |
| 磁盘 | 权重 748.8 MiB（已在盘，无需下载） |

一次 CLI 调用可传**目录**，模型只初始化一次（实测 2 张图共 9 s：4.6 s 初始化 +
2.7 s + 1.3 s）；集成时按“一页一批”组织，不要一张一进程。

## 验收（阶段 1 落地后）

- 新字段只增不改；旧产物无 `formula` 块时所有门跳过；
- `check_layout_json` 对含公式证据块的产物做结构校验（bbox/dpi/来源枚举/LaTeX 非空）；
- `quality` 增加“公式区域已复核比例”，只作诊断（warning/统计），**不进硬门**；
- 23 套 + 新增 3–5 套不回归；`run_all_checks` / `--fast` / 单元 / 冒烟基线保持；
- 人工标注集与三条路径的对照结果写进验收报告，含失败样例。

## 不做的事

- 不下载新模型、不引入联网依赖、不安装 PyMuPDF/UniMERNet；
- 不把公式模型结果直接写进 `final` 或覆盖现有 OCR 文本；
- 不用公式模型重跑整卷，也不对 text/table/figure 区域调用它；
- 不用“渲染通过率”替代内容准确率。

## 现有产物

- `ocr_json_app/out/_t2_probe/report.md`：离线/CPU/依赖/语料取证（零副作用）；
- `ocr_json_app/out/_t2_probe/crop_pure.png`、`crop_mixed.png` + `run_*.log`：整行裁片两条实测；
- `ocr_json_app/out/_t2_probe/page1_200dpi.png`、`layout_page1/page1_200dpi_res.json`：
  PP-DocLayout_plus-L 版面检测（31 个 formula 框）；
- `ocr_json_app/out/_t2_probe/formula_crops/formula_*.png` + `formula_runs.log`：单条公式裁片批量推理；
- 脚本 `%TEMP%\t2_crop.py`、`%TEMP%\t2_formula_crops.py`；`out/_t2_probe/census.py`（公式规模普查）。

**待用户提供**：3–5 套 10–15 页原生电子卷 PDF（本机现有语料最多 6 页 A4，其余都是
2 页 A3，不能联网下载）。
