"""前台窗口感知：判断 CS2 是否在前台，以及它是否全屏。

为什么需要：实测现场两份实例 + 没有前台感知 → 无论前台是什么窗口都在
每 250ms 读日志、每秒刷新状态栏和统计、各自持一个托盘图标。Windows 事件日志里
Display 提供程序的 4127（显示能力重新评估）在短时间内高频出现，与「一个外部
窗口在 CS2 独占全屏时持续重绘」的表现一致。

修法：前台是 cs2.exe 时降频轮询 + 暂停非必要 UI 更新（见 main_window）。

只用 Win32 只读查询，不注入、不 hook、不读游戏内存：
  GetForegroundWindow → GetWindowThreadProcessId → OpenProcess(QUERY_LIMITED_INFORMATION)
  → QueryFullProcessImageNameW → 比对可执行文件名
"""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger("cs2_translator.foreground")

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# 目标进程文件名（小写比对）
CS2_PROCESS_NAMES = ("cs2.exe",)

_IS_WIN = sys.platform.startswith("win")


# ---------------------------------------------------------------- Win32 查询
def _win32():
    """惰性导入 ctypes 与 user32/kernel32，非 Windows 返回 None。"""
    if not _IS_WIN:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
        ]
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]

        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD, wintypes.BOOL, wintypes.DWORD
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

        return {
            "ctypes": ctypes, "wintypes": wintypes,
            "user32": user32, "kernel32": kernel32,
        }
    except Exception:  # noqa: BLE001
        logger.debug("加载 Win32 失败", exc_info=True)
        return None


def foreground_pid() -> int | None:
    """当前前台窗口所属进程 PID。"""
    api = _win32()
    if api is None:
        return None
    ctypes, wintypes = api["ctypes"], api["wintypes"]
    user32 = api["user32"]
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value) or None


def process_name(pid: int) -> str:
    """按 PID 取可执行文件名（小写，不含路径）。失败返回空串。"""
    api = _win32()
    if api is None or not pid:
        return ""
    ctypes, wintypes = api["ctypes"], api["wintypes"]
    kernel32 = api["kernel32"]

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buf, ctypes.byref(size)
        ):
            return ""
        return os.path.basename(buf.value).lower()
    except Exception:  # noqa: BLE001
        return ""
    finally:
        # 必须关：不关就是句柄泄漏的来源（现场见过 1989 个句柄）
        try:
            kernel32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            pass


def is_process_foreground(names=CS2_PROCESS_NAMES) -> bool:
    """前台进程名是否在给定名单里。"""
    pid = foreground_pid()
    if not pid:
        return False
    name = process_name(pid)
    return bool(name) and name in {n.lower() for n in names}


def foreground_is_fullscreen() -> bool:
    """前台窗口是否覆盖整个显示器（用于日志/诊断，不参与决策）。"""
    api = _win32()
    if api is None:
        return False
    ctypes, wintypes = api["ctypes"], api["wintypes"]
    user32 = api["user32"]
    hwnd = user32.GetForegroundWindow()
    if not hwnd or not user32.IsWindowVisible(hwnd):
        return False

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    if not monitor:
        return False
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return False
    m = info.rcMonitor
    return (rect.left <= m.left and rect.top <= m.top
            and rect.right >= m.right and rect.bottom >= m.bottom)


# ---------------------------------------------------------------- 周期性监视
class ForegroundMonitor(QObject):
    """周期性判断目标进程是否在前台，状态变化时发信号。

    间隔默认 1.5s：足够快地在「切进游戏」时降频，又不会自己变成新的高频源。
    """

    state_changed = Signal(bool)   # True = 目标进程在前台

    def __init__(self, names=CS2_PROCESS_NAMES, interval_ms: int = 1500, parent=None):
        super().__init__(parent)
        self._names = tuple(n.lower() for n in names)
        self._active = False
        self._timer = QTimer(self)
        self._timer.setInterval(max(300, int(interval_ms)))
        self._timer.timeout.connect(self._poll)

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self) -> None:
        self._timer.start()
        self._poll()   # 立即判一次，避免启动后 1.5s 内还在全速轮询

    def stop(self) -> None:
        self._timer.stop()

    def _poll(self) -> None:
        try:
            active = is_process_foreground(self._names)
        except Exception:  # noqa: BLE001
            logger.debug("前台检测失败", exc_info=True)
            return
        if active != self._active:
            self._active = active
            logger.info("前台状态变化：目标进程在前台=%s", active)
            self.state_changed.emit(active)
