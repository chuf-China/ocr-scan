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
import io
import json
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
    print("[1/10] 低版本解释器语法（只允许 3.9 / 3.11 能编译的写法）")
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
    print("[2/10] 未定义名（函数体引用了模块级没有的名字）")
    for path in sorted(glob.glob(os.path.join(BOOT, "*.py"))):
        fn = os.path.basename(path)
        tree = ast.parse(open(path, encoding="utf-8").read(), fn)
        report = _free_names_in_functions(tree)
        detail = "; ".join(f"{k}(): {sorted(v)}" for k, v in sorted(report.items()))
        check(f"函数内引用都有定义 · {fn}", not report, detail)


# ---------------------------------------------------------------- ③ 结构块流水线
def test_blocks_pipeline() -> None:
    print("[3/10] ocr_pipeline_blocks 纯函数")
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
    print("[4/10] 表格形状闸门（ocr_pipeline_layout._table_shape_ok）")
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
    print("[5/10] ocr_pipeline_layout 纯函数（投票 / 幻觉 / 行合并 / 框线）")
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

    # 150dpi 小字行裁片必须放大；否则模型会把整行吐成乱码。
    canvas = layout.Image.new("RGB", (300, 200), "white")
    line = {"px_left": 80, "px_top": 50, "px_right": 220, "px_bottom": 90}
    crop = layout.Image.open(io.BytesIO(layout.crop_line(canvas, line, extra_left=40)))
    check("小字行裁片放大 2 倍", crop.height == 2 * (90 - 50 + 1 + 2 * 3),
          f"实得 {crop.height}")

    # 横向 A3 卷：2/3 栏都必须按“整列空白 + 两侧有文字”检出中缝。
    # 历史事故：假设中缝在页面正中，三栏卷的第 2 条中缝漏掉，右栏文字
    # 被并进中栏；这里用合成三栏页锁死“不假设栏数和居中”。
    def column_page(width=1200, height=800, spans=((40, 340), (400, 700), (760, 1060))):
        gray = numpy.full((height, width), 255, dtype=numpy.uint8)
        boxes = []
        for x0, x1 in spans:
            for y in range(60, 560, 24):
                gray[y:y + 12, x0:x1] = 0
                boxes.append({"px_left": x0, "px_right": x1,
                              "px_top": y, "px_bottom": y + 12,
                              "px_height": 12, "ink": (x1 - x0) * 12})
        return gray, boxes

    three, boxes3 = column_page()
    g3 = layout.detect_column_gutters(three, lines=boxes3)
    check("三栏卷检出两条中缝", len(g3) == 2, f"实得 {g3}")

    two, boxes2 = column_page(width=1000, spans=((40, 430), (520, 910)))
    g2 = layout.detect_column_gutters(two, lines=boxes2)
    check("两栏卷检出一条中缝", len(g2) == 1, f"实得 {g2}")

    # 真实栏缝比“栏内短行空隙”宽：2023 北京第 2 页左栏很短，按两侧字数
    # 会把栏内 8–13px 的空隙选成中缝，丢掉左/中两栏。这里在三栏页的
    # 文字盒里再切一条 10px 窄缝，锁死“取最宽”的判据。
    gray4 = numpy.full((800, 1200), 255, dtype=numpy.uint8)
    chars = []
    for y in range(60, 560, 40):
        gray4[y:y + 20, 40:340] = 0
        gray4[y:y + 20, 400:700] = 0
        gray4[y:y + 20, 760:1060] = 0
        for x0, x1 in ((40, 250), (260, 340), (400, 610), (620, 700),
                       (760, 1060)):
            chars.append({"x0": (x0 + 4) / 2.0833, "x1": (x1 - 4) / 2.0833,
                          "top": (y + 4) / 2.0833, "bottom": (y + 16) / 2.0833,
                          "text": "字"})
    g4 = layout.detect_column_gutters(gray4, chars=chars)
    check("栏内窄空隙不抢真实栏缝", len(g4) == 2, f"实得 {g4}")

    portrait = numpy.full((1200, 800), 255, dtype=numpy.uint8)
    portrait[60:560, 100:700] = 0
    check("纵向单栏不误判", layout.detect_column_gutters(portrait) == [])


