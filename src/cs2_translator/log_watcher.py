"""只读增量日志监听器。

- 只读方式打开文件，绝不修改日志内容；
- 增量读取（记录字节偏移），不每轮重读全文件；
- 支持 UTF-8 / UTF-8 BOM / GBK 等编码回退；
- 支持 CRLF 与 LF；
- 支持日志暂不存在、游戏重启、日志被清空/截断、文件被删除后重建；
- 默认从日志末尾开始（只处理启动后的新消息），可通过 read_from_start 读取历史。

Windows 共享模式说明：
Python 内置 ``open()`` 在 Windows 上不设置 ``FILE_SHARE_DELETE``，因此只要本程序
打开着 ``console.log``，游戏/Steam 就无法删除或轮转该文件（表现为 WinError 32）。
CS2 在每次启动时会重建 console.log，所以这里改用 ``CreateFileW`` 以
``FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE`` 打开，
既保持只读，又不妨碍游戏侧删除/重建日志。
"""

from __future__ import annotations

import logging
import os
import sys
import threading

logger = logging.getLogger("cs2_translator.log_watcher")

_MAX_READ_BYTES = 1 << 20  # 单轮最多读取 1MB

# ---- Windows 共享模式只读打开 ----
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_FILE_SHARE_DELETE = 0x00000004
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x00000080
_INVALID_HANDLE_VALUE = -1


