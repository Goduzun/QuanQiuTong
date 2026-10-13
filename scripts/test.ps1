# ============================================================
# test.ps1 —— 运行自动化测试（Windows / PowerShell）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\test.ps1 [-V]
#   -V   显示每个测试的详细结果（不加则只输出汇总）
# 说明：测试全部使用 Mock/本地假 API，不会消耗真实额度，也不需要网络。
# ============================================================

$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    Write-Host "未找到 .venv，请先执行 scripts\setup_dev.ps1" -ForegroundColor Red
    exit 1
}

# 无头环境运行 Qt 测试（offscreen 平台插件）
$env:QT_QPA_PLATFORM = "offscreen"

$pytestArgs = @("tests/")
if ($args.Count -gt 0 -and $args[0] -eq "-V") {
    $pytestArgs += @("-v", "--tb=short")
} else {
    $pytestArgs += @("-q", "--tb=short")
}

& ".venv\Scripts\python.exe" -m pytest @pytestArgs
$code = $LASTEXITCODE

if ($code -eq 0) {
    Write-Host ""
    Write-Host "==> 全部测试通过。" -ForegroundColor Green
} else {
    Write-Host ""
    Write-Host "==> 存在失败测试（退出码 $code），请查看上方输出。" -ForegroundColor Red
}
exit $code
