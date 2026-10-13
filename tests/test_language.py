"""语言识别测试：覆盖需求 5.4 与测试清单 3、4。"""

from __future__ import annotations

from cs2_translator.language import (
    chinese_ratio,
    detect_primary_language,
    is_chinese_dominant,
    needs_translation,
    normalize_language_code,
    language_display_name,
    suggest_reply_language,
)

ALL_ENABLED = ["en", "ru", "uk", "ja", "ko", "th", "ar", "he", "hi", "de", "fr", "other"]


class TestDetection:
    def test_russian(self):
        assert detect_primary_language("Привет, как дела?") == "ru"

    def test_ukrainian_distinguished(self):
        assert detect_primary_language("Дякую друзі") == "uk"

    def test_japanese(self):
        assert detect_primary_language("こんにちは") == "ja"

    def test_korean(self):
        assert detect_primary_language("안녕하세요") == "ko"

    def test_english(self):
        assert detect_primary_language("rush B now") == "en"

    def test_chinese(self):
        assert detect_primary_language("冲B点，跟我走") == "zh"

    def test_mixed_english_chinese(self):
        # 有汉字但比例低 -> 不判定为中文主导
        assert detect_primary_language("rush B 冲") != "zh"

    def test_arabic(self):
        assert detect_primary_language("مرحبا") == "ar"

    def test_numbers_and_punctuation(self):
        assert detect_primary_language("12345 !!! ???") == "other"


class TestChineseRatio:
    def test_pure_chinese(self):
        assert chinese_ratio("你好世界") == 1.0

    def test_mixed(self):
        # "hello你好" = 5 个拉丁字母 + 2 个汉字
        assert abs(chinese_ratio("hello你好") - 2 / 7) < 1e-9

    def test_no_letters(self):
        assert chinese_ratio("12345 !!!") == 0.0

    def test_dominant_threshold(self):
        assert is_chinese_dominant("快点rush") is True
        assert is_chinese_dominant("rush rush rush 冲") is False
        # 仅有汉字出现但非主导，不得判为中文
        assert is_chinese_dominant("rotate B 转") is False


class TestNeedsTranslation:
    def test_english_needs(self):
        assert needs_translation("hello team", ALL_ENABLED) is True

    def test_russian_needs(self):
        assert needs_translation("привет", ALL_ENABLED) is True

    def test_chinese_kept(self):
        assert needs_translation("冲B点", ALL_ENABLED) is False

    def test_mixed_translated(self):
        # 中英混合：外语占主导，需要翻译（非中文部分由模型处理）
        assert needs_translation("rush B 冲！", ALL_ENABLED) is True

    def test_pure_numbers_skipped(self):
        assert needs_translation("12345", ALL_ENABLED) is False

    def test_pure_punctuation_skipped(self):
        assert needs_translation("!!! ???", ALL_ENABLED) is False

    def test_empty_skipped(self):
        assert needs_translation("", ALL_ENABLED) is False
        assert needs_translation("   ", ALL_ENABLED) is False

    def test_disabled_language_skipped(self):
        assert needs_translation("hello", ["ru"]) is False

    def test_other_fallback(self):
        assert needs_translation("สวัสดี", ALL_ENABLED) is True


class TestNormalize:
    def test_display_names(self):
        assert normalize_language_code("英语") == "en"
        assert normalize_language_code("english") == "en"
        assert normalize_language_code("Russian") == "ru"
        assert normalize_language_code("日语") == "ja"

    def test_codes_pass_through(self):
        assert normalize_language_code("EN") == "en"
        assert normalize_language_code("pt-BR") == "pt-br"

    def test_display_name_lookup(self):
        assert language_display_name("ru") == "俄语"
        assert language_display_name("英语") == "英语"


class TestReplySuggestion:
    def test_russian_message_suggests_ru(self):
        assert suggest_reply_language("привет как дела") == "ru"

    def test_japanese_message_suggests_ja(self):
        assert suggest_reply_language("こんにちは") == "ja"

    def test_korean_message_suggests_ko(self):
        assert suggest_reply_language("안녕") == "ko"

    def test_chinese_message_no_suggestion(self):
        assert suggest_reply_language("冲B点") is None

    def test_english_message_no_suggestion(self):
        # 英语无法与德/法等拉丁语言可靠区分，不给建议
        assert suggest_reply_language("hello team") is None

    def test_empty_no_suggestion(self):
        assert suggest_reply_language("") is None
        assert suggest_reply_language(None) is None
