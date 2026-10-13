"""常用战术短语收藏测试（新增功能）。"""

from __future__ import annotations

import json

import pytest

from cs2_translator.phrases import DEFAULT_PHRASES, PhraseBook, TacticalPhrase


@pytest.fixture
def book(tmp_path):
    return PhraseBook(tmp_path / "phrases.json")


class TestDefaults:
    def test_builtin_phrases_available(self, book):
        phrases = book.all()
        assert len(phrases) >= 15
        zh_texts = {p.zh for p in phrases}
        for expected in ("冲B点", "保枪", "转点", "干得漂亮"):
            assert expected in zh_texts

    def test_builtin_has_translations(self, book):
        rush = next(p for p in book.all() if p.zh == "冲B点")
        assert rush.translations["en"] == "rush B"
        assert rush.translations["ru"] == "раш Б"
        assert rush.translations["ja"] == "Bラッシュ"
        assert rush.translations["ko"] == "B 러시"

    def test_translation_for_language(self, book):
        phrase = next(p for p in book.all() if p.zh == "保枪")
        assert PhraseBook.translation_for(phrase, "en") == "save"
        assert PhraseBook.translation_for(phrase, "ru") == "сейв"
        assert PhraseBook.translation_for(phrase, "xx") is None


class TestUserPhrases:
    def test_add_and_persist(self, tmp_path):
        path = tmp_path / "phrases.json"
        book = PhraseBook(path)
        book.add("小心二楼", {"en": "watch upper"}, category="自定义")
        assert any(p.zh == "小心二楼" for p in book.all())

        # 重新加载后仍在
        reloaded = PhraseBook(path)
        phrase = next((p for p in reloaded.all() if p.zh == "小心二楼"), None)
        assert phrase is not None
        assert phrase.translations["en"] == "watch upper"
        assert phrase.builtin is False

    def test_add_duplicate_updates_translation(self, book):
        book.add("我的短语", {"en": "first"})
        book.add("我的短语", {"ru": "второй"})
        phrase = next(p for p in book.all() if p.zh == "我的短语")
        assert phrase.translations["en"] == "first"
        assert phrase.translations["ru"] == "второй"

    def test_add_builtin_name_creates_user_copy(self, book):
        book.add("冲B点", {"en": "custom rush"})
        matches = [p for p in book.all() if p.zh == "冲B点"]
        assert len(matches) == 2
        user_copy = next(p for p in matches if not p.builtin)
        assert user_copy.translations["en"] == "custom rush"
        assert user_copy.translations["ru"] == "раш Б"  # 保留内置译文

    def test_remove_user_phrase(self, book):
        phrase = book.add("临时短语")
        assert book.remove(phrase.phrase_id) is True
        assert not any(p.zh == "临时短语" for p in book.all())

    def test_cannot_remove_builtin(self, book):
        builtin = next(p for p in book.all() if p.builtin)
        assert book.remove(builtin.phrase_id) is False
        assert any(p.zh == builtin.zh for p in book.all())

    def test_remove_unknown_returns_false(self, book):
        assert book.remove("nope") is False

    def test_empty_phrase_rejected(self, book):
        with pytest.raises(ValueError):
            book.add("   ")

    def test_categories(self, book):
        cats = book.categories()
        assert "战术" in cats

    def test_corrupt_file_recovers(self, tmp_path):
        path = tmp_path / "phrases.json"
        path.write_text("{ 不是合法 JSON", encoding="utf-8")
        book = PhraseBook(path)
        # 内置短语仍然可用
        assert any(p.zh == "冲B点" for p in book.all())
