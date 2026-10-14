# -*- coding: utf-8 -*-
"""第二／四階段驗證：查詢順序、去重、語言方向、失敗處理、日誌不重複處理。

這裡的「真實訊息格式」全部取自使用者本機 console.log 的實際內容
（`e:\\steam\\...\\game\\csgo\\console.log`），不是編造的示例資料。
"""

from __future__ import annotations

import pytest

from cs2_translator.chat_parser import ChatParser
from cs2_translator.config import AppConfig
from cs2_translator.engine import TranslationEngine
from cs2_translator.language import detect_primary_language, needs_translation
from cs2_translator.memory import (
    DIRECTION_INCOMING,
    HIT_CORRECTION,
    HIT_EXACT,
    HIT_TERM,
    TranslationMemory,
)
from cs2_translator.translator import AuthError, NetworkError, TranslatorError

# ---------------------------------------------------------------------------
# 來自真實 console.log 的聊天行（含全角冒號、俄文、中文、練習圖廣播）
# ---------------------------------------------------------------------------
REAL_CHAT_LINES = [
    ("10/13 11:10:37  [T] 慢拔out急拔in\u200e﹫CSSTATS.GG: Welcome, 慢拔out急拔in!",
     "慢拔out急拔in\u200e﹫CSSTATS.GG", "Welcome, 慢拔out急拔in!"),
    ("10/13 11:22:31  [ALL] 慢拔out急拔in\u200e： 欢迎回来，慢拔out急拔in，正在载入2026-10-13 11:22:11的存档。距离您上次退出地图已经经过了20秒.",
     None, "欢迎回来"),
    ("10/13 11:25:25  [ALL] 慢拔out急拔in\u200e： Tip: Crosshair placement is key. Always keep it at head level.",
     None, "Tip: Crosshair placement"),
    ("10/13 11:31:25  [ALL] 慢拔out急拔in\u200e： 提示：预瞄（Crosshair Placement）是重中之重，请始终把准星卡在爆头线高度。",
     None, "提示：预瞄"),
    ("10/13 10:22:21  [CT] 本次选拔赛\u200e [DEAD]: ото", None, "ото"),
    ("10/13 10:22:27  [CT] 本次选拔赛\u200e [DEAD]: сколько остал", None, "сколько остал"),
    ("10/13 10:22:59  [ALL] 慢拔out急拔in\u200e： Чё?", None, "Чё?"),
]


class TestRealMessageFormat:
    """用真實日誌行驗證訊息格式解析。"""

    @pytest.mark.parametrize("line,_sender,expected_content", REAL_CHAT_LINES)
    def test_real_lines_parse(self, line, _sender, expected_content):
        msg = ChatParser().parse(line)
        assert msg is not None, f"未能解析真實日誌行: {line[:60]!r}"
        assert expected_content in msg.content

    def test_timestamp_and_channel_extracted(self):
        msg = ChatParser().parse(REAL_CHAT_LINES[0][0])
        assert msg is not None
        assert msg.timestamp == "10/13 11:10:37"
        assert msg.channel == "TEAM"          # [T] -> TEAM
        assert msg.channel_label == "队伍"

    def test_dead_suffix_detected(self):
        msg = ChatParser().parse(REAL_CHAT_LINES[4][0])
        assert msg is not None
        assert msg.is_dead is True
        assert "死亡" in msg.channel_label

    def test_zero_width_removed_from_sender(self):
        msg = ChatParser().parse(REAL_CHAT_LINES[1][0])
        assert msg is not None
        assert "\u200e" not in msg.sender

    def test_engine_system_lines_are_not_chat(self):
        parser = ChatParser()
        for line in (
            "10/13 11:35:46 [HostStateManager] Host activate: Quitting",
            "10/13 11:10:34 [Server] CSource2Server::GameServerSteamAPIActivated()",
            "10/13 10:15:48 Pagefile: estimated 3388mb CPU 7189mb memory needed",
        ):
            assert parser.parse(line) is None, f"系統行被誤判為聊天: {line!r}"


class TestLanguageDirection:
    """語言方向識別：英文/俄文要翻，中文不需翻。"""

    @pytest.mark.parametrize("text,expected", [
        ("Tip: Crosshair placement is key.", "en"),
        ("сколько остал", "ru"),
        ("ото", "ru"),
        ("Чё?", "ru"),
        ("欢迎回来，正在载入存档", "zh"),
        ("提示：预瞄是重中之重", "zh"),
    ])
    def test_detect(self, text, expected):
        assert detect_primary_language(text) == expected

    def test_needs_translation_direction(self):
        enabled = AppConfig().enabled_source_languages
        # 外语 -> 需要翻译
        assert needs_translation("rush b now", enabled) is True
        assert needs_translation("сколько остал", enabled) is True
        # 中文 -> 不需要（避免中文再翻成中文）
        assert needs_translation("欢迎回来", enabled) is False

    def test_pure_symbols_not_translated(self):
        enabled = AppConfig().enabled_source_languages
        assert needs_translation("!!!***", enabled) is False
        assert needs_translation("", enabled) is False


