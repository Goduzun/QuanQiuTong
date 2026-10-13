# ============================================================
# run_dev.ps1 —— 以源码方式启动应用（Windows / PowerShell）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\run_dev.ps1 [-Cli]
#   -Cli    以无界面命令行模式运行（不加则启动桌面界面）
# ============================================================

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    Write-Host "未找到 .venv，请先执行 scripts\setup_dev.ps1" -ForegroundColor Red
    exit 1
}

$argsList = @()
if ($args.Count -gt 0 -and $args[0] -eq "-Cli") {
    $argsList += "--cli"
}

& ".venv\Scripts\python.exe" app.py @argsList
