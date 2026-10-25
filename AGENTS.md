# AGENTS.md

本文件给在本仓库工作的编码代理（Codex / Claude Code / Cursor / Copilot 等）使用。
仓库文档用中文，本文件也用中文；命令、路径、标识符保持原样。

**先读**：`ocr_json_app/README.md`（环境前提、官方流程、架构、校验、排障）、
`ocr_json_app/docs/paddleocr-vl-guide.md`（官方流程的本机实战与坑）、
`ocr_json_app/src/README.md`、`ocr_json_app/tools/README.md`。根 `README.md` 只是导航。

## 项目一览

把扫描版 / 文字层损坏的试卷 PDF 在**本机**转成带版面坐标的 JSON，再渲染成和原卷一致的
单文件网页。全程 `127.0.0.1`，文档不出机器。仓库当前有**两条路线**：

| 路线 | 入口 | 引擎 | 主要产物 |
|---|---|---|---|
| **官方 PaddleOCR-VL（首选）** | WSL2 内 `bash run_paddleocr_vl.sh` | PP-DocLayoutV3 + PaddleOCR-VL-1.6 | `paper.layout.paddleocrvl16.json`、`viewer_layout_paddleocrvl16.html`、`paper.exact.*`、`paper.chars.*` 等 |
| llama.cpp 逐行（Windows） | `run_ocr.ps1` | llama.cpp 上的 PaddleOCR-VL-1.6 / OvisOCR2 | `paper.layout.paddle.json` / `paper.layout.json` |

- 结构块（heading / question / table，供程序消费）：llama 路线产物 `paper.ocr.json`；
  官方流程用 `tools/import_paddleocr_blocks.py` 转出 `paper.ocr.paddleocrvl16.json`。
- 仓库根是 `ocr scan/`，代码全在 `ocr_json_app/`。根 `README.md` 是导航，正文以
  `ocr_json_app/README.md` 为准。
- 样例输入：`ocr_json_app/data/静安区2026届高三一模数学试卷.pdf`（6 页 A4，公开试卷）。
- `out/` 下的 JSON / HTML / 字体 / 截图**全部已提交**（`out/` 约 58 MB，其中 exact 阅读器
  18 MB、`out/fonts/` 25 MB、`out/render/` 5.4 MB）。重跑会刷新大文件，只在确有意图时重跑。
- 本地 `main` 已合并 `origin/main v0.1`；动手前先 `git log --oneline -3` 确认基线。

## 环境

- **官方流程**：Linux / WSL2 + `paddlepaddle-gpu==3.2.1`（cu129 源）+ `paddleocr[doc-parser]`。
  Windows 装不了 GPU 版飞桨；RTX 50 系（sm_120）的 Docker 镜像标签必须带
  `-nvidia-gpu-sm120`。装/跑/坑详见 `ocr_json_app/docs/paddleocr-vl-guide.md`。
- **llama.cpp 路线**：Windows + PowerShell 7（`pwsh`）；本地引擎在 `$HOME\.dsh-llama\`，
  需要 NVIDIA GPU（8 GB 级）。端口按模型固定：`paddleocr-vl` → `8080`，`ovisocr2` → `8081`。
  **同一时间只允许一个 `llama-server`**；用完停掉。长页图要 `-Ctx 16384`，否则整页只出几行。
- Python **3.9+**（两个流水线都有 `ensure_python_version` 守卫；当前 3.12.10）。依赖
  `pypdfium2`、`Pillow`、`numpy`；`pdfplumber` 可选（缺了降级为按行高估算字号）。
  **不要引入 3.12 才支持的语法**（如 PEP 701 同引号嵌套 f-string）——仓库声称 3.9+ 可解析，
  `smoke_pipelines.py` 会用 `ast.parse(feature_version=(3, 9))` 检查。
- Node 22+（当前 v24）；**没有 npm 依赖**；KaTeX 已 vendored。浏览器校验自动探测：
  `--chrome` → 环境变量 `CHROME_PATH` → 系统 Chrome / Edge → Playwright 的 chromium（最新版）。
- 仓库已有 `.gitignore`（忽略 `__pycache__/`、`*.pyc`、`node_modules/`、临时截图/日志）；
  没有 `pyproject.toml` / `package.json` / formatter / linter / CI 配置。
- 远端 `origin` 是 `https://github.com/chuf-China/ocr-scan.git`，主分支 `main`。

## 常用命令

以下命令都在 `ocr_json_app/` 下执行（默认路径不依赖 cwd；但显式传入的相对路径按 cwd 解析）。

### 官方 PaddleOCR-VL 流程（首选）

