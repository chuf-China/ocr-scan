# tools/ 与 verify/ —— 工具与校验

## tools/ —— 命令行工具

| 工具 | 作用 |
|---|---|
| `run_all_checks.py` | **一键跑全部校验**（CI 入口），`--fast` 跳过浏览器 |
| `build_viewers.py` | 为每份版式 JSON 生成单文件渲染（按 `document.engine` 自动选定界符） |
| `build_compare.py` | 生成两引擎并排对比页 `out/compare.html` |
| `build_exam_json.py` | 版式 JSON → `exam.json` + `quality.json`（题号/选项/小问/图归属） |
| `run_exam_acceptance.py` | **批量验收**：多份 PDF/JSON → 覆盖率/选项/公式/表格/原生字/样式/乱码/人工动作汇总，硬门失败退出 1 |
| `check_layout_json.py` | 版式 JSON：页序、坐标是否在页内、行序、幻觉残留；含 `regions` 的产物还校验区域层（id/bbox/枚举/回链/统计一致），含 `formulas` 证据的产物校验公式复核块（来源/dpi/一致性/汇总计数） |
| `review_formulas.py` | **公式复核（证据模式）**：PP-DocLayout_plus-L 出公式框 → 300dpi 重渲裁片 → PP-FormulaNet_plus-M（CPU 子进程）出 LaTeX → 与现有 OCR 做 `exact/normalized/partial/differs` 比对；只写证据，不改正文 |
| `build_formula_ab.py` | **公式内容级 A/B 材料**：按一致性配额抽样，从原始 PDF 重渲裁片拼成带序号对照图 + `review.md` 明细表，供人工逐条判「模型更好 / 现有更好 / 相同」；只读，不跑模型 |
| `check_exam_json.py` | **试卷结构门**：题号/选项/图归属/质量；`quality.status=fail` 退出 1；历史产物可加 `--advisory` |
| `check_render.py` | **裁片是否压正文**、行是否叠字、是否越界 |
| `check_ocr_json.py` | 结构块 JSON：页码、题号连续性、截断、乱码 |
| `cdp_probe.mjs` | 真浏览器渲染：等静置后取诊断量（节点数/叠字/越界）并可整页截图 |
| `crop_png.py` | 从截图裁一块放大，用于看清局部 |

## verify/ —— 针对性断言

| 脚本 | 作用 |
|---|---|
| `verify_math.mjs` | 用真实 KaTeX 逐条编译 JSON 里的公式（定界符按引擎自动选） |
| `verify_frontend.mjs` | 最小 DOM 桩真跑渲染脚本并断言（不是只看语法） |
| `ocr_ab.py` | 两个引擎的量化 A/B：同一批图，指标含幻觉数字串、重复片段 |
| `compare_ocr.py` | 两个引擎在关键区域上的并排输出 |

全部工具的路径都**相对项目根**解析（`tools/`、`verify/` 通过 `../src/paths.py`），
所以在任何工作目录下调用都可以：

```powershell
python tools\run_all_checks.py
python tools\check_render.py out\paper.layout.paddle.json
node   verify\verify_math.mjs                     # 默认参数即可
```

## 校验为什么必须分层

| 层 | 抓什么 | 工具 |
|---|---|---|
| ⓪ 代码 | 未定义名、低版本语法、纯函数判据 | `smoke_pipelines.py` |
| ① 结构 | JSON 自身是否合法、页序、坐标、行序、幻觉残留 | `check_layout_json` / `check_ocr_json` |
| ② 渲染 | 裁片是否压正文、行是否叠字、越界、浏览器实渲 | `check_render` / `cdp_probe` |
| ③ 试卷 | 题号连续、选项 A–D、小问/图归属、跨页续题 | `exam_structure` / `check_exam_json` |
| ④ 内容 | 公式能否编译、前端断言 | `verify_math` / `verify_frontend` |
| ⑤ 验收 | 多套真题的覆盖率/选项/人工动作门槛 | `run_exam_acceptance` |

实测"插图裁片盖住题干"时：结构层通过、内容层通过、行与行的重叠统计也还是 0（裁片不是
`.ln`，不在该统计口径里），**只有渲染层抓得到**。所以 `run_all_checks.py` 各层都跑。
