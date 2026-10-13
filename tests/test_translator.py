"""翻译引擎测试：覆盖需求 5.5 与测试清单 13-17（Mock，不消耗真实 API）。"""

from __future__ import annotations

import logging
import time

import pytest

from cs2_translator.config import AppConfig
from cs2_translator.translator import (
    AuthError,
    BadRequestError,
    EmptyResponseError,
    MessageTooLongError,
    NetworkError,
    OpenAITranslator,
    RateLimitError,
    ServerError,
    TranslatorError,
)

from conftest import make_api_error, make_connection_error, make_timeout_error


def make_translator(config: AppConfig, client, sleeps=None):
    sleeper = (lambda s: sleeps.append(s)) if sleeps is not None else (lambda s: None)
    return OpenAITranslator(config, api_key=config.api_key, client=client, sleeper=sleeper)


class TestSuccess:
    def test_translate_success(self, base_config, fake_client):
        client = fake_client(responses=["冲B点，快点！"])
        translator = make_translator(base_config, client)
        result = translator.translate("rush B now!")
        assert result == "冲B点，快点！"
        metrics = translator.metrics()
        assert metrics["successes"] == 1
        assert metrics["failures"] == 0
        assert metrics["last_latency_ms"] >= 0

    def test_request_payload(self, base_config, fake_client):
        client = fake_client(responses=["译文"])
        translator = make_translator(base_config, client)
        translator.translate("hello")
        assert len(client.calls) == 1
        call = client.calls[0]
        assert call["model"] == "gpt-4o-mini"
        messages = call["messages"]
        assert messages[0]["role"] == "system"
        assert messages[1] == {"role": "user", "content": "hello"}

    def test_prompt_contains_glossary_and_rules(self, base_config, fake_client):
        client = fake_client(responses=["译文"])
        translator = make_translator(base_config, client)
        translator.translate("rush B")
        prompt = client.calls[0]["messages"][0]["content"]
        assert "只输出译文" in prompt
        assert "rush B" in prompt  # 术语表
        assert "保枪" in prompt or "save" in prompt


class TestRetryAndErrors:
    def test_timeout_no_retry_when_disabled(self, base_config, fake_client):
        base_config.max_retries = 0
        client = fake_client(errors=[make_timeout_error()])
        translator = make_translator(base_config, client)
        with pytest.raises(NetworkError):
            translator.translate("hello")
        assert translator.metrics()["failures"] == 1
        assert translator.metrics()["requests"] == 1

    def test_rate_limit_retry_then_success(self, base_config, fake_client):
        sleeps = []
        client = fake_client(
            errors=[make_api_error(429, "too many", {"Retry-After": "1"})],
            responses=["终于成功"],
        )
        translator = make_translator(base_config, client, sleeps=sleeps)
        result = translator.translate("hello")
        assert result == "终于成功"
        assert sleeps == [1.0]  # 遵循 Retry-After
        metrics = translator.metrics()
        assert metrics["retries"] == 1
        assert metrics["successes"] == 1

    def test_rate_limit_exhausted(self, base_config, fake_client):
        base_config.max_retries = 1
        sleeps = []
        client = fake_client(errors=[make_api_error(429, "rl"), make_api_error(429, "rl")])
        translator = make_translator(base_config, client, sleeps=sleeps)
        with pytest.raises(RateLimitError):
            translator.translate("hello")
        assert translator.metrics()["failures"] == 1

    def test_invalid_api_key_no_retry(self, base_config, fake_client):
        base_config.max_retries = 3
        client = fake_client(errors=[make_api_error(401, "invalid key")])
        translator = make_translator(base_config, client)
        with pytest.raises(AuthError) as exc_info:
            translator.translate("hello")
        assert "API Key" in str(exc_info.value)
        assert len(client.calls) == 1  # 认证错误不重试

    def test_forbidden_no_retry(self, base_config, fake_client):
        base_config.max_retries = 3
        client = fake_client(errors=[make_api_error(403, "forbidden")])
        translator = make_translator(base_config, client)
        with pytest.raises(AuthError):
            translator.translate("hello")
        assert len(client.calls) == 1

    def test_server_error_retry_then_success(self, base_config, fake_client):
        sleeps = []
        client = fake_client(errors=[make_api_error(503, "unavailable")], responses=["恢复"])
        translator = make_translator(base_config, client, sleeps=sleeps)
        assert translator.translate("hello") == "恢复"
        assert sleeps  # 发生退避重试
        assert translator.metrics()["retries"] == 1

    def test_server_error_exhausted(self, base_config, fake_client):
        base_config.max_retries = 1
        client = fake_client(errors=[make_api_error(500, "boom"), make_api_error(500, "boom")])
        translator = make_translator(base_config, client)
        with pytest.raises(ServerError):
            translator.translate("hello")

    def test_model_not_found_bad_request(self, base_config, fake_client):
        base_config.max_retries = 3
        client = fake_client(errors=[make_api_error(404, "no model")])
        translator = make_translator(base_config, client)
        with pytest.raises(BadRequestError) as exc_info:
            translator.translate("hello")
        assert "模型" in str(exc_info.value)
        assert len(client.calls) == 1

    def test_connection_error_retried(self, base_config, fake_client):
        base_config.max_retries = 2
        client = fake_client(errors=[make_connection_error(), make_connection_error(), None], responses=["ok"])
        translator = make_translator(base_config, client)
        assert translator.translate("hello") == "ok"

    def test_empty_response_retried_then_error(self, base_config, fake_client):
        base_config.max_retries = 1
        client = fake_client(responses=["", ""])
        translator = make_translator(base_config, client)
        with pytest.raises(EmptyResponseError):
            translator.translate("hello")

    def test_missing_api_key_raises_auth_error(self, base_config, fake_client):
        base_config.api_key = ""
        translator = OpenAITranslator(base_config, api_key="", client=None)
        with pytest.raises(AuthError):
            translator.translate("hello")


