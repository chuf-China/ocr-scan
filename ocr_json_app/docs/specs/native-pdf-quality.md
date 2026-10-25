# 原生 PDF 质量规格（P0–P3）

## 背景

输入是**带文字层的原生数学试卷 PDF**，但旧流水线仍把每一行都送 OCR：20 套验收的
1760 行中 `text_source=ocr` 为 1760/1760。结果是：

- 有文字层却不用，OCR 的随机出错和慢速被完整保留；
- 散点图的点、坐标轴标题、图例被裁掉；
- 字体样式（粗体/斜体/字号/颜色）全部丢失，重建版式看起来“不像原卷”；
- 私有字库的 `(cid:)`/替换符可能静默进入正文。

目标不是“把所有 OCR 换成原生字”，而是：**能安全用原生字的地方不再 OCR，公式/乱码/
可疑区域仍交给 OCR**，并把图、样式、字体问题变成可验收的指标。

## P0 原生快路

`--text-source auto`（默认）：

1. `pdfplumber` chars 聚成原生行；
2. 只接受 `native_quality=text` 的行；文本必须无公式定界符、无 `(cid:)`、无 U+FFFD/
   控制符，CJK 字符 ≥2 且 CJK 占比 ≥45%；
3. `match_native_lines_to_pixels()` 按行盒做一对一匹配：纵向重叠 / 较小行高 ≥0.45，
   横向重叠 / 较小行宽 ≥0.25；
4. 命中的行写 `text_source=native-text`，跳过 OCR；`task_png` 为空，OCR 池只跑其余行；
5. 任何整页回退会清空该页快路；`--text-source ocr` 强制全 OCR。

记录：`document.native_text.fast`、`stats.native_text_fast_path`、每行
`text_source`。快路行数与 OCR 行数必须能在报告里分开，不能只报总行数。

## P1 插图/标注裁片

原生矢量/图片簇仍按 `_native_figure_boxes()` 取：

- `exclude_boxes=native_tables`：与 `pdfplumber.find_tables()` 确认的表格 bbox 相交
  ≥45% 的簇不当插图；不再用旧的“内部有 ≥12 字符且长行 ≥3”启发式误杀图；
- `_expand_box_with_labels()`：吸收 bbox 周围 12pt 内的小字号字符（远离矢量时最多
  26pt），覆盖坐标轴标题、点标注、图例；
- 相邻矢量簇间隙 < `NATIVE_FIGURE_GAP_PT=14pt` 时合并，避免同一张图被拆成两块；
- 裁片按扩后的 bbox 以 300dpi 出图，表格/插图裁片与正文行交叠仍必须为 0。

## P2 行样式

`annotate_line_styles(chars, line, scale, native_line)` 从 chars 取：

- 字号：字符高度中位数（pt），夹在 5–36pt；
- `bold`：fontname 含 `bold/heavy/black` 或 flags 第 5 位；
- `italic`：fontname 含 `italic/oblique` 或 flags 第 2 位；
- `color`：`non_stroking_color` 转 `#RRGGBB`（灰度/RGB/CMYK）；
- `font_family`：`serif` / `sans`，`font_name` 保留原字体名。

字段写在 `line.style`，只对原生字层可信的行写；阅读器在 `fitLineWidth()` 之前应用，
避免样式改变后行宽估算失真。

## P3 字体审计与乱码门

`audit_fonts(chars, lines)` 每页/文档输出：

- `fonts` / `font_count` / `font_names`；
- `pua_chars`（U+E000–U+F8FF）、`cid_chars`（`(cid:`）、`replacement_chars`（U+FFFD）；
- `garbled_lines` / `garbled_samples`（行号 + 文本片段）。

`exam_structure` 把有乱码的行写成 `garbled_text` error，并进入 `quality.repair`
的 `manual` 队列；不允许“能渲染”吞掉乱码。`document.font_audit` 同时汇总。

## 验收

`tools/run_exam_acceptance.py` 新增指标：

- `native_ratio` / `native_fast_lines`：原生字行占比；
- `style_ratio` / `styled_lines`：带样式的行占比；
- `garbled_lines` / `font_count`。

报告表格新增“原生”列，并在汇总里打印平均原生占比、平均样式占比、乱码行总数。
这些是**诊断指标**，不替代覆盖率/公式/表格/选项硬门；它们用来证明“原生 PDF 不再
被当扫描件”，而不是用原生字比例换掉内容正确性。

本次 23 套（20 套历年 + 3 套未见 2022 卷）基线：23/23 通过，平均覆盖率 99.98%，
公式/表格 100%，原生直出 34.22%，样式覆盖 100%，乱码 0 行，人工动作 0.15/页。
原生占比不到一半是刻意取舍：含公式/上下标的行仍走 OCR，避免把 `x2` 这类原生字层
退化解当成公式排版。

## 非目标

- 不用原生字替换公式；
- 不自动修复私有字库/乱码；
- 不因为原生占比高就放宽题号、选项、覆盖率门槛；
- 不把 `native_ratio` 当作内容准确率。