```bash
# 在 WSL2 里执行；脚本会预渲染 PNG → paddleocr doc_parser → 拷回 Windows → 转换 → 生成阅读器
bash run_paddleocr_vl.sh
# ⚠️ 脚本里的 PROJ_WIN / PY_WIN / 默认 PDF 路径写死了另一台机器的目录，先按本机改（见“已知陷阱”）

# 也可手动分步（Windows 侧）
python tools\import_paddleocr_json.py out\paddleocr_raw --out out\paper.layout.paddleocrvl16.json --viewer-base viewer_layout_paddleocrvl16
python tools\import_paddleocr_blocks.py out\paddleocr_raw --out out\paper.ocr.paddleocrvl16.json
python tools\build_viewers.py
```

**必须显式给 `--out` / `--viewer-base`**：`import_paddleocr_json.py` 认不出
`paddleocr-vl-1.6` 时会按 engine 名推导文件名，和脚本约定的 `paddleocrvl16` 不一致；
阅读器名靠 JSON 里的 `document.viewer_base`，写错会导致页面静默渲染成 0 页。

### llama.cpp 逐行路线（Windows）

```powershell
# 一键：起引擎 → 逐行 OCR → JSON → 阅读器 → 对比页（需要 GPU；样例约 1.5–2 分钟）
pwsh -NoProfile -File run_ocr.ps1
pwsh -NoProfile -File run_ocr.ps1 -Pdf data\其他卷子.pdf       # 换输入，其余默认

# 备选引擎 OvisOCR2 不能走 run_ocr.ps1（见“已知陷阱”）：手动起 :8081 后显式传参
# pwsh -NoProfile -File $HOME\.dsh-llama\start-ocr-server.ps1 -Model ovisocr2 -Ctx 16384
# python src\ocr_pipeline_layout.py --pdf data\静安区2026届高三一模数学试卷.pdf --model ovisocr2 --host http://127.0.0.1:8081 --vote 3 --out out\paper.layout.json

# 只重建阅读器 / 对比页（不需要 GPU、不跑 OCR；会覆盖 out/ 里的生成文件）
python tools\build_viewers.py
python tools\build_compare.py

# 手动起停引擎（启动脚本默认 OvisOCR2；用 Paddle 时必须显式 -Model）
pwsh -NoProfile -File $HOME\.dsh-llama\start-ocr-server.ps1 -Model paddleocr-vl -Ctx 16384
Get-Process llama-server | Stop-Process        # 用完释放显存

# 两条 llama 流水线（引擎已在跑）
python src\ocr_pipeline_layout.py --pdf data\静安区2026届高三一模数学试卷.pdf --out out\paper.layout.paddle.json
python src\ocr_pipeline_blocks.py --pdf data\静安区2026届高三一模数学试卷.pdf --out out\paper.ocr.json

# 经服务打开需要 fetch 的页面（file:// 下会被 CORS 拒绝）
python src\ocr_pipeline_layout.py --serve-only --out out\paper.layout.paddle.json --port 8780
#   → http://127.0.0.1:8780/compare.html
```

`run_ocr.ps1` 只接受 `-Engine -Pdf -Dpi -Vote -Workers`，**没有分页参数**；只跑部分页用
`python src\ocr_pipeline_layout.py --pages 1-2`（格式只有 `3` 或 `3-8`，没有逗号列表）。

**Windows 控制台默认 GBK**：跑任何会打印 `✓` / `✗` / `⚠` 的校验前先设

```powershell
$env:PYTHONIOENCODING = "utf-8"
```

否则 `run_all_checks.py` 会以 `UnicodeEncodeError` 退出；带 warning 的
`check_layout_json.py` 也会因编码错误假失败。这是环境问题，不要改校验逻辑。

## 校验（完成定义）

```powershell
$env:PYTHONIOENCODING = "utf-8"
python tools\run_all_checks.py           # 五道门 + 真浏览器；当前基线 21/21
python tools\run_all_checks.py --fast    # 跳过浏览器；当前基线 18/18
```

四层分工——改哪层至少跑哪层，UI/版式改动必须跑完整版（含浏览器）：

| 层 | 抓什么 | 工具 |
|---|---|---|
| ⓪ 代码 | 真 import 两条流水线跑纯函数：未定义名、3.9 语法、投票/幻觉/框线判据 | `smoke_pipelines.py` |
| ① 结构 | JSON 合法、页序、坐标、行序、幻觉残留 | `check_layout_json` / `check_ocr_json` |
| ② 渲染 | **裁片是否压正文**、叠字、越界、浏览器实渲；浏览器叠字/越界是 llama 产物的硬门 | `check_render` / `cdp_probe` / `run_all_checks` |
| ③ 内容 | 公式能否被 KaTeX 编译、前端断言 | `verify_math` / `verify_frontend` |

