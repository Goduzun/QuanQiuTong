# -*- mode: python ; coding: utf-8 -*-
# 全球通（QuanQiuTong）—— GUI 版 onedir 打包（推荐分发形态）
# 用法：pyinstaller QuanQiuTong.spec --noconfirm --clean
# 产物：dist/QuanQiuTong/QuanQiuTong.exe
#
# 注意：本 spec 面向 Windows 构建（.exe）。在 Linux/macOS 上执行仅用于
# 验证打包流程，产物为对应平台的可执行文件，不能直接在 Windows 运行。

import sys
from pathlib import Path

SPEC_DIR = Path(SPECPATH)
ICON_ICO = SPEC_DIR / "assets" / "icon.ico"
ICON_PNG = SPEC_DIR / "assets" / "icon.png"
VERSION_FILE = SPEC_DIR / "scripts" / "version_info.txt"

# 与 CS2 游戏无关的可选 Qt 模块，排除以缩小体积
EXCLUDES = [
    "tkinter",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSerialPort",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
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
    datas=[
        (str(ICON_PNG), "assets"),
        (str(ICON_ICO), "assets"),
    ],
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
    name="QuanQiuTong",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX 可能误报病毒且非必需，默认关闭
    runtime_tmpdir=None,
    console=False,  # GUI 应用：不弹控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_ICO) if ICON_ICO.is_file() else None,
    version=str(VERSION_FILE) if VERSION_FILE.is_file() else None,
    uac_admin=False,  # 只读日志，无需管理员权限
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="QuanQiuTong",
)