class TestCacheAndLimits:
    def test_cache_avoids_duplicate_requests(self, base_config, fake_client):
        client = fake_client(responses=["译文A"])
        translator = make_translator(base_config, client)
        first = translator.translate("same message")
        second = translator.translate("same message")
        assert first == second == "译文A"
        assert len(client.calls) == 1
        assert translator.metrics()["cache_hits"] == 1

    def test_cache_expiry(self, base_config, fake_client):
        base_config.cache_ttl_seconds = 0  # 立即过期
        client = fake_client(responses=["A", "B"])
        translator = make_translator(base_config, client)
        assert translator.translate("x") == "A"
        assert translator.translate("x") == "B"
        assert len(client.calls) == 2

    def test_too_long_message_rejected(self, base_config, fake_client):
        base_config.max_message_length = 20
        client = fake_client(responses=["should not happen"])
        translator = make_translator(base_config, client)
        with pytest.raises(MessageTooLongError):
            translator.translate("x" * 50)
        assert len(client.calls) == 0

    def test_empty_message_rejected(self, base_config, fake_client):
        client = fake_client(responses=["x"])
        translator = make_translator(base_config, client)
        with pytest.raises(TranslatorError):
            translator.translate("   ")
        assert len(client.calls) == 0

    def test_rate_gate_serializes_requests(self, base_config, fake_client):
        base_config.rate_limit_per_minute = 600  # 0.1s 间隔
        client = fake_client(responses=["A", "B"])
        translator = make_translator(base_config, client)
        start = time.monotonic()
        translator.translate("one")
        translator.translate("two")
        elapsed = time.monotonic() - start
        assert elapsed >= 0.1
        assert len(client.calls) == 2


class TestSecurity:
    def test_api_key_not_logged(self, base_config, fake_client, caplog):
        logger = logging.getLogger("cs2_translator")
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Capture()
        logger.addHandler(handler)
        try:
            base_config.max_retries = 0
            client = fake_client(errors=[make_api_error(401, "bad key sk-test-key")])
            translator = make_translator(base_config, client)
            with pytest.raises(AuthError):
                translator.translate("hello")
        finally:
            logger.removeHandler(handler)
        combined = "\n".join(records)
        assert "sk-test-key" not in combined

    def test_redacting_filter_scrubs_keys(self):
        from cs2_translator.logging_setup import SecretRedactingFilter

        redactor = SecretRedactingFilter(secrets=["sk-super-secret"])
        text = "using key sk-super-secret and sk-abcdef123456 now"
        cleaned = redactor._redact_text(text)
        assert "sk-super-secret" not in cleaned
        assert "sk-abcdef123456" not in cleaned
        assert "***REDACTED***" in cleaned

    def test_redacting_filter_preserves_non_string_args(self):
        """回归：过滤器不得把 int 等参数字符串化，否则 %d 格式化崩溃。"""
        import logging

        from cs2_translator.logging_setup import SecretRedactingFilter

        redactor = SecretRedactingFilter(secrets=["sk-super-secret"])

        captured = []

        class Capture(logging.Handler):
            def emit(self, record):
                captured.append(record.getMessage())

        logger = logging.getLogger("cs2_translator.test_filter_args")
        handler = Capture()
        handler.addFilter(redactor)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.warning("失败(kind=%s, attempt=%d): %s", "auth", 0, "sk-super-secret")
        finally:
            logger.removeHandler(handler)

        assert len(captured) == 1
        assert "attempt=0" in captured[0]
        assert "kind=auth" in captured[0]
        assert "sk-super-secret" not in captured[0]
        assert "***REDACTED***" in captured[0]
