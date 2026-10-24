# 试卷 OCR → JSON → 原版式网页

把扫描版/文字层损坏的试卷 PDF 用**本地 VLM-OCR** 转成带版面坐标的 JSON，并渲染成和
试卷原版式一致的网页。全程 `127.0.0.1`，文档不出机器。

当前结果（静安区 2026 届高三一模数学试卷，6 页 A4）：

| 产物 | 内容 |
|---|---|
| [out/paper.layout.paddle.json](out/paper.layout.paddle.json) | 首选引擎：6 页 / 77 行 / 51 行含公式 / 5 张插图 |
| [out/viewer_layout_paddleocrvl.html](out/viewer_layout_paddleocrvl.html) | 单文件渲染（数据内嵌，双击即开） |
| [out/compare.html](out/compare.html) | 两个引擎的并排对比（需本地服务） |

---

## 快速开始

> **先看前提**：本仓库不含运行时依赖与模型权重（见下方「环境前提」），换机器要先补齐。

```powershell
cd ocr_json_app

# 一条命令跑完：起引擎 → 逐行 OCR → JSON → 单文件网页 → 对比页
pwsh -NoProfile -File run_ocr.ps1
# 备选引擎 OvisOCR2：当前需手动起 :8081 并显式传 --model/--host（见 AGENTS.md）

# 打开（单文件，不需要服务器）
start out\viewer_layout_paddleocrvl.html

# 对比页（跨源 fetch 会被浏览器挡，必须经服务打开）
python src\ocr_pipeline_layout.py --serve-only --out out\paper.layout.paddle.json --port 8780
#   → http://127.0.0.1:8780/compare.html

# 一键跑全部校验（Windows GBK 控制台必须先设 UTF-8）
$env:PYTHONIOENCODING = "utf-8"
python tools\run_all_checks.py
```

引擎是本地 llama.cpp 服务，由 `run_ocr.ps1` 自动起停。手动起：

```powershell
pwsh -NoProfile -File $HOME\.dsh-llama\start-ocr-server.ps1 -Model paddleocr-vl -Ctx 16384
Get-Process llama-server | Stop-Process      # 用完释放显存
```

---

## 环境前提

仓库**只存代码与产物**，模型权重、第三方依赖、引擎启动脚本都在机器上，不随 git 走。
换机器直接 `run_ocr.ps1` 会失败——按下面补齐。缺什么、报什么错、怎么办：

| 依赖 | 用途 | 缺失时的表现 | 处理 |
|---|---|---|---|
| Python **3.9+** | 两个流水线 | 明确提示 + 退出码 2 | 见下 |
| `pypdfium2` | PDF 渲染（**必需**） | `缺少 pypdfium2（PDF 渲染库）` | `python -m pip install pypdfium2` |
| `numpy` | 像素投影 / 图形区判定（**必需**） | `ModuleNotFoundError: numpy` | `python -m pip install numpy` |
| `Pillow` | 裁片 / 页图编码（**必需**） | `ModuleNotFoundError: PIL` | `python -m pip install Pillow` |
| `pdfplumber` | 从文字层读字号（**可选**） | 自动降级为按行高估算，不报错 | `python -m pip install pdfplumber` |
| 本地 llama.cpp OCR 引擎 | 逐行识别 | `连不上 OCR 服务 http://127.0.0.1:8080` | 启动脚本见上，必须 `-Ctx 16384` |
| Chromium 内核浏览器 | 仅 `cdp_probe.mjs` 真浏览器校验 | 提示用 `--chrome` 指定 | 见下 |

```powershell
python -m pip install pypdfium2 Pillow numpy
python -m pip install pdfplumber        # 可选
```

