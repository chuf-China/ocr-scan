# S2：公式结构与可编译性

## 目标

公式不能只“看起来像 LaTeX”。每条公式必须：

1. 定界符成对（Paddle 的 `\(...\)` / `\[...\]`，Ovis 的 `$...$`）；
2. 花括号、`\left`/`\right`、`\begin{...}`/`\end{...}` 平衡；
3. 能被真实 KaTeX 编译；
4. 出问题时在 `quality.json` 里定位到页和行，而不是整卷只报“公式错”。

S2 不训练/替换公式模型。它先把“结构坏了”和“模型认错了”分开：
结构问题能自动定位、阻断；模型精度问题由 S4 的抽检和人工修正处理。

## 产物字段

`quality.json` 新增：

```json
{
  "summary": {
    "formula_count": 149,
    "formula_lines": 56,
    "formula_broken": 0,
    "formula_suspicious": 0,
    "formula_samples": [
      {"page": 1, "line_id": "p1-l12",
       "body": "\\frac{x}{2", "issues": ["unbalanced_braces"]}
    ]
  },
  "checks": {
    "formula_structure_ok": true
  }
}
```

结构问题写 `error`（`formula_structure_broken`），让 `check_exam_json.py`
退出 1；可疑字符/超长写 warning（`formula_suspicious`），保留人工抽查入口。

## 检查规则

- 只做结构检查，不改写正文；静默修补会把 OCR 错误藏起来。
- 花括号计数忽略 `\{` / `\}`。
- `\left` / `\right` 用词边界匹配，避免把 `\leftarrow` 算成定界符。
- 环境用栈匹配；`\begin{cases}` 没有对应 `\end{cases}` 即失败。
- `\ufffd`（替换字符）和超长公式（>600 字符）进入 suspicious。
- 真实编译由 `verify/verify_math.mjs` 负责，按 `document.engine` 选 `\(...\)`
  或 `$...$` 定界符；两者是互补门，不互相替代。

## 验收

1. 合法公式：无 issue。
2. `\frac{x}{2`：`unbalanced_braces`。
3. `\left( x`：`unbalanced_left_right`。
4. `\begin{cases} x`：`unbalanced_environment`。
5. 空公式：`empty`。
6. 三套 A3 验收卷：`formula_broken=0`；`verify_math.mjs` 编译通过率 100%。
7. `smoke_pipelines.py` 覆盖以上合成用例。

## 实现状态（2026-10）

- 新增 `src/formula_quality.py`（纯函数，无 Node/浏览器依赖）。
- `exam_structure.build_quality()` 按栏序逐行审计，写 summary/checks/issue。
- `tools/smoke_pipelines.py` 新增 `[11/11] formula_quality`。
- 三套 A3：2023 新高考 I 149 条、2023 北京 153 条、2023 全国乙理 127 条；
  结构错误均为 0，KaTeX 全部可编译。