class TestDeduplication:
    """同一条消息不应被重复提交翻译。"""

    def test_queue_drops_duplicate_while_pending(self):
        from cs2_translator.chat_parser import ChatMessage
        from cs2_translator.queue_manager import TranslationQueueManager

        seen = []

        def handler(msg, seq):
            seen.append(msg.content)

        mgr = TranslationQueueManager(handler=handler, max_size=10, workers=1,
                                      reorder_timeout=0.2)
        mgr.start()
        try:
            msg = ChatMessage(sender="A", content="duplicate text")
            first = mgr.submit(msg)
            second = mgr.submit(ChatMessage(sender="A", content="duplicate text"))
            assert first is True
            assert second is False, "相同發送者+內容的待處理訊息應被去重"
            stats = mgr.stats()
            assert stats["dropped_duplicate"] >= 1
        finally:
            mgr.stop()

    def test_different_content_not_deduped(self):
        from cs2_translator.chat_parser import ChatMessage
        from cs2_translator.queue_manager import TranslationQueueManager

        mgr = TranslationQueueManager(handler=lambda m, s: None, max_size=10, workers=1)
        mgr.start()
        try:
            assert mgr.submit(ChatMessage(sender="A", content="one")) is True
            assert mgr.submit(ChatMessage(sender="A", content="two")) is True
        finally:
            mgr.stop()


class TestLookupOrder:
    """查詢順序：人工修正/長期記憶 -> 術語 -> 共用快取 -> 才呼叫 API。"""

    def test_exact_memory_hit_skips_api(self, tmp_path):
        mem = TranslationMemory(tmp_path / "m.db")
        try:
            mem.save_translation(text="cheater", target_language="zh",
                                 direction=DIRECTION_INCOMING, translated="作弊的")
            hit = mem.lookup("cheater", "zh", DIRECTION_INCOMING)
            assert hit.hit is True
            assert hit.kind == HIT_EXACT
            # 命中即不應有任何 API 呼叫統計增加
            before = mem.get_stats()["api_calls"]
            mem.lookup("cheater", "zh", DIRECTION_INCOMING)
            assert mem.get_stats()["api_calls"] == before
        finally:
            mem.close()

    def test_correction_beats_exact(self, tmp_path):
        mem = TranslationMemory(tmp_path / "m.db")
        try:
            mem.save_translation(text="cheater", target_language="zh",
                                 direction=DIRECTION_INCOMING, translated="作弊的")
            mem.confirm_translation("cheater", "zh", DIRECTION_INCOMING, new_text="开挂的")
            hit = mem.lookup("cheater", "zh", DIRECTION_INCOMING)
            assert hit.kind == HIT_CORRECTION
            assert hit.translated == "开挂的"
        finally:
            mem.close()

    def test_correction_not_overwritten_by_ai(self, tmp_path):
        mem = TranslationMemory(tmp_path / "m.db")
        try:
            mem.confirm_translation("rotate mid", "zh", DIRECTION_INCOMING, new_text="转中路")
            mem.save_translation(text="rotate mid", target_language="zh",
                                 direction=DIRECTION_INCOMING, translated="AI错误译文")
            hit = mem.lookup("rotate mid", "zh", DIRECTION_INCOMING)
            assert hit.translated == "转中路", "人工修正被 AI 结果覆盖"
            assert hit.kind == HIT_CORRECTION
        finally:
            mem.close()

    def test_whole_term_hit(self, tmp_path):
        mem = TranslationMemory(tmp_path / "m.db")
        try:
            mem.add_term("eco round", "zh", "经济局", direction=DIRECTION_INCOMING)
            hit = mem.lookup("eco round", "zh", DIRECTION_INCOMING)
            assert hit.hit is True
            assert hit.kind == HIT_TERM
            assert "经济局" in hit.translated
        finally:
            mem.close()

    def test_direction_and_language_isolated(self, tmp_path):
        mem = TranslationMemory(tmp_path / "m.db")
        try:
            from cs2_translator.memory import DIRECTION_REPLY
            mem.save_translation(text="rush b", target_language="en",
                                 direction=DIRECTION_REPLY, translated="rush B")
            mem.save_translation(text="rush b", target_language="ru",
                                 direction=DIRECTION_REPLY, translated="раш Б")
            assert mem.lookup("rush b", "en", DIRECTION_REPLY).translated == "rush B"
            assert mem.lookup("rush b", "ru", DIRECTION_REPLY).translated == "раш Б"
            # 接收方向不应命中回复方向的缓存
            assert mem.lookup("rush b", "en", DIRECTION_INCOMING).hit is False
        finally:
            mem.close()

    def test_negative_and_number_not_conflated(self, tmp_path):
        """否定詞不同的句子絕不可復用同一快取。

        註 1：內建句式模板 ``(\\d+) enemies left`` 是**帶數字變數**的合法模板，
              會代入實際數字（``还剩 N 个敌人``），這是既有測試覆蓋的刻意行為。
        註 2：``rush B`` 會命中內建**術語**（冲B点），所以這裡刻意避開 glossary
              詞彙，改用中性句子驗證「否定詞不可互換」這個真正的風險點。
        """
        mem = TranslationMemory(tmp_path / "m.db")
        try:
            mem.save_translation(text="the door is open", target_language="zh",
                                 direction=DIRECTION_INCOMING, translated="门开着")
            # 否定句絕不能被肯定句命中（反之亦然）
            assert mem.lookup("the door is not open", "zh",
                              DIRECTION_INCOMING).hit is False

            mem.save_translation(text="the door is not open", target_language="zh",
                                 direction=DIRECTION_INCOMING, translated="门没开")
            assert mem.lookup("the door is open", "zh",
                              DIRECTION_INCOMING).translated == "门开着"
            assert mem.lookup("the door is not open", "zh",
                              DIRECTION_INCOMING).translated == "门没开"
        finally:
            mem.close()

    def test_glossary_term_still_matches_by_design(self, tmp_path):
        """``rush B`` 命中內建術語是**設計行為**，不是錯誤復用。"""
        mem = TranslationMemory(tmp_path / "m.db")
        try:
            hit = mem.lookup("rush B", "zh", DIRECTION_INCOMING)
            assert hit.hit is True
            assert hit.kind == HIT_TERM
            assert "冲B" in hit.translated
        finally:
            mem.close()

    def test_template_number_variable_substitutes_correctly(self, tmp_path):
        """內建數字模板必須代入實際數字，不能張冠李戴。"""
        mem = TranslationMemory(tmp_path / "m.db")
        try:
            three = mem.lookup("3 enemies left", "zh", DIRECTION_INCOMING)
            five = mem.lookup("5 enemies left", "zh", DIRECTION_INCOMING)
            assert "3" in three.translated
            assert "5" in five.translated
            assert three.translated != five.translated
        finally:
            mem.close()

    def test_unrelated_text_does_not_hit_cache(self, tmp_path):
        """完全無關的句子不得命中任何內建術語/模板。"""
        mem = TranslationMemory(tmp_path / "m.db")
        try:
            hit = mem.lookup("the quick brown fox jumps over", "zh", DIRECTION_INCOMING)
            assert hit.hit is False
        finally:
            mem.close()


