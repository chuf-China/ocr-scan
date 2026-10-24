# 试卷 OCR → JSON → 原版式网页

把扫描版 / 文字层损坏的试卷 PDF 用**本地 VLM-OCR** 转成带版面坐标的 JSON，再渲染成和
试卷原版式一致的单文件网页。全程 `127.0.0.1`，文档不出机器。

> 代码、完整文档与样例产物都在 [`ocr_json_app/`](ocr_json_app/README.md)。本文件只是入口导航。

## 快速开始

```powershell
cd ocr_json_app

# 一条命令跑完：起引擎 → 逐行 OCR → JSON → 单文件网页 → 对比页
pwsh -NoProfile -File run_ocr.ps1

# 打开（单文件，不需要服务器）
start out\viewer_layout_paddleocrvl.html

# 一键跑全部校验（Windows GBK 控制台先设 UTF-8）
$env:PYTHONIOENCODING = "utf-8"
python tools\run_all_checks.py
```

需要 GPU 与本地 llama.cpp 引擎。环境前提、两条流水线、设计取舍、JSON 结构、校验与排障
都在 [`ocr_json_app/README.md`](ocr_json_app/README.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [`ocr_json_app/README.md`](ocr_json_app/README.md) | 完整说明：架构、设计取舍、JSON 结构、校验、排障 |
| [`AGENTS.md`](AGENTS.md) | 给编码代理的仓库约定：命令、硬约束、陷阱 |
| [`ocr_json_app/src/README.md`](ocr_json_app/src/README.md) | 共用底层与两条流水线 |
| [`ocr_json_app/tools/README.md`](ocr_json_app/tools/README.md) | 命令行工具与三层校验 |
| [`ocr_json_app/docs/ocr-workflow.html`](ocr_json_app/docs/ocr-workflow.html) | 可视化工作流与架构（[预览图](ocr_json_app/docs/ocr-workflow-preview.png)） |

## 样例产物

| 产物 | 内容 |
|---|---|
| [`paper.layout.paddle.json`](ocr_json_app/out/paper.layout.paddle.json) | 首选引擎：6 页 / 77 行 / 51 行含公式 / 5 张插图 |
| [`viewer_layout_paddleocrvl.html`](ocr_json_app/out/viewer_layout_paddleocrvl.html) | 单文件渲染（数据内嵌，双击即开） |
| [`compare.html`](ocr_json_app/out/compare.html) | 两个引擎的并排对比（需本地服务） |

## 仓库结构

```text
ocr scan/
├─ README.md                入口导航（本文件）
├─ AGENTS.md / CLAUDE.md    编码代理约定（CLAUDE.md 导入 AGENTS.md）
├─ .gitignore               忽略 pycache / 临时文件
└─ ocr_json_app/            全部代码、数据、文档与样例产物
   ├─ run_ocr.ps1           唯一流水线入口
   ├─ src/ tools/ verify/   库与流水线 / 工具 / 针对性校验
   ├─ layout_viewer/        阅读器模板
   ├─ docs/                 工作流与架构可视化
   ├─ data/                 样例 PDF
   ├─ vendor/               本地 KaTeX（离线可用）
   └─ out/                  生成的 JSON / 单文件 HTML / 对比页
```