**解释器版本**：代码要求 **3.9+**，已在 **3.11 / 3.12 / 3.14** 上实测通过；版本过老时
`ensure_python_version()` 会打印一行明确提示并以退出码 2 结束。
（历史上这个文件用过 3.12 才支持的嵌套同引号 f-string，在 3.11 下报的是
`SyntaxError: f-string: unmatched '['`，指向一行完全看不懂的代码——所以才加了这层守卫。）
⚠️ **3.9 / 3.10 属于"声称支持但未实测"**：本机没有这两个解释器，只有
`ast.parse(feature_version=(3,9))` 全过 + 3.11 全过作为间接证据。要在 3.9 上跑，先自测。

**⚠️ 关于"能不能跑通"的边界**：本仓库的自动校验（`run_all_checks.py`）能证明**代码层**与
**已有产物**是好的，但**不能**证明端到端可用——`src/` 的两个流水线在本机从未被完整执行过，
因为它们都在 `import_pdfium` / `connect engine` 处停下（缺 `pypdfium2` 与 OCR 引擎）。
`out/` 里的产物是更早的机器上生成的。所以**首次在真机跑通一遍 `run_ocr.ps1` 仍属未验证步骤**，
这也是 `tools/smoke_pipelines.py`（代码层）不能替代的东西。

**校验用的浏览器**：`tools/cdp_probe.mjs` 自动按序探测
`--chrome` 参数 → `CHROME_PATH` → 系统 Chrome → 系统 Edge → playwright 的 chromium
（取最新版本目录）。全都找不到时会说明怎么指定，而不是丢一个 `ENOENT`。

> 真实引擎只在 `127.0.0.1` 上对话，文档不出机器。这一点的前提是**你自己起的本地
> llama.cpp**；若该端口上跑的是别的转发服务，就不再满足这个前提——先用
> `curl http://127.0.0.1:8080/health` 确认它就是你的 OCR 引擎。

---

## 用官方 PaddleOCR-VL 流程（首选）

### 为什么换掉 llama.cpp

llama.cpp 那条路**只跑了 PaddleOCR-VL 两个阶段中的一个**。官方文档对此有明确警告：

> **若需使用 PaddleOCR-VL 的完整能力，必须采用版面分析与 VLM 识别协同的完整流程，
> 而不能仅单独使用 VLM。**……直接通过 Transformers 执行 0.9B、或直接请求推理服务，
> **都不等同于运行完整的 PaddleOCR-VL 流程**。出现大量幻觉文本时，先确认用的是完整流程。

| 阶段 | 模型 | llama.cpp 路线 | 官方流程 |
|---|---|---|---|
| 版面分析 | PP-DocLayoutV3：检测 `doc_title/text/table/formula/image`、**定阅读顺序**、裁子图 | ❌ 用像素投影猜行 | ✅ |
| VLM 识别 | PaddleOCR-VL-1.6-0.9B | ✅ 仅此一段 | ✅ |

丢掉的不只是"结构"，还有：原生 **HTML 表格**（不再裁成图）、独立公式识别、
图表/印章识别、文档方向分类、文本图像矫正、异形框（quad/poly）。

### 硬件前提（必须对号入座）

RTX 50 系是 **Blackwell（sm_120）**，官方要求读专用文档，并且：

- **驱动支持 CUDA ≥ 12.9**
- 官方 Docker 镜像标签必须带 `-nvidia-gpu-sm120`（普通 `-nvidia-gpu` 在 sm_120 上不可用）

官方镜像约 10GB。若本机到百度 CCR registry 握手超时、或带宽只有 ~0.9MB/s，
Docker 路线会变成数小时级别——那种情况走下面的手动安装。

### 手动安装（Linux 侧，Windows 装不了）

飞桨 GPU 版**只发 Linux wheel**：Windows 上 `paddlepaddle-gpu` 直接
`No matching distribution found`。所以在 WSL2 里建环境：

```bash
uv venv --python 3.12 ~/.venv_paddleocr
. ~/.venv_paddleocr/bin/activate
uv pip install "paddlepaddle-gpu==3.2.1" -i https://www.paddlepaddle.org.cn/packages/stable/cu129/
uv pip install -U "paddleocr[doc-parser]"
python -c "import paddle; print(paddle.is_compiled_with_cuda(), paddle.device.cuda.device_count())"
```

