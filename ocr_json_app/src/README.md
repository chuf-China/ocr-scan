# src/ —— 库与流水线

| 模块 | 职责 |
|---|---|
| `ocr_common.py` | **共用底层**：PDF 渲染、OCR 引擎调用、本地网页服务、提示词选择 |
| `ocr_pipeline_layout.py` | **原版式流水线**：逐行坐标 + 插图裁片 + 表格裁片 → 单文件网页 |
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
