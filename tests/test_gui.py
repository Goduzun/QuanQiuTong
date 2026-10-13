"""GUI 测试（offscreen 离屏渲染）：主窗口、回复面板、设置对话框。"""

from __future__ import annotations

import pytest
from PySide6.QtGui import QGuiApplication

from cs2_translator.config import AppConfig, ConfigManager
from cs2_translator.phrases import PhraseBook
from cs2_translator.ui.main_window import MainWindow
from cs2_translator.ui.reply_panel import ReplyPanel
from cs2_translator.ui.settings_dialog import SettingsDialog


@pytest.fixture
def window_config(tmp_path) -> AppConfig:
    return AppConfig(
        cs2_log_path=str(tmp_path / "console.log"),
        api_key="sk-gui-test",
        model="gpt-4o-mini",
        rate_limit_per_minute=600,
        log_poll_interval=0.05,
    )


class TestMainWindow:
    """新版外壳：左侧导航 + 顶部状态栏 + 页面堆栈。"""

    def _make(self, tmp_path, window_config):
        manager = ConfigManager(data_dir=tmp_path / "data")
        window = MainWindow(window_config, manager)
        window.show()
        return window

    def test_constructs_and_shuts_down(self, qapp, tmp_path, window_config):
        from cs2_translator import APP_DISPLAY_NAME

        window = self._make(tmp_path, window_config)
        try:
            assert window.windowTitle() == APP_DISPLAY_NAME
            # 五个页面全部就位
            assert set(window.pages) == {"live", "history", "memory", "glossary", "settings"}
            assert window.stack.count() == 5
            assert window.controller.engine._running is True
        finally:
            # 必须确保引擎停止：否则遗留的 translate-* 线程会污染
            # test_queue_manager 的全局线程计数断言
            window._really_quit = True
            window.close()
        assert window.controller.engine._running is False

    def test_navigation_switches_every_page(self, qapp, tmp_path, window_config):
        window = self._make(tmp_path, window_config)
        try:
            from cs2_translator.ui.main_window import NAV_ITEMS

            for key, _label, _icon in NAV_ITEMS:
                window._switch_page(key)
                assert window.stack.currentWidget() is window.pages[key]
                assert window.nav_buttons[key].isChecked() is True
        finally:
            window._really_quit = True
            window.close()

    def test_minimum_size_prevents_overflow(self, qapp, tmp_path, window_config):
        window = self._make(tmp_path, window_config)
        try:
            assert window.minimumWidth() >= 900
            assert window.minimumHeight() >= 600
            # 缩小到最小尺寸后仍应有有效几何
            window.resize(window.minimumSize())
            assert window.width() >= window.minimumWidth()
            assert window.height() >= window.minimumHeight()
        finally:
            window._really_quit = True
            window.close()

    def test_incoming_message_renders_in_stream(self, qapp, tmp_path, window_config):
        from cs2_translator.chat_parser import ChatMessage

        window = self._make(tmp_path, window_config)
        try:
            message = ChatMessage(sender="Bot David", content="bonjour", channel="ALL")
            window._on_chat(message)
            rows = window.live_page.model.rows()
            assert len(rows) == 1
            assert rows[0]["sender"] == "Bot David"
            assert rows[0]["source"] == "bonjour"
            assert rows[0]["state"] == "pending"

            window._on_translation(message, "你好", 120.0, None, False, {"hit_kind": "api"})
            assert rows[0]["translated"] == "你好"
            assert rows[0]["state"] == "done"

            window._on_translation(message, "bonjour", 10.0, None, True, {"hit_kind": "skip"})
            assert rows[0]["translated"] == "bonjour"
            assert rows[0]["state"] == "skipped"
        finally:
            window._really_quit = True
            window.close()

    def test_stale_result_is_discarded(self, qapp, tmp_path, window_config):
        """上一局的迟到结果不得写入新一局的消息流。"""
        from cs2_translator.chat_parser import ChatMessage

        window = self._make(tmp_path, window_config)
        try:
            message = ChatMessage(sender="A", content="rush B", channel="ALL")
            window._on_chat(message)
            window._on_translation(
                message, "冲B", 50.0, None, False, {"hit_kind": "api", "stale": True}
            )
            rows = window.live_page.model.rows()
            # 迟到的过期结果被丢弃，该行仍停留在待翻译状态
            assert rows[0]["state"] == "pending"
            assert rows[0]["translated"] == ""
        finally:
            window._really_quit = True
            window.close()

    def test_search_filter_hides_non_matching_rows(self, qapp, tmp_path, window_config):
        from cs2_translator.chat_parser import ChatMessage

        window = self._make(tmp_path, window_config)
        try:
            window._on_chat(ChatMessage(sender="Alpha", content="hello"))
            window._on_chat(ChatMessage(sender="Beta", content="world"))
            window.live_page.search_edit.setText("Alpha")
            assert window.live_page.list_view.isRowHidden(0) is False
            assert window.live_page.list_view.isRowHidden(1) is True
            window.live_page.search_edit.setText("")
            assert window.live_page.list_view.isRowHidden(0) is False
            assert window.live_page.list_view.isRowHidden(1) is False
        finally:
            window._really_quit = True
            window.close()

    def test_clear_view_empties_stream(self, qapp, tmp_path, window_config):
        from cs2_translator.chat_parser import ChatMessage

        window = self._make(tmp_path, window_config)
        try:
            window._on_chat(ChatMessage(sender="A", content="x"))
            assert len(window.live_page.model.rows()) == 1
            window.live_page.clear_stream()
            assert window.live_page.model.rows() == []
        finally:
            window._really_quit = True
            window.close()

    def test_long_message_gets_taller_row(self, qapp, tmp_path, window_config):
        """长消息必须换行显示（委托 sizeHint 随内容增高）。"""
        from PySide6.QtCore import QSize
        from PySide6.QtWidgets import QStyleOptionViewItem

        from cs2_translator.chat_parser import ChatMessage

        window = self._make(tmp_path, window_config)
        try:
            short = ChatMessage(sender="A", content="hi")
            long_text = "this is a very long chat message " * 12
            long = ChatMessage(sender="A", content=long_text)
            window._on_chat(short)
            window._on_chat(long)

            option = QStyleOptionViewItem()
            option.rect = window.live_page.list_view.viewport().rect()
            option.font = window.live_page.list_view.font()
            h_short = window.live_page.delegate.sizeHint(
                option, window.live_page.model.index(0, 0)
            ).height()
            h_long = window.live_page.delegate.sizeHint(
                option, window.live_page.model.index(1, 0)
            ).height()
            assert h_long > h_short, f"长消息未增高: {h_long} vs {h_short}"
        finally:
            window._really_quit = True
            window.close()

    def test_copy_routes_to_clipboard(self, qapp, tmp_path, window_config):
        window = self._make(tmp_path, window_config)
        try:
            window._on_copy_text("copy-me-123")
            assert QGuiApplication.clipboard().text() == "copy-me-123"
        finally:
            window._really_quit = True
            window.close()

    def test_error_state_is_surfaced(self, qapp, tmp_path, window_config):
        from cs2_translator.chat_parser import ChatMessage
        from cs2_translator.translator import TranslatorError

        window = self._make(tmp_path, window_config)
        try:
            message = ChatMessage(sender="A", content="hello")
            window._on_chat(message)
            err = TranslatorError("网络连接失败", kind="network")
            window._on_translation(message, "", 5.0, err, False, {"hit_kind": "error"})
            row = window.live_page.model.rows()[0]
            assert row["state"] == "error"
            assert "网络连接失败" in row["translated"]
        finally:
            window._really_quit = True
            window.close()

    def test_message_limit_enforced(self, qapp, tmp_path, window_config):
        """达到配置上限后淘汰最旧显示项，界面不会无限增长。"""
        from cs2_translator.chat_parser import ChatMessage

        window_config.match_message_limit = 60
        window = self._make(tmp_path, window_config)
        try:
            for i in range(90):
                window._on_chat(ChatMessage(sender="P", content=f"msg {i}"))
            rows = window.live_page.model.rows()
            assert len(rows) == 60
            assert rows[-1]["source"] == "msg 89"
            assert rows[0]["source"] == "msg 30"
        finally:
            window._really_quit = True
            window.close()

    def test_new_match_clears_stream_but_keeps_memory(self, qapp, tmp_path, window_config):
        """换局后界面清空，但长期翻译记忆保留。"""
        from cs2_translator.chat_parser import ChatMessage

        window = self._make(tmp_path, window_config)
        try:
            memory = window.context.memory
            memory.save_translation(
                text="rush B", target_language="zh",
                direction="incoming", translated="冲B点",
            )
            before = memory.count_memory()

            window._on_chat(ChatMessage(sender="A", content="first match msg"))
            assert len(window.live_page.model.rows()) == 1

            window._on_new_match()
            assert window.live_page.model.rows() == []
            # 长期翻译记忆不变，且仍能命中
            assert memory.count_memory() == before
            assert memory.lookup("rush B", "zh", "incoming").translated == "冲B点"
        finally:
            window._really_quit = True
            window.close()

    def test_status_bar_binds_real_data(self, qapp, tmp_path, window_config):
        """顶部状态必须来自真实数据源，不是写死的占位。"""
        window = self._make(tmp_path, window_config)
        try:
            window._refresh_status()
            # 日志胶囊显示真实文件名与行数（未配置时显示提示）
            assert window._top_log.text() != ""
            # 对局胶囊显示真实 match_id 的短标识
            match_id = window.context.matches.current.match_id
            assert match_id[-6:] in window._top_match.text()
            # 完整 id 在悬停提示中可见
            assert match_id in window._top_match.parentWidget().toolTip()
            # 目标语言来自配置
            assert window_config.reply_target_language in window._top_target.text()
        finally:
            window._really_quit = True
            window.close()

    def test_settings_page_collect_and_validate(self, qapp, tmp_path, window_config):
        window = self._make(tmp_path, window_config)
        try:
            page = window.pages["settings"]
            page.reload()
            collected = page._collect()
            assert collected.cs2_log_path == window_config.cs2_log_path
            assert page._validate(collected) == []
            page.log_path_edit.setText("")
            assert page._validate(page._collect())
        finally:
            window._really_quit = True
            window.close()

    def test_settings_save_roundtrip(self, qapp, tmp_path, window_config):
        window = self._make(tmp_path, window_config)
        try:
            page = window.pages["settings"]
            page.log_path_edit.setText(str(tmp_path / "console.log"))
            page.model_edit.setText("test-model")
            page.match_limit_spin.setValue(123)
            page.history_limit_spin.setValue(7)
            saved = []
            page.saved.connect(lambda cfg: saved.append(cfg))
            page._on_save()
            assert saved, "保存未发出信号"
            cfg = saved[0]
            assert cfg.model == "test-model"
            assert cfg.match_message_limit == 123
            assert cfg.history_match_limit == 7
            # 配置确实落盘且可重新读取
            reloaded = window.config_manager.load()
            assert reloaded.model == "test-model"
            assert reloaded.match_message_limit == 123
        finally:
            window._really_quit = True
            window.close()

    def test_theme_switch_generates_stylesheet(self, qapp, tmp_path, window_config):
        from cs2_translator.ui.design import build_stylesheet, palette_for

        dark = build_stylesheet(palette_for("dark"))
        light = build_stylesheet(palette_for("light"))
        assert dark != light
        assert palette_for("dark").bg_surface == "#14161b"
        assert palette_for("light").bg_surface == "#ffffff"