跑整条链路（**先渲染成 PNG 再喂目录**，原因见下）：

```bash
bash run_paddleocr_vl.sh
```

### ⚠️ 三个实测踩到的坑

1. **不要把 PDF 直接喂给 `doc_parser`**：实测卡在 PDF→图像转换——CPU 100%、GPU 8%、
   **4 小时零输出**。先 `pypdfium2` 渲染成 PNG（6 页 0.5 秒）再按目录输入，
   GPU 立刻打满 99%，6 页约 4 分钟跑完。
2. **两个引擎的产物名不能混**：官方流程 engine 是 `paddleocr-vl-1.6`，名字里也带
   "paddle"，早先 `paths.layout_json_for()` 会把它映射到 `paper.layout.paddle.json`，
   **覆盖 llama.cpp 那版**。现已严格匹配，官方流程单独落 `paper.layout.paddleocrvl16.json`。
3. **定界符不是 `\(...\)`**：官方流程输出 `$...$`。阅读器原先按 "paddle" 关键字给
   `\(...\)`，会把整页公式显示成源码——现已改为两种都给（实测旧产物里 0 个孤立 `$`，
   所以对旧产物无损）。

### 转换器

```powershell
python tools\import_paddleocr_json.py out\paddleocr_raw --out out\paper.layout.paddleocrvl16.json --viewer-base viewer_layout_paddleocrvl16
```

它只做坐标换算与标签映射，**不再做任何结构猜测**：`block_label` 直接映射成标题/表格/图，
`block_order` 就是阅读顺序，字号从 PDF 文字层读（与本项目 `enrich_size` 同判据）。

---

## 架构

目标是把四件事分开：**共用底层** / **两条流水线** / **工具** / **校验**。
关键约束是**每个脚本都不依赖当前工作目录**（`src/paths.py` 以项目根推导所有默认路径）。

```
ocr_json_app/
├─ run_ocr.ps1                  llama.cpp 路线入口（Windows）
├─ run_paddleocr_vl.sh          官方 PaddleOCR-VL 流程入口（WSL2）
├─ README.md
├─ docs/                        手写说明与官方流程指南
│  ├─ paddleocr-vl-guide.md     官方流程实战（安装 / 参数 / 坑）
│  ├─ ocr-workflow.html         工作流与架构可视化
│  └─ ocr-workflow-preview.png
│
├─ src/                         库与流水线
│  ├─ ocr_common.py             PDF 渲染 / OCR 引擎调用 / 本地网页服务
│  ├─ ocr_pipeline_layout.py    ★ 原版式流水线：逐行坐标 + 插图 + 表格裁片
│  ├─ ocr_pipeline_blocks.py    结构块流水线：heading/question/table（供程序消费）
│  └─ paths.py                  项目根、默认 PDF、两份产物的路径映射
│
├─ tools/                       命令行工具
│  ├─ smoke_pipelines.py        ★ 代码层冒烟：真 import 两个流水线跑纯函数
│  ├─ build_viewers.py          为每份 JSON 生成单文件渲染
│  ├─ build_compare.py          生成两引擎并排对比页
│  ├─ check_layout_json.py      JSON 结构/坐标/行序/幻觉残留
│  ├─ check_render.py           ★ 裁片不得压正文、行不得叠字、不得越界
│  ├─ check_ocr_json.py         结构块 JSON 校验
│  ├─ cdp_probe.mjs             真浏览器渲染 + 版式诊断量 + 整页截图
│  ├─ crop_png.py               截图裁块放大（判断局部问题用）
│  └─ run_all_checks.py         ★ 一键跑全部校验（CI 入口）
│
├─ verify/                      针对性断言
│  ├─ verify_math.mjs           用真实 KaTeX 逐条编译公式
│  ├─ verify_frontend.mjs       最小 DOM 桩真跑渲染脚本并断言
│  ├─ ocr_ab.py                 两个引擎的量化 A/B（同一批图）
│  └─ compare_ocr.py            两个引擎的关键区域对比
│
├─ layout_viewer/               阅读器前端模板
│  └─ viewer_template.html      生成器把数据内嵌进它
│
├─ data/                        源 PDF
├─ vendor/katex/                本地 KaTeX（离线可用，含 woff2 字体）
└─ out/                         产物（JSON / 单文件 HTML / 对比页）
   └─ compare/                  两个引擎的 A/B 原始数据
```

