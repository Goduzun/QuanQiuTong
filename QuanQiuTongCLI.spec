# -*- mode: python ; coding: utf-8 -*-
# 全球通（QuanQiuTong）—— 命令行/诊断版（控制台程序，不含 PySide6）
# 用法：pyinstaller QuanQiuTongCLI.spec --noconfirm --clean
# 产物：dist/QuanQiuTongCLI/QuanQiuTongCLI.exe
#
# 适用场景：
#   QuanQiuTongCLI.exe --cli                # 无界面实时翻译，输出到终端
#   QuanQiuTongCLI.exe --diagnose 日志文件  # 本地日志诊断（只分析不上传）
#   QuanQiuTongCLI.exe --reply "中文" --to en
#   QuanQiuTongCLI.exe --version
#
# 注意：本 spec 面向 Windows 构建（.exe）。在 Linux/macOS 上执行仅用于
# 验证打包流程，产物为对应平台的可执行文件，不能直接在 Windows 运行。

from pathlib import Path

SPEC_DIR = Path(SPECPATH)
ICON_ICO = SPEC_DIR / "assets" / "icon.ico"
VERSION_FILE = SPEC_DIR / "scripts" / "version_info.txt"

EXCLUDES = [
    "tkinter",
    "PySide6",  # CLI 版不需要图形界面
    "numpy",
    "pandas",
    "matplotlib",
    "pytest",
    "IPython",
]

a = Analysis(
    ["app.py"],
    pathex=[str(SPEC_DIR), str(SPEC_DIR / "src")],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="QuanQiuTongCLI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,  # 控制台程序：保留标准输出
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_ICO) if ICON_ICO.is_file() else None,
    version=str(VERSION_FILE) if VERSION_FILE.is_file() else None,
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="QuanQiuTongCLI",
)