单独跑：

```powershell
python tools\check_layout_json.py out\paper.layout.paddle.json   # 结构/坐标/行序/幻觉残留
python tools\check_render.py      out\paper.layout.paddle.json   # 裁片压正文/叠字/越界
node   tools\cdp_probe.mjs out\viewer_layout_paddleocrvl.html --shot shot.png
node   verify\verify_math.mjs                                    # 默认即可
node   verify\verify_frontend.mjs                                # 默认即可
python tools\crop_png.py shot.png crop.png 180 1470 560 330 2.2  # 放大局部看清
```

三条验证纪律（README 里都吃过亏）：

- **别只看统计量**。`overlap: 0` 可能因为裁片盖住文字反而"藏"了重叠；`check_render` 专查"裁片 × 正文行"。
- **别把放大截图上的"重影"当真**。PNG 放大 + JPEG 压缩会制造错位；判断双重绘制要看墨迹剖面。
- **没跑过的检查不要声称通过**。引擎没起就停下来说明，不要改用云 OCR、也不要伪造产物。
  汇总里的“跳过”不算通过；缺产物会少项，别把“全绿”当成所有产物都验过。

## 硬约束

1. **`out/` 基本都是生成物**：`paper.layout*.json`、`paper.ocr*.json`、`viewer_*.html`、
   `compare.html`、`out/fonts/`、`out/render/`、`out/paddleocr_raw/`、`out/vendor/` 等。
   不要手改；改源码 / 模板 / 转换器后重新生成。`out/fonts/` 是精确重建用的内嵌字体提取物，
   `vendor/katex/` 是第三方文件，都不要删改。
2. **两条路线 / 三个引擎的产物不能混名**：官方流程 engine 是 `paddleocr-vl-1.6`，llama 路线是
   `paddleocr-vl` 与 `ovisocr2`；`paths._engine_key()` 只严格匹配后两者。转换器必须显式
   `--out` + `--viewer-base`，否则会覆盖 llama 产物或生成打不开的阅读器。
3. **官方流程不做结构猜测**：`import_paddleocr_json` / `import_paddleocr_blocks` 只做坐标换算与
   `block_label` 映射；阅读顺序用官方的 `block_order`。不要在这两个转换器里加启发式折行/估字号。
4. **不要拆开 llama 路线的模型与提示词**：PaddleOCR-VL 只认 `OCR:` 前缀；长自然语言提示词会让它
   退化（实测重复 `<br>` 23 秒）。OvisOCR2 才用整页解析指令，见 `ocr_common.default_prompt_for()`。
5. **默认路径不得依赖 cwd**：Python 侧项目根与默认路径从 `src/paths.py` 取（其余用脚本位置推导）；
   `verify/*.mjs` 用脚本位置推 `ROOT`。显式传入的路径参数（`cdp_probe` target、`check_*` JSON、
   `crop_png` 文件）按 cwd 解析，在 shell 里用相对路径前先 `cd` 到 `ocr_json_app`。
6. **import 分层**：`paths` / `ocr_common` 不得 import 任何流水线；两条流水线只依赖它们。
   新的共用逻辑放 `ocr_common`，不要复制。
7. **pypdfium2 渲染必须串行**（原生库多线程会 access violation），只有 HTTP OCR 可以并发。
8. **一次只起一个 `llama-server`**；跑完停掉。不要 `Stop-Process` 与本项目无关的 GPU 进程；
   `run_ocr.ps1` 会先杀掉所有 `llama-server`，别人也在用时先确认。
9. **插图/表格不信任模型坐标**：优先取 PDF 原生图形资源（`pdfplumber` 的 `images` /
   `curves+lines+rects` 并集），**在行投影前把原生图区涂掉**，避免图里的曲线把相邻文字行粘成
   一个超高的合并行；没有原生资源时再退回墨迹比例 / 框线游程。整页扫描位图是页面背景，
   不能当插图涂白（否则整页正文全没），`_is_page_background()` 会跳过它。裁片按原生 bbox
   以 300 dpi 出图，插图裁片与正文行的交叠必须为 **0**（`check_render` 在 > 1 pt² 时即报错），
   这是红线。
   浏览器实渲的“叠字/越界”也已是 llama 产物的硬门（`run_all_checks` 解析 `__OCR_DIAG__`，
   非 0 即失败）；官方 line-only 产物只告警。
10. **公式定界符两种都要支持**：官方流程输出 `$...$`，llama 时代的 Paddle 输出 `\(...\)`；
    `_delims_for()` 现在两种都给。不要为某个引擎退回“只认一种”，否则整页公式会退化成源码。