### 两条流水线，按用途选

| | `ocr_pipeline_layout`（原版式） | `ocr_pipeline_blocks`（结构块） |
|---|---|---|
| 单元 | **行**：页面坐标 + 字号 + 原文换行 | **块**：heading / question / paragraph / table |
| 产物 | `out/paper.layout*.json` | `out/paper.ocr.json` |
| 网页 | 单文件，A4 画布绝对定位 | `out/viewer.html` |
| 适合 | 复核、打印、对照原稿 | 抽题号、判题型、建题库 |

```powershell
python src\ocr_pipeline_layout.py --pdf data\试卷.pdf            # 原版式
python src\ocr_pipeline_blocks.py --pdf data\试卷.pdf            # 结构块
python src\ocr_pipeline_layout.py --serve-only --out out\paper.layout.paddle.json --port 8780
```

### 数据流（原版式）

```
PDF ──pypdfium2 渲染 150dpi──> 灰度图
    ├── 行墨迹投影 ──────────> 文本行盒
    │      └── 逐行裁图 → 本地 VLM-OCR（0.1~0.3s/行，并发，每行 3 次投票）
    ├── 文字蒙版判图形区 ─────> 插图裁片
    └── 框线游程判表格 ───────> 表格裁片
         └── 坐标换算成 PDF 点 ──> paper.layout.json ──> 单文件网页
```

---

## 设计取舍

### 为什么首选 PaddleOCR-VL-1.6

同一 PDF、同一套行盒、同一台机器、每行 3 次投票的实测：

| 指标 | PaddleOCR-VL-1.6 | OvisOCR2 |
|---|---|---|
| 行数 | 77 | 69 |
| 含公式行 | 51 | 46 |
| 还原插图 | 5 | 5 |
| 表格 | 裁片（结构保真） | 3 张 HTML 表（文本可搜索） |
| 叠字 / 越界 | 0 / 0 | 0 / 2 |
| 公式定界符 | `\(...\)` | `$...$` |

正文与公式质量两者相当，Paddle 更快且更稳（Ovis 出现过随机幻觉与更多越界）。
**表格是唯一实质差异**：Ovis 能输出带 `rowspan/colspan` 的 HTML 表（可选中、可搜索），
Paddle 把表格逐格输出成换行文本、行列结构不可恢复。

**提示词按模型固定，不能混用**：Paddle 认官方前缀 `OCR:`；给它自然语言长提示词会**退化**
（实测重复输出 `<br>` 达 23 秒）。Ovis 反之。所以 `ocr_common.default_prompt_for()` 按模型选，
CLI 不要求手动传 `--prompt`。

### 为什么插图与表格都不依赖模型输出的结构

模型对插图给的 `<img src="images/bbox_...">` 坐标**不可信**：实测第 1 页连发 11 个相同的
47×46pt 占位符，第 4/5 页给的是整页框。照它裁图只会把正文复制一遍。表格同理，Paddle 不给结构。

所以两块都由**像素几何**自己认，与 OCR 引擎无关：

- **插图**：用**墨迹落在文字蒙版上的比例**判定。这份 PDF 的数学符号是坏私有字形，但中文与
  ASCII 能正确解码；把好字符画成蒙版，图形线条不在文字层里。实测真插图比例 0.0、直方图 0.247、
  `cases` 公式 0.545、题组行 0.58~0.91 → 阈值 **0.20** 落在空档里。
