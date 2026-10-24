# S4 内容一致性门（content consistency gate）

## 目标

把“看起来有文字、实际被截断”的题目变成**显式 error**，而不是等到用户阅读时才发现。

S4 只做纯结构/内容审计，不调用 LLM；范围限定在已经重建出的 `exam.json`。

## 规则

1. **非选择题题干截断**：题干以连接词/悬空操作结尾时判 error：
   `则、求、若、已知、设、得、且、及、与、或、的、是、有、由、当、将、把、使、对于、证明、解、计算`；
   或以 `= + - × ÷ * / < ( [ 【` 结尾；或公式括号/定界符不平衡。
   - 选择题题干允许以 `则/若` 结尾（后面本来就跟选项），不套用该规则。
2. **空大题**：`sections[].question_ids` 为空的 section 判 error `section_empty`。
3. 保留已有规则：题号缺口/重复、空题干、选择题少于 4 个选项、空选项、表格空表头/空行、
   公式结构损坏、页面未覆盖墨迹、图/表未归属。

## 输出

`quality.json` 新增：

- `summary.content_truncated`：截断题干数；
- `summary.content_truncated_samples[]`：`{number, reason, tail}`；
- `summary.empty_sections`：空大题数；
- `checks.content_consistency_ok`：以上两项都为 0；
- `issues[]`：`question_content_truncated` / `section_empty`（error）。

## 非目标

- 不做“语义是否通顺”的模型判断；
- 不自动补全被截断的文字；
- 不替代 KaTeX 编译（S2 已覆盖公式）。

## 验收

- 合成单测：非选择题干 `已知函数，则` → `question_content_truncated`；
  空大题 → `section_empty`。
- 3 套 A3 验收卷：`content_consistency_ok=true`、error=0。
- 2024 新高考 A4 样例（截断练习）保持可识别：Q13 的漏空被显式报告，不静默通过。

## 实现状态（2026-10）

- `exam_structure._stem_truncation_reason()` + `build_quality()` 已接入；
- `test_content_truncation_and_empty_section` 已加入 `tools/test_exam_structure.py`；
- `quality.json` 已输出 `content_truncated` / `content_truncated_samples` / `empty_sections`。