def test_native_figures() -> None:
    """原生图形资源 → 掩膜 → 高分辨率裁片，全部用合成页面锁定几何行为。

    历史事故：Q11 曲线图 / Q17 四棱锥的墨迹被投影进正文行，整行撑成 141.6pt，
    渲染时压到下一节标题。像素掩膜只能按"文字少"猜图形，混排图必然漏；这里用
    PDF 自己的 image / curve / line / rect 资源，并用表格与装饰图反例锁死过滤。
    """
    print("[6/10] ocr_pipeline_layout 原生图形（图片 / 矢量簇 / 掩膜 / 300dpi 裁片）")
    try:
        import numpy
        from PIL import Image
    except ImportError:
        skip("原生图形测试依赖 numpy / Pillow，未安装 → 跳过")
        return
    layout = load_module("ocr_pipeline_layout.py")

    class FakePage:
        width = 595.0
        height = 842.0

        def __init__(self, images=(), curves=(), lines=(), rects=(), chars=()):
            self.images = list(images)
            self.curves = list(curves)
            self.lines = list(lines)
            self.rects = list(rects)
            self.chars = list(chars)

    # 1) 图片：Q11 那种 85x133pt 的图必须检出；12x8pt 的装饰图必须忽略。
    page = FakePage(images=[{"x0": 448.0, "top": 370.0, "x1": 533.0, "bottom": 503.0}])
    boxes = layout._native_figure_boxes(page)
    check("原生图片检出", len(boxes) == 1 and boxes[0]["sources"] == ["pdf-image"],
          f"实得 {boxes}")
    page = FakePage(images=[{"x0": 10.0, "top": 10.0, "x1": 22.0, "bottom": 18.0}])
    check("装饰小图被忽略", layout._native_figure_boxes(page) == [])
    # 纯扫描 PDF：整页是一张位图，它是页面背景，不是插图；误当图会涂掉整页正文。
    page = FakePage(images=[{"x0": 0.0, "top": 0.0, "x1": 595.0, "bottom": 842.0}])
    check("整页扫描图不被当成插图", layout._native_figure_boxes(page) == [],
          f"实得 {layout._native_figure_boxes(page)}")

    # 2) 矢量图：坐标轴/曲线/刻度分成多个 path，按邻近关系并成一个框。
    curves = [
        {"x0": 100, "top": 100, "x1": 200, "bottom": 220},
        {"x0": 120, "top": 90, "x1": 122, "bottom": 230},
        {"x0": 80, "top": 150, "x1": 220, "bottom": 152},
    ]
    page = FakePage(curves=curves)
    boxes = layout._native_figure_boxes(page)
    check("矢量簇并成一个图", len(boxes) == 1 and boxes[0]["sources"] == ["pdf-vector"],
          f"实得 {boxes}")
    if boxes:
        check("矢量簇 bbox 取并集", boxes[0]["pt"][:2] == [80.0, 90.0]
              and boxes[0]["pt"][2:] == [220.0, 230.0], f"实得 {boxes[0]['pt']}")

    # 3) 表格反例：长直线 + 格内大量字符，不能被当成矢量图。
    table_lines = [
        {"x0": 100, "top": 100, "x1": 400, "bottom": 100},
        {"x0": 100, "top": 100, "x1": 100, "bottom": 300},
        {"x0": 100, "top": 300, "x1": 400, "bottom": 300},
    ]
    table_chars = [{"x0": 110 + i * 8, "top": 120 + i * 5,
                    "x1": 114 + i * 8, "bottom": 126 + i * 5} for i in range(20)]
    page = FakePage(lines=table_lines, chars=table_chars)
    check("表格不被当成矢量图",
          layout._native_figure_boxes(page, exclude_boxes=[
              {"bbox_pt": [100, 100, 400, 300]}]) == [])

    # 3b) 散点图的刻度/轴标题在路径 bbox 外侧，必须吸收进图框。
    page = FakePage(curves=[
        {"x0": 100, "top": 100, "x1": 200, "bottom": 220},
        {"x0": 80, "top": 150, "x1": 220, "bottom": 152},
    ], chars=[{"x0": 86, "top": 146, "x1": 94, "bottom": 154},
              {"x0": 140, "top": 226, "x1": 148, "bottom": 234}])
    boxes = layout._native_figure_boxes(page)
    check("图簇吸收刻度/轴标题", len(boxes) == 1
          and boxes[0]["pt"] == [80.0, 100.0, 220.0, 234.0],
          f"实得 {boxes}")

    # 4) 占页大半的边框也必须拒绝。
    page = FakePage(rects=[{"x0": 10, "top": 10, "x1": 590, "bottom": 830}])
    check("近整页边框被拒绝", layout._native_figure_boxes(page) == [])

    # 5) 图片与矢量簇重叠时只保留一个，来源合并。
    page = FakePage(
        images=[{"x0": 448.0, "top": 370.0, "x1": 533.0, "bottom": 503.0}],
        curves=[{"x0": 450.0, "top": 372.0, "x1": 531.0, "bottom": 501.0}],
    )
    boxes = layout._native_figure_boxes(page)
    check("图片与矢量重叠合并", len(boxes) == 1
          and set(boxes[0]["sources"]) == {"pdf-image", "pdf-vector"}, f"实得 {boxes}")

    # 6) 掩膜：只有图形区域变白，边缘 margin 生效，图外像素不动。
    gray = numpy.full((100, 100), 7, dtype=numpy.uint8)
    masked = layout._mask_figure_areas(
        gray, [{"pt": [10.0, 10.0, 20.0, 20.0], "sources": ["pdf-image"]}],
        scale=1.0, margin_pt=2.0)
    check("图形区涂白", int(masked[10:21, 10:21].min()) == 255)
    check("掩膜外不动", int(masked[0, 0]) == 7 and int(masked[50, 50]) == 7)

    # 7) 300dpi 裁片：page_obj.render(scale) 返回高分辨率页图，裁片按 pt 映射。
    class FakeHi:
        def __init__(self, image):
            self._image = image

        def to_pil(self):
            return self._image

    class FakeRenderPage(FakePage):
        def render(self, scale=1.0):
            return FakeHi(Image.new("RGB", (420, 600), "white"))

    page_obj = FakeRenderPage()
    native = [{"pt": [10.0, 10.0, 50.0, 50.0], "sources": ["pdf-image"]}]
    entries = layout.crop_native_figures(page_obj, native, 100.0, 150.0, 150, True)
    check("300dpi 原生裁片生成", len(entries) == 1 and "png_base64" in entries[0])
    if entries:
        check("裁片 pt→px 基准正确",
              entries[0]["px"] == [21, 21, 104, 104], f"实得 {entries[0]['px']}")
        import base64
        import io
        with Image.open(io.BytesIO(base64.b64decode(entries[0]["png_base64"]))) as im:
            check("裁片按 300dpi 实际像素生成", im.size == (168, 160),
                  f"实得 {im.size}")

    # 8) 原生图与像素图重叠：原生优先，不叠两张半截图。
    pixel = [{"pt": [10.0, 10.0, 50.0, 50.0], "png_base64": "pixel", "index": 0}]
    native = [{"pt": [10.0, 10.0, 50.0, 50.0], "png_base64": "native",
               "source": "pdf-image"}]
    merged = layout.merge_native_figures(pixel, native)
    check("同位置只保留原生图", len(merged) == 1
          and merged[0]["png_base64"] == "native", f"实得 {merged}")