def open_read_shared(path: str):
    """以只读 + 允许删除/写入共享的方式打开文件。

    Windows 上使用 ``CreateFileW``（带 ``FILE_SHARE_DELETE``），
    其他平台回退到普通 ``open(path, "rb")``。
    """
    if not sys.platform.startswith("win"):
        return open(path, "rb")
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    CreateFileW = kernel32.CreateFileW
    CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
    ]
    CreateFileW.restype = wintypes.HANDLE

    # CloseHandle 必须声明参数类型，否则 64 位 HANDLE 会溢出
    CloseHandle = kernel32.CloseHandle
    CloseHandle.argtypes = [wintypes.HANDLE]
    CloseHandle.restype = wintypes.BOOL

    handle = CreateFileW(
        str(path),
        _GENERIC_READ,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_EXISTING,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if not handle or handle == wintypes.HANDLE(_INVALID_HANDLE_VALUE).value:
        err = ctypes.get_last_error()
        if err == 2:  # ERROR_FILE_NOT_FOUND
            raise FileNotFoundError(err, os.strerror(err), str(path))
        raise OSError(err or 1, f"CreateFileW 失败: {ctypes.FormatError(err)}", str(path))

    import msvcrt

    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError as exc:
        # 句柄无法转换为 fd（例如文件在打开后被删除）：按"文件不存在"处理，
        # 让上层走 waiting / 重新打开的路径，而不是反复报错。
        try:
            CloseHandle(handle)
        except Exception:  # noqa: BLE001
            pass
        raise FileNotFoundError(getattr(exc, "errno", 2), str(exc), str(path)) from exc
    return os.fdopen(fd, "rb")


def decode_line(raw: bytes) -> str:
    """按 UTF-8 -> GBK -> Latin-1 顺序回退解码。"""
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class LogWatcher(threading.Thread):
    """轮询式增量日志监听线程。"""

    def __init__(
        self,
        path,
        poll_interval: float = 0.25,
        read_from_start: bool = False,
        on_line=None,
        on_event=None,
        max_line_bytes: int = 65536,
    ):
        super().__init__(name="log-watcher", daemon=True)
        self.path = str(path)
        self.poll_interval = max(0.02, float(poll_interval))
        self.read_from_start = bool(read_from_start)
        self._on_line = on_line
        self._on_event = on_event
        self.max_line_bytes = max_line_bytes

        self._stop_event = threading.Event()
        self._fh = None
        self._file_id = None
        self._offset = 0
        self._byte_buffer = b""
        self._bom_checked = False
        self._waiting_emitted = False
        self._force_from_start_next = False

        # 统计
        self.stats = {
            "lines": 0,
            "bytes_read": 0,
            "empty_lines": 0,
            "overlong_lines": 0,
            "truncations": 0,
            "recreates": 0,
            "errors": 0,
        }

    # ------------------------------------------------------------------
    @property
    def current_offset(self) -> int:
        return self._offset

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=timeout)
        self._close_file()

    # ------------------------------------------------------------------
    def run(self) -> None:
        self._emit_event({"type": "started", "path": self.path})
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception as exc:  # 监听错误不应导致线程退出
                self.stats["errors"] += 1
                self._emit_event({"type": "error", "stage": "poll", "error": str(exc)})
                logger.exception("日志监听话轮出错")
                self._stop_event.wait(min(self.poll_interval * 4, 2.0))
            self._stop_event.wait(self.poll_interval)
        self._close_file()
        self._emit_event({"type": "stopped", "stats": dict(self.stats)})

    # ------------------------------------------------------------------
    def _emit_event(self, event: dict) -> None:
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:
                logger.exception("日志事件回调出错")

    def _emit_line(self, text: str) -> None:
        if self._on_line:
            try:
                self._on_line(text)
            except Exception:
                logger.exception("日志行回调出错")

    def _poll_once(self) -> None:
        if self._fh is None:
            self._try_open()
            return

        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            self._close_file()
            self._force_from_start_next = True  # 文件重建后从头读取新内容
            self._emit_event({"type": "missing", "path": self.path})
            return
        except OSError as exc:
            self.stats["errors"] += 1
            self._emit_event({"type": "error", "stage": "stat", "error": str(exc)})
            return

        file_id = (st.st_dev, st.st_ino)
        if self._file_id is not None and file_id != self._file_id:
            # 文件被删除后重建（inode 变化）：日志轮换/重建
            self.stats["recreates"] += 1
            self._close_file()
            self._emit_event({"type": "recreated", "path": self.path})
            self._try_open(from_start=True)  # 新文件从头读取
            return
        size = st.st_size
        if size < self._offset:
            # 日志被清空或截断：从头读取新内容
            self.stats["truncations"] += 1
            self._offset = 0
            self._byte_buffer = b""
            self._bom_checked = False
            try:
                self._fh.seek(0)
            except OSError:
                self._close_file()
                return
            self._emit_event({"type": "truncated", "path": self.path})
            return

        if size > self._offset:
            to_read = min(size - self._offset, _MAX_READ_BYTES)
            try:
                self._fh.seek(self._offset)
                chunk = self._fh.read(to_read)
            except OSError as exc:
                self.stats["errors"] += 1
                self._emit_event({"type": "error", "stage": "read", "error": str(exc)})
                return
            if not chunk:
                return
            self._offset += len(chunk)
            self.stats["bytes_read"] += len(chunk)
            self._process_chunk(chunk)

    # ------------------------------------------------------------------
    def _try_open(self, from_start: bool = False) -> None:
        try:
            # 只读 + 共享删除/写入：不阻止游戏侧重建或轮转日志
            fh = open_read_shared(self.path)
        except FileNotFoundError:
            if not self._waiting_emitted:
                self._waiting_emitted = True
                self._emit_event({"type": "waiting", "path": self.path})
            # 日志尚不存在：一旦出现（游戏首次启动），从头读取新内容
            self._force_from_start_next = True
            return
        except OSError as exc:
            self.stats["errors"] += 1
            self._emit_event({"type": "error", "stage": "open", "error": str(exc)})
            return

        self._waiting_emitted = False
        try:
            st = os.fstat(fh.fileno())
        except OSError:
            fh.close()
            return
        from_start = self.read_from_start or self._force_from_start_next
        self._force_from_start_next = False
        self._fh = fh
        self._file_id = (st.st_dev, st.st_ino)
        self._offset = 0 if from_start else st.st_size
        self._byte_buffer = b""
        self._bom_checked = False
        self._emit_event(
            {
                "type": "opened",
                "path": self.path,
                "size": st.st_size,
                "from_start": from_start,
            }
        )

    def _close_file(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
        self._fh = None
        self._file_id = None
        self._offset = 0
        self._byte_buffer = b""

    # ------------------------------------------------------------------
    def _process_chunk(self, chunk: bytes) -> None:
        self._byte_buffer += chunk
        while True:
            idx = self._byte_buffer.find(b"\n")
            if idx < 0:
                break
            raw_line = self._byte_buffer[:idx]
            self._byte_buffer = self._byte_buffer[idx + 1:]
            self._handle_line_bytes(raw_line)

        if len(self._byte_buffer) > self.max_line_bytes:
            # 超长且无换行的内容：丢弃并计数，防止内存无限增长
            self.stats["overlong_lines"] += 1
            self._emit_event({"type": "overlong_line", "bytes": len(self._byte_buffer)})
            self._byte_buffer = b""

    def _handle_line_bytes(self, raw: bytes) -> None:
        raw = raw.rstrip(b"\r")
        if not self._bom_checked:
            self._bom_checked = True
            if raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
        if not raw.strip():
            self.stats["empty_lines"] += 1
            return
        text = decode_line(raw)
        self.stats["lines"] += 1
        self._emit_line(text)
