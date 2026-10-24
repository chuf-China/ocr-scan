# AGENTS.md

本文件给在本仓库工作的编码代理（Codex / Claude Code / Cursor / Copilot 等）使用。
仓库文档用中文，本文件也用中文；命令、路径、标识符保持原样。

**先读这三份，再动手**：`README.md`（设计与取舍 + 实测数据）、`ocr_json_app/src/README.md`、
`ocr_json_app/tools/README.md`。本文件只写"代理需要、README 没写或最容易踩"的部分。

## 项目一览

把扫描版 / 文字层损坏的试卷 PDF 在**本机**转成带版面坐标的 JSON，再渲染成和原卷一致的
单文件网页。首选引擎 PaddleOCR-VL-1.6（llama.cpp，`:8080`），备选 OvisOCR2（`:8081`）。
全程 `127.0.0.1`，文档不出机器。

```
PDF → pypdfium2 渲染 150dpi → 行墨迹投影 → 逐行裁图 → 本地 VLM-OCR（并发 + 投票）
    → 坐标换算成 PDF 点 → paper.layout*.json → 单文件 HTML 阅读器
```

- 仓库根是 `ocr scan/`，实际代码全在 `ocr_json_app/`。
- 根 `README.md` 与 `ocr_json_app/README.md` 目前是**同一份内容的两个副本**；改一份必须同步另一份。
- 样例输入：`ocr_json_app/data/静安区2026届高三一模数学试卷.pdf`（6 页 A4，公开试卷）。
- 产物 `ocr_json_app/out/*.json`、`out/viewer_layout*.html` **已提交进 git**，单份 0.9–1.6 MB；
  重跑流水线会刷新这些文件，产生大 diff——只在确有意图时重跑。
- 两条流水线：`ocr_pipeline_layout`（按行 + 坐标，原版式，产物 `paper.layout*.json`）与
  `ocr_pipeline_blocks`（语义块 heading/question/table，产物 `paper.ocr.json`）。按用途选，别混用。

> **仓库状态（2026-10 快照）**：本地 `main` 落后 `origin/main` 一个提交 `0f9a21b v0.1`
> （133 个文件、约 +8.8 万行：新增 `.gitignore`、`ocr_json_app/docs/paddleocr-vl-guide.md`、
> `ocr_json_app/out/fonts/`、约 20 个工具，重写两份 README，并把首选路线改为官方 PaddleOCR-VL 流程）。
> 本文件只描述**当前本地快照**；`git pull` 前先确认按哪版工作，pull 后本文件需要重写。

## 环境

- Windows + PowerShell 7（`pwsh`）；脚本使用 Windows 路径与 `$HOME`。
- Python 3.12（**本地快照必需**：代码用了 3.12 的 PEP 701 同引号嵌套 f-string，3.11 及以下会
  `SyntaxError`；当前 3.12.10）。依赖：`pypdfium2`、`Pillow`、`numpy`；`pdfplumber` **可选**
  （用于从 PDF 文字层取字号；缺失时降级为按行高估算并打日志）。

  ```powershell
  python -m pip install pypdfium2 Pillow numpy pdfplumber
  ```

- Node 22+（当前 v24；`cdp_probe.mjs` 用全局 `fetch` / `WebSocket`）。**没有 npm 依赖**，只用
  `node:*` 内置模块；KaTeX 已 vendored 在 `vendor/katex/`。浏览器校验用本地 Playwright 的
  headless Chromium，默认路径写死在 `tools/cdp_probe.mjs:31-32`（Playwright 升级后目录名会变），
  不存在时用 `--chrome <exe>` 覆盖。
- OCR 引擎**不在仓库里**：`$HOME\.dsh-llama\start-ocr-server.ps1` + 本地模型，需要 NVIDIA GPU
  （8 GB 级显存）。端口按模型固定：`paddleocr-vl` → `8080`，`ovisocr2` → `8081`。
  代码默认 `paddleocr-vl` + `:8080`（`src/ocr_common.py`）。
- **同一时间只允许一个 `llama-server`**；用完必须停掉释放显存。
- 长页图需要 `-Ctx 16384`，否则整页只出几行（README 排障表）。

仓库本地快照没有 `pyproject.toml` / `package.json` / formatter / linter / `.gitignore` / CI 配置
（上游 v0.1 已加 `.gitignore`）。

## 常用命令

以下命令都在 `ocr_json_app/` 下执行（脚本本身不依赖 cwd，但相对路径以这里为根最省事）。