def test_native_text() -> None:
    """原生文字层：质量标记、字符聚行、与 OCR 行匹配。"""
    print("[7/10] ocr_pipeline_layout 原生文字层（质量 / 聚行 / 匹配）")
    layout = load_module("ocr_pipeline_layout.py")

    quality, score = layout.native_text_quality("2024 年普通高等学校招生全国统一考试")
    check("原生正文判为 text", quality == "text" and score >= 0.9,
          f"实得 {(quality, score)}")
    quality, _ = layout.native_text_quality("\ue000\ue001\ue002")
    check("私有字库字符判为 math", quality == "math", f"实得 {quality}")
    quality, _ = layout.native_text_quality("若 α+β=π，则 cos α=−1")
    check("希腊字母/数学符号判为 math", quality == "math", f"实得 {quality}")
    check("空原生行判为 empty", layout.native_text_quality("  ")[0] == "empty")

    chars = [
        {"text": "已", "x0": 10.0, "x1": 22.0, "top": 10.0, "bottom": 24.0, "size": 12.0},
        {"text": "知", "x0": 23.0, "x1": 35.0, "top": 10.5, "bottom": 24.5, "size": 12.0},
        {"text": "集", "x0": 36.0, "x1": 48.0, "top": 11.0, "bottom": 25.0, "size": 12.0},
        {"text": "合", "x0": 10.0, "x1": 22.0, "top": 34.0, "bottom": 48.0, "size": 12.0},
    ]
    lines = layout.native_lines_from_chars(chars)
    check("相邻字符聚成同一行", len(lines) == 2, f"实得 {len(lines)} 行")
    check("原生行按 y 排序并拼字", lines and lines[0]["text"] == "已知集"
          and lines[1]["text"] == "合", f"实得 {[l['text'] for l in lines]}")
    check("图形区内字符被排除",
          layout.native_lines_from_chars(chars, [{"pt": [0, 0, 60, 60]}]) == [])

    ocr = [{"top": 8.0, "bottom": 26.0, "left": 8.0, "right": 60.0,
            "_ocr": {"text": "已知集合"}}]
    native = [{"text": "已知集合", "top": 10.0, "bottom": 25.0,
               "left": 10.0, "right": 48.0, "quality": "text"}]
    info = layout.annotate_native_text(ocr, native)
    check("原生行匹配到 OCR 行", info["matched"] == 1 and info["mismatched"] == 0,
          f"实得 {info}")
    check("匹配结果挂回 OCR 行", ocr[0].get("native_text") == "已知集合"
          and ocr[0].get("native_quality") == "text")

    ocr = [{"top": 8.0, "bottom": 26.0, "left": 8.0, "right": 60.0,
            "_ocr": {"text": "完全不同的一句话"}}]
    info = layout.annotate_native_text(ocr, native)
    check("可靠原生行与 OCR 明显不同 → mismatch",
          info["mismatched"] == 1 and ocr[0].get("native_mismatch") is True,
          f"实得 {info}")
    check("mismatch 保留诊断样本",
          len(info.get("mismatch_samples") or []) == 1
          and info["mismatch_samples"][0]["native"] == "已知集合",
          f"实得 {info.get('mismatch_samples')}")

    px_lines = [{"px_top": 20.0, "px_bottom": 50.0,
                 "px_left": 20.0, "px_right": 200.0}]
    native = [{"text": "已知集合", "left": 10.0, "right": 100.0,
               "top": 10.0, "bottom": 25.0, "quality": "text"}]
    fast = layout.match_native_lines_to_pixels(px_lines, native, 2.0)
    check("原生快路：几何匹配可靠文字行", 0 in fast, f"实得 {fast}")
    native[0]["text"] = "若 $x=1$，则"
    check("原生快路：公式行不抢 OCR",
          layout.match_native_lines_to_pixels(px_lines, native, 2.0) == {})
    native[0].update(text="已知集合", quality="math")
    check("原生快路：非 text 质量不匹配",
          layout.match_native_lines_to_pixels(px_lines, native, 2.0) == {})

    style_line = {"left": 10.0, "right": 100.0, "top": 10.0, "bottom": 24.0}
    style_chars = [
        {"x0": 10, "x1": 20, "top": 10, "bottom": 24,
         "fontname": "ABCDEF+SimSun-Bold", "non_stroking_color": (0,)},
        {"x0": 20, "x1": 30, "top": 10, "bottom": 24,
         "fontname": "ABCDEF+SimSun-Bold", "non_stroking_color": (0,)},
    ]
    n = layout.annotate_line_styles([style_line], style_chars)
    check("原生行恢复字体样式", n == 1
          and style_line["style"]["bold"] is True
          and "serif" in style_line["style"]["font_family"]
          and style_line["style"]["color"] == "#000000",
          f"实得 {style_line}")


