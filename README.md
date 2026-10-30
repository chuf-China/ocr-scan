# ocr-scan

目标：把原生（born-digital）试卷 PDF 转成题级 JSON，几何优先、模型只做升级、运行时不设人工复核。

## 现在有什么

- 目标架构文档：`C:\Users\25840\Downloads\Native-PDF Math Exam Conversion Target Architecture.md`
- 目标架构图：`ocr_json_app/docs/native-pdf-target-architecture.html` / `.png`
- 语料：`ocr_json_app/data/exams/`（27 份）、`ocr_json_app/data/`（教师卷、静安区样本）

## 当前状态

- 设计已定稿（v2.2）；工作区当前 **0 行实现代码**，S0–S9 尚未实现。
- S3 数学重建完全未做；M0 只有审计证据（27 份 / 77 页、per-font 778 / 55,741 字符待验证），**没有 gate 代码**；M1–M6 未开工。
- 旧流水线代码（含 `audit_fonts()` / `dump_pua.py` 等旧审计能力）和旧产物都在 legacy，只作为 S8 回退参考，不计入当前工作区实现。

## 旧内容

旧 OCR 流水线代码、旧阅读器/验收产物、旧文档与题库已移到：

`C:\Users\25840\Desktop\ocr-scan-legacy-2026-10-30\`

保留它们是作为 S8 回退路径和审计参考；确认不再需要后可直接删除该目录。