```powershell
# 一键全流程：起引擎 → 逐行 OCR → 版式 JSON → 单文件阅读器 → 对比页
# （需要 GPU；样例 6 页 + 模型加载约 1.5–2 分钟）
pwsh -NoProfile -File run_ocr.ps1
pwsh -NoProfile -File run_ocr.ps1 -Pdf data\其他卷子.pdf       # 换输入，其余默认

# 备选引擎 OvisOCR2 目前不能走 run_ocr.ps1（见"已知陷阱"）：手动起 :8081 的引擎后显式传参
# pwsh -NoProfile -File $HOME\.dsh-llama\start-ocr-server.ps1 -Model ovisocr2 -Ctx 16384
# python src\ocr_pipeline_layout.py --pdf data\静安区2026届高三一模数学试卷.pdf --model ovisocr2 --host http://127.0.0.1:8081 --vote 3 --out out\paper.layout.json

# 只重建阅读器 / 对比页：不需要 GPU、不跑 OCR（会覆盖 out/ 里的生成文件）
python tools\build_viewers.py
python tools\build_compare.py

# 手动起停引擎（启动脚本默认 OvisOCR2；用 Paddle 时必须显式 -Model）
pwsh -NoProfile -File $HOME\.dsh-llama\start-ocr-server.ps1 -Model paddleocr-vl -Ctx 16384
Get-Process llama-server | Stop-Process        # 用完释放显存

# 两条流水线（引擎已在跑）
python src\ocr_pipeline_layout.py --pdf data\静安区2026届高三一模数学试卷.pdf --out out\paper.layout.paddle.json
python src\ocr_pipeline_blocks.py --pdf data\静安区2026届高三一模数学试卷.pdf --out out\paper.ocr.json

# 经服务打开需要 fetch 的页面（file:// 下会被 CORS 拒绝）
python src\ocr_pipeline_layout.py --serve-only --out out\paper.layout.paddle.json --port 8780
#   → http://127.0.0.1:8780/compare.html
```

`run_ocr.ps1` 只接受 `-Engine -Pdf -Dpi -Vote -Workers`，**没有分页参数**。只跑部分页要用
`python src\ocr_pipeline_layout.py --pages 1-2`（格式只有 `3` 或 `3-8`，没有逗号列表）。

**Windows 控制台默认 GBK**：任何会打印 `✓` / `✗` / `⚠` 的校验前，先设

```powershell
$env:PYTHONIOENCODING = "utf-8"
```

否则 `run_all_checks.py` 汇总时直接抛 `UnicodeEncodeError`；带 warning 的
`check_layout_json.py` 也会以编码错误假失败（实测 Ovis 产物即如此）。这是环境问题，不要改校验逻辑。

## 校验（完成定义）

```powershell
$env:PYTHONIOENCODING = "utf-8"
python tools\run_all_checks.py           # 三层 + 真浏览器；约 8s，当前基线 9/9 通过
python tools\run_all_checks.py --fast    # 跳过浏览器（②里的 cdp_probe），约 0.3s，7/7
```

单独跑：

```powershell
python tools\check_layout_json.py out\paper.layout.paddle.json   # 结构/坐标/行序/幻觉残留
python tools\check_render.py      out\paper.layout.paddle.json   # 裁片压正文/叠字/越界
node   tools\cdp_probe.mjs out\viewer_layout_paddleocrvl.html --shot shot.png
node   verify\verify_math.mjs                                    # 默认即可
node   verify\verify_frontend.mjs                                # 默认即可
python tools\crop_png.py shot.png crop.png 180 1470 560 330 2.2  # 放大局部看清
```

三层分工——改哪层至少跑哪层，UI/版式改动必须跑完整版（含浏览器）：

| 层 | 抓什么 | 工具 |
|---|---|---|
| ① 结构 | JSON 合法、页序、坐标、行序、幻觉残留 | `check_layout_json` / `check_ocr_json` |
| ② 渲染 | **裁片是否压正文**、叠字、越界、浏览器实渲 | `check_render` / `cdp_probe` |
| ③ 内容 | 公式能否被 KaTeX 编译、前端断言 | `verify_math` / `verify_frontend` |

三条验证纪律（README 里都吃过亏）：

- **别只看统计量**。`overlap: 0` 可能因为裁片盖住文字反而"藏"了重叠；`check_render` 专查"裁片 × 正文行"。
- **别把放大截图上的"重影"当真**。PNG 放大 + JPEG 压缩会制造错位；判断双重绘制要看墨迹剖面。
- **没跑过的检查不要声称通过**。引擎没起就停下来说明，不要改用云 OCR、也不要伪造产物。

## 硬约束

1. **`out/` 基本都是生成物**：`paper.layout*.json`、`paper.ocr.json`、`viewer_layout*.html`、
   `viewer.html`、`compare.html`、`out/vendor/`。不要手改；改源码或 `layout_viewer/viewer_template.html`
   后重新生成。`out/compare/` 是历史 A/B 数据，当前 `verify/` 脚本的默认输出名/结构与已提交文件
   不完全一致，不要把它当可复现产物。`vendor/katex/` 是第三方文件，也不要改。