- **表格**：用**框线游程**判定（表格有长直线），裁成图后内容与结构 100% 保真。

### 表格裁片为什么不用"模型给的坐标"或"占页比例"

- 不能用占页比例：第 17 题表格只占页宽 26%，用全页比例永远检不出来 → 用**局部最长连续游程**。
- 框线带要用**最密的一段连续线**：用整体中位间距剔离群线会被大间距抬高阈值（页边线留下），
  用贪心扩簇又会在首列较宽时提前停住（只剩一条线）。
- 表格盒**直接用框线带极值**，不再用"框线墨迹跨度"收紧：表格里的文字也算墨迹，会把盒子按行
  推回整个内容区（实测从正确的 142×131pt 被推成 147×322pt，把标题与题干圈进裁片）。

### 裁片与正文的关系：必须零交叠

插图裁片**不得压住任何正文行**。判据是**交叠为零**，不是"占该行比例小于阈值"——题干行很长
（约 415pt），裁片压掉最左 46pt 只占 11%，按比例看"很小"，但压掉的正是题号与首字。

另外，插图裁片里**本来就有图上的字母标注**，OCR 对插图区也会把那串字母读成一行文字，
于是页面上图里一套、文字层又叠一套。所以交叠 ≥50% 的正文行会被丢弃（`drop_lines_inside_figures`）。

### 为什么要投票

引擎跑的是 `--temp 0`，但**不是确定性的**。实测同图同参 3 次全对，另一次整卷跑到第 3 页时，
一张**只含表格**的裁图被输出成完全无关的财务表。默认 `--vote 3` 取多数票并记录
`disagreement`，用 3 倍耗时换掉这类低频随机幻觉。

---

## JSON 结构

```jsonc
{
  "document": { "title", "source_pdf", "mode": "layout", "dpi": 150,
                "engine": "llama.cpp/paddleocr-vl", "page_size_pt": [595.3, 841.9],
                "elapsed_s", "warnings": [] },
  "stats": { "pages": 6, "lines": 77, "lines_with_math": 51, "figures": 5,
             "vote": 3, "disagreed_lines": 0 },
  "pages": [ {
      "page": 3, "width": 595.3, "height": 841.9,     // pt，A4
      "render_px": [1240, 1754], "line_source": "raster-projection",
      "image_jpeg_base64": "……页图（--no-images 可去掉）……",
      "lines": [ {
          "top": 280.0, "bottom": 296.3, "left": 90.2, "right": 504.5,   // pt，左上原点
          "size_pt": 10.45, "size_source": "pdf-text-layer", "size_clamped": false,
          "multiline": false, "typical_h_pt": 19.4,
          "text": "17. 为开展社区与学校教育共建活动…",
          "tables": [ { "headers": [...], "rows": [[...]], "source": "html-table" } ],
          "votes": 3, "disagreement": false
      } ],
      "figures": [ {
          "index": 0, "source": "table-grid",              // 或 raster-diagram
          "px": [169, 584, 476, 868],                      // 页图上的像素盒
          "pt": [81.1, 280.3, 228.3, 416.6],               // PDF 点，前端按此绝对定位
          "w_pt": 147.3, "h_pt": 136.3,
          "png_base64": "……裁片……"
      } ],
      "markdown": "……该页按行拼接的纯文本……"
  } ]
}
```

网页顶部可切换 **「OCR 文本层」/「原始扫描」**：前者是重建版式（可选中、可搜索，KaTeX 排版公式），
后者是原始页图。插图与表格裁片在两个视图下都在原位。

---

## 校验

```powershell
python tools\run_all_checks.py          # 一键全部（含真浏览器渲染）
python tools\run_all_checks.py --fast   # 跳过浏览器

# 单独用
python tools\smoke_pipelines.py         # ⓪ 代码层：真 import 流水线跑纯函数
python tools\check_layout_json.py out\paper.layout.paddle.json   # 结构/坐标/行序/幻觉残留
python tools\check_render.py      out\paper.layout.paddle.json   # 裁片压正文 / 叠字 / 越界
node   tools\cdp_probe.mjs out\viewer_layout_paddleocrvl.html --shot shot.png
python tools\crop_png.py shot.png crop.png 180 1470 560 330 2.2  # 裁块放大看清局部
```

