# -*- coding: utf-8 -*-
"""对局隔离测试：验证「当前局 / 历史对局 / 长期翻译记忆」三层数据严格分离。

关键验收点：
- 第一局消息全部关联第一局；
- 第二局开始后，当局视图不再包含第一局消息；
- 第二局消息只写入第二局；
- 清理历史对局不会删除长期翻译记忆；
- 清空本局显示不影响长期翻译记忆；
- 连续切换多局不产生数据竞争；
- 无可靠对局开始信号时安全降级（手动模式）。
"""

from __future__ import annotations

import threading

import pytest

from cs2_translator.memory import MatchManager, detect_match_signal
from cs2_translator.memory.db import Database
from cs2_translator.memory.normalize import DIRECTION_INCOMING
from cs2_translator.memory.store import TranslationMemory


@pytest.fixture()
def db(tmp_path):
    database = Database(tmp_path / "memory.db")
    yield database
    database.close()


@pytest.fixture()
def manager(db):
    return MatchManager(db, message_limit=300, history_limit=3)


def _add(manager, text, sender="p1", match_id=None):
    return manager.add_message(
        timestamp="10/13 10:20:00",
        sender=sender,
        channel="ALL",
        content=text,
        match_id=match_id,
    )


# ======================================================================
# 1. 基本归属
# ======================================================================
class TestMatchAttribution:
    def test_first_match_messages_all_belong_to_first(self, manager):
        first = manager.current_match_id
        for i in range(5):
            _add(manager, f"rush B {i}")
        msgs = manager.list_messages(first)
        assert len(msgs) == 5
        assert all(m.match_id == first for m in msgs)

    def test_new_match_does_not_show_previous_messages(self, manager):
        first = manager.current_match_id
        _add(manager, "第一局消息")
        _add(manager, "第一局消息2")
        assert manager.message_count == 2

        second_info = manager.start_new_match()
        second = second_info.match_id
        assert second != first
        # 新局的视图必须为空
        assert manager.list_messages(second) == []
        assert manager.message_count == 0
        # 第一局的数据仍然存在于历史中
        assert manager.count_messages(first) == 2

    def test_second_match_messages_only_go_to_second(self, manager):
        first = manager.current_match_id
        _add(manager, "old")
        second = manager.start_new_match().match_id
        _add(manager, "new-one")
        _add(manager, "new-two")

        assert manager.count_messages(second) == 2
        assert manager.count_messages(first) == 1
        contents = [m.content for m in manager.list_messages(second)]
        assert contents == ["new-one", "new-two"]
        # 第二局中绝不能出现第一局内容
        assert "old" not in contents

    def test_generation_changes_on_new_match(self, manager):
        gen0 = manager.generation
        manager.start_new_match()
        assert manager.generation == gen0 + 1
        manager.start_new_match()
        assert manager.generation == gen0 + 2

    def test_late_result_can_be_detected_as_stale(self, manager):
        """模拟：第一局的请求发出后换局，结果应被判定为过期。"""
        gen_of_first = manager.generation
        manager.start_new_match()
        # 引擎用该代际与当前代际比较来丢弃迟到结果
        assert gen_of_first != manager.generation


