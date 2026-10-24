"""单实例保护。

为什么需要：一次会话实测到 **2 份实例同时运行**（onefile 的 bootloader + 主进程
成对出现，共 4 个进程），旧的那份跑了 2 天没退出，累计 877 秒 CPU 一直在
每 250ms 轮询 console.log，并且各自持有一个托盘图标。双份实例 = 双倍文件系统
活动 + 双倍 UI 重绘，而这两件事正是「CS2 全屏时被外部窗口反复打断」的嫌疑来源。

实现：QLocalServer / QLocalSocket（Windows 上是命名管道）。
第二个实例连接成功即把第一个实例的窗口拉到前台，然后自己退出。
"""

from __future__ import annotations

import getpass
import logging
import os

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger("cs2_translator.single_instance")

# 唤醒指令：第二个实例告诉第一个实例「把窗口显示出来」
_PING = b"show"


def instance_key(app_name: str) -> str:
    """命名管道名称。

    Windows 的命名管道是**整机**可见而不是按用户隔离的，所以把用户名拼进去，
    避免同一台机器上不同用户会话互相顶掉。
    """
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = str(os.getpid())
    safe = "".join(ch for ch in user if ch.isalnum() or ch in "-_") or "user"
    return f"{app_name}-single-{safe}"


class SingleInstanceGuard(QObject):
    """单实例守卫。

    用法::

        guard = SingleInstanceGuard(instance_key(APP_NAME))
        if not guard.try_acquire():
            return 0          # 已有实例在跑，已在 try_acquire 里唤醒了它
        ...
        guard.release()       # 退出时释放

    ``activated`` 信号：检测到第二个实例启动并请求唤醒时发出。
    """

    activated = Signal()

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self._key = key
        self._server: QLocalServer | None = None
        self._holds_lock = False

    # ---------------- 核心 ----------------
    def try_acquire(self) -> bool:
        """尝试成为唯一实例。成功返回 True；已有实例则唤醒它并返回 False。"""
        if self._notify_existing():
            logger.info("检测到已在运行的实例，已请求它显示窗口")
            return False

        # 到这里说明没有活着的实例。上次异常退出可能留下 socket 文件，
        # 先清掉再 listen，否则 listen 会因为地址占用失败。
        try:
            QLocalServer.removeServer(self._key)
        except Exception:
            logger.debug("清理残留 socket 失败（可忽略）", exc_info=True)

        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not server.listen(self._key):
            # listen 失败最常见的两个原因：残留 socket、或者刚好被抢占。
            # 两种情况都再试一次「唤醒已有实例」，成功就说明是真有实例。
            if self._notify_existing():
                logger.info("listen 失败但检测到已有实例")
                return False
            logger.warning("单实例监听失败：%s", server.errorString())
            return True  # 宁可继续运行，也不要因为单实例失败而不给用户用

        server.newConnection.connect(self._on_new_connection)
        self._server = server
        self._holds_lock = True
        logger.info("已取得单实例锁：%s", self._key)
        return True

    def release(self) -> None:
        """释放锁（退出时调用）。"""
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                logger.debug("关闭单实例服务器失败", exc_info=True)
            try:
                QLocalServer.removeServer(self._key)
            except Exception:
                logger.debug("移除单实例 endpoint 失败", exc_info=True)
            self._server = None
        self._holds_lock = False

    # ---------------- 内部 ----------------
    def _notify_existing(self) -> bool:
        """连一下已有实例；连上就把唤醒指令发过去。"""
        socket = QLocalSocket()
        socket.connectToServer(self._key)
        if not socket.waitForConnected(300):
            return False
        try:
            socket.write(_PING)
            socket.flush()
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
        except Exception:
            logger.debug("发送唤醒指令失败", exc_info=True)
        finally:
            socket.abort()
        return True

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if conn is None:
                break
            try:
                if conn.waitForReadyRead(300):
                    conn.readAll()
                conn.disconnectFromServer()
            except Exception:
                logger.debug("读取唤醒请求失败", exc_info=True)
            finally:
                conn.deleteLater()
            logger.info("收到第二个实例的唤醒请求")
            self.activated.emit()