11. **提交前只添加真正改动的文件**（不要 `git add -A`）：`out/` 样例产物、`data/` 输入、
    `vendor/` 是刻意跟踪的；`__pycache__/` 与 `*.pyc` 已被 `.gitignore` 忽略。工作区里仓库外的
    `ocr_json_app.rar` 不要提交，也不要解压进仓库。
12. **原生文字层走“有条件快路”，公式仍以 OCR 为准**：PDF 有 text layer 时，`pdfplumber`
    chars 聚行；只有纯文字行（无公式定界符、无 `(cid:)`/替换符/控制符、CJK ≥2 字且占比
    ≥45%）在 `match_native_lines_to_pixels()` 的几何一对一门槛（纵向 ≥0.45、横向 ≥0.25）
    通过后，才用 `text_source=native-text` 跳过 OCR。**还有两条防丢字闸门**：原生行必须
    覆盖像素行高度的 55% 以上（上下分式会被字层拆成“分子/分母”两条，像素行却合并成一条
    高行，矮行按“较小行高”算照样 100% 命中）；认领后若盒内还有未被认领的可信原生行，
    这条快路作废回 OCR。两条都进 `stats.native_text_fast_rejected`——只认领一条原生行
    = 另一条内容静默丢失，不许用“命中率高”掩盖。公式行、乱码行、页缩放/旋转页必须
    回 OCR；`--text-source ocr` 可全量回退。原生字层常把公式拆成 `x2`/`y0` 或私有字库，
    不能无门槛替换。行样式来自 `annotate_line_styles()`（字号/粗斜体/颜色/衬线），
    `audit_fonts()` 的 PUA/`(cid:)`/替换符/乱码是硬 error。不要为了“多用原生字”放宽
    这些门槛——公式正确性和不乱码优先。
13. **横向 A3 卷的多栏必须逐栏检测**：只在 `width >= height * 1.18` 的页启用
    `detect_column_gutters()`；有 PDF 文字层时用字符盒找整列空白（过滤文字层里
    图像上无墨的离群字符），没有时退回像素投影。找到中缝后**先按栏切图，再在
    每栏内部做行投影/图形/表格检测**；不要整页检测完再 `split_lines_by_column`，
    否则另一栏的文字会填满行间空白，把多行粘成超高行（实测吞过整道 Q20）。
    不假设栏数，也不假设中缝居中。行序仍按 `top/left` 自上而下排序，
    `check_layout_json` 只接受这个口径；阅读器的绝对定位不依赖行序。
    **栏首题号会悬在栏缝左侧**（2025 全国 1 第 16 题的 `16.` 在栏起点左 12pt），
    逐行裁图必须对页顶栏首行向左多带 40px；不要对栏中部行也加，否则左邻栏末字会
    混进来（实测把 `三、填空题` 变成 `时三、填空题`，整节丢失）。150dpi 小字行裁片
    低于 72px 时放大 2 倍再送 OCR，否则会出现 `��֪����` 一类乱码。
14. **坐标不是语义，试卷结构必须单独建层**：`src/exam_structure.py` 从 layout JSON 重建
   `sections/questions/options/subquestions/figures`，栏感知阅读顺序用 `reading_order`，
    不能拿 `lines[]` 数组下标当题号或归属。题号、选项（选择题必须 A–D 四项且非空）、
    图归属、跨页/跨栏续题、缺号/重号都进 `quality`；`quality.status=fail` 时
    `check_exam_json.py` 退出 1。不要用“能渲染”代替试卷结构正确。布局 JSON 的新字段
    （`schema_version`、稳定 `id`、`column`、`reading_order`、`content_type`）只增不改，
    旧产物仍须可读。
15. **版面层必须报告“没覆盖到的墨迹”，不能只报成功**：每页写 `page_type`
    （`blank` / `no-lines` / `text`、方向、栏数、文字层、rotation）和 `coverage`
    （行盒+插图盒掩膜、`ratio`、`uncovered_regions`）；文档级写 `document.coverage`。
    `exam_structure` 把未覆盖区域变成 `page_uncovered_ink` warning，不要为了消
    warning 直接放宽容差。覆盖率告警先定位 pt 区域：可能是插图裁片切掉字母标注、
    表格框线或扫描噪点；确认后再扩图/补行，不能把未覆盖区域静默当成空白。
16. **公式先过结构门，再过 KaTeX 编译门**：`src/formula_quality.py` 只检查定界符、
   花括号、`\left`/`\right`、`\begin{...}`/`\end{...}` 是否成对，**不改写正文**。
   结构错误写 `formula_structure_broken` error，`check_exam_json.py` 退出 1；可疑字符/
   超长写 warning。真实可渲染性仍由 `verify/verify_math.mjs` 逐条编译证明，两者不能互相
   替代。不要用“页面能显示”代替公式结构正确。
