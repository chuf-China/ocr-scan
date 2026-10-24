# src/ —— 库与流水线

| 模块 | 职责 |
|---|---|
| `ocr_common.py` | **共用底层**：PDF 渲染、OCR 引擎调用、本地网页服务、提示词选择 |
| `ocr_pipeline_layout.py` | **原版式流水线**：原生图形资源 + 逐行坐标 + 插图/表格裁片 → 单文件网页 |
| `ocr_pipeline_blocks.py` | **结构块流水线**：heading/question/paragraph/table（供程序消费） |
| `paths.py` | 项目根与所有默认路径（脚本不依赖当前工作目录） |

层级是单向的：`paths` / `ocr_common` 不 import 任何流水线；两条流水线只依赖它们。
早先 `ocr_pipeline_layout` 是从 `ocr_pipeline_blocks` 里 import 的（那时两者只有渲染与 HTTP 共用），
于是"扫描 PDF 的库"依赖了"搜索排序格式的程序"——现在共用部分抽到了 `ocr_common`。

```powershell
python src\ocr_pipeline_layout.py --pdf data\试卷.pdf --out out\paper.layout.paddle.json
python src\ocr_pipeline_blocks.py --pdf data\试卷.pdf --out out\paper.ocr.json
python src\ocr_pipeline_layout.py --serve-only --out out\paper.layout.paddle.json --port 8780
```

两者都提供了 `main()`，可直接当脚本跑。详细设计取舍见 ../README.md。

## 原生文字层（layout 流水线）

PDF 本身有文字层时，`ocr_pipeline_layout.py` 会从 `pdfplumber` 的 chars 取原生字符，按 y
聚成行，和 OCR 行做几何 + 文本比对；结果只作为**旁路**，不直接覆盖 OCR：

- `native_text` / `native_quality` / `native_similarity` / `native_mismatch`：每行的候选原生文字、
  质量（`text` / `mixed` / `math` / `empty`）、匹配相似度、是否明显不一致；
- 页和文档的 `native_text` 统计：`chars`、`lines`、`matched_lines`、`mismatched_lines`、
  `native_only_lines`、`fallbacks`；
- 只有 OCR 行文字为空、且原生行质量是 `text` 时才用 `native-text` 兜底；公式行仍走 OCR。

数学卷的原生字层常把公式拆成 `x2`/`y0`，或使用私有字库；原生文字只用于质量诊断和
“OCR 缺字”兜底，不能拿它替换公式。
