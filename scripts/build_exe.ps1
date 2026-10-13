# ============================================================
# build_exe.ps1 —— Windows EXE 一键构建（PyInstaller）
# 用法：powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
#
# 流程：
#   1. onedir 构建 GUI 版（dist\QuanQiuTong\）
#   2. 冒烟验证（--version / --diagnose）
#   3. onefile 构建 GUI 版（单文件）
#   4. onedir 构建命令行/诊断版（dist\QuanQiuTongCLI\）
#   5. 生成 SHA-256 校验文件（dist\SHA256SUMS.txt）
#
# 要求：Python 3.11+，已执行 scripts\setup_dev.ps1 安装依赖。
# 无需管理员权限（本应用只读日志，不请求提权）。
# ============================================================

$ErrorActionPreference = "Stop"

$Root = $PSScriptRoot
if (-not $Root) { $Root = Split-Path -Parent $MyInvocation.MyCommand.Path }
$ProjectDir = Split-Path -Parent $Root

Set-Location $ProjectDir

$Py = ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host "未找到虚拟环境，请先执行 scripts\setup_dev.ps1" -ForegroundColor Red
    exit 1
}

function Invoke-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "==> $Message" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
}

# ---------- 1. onedir GUI 构建 ----------
Invoke-Step "步骤 1/5：onedir 构建 GUI 版"
& $Py -m PyInstaller QuanQiuTong.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { Write-Host "onedir 构建失败" -ForegroundColor Red; exit 1 }

$GuiExe = "dist\QuanQiuTong\QuanQiuTong.exe"
if (-not (Test-Path $GuiExe)) {
    Write-Host "未找到预期产物 $GuiExe" -ForegroundColor Red
    exit 1
}
Write-Host "产物: $GuiExe" -ForegroundColor Green

# ---------- 2. 冒烟验证 ----------
Invoke-Step "步骤 2/5：冒烟验证（--version）"
& $GuiExe --version
if ($LASTEXITCODE -ne 0) { Write-Host "冒烟验证失败：--version 返回非零" -ForegroundColor Red; exit 1 }

# ---------- 3. onefile GUI 构建 ----------
Invoke-Step "步骤 3/5：onefile 构建 GUI 版（单文件）"
& $Py -m PyInstaller QuanQiuTong-onefile.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { Write-Host "onefile 构建失败" -ForegroundColor Red; exit 1 }

$OneFileExe = "dist\QuanQiuTong-onefile.exe"
if (-not (Test-Path $OneFileExe)) {
    Write-Host "未找到预期产物 $OneFileExe" -ForegroundColor Red
    exit 1
}
Write-Host "产物: $OneFileExe" -ForegroundColor Green

# ---------- 4. CLI onedir 构建 ----------
Invoke-Step "步骤 4/5：构建命令行/诊断版"
& $Py -m PyInstaller QuanQiuTongCLI.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { Write-Host "CLI 构建失败" -ForegroundColor Red; exit 1 }

$CliExe = "dist\QuanQiuTongCLI\QuanQiuTongCLI.exe"
if (-not (Test-Path $CliExe)) {
    Write-Host "未找到预期产物 $CliExe" -ForegroundColor Red
    exit 1
}
Write-Host "产物: $CliExe" -ForegroundColor Green

# CLI 冒烟：诊断模式（本地只读分析，不联网）
Invoke-Step "步骤 4/5：CLI 冒烟验证（--diagnose，使用测试 fixture）"
& $CliExe --diagnose "tests\fixtures\sample_console.log"
if ($LASTEXITCODE -ne 0) {
    Write-Host "提示：--diagnose 冒烟验证返回非零，请检查上方输出。" -ForegroundColor Yellow
}

# ---------- 5. SHA-256 ----------
Invoke-Step "步骤 5/5：生成 SHA-256 校验文件"
$sums = @()
foreach ($pattern in @("dist\QuanQiuTong\*.exe", "dist\QuanQiuTong-onefile.exe", "dist\QuanQiuTongCLI\*.exe")) {
    Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | ForEach-Object {
        $hash = (Get-FileHash -Algorithm SHA256 -Path $_.FullName).Hash.ToLower()
        $rel = $_.FullName.Replace((Get-Location).Path + "\", "")
        $sums += ("$hash  $rel")
    }
}
$sums | Out-File -FilePath "dist\SHA256SUMS.txt" -Encoding utf8
Get-Content "dist\SHA256SUMS.txt"

Write-Host ""
Write-Host "============================================================" -ForegroundColor Green
Write-Host "==> 构建完成！产物位于 dist\ 目录：" -ForegroundColor Green
Write-Host "    dist\QuanQiuTong\QuanQiuTong.exe        （GUI onedir，推荐）"
Write-Host "    dist\QuanQiuTong-onefile.exe （GUI 单文件）"
Write-Host "    dist\QuanQiuTongCLI\QuanQiuTongCLI.exe           （命令行/诊断）"
Write-Host "    dist\SHA256SUMS.txt                                  （校验和）"
Write-Host ""
Write-Host "分发提示：打包整个 dist\QuanQiuTong\ 目录（onedir 形态）。" -ForegroundColor Yellow
Write-Host "本应用只读取 CS2 console.log，不注入游戏进程、不修改游戏文件。" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Green
