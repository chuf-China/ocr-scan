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

```powershell
cd ocr_json_app

# 一条命令跑完：起引擎 → 逐行 OCR → JSON → 单文件网页 → 对比页
pwsh -NoProfile -File run_ocr.ps1
# 备选引擎：pwsh -NoProfile -File run_ocr.ps1 -Engine ovisocr2

# 打开（单文件，不需要服务器）
start out\viewer_layout_paddleocrvl.html

# 对比页（跨源 fetch 会被浏览器挡，必须经服务打开）
python src\ocr_pipeline_layout.py --serve-only --out out\paper.layout.paddle.json --port 8780
#   → http://127.0.0.1:8780/compare.html

# 一键跑全部校验
python tools\run_all_checks.py
```

引擎是本地 llama.cpp 服务，由 `run_ocr.ps1` 自动起停。手动起：

```powershell
pwsh -NoProfile -File $HOME\.dsh-llama\start-ocr-server.ps1 -Model paddleocr-vl -Ctx 16384
Get-Process llama-server | Stop-Process      # 用完释放显存
```

---

## 架构

目标是把四件事分开：**共用底层** / **两条流水线** / **工具** / **校验**。
关键约束是**每个脚本都不依赖当前工作目录**（`src/paths.py` 以项目根推导所有默认路径）。

```
ocr_json_app/
├─ run_ocr.ps1                  唯一入口：起引擎 → OCR → 渲染 → 对比页
├─ README.md
│
├─ src/                         库与流水线
│  ├─ ocr_common.py             PDF 渲染 / OCR 引擎调用 / 本地网页服务
│  ├─ ocr_pipeline_layout.py    ★ 原版式流水线：逐行坐标 + 插图 + 表格裁片
│  ├─ ocr_pipeline_blocks.py    结构块流水线：heading/question/table（供程序消费）
│  └─ paths.py                  项目根、默认 PDF、两份产物的路径映射
│
├─ tools/                       命令行工具
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
python tools\check_layout_json.py out\paper.layout.paddle.json   # 结构/坐标/行序/幻觉残留
python tools\check_render.py      out\paper.layout.paddle.json   # 裁片压正文 / 叠字 / 越界
node   tools\cdp_probe.mjs out\viewer_layout_paddleocrvl.html --shot shot.png
python tools\crop_png.py shot.png crop.png 180 1470 560 330 2.2  # 裁块放大看清局部
```

校验分三层，越靠下越接近用户看到的东西：

| 层 | 抓什么 |
|---|---|
| ① 结构 | JSON 自身是否合法、页序、题号、幻觉残留 |
| ② 渲染 | **裁片是否压正文**、行是否叠字、是否越界、浏览器实渲节点数 |
| ③ 内容 | 公式能否被 KaTeX 编译、前端断言 |

**为什么必须分三层**：实测"插图裁片盖住题干"时，①通过、③通过、行与行的重叠统计也还是 0
——裁片不是 `.ln`，不在那个统计口径里。只有 ② 的 `check_render` 抓得到。

两条验证纪律（都吃过亏）：

- **别只看统计量**。`overlap: 0` 可能是因为裁片盖在文字上把重叠藏了。要单独查"裁片 × 正文行"。
- **别把放大截图上的"重影"当真**。PNG 放大 2.2× 再加 JPEG 压缩会产生错位感。判断真假要看
  **墨迹剖面**：同一行只应有一个连续块，出现第二个错位峰值才是真双重绘制。

---

## 已知限制

- **表格在首选引擎下是图片裁片**，不是可选中/可搜索的表格文本。需要可检索表格请用
  `out/viewer_layout.html`（OvisOCR2，输出真 HTML 表）。
- **插图是裁片，不是矢量还原**：按原位置贴回原稿裁出的图，图像精确但不参与排版。
- **插图/表格检测依赖文字层**：判据是"墨迹是否落在文字蒙版上"，纯扫描件（无文字层）会退回
  "全部当正文"，不还原插图。
- **字体回退**：原卷中文是 `HYShuSongErKW`（本机未安装），会回退到 SimSun，字宽略有差异；
  实测行宽仍在本行盒内，未造成溢出。
- **OCR 是模型转录**，个位错字难免；关键数字与题号请对照「原始扫描」视图或原图复核。

---

## 排障

| 现象 | 处理 |
|---|---|
| `连不上 OCR 服务` | 引擎没起。跑 `run_ocr.ps1`，或手动起（见上文） |
| 整页只出 3 行 | 引擎 `-c` 太小。长页图会占满上下文，必须 `-Ctx 16384` |
| 公式显示成源码 | 阅读器定界符与引擎不匹配（Paddle 是 `\(...\)`，Ovis 是 `$...$`）。用 `tools/build_viewers.py` 重新生成，它会按 `document.engine` 自动选定界符 |
| 页面只渲染出一半 | 打开控制台看异常。历史上出现过 `esc is not defined` 让部分页面静默失败——`tools/cdp_probe.mjs` 会数 DOM 节点，能抓到 |
| 需要复现某页异常 | `python tools\crop_png.py <整页截图> crop.png x y w h 2.2` 放大看；再看 `check_render.py` 的输出定位是哪张裁片 |