2. **不要拆开模型与提示词**：PaddleOCR-VL 只认官方前缀 `OCR:`；给它自然语言长提示词会退化
   （实测重复输出 `<br>` 达 23 秒）。OvisOCR2 才用整页解析指令。见 `ocr_common.default_prompt_for()`。
   改提示词必须实测并更新 README 的对比表。
3. **两个引擎的产物必须分开命名**：`paths.LAYOUT_JSON` / `VIEWER_BASE` 已按引擎区分；同名会互相
   覆盖，出现"两份 JSON 只有一份能看"。
4. **默认路径不得依赖 cwd**：Python 侧项目根与默认路径从 `src/paths.py` 取（其余用脚本位置推导），
   `verify/*.mjs` 用脚本位置推 `ROOT`。但**显式传入**的路径（`cdp_probe` 的 target、`check_*` 的
   JSON、`crop_png` 的文件）按 cwd 解析，在 shell 里用相对路径前先 `cd` 到 `ocr_json_app`。
5. **import 分层**：`paths` / `ocr_common` 不得 import 任何流水线；两条流水线只依赖它们。
   新的共用逻辑放 `ocr_common`，不要复制。
6. **pypdfium2 渲染必须串行**（原生库多线程会 access violation），只有 HTTP OCR 可以并发。
7. **一次只起一个 `llama-server`**；跑完停掉。不要 `Stop-Process` 与本项目无关的 GPU 进程；
   `run_ocr.ps1` 会先杀掉所有 `llama-server`，在别人也在用时先确认。
8. **插图/表格不信任模型坐标**：几何由像素判定（墨迹比例 / 框线游程）。插图裁片与正文行的
   交叠必须为 **0**（`check_render` 在 > 1 pt² 时即报错），这是红线。
9. **公式定界符随引擎变**：Paddle 是 `\(...\)` / `\[...\]`，Ovis 是 `$...$` / `$$...$$`；
   `build_viewers.py` 按 `document.engine` 自动选，不要写死一种。
10. **不要用 `git add -A`**：本地快照没有 `.gitignore`（上游 v0.1 已加）；
    `ocr_json_app/src/__pycache__/paths.cpython-312.pyc` 已被跟踪，跑过工具后还会多出未跟踪的
    `*.pyc`；`ocr_json_app/` 下还有未跟踪的 `ocr-workflow.html` / `.png`，工作区里还有仓库外的
    `ocr_json_app.rar`。只添加真正改动的文件。

## 改哪里 / 怎么改

- OCR 后处理、行检测、插图/表格裁片 → `src/ocr_pipeline_layout.py`（1477 行；先读顶部 docstring
  与常量）。阈值都有实测依据：改阈值先用 `--pages 1-2` 小范围验证，再跑整卷对比 `stats`。
- 结构块、题号解析 → `src/ocr_pipeline_blocks.py`。
- 共用底层（渲染、HTTP、提示词、网页服务）→ `src/ocr_common.py`。
- 阅读器外观/交互 → `layout_viewer/viewer_template.html`（占位符 `__TITLE__`、`__ENGINE__`、
  `__DELIMS__`、`__JSON_URL__`、`__DATA_SCRIPT__`）。改完必须重新生成并跑 `cdp_probe`；
  KaTeX 必须同步加载（模板注释解释了原因：defer 会在 headless 下让公式退化成源码）。
- 新校验 → `tools/`，并挂进 `tools/run_all_checks.py`；否则不会被任何人跑到。
- 文档：行为/命令变了同步 `README.md` + `ocr_json_app/README.md` + `src/README.md` / `tools/README.md`。

小步原则：改模板/校验不需要 GPU（`build_viewers.py` 读已有 JSON，会 import 流水线所以需要 numpy）；
跑 OCR 才需要。先用 `--pages 1-2 --vote 1 --no-images` 快速迭代，确认后再用 `run_ocr.ps1` 出正式产物。

## 代码风格

- **Python**：3.12；文件头 `#!/usr/bin/env python3` + `# -*- coding: utf-8 -*-`（例外：
  `tools/crop_png.py` 无文件头、直接用 `sys.argv`）；`from __future__ import annotations`；
  公开函数写类型标注；用 `argparse`；模块 docstring 用中文讲**为什么**和用法；行内注释解释取舍，
  而不是复述代码。
- **日志与退出码**：`print` 中文日志带 `[layout]` / `[ocr]` / `[http]` / `[run]` 前缀；流水线错误用
  `die(msg, code)`：`2` = 引擎/依赖，`3` = 输入，`4` = 识别失败（**仅结构块流水线**；版式流水线把
  逐行 OCR 失败记为 warning 不退出）；校验脚本失败则 `return 1`。成功返回 `0`。
