# 试卷 OCR → JSON → 原版式网页

把扫描版 / 文字层损坏的试卷 PDF，用**本地 VLM-OCR** 转成带版面坐标的 JSON，
再渲染成和原卷版式一致的网页。全程 `127.0.0.1`，文档不出机器。

**代码在 [`ocr_json_app/`](ocr_json_app/)，文档也在那里：[ocr_json_app/README.md](ocr_json_app/README.md)**
（架构、数据流、设计取舍、JSON 结构、校验分层、已知限制、排障，全在里面。）

> 给编码代理的仓库约定见 [`AGENTS.md`](AGENTS.md)；可视化工作流见
> [`ocr_json_app/docs/ocr-workflow.html`](ocr_json_app/docs/ocr-workflow.html)，
> 官方流程实战见 [`ocr_json_app/docs/paddleocr-vl-guide.md`](ocr_json_app/docs/paddleocr-vl-guide.md)。

```powershell
cd ocr_json_app
pwsh -NoProfile -File run_ocr.ps1          # 起引擎 → 逐行 OCR → JSON → 单文件网页
start out\viewer_layout_paddleocrvl.html   # 双击即可，数据内嵌
python tools\run_all_checks.py             # 一键跑全部校验
```

当前产物（`ocr_json_app/out/`，静安区 2026 届高三一模数学试卷，6 页 A4）：

| 产物 | 内容 |
|---|---|
| `paper.layout.paddle.json` / `paper.layout.json` | 首选 / 备选引擎的逐行版式数据（77 / 69 行，5 张插图） |
| `viewer_layout_paddleocrvl.html` / `viewer_layout.html` | 单文件阅读器（数据内嵌，双击即开） |
| `compare.html` | 两个引擎并排对比（需本地服务） |
| `paper.ocr.json` + `viewer.html` | 结构块流水线（heading / question / table，供程序消费） |

---

## 环境前提（本仓库不含这些，需自行准备）

这是**有意为之**：仓库只存代码与产物，模型权重、运行时依赖、引擎启动脚本都在机器上，
不随 git 走。所以换了机器**不能**直接跑，先补齐下面四项。

| 依赖 | 用途 | 缺失时的表现 |
|---|---|---|
| Python **3.9+** | 两个流水线 | 见下方"解释器" |
| `pypdfium2`、`Pillow`、`numpy` | PDF 渲染、像素投影 | `缺少 pypdfium2（PDF 渲染库）` |
| `pdfplumber`（**可选**） | 从文字层读字号 | 自动降级为按行高估算字号，不报错 |
| 本地 llama.cpp OCR 引擎 | 逐行识别 | `连不上 OCR 服务 http://127.0.0.1:8080` |
| Chromium 内核浏览器（**仅校验用**） | `cdp_probe.mjs` 真浏览器渲染 | 会提示用 `--chrome` 指定 |

```powershell
python -m pip install pypdfium2 Pillow numpy
python -m pip install pdfplumber        # 可选：文字层字号

# OCR 引擎（llama.cpp + VLM）。脚本路径按本机既有约定：
pwsh -NoProfile -File $HOME\.dsh-llama\start-ocr-server.ps1 -Model paddleocr-vl -Ctx 16384
#   paddleocr-vl → 127.0.0.1:8080      ovisocr2 → 127.0.0.1:8081
#   必须 -Ctx 16384：上下文太小会让整页只出 3 行
Get-Process llama-server | Stop-Process   # 用完释放显存
```

**解释器**：代码要求 **3.9+**，并在 3.11 / 3.12 / 3.14 上验证过。若 `python` 指向更老的版本，
两个流水线都会打印一行明确提示后以退出码 2 结束（不会只丢一个看不懂的 `SyntaxError`）。

**校验用的浏览器**：[ocr_json_app/tools/cdp_probe.mjs](ocr_json_app/tools/cdp_probe.mjs)
会自动按顺序找：`--chrome` 参数 → 环境变量 `CHROME_PATH` → 系统 Chrome → 系统 Edge →
playwright 下载的 chromium（取最新版本目录）。都没有才会报错。

---

## 校验从哪一层开始看

`python tools\run_all_checks.py` 分四层，越靠下越接近用户真正看到的东西：

| 层 | 抓什么 |
|---|---|
| ⓪ **代码** | 真的 import 两个流水线跑纯函数：未定义名、低版本语法、投票/幻觉/框线判据 |
| ① 结构 | JSON 自身是否合法、页序、题号、幻觉残留 |
| ② 渲染 | **裁片是否压正文**、行是否叠字、是否越界、浏览器实渲节点数 |
| ③ 内容 | 公式能否被 KaTeX 编译、前端断言 |

⓪ 是后来补上的：① ② ③ **全部只看已经生成好的产物**，所以"代码已经坏了"它们一无所知
——`SECTION_RE` 从未定义（且是函数里的第一个判断，必然 `NameError`）这种事故，
当时 `run_all_checks.py` 的 **9 项里有 7 项"产物校验"全绿**，只有 2 项浏览器校验因为
硬编码的 Chrome 路径失效而失败，**没有任何一项碰到过流水线代码**。

**边界说明**：这些校验能证明代码层与已有产物是好的，**不能**证明端到端可用。
本仓库的产物是更早的机器上生成的；要在新机器上用，先按下文「环境前提」补齐依赖，
并**先跑通一次 `run_ocr.ps1`**。详见 [ocr_json_app/README.md](ocr_json_app/README.md)
的「环境前提」与「已知限制」。

---

## 许可与数据

`data/` 下是用于自测的公开试卷，`vendor/katex/` 是本地缓存的 KaTeX（离线可用）。
模型权重不在本仓库内。
