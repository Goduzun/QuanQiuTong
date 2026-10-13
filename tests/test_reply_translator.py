"""双向回复翻译测试（新增功能）：语言选择、译文输出、术语优化、错误恢复。"""

from __future__ import annotations

import pytest

from cs2_translator.config import AppConfig
from cs2_translator.reply_translator import COMMON_REPLY_TARGETS, ReplyTranslator
from cs2_translator.translator import AuthError, TranslatorError

from conftest import make_api_error


def make_reply_translator(config: AppConfig, client, sleeps=None):
    sleeper = (lambda s: sleeps.append(s)) if sleeps is not None else (lambda s: None)
    return ReplyTranslator(config, api_key=config.api_key, client=client, sleeper=sleeper)


class TestTargetLanguageSelection:
    def test_common_targets_available(self):
        codes = {code for code, _ in COMMON_REPLY_TARGETS}
        for expected in ("en", "ru", "ja", "ko"):
            assert expected in codes

    def test_translate_to_english(self, base_config, fake_client):
        client = fake_client(responses=["rush B, follow me!"])
        translator = make_reply_translator(base_config, client)
        result = translator.translate("冲B点，跟我走！", "en")
        assert result == "rush B, follow me!"
        # 请求体中的用户消息是中文原文
        assert client.calls[0]["messages"][1]["content"] == "冲B点，跟我走！"

    def test_translate_to_russian(self, base_config, fake_client):
        client = fake_client(responses=["раш Б, за мной!"])
        translator = make_reply_translator(base_config, client)
        result = translator.translate("冲B点，跟我走！", "ru")
        assert result == "раш Б, за мной!"
        prompt = client.calls[0]["messages"][0]["content"]
        assert "俄语" in prompt
        assert "ru" in prompt

    def test_translate_to_japanese_and_korean(self, base_config, fake_client):
        client = fake_client(responses=["Bラッシュ、ついてきて！"])
        translator = make_reply_translator(base_config, client)
        assert translator.translate("冲B点，跟我走！", "ja") == "Bラッシュ、ついてきて！"
        client2 = fake_client(responses=["B 러시, 따라와!"])
        translator2 = make_reply_translator(base_config, client2)
        assert translator2.translate("冲B点，跟我走！", "ko") == "B 러시, 따라와!"

    def test_language_alias_normalized(self, base_config, fake_client):
        client = fake_client(responses=["hello"])
        translator = make_reply_translator(base_config, client)
        translator.translate("你好", "英语")
        assert "英语" in client.calls[0]["messages"][0]["content"]
        assert "en" in client.calls[0]["messages"][0]["content"]

    def test_display_name(self):
        assert ReplyTranslator.display_name("ru") == "俄语"
        assert ReplyTranslator.display_name("en") == "英语"

    def test_suggestion_from_last_message(self):
        assert ReplyTranslator.suggest_target("привет") == "ru"
        assert ReplyTranslator.suggest_target("こんにちは") == "ja"
        assert ReplyTranslator.suggest_target("안녕") == "ko"
        assert ReplyTranslator.suggest_target("冲B点") is None
        assert ReplyTranslator.suggest_target(None) is None


class TestTerminologyOptimization:
    def test_reply_prompt_contains_glossary(self, base_config, fake_client):
        client = fake_client(responses=["save"])
        translator = make_reply_translator(base_config, client)
        translator.translate("保枪", "en")
        prompt = client.calls[0]["messages"][0]["content"]
        assert "保枪" in prompt and "save" in prompt

    def test_russian_glossary_in_prompt(self, base_config, fake_client):
        client = fake_client(responses=["сейв"])
        translator = make_reply_translator(base_config, client)
        translator.translate("保枪", "ru")
        prompt = client.calls[0]["messages"][0]["content"]
        assert "сейв" in prompt

    def test_unknown_language_falls_back(self, base_config, fake_client):
        client = fake_client(responses=["halló"])
        translator = make_reply_translator(base_config, client)
        assert translator.translate("你好", "is") == "halló"


class TestErrorHandling:
    def test_auth_error_surfaces(self, base_config, fake_client):
        client = fake_client(errors=[make_api_error(401, "bad")])
        translator = make_reply_translator(base_config, client)
        with pytest.raises(AuthError):
            translator.translate("你好", "en")

    def test_empty_input_rejected(self, base_config, fake_client):
        client = fake_client(responses=["x"])
        translator = make_reply_translator(base_config, client)
        with pytest.raises(TranslatorError):
            translator.translate("  ", "en")
        assert len(client.calls) == 0

    def test_too_long_rejected(self, base_config, fake_client):
        base_config.max_message_length = 20
        client = fake_client(responses=["x"])
        translator = make_reply_translator(base_config, client)
        with pytest.raises(TranslatorError):
            translator.translate("长" * 50, "en")
        assert len(client.calls) == 0

    def test_cache_key_includes_target(self, base_config, fake_client):
        client = fake_client(responses=["hello", "привет"])
        translator = make_reply_translator(base_config, client)
        assert translator.translate("你好", "en") == "hello"
        assert translator.translate("你好", "ru") == "привет"
        assert translator.translate("你好", "en") == "hello"
        assert len(client.calls) == 2  # 第三次命中缓存

    def test_metrics_tracked(self, base_config, fake_client):
        client = fake_client(responses=["ok"])
        translator = make_reply_translator(base_config, client)
        translator.translate("你好", "en")
        metrics = translator.metrics()
        assert metrics["successes"] == 1
        assert metrics["failures"] == 0