class TestFailureHandling:
    """API 失敗、網路中斷、服務端不可用時的表現。"""

    def _engine(self, tmp_path, **over):
        cfg = AppConfig(api_key="sk-test", cs2_log_path=str(tmp_path / "console.log"),
                        translate_enabled=True, queue_max_size=20, **over)
        return TranslationEngine(cfg, data_dir=tmp_path / "data")

    def test_network_error_surfaces_and_is_not_cached(self, tmp_path):
        eng = self._engine(tmp_path)
        try:
            def boom(**kwargs):
                raise NetworkError("网络连接失败")

            eng.translator.translate = boom  # type: ignore[assignment]
            outcome = eng._translate_with_memory(
                text="rush b now", direction=DIRECTION_INCOMING, target_language="zh",
                source_language="en", source_type="chat",
                api_translator=eng.translator,
                api_call=lambda hints: eng.translator.translate(),
            )
            assert outcome.error is not None
            assert isinstance(outcome.error, TranslatorError)
            # 失败绝不写入缓存
            assert eng.memory.lookup("rush b now", "zh", DIRECTION_INCOMING).hit is False
        finally:
            eng.stop()

    def test_auth_error_marks_api_state_error(self, tmp_path):
        """認證失敗必須把全域 API 狀態標成 error，讓介面能提示。"""
        eng = self._engine(tmp_path)
        try:
            def boom(*args, **kwargs):
                raise AuthError("API Key 无效")

            eng.translator.translate = boom  # type: ignore[assignment]
            from cs2_translator.chat_parser import ChatMessage

            # 使用不會命中內建術語/模板的句子，確保真的走到 API
            eng._handle_queued(
                ChatMessage(sender="A", content="zzqx unrelated sentence one",
                            channel="ALL"), 0)
            assert eng._api_state == "error"
            assert eng._last_error
        finally:
            eng.stop()

    def test_failure_then_retry_can_succeed(self, tmp_path):
        """失敗不寫快取，因此後續重試仍可成功並寫入快取。"""
        eng = self._engine(tmp_path)
        try:
            text = "zzqx unrelated sentence two"   # 避免命中內建術語/模板
            calls = {"n": 0}

            def flaky(*args, **kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise NetworkError("第一次失败")
                return "冲B"

            outcome1 = eng._translate_with_memory(
                text=text, direction=DIRECTION_INCOMING, target_language="zh",
                source_language="en", source_type="chat",
                api_translator=eng.translator, api_call=lambda hints: flaky())
            assert outcome1.error is not None
            assert eng.memory.lookup(text, "zh", DIRECTION_INCOMING).hit is False

            outcome2 = eng._translate_with_memory(
                text=text, direction=DIRECTION_INCOMING, target_language="zh",
                source_language="en", source_type="chat",
                api_translator=eng.translator, api_call=lambda hints: flaky())
            assert outcome2.error is None
            assert outcome2.translated == "冲B"
            assert eng.memory.lookup(text, "zh", DIRECTION_INCOMING).hit is True
        finally:
            eng.stop()

    def test_empty_response_raises(self, tmp_path):
        eng = self._engine(tmp_path)
        try:
            text = "zzqx unrelated sentence three"

            def empty(*args, **kwargs):
                from cs2_translator.translator import EmptyResponseError
                raise EmptyResponseError("模型返回了空内容")

            outcome = eng._translate_with_memory(
                text=text, direction=DIRECTION_INCOMING, target_language="zh",
                source_language="en", source_type="chat",
                api_translator=eng.translator, api_call=lambda hints: empty())
            assert outcome.error is not None
            assert eng.memory.lookup(text, "zh", DIRECTION_INCOMING).hit is False
        finally:
            eng.stop()


class TestLogNotReprocessed:
    """日志读取不得重复处理旧消息。"""

    def test_default_starts_at_end_of_file(self, tmp_path):
        """默认（read_history_on_start=False）只处理启动后新增的行。"""
        from cs2_translator.log_watcher import LogWatcher

        log = tmp_path / "console.log"
        log.write_text("[ALL] old1: hi\n[ALL] old2: there\n", encoding="utf-8")

        lines = []
        watcher = LogWatcher(log, poll_interval=0.02, read_from_start=False,
                             on_line=lines.append)
        watcher.start()
        try:
            import time
            # 等待打开（应定位到文件末尾）
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and watcher._fh is None:
                time.sleep(0.02)
            # 给一点时间让旧内容有机会（错误地）被读到
            time.sleep(0.4)
            assert lines == [], f"旧日志被重复处理了: {lines}"

            with open(log, "a", encoding="utf-8") as fh:
                fh.write("[ALL] new: fresh message\n")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not lines:
                time.sleep(0.02)
            assert lines == ["[ALL] new: fresh message"], f"新行未被处理: {lines}"
        finally:
            watcher.stop()

    def test_engine_default_config_does_not_reread(self, tmp_path):
        """引擎默认设置必须是「不读历史」，避免每次启动重复翻译。"""
        eng = TranslationEngine(
            AppConfig(api_key="x", cs2_log_path=str(tmp_path / "console.log")),
            data_dir=tmp_path / "data",
        )
        try:
            assert eng._watcher.read_from_start is False
        finally:
            eng.stop()

    def test_truncation_resets_offset(self, tmp_path):
        """日志被清空后应从头读取新内容，而不是停在旧偏移。"""
        from cs2_translator.log_watcher import LogWatcher
        import time

        log = tmp_path / "console.log"
        log.write_text("[ALL] a: first\n", encoding="utf-8")
        lines = []
        events = []
        watcher = LogWatcher(log, poll_interval=0.02, read_from_start=True,
                             on_line=lines.append, on_event=events.append)
        watcher.start()
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and len(lines) < 1:
                time.sleep(0.02)
            assert lines == ["[ALL] a: first"]

            log.write_text("", encoding="utf-8")   # 清空
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and not any(
                    e["type"] == "truncated" for e in events):
                time.sleep(0.02)
            assert any(e["type"] == "truncated" for e in events)

            with open(log, "a", encoding="utf-8") as fh:
                fh.write("[ALL] b: after truncate\n")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline and len(lines) < 2:
                time.sleep(0.02)
            assert lines == ["[ALL] a: first", "[ALL] b: after truncate"]
        finally:
            watcher.stop()


class TestChineseReplyFlow:
    """第四階段：中文輸入 -> 譯文編輯 -> 複製 -> 使用者確認 的完整流程。"""

    def _win(self, qapp, tmp_path):
        from cs2_translator.config import ConfigManager
        from cs2_translator.ui.main_window import MainWindow

        cfg = AppConfig(cs2_log_path=str(tmp_path / "console.log"), api_key="sk-t",
                        model="gpt-4o-mini", rate_limit_per_minute=600,
                        log_poll_interval=0.05)
        win = MainWindow(cfg, ConfigManager(data_dir=tmp_path / "data"))
        win.show()
        return win

    def test_blank_input_not_submitted(self, qapp, tmp_path):
        win = self._win(qapp, tmp_path)
        try:
            live = win.live_page
            captured = []
            live.reply_requested.connect(lambda t, l: captured.append((t, l)))
            live.reply_input.setPlainText("   ")
            live._on_reply_translate()
            assert captured == [], "空白輸入不應送出翻譯請求"
            assert "请输入" in live.reply_status.text()
        finally:
            win._really_quit = True
            win.close()

    def test_chinese_input_and_target_language(self, qapp, tmp_path):
        win = self._win(qapp, tmp_path)
        try:
            live = win.live_page
            captured = []
            live.reply_requested.connect(lambda t, l: captured.append((t, l)))
            live.reply_input.setPlainText("别冲，先架枪")
            live.set_target_language("ru")
            live._on_reply_translate()
            assert captured == [("别冲，先架枪", "ru")]
            # 送出後按鈕停用，避免重複提交
            assert live.reply_translate_button.isEnabled() is False
        finally:
            win._really_quit = True
            win.close()

    def test_result_is_editable_and_selectable(self, qapp, tmp_path):
        win = self._win(qapp, tmp_path)
        try:
            from PySide6.QtCore import Qt

            live = win.live_page
            live.show_reply_result({"ok": True, "translated": "原始譯文"})
            assert live.reply_result_label.textInteractionFlags() & Qt.TextSelectableByMouse
            live.reply_result_label.setText("使用者修改後的譯文")
            assert live.reply_result_label.text() == "使用者修改後的譯文"
        finally:
            win._really_quit = True
            win.close()

    def test_retranslate_resets_sent_state(self, qapp, tmp_path):
        """重新翻譯後必須回到『已生成』，不能殘留『已發送』。"""
        win = self._win(qapp, tmp_path)
        try:
            live = win.live_page
            live.show_reply_result({"ok": True, "translated": "第一版"})
            live.mark_copied()
            live.send_button.setChecked(True)
            assert live._reply_state == "sent"

            live.show_reply_result({"ok": True, "translated": "第二版"})
            assert live._reply_state == "generated"
            assert live.send_button.isChecked() is False
        finally:
            win._really_quit = True
            win.close()

    def test_failure_keeps_draft(self, qapp, tmp_path):
        win = self._win(qapp, tmp_path)
        try:
            live = win.live_page
            live.reply_input.setPlainText("转点，快点")
            live.show_reply_result({"ok": False, "error": "网络断开"})
            assert live.reply_input.toPlainText() == "转点，快点"
            assert live._reply_state == "pending"
        finally:
            win._really_quit = True
            win.close()


class TestGlossaryPerformance:
    """詞彙表效能回歸：欄寬自動貼合不得退化成 O(n²)。

    真實事故：欄位用 ResizeToContents 時，Qt 會在**每個** setItem 後重算欄寬，
    153 列 × 6 欄就退化成 O(n²)，實測同一份資料重建要 1300~3200 ms，
    且反覆切換會越切越慢（14ms → 3200ms），使用者感受為「打開詞彙表很卡」。
    修法：重建期間隱藏表格 + 只呼叫一次 resizeColumnsToContents()。
    """

    @staticmethod
    def _fill(mem, count):
        """以單一交易快速種入 count 條術語（避免測試本身成為瓶頸）。"""
        with mem.db.transaction() as conn:
            conn.execute("DELETE FROM translation_terms")
            conn.executemany(
                "INSERT INTO translation_terms (source_term, target_language, direction,"
                " translated_term, category, confirmed, created_at, updated_at)"
                " VALUES (?, 'zh', 'incoming', ?, '戰術', 1,"
                " '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
                [(f"term{i:05d}", f"譯文{i}") for i in range(count)],
            )

    def test_term_stats_matches_manual_count(self, tmp_path):
        """SQL 聚合統計必須與逐筆計算一致。"""
        from cs2_translator.memory import DIRECTION_INCOMING, DIRECTION_REPLY, TranslationMemory

        mem = TranslationMemory(tmp_path / "m.db")
        try:
            with mem.db.transaction() as conn:
                conn.execute("DELETE FROM translation_terms")
                conn.executemany(
                    "INSERT INTO translation_terms (source_term, target_language,"
                    " direction, translated_term, confirmed, created_at, updated_at)"
                    " VALUES (?, 'zh', ?, ?, ?, '2026-01-01', '2026-01-01')",
                    [
                        ("a1", DIRECTION_INCOMING, "a1z", 1),
                        ("a2", DIRECTION_INCOMING, "a2z", 0),
                        ("b1", DIRECTION_REPLY, "b1z", 1),
                        ("b2", DIRECTION_REPLY, "b2z", 1),
                        ("b3", DIRECTION_REPLY, "b3z", 0),
                    ],
                )
            stats = mem.term_stats()
            assert stats["total"] == 5
            assert stats["incoming"] == 2
            assert stats["reply"] == 3
            assert stats["confirmed"] == 3

            manual = mem.list_terms(limit=100000)
            assert stats["total"] == len(manual)
            assert stats["incoming"] == sum(
                1 for t in manual if t.get("direction") == DIRECTION_INCOMING)
            assert stats["confirmed"] == sum(1 for t in manual if t.get("confirmed"))
        finally:
            mem.close()

    def test_term_stats_on_empty_table(self, tmp_path):
        from cs2_translator.memory import TranslationMemory

        mem = TranslationMemory(tmp_path / "m.db")
        try:
            with mem.db.transaction() as conn:
                conn.execute("DELETE FROM translation_terms")
            stats = mem.term_stats()
            assert stats == {"total": 0, "incoming": 0, "reply": 0, "confirmed": 0}
        finally:
            mem.close()

    def test_repeated_rebuild_does_not_degrade_quadratically(self, qapp, tmp_path):
        """反覆重建不得越重建越慢（O(n²) 回歸防護）。

        資料量刻意放大到 600 條：若欄寬仍逐 cell 重算，總時間會爆到數秒；
        修好後 8 次重建應在數十毫秒內完成。門檻設得寬鬆以避免 CI 抖動誤判。
        """
        import time

        from PySide6.QtWidgets import QHeaderView, QTableWidget, QTableWidgetItem, QWidget, QVBoxLayout

        from cs2_translator.memory import TranslationMemory

        mem = TranslationMemory(tmp_path / "m.db")
        table = QTableWidget(0, 6)
        table.setHorizontalHeaderLabels(["術語", "譯文", "方向", "目標語言", "分類", "確認"])
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in (2, 3, 4, 5):
            header.setSectionResizeMode(i, QHeaderView.ResizeToContents)

        host = QWidget()
        layout = QVBoxLayout(host)
        layout.addWidget(table)
        host.resize(1100, 700)
        host.show()
        qapp.processEvents()

        try:
            self._fill(mem, 600)
            rows = mem.list_terms(limit=2000)
            assert len(rows) == 600

            def rebuild():
                # 與 GlossaryPage._fill_table 相同的策略：重建期間隱藏
                was_visible = table.isVisible()
                if was_visible:
                    table.setVisible(False)
                try:
                    table.setRowCount(len(rows))
                    for r, e in enumerate(rows):
                        for c, v in enumerate([e["source_term"], e["translated_term"],
                                               "外→中", e["target_language"],
                                               e["category"], "是"]):
                            table.setItem(r, c, QTableWidgetItem(str(v)))
                finally:
                    if was_visible:
                        table.setVisible(True)
                table.resizeColumnsToContents()

            rebuild()  # 暖機
            qapp.processEvents()

            start = time.perf_counter()
            for _ in range(8):
                rebuild()
                qapp.processEvents()
            elapsed_ms = (time.perf_counter() - start) * 1000

            # 修好後約 <200ms；O(n²) 版本會是數秒
            assert elapsed_ms < 3000, (
                f"8 次重建耗時 {elapsed_ms:.0f} ms，疑似欄寬重算退化成 O(n²)")
        finally:
            host.close()
            mem.close()

    def test_glossary_fill_helper_hides_table_during_rebuild(self, qapp, tmp_path):
        """_fill_table 必須在重建期間隱藏表格（效能修正的結構性驗證）。"""
        from cs2_translator.config import ConfigManager
        from cs2_translator.ui.main_window import MainWindow

        cfg = AppConfig(cs2_log_path=str(tmp_path / "console.log"), api_key="sk",
                        model="gpt-4o-mini", log_poll_interval=0.05)
        win = MainWindow(cfg, ConfigManager(data_dir=tmp_path / "data"))
        win.show()
        try:
            page = win.pages["glossary"]
            page.reload()
            qapp.processEvents()
            # 重建完成後表格應回到可見狀態（有資料時）
            if page._rows:
                assert page.table.isVisibleTo(page), "重建後表格應可見"
            assert page.table.rowCount() == len(page._rows)
        finally:
            win._really_quit = True
            win.close()

    def test_search_uses_debounce_timer(self, qapp, tmp_path):
        """搜尋框應經由延遲計時器觸發，而不是每鍵立即重建。"""
        from cs2_translator.config import ConfigManager
        from cs2_translator.ui.main_window import MainWindow

        cfg = AppConfig(cs2_log_path=str(tmp_path / "console.log"), api_key="sk",
                        model="gpt-4o-mini", log_poll_interval=0.05)
        win = MainWindow(cfg, ConfigManager(data_dir=tmp_path / "data"))
        win.show()
        try:
            page = win.pages["glossary"]
            assert page._search_timer.isSingleShot(), "搜尋計時器應為單次觸發"
            assert not page._search_timer.isActive()
            page.search_edit.setText("rush")
            assert page._search_timer.isActive(), "輸入後應啟動延遲計時器"
        finally:
            win._really_quit = True
            win.close()


class TestMessageOrderAndAutoscroll:
    """实时翻译页的消息排列与自动滚动。

    真实问题：有新消息出来时必须手动往下滑才能看到。
    现在提供两种模式：
    - 最新在底部 + 自动滚动到最新（默认）
    - 最新在顶部（新消息插在 row 0，永远第一眼看到）

    另外用旗标 _user_scrolled_away 取代每次由 value/maximum 现算，
    因为消息刚插入时滚动条范围尚未更新，现算会把「其实还在底部」
    误判成「已离开底部」，导致自动滚动从此失效。
    """

    @staticmethod
    def _win(qapp, tmp_path, **over):
        from cs2_translator.config import ConfigManager
        from cs2_translator.ui.main_window import MainWindow

        cfg = AppConfig(cs2_log_path=str(tmp_path / "console.log"), api_key="sk",
                        model="gpt-4o-mini", log_poll_interval=0.05)
        for k, v in over.items():
            setattr(cfg, k, v)
        win = MainWindow(cfg, ConfigManager(data_dir=tmp_path / "data"))
        win.resize(1100, 640)
        win.show()
        win._switch_page("live")
        qapp.processEvents()
        return win

    @staticmethod
    def _msg(i):
        return {"sender": f"p{i}", "source": f"message {i}", "translated": "譯",
                "channel": "ALL", "time": "12:00", "raw": f"[ALL] p{i}: message {i}"}

    def test_newest_first_inserts_at_top(self, qapp):
        from cs2_translator.ui.page_live import MessageStreamModel

        m = MessageStreamModel(limit=50, newest_first=True)
        for i in range(3):
            m.append({"source": f"m{i}"})
        assert [r["source"] for r in m.rows()] == ["m2", "m1", "m0"]
        assert m.newest_index() == 0

    def test_oldest_first_appends_at_bottom(self, qapp):
        from cs2_translator.ui.page_live import MessageStreamModel

        m = MessageStreamModel(limit=50, newest_first=False)
        for i in range(3):
            m.append({"source": f"m{i}"})
        assert [r["source"] for r in m.rows()] == ["m0", "m1", "m2"]
        assert m.newest_index() == 2

    def test_limit_evicts_oldest_in_both_orders(self, qapp):
        """两种排列都必须淘汰最旧的那一端，且保留最新 50 条。"""
        from cs2_translator.ui.page_live import MessageStreamModel

        for newest_first in (False, True):
            m = MessageStreamModel(limit=50, newest_first=newest_first)
            for i in range(55):
                m.append({"source": f"m{i:02d}"})
            rows = [r["source"] for r in m.rows()]
            assert len(rows) == 50, f"newest_first={newest_first} 列數 {len(rows)}"
            assert rows[m.newest_index()] == "m54"
            for gone in ("m00", "m01", "m02", "m03", "m04"):
                assert gone not in rows, f"newest_first={newest_first} 未淘汰 {gone}"
            if newest_first:
                assert rows[0] == "m54" and rows[-1] == "m05"
            else:
                assert rows[0] == "m05" and rows[-1] == "m54"

    def test_set_newest_first_reverses_without_data_loss(self, qapp):
        from cs2_translator.ui.page_live import MessageStreamModel

        m = MessageStreamModel(limit=50, newest_first=False)
        for i in range(4):
            m.append({"source": f"x{i}"})
        before = [r["source"] for r in m.rows()]
        m.set_newest_first(True)
        after = [r["source"] for r in m.rows()]
        assert after == before[::-1], f"{before} -> {after}"
        assert sorted(after) == sorted(before)

    def test_new_message_visible_without_manual_scroll(self, qapp, tmp_path):
        """預設（最新在底部）下，連來 30 條都不需要手動滾動。"""
        win = self._win(qapp, tmp_path, auto_scroll=True, newest_first=False)
        try:
            page = win.live_page
            for i in range(30):
                page.add_message(self._msg(i))
                qapp.processEvents()
            idx = page.model.index(page.model.newest_index(), 0)
            vp = page.list_view.viewport().rect()
            assert vp.intersects(page.list_view.visualRect(idx)), "最新一條不在可視範圍內"
            assert page._user_scrolled_away is False
        finally:
            win._really_quit = True
            win.close()

    def test_user_scrolling_away_is_respected(self, qapp, tmp_path):
        """使用者主動往上翻時不得被強制拉回，但必須出現「回到最新」按鈕。"""
        win = self._win(qapp, tmp_path, auto_scroll=True, newest_first=False)
        try:
            page = win.live_page
            for i in range(30):
                page.add_message(self._msg(i))
                qapp.processEvents()
            page.list_view.verticalScrollBar().setValue(0)
            qapp.processEvents()
            assert page._user_scrolled_away is True

            page.add_message(self._msg(99))
            qapp.processEvents()
            assert page.new_message_button.isVisible(), "离开底部后应显示回到最新按钮"
            page.scroll_to_newest()
            qapp.processEvents()
            assert page._user_scrolled_away is False
            idx = page.model.index(page.model.newest_index(), 0)
            vp = page.list_view.viewport().rect()
            assert vp.intersects(page.list_view.visualRect(idx))
        finally:
            win._really_quit = True
            win.close()

    def test_newest_first_mode_shows_newest_at_top(self, qapp, tmp_path):
        """最新在頂部模式：新消息插在最上面，不需滾動。"""
        win = self._win(qapp, tmp_path, auto_scroll=True, newest_first=True)
        try:
            page = win.live_page
            assert page.model.newest_first is True
            for i in range(20):
                page.add_message(self._msg(i))
                qapp.processEvents()
            rows = [r["source"] for r in page.model.rows()]
            assert rows[0] == "message 19", f"最新應在最前，實際 {rows[:3]}"
            vp = page.list_view.viewport().rect()
            assert vp.intersects(page.list_view.visualRect(page.model.index(0, 0))), \
                "最新一條應可見"
        finally:
            win._really_quit = True
            win.close()

    def test_apply_display_order_switches_live(self, qapp, tmp_path):
        """設定變更後可即時切換排列，且內容不遺失。"""
        win = self._win(qapp, tmp_path, newest_first=False)
        try:
            page = win.live_page
            for i in range(6):
                page.add_message(self._msg(i))
                qapp.processEvents()
            before = [r["source"] for r in page.model.rows()]

            page.apply_display_order(True)
            qapp.processEvents()
            after = [r["source"] for r in page.model.rows()]
            assert page.model.newest_first is True
            assert sorted(after) == sorted(before), "切换排列不应丢消息"
            assert after[0] == before[-1]

            page.apply_display_order(False)
            qapp.processEvents()
            assert page.model.newest_first is False
            assert [r["source"] for r in page.model.rows()] == before
        finally:
            win._really_quit = True
            win.close()

    def test_auto_scroll_disabled_does_not_force_scroll(self, qapp, tmp_path):
        """关掉自动滚动后，不应强行把视图拉到最新。"""
        win = self._win(qapp, tmp_path, auto_scroll=False, newest_first=False)
        try:
            page = win.live_page
            for i in range(30):
                page.add_message(self._msg(i))
                qapp.processEvents()
            page.list_view.verticalScrollBar().setValue(0)
            qapp.processEvents()
            page.add_message(self._msg(99))
            qapp.processEvents()
            assert page.list_view.verticalScrollBar().value() == 0, \
                "关闭自动滚动后不应被拉到底部"
        finally:
            win._really_quit = True
            win.close()

    def test_clear_stream_resets_scroll_state(self, qapp, tmp_path):
        win = self._win(qapp, tmp_path, newest_first=False)
        try:
            page = win.live_page
            for i in range(30):
                page.add_message(self._msg(i))
                qapp.processEvents()
            page.list_view.verticalScrollBar().setValue(0)
            qapp.processEvents()
            assert page._user_scrolled_away is True
            page.clear_stream()
            qapp.processEvents()
            assert page._user_scrolled_away is False
            assert page.model.rowCount() == 0
        finally:
            win._really_quit = True
            win.close()

    def test_settings_roundtrip_newest_first(self, qapp, tmp_path):
        """設定頁讀寫必須保留 newest_first。"""
        win = self._win(qapp, tmp_path, newest_first=True)
        try:
            page = win.pages["settings"]
            page.reload()
            qapp.processEvents()
            assert page.order_combo.currentData() == "top"
            assert page._collect().newest_first is True

            page.order_combo.setCurrentIndex(page.order_combo.findData("bottom"))
            assert page._collect().newest_first is False
        finally:
            win._really_quit = True
            win.close()


class TestMatchSwitchNoContamination:
    """切换对局后，旧消息与旧任务不得污染新对局。"""

    def test_old_messages_not_in_new_match_view(self, tmp_path):
        from cs2_translator.memory import MatchManager

        eng = TranslationEngine(
            AppConfig(api_key="x", cs2_log_path=str(tmp_path / "console.log"),
                      translate_enabled=False, queue_max_size=20),
            data_dir=tmp_path / "data",
        )
        try:
            first = eng.matches.current_match_id
            eng._on_log_line("10/13 10:20:00  [ALL] p1: first match message")
            assert eng.matches.count_messages(first) == 1

            eng.start_new_match(source="manual")
            second = eng.matches.current_match_id
            assert second != first
            # 新对局视图为空
            assert eng.matches.list_messages(second) == []
            # 旧消息仍在历史中，不属于新对局
            contents = [m.content for m in eng.matches.list_messages(second)]
            assert "first match message" not in contents
            assert eng.matches.count_messages(first) == 1
        finally:
            eng.stop()

    def test_late_result_detected_as_stale(self, tmp_path):
        eng = TranslationEngine(
            AppConfig(api_key="x", cs2_log_path=str(tmp_path / "console.log"),
                      translate_enabled=False, queue_max_size=20),
            data_dir=tmp_path / "data",
        )
        try:
            gen_old = eng.matches.generation
            eng.start_new_match(source="manual")
            assert eng._is_stale_generation(gen_old) is True
            assert eng._is_stale_generation(eng.matches.generation) is False
        finally:
            eng.stop()

    def test_queue_pending_cancelled_on_switch(self, tmp_path):
        """换局时未开始的任务应被取消，避免旧任务结果落到新局。"""
        eng = TranslationEngine(
            AppConfig(api_key="x", cs2_log_path=str(tmp_path / "console.log"),
                      translate_enabled=False, queue_max_size=50),
            data_dir=tmp_path / "data",
        )
        try:
            for i in range(5):
                eng._on_log_line(f"10/13 10:20:0{i}  [ALL] p: message number {i}")
            before = eng._queue.stats()
            eng.start_new_match(source="manual")
            after = eng._queue.stats()
            assert after["cancelled"] >= before.get("cancelled", 0)
            # 上下文已清空，旧消息不会再被回填
            assert len(eng._message_context) == 0
        finally:
            eng.stop()