17. **表格用 PDF 原生框线 + 逐格 OCR，不靠模型结构**：`PdfFontProbe.table_boxes()`
   取 `pdfplumber.find_tables()` 的 bbox/行列/单元格，`ocr_table_cells()` 逐格 OCR，
   每页写 `page["tables"]`，`exam_structure` 按 bbox 归属到题并写 `question.tables`。
   不要用 `pdfplumber.extract()` 的文本（数学卷中文字库常是 `(cid:0)`），不要把表格
   压成一行正文；无文字层/无框线表格仍走裁片，但必须在 `quality` 里可见。行级
   `source=pdf-table-cells` 的 table 不再重复归属；空表头/空行/空单元格/未归属表是
   warning，表格单元格公式结构损坏是 error。
18. **内容截断必须显式报错**：非选择题题干以 `则/求/若/已知/设/得/且/…`、运算符、
   未闭合括号结尾，或公式定界符不平衡，`build_quality()` 写
   `question_content_truncated` error；空大题写 `section_empty`。选择题题干允许以
   `则/若` 结尾（后面本来就跟选项），不要误报。检测到后不要自动补字，保留原样并让
   质量门失败。
19. **修复队列只路由、不编造**：`src/repair_plan.py` 把 `quality.issues` 映射为
   `auto_candidate` / `manual` / `blocked`。只有空表格单元格允许标记为
   `reocr_table_cell`（幂等、可回退）；截断、缺选项、图表未归属、未覆盖墨迹、
   原生字层不一致一律 `manual`。`auto_candidate` 不等于已修复，执行前必须保留
   原 JSON；不得自动补题干/选项/表头/题号或改公式。
20. **稳定性必须用多套真题验收，不能拿单张卷调参**：`tools/run_exam_acceptance.py`
   对 20 套历年语料 + 3 套未见样本跑覆盖率（≥99%）、选项完整、公式结构（≥95%）、表格填充（≥90%）、
   人工动作/页（≤1）；`quality.status=fail` 或任一检查非零即整批失败。纯图形选项用
   `option_mode=graphical` 标记并按 warning 处理，不能假装成 4 个空文字选项。语料在
   `data/exams/` 与 `data/exams/corpus/`，失败样本先复现再改判据；禁止只对某张卷
   写特例。公式通过率/表格填充率不是内容准确率或 F1，报告里不得混称。
21. **内容门对历史演示产物只告警，对新产物必须硬失败**：没有 `schema_version` 的
   `out/paper.layout*.json` 是架构升级前的演示产物，仍跑结构与渲染检查；内容门用
   `check_exam_json.py --advisory` 记录 fail 但不阻断回归。新产物（`schema_version=1`）
   不得加 `--advisory`，验收批次更不得用它掩盖缺题/缺选项。
22. **原生 PDF 的图、样式、字体必须单独验收**：插图 bbox 先排除已确认表格，再用
   `_expand_box_with_labels()` 吸收 12pt 内的坐标轴/点/图例小字（远离矢量最多 26pt），
   相邻矢量簇间隙 <14pt 合并；不要因为扩框后图数变少就退回旧启发式。行样式写
   `line.style`（字号/粗斜体/颜色/衬线），阅读器在 `fitLineWidth()` 前应用；
   `document.font_audit` 的 PUA、`(cid:)`、U+FFFD、乱码行必须进质量门。验收报告同时
   输出 `native_ratio`、`style_ratio`、`garbled_lines`，但它们只是诊断指标，不能
   替代覆盖率/公式/表格/选项硬门。详见 `docs/specs/native-pdf-quality.md`。
23. **区域层是 layout 的一等证据，只增不改**：`src/layout_regions.py` 把每页的行 / 表格
   / 图收成 `page.regions[]`（`p<页>-r<序>`、`bbox_pt`、`content_type`
   `text|formula|table|figure`、`source`、`confidence`、`spans[]`、`refs`），并反向写
   `line.region_id` / `figure.region_id` / `table.region_id`。不变量：每条物理行**恰好**
   属于一个区域（表格占位行归表格区域，不再额外生成行区域）；区域 id 稳定；
   `lines[]` / `figures[]` / `tables[]` 的语义与顺序不变，区域只做引用；旧产物由
   `ensure_regions()` 就地派生（幂等）。`exam.json` 的题干/选项/小问/图/表带
   `region_refs` / `region_id` 回链；`quality.checks.regions_consistent` 只是证据
   （warning 级），不能替代覆盖率/公式/表格/选项硬门。`check_layout_json` 只对含
   `regions` 的产物硬校验，历史产物没有该字段则跳过。详见
   `ocr_json_app/docs/specs/t1-layout-regions.md`。
