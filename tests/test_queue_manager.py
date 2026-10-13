"""翻译队列测试：有界、去重、保序、取消、暂停、过期跳过、清理。"""

from __future__ import annotations

import threading
import time

import pytest

from cs2_translator.chat_parser import ChatMessage
from cs2_translator.queue_manager import TranslationQueueManager


def make_message(text: str, sender: str = "Player") -> ChatMessage:
    return ChatMessage(sender=sender, content=text, raw=f"[ALL] {sender}: {text}")


class TestBoundedAndDedup:
    def test_queue_full_drops(self):
        manager = TranslationQueueManager(handler=lambda m, s: None, max_size=2, workers=1)
        manager.set_paused(True)  # 暂停消费，制造堆积
        assert manager.submit(make_message("a")) is True
        assert manager.submit(make_message("b")) is True
        assert manager.submit(make_message("c")) is False  # 队列已满
        assert manager.stats()["dropped_full"] == 1
        manager.stop()

    def test_duplicate_dropped(self):
        manager = TranslationQueueManager(handler=lambda m, s: None, max_size=10, workers=1)
        manager.set_paused(True)
        assert manager.submit(make_message("same text")) is True
        assert manager.submit(make_message("same text")) is False
        assert manager.stats()["dropped_duplicate"] == 1
        # 同一玩家的相同内容才算重复；不同发送者的相同内容应被接受
        assert manager.submit(make_message("same text", sender="Other")) is True
        manager.stop()

    def test_different_messages_accepted(self):
        manager = TranslationQueueManager(handler=lambda m, s: None, max_size=10, workers=1)
        manager.set_paused(True)
        assert manager.submit(make_message("a")) is True
        assert manager.submit(make_message("b")) is True
        assert manager.stats()["submitted"] == 2
        manager.stop()


class TestOrdering:
    def test_results_emitted_in_order(self):
        emitted = []
        manager = TranslationQueueManager(
            handler=None, max_size=10, workers=2, reorder_timeout=2.0
        )
        delays = {"slow": 0.3, "fast1": 0.0, "fast2": 0.05}

        def handler(message, seq):
            time.sleep(delays.get(message.content, 0))
            manager.submit_result(seq, lambda: emitted.append(message.content))

        manager._handler = handler
        manager.start()
        try:
            for text in ("slow", "fast1", "fast2"):
                manager.submit(make_message(text))
            deadline = time.monotonic() + 3
            while len(emitted) < 3 and time.monotonic() < deadline:
                time.sleep(0.02)
            assert emitted == ["slow", "fast1", "fast2"]
        finally:
            manager.stop()


class TestCancelAndPause:
    def test_cancel_pending(self):
        manager = TranslationQueueManager(handler=lambda m, s: None, max_size=10, workers=1)
        manager.set_paused(True)
        for i in range(3):
            manager.submit(make_message(f"m{i}"))
        cancelled = manager.cancel_pending()
        assert cancelled == 3
        assert manager.stats()["queue_length"] == 0
        manager.stop()

    def test_pause_blocks_processing(self):
        processed = []
        manager = TranslationQueueManager(
            handler=lambda m, s: processed.append(m.content), max_size=10, workers=1
        )
        manager.set_paused(True)  # 先暂停再启动，避免竞争
        manager.start()
        manager.submit(make_message("a"))
        time.sleep(0.3)
        assert processed == []
        manager.set_paused(False)
        deadline = time.monotonic() + 2
        while not processed and time.monotonic() < deadline:
            time.sleep(0.02)
        assert processed == ["a"]
        manager.stop()

    def test_skip_expired(self):
        processed = []
        manager = TranslationQueueManager(
            handler=lambda m, s: processed.append(m.content),
            max_size=10,
            workers=1,
            skip_expired_seconds=1,
        )
        manager.set_paused(True)  # 先暂停再启动
        manager.start()
        manager.submit(make_message("old"))
        time.sleep(1.2)  # 消息过期
        manager.set_paused(False)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and manager.stats()["skipped_expired"] == 0:
            time.sleep(0.05)
        assert processed == []
        assert manager.stats()["skipped_expired"] == 1
        manager.stop()


class TestRobustness:
    def test_handler_error_does_not_kill_workers(self):
        def handler(message, seq):
            if message.content == "bad":
                raise RuntimeError("boom")
            time.sleep(0.01)

        manager = TranslationQueueManager(handler=handler, max_size=10, workers=1)
        manager.start()
        try:
            for text in ("bad", "good1", "good2"):
                manager.submit(make_message(text))
            deadline = time.monotonic() + 3
            while manager.stats()["processed"] < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            stats = manager.stats()
            assert stats["failed"] == 1
            assert stats["processed"] == 2
        finally:
            manager.stop()

    def test_stop_cleans_up_threads(self):
        manager = TranslationQueueManager(handler=lambda m, s: None, max_size=10, workers=3)
        manager.start()
        names = [t.name for t in threading.enumerate() if t.name.startswith("translate-")]
        assert len(names) == 3
        manager.stop(timeout=3)
        alive = [t.name for t in threading.enumerate() if t.name.startswith("translate-")]
        assert alive == []

    def test_stats_snapshot(self):
        manager = TranslationQueueManager(handler=lambda m, s: None, max_size=7, workers=1)
        stats = manager.stats()
        assert stats["max_size"] == 7
        assert stats["queue_length"] == 0
        assert stats["submitted"] == 0