- **命名**：`snake_case` 函数/变量，`UPPER_SNAKE` 常量，内部辅助函数前缀 `_`。
- **JS**：ESM `.mjs`，只用 `node:*` 内置 + vendored KaTeX（`createRequire`）；`verify/*.mjs` 用
  脚本位置推 `ROOT`；中文注释；退出码 `0` / `1`。
- **HTML/CSS**：单文件、内嵌 CSS/JS；数据内嵌时把 `<` 转义成 `\u003c`（防 `</script>` 提前截断）。
- **没有 formatter/linter**：按周围风格写，**不要顺手重排无关代码**。全仓库 UTF-8；
  `.gitattributes` 只有 `* text=auto`。

## 已知陷阱

- `run_all_checks.py` 在 GBK 控制台下汇总会崩（见"常用命令"）；先设 `PYTHONIOENCODING=utf-8`。
- `cdp_probe.mjs` 默认浏览器路径写死在 `:31-32`（Playwright `chromium_headless_shell-1234`）。
  不存在时传 `--chrome "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"`。
- `check_layout_json.py` 默认 `--expect-pages 1-6`；`check_ocr_json.py` 默认 `--expect-questions 1-21`。
  只对样例卷成立，换 PDF 必须传参，否则会出现莫名其妙的失败。
- **docstring 有过期的**：`check_layout_json.py` 提到不存在的 `--expect-lines` 和旧文件名
  `ocr_pdf_layout.py`；`ocr_pipeline_layout.py` 顶部说默认 `:8081`，而代码默认 `:8080`。
  以 `argparse` 和 `src/ocr_common.py` 为准；顺手修可以，但别据此写代码。
- **引擎 `--temp 0` 也不是确定性的**，低频随机幻觉真实存在。`run_ocr.ps1` 默认 `--vote 3`；
  直接跑 CLI 默认 `--vote 1`。不要把 `--vote 1` 的一次结果当基准。
- **手动起引擎的默认模型是 OvisOCR2（:8081）**，而流水线默认 `paddleocr-vl`（:8080）。
  手动起 Paddle 一定要传 `-Model paddleocr-vl`，否则健康检查报"连不上 OCR 服务"。
- **`run_ocr.ps1 -Engine ovisocr2` 目前是坏的**：脚本把 Ovis 起在 `:8081`，但调用 Python 时没传
  `--model` / `--host`，流水线用默认 `paddleocr-vl` + `:8080`，健康检查直接 exit 2。要跑 Ovis 按
  "常用命令"里的手动方式，或先修 `run_ocr.ps1:46`（补 `--model $Engine` 与对应 `--host`）。
  实测 Ovis 也更慢（304s vs Paddle 83s，均 vote 3）。
- `run_ocr.ps1` 在 OCR 失败时**提前 `exit`，不会执行收尾的 `Stop-Process`**，引擎会留在后台；
  失败后记得手动 `Get-Process llama-server | Stop-Process`。
- `run_all_checks.py` 会**静默跳过缺失的产物，零项检查也返回 0**。删过 / 没生成 `out/` 时"全绿"是
  假象；看汇总行数（完整 9 项、`--fast` 7 项）确认真的跑了。
- **`--serve-only` 不是纯只读**：版式流水线发现阅读器 HTML 不存在会重新生成；结构块流水线则**总是**
  重写其输出目录的 `viewer.html`（默认 `out/viewer.html`）。
- 已提交的 `out/` 是更早机器上生成的，可能落后于当前代码；不要把它当"可复现基线"，也不要据它推断
  当前代码行为（README 的部分统计数字同样可能对不上，例如 Ovis 的 HTML 表数量）。
- 结构块阅读器 `out/viewer.html` 从 `out/vendor/` 加载 KaTeX；`write_viewer` 的复制源 `src/vendor`
  并不存在，若 `out/vendor/` 缺失会退到 CDN（联网）。只改版式链路时一般不受影响。
- `--no-images` 会让 JSON 不含页图/裁片：阅读器的"原始扫描"与插图会缺失，
  `check_layout_json` 只给 warning。只在明确不需要图时用。
- `out/compare.html` 必须经 `--serve-only` 打开；`file://` 下 fetch 会失败。
- JSON 里的 `document.source_pdf` 记录的是**生成时机器上的绝对路径**（当前样例来自
  `C:\...\新建文件夹\...`），不要据此判断文件在哪。
- 仓库没有 CI；`run_all_checks.py` 就是门槛。`ocr_json_app/ocr-workflow.html` 与
  `.png` 目前未跟踪，不是流水线产物。

## 提交

- 提交前：`git diff` 自查；确认没有把临时截图、`__pycache__`、未跟踪产物带进来。
- 大产物（`out/*.json`、`out/viewer*.html`）有变化时，提交信息里说明是**哪个命令、哪个引擎、
  哪些参数**生成的，便于复现。
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