def test_exam_structure() -> None:
    """试卷结构层：跨栏阅读顺序、题号/选项、统计小数不误判、质量门。"""
    print("[9/10] exam_structure（栏序 / 题号 / 选项 / quality）")
    es = load_module("exam_structure.py")

    def line(text, left, top):
        return {"text": text, "left": left, "right": left + 80,
                "top": top, "bottom": top + 12, "height_pt": 12,
                "size_pt": 10, "tables": []}

    page = {
        "page": 1, "width": 1000, "height": 800,
        "lines": [
            line("一、单选题", 50, 50),
            line("1. 第一题", 50, 80),
            line("(A) 1", 70, 100),
            line("(B) 2", 70, 112),
            line("(C) 3", 70, 124),
            line("(D) 4", 70, 136),
            line("2. 右栏题", 500, 70),
            line("(A) 3", 520, 90),
            line("(B) 4", 520, 102),
            line("(C) 5", 520, 114),
            line("(D) 6", 520, 126),
            line("15.2 18.8 20.2", 50, 150),
            line("3. 左栏第二题", 50, 170),
            line("(A) 5", 70, 190),
            line("(B) 6", 70, 202),
            line("(C) 7", 70, 214),
            line("(D) 8", 70, 226),
        ],
        "figures": [{"id": "p1-f1", "pt": [480, 60, 560, 130],
                     "source": "raster-projection"}],
    }
    layout = {"document": {"title": "合成卷"}, "pages": [page]}
    exam = es.build_exam(layout)
    check("栏序：左栏整栏先于右栏", [q["number"] for q in exam["questions"]] == [1, 3, 2],
          f"实得 {[q['number'] for q in exam['questions']]}")
    check("统计小数不当成题号", len(exam["questions"]) == 3,
          f"实得 {len(exam['questions'])}")
    check("选项拆分", [o["label"] for o in exam["questions"][0]["options"]]
          == ["A", "B", "C", "D"])
    right_q = next(q for q in exam["questions"] if q["number"] == 2)
    check("图归属右栏题", [f["ref"] for f in right_q["figures"]] == ["p1-f1"])
    quality = es.build_quality(layout, exam)
    check("质量门通过", quality["summary"]["status"] == "pass",
          str(quality["summary"]))