24. **公式复核只是证据，绝不覆盖正文**：`src/formula_review.py` 把公式模型的输出写进
   `regions[].formulas[]`（`source` / `crop` / `latex` / `text_marked` / `compare`），
   **不改** `line.text` / `region.text`，也不直接写 `final`；`compare.agreement` 分
   `exact|normalized|partial|differs|no_current`，其中 `partial` 是裁片只覆盖公式片段，
   不能当模型错误统计。映射不到的检测框写 `page.unassigned_formula_boxes[]`，不猜归属。
   推理只在 `formula-venv` 子进程里跑（CPU 版 paddle，不占显存、不抢 8080），主环境
   不得 import paddle。`check_layout_json` 只对含 `formulas` 的产物硬校验，历史产物跳过。
    内容级 A/B 用 `tools/build_formula_ab.py` 按一致性配额抽样、渲裁片拼对照图人工判定，
    **不能**用"KaTeX 能渲染"或一致性分布代替内容准确率；KaTeX 过门（`formula_quality`）
    仍只回答"结构可解析"，不是公式对错。详见 `ocr_json_app/docs/specs/t2-formula-regions.md`。

25. **题号写法与答案区是结构层的硬骨头**：试卷题号既有 `12.` 也有行首「（12）」，小问同样从
    「（1）」数起，所以 `（n）` 只有在 `n` 比当前最后一题大（允许 1–5 跳跃）时才是新题号，
    比它小一律按小问走；不许为了多认几题直接把 `(n)` 全当题号。大题标题里的「共 N 小题」
    用于收口：收满后再出现题号说明小标题整行被吞，开 `inferred` 大题并报
    `section_heading_missing` **warning**，不猜标题、不继承选择题的选项要求。同一标签 + 同一
    `共 N 小题` 的标题第二次出现视为文末参考答案区，其后行不再建题（否则空大题）。这些是
    结构层的真实缺陷，只能按卷面事实报 `error`/`warning`，**不许**为让某套卷过门放宽正则或
    写单卷特例。详见 `ocr_json_app/docs/specs/section-and-question-detection.md`。

26. **表格复核只是证据，绝不覆盖表格正文**：`src/table_review.py` 把表格模型的输出写进
    `tables[].reviews[]`（`source` / `pipeline` / `crop` / `html` / `cells` / `compare`），
    **不改** `table.headers` / `rows_data` / `text_available`，也不新造表格；`compare.agreement`
    分 `exact|cell_mismatch|structure_differs|no_current`，`matched`/`differing` 只统计两边都在的
    坐标，缺格单独计 `only_current`/`only_model`。单元格比较前必须归一化（去定界符、
    Unicode 数学符号转 LaTeX、全角转半角），否则 `\(a\leq b\)` 与 `a≤b` 会被误判成不一致。
    映射不到的裁片写 `page.unassigned_table_boxes[]`。**不许整页跑 `pp_structurev3`**：它一次
    加载 14 个模型（约 1.6 GB 权重），本机 16 GB 内存实测会被拖死；只能裁表格 bbox 跑
    `table_recognition_v2`，CPU 线程压到 4，并关闭版面检测/方向矫正/去扭曲。推理只在
    `formula-venv` 子进程里跑，主环境不得 import paddle。`check_layout_json` 只对含 `reviews`
    的产物硬校验，历史产物跳过。详见 `ocr_json_app/docs/specs/t3-table-review.md`。

27. **缺陷先定责，再谈修**：`src/defect_taxonomy.py` 把每个 issue code 登记成
    「六类现象（乱码 / 重叠 / 少图 / 少文字 / 多余 / 结构内容错）× 责任方
    （`program` 架构程序 / `model` 模型 / `input` 输入）× 修复层」；`build_quality`
    据此输出 `summary.defects{by_class, by_owner, unknown}`，只增不改，不影响 status。
    **新增 issue code 必须同时登记**，否则 `checks.defect_codes_registered` 转 false；
    `mixed` 只是临时状态，必须写清"怎么区分程序/模型"的判据（现有的三个：题号 gap 看
    该行在不在 layout、题干截断看是否被表格占位行顶替、选项不全看是否一行多标签没拆开）。
    判定顺序固定：文本在不在 → 几何对不对 → 内容对不对，不许按单卷调规则。页眉页脚的小图
    （≤90pt 且贴边 10%）一律当装饰：不进 `figures`、计入覆盖率、写 `page.page_furniture[]`，
    否则二维码会被当成"未归属插图"淹掉真正的少图信号。详见
    `ocr_json_app/docs/specs/defect-taxonomy.md`。

## 改哪里 / 怎么改

