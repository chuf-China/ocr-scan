# tools/ 与 verify/ —— 工具与校验

## tools/ —— 命令行工具

| 工具 | 作用 |
|---|---|
| `run_all_checks.py` | **一键跑全部校验**（CI 入口），`--fast` 跳过浏览器 |
| `build_viewers.py` | 为每份版式 JSON 生成单文件渲染（按 `document.engine` 自动选定界符） |
| `build_compare.py` | 生成两引擎并排对比页 `out/compare.html` |
| `check_layout_json.py` | 版式 JSON：页序、坐标是否在页内、行序、幻觉残留 |
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

## 三层校验为什么必须都在

| 层 | 抓什么 | 工具 |
|---|---|---|
| ① 结构 | JSON 自身是否合法、页序、题号、幻觉残留 | `check_layout_json` / `check_ocr_json` |
| ② 渲染 | 裁片是否压正文、行是否叠字、越界、浏览器实渲 | `check_render` / `cdp_probe` |
| ③ 内容 | 公式能否编译、前端断言 | `verify_math` / `verify_frontend` |

实测"插图裁片盖住题干"时：①通过、③通过、行与行的重叠统计也还是 0（裁片不是 `.ln`，
不在该统计口径里），**只有 ② 抓得到**。所以 `run_all_checks.py` 三层都跑。
