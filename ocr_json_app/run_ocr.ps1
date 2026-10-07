# run_ocr.ps1 —— 跑「原版式」OCR 链路的唯一入口。
#
#   .\run_ocr.ps1                      # 首选引擎 PaddleOCR-VL-1.6
#   .\run_ocr.ps1 -Engine ovisocr2     # 备选引擎
#   .\run_ocr.ps1 -Pdf data\别的卷子.pdf
#
# 一条命令做完：起本地引擎 → 逐行 OCR → 带版式坐标的 JSON → 单文件网页 → 对比页。
# 引擎端口与提示词按模型固定（PaddleOCR-VL 认官方前缀 "OCR:"，换成自然语言长提示词会让它
# 退化——实测重复输出 <br> 达 23 秒；OvisOCR2 反之），所以不需要手动传 --prompt。
param(
    [ValidateSet("paddleocr-vl", "ovisocr2")][string]$Engine = "paddleocr-vl",
    [string]$Pdf = "data\静安区2026届高三一模数学试卷.pdf",
    [int]$Dpi = 150,
    [int]$Vote = 3,
    [int]$Workers = 4
)
$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
Set-Location $PSScriptRoot

$layout = "src\ocr_pipeline_layout.py"
$json = if ($Engine -eq "paddleocr-vl") { "out\paper.layout.paddle.json" }
        else { "out\paper.layout.json" }

Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 3

$job = Start-Job -ScriptBlock {
    param($m)
    & pwsh -NoProfile -File "$HOME\.dsh-llama\start-ocr-server.ps1" -Model $m -Ctx 16384
} -ArgumentList $Engine

# 引擎端口：启动脚本按模型自动选（paddleocr-vl → 8080，ovisocr2 → 8081）
$port = if ($Engine -eq "paddleocr-vl") { 8080 } else { 8081 }
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 2
    try {
        $h = (Invoke-WebRequest "http://127.0.0.1:$port/health" -TimeoutSec 3 -UseBasicParsing).Content
        if ($h -match "ok") { Write-Host "[run] $Engine 就绪（:$port）"; $ready = $true; break }
    } catch { }
}
if (-not $ready) { Write-Host "[run] 引擎未就绪" -ForegroundColor Red; exit 2 }

# --host / --model / --prompt 都用默认值（默认即本引擎），只覆盖需要的
python $layout --pdf $Pdf --out $json --dpi $Dpi --workers $Workers --vote $Vote
if ($LASTEXITCODE -ne 0) { Write-Host "[run] OCR 失败" -ForegroundColor Red; exit $LASTEXITCODE }

python tools\build_viewers.py
python tools\build_compare.py

Stop-Job $job -ErrorAction SilentlyContinue
Remove-Job $job -Force -ErrorAction SilentlyContinue
Get-Process llama-server -ErrorAction SilentlyContinue | Stop-Process -Force
Write-Host "[run] 完成"
Write-Host "[run] 阅读器：out\viewer_layout_paddleocrvl.html（双击即可，数据内嵌）"
Write-Host "[run] 对比页：python src\ocr_pipeline_layout.py --serve-only --out out\paper.layout.paddle.json --port 8780"