class TestReplyPanel:
    @pytest.fixture
    def panel(self, qapp, tmp_path):
        return ReplyPanel(PhraseBook(tmp_path / "phrases.json"))

    def test_translate_request_emits_text_and_language(self, panel):
        captured = []
        panel.translate_requested.connect(lambda text, lang: captured.append((text, lang)))
        panel.input_edit.setPlainText("冲B点，跟我走")
        panel.lang_combo.setCurrentIndex(0)  # 英语
        panel.translate_button.click()
        assert captured == [("冲B点，跟我走", "en")]

    def test_language_switching(self, panel):
        assert panel.current_target_language() == "en"
        panel.set_target_language("ru")
        assert panel.current_target_language() == "ru"
        panel.set_target_language("ja")
        assert panel.current_target_language() == "ja"
        panel.set_target_language("ko")
        assert panel.current_target_language() == "ko"

    def test_suggestion_respects_manual_override(self, panel):
        panel.auto_check.setChecked(False)
        panel.set_target_language("en")
        panel.apply_suggestion("ru")
        # 用户手动选择英语后，自动建议不应强行改回俄语
        assert panel.current_target_language() == "en"

    def test_suggestion_applies_in_auto_mode(self, panel):
        panel.auto_check.setChecked(True)
        panel.set_target_language("en")
        panel.apply_suggestion("ru")
        assert panel.current_target_language() == "ru"

    def test_success_shows_original_and_translation(self, panel):
        panel.show_result(
            {
                "ok": True,
                "original": "冲B点",
                "translated": "rush B",
                "target": "en",
                "error": None,
                "latency_ms": 130.5,
            }
        )
        assert "冲B点" in panel.original_label.text()
        assert "rush B" in panel.translated_label.text()
        assert panel.copy_button.isEnabled() is True
        assert "完成" in panel.status_label.text()

    def test_copy_translation_to_clipboard(self, panel):
        panel.show_result(
            {
                "ok": True,
                "original": "保枪",
                "translated": "save",
                "target": "en",
                "latency_ms": 10,
            }
        )
        assert panel.copy_translation() is True
        clipboard = QGuiApplication.clipboard()
        assert clipboard.text() == "save"

    def test_failure_keeps_draft(self, panel):
        panel.input_edit.setPlainText("转点，快点")
        panel.show_result(
            {
                "ok": False,
                "original": "转点，快点",
                "translated": None,
                "target": "ru",
                "error": "网络连接失败",
                "latency_ms": 5,
            }
        )
        # 草稿必须保留
        assert panel.draft_text() == "转点，快点"
        assert "失败" in panel.status_label.text()
        assert "草稿已保留" in panel.status_label.text()
        assert panel.copy_button.isEnabled() is False

    def test_busy_state_blocks_double_submit(self, panel):
        panel.set_busy(True)
        assert panel.translate_button.isEnabled() is False
        panel.set_busy(False)
        assert panel.translate_button.isEnabled() is True

    def test_empty_input_not_submitted(self, panel):
        captured = []
        panel.translate_requested.connect(lambda text, lang: captured.append((text, lang)))
        panel.translate_button.click()
        assert captured == []
        assert "请输入" in panel.status_label.text()

    def test_phrase_quick_insert(self, panel):
        panel.phrase_book.add("小心二楼", {"en": "watch upper"})
        panel.reload_phrases()
        item = None
        for row in range(panel.phrase_list.count()):
            candidate = panel.phrase_list.item(row)
            if candidate.text().startswith("小心二楼"):
                item = candidate
                break
        assert item is not None
        panel._on_phrase_double_clicked(item)
        assert panel.input_edit.toPlainText() == "watch upper"

    def test_learn_translation_updates_user_phrase(self, panel):
        phrase = panel.phrase_book.add("我的口令")
        panel.learn_translation("我的口令", "de", "mein Kommando")
        assert phrase.translations["de"] == "mein Kommando"

    def test_learn_translation_skips_builtin(self, panel):
        panel.learn_translation("冲B点", "de", "Sturm B")
        builtin = next(p for p in panel.phrase_book.all() if p.zh == "冲B点")
        assert "de" not in builtin.translations

    def test_add_current_as_phrase(self, panel, monkeypatch):
        from PySide6.QtWidgets import QInputDialog

        monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("我的分类", True))
        panel.input_edit.setPlainText("拉出去打")
        panel.add_current_as_phrase()
        assert any(p.zh == "拉出去打" for p in panel.phrase_book.all())

    def test_clear_input(self, panel):
        panel.input_edit.setPlainText("测试")
        panel.clear_input()
        assert panel.draft_text() == ""
        assert panel.translated_label.text() == "译文：-"


