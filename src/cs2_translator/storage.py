"""本地存储：API 密钥安全存储（Windows DPAPI）、原子写入、可选历史记录。"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
import tempfile
import threading
from ctypes import wintypes
from pathlib import Path


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_available() -> bool:
    return sys.platform.startswith("win") and hasattr(ctypes, "windll")


def _dpapi_protect(data: bytes) -> bytes:
    """使用 Windows DPAPI (CryptProtectData) 加密，仅当前用户可解密。"""
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptProtectData 失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    blob_in = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data, len(data)), ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("CryptUnprotectData 失败")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


class SecretStore:
    """密钥存储。

    Windows 上使用 DPAPI（当前用户级加密，密钥不会以明文落盘）；
    其他平台退化为明文文件并在日志中给出警告（桌面版主要面向 Windows）。
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    @property
    def is_secure(self) -> bool:
        return _dpapi_available()

    def _read(self) -> dict:
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _write(self, data: dict) -> None:
        atomic_write_bytes(self.path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))

    def get(self, name: str):
        with self._lock:
            entry = self._read().get(name)
        if not isinstance(entry, dict):
            return None
        enc = entry.get("enc")
        payload = entry.get("data", "")
        try:
            if enc == "dpapi":
                return _dpapi_unprotect(base64.b64decode(payload)).decode("utf-8")
            if enc == "plain":
                return payload
        except Exception:
            return None
        return None

    def set(self, name: str, value: str) -> None:
        with self._lock:
            data = self._read()
            if _dpapi_available():
                data[name] = {
                    "enc": "dpapi",
                    "data": base64.b64encode(_dpapi_protect(value.encode("utf-8"))).decode("ascii"),
                }
            else:
                data[name] = {"enc": "plain", "data": value}
            self._write(data)

    def delete(self, name: str) -> None:
        with self._lock:
            data = self._read()
            if name in data:
                del data[name]
                self._write(data)


class HistoryStore:
    """可选的聊天历史保存（默认关闭，由用户在设置中开启）。"""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(line)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """先写临时文件再原子替换，避免写一半损坏配置。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_bytes(path, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))