校验分四层，越靠下越接近用户看到的东西：

| 层 | 抓什么 |
|---|---|
| ⓪ 代码 | 未定义名、低版本语法、投票/幻觉/框线判据（真的 import 流水线跑） |
| ① 结构 | JSON 自身是否合法、页序、题号、幻觉残留 |
| ② 渲染 | **裁片是否压正文**、行是否叠字、是否越界、浏览器实渲节点数 |
| ③ 内容 | 公式能否被 KaTeX 编译、前端断言 |

**为什么必须分四层**：① ② ③ **全部只看已经生成好的产物**，从不 import `src/` 里的代码，
也不喂合成输入。实测漏过一次：`ocr_pipeline_blocks.py` 里 `SECTION_RE` 从未定义，而它是
`blocks_from_text()` 的**第一个**判断 → 任何非表格文本块都 `NameError`，整条结构块流水线
不可用（同文件 `base64` 也没 import）。当时 `run_all_checks.py` 的 **9 项里有 7 项通过**，
而且这 7 项全是"产物校验"——另外 2 项浏览器校验还因为硬编码的 Chrome 路径失效而失败，
**没有任何一项 import 过流水线代码**。⓪ 就是补这个洞的。

另外"插图裁片盖住题干"那次：①通过、③通过、行与行的重叠统计也还是 0——裁片不是 `.ln`，
不在那个统计口径里。只有 ② 的 `check_render` 抓得到。

两条验证纪律（都吃过亏）：

- **别只看统计量**。`overlap: 0` 可能是因为裁片盖在文字上把重叠藏了。要单独查"裁片 × 正文行"。
- **别把放大截图上的"重影"当真**。PNG 放大 2.2× 再加 JPEG 压缩会产生错位感。判断真假要看
  **墨迹剖面**：同一行只应有一个连续块，出现第二个错位峰值才是真双重绘制。

---

## 已知限制

- **官方流程产物的版式保真度：越界为 0，叠字剩 1 处（已知取舍）**。官方
  `parsing_res_list` 给的是**块框**（一个块可能含 5 行文字），阅读器要的是**逐行框**。
  转换器现在做三件事把块展开成视觉行：① 用**像素投影**取块内行盒；② 用**文字层的
  汉字/ASCII 字符**数真实行数并量真实字号（数学坏字形不参与）；③ 在"行数上限 × 可用宽度"
  下**迭代求解整块统一字号**，并把 `$...$` 当原子（不再把公式从中间断开）。
  结果：**105~106 行 / 水平越界 0 / 叠字 1**，字号可读（8.9~15.1pt）。
  剩下那 1 处是 `8. 已知函数 $f(x)=\begin{cases}…` 这一行：`cases` 在**行内**渲染时
  实际高度（44.8px）远超任何行盒高度，而它下方的空白本来就只有 25.6pt。
  消除它的唯一办法是把整块字号压到约 5.9pt——**不可读**，所以选择保留这处叠字。
  要彻底解决需要按"渲染后高度"反算行盒（即先渲染、再回填坐标），那是另一层工作。
- **表格在首选引擎下是图片裁片**，不是可选中/可搜索的表格文本。需要可检索表格请用
  `out/viewer_layout.html`（OvisOCR2，输出真 HTML 表）。
- **插图是裁片，不是矢量还原**：按原位置贴回原稿裁出的图，图像精确但不参与排版。
- **插图/表格检测依赖文字层**：判据是"墨迹是否落在文字蒙版上"，纯扫描件（无文字层）会退回
  "全部当正文"，不还原插图。
- **字体回退**：原卷中文是 `HYShuSongErKW`（本机未安装），会回退到 SimSun，字宽略有差异；
  实测行宽仍在本行盒内，未造成溢出。
