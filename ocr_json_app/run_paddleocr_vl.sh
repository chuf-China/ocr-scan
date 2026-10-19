#!/usr/bin/env bash
# run_paddleocr_vl.sh —— 用**官方 PaddleOCR-VL 流程**跑完整链路（WSL 内执行）。
#
# 为什么单独一个入口：官方流程需要一个装了 paddlepaddle-gpu 的 Linux 环境，
# 而本项目其余部分跑在 Windows 的 Python 上。这个脚本把两边串起来：
#
#   ① PDF ──pypdfium2 预渲染──> PNG        （**必须先渲染**，见下方警告）
#   ② PNG 目录 ──paddleocr doc_parser──> *_res.json + *.md
#   ③ *_res.json ──import_paddleocr_json.py──> 本项目 JSON（Windows 侧执行）
#   ④ build_viewers.py / build_compare.py ──> 阅读器与对比页
#
# ⚠️ 不要直接把 PDF 喂给 `doc_parser`：实测它会卡在 PDF→图像转换上
#    （CPU 100%、GPU 8%、4 小时零输出），而先渲染成 PNG 只要 0.5 秒/6 页，
#    改成目录输入后 GPU 立刻打满 99%。原因未深究（怀疑 /mnt/c 上的 PDF 读取），
#    但预渲染这条路已被实测证明可靠。
#
# 用法：
#   bash run_paddleocr_vl.sh [PDF路径] [输出目录]
# 环境变量：
#   PADDLE_VENV  虚拟环境路径（默认 ~/.venv_paddleocr）
#   DPI          渲染 dpi（默认 150，与本项目产物的 1241x1754 一致）
set -euo pipefail

export HOME="${HOME:-/home/chuf}"
export PATH="$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

PDF="${1:-/mnt/c/Users/25840/Desktop/ocr-scan/ocr_json_app/data/静安区2026届高三一模数学试卷.pdf}"
PROJ_WIN="/mnt/c/Users/25840/Desktop/ocr-scan/ocr_json_app"
WORK="$HOME/paddleocr_run"
VENV="${PADDLE_VENV:-$HOME/.venv_paddleocr}"
DPI="${DPI:-150}"

echo "[1/4] 预渲染 $PDF"
mkdir -p "$WORK/pages" "$WORK/raw"
rm -f "$WORK"/pages/*.png "$WORK"/raw/* 2>/dev/null || true
. "$VENV/bin/activate"
python - "$PDF" "$WORK/pages" "$DPI" <<'PY'
import sys, os
import pypdfium2 as pdfium
pdf, dst, dpi = sys.argv[1], sys.argv[2], int(sys.argv[3])
doc = pdfium.PdfDocument(pdf)
for i in range(len(doc)):
    img = doc[i].render(scale=dpi / 72).to_pil().convert("RGB")
    img.save(os.path.join(dst, f"page{i+1:02d}.png"))
print(f"  渲染 {len(doc)} 页 @ {dpi}dpi")
PY

echo "[2/4] 官方 doc_parser（PP-DocLayoutV3 + PaddleOCR-VL-1.6）"
paddleocr doc_parser -i "$WORK/pages" --save_path "$WORK/raw" --device gpu 2>&1 \
  | grep -vE 'use GQA' | tail -20

echo "[3/4] 拷回 Windows 侧并转换成本项目 JSON"
cp "$WORK"/raw/*_res.json "$PROJ_WIN/out/paddleocr_raw/" 2>/dev/null || {
  mkdir -p "$PROJ_WIN/out/paddleocr_raw"; cp "$WORK"/raw/*_res.json "$PROJ_WIN/out/paddleocr_raw/"; }
# ⚠️ 转换器要 pdfplumber 读字号，而 PATH 上的 `python` 未必装了它（本机 PATH 的
#    python 是 hermes venv，无 pip、无 pdfplumber）。所以显式指定解释器。
PY_WIN="${PY_WIN:-C:\\Users\\25840\\AppData\\Local\\Python\\pythoncore-3.14-64\\python.exe}"
OUT_JSON="out\\paper.layout.paddleocrvl16.json"
VIEW_BASE="viewer_layout_paddleocrvl16"
powershell.exe -NoProfile -Command \
  "cd '$PROJ_WIN'; & '$PY_WIN' tools\\import_paddleocr_json.py out\\paddleocr_raw --out $OUT_JSON --title '静安区2026届高三一模数学试卷'"

echo "[4/4] 生成阅读器"
powershell.exe -NoProfile -Command "cd '$PROJ_WIN'; & '$PY_WIN' tools\\build_viewers.py"
echo "[run] 完成：$PROJ_WIN/out/$VIEW_BASE.html"