- **官方流程入口 / 路径**：`run_paddleocr_vl.sh`（WSL 侧，硬编码路径先改）+ `tools/import_paddleocr_json.py`
  + `tools/import_paddleocr_blocks.py` + `tools/build_viewers.py`。改流程前先读
  `docs/paddleocr-vl-guide.md`（含 27 个子命令、API 参数、已踩的坑）。
- **高保真重建实验**：`tools/build_line_level.py`（逐行）、`build_char_level.py`（逐字符）、
  `build_exact.py`（内嵌原字体 + 字符位置）、`run_acceptance.py`（三项口径验收）。
  对应产物在 `out/paper.layout.lines.*`、`paper.chars.*`、`paper.exact.*`、`out/render/`；
  这条链未全部挂进 `run_all_checks`，改完至少跑 `run_acceptance.py`。
- **llama 路线**：OCR 后处理/行检测/裁片 → `src/ocr_pipeline_layout.py`；结构块 → 
  `src/ocr_pipeline_blocks.py`；共用底层 → `src/ocr_common.py`。阈值都有实测依据，改阈值
  先用 `--pages 1-2` 小范围验证，再跑整卷对比 `stats`。
- **阅读器外观/交互** → `layout_viewer/viewer_template.html`（占位符 `__TITLE__`、`__ENGINE__`、
  `__DELIMS__`、`__JSON_URL__`、`__DATA_SCRIPT__`；结构块阅读器模板在 `ocr_pipeline_blocks.py` 内）。
  改完重新生成并跑 `cdp_probe`；KaTeX 必须同步加载（模板注释解释了原因）。
- **新校验** → 放 `tools/` 并挂进 `tools/run_all_checks.py`；否则不会被任何人跑到。
- **文档**：行为/命令变了同步 `ocr_json_app/README.md`（正文）+ 根 `README.md`（导航）+
  `src/README.md` / `tools/README.md` / `docs/`（`tools/README.md` 目前还没列全新工具）。
- **工作流可视化**（手写 HTML，非流水线产物）→ `ocr_json_app/docs/ocr-workflow.html`。

小步原则：改模板/校验不需要 GPU；跑 OCR 才需要。llama 路线先用
`--pages 1-2 --vote 1 --no-images` 快速迭代；官方流程先小页数跑通再整卷。

## 代码风格

- **Python**：3.9+ 兼容；文件头 `#!/usr/bin/env python3` + `# -*- coding: utf-8 -*-`（例外：
  `tools/crop_png.py` 无文件头、直接用 `sys.argv`）；`from __future__ import annotations`；
  公开函数写类型标注；用 `argparse`；模块 docstring 用中文讲**为什么**和用法；行内注释解释取舍。
- **日志与退出码**：`print` 中文日志带 `[layout]` / `[ocr]` / `[http]` / `[run]` 前缀；流水线错误用
  `die(msg, code)`：`2` = 引擎/依赖，`3` = 输入，`4` = 识别失败（**仅结构块流水线**；版式流水线把
  逐行 OCR 失败记为 warning 不退出）；校验脚本失败则 `return 1`。成功返回 `0`。
- **命名**：`snake_case` 函数/变量，`UPPER_SNAKE` 常量，内部辅助函数前缀 `_`。
- **JS**：ESM `.mjs`，只用 `node:*` 内置 + vendored KaTeX（`createRequire`）；`verify/*.mjs` 从
  脚本位置推 `ROOT`；中文注释；退出码 `0` / `1`。
- **HTML/CSS**：单文件、内嵌 CSS/JS；数据内嵌时把 `<` 转义成 `\u003c`（防 `</script>` 提前截断）。
- **没有 formatter/linter**：按周围风格写，**不要顺手重排无关代码**。全仓库 UTF-8；
  `.gitattributes` 只有 `* text=auto`。

## 已知陷阱

- **GBK 控制台**：`run_all_checks.py` 和带 warning 的 JSON 校验会因 `✓/⚠` 抛
  `UnicodeEncodeError`；先设 `PYTHONIOENCODING=utf-8`（见“常用命令”）。
- **Python 版本**：代码声称 3.9+，不要写 3.12-only 语法；`smoke_pipelines.py` 会检查 3.9 解析，
  但本机没有 3.9 解释器，涉及低版本时说明“未实测”。
- **`run_paddleocr_vl.sh` 路径写死**：`PROJ_WIN="/mnt/c/Users/25840/Desktop/ocr-scan/ocr_json_app"`、
  `PY_WIN="C:\Users\25840\...\python.exe"`、默认 PDF 也是旧路径；换机器先改这三处。
- **不要把 PDF 直接喂给 `paddleocr doc_parser`**：实测卡在 PDF→图像转换 4 小时零输出；
  先用 `pypdfium2` 渲染 PNG 再喂目录（脚本已这么做）。
