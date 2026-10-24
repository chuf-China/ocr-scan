# S5 规格：质量修复队列（不编造内容）

## 目标

把 `quality.json` 的 error/warning 变成**可审计、可执行的修复队列**：

1. 每个问题有明确的 `action`、`mode`（`auto_candidate` / `manual` / `blocked`）、
   `target` 和原因；
2. 只有证据足够、幂等、失败可回退的动作才标 `auto_candidate`（当前仅空表格
   单元格逐格重识别）；
3. 截断、缺选项、图/表未归属、未覆盖墨迹、原生字层不一致一律 `manual`，
   **不自动补字、不猜题号、不猜内容**；
4. 队列写进 `quality["repair"]`，`check_exam_json.py` 打印 auto/manual 计数。

这不是“自动把试卷修到全绿”。质量门的 error 仍然会让检查失败；修复队列只是告诉
人/上层该做什么，避免看到一堆 issue 无从下手。

## 数据模型

```json
"repair": {
  "actions": [
    {"code": "table_empty_cell", "action": "reocr_table_cell",
     "mode": "auto_candidate", "reason": "...", "target": "p1-t1",
     "message": "..."}
  ],
  "counts": {"auto_candidate": 1, "manual": 2, "blocked": 0},
  "total": 3,
  "auto_executed": false,
  "note": "auto_candidate 只是允许重试，执行前仍应保留原 JSON；manual 不得自动补内容"
}
```

### 动作表

| issue.code | action | mode |
|---|---|---|
| `table_empty_cell` | `reocr_table_cell` | auto_candidate |
| `table_empty_header` | `manual_table_header` | manual |
| `table_unassigned` | `attach_table` | manual |
| `figure_unassigned` | `attach_figure` | manual |
| `page_uncovered_ink` | `review_uncovered_region` | manual |
| `native_ocr_mismatch` | `review_native_text` | manual |
| `choice_options_incomplete` | `review_question_region` | manual |
| `question_content_truncated` | `review_question_tail` | manual |
| `question_number_duplicate` / `question_number_missing` | `review_question_numbers` | manual |
| `formula_structure_broken` | `review_formula` | manual |
| `section_empty` | `review_section` | manual |
| `line_overflow` / `line_overlap` | `review_line_box` | manual |
| `page_blank` | `blocked_blank_page` | blocked |
| 未登记 | `manual_review` | manual |

同一 `(code, action, target)` 只保留一条，避免同一页 20 个空单元格刷屏。

## 非目标

- 不自动补题干/选项/表头；
- 不自动挂图/表到题号；
- 不自动改公式；
- 不把 `quality.status` 改成 pass；
- 不依赖 OCR 服务（纯函数，可单测）。

## 验收

1. `tools/test_exam_structure.py` 覆盖：空单元格 → auto、截断 → manual、
   相同 target 去重；
2. 三套 A3 验收卷和 2024 竖版切片的 `quality.json` 都含 `repair`，且
   `auto + manual + blocked == total`；
3. `check_exam_json.py` 在质量门行后打印修复队列计数；
4. 全量检查 `21/21`、冒烟 `112+` 通过；
5. 文档同步 `AGENTS.md` 第 19 条、`src/README.md`、`ocr_json_app/README.md`。

## 后续（S6）

S6 再把 3 套新卷的 `repair` 汇总成验收集报告：哪些 auto 被执行过、哪些 manual
需要人工确认、每页修复成本。S5 不提前宣称“已自动修复”。