# ======================================================================
# 2. 三层数据隔离
# ======================================================================
class TestThreeLayerIsolation:
    def test_clearing_view_does_not_touch_long_term_memory(self, tmp_path):
        memory = TranslationMemory(tmp_path / "memory.db")
        manager = MatchManager(memory.db, history_limit=3)
        _add(manager, "hello")
        memory.save_translation(text="hello", target_language="zh", direction=DIRECTION_INCOMING,
                                translated="你好")
        assert memory.count_memory() == 1

        manager.clear_current_view(delete_records=True)
        # 局内消息已删除，但长期翻译记忆完好
        assert memory.count_memory() == 1
        assert memory.lookup("hello", "zh", DIRECTION_INCOMING).translated == "你好"
        memory.close()

    def test_history_cleanup_does_not_touch_long_term_memory(self, tmp_path):
        memory = TranslationMemory(tmp_path / "memory.db")
        manager = MatchManager(memory.db, history_limit=2)
        memory.save_translation(text="rotate mid", target_language="zh",
                                direction=DIRECTION_INCOMING, translated="转中路")
        before = memory.count_memory()

        # 制造 5 局，超过保留上限 2
        for i in range(5):
            _add(manager, f"msg in match {i}")
            manager.start_new_match()

        # 历史对局被裁剪
        assert manager.count_ended_matches() <= 2
        # 长期翻译记忆一字未动
        assert memory.count_memory() == before
        assert memory.lookup("rotate mid", "zh", DIRECTION_INCOMING).translated == "转中路"
        memory.close()

    def test_new_match_does_not_clear_long_term_memory(self, tmp_path):
        memory = TranslationMemory(tmp_path / "memory.db")
        manager = MatchManager(memory.db)
        memory.save_translation(text="eco round", target_language="zh",
                                direction=DIRECTION_INCOMING, translated="经济局")
        count0 = memory.count_memory()

        for _ in range(4):
            manager.start_new_match()
        assert memory.count_memory() == count0

        # 新一局再次出现相同文本，仍能命中缓存
        result = memory.lookup("eco round", "zh", DIRECTION_INCOMING)
        assert result.hit
        assert result.translated == "经济局"
        memory.close()

    def test_clear_view_without_delete_keeps_records(self, manager):
        _add(manager, "keep me")
        removed = manager.clear_current_view(delete_records=False)
        assert removed == 0
        assert manager.count_messages(manager.current_match_id) == 1

    def test_terms_and_templates_survive_history_cleanup(self, tmp_path):
        memory = TranslationMemory(tmp_path / "memory.db")
        manager = MatchManager(memory.db, history_limit=1)
        terms_before = len(memory.list_terms(limit=1000))
        templates_before = len(memory.list_templates(limit=1000))
        assert terms_before > 0  # 内置术语已种子化

        for i in range(4):
            _add(manager, f"m{i}")
            manager.start_new_match()

        assert len(memory.list_terms(limit=1000)) == terms_before
        assert len(memory.list_templates(limit=1000)) == templates_before
        memory.close()


# ======================================================================
# 3. 保留策略
# ======================================================================
class TestRetention:
    def test_message_limit_trims_oldest(self, db):
        manager = MatchManager(db, message_limit=50)
        for i in range(80):
            _add(manager, f"m{i}")
        messages = manager.list_messages(manager.current_match_id)
        assert len(messages) == 50
        # 最旧的被移除，保留的是最后 50 条
        assert messages[-1].content == "m79"
        assert messages[0].content == "m30"

    def test_history_limit_enforced(self, db):
        manager = MatchManager(db, history_limit=2)
        for i in range(6):
            _add(manager, f"m{i}")
            manager.start_new_match()
        assert manager.count_ended_matches() <= 2

    def test_stats_reflect_real_counts(self, manager):
        _add(manager, "a")
        _add(manager, "b")
        stats = manager.stats()
        assert stats["current_message_count"] == 2
        manager.start_new_match()
        stats2 = manager.stats()
        assert stats2["current_message_count"] == 0
        assert stats2["history_match_count"] >= 1

    def test_cannot_delete_active_match(self, manager):
        active = manager.current_match_id
        assert manager.delete_match(active) is False