- **官方流程只能在 Linux/WSL2 跑**：Windows 上 `paddlepaddle-gpu` 无 wheel；RTX 50 系注意
  CUDA ≥ 12.9 与 `-nvidia-gpu-sm120` 镜像标签。
- **`out/` 产物可能落后于代码**：已提交的 JSON/HTML 是更早机器生成的；不要当“可复现基线”，
  也不要据它推断当前代码行为（README 的统计数字同理）。
- **`tools/README.md` 未列全新工具**：新工具（`import_paddleocr_*`、`build_exact`、
  `build_char_level`、`run_acceptance`、`smoke_pipelines` 等）直接看 `tools/` 目录与各自 docstring。
- **docstring 有过期的**：`check_layout_json.py` 提到不存在的 `--expect-lines` 和旧文件名
  `ocr_pdf_layout.py`；`ocr_pipeline_layout.py` 顶部说默认 `:8081`，而代码默认 `:8080`。
  以 `argparse` 和 `src/ocr_common.py` 为准。
- **llama 引擎 `--temp 0` 也不是确定性的**，低频随机幻觉真实存在。`run_ocr.ps1` 默认 `--vote 3`；
  直接跑 CLI 默认 `--vote 1`。不要把 `--vote 1` 的一次结果当基准。
- **手动起 llama 引擎的默认模型是 OvisOCR2（:8081）**，而流水线默认 `paddleocr-vl`（:8080）。
  手动起 Paddle 一定要传 `-Model paddleocr-vl`。
- **`run_ocr.ps1 -Engine ovisocr2` 目前是坏的**：脚本把 Ovis 起在 `:8081`，但调用 Python 时没传
  `--model` / `--host`，流水线用默认 `paddleocr-vl` + `:8080`，健康检查直接 exit 2。要跑 Ovis 按
  “常用命令”里的手动方式，或先修 `run_ocr.ps1:46`（补 `--model $Engine` 与对应 `--host`）。
- `run_ocr.ps1` 在 OCR 失败时**提前 `exit`，不会执行收尾的 `Stop-Process`**，引擎会留在后台；
  失败后手动 `Get-Process llama-server | Stop-Process`。
- `check_layout_json.py` 默认 `--expect-pages 1-6`；`check_ocr_json.py` 默认 `--expect-questions 1-21`。
  只对样例卷成立，换 PDF 必须传参。
- **`--serve-only` 不是纯只读**：版式流水线发现阅读器 HTML 不存在会重新生成；结构块流水线则
  **总是**重写其输出目录的 `viewer.html`。
- 结构块阅读器 `out/viewer.html` 从 `out/vendor/` 加载 KaTeX；`write_viewer` 的复制源 `src/vendor`
  并不存在，若 `out/vendor/` 缺失会退到 CDN（联网）。
- `--no-images` 会让 JSON 不含页图/裁片：阅读器的“原始扫描”与插图会缺失，
  `check_layout_json` 只给 warning。
- `out/compare.html` 必须经 `--serve-only` 打开；`file://` 下 fetch 会失败。
- JSON 里的 `document.source_pdf` 记录的是**生成时机器上的绝对路径**，不要据此判断文件在哪。
- 仓库没有 CI；`run_all_checks.py` 就是门槛。`docs/ocr-workflow.html`（+ 预览 PNG）是手写说明，
  不是流水线产物。

## 提交

- 提交前：`git diff` 自查；确认没有把临时截图、`__pycache__`、未跟踪产物带进来。
- 大产物（`out/**`、`out/fonts/`、`out/render/`）有变化时，提交信息里说明是**哪个命令、哪条
  路线/引擎、哪些参数**生成的，便于复现。
- 提交信息沿用历史风格：短句（`version0.1`、`创建 README.md`）；一次提交只做一件事。
- 远端 `origin` 是 GitHub 的 `main`；不要 force-push。
- 改了行为就同步文档与校验；新增不变量要补一个 check 并挂进 `run_all_checks.py`。

## 安全

- 全离线：试卷与产物不出机器；不接云 OCR、不上传；本地服务只绑 `127.0.0.1`（`ocr_common.serve` 写死）。
- 不要把私有试卷产物提交到公开仓库 / 发给外部服务。
- 不引入需要联网或需要密钥的新依赖；不要扩大监听地址。

---

本文件遵循 [agents.md](https://agents.md/) 开放格式，并按 Anthropic `CLAUDE.md` 的惯例保持
"命令前置、可执行、少而具体"。Claude Code 通过同目录的 `CLAUDE.md`（`@AGENTS.md` 导入）读取本文件。
命令、目录或约束变化时请同步更新本文件。
