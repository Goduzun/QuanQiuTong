"""翻译记忆库端到端集成测试：验证真实 API 调用次数（Mock 客户端注入）。

覆盖需求 11 的 12 项场景：重复句子仅一次 API、多语言/多方向隔离、
人工纠错、否定句、失败不污染、删除后重请求、并发去重等。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from cs2_translator.config import AppConfig
from cs2_translator.engine import TranslationEngine
from cs2_translator.memory import DIRECTION_INCOMING, DIRECTION_REPLY


class CountingClient:
    """记录调用次数、可注入延迟与固定译文的假客户端。"""

    def __init__(self, translated="默认译文", delay=0.0, fail_times=0):
        self.calls = 0
        self.translated = translated
        self.delay = delay
        self.fail_times = fail_times
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.fail_times > 0:
            self.fail_times -= 1
            from cs2_translator.translator import ServerError

            raise ServerError("模拟服务端错误")
        from types import SimpleNamespace

        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content=self.translated), index=0)
            ],
            usage=SimpleNamespace(total_tokens=10),
        )


@pytest.fixture
def engine(tmp_path):
    cfg = AppConfig(
        cs2_log_path=str(tmp_path / "console.log"),
        api_key="sk-test",
        memory_enabled=True,
        save_memory_history=True,
        max_retries=0,
        rate_limit_per_minute=600,
    )
    client = CountingClient()
    eng = TranslationEngine(cfg, data_dir=tmp_path / "data")
    eng.translator._client = client
    eng.reply_translator._client = client
    yield eng, client
    eng.stop()


def translate_incoming(engine, text):
    """同步执行一次接收方向翻译，返回 TranslateOutcome。"""
    return engine._translate_with_memory(
        text=text,
        direction=DIRECTION_INCOMING,
        target_language="zh",
        source_language="en",
        source_type="chat",
        api_translator=engine.translator,
        api_call=lambda hints: engine.translator.translate(
            text, source_language="en", term_hints=hints,
            use_internal_cache=False,
        ),
    )


def translate_reply(engine, text, target):
    return engine._translate_with_memory(
        text=text,
        direction=DIRECTION_REPLY,
        target_language=target,
        source_language="zh",
        source_type="reply",
        api_translator=engine.reply_translator,
        api_call=lambda hints: engine.reply_translator.translate(
            text, target, term_hints=hints, use_internal_cache=False
        ),
    )


# ================================================================
# 重复句子仅一次 API
# ================================================================
class TestRepeatDedup:
    def test_same_english_sentence_one_api_call(self, engine):
        eng, client = engine
        for _ in range(3):
            outcome = translate_incoming(eng, "hello team, rush B please")
            assert outcome.error is None
        assert client.calls == 1
        # 后两次命中精确缓存
        assert translate_incoming(eng, "hello team, rush B please").cache_hit is True

    def test_same_russian_sentence_one_api_call(self, engine):
        eng, client = engine
        for _ in range(3):
            translate_incoming(eng, "привет, идём на B")
        assert client.calls == 1
        assert translate_incoming(eng, "привет, идём на B").cache_hit is True


# ================================================================
# 语言与方向隔离
# ================================================================
class TestIsolation:
    def test_same_source_different_target_language(self, engine):
        eng, client = engine
        translate_reply(eng, "帮我守一下B点", "en")
        assert client.calls == 1
        translate_reply(eng, "帮我守一下B点", "ru")
        assert client.calls == 2  # 不同目标语言不串用

    def test_incoming_and_reply_not_reused(self, engine):
        eng, client = engine
        translate_incoming(eng, "hello my friend")
        assert client.calls == 1
        translate_reply(eng, "hello my friend", "en")
        assert client.calls == 2  # 方向不同，不串用


# ================================================================
# 人工纠错
# ================================================================
class TestCorrection:
    def test_correction_used_on_next_lookup(self, engine):
        eng, client = engine
        first = translate_incoming(eng, "nice shot my friend")
        assert first.translated == "默认译文"
        assert client.calls == 1

        eng.save_correction("nice shot my friend", "zh", DIRECTION_INCOMING, new_text="好枪！")
        second = translate_incoming(eng, "nice shot my friend")
        assert second.translated == "好枪！"
        assert second.hit_kind == "correction"
        assert client.calls == 1  # 命中人工修正，不请求 API


# ================================================================
# 否定句 / 不同内容不误命中
# ================================================================
class TestNoFalseHit:
    def test_negated_sentence_not_confused(self, engine):
        eng, client = engine
        translate_incoming(eng, "rush B now!")
        assert client.calls == 1
        translate_incoming(eng, "don't rush B now!")
        assert client.calls == 2  # 否定句是不同缓存键

    def test_different_site_number_not_reused(self, engine):
        eng, client = engine
        translate_incoming(eng, "two enemies at A")
        translate_incoming(eng, "three enemies at B")
        assert client.calls == 2  # 点位/数字不同，不套用未确认译文


# ================================================================
# 失败不污染缓存
# ================================================================
class TestFailureSafety:
    def test_api_failure_does_not_pollute(self, tmp_path):
        cfg = AppConfig(
            cs2_log_path=str(tmp_path / "console.log"),
            api_key="sk-test", memory_enabled=True, max_retries=0,
            rate_limit_per_minute=600,
        )
        client = CountingClient(fail_times=1, translated="好的")
        eng = TranslationEngine(cfg, data_dir=tmp_path / "data")
        eng.translator._client = client
        try:
            first = translate_incoming(eng, "hello there")
            assert first.error is not None
            assert first.translated is None
            # 失败后重试应再次请求 API（而不是命中脏缓存）
            second = translate_incoming(eng, "hello there")
            assert second.error is None
            assert second.translated == "好的"
            assert client.calls == 2
        finally:
            eng.stop()


# ================================================================
# 删除后重新请求
# ================================================================
class TestDelete:
    def test_delete_then_re_request(self, engine):
        eng, client = engine
        translate_incoming(eng, "unique phrase here")
        assert client.calls == 1
        assert eng.delete_memory(eng.find_memory_id("unique phrase here", "zh", DIRECTION_INCOMING))
        translate_incoming(eng, "unique phrase here")
        assert client.calls == 2


# ================================================================
# 重启后缓存仍存在（引擎级）
# ================================================================
class TestPersistence:
    def test_engine_restart_keeps_cache(self, tmp_path):
        cfg = AppConfig(
            cs2_log_path=str(tmp_path / "console.log"),
            api_key="sk-test", memory_enabled=True,
            max_retries=0, rate_limit_per_minute=600,
        )
        data_dir = tmp_path / "data"

        client1 = CountingClient(translated="第一版译文")
        eng1 = TranslationEngine(cfg, data_dir=data_dir)
        eng1.translator._client = client1
        translate_incoming(eng1, "persist me please")
        assert client1.calls == 1
        eng1.stop()

        # 第二个引擎（模拟重启）：同一句应命中缓存，不再请求 API
        client2 = CountingClient(translated="第二版译文")
        eng2 = TranslationEngine(cfg, data_dir=data_dir)
        eng2.translator._client = client2
        try:
            outcome = translate_incoming(eng2, "persist me please")
            assert outcome.cache_hit is True
            assert outcome.translated == "第一版译文"  # 复用重启前的缓存
            assert client2.calls == 0
        finally:
            eng2.stop()


# ================================================================
# 并发同句去重
# ================================================================
class TestConcurrency:
    def test_concurrent_same_sentence_one_api_call(self, tmp_path):
        cfg = AppConfig(
            cs2_log_path=str(tmp_path / "console.log"),
            api_key="sk-test", memory_enabled=True, max_retries=0,
            rate_limit_per_minute=600,
        )
        client = CountingClient(delay=0.15, translated="并发译文")
        eng = TranslationEngine(cfg, data_dir=tmp_path / "data")
        eng.translator._client = client
        try:
            def run():
                return translate_incoming(eng, "concurrent rush B")

            with __import__("concurrent.futures").futures.ThreadPoolExecutor(max_workers=6) as pool:
                results = list(pool.map(lambda _: run(), range(6)))
            assert all(r.translated == "并发译文" for r in results)
            assert client.calls == 1
        finally:
            eng.stop()


# ================================================================
# 双向缓存不串方向
# ================================================================
class TestBidirectional:
    def test_direction_cache_not_mixed(self, engine):
        eng, client = engine
        translate_incoming(eng, "boost me")
        incoming_calls = client.calls
        # 回复方向同文本，应单独请求 API（不是复用接收方向缓存）
        translate_reply(eng, "boost me", "en")
        assert client.calls == incoming_calls + 1


# ================================================================
# 最终验收：完整流水线（日志监听 -> 队列 -> 记忆库）
# 连续三次相同外语消息：首次 API，后两次缓存命中且零 API 请求；重启后仍复用。
# ================================================================
class TestAcceptance:
    def _wait_until(self, predicate, timeout=6.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return predicate()

    def test_three_repeats_full_pipeline(self, tmp_path):
        log = tmp_path / "console.log"
        log.write_text("", encoding="utf-8")

        cfg = AppConfig(
            cs2_log_path=str(log),
            api_key="sk-test", memory_enabled=True, save_memory_history=True,
            max_retries=0, rate_limit_per_minute=600, log_poll_interval=0.05,
        )
        client1 = CountingClient(translated="冲B点，快！")
        eng = TranslationEngine(cfg, data_dir=tmp_path / "data")
        eng.translator._client = client1

        results = []
        done = threading.Event()

        def on_translation(msg, translated, latency, error, skipped, meta):
            results.append((meta or {}).get("hit_kind", ""))
            if len(results) == 3:
                done.set()

        eng.on_translation = on_translation
        eng.start()
        try:
            # 等待 watcher 打开日志文件
            self._wait_until(lambda: eng._watcher._fh is not None)

            for _ in range(3):
                with open(log, "a", encoding="utf-8") as fh:
                    fh.write("[ALL] Ivan: rush B now!\n")
                    fh.flush()
                time.sleep(0.3)

            assert self._wait_until(lambda: len(results) == 3), f"只收到 {len(results)} 条结果"
            assert client1.calls == 1  # 三次仅一次 API
            assert results == ["", "exact", "exact"]  # 首次 API，后两次精确缓存
        finally:
            eng.stop()

        # 重启后仍复用缓存（不请求 API）
        client2 = CountingClient(translated="重启后的译文")
        eng2 = TranslationEngine(cfg, data_dir=tmp_path / "data")
        eng2.translator._client = client2
        eng2.on_translation = lambda *a, **k: None
        eng2.start()
        try:
            self._wait_until(lambda: eng2._watcher._fh is not None)
            with open(log, "a", encoding="utf-8") as fh:
                fh.write("[ALL] Ivan: rush B now!\n")
                fh.flush()
            # 等待缓存命中（watcher -> 队列 -> 记忆库）
            def hit_received():
                stats = eng2.memory_stats()
                return stats["exact_hits"] >= 1

            assert self._wait_until(hit_received), "重启后未命中缓存"
            assert client2.calls == 0  # 重启后复用缓存，零 API 请求
        finally:
            eng2.stop()