# ======================================================================
# 4. 并发安全
# ======================================================================
class TestConcurrency:
    def test_concurrent_new_matches_do_not_corrupt(self, db):
        """连续切换多局 + 并发写入不应产生异常或数据错乱。"""
        manager = MatchManager(db, history_limit=100)
        errors = []

        def switcher():
            try:
                for _ in range(15):
                    manager.start_new_match()
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        def writer():
            try:
                for i in range(60):
                    _add(manager, f"w{i}")
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=switcher) for _ in range(3)]
        threads += [threading.Thread(target=writer) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []
        # 每条消息都必须属于某个真实存在的对局
        known = {m.match_id for m in manager.list_matches(limit=1000)}
        with db.read() as conn:
            rows = conn.execute("SELECT DISTINCT match_id FROM match_messages").fetchall()
        for row in rows:
            assert row["match_id"] in known

    def test_concurrent_writes_same_match(self, manager):
        match_id = manager.current_match_id
        errors = []

        def worker(base):
            try:
                for i in range(30):
                    _add(manager, f"t{base}-{i}", match_id=match_id)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert errors == []
        assert manager.count_messages(match_id) == 120


# ======================================================================
# 5. 对局信号识别（基于真实日志行）
# ======================================================================
# 以下行摘自真实 CS2 console.log（E:\steam\...\game\csgo\console.log）
REAL_LOG_LINES = {
    "start": [
        "10/13 10:18:23 [HostStateManager] Host activate: Remote Connect (=[A:1:3429989395:51849])",
        "10/13 10:16:29 [HostStateManager] Host activate: Loading (csstats)",
    ],
    "end": [
        "10/13 10:23:49 [HostStateManager] Host activate: Idle (levelload)",
        "10/13 10:26:26 [HostStateManager] Host activate: Quitting",
    ],
    "none": [
        "10/13 10:16:51  [ALL] player： hi bro",
        "10/13 10:17:34  [ALL] player： cheater",
        "10/13 10:22:21  [CT] other [DEAD]: ото",
        "10/13 10:15:55 [Server] SV:  CGameRulesGameSystem::GameInit installed game rules",
        "10/13 10:16:30 [Server] CSource2Server::GameServerSteamAPIActivated()",
    ],
}


class TestMatchSignalDetection:
    @pytest.mark.parametrize("line", REAL_LOG_LINES["start"])
    def test_real_start_signals(self, line):
        result = detect_match_signal(line)
        assert result is not None
        assert result[0] == "start"

    @pytest.mark.parametrize("line", REAL_LOG_LINES["end"])
    def test_real_end_signals(self, line):
        result = detect_match_signal(line)
        assert result is not None
        assert result[0] == "end"

    @pytest.mark.parametrize("line", REAL_LOG_LINES["none"])
    def test_chat_and_engine_lines_are_not_signals(self, line):
        assert detect_match_signal(line) is None

    def test_auto_detection_disabled_by_default(self):
        """无可靠信号时必须安全降级：默认不自动识别，仅手动换局。"""
        from cs2_translator.config import AppConfig

        assert AppConfig().auto_match_detection is False

    def test_manual_mode_still_creates_and_switches(self, manager):
        """即使没有任何日志信号，手动换局也必须可用。"""
        first = manager.current_match_id
        second = manager.start_new_match(source="manual").match_id
        assert second != first
        assert manager.current_match_id == second


class TestMapNameExtraction:
    """重复载入同一张地图不应被算作新对局（真实日志中 5e_aimhub 连续重载 3 次）。"""

    def test_extract_from_real_lines(self):
        from cs2_translator.memory import extract_map_name

        assert extract_map_name("10/13 11:11:43 [HostStateManager] Host activate: Loading (5e_aimhub)") == "5e_aimhub"
        assert extract_map_name("10/13 11:21:51 [HostStateManager] Host activate: Loading (de_vertigo)") == "de_vertigo"
        assert extract_map_name("10/13 11:10:15 [HostStateManager] Host activate: Idle (levelload)") is None
        assert extract_map_name("[ALL] someone: hi") is None

    def test_real_log_repeated_reload_sequence(self):
        """真实日志顺序：csstats -> csstats -> 5e_aimhub -> de_vertigo -> 5e_aimhub。"""
        from cs2_translator.memory import extract_map_name

        lines = [
            "10/13 11:10:34 [HostStateManager] Host activate: Loading (csstats)",
            "10/13 11:11:02 [HostStateManager] Host activate: Loading (csstats)",
            "10/13 11:11:43 [HostStateManager] Host activate: Loading (5e_aimhub)",
            "10/13 11:21:51 [HostStateManager] Host activate: Loading (de_vertigo)",
            "10/13 11:22:25 [HostStateManager] Host activate: Loading (5e_aimhub)",
        ]
        names = [extract_map_name(line) for line in lines]
        assert names == ["csstats", "csstats", "5e_aimhub", "de_vertigo", "5e_aimhub"]
        # 只忽略「紧接着重复同一张图」的情况（练习图重载）。
        # 换到别的地图后又载入 5e_aimhub 仍算新的一局。
        switches = []
        last = None
        for name in names:
            if name != last:
                switches.append(name)
                last = name
        assert switches == ["csstats", "5e_aimhub", "de_vertigo", "5e_aimhub"]
        # 5 次载入 -> 4 次换局（少了紧邻重复的那一次）
        assert len(switches) == 4


class TestStartupReset:
    """每次启动清空「实时翻译」与「历史对局」，只保留长期缓存与词汇表。"""

    def test_reset_clears_matches_keeps_memory(self, tmp_path):
        from cs2_translator.memory import DIRECTION_INCOMING, TranslationMemory

        memory = TranslationMemory(tmp_path / "memory.db")
        matches = MatchManager(memory.db, history_limit=5)
        matches.add_message(timestamp="1", sender="a", channel="ALL", content="old msg")
        matches.start_new_match(source="manual")
        matches.add_message(timestamp="2", sender="b", channel="ALL", content="older msg")
        memory.save_translation(text="rush b", target_language="zh",
                                direction=DIRECTION_INCOMING, translated="冲B")
        terms_before = len(memory.list_terms(limit=99999))
        memory_before = memory.count_memory()

        assert matches.count_matches() >= 2
        result = matches.reset_for_new_session()

        assert result["matches_removed"] >= 2
        assert result["messages_removed"] >= 2
        # 只剩新的当前局，且没有历史对局、没有消息
        assert matches.count_matches() == 1
        assert matches.count_ended_matches() == 0
        assert matches.message_count == 0
        # 长期记忆与词汇表保留
        assert memory.count_memory() == memory_before
        assert len(memory.list_terms(limit=99999)) == terms_before
        assert memory.lookup("rush b", "zh", DIRECTION_INCOMING).translated == "冲B"
        memory.close()

    def test_reset_bumps_generation(self, tmp_path):
        """重置必须作废旧代际，避免启动前的迟到结果进入新会话。"""
        from cs2_translator.memory import TranslationMemory

        memory = TranslationMemory(tmp_path / "memory.db")
        matches = MatchManager(memory.db)
        gen_before = matches.generation
        matches.reset_for_new_session()
        assert matches.generation == gen_before + 1
        memory.close()

    def test_engine_resets_on_start(self, tmp_path):
        from cs2_translator.config import AppConfig
        from cs2_translator.engine import TranslationEngine
        from cs2_translator.memory import DIRECTION_INCOMING

        cfg = AppConfig(api_key="x", cs2_log_path=str(tmp_path / "console.log"),
                        translate_enabled=False, queue_max_size=20,
                        reset_on_startup=True)
        # 第一次：留下对局与长期记忆
        e1 = TranslationEngine(cfg, data_dir=tmp_path / "data")
        e1.matches.add_message(timestamp="1", sender="a", channel="ALL", content="x")
        e1.matches.start_new_match(source="manual")
        e1.memory.save_translation(text="eco", target_language="zh",
                                   direction=DIRECTION_INCOMING, translated="经济局")
        e1.stop()

        # 第二次：启动应重置
        e2 = TranslationEngine(cfg, data_dir=tmp_path / "data")
        e2.start()
        try:
            assert e2.last_reset_result is not None
            assert e2.matches.count_ended_matches() == 0
            assert e2.matches.message_count == 0
            hit = e2.memory.lookup("eco", "zh", DIRECTION_INCOMING)
            assert hit.hit and hit.translated == "经济局"
        finally:
            e2.stop()

    def test_reset_can_be_disabled(self, tmp_path):
        from cs2_translator.config import AppConfig
        from cs2_translator.engine import TranslationEngine

        cfg = AppConfig(api_key="x", cs2_log_path=str(tmp_path / "console.log"),
                        translate_enabled=False, queue_max_size=20,
                        reset_on_startup=False)
        e1 = TranslationEngine(cfg, data_dir=tmp_path / "data")
        e1.matches.add_message(timestamp="1", sender="a", channel="ALL", content="keep")
        e1.stop()

        e2 = TranslationEngine(cfg, data_dir=tmp_path / "data")
        e2.start()
        try:
            assert e2.last_reset_result is None
            assert e2.matches.message_count >= 0
        finally:
            e2.stop()

    def test_defaults_are_reset_on_and_no_history_reread(self):
        from cs2_translator.config import AppConfig

        cfg = AppConfig()
        assert cfg.reset_on_startup is True
        # 默认只读启动后的新消息，避免每次启动重复翻译旧日志
        assert cfg.read_history_on_start is False


class TestReplyThreeStates:
    """回复三态必须严格区分，绝不虚报「已发送」。"""

    def _window(self, qapp, tmp_path):
        from cs2_translator.config import AppConfig, ConfigManager
        from cs2_translator.ui.main_window import MainWindow

        cfg = AppConfig(cs2_log_path=str(tmp_path / "console.log"), api_key="sk-t",
                        model="gpt-4o-mini", rate_limit_per_minute=600,
                        log_poll_interval=0.05)
        win = MainWindow(cfg, ConfigManager(data_dir=tmp_path / "data"))
        win.show()
        return win

    def test_success_auto_copies_and_never_claims_sent(self, qapp, tmp_path):
        """翻譯成功自動複製；狀態只到「已複製」，**永不聲稱已發送**。

        設計邏輯（使用者指定）：翻譯出來 → 自動複製 → 進遊戲 Ctrl+V 粘貼。
        因此沒有「我已發送」確認框，狀態提示也不得提「已發送／已確認」，
        避免讓人誤以為程式會自動發送。
        """
        win = self._window(qapp, tmp_path)
        try:
            live = win.live_page
            assert live._reply_state == "pending"

            copied = []
            live.copy_requested.connect(copied.append)
            live.show_reply_result({"ok": True, "translated": "Rush B",
                                    "hit_kind": "api", "latency_ms": 10.0})
            assert copied == ["Rush B"], "翻譯成功必須自動複製"
            assert live._reply_state == "copied"

            text = live.state_label.text()
            assert "已复制" in text
            # 絕不可聲稱已發送
            assert "已发送" not in text and "已确认" not in text
            # 必須提示使用者自己去粘貼
            assert "Ctrl+V" in text or "粘贴" in text
        finally:
            win._really_quit = True
            win.close()

    def test_retranslate_auto_copies_again(self, qapp, tmp_path):
        """重新翻譯會再次自動複製，並覆蓋前一版。"""
        win = self._window(qapp, tmp_path)
        try:
            live = win.live_page
            copied = []
            live.copy_requested.connect(copied.append)
            live.show_reply_result({"ok": True, "translated": "go mid"})
            live.show_reply_result({"ok": True, "translated": "hold the angle"})
            assert copied == ["go mid", "hold the angle"]
            assert live._reply_state == "copied"
            assert live.reply_result_label.text() == "hold the angle"
        finally:
            win._really_quit = True
            win.close()

    def test_failure_sets_failed_state(self, qapp, tmp_path):
        """失敗時狀態為 failed（不是 pending），且必須明確顯示失敗。

        舊版把失敗退回 pending，會讓使用者分不清「還沒開始」與「剛剛失敗了」。
        """
        win = self._window(qapp, tmp_path)
        try:
            live = win.live_page
            live.show_reply_result({"ok": True, "translated": "x"})
            live.show_reply_result({"ok": False, "error": "网络断开"})
            assert live._reply_state == "failed"
            assert "失败" in live.state_label.text()
            # 草稿必須保留，讓使用者可直接重試
            assert "草稿已保留" in live.state_label.text()
        finally:
            win._really_quit = True
            win.close()


class TestAutoDetectionClearsDisplay:
    """自动识别换局时，界面显示必须同步清空（真实 bug 的回归测试）。

    实测：开启自动识别后引擎已切到新对局，但界面仍显示上一局的聊天记录。
    """

    def test_engine_notifies_on_new_match(self, tmp_path):
        from cs2_translator.config import AppConfig
        from cs2_translator.engine import TranslationEngine

        cfg = AppConfig(api_key="x", cs2_log_path=str(tmp_path / "console.log"),
                        auto_match_detection=True, queue_max_size=50)
        engine = TranslationEngine(cfg, data_dir=tmp_path / "data")
        events = []
        engine.on_match_change = lambda info: events.append(info)
        try:
            engine.start_new_match(source="manual")
            assert events, "换局时必须通知界面"
            assert events[-1]["event"] == "started"
            assert events[-1]["match_id"]
        finally:
            engine.stop()

    def test_auto_detection_triggers_and_ignores_repeat_map(self, tmp_path):
        from cs2_translator.config import AppConfig
        from cs2_translator.engine import TranslationEngine

        cfg = AppConfig(api_key="x", cs2_log_path=str(tmp_path / "console.log"),
                        auto_match_detection=True, queue_max_size=50)
        engine = TranslationEngine(cfg, data_dir=tmp_path / "data")
        events = []
        engine.on_match_change = lambda info: events.append(info)
        try:
            engine._on_log_line("[HostStateManager] Host activate: Loading (csstats)")
            first = engine.matches.current_match_id
            assert events and events[-1]["source"] == "auto"

            # 同一张地图重复载入 -> 不应换局
            engine._on_log_line("[HostStateManager] Host activate: Loading (csstats)")
            assert engine.matches.current_match_id == first
            assert len([e for e in events if e["event"] == "started"]) == 1

            # 换一张地图 -> 应换局
            engine._on_log_line("[HostStateManager] Host activate: Loading (de_vertigo)")
            assert engine.matches.current_match_id != first
        finally:
            engine.stop()

    def test_display_cleared_on_auto_match_change(self, qapp, tmp_path):
        """端到端：引擎报告自动换局后，实时页消息流必须清空。"""
        from cs2_translator.chat_parser import ChatMessage
        from cs2_translator.config import AppConfig, ConfigManager
        from cs2_translator.ui.main_window import MainWindow

        cfg = AppConfig(cs2_log_path=str(tmp_path / "console.log"), api_key="sk-test",
                        model="gpt-4o-mini", rate_limit_per_minute=600,
                        log_poll_interval=0.05)
        window = MainWindow(cfg, ConfigManager(data_dir=tmp_path / "data"))
        window.show()
        try:
            window._on_chat(ChatMessage(sender="A", content="上一局的消息"))
            assert len(window.live_page.model.rows()) == 1

            # 模拟引擎自动识别换局（不经过手动按钮）
            window._on_match_changed({
                "event": "started", "match_id": "auto-new", "source": "auto",
                "note": "自动识别: loading_map",
            })
            assert window.live_page.model.rows() == [], "自动换局后界面未清空"
        finally:
            window._really_quit = True
            window.close()