def test_layout_regions() -> None:
    """T1 区域层：行 / 公式 / 表格 / 图各成一等区域，双向回链且幂等。"""
    print("[12/12] 区域层（T1：区域 / 回链 / 幂等）")
    lr = load_module("layout_regions.py")

    table = {"id": "p1-t1", "page": 1, "source": "pdf-table-cells",
             "bbox_pt": [50, 180, 400, 260], "rows": 2, "cols": 2,
             "headers": ["甲"], "rows_data": [["1"]], "text_available": True}
    page = {
        "page": 1, "width": 600, "height": 800,
        "lines": [
            {"text": "一、单选题", "left": 50, "right": 140, "top": 50,
             "bottom": 62, "height_pt": 12, "size_pt": 10, "tables": []},
            {"text": "1. 已知 $x^2$ 则", "left": 50, "right": 300, "top": 80,
             "bottom": 92, "height_pt": 12, "size_pt": 10,
             "content_type": "formula",
             "style": {"font_family": "serif", "bold": True}, "tables": []},
            {"text": "（表格）", "left": 50, "right": 400, "top": 180,
             "bottom": 260, "height_pt": 12, "size_pt": 10,
             "tables": [table]},
            {"text": "(A) 1", "left": 70, "right": 140, "top": 300,
             "bottom": 312, "height_pt": 12, "size_pt": 10, "tables": []},
            {"text": "(B) 2", "left": 70, "right": 140, "top": 320,
             "bottom": 332, "height_pt": 12, "size_pt": 10, "tables": []},
        ],
        "tables": [table],
        "figures": [{"id": "p1-f1", "pt": [120, 400, 420, 700],
                     "source": "pdf-vector+labels"}],
    }
    regions = lr.build_regions(page, 1)
    kinds = [r["content_type"] for r in regions]
    check("区域类型齐全（text/formula/table/figure）",
          set(kinds) == {"text", "formula", "table", "figure"}, f"实得 {kinds}")
    check("区域 id 唯一且前缀稳定",
          len({r["id"] for r in regions}) == len(regions)
          and all(r["id"].startswith("p1-r") for r in regions))
    check("行 ↔ 区域双向一致",
          all(ln.get("region_id") in {r["id"] for r in regions}
              for ln in page["lines"]))
    check("表格区域引用原生表",
          any(r["refs"].get("table_id") == "p1-t1" for r in regions))
    check("图源规范化（pdf-vector+labels → pdf-vector）",
          any(r["content_type"] == "figure" and r["source"] == "pdf-vector"
              for r in regions))
    problems = lr.validate_regions([page])
    check("区域校验干净", problems == [], str(problems[:2]))
    check("confidence 在 0–1",
          all(0.0 <= r["confidence"] <= 1.0 for r in regions))

    legacy = {"document": {}, "pages": [{
        "page": 1, "width": 600, "height": 800, "tables": [], "figures": [],
        "lines": [{"text": "1. 题", "left": 10, "right": 100, "top": 10,
                   "bottom": 22, "height_pt": 12, "size_pt": 10,
                   "tables": []}],
    }]}
    first = lr.ensure_regions(legacy)
    second = lr.ensure_regions(legacy)
    check("ensure_regions 幂等（旧产物派生后不再重复）",
          first > 0 and second == 0, f"实得 {first}/{second}")


