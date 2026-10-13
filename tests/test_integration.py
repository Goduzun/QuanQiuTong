"""端到端集成测试：本地 Mock OpenAI 服务器 + 真实日志文件 + 完整引擎流水线。

这些测试通过真实 HTTP 请求验证：日志监听 -> 解析 -> 队列 -> 翻译 -> 输出，
以及回复翻译、限流重试、认证失败与故障恢复。不消耗任何真实 API 额度。
"""

from __future__ import annotations

import threading
import time

import pytest

from cs2_translator.config import AppConfig
from cs2_translator.engine import TranslationEngine

INITIAL_CONTENT = "Executing command: -condebug\n"

NEW_CONTENT = (
    "08/28 18:00:28  [ALL] LITE | котость: ахпахпап\n"
    "[ALL] Player One: rush B now!\n"
    "[ALL] 小明: 大家好\n"
    "[TEAM] s1mple: rotate mid\n"
    "WARNING: engine noise should be ignored\n"
    "[ALL] Ivanov: го го го\n"
)


def wait_until(predicate, timeout=8.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def make_engine(tmp_path, mock_server, **overrides) -> TranslationEngine:
    kwargs = dict(
        cs2_log_path=str(tmp_path / "console.log"),
        api_base_url=mock_server.base_url,
        api_key="sk-integration-test",
        model="test-model",
        timeout_seconds=5.0,
        max_retries=2,
        rate_limit_per_minute=600,
        cache_ttl_seconds=0,
        queue_max_size=50,
        translation_workers=2,
        log_poll_interval=0.03,
    )
    kwargs.update(overrides)
    return TranslationEngine(AppConfig(**kwargs), data_dir=tmp_path / "data")


class TestEndToEndPipeline:
    def test_full_pipeline(self, tmp_path, mock_server):
        log = tmp_path / "console.log"
        log.write_text(INITIAL_CONTENT, encoding="utf-8")

        engine = make_engine(tmp_path, mock_server)
        results = []
        done = threading.Event()
        expected = 5  # 3 条外语翻译 + 1 条中文保留 + 1 条俄语

        def on_translation(message, translated, latency_ms, error, skipped, meta=None):
            results.append(
                {
                    "sender": message.sender,
                    "original": message.content,
                    "translated": translated,
                    "error": error,
                    "skipped": skipped,
                    "channel": message.channel,
                }
            )
            if len(results) >= expected:
                done.set()

        engine.on_translation = on_translation
        engine.start()
        try:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(NEW_CONTENT)
            assert wait_until(done.is_set, timeout=10), f"仅收到 {len(results)} 条结果"

            # 五条消息都应产生结果（翻译线程与跳过路径并发，不保证到达顺序）
            by_original = {r["original"]: r for r in results}
            assert set(by_original) == {
                "ахпахпап",
                "rush B now!",
                "大家好",
                "rotate mid",
                "го го го",
            }
            # 外语消息被翻译（mock 回显标记）
            assert by_original["ахпахпап"]["translated"] == "[译] ахпахпап"
            assert by_original["ахпахпап"]["channel"] == "ALL"
            assert by_original["ахпахпап"]["skipped"] is False
            assert by_original["rush B now!"]["translated"] == "[译] rush B now!"
            assert by_original["rotate mid"]["translated"] == "[译] rotate mid"
            assert by_original["rotate mid"]["channel"] == "TEAM"
            assert by_original["го го го"]["translated"] == "[译] го го го"
            # 中文消息保留原文
            assert by_original["大家好"]["skipped"] is True
            assert by_original["大家好"]["translated"] == "大家好"
            # 引擎日志未被当作聊天
            assert not any("WARNING" in (r["original"] or "") for r in results)

            # HTTP 层验证：每条请求只包含一条待翻译消息，未上传整份日志
            user_messages = {req["body"]["messages"][-1]["content"] for req in mock_server.requests}
            assert user_messages == {"ахпахпап", "rush B now!", "rotate mid", "го го го"}
            assert all(req["auth"] == "Bearer sk-integration-test" for req in mock_server.requests)
            for req in mock_server.requests:
                assert "WARNING" not in json_dump(req["body"])
                assert len(req["body"]["messages"][-1]["content"]) < 500
        finally:
            engine.stop()

        assert engine._running is False

    def test_reply_translation_through_engine(self, tmp_path, mock_server):
        engine = make_engine(tmp_path, mock_server)
        replies = []
        done = threading.Event()

        def on_reply(result):
            replies.append(result)
            done.set()

        engine.on_reply = on_reply
        engine.start()
        try:
            engine.translate_reply("冲B点，跟我走", "ru")
            assert wait_until(done.is_set, timeout=8)
            result = replies[0]
            assert result["ok"] is True
            assert result["translated"] == "[译] 冲B点，跟我走"
            assert result["target"] == "ru"
            assert result["latency_ms"] >= 0
        finally:
            engine.stop()

    def test_reply_suggestion_follows_last_message(self, tmp_path, mock_server):
        log = tmp_path / "console.log"
        log.write_text(INITIAL_CONTENT, encoding="utf-8")
        engine = make_engine(tmp_path, mock_server)
        seen = []
        done = threading.Event()

        def on_translation(message, translated, latency_ms, error, skipped, meta=None):
            seen.append(message)
            if not skipped:
                done.set()

        engine.on_translation = on_translation
        engine.start()
        try:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write("[ALL] Иван: привет\n")
            assert wait_until(done.is_set, timeout=8)
            # 最近一条是俄语 -> 建议俄语
            assert engine.suggest_reply_language() == "ru"
        finally:
            engine.stop()

    def test_suggestion_falls_back_to_user_default(self, tmp_path, mock_server):
        log = tmp_path / "console.log"
        log.write_text(INITIAL_CONTENT, encoding="utf-8")
        engine = make_engine(tmp_path, mock_server, reply_target_language="ja")
        seen = []
        done = threading.Event()

        def on_translation(message, translated, latency_ms, error, skipped, meta=None):
            seen.append(message)
            if not skipped:
                done.set()

        engine.on_translation = on_translation
        engine.start()
        try:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write("[ALL] Mike: hello team\n")  # 英语 -> 无明确建议
            assert wait_until(done.is_set, timeout=8)
            assert engine.suggest_reply_language() == "ja"  # 回退用户默认
        finally:
            engine.stop()


class TestHttpErrorHandling:
    def test_rate_limit_retried_over_http(self, tmp_path, mock_server):
        mock_server.script = [
            {"status": 429, "json": {"error": {"message": "slow down"}}, "headers": {"Retry-After": "0"}},
        ]
        log = tmp_path / "console.log"
        log.write_text(INITIAL_CONTENT, encoding="utf-8")
        engine = make_engine(tmp_path, mock_server, max_retries=1)
        results = []
        done = threading.Event()

        def on_translation(message, translated, latency_ms, error, skipped, meta=None):
            results.append((translated, error, skipped))
            done.set()

        engine.on_translation = on_translation
        engine.start()
        try:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write("[ALL] Player: hello there\n")
            assert wait_until(done.is_set, timeout=10)
            translated, error, skipped = results[0]
            assert error is None
            assert translated == "[译] hello there"
            assert len(mock_server.requests) == 2  # 429 + 200
        finally:
            engine.stop()

    def test_auth_failure_then_recovery(self, tmp_path, mock_server):
        # 第一条 401，第二条正常：验证 API 失败不会拖垮后续消息
        mock_server.script = [
            {"status": 401, "json": {"error": {"message": "invalid api key"}}},
        ]
        log = tmp_path / "console.log"
        log.write_text(INITIAL_CONTENT, encoding="utf-8")
        engine = make_engine(tmp_path, mock_server, max_retries=0)
        results = []
        done = threading.Event()

        def on_translation(message, translated, latency_ms, error, skipped, meta=None):
            results.append((translated, error, skipped))
            if len(results) >= 2:
                done.set()

        engine.on_translation = on_translation
        engine.start()
        try:
            with open(log, "a", encoding="utf-8") as fh:
                fh.write("[ALL] A: first message\n")
            assert wait_until(lambda: len(results) >= 1, timeout=8)
            _, first_error, _ = results[0]
            assert first_error is not None
            assert getattr(first_error, "kind", None) == "auth"
            with open(log, "a", encoding="utf-8") as fh:
                fh.write("[ALL] B: second message\n")
            assert wait_until(done.is_set, timeout=8)
            assert results[1][0] == "[译] second message"
            assert results[1][1] is None
        finally:
            engine.stop()

    def test_reply_auth_failure_reported(self, tmp_path, mock_server):
        mock_server.script = [
            {"status": 401, "json": {"error": {"message": "invalid api key"}}},
        ]
        engine = make_engine(tmp_path, mock_server, max_retries=0)
        replies = []
        done = threading.Event()
        engine.on_reply = lambda result: (replies.append(result), done.set())
        engine.start()
        try:
            engine.translate_reply("你好", "en")
            assert wait_until(done.is_set, timeout=8)
            assert replies[0]["ok"] is False
            assert replies[0]["error_kind"] == "auth"
            assert replies[0]["translated"] is None
        finally:
            engine.stop()


def json_dump(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)