- **OCR 是模型转录**，个位错字难免；关键数字与题号请对照「原始扫描」视图或原图复核。
- **结构块的标题/题号判据有固有歧义**：行首"数字 + `.`"一律当题号，所以 `1.5 倍于原数`
  这种行内数字会被判成第 1 题（`（1）` 这类括号编号则不会）。判据偏向**不漏题**
  （题号宽度放宽到三位）。真正需要精确题号时，请用 `out/paper.ocr.json` 的
  `stats.question_numbers` 人工核对。
- **结构块流水线不校验表格形状**：版式流水线有 `_table_shape_ok` 挡模型编出来的假表
  （100 列表头、1,000.00 财务表），结构块流水线**没有**这道闸门——模型吐什么 `<table>`
  就收录什么。用 `paper.ocr.json` 建题库前建议先看一遍表格块。
- **Python 3.9 / 3.10 是"声称支持但未实测"**：代码里 `ensure_python_version` 的下限是 3.9，
  实测只在 3.11 / 3.12 / 3.14 上跑过；3.9 只有 `ast.parse(feature_version=(3,9))` 全过这种
  间接证据。要在 3.9 上部署，请先自己跑一遍 `tools/smoke_pipelines.py` 与一次真 PDF。
- **端到端从未在本环境跑通**：`out/` 里的产物是更早的机器上生成的；本环境缺 `pypdfium2`
  与 OCR 引擎，两个流水线都停在 `import_pdfium`。`run_all_checks.py` 的 ⓪ 层只能证明
  **代码层**与**已有产物**是好的，替代不了"真机跑通一次 `run_ocr.ps1`"。
- **仓库里混进过 tracked 的字节码**：`src/__pycache__/paths.cpython-312.pyc` 被提交过。
  已加 `.gitignore`，并且 `tools/run_all_checks.py` 与 `tools/smoke_pipelines.py` 都设了
  `sys.dont_write_bytecode = True`（校验工具不该顺手改工作区状态），所以它不会再被生成；
  但索引里那一条还在，提交时记得带上它的删除：`git rm -r --cached ocr_json_app/src/__pycache__`。

---

## 排障

| 现象 | 处理 |
|---|---|
| `连不上 OCR 服务` | 引擎没起。跑 `run_ocr.ps1`，或手动起（见上文） |
| `缺少 pypdfium2` | 依赖没装（仓库不含依赖）：`python -m pip install pypdfium2 Pillow numpy` |
| `需要 Python 3.9+，当前是 3.x` | 解释器太老，换 3.9+ 的 `python`；见「环境前提」 |
| `找不到 Chromium 内核浏览器` | `cdp_probe.mjs` 需要浏览器：装 Chrome/Edge，或 `--chrome <路径>`、`CHROME_PATH` |
| 整页只出 3 行 | 引擎 `-c` 太小。长页图会占满上下文，必须 `-Ctx 16384` |
| 公式显示成源码 | 阅读器定界符与引擎不匹配（Paddle 是 `\(...\)`，Ovis 是 `$...$`）。用 `tools/build_viewers.py` 重新生成，它会按 `document.engine` 自动选定界符 |
| 页面只渲染出一半 | 打开控制台看异常。历史上出现过 `esc is not defined` 让部分页面静默失败——`tools/cdp_probe.mjs` 会数 DOM 节点，能抓到 |
| 需要复现某页异常 | `python tools\crop_png.py <整页截图> crop.png x y w h 2.2` 放大看；再看 `check_render.py` 的输出定位是哪张裁片 |
| 校验全绿但功能明显坏了 | 先看 ⓪ 层有没有跑（`python tools\smoke_pipelines.py`）。只校验产物时，代码坏了它发现不了 |
| 汇总里 ⓪ 层显示"跳过" | 说明当前解释器缺 `numpy`，版式流水线那两组（22 项）没执行。汇总里的明细会写出来——**跳过不等于通过** |
