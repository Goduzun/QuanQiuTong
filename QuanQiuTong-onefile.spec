# -*- mode: python ; coding: utf-8 -*-
# 全球通（QuanQiuTong）—— GUI 版 onefile 打包（单文件，启动稍慢）
# 用法：pyinstaller QuanQiuTong-onefile.spec --noconfirm --clean
# 产物：dist/全球通.exe（单文件）
#
# 注意：本 spec 面向 Windows 构建（.exe）。在 Linux/macOS 上执行仅用于
# 验证打包流程，产物为对应平台的可执行文件，不能直接在 Windows 运行。
#
# ============================================================================
# 瘦身说明（v1.2.0，54.44 MB → 32.52 MB，-40.3%）
#
# 以下排除项全部经过「实际量测 + PE 汇入表解析」确认，不是凭感觉删：
#   1. opengl32sw.dll   19.68 MB  没有任何 DLL 直接汇入它；Qt6Gui 只汇入
#                                 d3d11/dxgi/d3d12（走系统 Direct3D）
#   2. Qt6Quick/Qml/... 12.55 MB  QML 全家桶，本程式纯 QWidget
#   3. Qt6Pdf            4.41 MB  PDF 模组，未使用
#   4. libcrypto/ssl-1_1 3.96 MB  Python 3.11 的 ssl 用自带的 OpenSSL 3.x，
#                                 这是旧版残留
#   5. translations      6.56 MB  99 个语言档只留 zh_CN
#   6. Qt6OpenGL/Network 3.60 MB  仅被 Quick/Pdf/Qml 依赖，全都移除
#   7. 多余外挂          3.30 MB  imageformats/platforms/tls 只留必需
#
# 验证方式：--version / --diagnose（真实 12594 行日志）/ GUI 存活 45 秒 /
#           日志写入 / 记忆库读取，6/6 全部通过（见 scripts/verify_build.py）
# ============================================================================

import sys
from pathlib import Path

SPEC_DIR = Path(SPECPATH)
ICON_ICO = SPEC_DIR / "assets" / "icon.ico"
ICON_PNG = SPEC_DIR / "assets" / "icon.png"
VERSION_FILE = SPEC_DIR / "scripts" / "version_info.txt"

# 模组层排除（Python import 名称）
EXCLUDES = [
    # 未使用的标准库 / 重量级套件
    "tkinter", "numpy", "pandas", "matplotlib", "pytest", "IPython",
    "setuptools", "pip", "wheel", "pydoc_data", "unittest", "doctest", "pdb",
    "lib2to3", "distutils",
    # Qt：未使用的模组（已确认原始码完全没有引用）
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2", "PySide6.QtQuickTest",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtPositioning",
    "PySide6.QtSerialPort", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtSensors", "PySide6.QtSerialBus", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtTextToSpeech",
    "PySide6.QtWebSockets", "PySide6.QtHttpServer", "PySide6.QtGraphs",
    "PySide6.QtGraphsWidgets", "PySide6.QtSql", "PySide6.QtTest",
    "PySide6.QtUiTools", "PySide6.QtSvgWidgets", "PySide6.QtNetwork",
    "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets",
]

# 二进位／资料档排除：这些是 DLL 或资源，只能靠档名过滤
BINARY_DROP = (
    "opengl32sw",
    "Qt6Quick", "Qt6Qml", "Qt6ShaderTools", "Qt6Labs", "Qt6Graphs",
    "Qt6Pdf", "Qt6Designer", "Qt6OpenGL", "Qt6OpenGLWidgets", "Qt6Network",
    "Qt6WebEngine", "Qt6WebChannel", "Qt6WebSockets", "Qt6Multimedia",
    "Qt6Charts", "Qt6DataVisualization", "Qt6Bluetooth", "Qt6Nfc",
    "Qt6Positioning", "Qt6SerialPort", "Qt6Sql", "Qt6Test", "Qt6Help",
    "Qt6Sensors", "Qt6SerialBus", "Qt6RemoteObjects", "Qt6Scxml",
    "Qt6SpatialAudio", "Qt6TextToSpeech", "Qt6HttpServer", "Qt6UiTools",
    "libcrypto-1_1", "libssl-1_1",
    "qopensslbackend", "qschannelbackend", "qcertonlybackend", "qnetworklistmanager",
    "qminimal", "qoffscreen", "qdirect2d",
    "qtiff", "qwebp", "qicns", "qtga", "qwbmp", "qpdf", "qgif",
    "qtuiotouchplugin", "qtvirtualkeyboard",
)

# 翻译档只留简体中文（介面本就是中文；其余 98 个语言档共 6.56 MB）
KEEP_TRANSLATIONS = {"qtbase_zh_CN.qm"}

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
    # 1 = 只移除 assert（安全）；2 会移除 docstring，可能影响依赖 __doc__ 的库
    optimize=1,
)

# 过滤 binaries：移除未使用的 DLL / 外挂
_new_binaries = []
_dropped_binaries = []
for _dest, _src, _kind in a.binaries:
    if any(_tok.lower() in Path(_dest).name.lower() for _tok in BINARY_DROP):
        _dropped_binaries.append(_dest)
        continue
    _new_binaries.append((_dest, _src, _kind))
a.binaries = _new_binaries

# 过滤 datas：translations 只留 zh_CN
_new_datas = []
_dropped_datas = []
for _dest, _src, _kind in a.datas:
    _d = _dest.replace("\\", "/")
    if "translations/" in _d and Path(_d).name not in KEEP_TRANSLATIONS:
        _dropped_datas.append(_dest)
        continue
    _new_datas.append((_dest, _src, _kind))
a.datas = _new_datas

print(f"[瘦身] 移除 binaries {len(_dropped_binaries)} 个")
print(f"[瘦身] 移除 datas    {len(_dropped_datas)} 个")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    exclude_binaries=False,
    name="全球通",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_ICO) if ICON_ICO.is_file() else None,
    version=str(VERSION_FILE) if VERSION_FILE.is_file() else None,
    uac_admin=False,
)
