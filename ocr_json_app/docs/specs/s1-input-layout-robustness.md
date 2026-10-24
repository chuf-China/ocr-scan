# S1：输入与版面鲁棒性

## 目标

让版面流水线在“不确定输入”上也不静默丢内容：空白页、无文字层、扫描页、旋转页、
单/双/三栏、插图/表格漏检，都必须有明确分类或质量证据。

S1 不负责公式正确率（S2）、表格单元格（S3）和跨页题组（S4），但为它们提供
稳定的页面分类、覆盖率和未覆盖区域坐标。

## 产物字段

每页新增：

```json
{
  "page_type": {
    "kind": "blank | no-lines | text",
    "orientation": "portrait | landscape",
    "columns": 1,
    "has_text_layer": true,
    "rotation": 0
  },
  "coverage": {
    "ink_pixels": 123456,
    "covered_ink_pixels": 123000,
    "ratio": 0.99631,
    "uncovered_regions": [
      {"px": [x0, y0, x1, y1], "pt": [x0, y0, x1, y1],
       "w_pt": 12.3, "h_pt": 8.4, "ink": 88}
    ],
    "uncovered_regions_total": 1
  }
}
```

文档新增 `document.coverage` 汇总；`schema_version` 保持 1，字段只增不改。

## 覆盖审计定义

1. 用 `gray < INK_THRESHOLD` 得到墨迹掩膜。
2. 把已检测行盒（`px_left/top/right/bottom`，外扩 2px）和已检测插图盒
   （`figures[].px`）画入覆盖掩膜。
3. 剩余墨迹按行投影找连续行程；过滤高度小于 `0.45 * typical_h_px` 或
   墨迹少于 30px 的噪点。
4. 每个成片区域写入 `uncovered_regions`；保留最严重的 20 个，
   `uncovered_regions_total` 记录真实总数。
5. 覆盖率 = `covered_ink_pixels / ink_pixels`；空白页（无墨）覆盖率为 1。

## 质量门

- `quality.json` 的 `summary.coverage_ratio` 取所有页最小值。
- 任一页存在未覆盖区域：warning `page_uncovered_ink`，并给出页号、覆盖率、
  区域数。
- `checks.page_coverage_ok` 为 `uncovered_regions_total == 0`。
- 空白页 `page_type.kind=blank`，覆盖率为 1，不产生未覆盖 warning。
- 覆盖率 warning 不直接让 `check_exam_json.py` 退出 1；它必须可见，但是否阻断
  由具体验收标准决定。S1 之后可在发布门里把它升级为 error。

## 验收

1. 合成页：只覆盖一行、故意漏一行 → 必须检出未覆盖区域。
2. 合成页：两行都被行盒覆盖 → 不误报。
3. 纯白页 → `kind=blank`、覆盖率 1、无未覆盖区域。
4. `check_layout_json.py` 校验新增字段的类型、范围和页内坐标。
5. `smoke_pipelines.py` 覆盖以上合成用例。
6. 现有 3 套 A3 卷重跑后：无 `page_uncovered_ink`，或每个 warning 都有明确
   的图/表/扫描背景解释；不得出现整页无分类。

## 非目标

- 不自动旋转页面（S1 只记录 PDF rotation；旋转页会通过低覆盖率暴露）。
- 不判断答案页/答题卡语义（S4）。
- 不把未覆盖区域自动当作插图；只报告，避免把漏行“修”成错误内容。

## 实现状态（2026-10）

- `ocr_pipeline_layout.audit_page_coverage()`、`classify_page()` 已接入
  `build()`；每页写 `page_type`、`coverage`，文档写 `document.coverage`。
- `exam_structure.build_quality()` 写 `page_uncovered_ink` warning、
  `summary.coverage_ratio`、`checks.page_coverage_ok`。
- `tools/check_layout_json.py` 校验字段类型、坐标范围和 `uncovered_regions_total`。
- `tools/smoke_pipelines.py` 覆盖：漏行检出、完整覆盖不误报、空白页、无行扫描页。
- 三份 A3 验收卷：覆盖率 99.27%–99.89%；未覆盖区域均为插图裁片边缘（字母标注/
  框线），已保留 warning，不静默放宽。最终质量门仍为 warn，不是 pass。
