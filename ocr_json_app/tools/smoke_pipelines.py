#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""smoke_pipelines.py —— 代码级冒烟测试：**真的把两个流水线 import 进来跑**。

为什么需要这一层：原有三层校验（结构 / 渲染 / 内容）**全部只看已经生成好的产物**
（out/*.json、out/*.html），从不 import `src/` 里的流水线代码，也不喂合成输入。
于是实测漏掉过这种事故：

    `ocr_pipeline_blocks.py` 里 `SECTION_RE` / `QUESTION_RE` 从未被定义，
    而 `SECTION_RE` 是 `blocks_from_text()` 的**第一个**判断
    → 任何非表格文本块都 `NameError`，整条结构块流水线不可用；
    同一文件 `base64` 也没 import，`work()` 存页图时必然崩。
    而 `run_all_checks.py` 当时 9 项里过了 7 项，**且这 7 项全是产物校验**
    （另外 2 项浏览器校验因硬编码 Chrome 路径失效）——没有任何一项 import 过
    流水线代码，所以代码坏了它一无所知。

本脚本只做"零副作用"的检查：不连 OCR 引擎、不读 PDF、不写任何文件，
因此可以进 CI 的 `--fast` 路径。

用法：
    python tools/smoke_pipelines.py
    python tools/smoke_pipelines.py -v      # 打印每个断言

退出码：0 全部通过；1 有失败。
"""
from __future__ import annotations

import ast
import glob
import importlib.util
import os
import subprocess
import sys

BOOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
ROOT = os.path.dirname(BOOT)
sys.path.insert(0, BOOT)
# 本脚本承诺"不写文件"：不写 .pyc，也不该往仓库里丢 __pycache__
sys.dont_write_bytecode = True

VERBOSE = "-v" in sys.argv or "--verbose" in sys.argv
FAILED: list[str] = []
PASSED = 0
SKIPPED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED
    if ok:
        PASSED += 1
        if VERBOSE:
            print(f"  ok   {name}")
    else:
        FAILED.append(f"{name}{(' —— ' + detail) if detail else ''}")
        print(f"  FAIL {name}{(' —— ' + detail) if detail else ''}")


def skip(name: str) -> None:
    SKIPPED.append(name)
    print(f"  skip {name}")


def load_module(filename: str):
    """按文件路径加载 src/ 下的模块（src 不是包，不能走 import）。"""
    path = os.path.join(BOOT, filename)
    spec = importlib.util.spec_from_file_location(
        os.path.splitext(filename)[0], path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------- ① 低版本语法
def find_python311() -> str | None:
    """找一个 Python 3.11 解释器，用来验证"源码在 3.11 下能编译"。

    为什么必须是**真的** 3.11：`ast.parse(feature_version=(3,9))` 拦不住 PEP 701
    （嵌套同引号 f-string）——它只限制语法**特性**，不改变 f-string 的解析规则，
    实测在 3.12 上对旧写法仍然返回 OK。只有真解释器能拦住。
    """
    import shutil
    found = shutil.which("python3.11")
    if found:
        return found
    # uv 的解释器目录名带补丁号，会随版本变（`cpython-3.11-windows…`
    # 与 `cpython-3.11.15-windows…` 都见过），所以按前缀扫目录而不是写死一个。
    uv_root = os.path.expanduser(r"~\AppData\Roaming\uv\python")
    if os.path.isdir(uv_root):
        for name in sorted(os.listdir(uv_root), reverse=True):
            if not name.startswith("cpython-3.11"):
                continue
            cand = os.path.join(uv_root, name, "python.exe")
            if os.path.isfile(cand):
                return cand
    for cand in (
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\python.exe"),
        "/usr/bin/python3.11",
    ):
        if os.path.isfile(cand):
            return cand
    return None


def test_low_version_syntax() -> None:
    print("[1/6] 低版本解释器语法（只允许 3.9 / 3.11 能编译的写法）")
    files = sorted(glob.glob(os.path.join(BOOT, "*.py")))
    # 先用当前解释器确认全部可解析
    for path in files:
        fn = os.path.basename(path)
        try:
            ast.parse(open(path, encoding="utf-8").read(), fn)
            check(f"当前解释器可解析 · {fn}", True)
        except SyntaxError as exc:
            check(f"当前解释器可解析 · {fn}", False, f"第 {exc.lineno} 行：{exc.msg}")

    py311 = find_python311()
    if not py311:
        skip("3.11 编译 · 未找到 python3.11（本次未验证最低版本兼容性）")
        return
    # 用 -c + compile() 而不是 py_compile：后者会**显式写出 .pyc**（`-B` 也拦不住），
    # 于是这个"不写文件"的脚本会往仓库里丢 __pycache__。compile() 只验证语法。
    script = ("import sys\n"
              "for p in sys.argv[1:]:\n"
              "    compile(open(p, encoding='utf-8').read(), p, 'exec')\n"
              "print('ok')\n")
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([py311, "-c", script, *files],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", env=env)
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    check("3.11 编译全部 src/*.py", result.returncode == 0,
          detail[-1] if detail else "")


# ---------------------------------------------------------------- ② 未定义名
def _free_names_in_functions(tree: ast.Module) -> dict[str, set[str]]:
    """找出"在函数体里被读取、但模块级和函数内都没有绑定"的名字。

    这是 SECTION_RE 事故的直接判据：不依赖任何输入就能抓到，而运行期冒烟测试
    可能正好没走到那个分支。闭包变量、嵌套函数名、局部 import 都算已绑定，
    避免误报。
    """
    import builtins

    def bound_in(node: ast.AST) -> set[str]:
        names: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, (ast.Store, ast.Del)):
                names.add(sub.id)
            elif isinstance(sub, ast.arg):
                names.add(sub.arg)
            elif isinstance(sub, ast.ExceptHandler) and sub.name:
                names.add(sub.name)
            elif isinstance(sub, ast.comprehension):
                for t in ast.walk(sub.target):
                    if isinstance(t, ast.Name):
                        names.add(t.id)
            elif isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                names.add(sub.name)
            elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                for alias in sub.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(sub, ast.Lambda):
                for a in sub.args.args:
                    names.add(a.arg)
        return names

    module_level = bound_in(tree) | {"__file__", "__name__", "__doc__"}
    report: dict[str, set[str]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        local = bound_in(node)
        loaded = {n.id for n in ast.walk(node)
                  if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        missing = {n for n in loaded
                   if n not in local and n not in module_level
                   and not hasattr(builtins, n)}
        if missing:
            report[node.name] = missing
    return report


def test_undefined_names() -> None:
    print("[2/6] 未定义名（函数体引用了模块级没有的名字）")
    for path in sorted(glob.glob(os.path.join(BOOT, "*.py"))):
        fn = os.path.basename(path)
        tree = ast.parse(open(path, encoding="utf-8").read(), fn)
        report = _free_names_in_functions(tree)
        detail = "; ".join(f"{k}(): {sorted(v)}" for k, v in sorted(report.items()))
        check(f"函数内引用都有定义 · {fn}", not report, detail)


# ---------------------------------------------------------------- ③ 结构块流水线
def test_blocks_pipeline() -> None:
    print("[3/6] ocr_pipeline_blocks 纯函数")
    try:
        blocks = load_module("ocr_pipeline_blocks.py")
    except Exception as exc:  # noqa: BLE001
        check("模块可加载 · ocr_pipeline_blocks", False, f"{type(exc).__name__}: {exc}")
        return
    check("模块可加载 · ocr_pipeline_blocks", True)
    # base64 必须真的在模块命名空间里：work() 里用它存页图，曾经漏 import
    check("模块导入 base64", hasattr(blocks, "base64"))

    heading_cases = [
        ("一、填空题（本大题共 12 题）", True),
        ("二．选择题", True),
        ("三. 解答题", True),
        ("一定成立的是（  ）", False),      # 中文量词开头 → 正文，不是标题
        ("三条直线两两相交", False),
        ("这是一段没有编号的说明文字。", False),
    ]
    for text, want in heading_cases:
        try:
            got = blocks.is_section_heading(text)
        except Exception as exc:  # noqa: BLE001
            # 判据本身抛异常（如正则未定义）也必须算失败，不能让整个脚本崩掉
            check(f"小节判据 {text[:14]!r} → {want}", False,
                  f"{type(exc).__name__}: {exc}")
            continue
        check(f"小节判据 {text[:14]!r} → {want}", got == want, f"实得 {got}")

    type_cases = [
        ("1. 已知集合 A={1,2}", "question"),
        ("12、设函数 f(x)=x^2", "question"),
        ("100. 三位数题号不能被漏掉", "question"),   # 旧写法 \d{1,2} 会漏题
        ("一、填空题（本大题共 12 题）", "heading"),
        ("本试卷共 6 页，满分 150 分。", "paragraph"),
        ("（1）求数列的通项公式", "paragraph"),
    ]
    for text, want in type_cases:
        try:
            got = blocks.blocks_from_text(text, 1)[0]["type"]
        except Exception as exc:  # noqa: BLE001
            check(f"块分类 {text[:16]!r} → {want}", False,
                  f"{type(exc).__name__}: {exc}")
            continue
        check(f"块分类 {text[:16]!r} → {want}", got == want, f"实得 {got}")

    md = ("<table><tr><th>学科</th><th>A学校</th><th>B学校</th></tr>"
          "<tr><td>语文</td><td>1</td><td>2</td></tr>"
          "<tr><td>数学</td><td>2</td><td>1</td></tr></table>")
    kinds = [b["type"] for b in blocks.markdown_to_blocks(md, 1)]
    check("真 HTML 表 → table 块", kinds == ["table"], f"实得 {kinds}")
    check("重复标点收敛",
          blocks.normalize_punct("取值范围是 ___。。") == "取值范围是 ___。",
          repr(blocks.normalize_punct("取值范围是 ___。。")))


# ---------------------------------------------------------------- ④ 表形状闸门
def test_table_shape_guard() -> None:
    """`_table_shape_ok` 是"把模型编出来的表挡在页面外"的唯一闸门。

    实测模型会吐 100 列表头（1..100）和整表 1,000.00 的财务表；没有这道闸门，
    页面上会出现一张看起来像原文的假表——比乱码更危险（乱码一眼可辨）。
    """
    print("[4/6] 表格形状闸门（ocr_pipeline_layout._table_shape_ok）")
    try:
        import numpy  # noqa: F401
    except ImportError:
        skip("版式流水线依赖 numpy，未安装 → 跳过")
        return
    try:
        layout = load_module("ocr_pipeline_layout.py")
    except Exception as exc:  # noqa: BLE001
        check("模块可加载 · ocr_pipeline_layout", False, f"{type(exc).__name__}: {exc}")
        return
    check("模块可加载 · ocr_pipeline_layout", True)

    real = [["学科", "A学校", "B学校"], ["语文", "1", "2"], ["数学", "2", "1"]]
    check("真实小表被接受", layout._table_shape_ok(real) is True)
    hundred = [[str(i) for i in range(1, 101)]]
    check("100 列表头伪表被拒", layout._table_shape_ok(hundred) is False)
    money = [["项目", "2018年", "2019年", "2020年"],
             ["收入", "1,000.00", "1,000.00", "1,000.00"],
             ["支出", "1,000.00", "1,000.00", "1,000.00"]]
    check("财务幻觉表被拒", layout._table_shape_ok(money) is False)
    check("空表被拒", layout._table_shape_ok([]) is False)
    ladder = [["x", "y"]] + [[str(i), str(i + 1)] for i in range(1, 6)]
    check("数字阶梯伪表被拒", layout._table_shape_ok(ladder) is False)


# ---------------------------------------------------------------- ⑤ 版式纯函数
def test_layout_helpers() -> None:
    print("[5/6] ocr_pipeline_layout 纯函数（投票 / 幻觉 / 行合并 / 框线）")
    try:
        import numpy
    except ImportError:
        skip("版式流水线依赖 numpy，未安装 → 跳过")
        return
    layout = load_module("ocr_pipeline_layout.py")

    text, votes = layout.majority_vote(["甲乙丙", "甲乙丙", "完全无关的幻觉财务表"])
    check("投票取多数", (text, votes) == ("甲乙丙", 2), f"实得 {(text, votes)}")
    check("三票一致", layout.majority_vote(["题干一行"] * 3)[0] == "题干一行")
    check("空输入不崩", layout.majority_vote([]) == ("", 0))
    check("单票直接采用", layout.majority_vote(["只有一次"])[0] == "只有一次")

    halluc = [
        ("123456789012345678901234", True),   # 超长数字串
        ("x" * 500, True),                    # 超长行
        ("17. 为开展社区与学校教育共建活动，随机抽取了 2 人。", False),
        ("$f(x)=x^2-2x+1$", False),
        ("\\(A_{1}\\)\\n\\n\\(D_{1}\\)\\n\\n\\(B_{1}\\)\\n\\n\\(C_{1}\\)", False),
    ]
    for text, want in halluc:
        got = layout._is_hallucination(text)
        check(f"幻觉判据 {text[:16]!r} → {want}", got == want, f"实得 {got}")

    # 下划线薄行必须并进上一行，且**不能级联**（历史事故：整页 24 行塌成 9 行）
    merged = layout._merge_small_runs([[10, 40], [41, 43], [50, 80], [81, 83]],
                                      min_h=8, gap=4)
    check("薄行并进上一行", merged == [[10, 43], [50, 83]], f"实得 {merged}")

    # 框线表格：合成图必须符合**真实表格的几何约束**，否则会被判据正确地拒绝：
    #   ① 框线游程远长于行距（`densest_band` 以 2×分位间距为簇上限）；
    #   ② 表格盒不超过页面的 45%（`width/height > 0.45 * 页` 直接返回空）。
    #      实测第 17 题的表是 296x273px / 1240x1754px 页 ≈ 24%×16%，这两个上限对真表格
    #      很宽松；随手画的"大半页方格"会被拒——那正是设计意图。
    # ⚠️ 参数是**灰度图**（函数内部做 `gray < INK_THRESHOLD`）：传布尔数组会让
    #    `True == 1 < 200`，整张图都被当成墨。这里一开始就踩了这个坑。

    def grid_gray(page=(400, 600), ys=(), x_span=(0, 0), xs=(), y_span=(0, 0)):
        gray = numpy.full(page, 255, dtype=numpy.uint8)
        for y in ys:
            gray[y, x_span[0]:x_span[1] + 1] = 0
        for x in xs:
            gray[y_span[0]:y_span[1] + 1, x] = 0
        return gray

    table = grid_gray(ys=(80, 120, 160, 200, 240), x_span=(100, 270),
                      xs=(100, 190, 280), y_span=(80, 241))
    grids = layout.detect_table_grids(table, 20.0)
    check("框线表格检出", len(grids) == 1, f"实得 {len(grids)} 个")
    if grids:
        check("表格行列数正确", (grids[0]["rows"], grids[0]["cols"]) == (4, 2),
              f"实得 {grids[0]['rows']}x{grids[0]['cols']}")
        check("表格类型标记正确", grids[0]["kind"] == "table-grid")

    # 反例：占页面近半的大方格必须被拒（它更像整页边框，不是表格）
    big = grid_gray(ys=(20, 120, 220, 320, 380), x_span=(20, 579),
                    xs=(20, 160, 300, 440, 579), y_span=(20, 380))
    check("占页过大的方格被拒", layout.detect_table_grids(big, 20.0) == [])

    blank = numpy.full((400, 600), 255, dtype=numpy.uint8)
    check("空白页不误判成表", layout.detect_table_grids(blank, 20.0) == [])
    text_only = numpy.full((400, 600), 255, dtype=numpy.uint8)
    text_only[100:110, 100:500:4] = 0          # 断续的"文字"笔画，不是框线
    check("纯文字不误判成表", layout.detect_table_grids(text_only, 20.0) == [])


def test_output_path_collision() -> None:
    """认不出的引擎必须单独命名，不能覆盖 OvisOCR2 的产物。

    事故形态：早先 `layout_json_for` 是 `"paddle" in model else ovisocr2`，
    于是 `--model qwen3-vl:8b` 会静默写进 `out/paper.layout.json`——把 OvisOCR2 的
    产物换成了另一个引擎的结果，而 `run_all_checks.py` 还在校验那份文件，
    校验照旧"通过"。这类覆盖没有任何报错，只能靠断言锁住。
    """
    print("[6/6] 产物路径：不同引擎不得互相覆盖")
    import paths

    reserved = {
        "llama.cpp/paddleocr-vl": ("paper.layout.paddle.json",
                                   "viewer_layout_paddleocrvl"),
        "llama.cpp/ovisocr2": ("paper.layout.json", "viewer_layout"),
    }
    for engine, (want_json, want_viewer) in reserved.items():
        got_json = os.path.basename(paths.layout_json_for(engine))
        got_viewer = paths.viewer_base_for(engine)
        check(f"预设引擎映射不变 · {engine}",
              (got_json, got_viewer) == (want_json, want_viewer),
              f"实得 {(got_json, got_viewer)}")

    ovis = os.path.basename(paths.layout_json_for("llama.cpp/ovisocr2"))
    for engine in ("qwen3-vl:8b", "llama.cpp/qwen3-vl:8b",
                   "AuditAid/PaddleOCR-VL-1.6-0.9B:latest", "gpt-4o"):
        got = os.path.basename(paths.layout_json_for(engine))
        check(f"陌生引擎不落到 ovis 产物 · {engine}", got != ovis, f"实得 {got}")
        check(f"陌生引擎有自己的阅读器名 · {engine}",
              paths.viewer_base_for(engine) not in ("viewer_layout",
                                                    "viewer_layout_paddleocrvl"))
    # 同一个陌生引擎两次调用必须稳定（否则每次跑都会新建一份产物）
    a = paths.layout_json_for("qwen3-vl:8b")
    check("同一引擎路径可重复", a == paths.layout_json_for("qwen3-vl:8b"))
    check("空引擎名不产生非法路径",
          os.path.basename(paths.layout_json_for("")).endswith(".json"))


def main() -> int:
    print("=" * 78)
    print("### 代码级冒烟（不连引擎、不读 PDF、不写文件）")
    print("=" * 78)
    test_low_version_syntax()
    test_undefined_names()
    test_blocks_pipeline()
    test_table_shape_guard()
    test_layout_helpers()
    test_output_path_collision()
    print(f"\n{PASSED} 个通过 / {len(FAILED)} 个失败"
          f"{f' / {len(SKIPPED)} 个跳过' if SKIPPED else ''}")
    for f in FAILED:
        print("  ✗ " + f)
    if FAILED:
        print("\n结果：失败")
        return 1
    print("\n结果：通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
