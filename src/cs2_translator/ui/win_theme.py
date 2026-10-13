"""Windows 原生窗口外观：让系统标题栏跟随深色主题。

背景：Qt 的 QSS 只能绘制窗口「内部」的内容，Windows 的原生标题栏
（那条带最小化/最大化/关闭按钮的横条）由系统绘制，默认永远是浅色，
在深色界面顶部会显得非常突兀。

这里通过 DWM 的未公开属性让标题栏变深色，并同步窗口边框与文字色。
全部调用都做了容错：非 Windows、系统版本较旧或调用失败时静默跳过，
不影响程序运行。
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger("cs2_translator.ui.win_theme")

# DWMWA_USE_IMMERSIVE_DARK_MODE：Windows 10 1903+ 为 20，1903 之前为 19
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
# DWMWA_BORDER_COLOR (Win11 22000+)
_DWMWA_BORDER_COLOR = 34
# DWMWA_CAPTION_COLOR (Win11 22000+)：直接指定标题栏颜色
_DWMWA_CAPTION_COLOR = 35
# DWMWA_TEXT_COLOR (Win11 22000+)：标题文字颜色
_DWMWA_TEXT_COLOR = 36
# DWMWA_WINDOW_CORNER_PREFERENCE (Win11)：圆角
_DWMWA_WINDOW_CORNER_PREFERENCE = 33

_DWMWCP_ROUND = 2


def _dwm_set(hwnd: int, attribute: int, value) -> bool:
    """设置一个 DWM 窗口属性；失败返回 False（不抛异常）。"""
    try:
        import ctypes
        from ctypes import wintypes

        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        setter = dwmapi.DwmSetWindowAttribute
        setter.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        setter.restype = ctypes.c_long

        buf = ctypes.c_int(value)
        result = setter(wintypes.HWND(int(hwnd)), wintypes.DWORD(attribute),
                        ctypes.byref(buf), ctypes.sizeof(buf))
        return result == 0
    except Exception:
        return False


def _to_colorref(hex_color: str) -> int:
    """把 #RRGGBB 转成 Windows 的 COLORREF（0x00BBGGRR）。"""
    value = hex_color.lstrip("#")
    r = int(value[0:2], 16)
    g = int(value[2:4], 16)
    b = int(value[4:6], 16)
    return (b << 16) | (g << 8) | r


def apply_titlebar_theme(widget, *, dark: bool, caption_color: str | None = None,
                         text_color: str | None = None) -> bool:
    """让原生标题栏跟随主题。

    Args:
        widget: 任意 Qt 顶层窗口（需已创建原生窗口句柄）。
        dark: True 使用深色标题栏，False 使用浅色。
        caption_color: 可选的标题栏底色（#RRGGBB），仅 Windows 11 生效。
        text_color: 可选的标题文字颜色（#RRGGBB），仅 Windows 11 生效。

    Returns:
        是否至少有一个属性设置成功。
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        hwnd = int(widget.winId())
    except Exception:
        return False

    applied = False
    # 深/浅色模式（同时尝试新旧两个属性号，兼容不同 Windows 版本）
    if _dwm_set(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0):
        applied = True
    if _dwm_set(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, 1 if dark else 0):
        applied = True

    # Windows 11：直接指定标题栏与文字颜色，效果比开关更精确
    if caption_color:
        if _dwm_set(hwnd, _DWMWA_CAPTION_COLOR, _to_colorref(caption_color)):
            applied = True
    if text_color:
        if _dwm_set(hwnd, _DWMWA_TEXT_COLOR, _to_colorref(text_color)):
            applied = True

    # 圆角（Win11），与界面圆角风格一致
    _dwm_set(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)

    if not applied:
        logger.debug("系统不支持深色标题栏，跳过（不影响功能）")
    return applied
