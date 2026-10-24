"""GUI 与引擎之间的桥接层：把引擎回调转换为 Qt 信号（线程安全）。"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QObject, Signal

from ..engine import TranslationEngine

logger = logging.getLogger("cs2_translator.ui.workers")


class EngineSignals(QObject):
    chat = Signal(object)                                              # ChatMessage
    translation = Signal(object, object, float, object, bool, dict)    # msg, 译文, 耗时, 错误, 跳过, meta
    status = Signal(dict)
    log_event = Signal(dict)
    reply_result = Signal(dict)
    api_test_done = Signal(bool, str)
    log_test_done = Signal(dict)
    memory_changed = Signal()                                          # 记忆库发生变更（纠错/删除）
    match_changed = Signal(dict)                                       # 对局切换（含自动识别）


class EngineController(QObject):
    """拥有 TranslationEngine，并把其回调桥接到 Qt 信号。"""

    def __init__(self, config, parent=None, data_dir=None):
        super().__init__(parent)
        self.config = config
        self.signals = EngineSignals()
        # 必须把数据目录透传给引擎，否则引擎会回退到默认的 %APPDATA% 目录，
        # 导致 --data-dir 参数失效、记忆库/历史写到错误位置。
        self.engine = TranslationEngine(config, data_dir=data_dir)

        engine = self.engine
        engine.on_chat = lambda msg: self.signals.chat.emit(msg)
        engine.on_translation = lambda msg, translated, latency, error, skipped, meta: (
            self.signals.translation.emit(msg, translated, latency, error, skipped, meta or {})
        )
        engine.on_status = lambda status: self.signals.status.emit(status)
        engine.on_log_event = lambda event: self.signals.log_event.emit(event)
        engine.on_reply = lambda result: self.signals.reply_result.emit(result)
        engine.on_match_change = lambda info: self.signals.match_changed.emit(info or {})

    # ---------------- 生命周期 ----------------
    def start(self) -> None:
        self.engine.start()

    def stop(self, timeout: float = 10.0) -> None:
        """停止引擎。``timeout`` 是每一步的上限，关闭流程会用很短的値。"""
        self.engine.stop(timeout=timeout)

    # ---------------- 回复翻译 ----------------
    def translate_reply(self, text: str, target_language: str) -> None:
        self.engine.translate_reply(text, target_language)

    def suggest_reply_language(self) -> str:
        return self.engine.suggest_reply_language()

    def cancel_pending_replies(self) -> int:
        return self.engine.cancel_pending_replies()

    # ---------------- 控制 ----------------
    def set_paused(self, paused: bool) -> None:
        self.engine._queue.set_paused(paused)

    def set_poll_interval(self, seconds: float) -> None:
        """调整日志轮询间隔（前台是 CS2 时降频）。"""
        self.engine.set_poll_interval(seconds)

    # ---------------- 翻译记忆库 ----------------
    def memory_stats(self) -> dict:
        return self.engine.memory_stats()

    def save_correction(self, text, target_language, direction, new_text=None):
        result = self.engine.save_correction(text, target_language, direction, new_text)
        self.signals.memory_changed.emit()
        return result

    def confirm_translation(self, text, target_language, direction):
        result = self.engine.confirm_translation(text, target_language, direction)
        self.signals.memory_changed.emit()
        return result

    def delete_memory(self, memory_id: int) -> bool:
        result = self.engine.delete_memory(memory_id)
        self.signals.memory_changed.emit()
        return result

    def restore_previous(self, memory_id: int):
        result = self.engine.restore_previous(memory_id)
        self.signals.memory_changed.emit()
        return result

    def find_memory_id(self, text, target_language, direction):
        return self.engine.find_memory_id(text, target_language, direction)

    # ---------------- 测试（放后台线程，避免阻塞 UI） ----------------
    def test_api(self, timeout: float | None = None) -> None:
        """测试 API。

        一律在后台线程执行 —— UI 线程绝不能被网络请求占住（Windows 对超过 5 秒
        不响应消息的窗口就判 AppHang）。``timeout`` 供启动自检用短超时。
        """

        def work():
            try:
                ok, message = self.engine.test_api(timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                ok, message = False, f"测试失败: {exc}"
            self.signals.api_test_done.emit(ok, message)

        threading.Thread(target=work, name="api-test", daemon=True).start()

    def test_log_read(self, path: str | None = None) -> None:
        def work():
            try:
                result = self.engine.test_log_read(path=path or None)
            except Exception as exc:  # noqa: BLE001
                result = {"error": f"测试失败: {exc}", "exists": False}
            self.signals.log_test_done.emit(result)

        threading.Thread(target=work, name="log-test", daemon=True).start()