class TestSettingsDialog:
    def test_validation_flags_missing_fields(self, qapp, tmp_path):
        config = AppConfig()
        dialog = SettingsDialog(ConfigManager(data_dir=tmp_path / "data"), config)
        dialog.log_path_edit.setText("")
        dialog.api_key_edit.setText("")
        errors = dialog._validate(dialog._collect())
        assert any("日志" in e for e in errors)
        assert any("API Key" in e for e in errors)

    def test_collect_roundtrip(self, qapp, tmp_path):
        config = AppConfig(cs2_log_path="/x", api_key="sk-1", reply_target_language="ru")
        dialog = SettingsDialog(ConfigManager(data_dir=tmp_path / "data"), config)
        collected = dialog._collect()
        assert collected.cs2_log_path == "/x"
        assert collected.api_key == "sk-1"
        assert collected.reply_target_language == "ru"
        assert collected.first_run_completed is True

    def test_collect_reflects_ui_changes(self, qapp, tmp_path):
        config = AppConfig()
        dialog = SettingsDialog(ConfigManager(data_dir=tmp_path / "data"), config)
        dialog.log_path_edit.setText("/new/path.log")
        dialog.api_key_edit.setText("sk-new")
        dialog.reply_lang_combo.setCurrentIndex(2)  # 日语
        dialog.auto_suggest_check.setChecked(False)
        collected = dialog._collect()
        assert collected.cs2_log_path == "/new/path.log"
        assert collected.reply_target_language == "ja"
        assert collected.auto_suggest_reply_language is False

    def test_save_writes_config(self, qapp, tmp_path):
        manager = ConfigManager(data_dir=tmp_path / "data")
        config = AppConfig(cs2_log_path="/saved.log", api_key="sk-save")
        dialog = SettingsDialog(manager, config)
        dialog._on_save()
        assert manager.config_path.is_file()
        loaded = manager.load()
        assert loaded.cs2_log_path == "/saved.log"
        assert loaded.api_key == "sk-save"
        raw = manager.config_path.read_text(encoding="utf-8")
        assert "sk-save" not in raw