def test_formula_review() -> None:
    """T2 公式复核纯函数：区域映射 / 等价归一化 / 证据块 / 校验。"""
    print("[13/13] 公式复核（T2：裁片映射 / 归一化 / 证据 / 校验）")
    fr = load_module("formula_review.py")

    segs = fr.extract_math_segments(r"11. 函数 \(f(x)=1/x\) 的定义域是 \(x>0\)。")
    check("抽取 \\(...\\) 公式片段", segs == ["f(x)=1/x", "x>0"], repr(segs))
    dollars = fr.extract_math_segments(r"已知 $a+b=1$，求 $ab$ 最大值")
    check("抽取 $...$ 公式片段", dollars == ["a+b=1", "ab"], repr(dollars))

    cases = [
        (r"x^{2}+y_{1}", r"x^2 + y_1"),
        (r"f(x)=\left\{\begin{aligned}&x,&x>0\\&-x,&x<0\end{aligned}\right.",
         r"f(x)=\begin{cases}x,&x>0\\-x,&x<0\end{cases}"),
        (r"a\geqslant b", r"a\geq b"),
        (r"\sqrt{3}x", r"\sqrt3 x"),
        (r"A\mathrm{~-~}C", r"ABC"[:1] + "-" + "C"),
        # 粗体只是排版强调：模型给 \boldsymbol/\pmb，现有 OCR 丢粗体，内容仍相同。
        (r"\boldsymbol{m}=(1,0,-1)", r"m=(1,0,-1)"),
        (r"\pmb{a}\cdot\pmb{b}=0", r"a\cdot b=0"),
        # 导数记号：f^{\prime} / f^\prime / f^{'} 与 f' 等价。
        (r"f^{\prime}(x)=0", r"f'(x)=0"),
        (r"f^{\prime\prime}(x)", r"f''(x)"),
        # 模型会把裁片边缘标点单独包成 \text{;}，下标也可能被套一层花括号；
        # 平行线与上划线也有多种等价写法。
        (r"a_{_{n}}>a_{_{n-1}}\text{;}", r"a_n>a_{n-1}"),
        (r"a\parallel b", r"a // b"),
        (r"z\cdot\stackrel{-}{z}", r"z\cdot\overline{z}"),
    ]
    for left, right in cases:
        check(f"等价归一化：{left[:16]}…",
              fr.normalize_latex(left) == fr.normalize_latex(right),
              f"{fr.normalize_latex(left)!r} != {fr.normalize_latex(right)!r}")

    agreement, matched = fr.classify_agreement(r"\frac{\pi}{3}", [r"\frac{\pi}{3}"])
    check("完全一致 -> exact", agreement == "exact" and matched, agreement)
    agreement, _ = fr.classify_agreement(r"x^{2}", [r"x^2"])
    check("写法差异 -> normalized", agreement == "normalized", agreement)
    agreement, _ = fr.classify_agreement(r"x+1", [r"x-1"])
    check("内容不同 -> differs", agreement == "differs", agreement)
    agreement, _ = fr.classify_agreement(r"-x)-f(x)=0", [r"f(-x) - f(x) = 0"])
    check("裁片只覆盖片段 -> partial", agreement == "partial", agreement)
    agreement, _ = fr.classify_agreement(r"x+1", [])
    check("没有现成片段 -> no_current", agreement == "no_current", agreement)

    regions = [
        {"id": "p1-r1", "content_type": "formula", "bbox_pt": [50, 100, 250, 130]},
        {"id": "p1-r2", "content_type": "text", "bbox_pt": [50, 200, 250, 230]},
    ]
    check("中心命中 -> 映射到公式区域",
          fr.map_box_to_region([60, 105, 120, 125], regions) == "p1-r1")
    check("非公式区域不参与映射",
          fr.map_box_to_region([60, 205, 120, 225], regions) is None)
    check("框完全在区域外 -> None",
          fr.map_box_to_region([400, 400, 420, 420], regions) is None)

    evidence = fr.build_evidence(r"x^{2}", [60, 105, 120, 125], 300, "pd-doclayout",
                                 0.9, [r"x^2"])
    check("证据块字段齐全",
          evidence["source"] == fr.EVIDENCE_SOURCE
          and evidence["crop"]["dpi"] == 300
          and evidence["compare"]["agreement"] == "normalized"
          and not evidence["review"]["needed"],
          repr(evidence)[:120])

    page = {"page": 1, "regions": [
        dict(regions[0], text=r"函数 \(x^2\) 的值域"),
        dict(regions[1]),
    ]}
    report = fr.attach_evidence(page, [dict(evidence, region_id="p1-r1"),
                                       dict(evidence, region_id="p1-r2")],
                               [{"bbox_pt": [400, 400, 420, 420], "score": 0.5}])
    check("只挂公式区域，正文文本不动",
          report == {"attached": 1, "unassigned": 1}
          and page["regions"][0]["formulas"][0]["latex"] == r"x^{2}"
          and page["regions"][0]["text"] == r"函数 \(x^2\) 的值域"
          and "formulas" not in page["regions"][1],
          repr(report))
    check("未归属公式框留在页上",
          len(page.get("unassigned_formula_boxes") or []) == 1)

    summary = fr.review_summary([page])
    check("复核统计",
          summary["formulas"] == 1 and summary["unassigned_boxes"] == 1
          and summary["agreement"]["normalized"] == 1, repr(summary))
    check("证据结构校验通过", fr.validate_formula_evidence([page]) == [],
          repr(fr.validate_formula_evidence([page]))[:120])

    broken = json.loads(json.dumps(page))
    broken["regions"][0]["formulas"][0]["crop"]["dpi"] = 72
    broken["regions"][0]["formulas"][0]["compare"]["agreement"] = "maybe"
    codes = {p["code"] for p in fr.validate_formula_evidence([broken])}
    check("非法 dpi / agreement 被抓出",
          {"formula_dpi", "formula_agreement"} <= codes, repr(codes))


