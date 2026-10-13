# ============================================================
# setup_dev.ps1 —— 开发环境一键搭建（Windows / PowerShell）
# 用法：在项目根目录执行  powershell -ExecutionPolicy Bypass -File scripts\setup_dev.ps1
# ============================================================

$ErrorActionPreference = "Stop"

Write-Host "==> 检查 Python 3.11+ ..." -ForegroundColor Cyan
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) {
    Write-Host "未找到 python 命令。请先安装 Python 3.11+（https://www.python.org/downloads/），" -ForegroundColor Red
    Write-Host "安装时勾选 'Add Python to PATH'。" -ForegroundColor Red
    exit 1
}
$ver = python -c "import sys; print('%d.%d' % sys.version_info[:2])"
Write-Host "    Python 版本: $ver"

Write-Host "==> 创建虚拟环境 .venv ..." -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

Write-Host "==> 安装依赖（含开发/测试/打包工具）..." -ForegroundColor Cyan
& ".venv\Scripts\python.exe" -m pip install --upgrade pip
& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
& ".venv\Scripts\python.exe" -m pip install pytest pyinstaller

Write-Host ""
Write-Host "==> 环境就绪。后续命令：" -ForegroundColor Green
Write-Host "    运行开发版:   powershell -ExecutionPolicy Bypass -File scripts\run_dev.ps1"
Write-Host "    运行测试:     powershell -ExecutionPolicy Bypass -File scripts\test.ps1"
Write-Host "    打包 EXE:     powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1"
