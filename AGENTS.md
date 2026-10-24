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
python tools\run_all_checks.py           # 四层 + 真浏览器；当前基线 17/17
python tools\run_all_checks.py --fast    # 跳过浏览器；当前基线 14/14
```

四层分工——改哪层至少跑哪层，UI/版式改动必须跑完整版（含浏览器）：

| 层 | 抓什么 | 工具 |
|---|---|---|
| ⓪ 代码 | 真 import 两条流水线跑纯函数：未定义名、3.9 语法、投票/幻觉/框线判据 | `smoke_pipelines.py` |
| ① 结构 | JSON 合法、页序、坐标、行序、幻觉残留 | `check_layout_json` / `check_ocr_json` |
| ② 渲染 | **裁片是否压正文**、叠字、越界、浏览器实渲 | `check_render` / `cdp_probe` |
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
9. **插图/表格不信任模型坐标**：几何由像素判定（墨迹比例 / 框线游程）。插图裁片与正文行的
   交叠必须为 **0**（`check_render` 在 > 1 pt² 时即报错），这是红线。
10. **公式定界符两种都要支持**：官方流程输出 `$...$`，llama 时代的 Paddle 输出 `\(...\)`；
    `_delims_for()` 现在两种都给。不要为某个引擎退回“只认一种”，否则整页公式会退化成源码。
11. **提交前只添加真正改动的文件**（不要 `git add -A`）：`out/` 样例产物、`data/` 输入、
    `vendor/` 是刻意跟踪的；`__pycache__/` 与 `*.pyc` 已被 `.gitignore` 忽略。工作区里仓库外的
    `ocr_json_app.rar` 不要提交，也不要解压进仓库。

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