def test_table_review() -> None:
    """T3 表格复核纯函数：HTML 解析 / 逐格比较 / 映射 / 证据 / 校验。"""
    print("[14/14] 表格复核（T3：解析 / 逐格一致 / 证据 / 校验）")
    tr = load_module("table_review.py")

    html = ("<html><body><table><tr><td>年份</td><td>2015</td></tr>"
            "<tr><td>产量（万台）</td><td>3.3</td></tr></table></body></html>")
    rows = tr.parse_html_table(html)
    check("解析表格 HTML", rows == [["年份", "2015"], ["产量（万台）", "3.3"]], repr(rows))
    check("非表格 HTML 返回空", tr.parse_html_table("<p>x</p>") == [])
    check("单元格归一化：全角括号/空格/尾标点",
          tr.normalize_cell(" 产量 ( 万台 ） ") == tr.normalize_cell("产量(万台)"))

    current = [["年份", "2015"], ["产量（万台）", "3.3"]]
    exact = tr.compare_tables(current, [["年份", "2015"], ["产量(万台)", "3.3"]])
    check("逐格全等 -> exact",
          exact["agreement"] == "exact" and exact["matched"] == 4 and exact["differing"] == 0,
          repr(exact))
    mismatch = tr.compare_tables(current, [["年份", "2015"], ["产量(万台)", "3.8"]])
    check("有格子不同 -> cell_mismatch",
          mismatch["agreement"] == "cell_mismatch" and mismatch["differing"] == 1
          and mismatch["samples"][0]["col"] == 1, repr(mismatch)[:160])
    structure = tr.compare_tables(current, [["年份", "2015"]])
    check("行列数不同 -> structure_differs",
          structure["agreement"] == "structure_differs"
          and structure["only_current"] == 2, repr(structure)[:160])
    check("两边都空 -> no_current",
          tr.compare_tables([], [])["agreement"] == "no_current")
    check("空单元格计填充率",
          tr.compare_tables([["a", ""]], [["a", "b"]])["fill_rate"] == {"current": 0.5, "model": 1.0},
          repr(tr.compare_tables([["a", ""]], [["a", "b"]])["fill_rate"]))

    tables = [
        {"id": "p1-t1", "bbox_pt": [50, 100, 250, 160]},
        {"id": "p1-t2", "bbox_pt": [50, 300, 250, 360]},
    ]
    check("中心命中 -> 映射到表格", tr.map_box_to_table([60, 105, 120, 130], tables) == "p1-t1")
    check("框在表格外 -> None", tr.map_box_to_table([400, 400, 420, 420], tables) is None)
    check("大面积覆盖也算命中",
          tr.map_box_to_table([45, 95, 255, 165], tables) == "p1-t1")

    evidence = tr.build_table_evidence(
        html, [{"text": "年份", "bbox_pt": [1, 2, 3, 4], "score": 0.9}],
        [50, 100, 250, 160], 200, structure_model="SLANet_plus", text_model="PP-OCRv5_server_rec",
        score=0.9, current_rows=current,
    )
    check("证据块字段齐全",
          evidence["source"] == tr.TABLE_EVIDENCE_SOURCE
          and evidence["crop"]["dpi"] == 200
          and evidence["compare"]["agreement"] == "exact"
          and not evidence["review"]["needed"], repr(evidence)[:140])

    page = {"page": 1, "tables": [dict(tables[0], headers=["年份", "2015"])]}
    report = tr.attach_table_evidence(page, [dict(evidence, table_id="p1-t1"),
                                             dict(evidence, table_id="p9-t9")],
                                      [{"bbox_pt": [400, 400, 420, 420]}])
    check("只挂到对应表格、正文不动",
          report == {"attached": 1, "unassigned": 2}
          and page["tables"][0]["reviews"][0]["html"] == html
          and page["tables"][0]["headers"] == ["年份", "2015"], repr(report))
    check("未归属表格框留在页上",
          len(page.get("unassigned_table_boxes") or []) == 2)

    summary = tr.table_review_summary([page])
    check("复核统计",
          summary["tables"] == 1 and summary["tables_reviewed"] == 1
          and summary["blocks"] == 1 and summary["agreement"]["exact"] == 1
          and summary["unassigned_boxes"] == 2, repr(summary))
    check("证据结构校验通过", tr.validate_table_evidence([page]) == [],
          repr(tr.validate_table_evidence([page]))[:120])

    broken = json.loads(json.dumps(page))
    broken["tables"][0]["reviews"][0]["crop"]["dpi"] = 72
    broken["tables"][0]["reviews"][0]["compare"]["agreement"] = "maybe"
    broken["tables"][0]["reviews"][0]["source"] = "pp-structurev3"
    codes = {p["code"] for p in tr.validate_table_evidence([broken])}
    check("非法 dpi / agreement / source 被抓出",
          {"review_dpi", "review_agreement", "review_source"} <= codes, repr(codes))


def test_page_coverage() -> None:
    """页面覆盖审计：漏行/未识别图像必须在 quality 里留下证据。"""
    print("[10/10] ocr_pipeline_layout 页面覆盖审计")
    import numpy as np

    layout = load_module("ocr_pipeline_layout.py")
    gray = np.full((100, 100), 255, dtype=np.uint8)
    gray[20:40, 10:80] = 0       # 已检测行
    gray[70:90, 10:80] = 0       # 故意漏掉的一行
    line = {"px_left": 8, "px_top": 18, "px_right": 82, "px_bottom": 42,
            "left": 8, "top": 18, "right": 82, "bottom": 42}
    cov = layout.audit_page_coverage(gray, [line], [], 1.0, 12)
    check("未覆盖行被检出", bool(cov["uncovered_regions"]),
          str(cov["uncovered_regions"]))
    check("覆盖率小于 1", cov["ratio"] < 1.0, str(cov["ratio"]))

    covered = layout.audit_page_coverage(
        gray, [line, {"px_left": 8, "px_top": 68, "px_right": 82,
                      "px_bottom": 92}], [], 1.0, 12)
    check("覆盖完整时不误报", covered["uncovered_regions"] == [],
          str(covered["uncovered_regions"]))

    blank = np.full((100, 100), 255, dtype=np.uint8)
    info = layout.classify_page(blank, [], [], [], 100, 100)
    check("空白页分类", info["kind"] == "blank", str(info))
    check("空白页覆盖率为 1", layout.audit_page_coverage(
        blank, [], [], 1.0, 12)["ratio"] == 1.0)

    scanned = np.full((100, 160), 255, dtype=np.uint8)
    scanned[20:80, 20:120] = 0
    no_lines = layout.classify_page(scanned, [], [], [], 160, 100)
    check("有墨但无行分类", no_lines["kind"] == "no-lines", str(no_lines))
    check("横向页方向", no_lines["orientation"] == "landscape", str(no_lines))
    check("无文字层标记", no_lines["has_text_layer"] is False, str(no_lines))


def test_formula_quality() -> None:
    """公式结构：括号、left/right、环境必须成对；不静默改文本。"""
    print("[11/11] formula_quality（定界符 / 括号 / 环境）")
    fq = load_module("formula_quality.py")

    good = fq.extract_formulas(
        r"已知 \(x^2+\frac{1}{2}\) 且 \(\left(a+b\right)\)",
        engine="llama.cpp/paddleocr-vl")
    check("抽取两条公式", len(good) == 2, str(good))
    check("合法公式无 issue", good and fq.latex_issues(good[0]["body"]) == [],
          str(fq.latex_issues(good[0]["body"]) if good else "no formula"))
    check("左括号不配对", "unbalanced_braces" in fq.latex_issues(r"\frac{x}{2"),
          str(fq.latex_issues(r"\frac{x}{2")))
    check("left 不配对", "unbalanced_left_right" in fq.latex_issues(r"\left( x"),
          str(fq.latex_issues(r"\left( x")))
    check("环境不配对", "unbalanced_environment" in fq.latex_issues(r"\begin{cases} x"),
          str(fq.latex_issues(r"\begin{cases} x")))
    check("空公式", "empty" in fq.latex_issues(" "), str(fq.latex_issues(" ")))

    audit = fq.audit_formulas([
        {"id": "p1-l1", "text": r"\(x^2\)"},
        {"id": "p1-l2", "text": r"\(\frac{x}{2\)"},
    ], engine="llama.cpp/paddleocr-vl", page_no=1)
    check("审计计数", audit["count"] == 2 and audit["broken"] == 1,
          str(audit))
    check("审计失败即 ok=False", audit["ok"] is False, str(audit))
    check("失败样本带行号", audit["samples"] and audit["samples"][0]["line_id"] == "p1-l2",
          str(audit["samples"]))


def test_output_path_collision() -> None:
    """认不出的引擎必须单独命名，不能覆盖 OvisOCR2 的产物。

    事故形态：早先 `layout_json_for` 是 `"paddle" in model else ovisocr2`，
    于是 `--model qwen3-vl:8b` 会静默写进 `out/paper.layout.json`——把 OvisOCR2 的
    产物换成了另一个引擎的结果，而 `run_all_checks.py` 还在校验那份文件，
    校验照旧"通过"。这类覆盖没有任何报错，只能靠断言锁住。
    """
    print("[8/10] 产物路径：不同引擎不得互相覆盖")
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
    test_native_figures()
    test_native_text()
    test_exam_structure()
    test_layout_regions()
    test_formula_review()
    test_table_review()
    test_page_coverage()
    test_formula_quality()
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
