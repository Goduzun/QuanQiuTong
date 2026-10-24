# -*- coding: utf-8 -*-
"""新功能回歸測試：無響應（AppHangB1）修復。

對應使用者回報的真實事故：
  「只要全球通在后台运行，启动 CS2 就会周期性出现无响应（AppHangB1）」
現場證據：2 份實例同時運行、無前台感知、每秒 UI 刷新 + DB 查詢。

五個缺陷各一組測試：
  A 單實例鎖        TestSingleInstance
  B 前台感知        TestForegroundMonitor
  C 啟動自檢短超時  TestStartupApiTimeout
  D 日誌退避        TestLogWatcherBackoff
  E 統計快取        TestStatsCache

全部在 offscreen 平台執行，不建立真實視窗、不接觸 CS2 進程。
"""
from __future__ import annotations

import sys
import time

import pytest

from cs2_translator.config import AppConfig


# ====================================================================
# A. 單實例鎖
# ====================================================================
class TestSingleInstance:
    """第二個實例必須讓位，並喚醒第一個實例。"""

    @staticmethod
    def _key(tag):
        # 測試用獨立 key，避免和使用者真正在跑的實例撞名
        import os
        return f"quanqiutong-test-{tag}-{os.getpid()}"

    def test_first_instance_acquires(self, qapp):
        from cs2_translator.single_instance import SingleInstanceGuard

        guard = SingleInstanceGuard(self._key("a1"))
        try:
            assert guard.try_acquire() is True
        finally:
            guard.release()

    def test_second_instance_yields_and_notifies(self, qapp):
        from cs2_translator.single_instance import SingleInstanceGuard

        key = self._key("a2")
        first = SingleInstanceGuard(key)
        second = SingleInstanceGuard(key)
        try:
            assert first.try_acquire() is True, "第一個應取得鎖"

            fired = []
            first.activated.connect(lambda: fired.append(1))
            assert second.try_acquire() is False, "第二個必須讓位"

            # 處理事件迴圈，讓伺服器收到連線並發出 activated
            for _ in range(20):
                qapp.processEvents()
                if fired:
                    break
                time.sleep(0.02)
            assert fired, "第一個實例應收到喚醒請求"
        finally:
            second.release()
            first.release()

    def test_release_allows_reacquire(self, qapp):
        from cs2_translator.single_instance import SingleInstanceGuard

        key = self._key("a3")
        g1 = SingleInstanceGuard(key)
        assert g1.try_acquire() is True
        g1.release()

        g2 = SingleInstanceGuard(key)
        try:
            assert g2.try_acquire() is True, "釋放後應能重新取得鎖"
        finally:
            g2.release()

    def test_instance_key_contains_app_and_user(self):
        from cs2_translator.single_instance import instance_key

        key = instance_key("CS2ChatTranslator")
        assert key.startswith("CS2ChatTranslator-single-")
        # 命名管道是整机可见，必须带用户名做隔离
        assert len(key) > len("CS2ChatTranslator-single-")


# ====================================================================
# B. 前台感知
# ====================================================================
class TestForegroundMonitor:
    """前台狀態翻轉才發信號；查詢本身不得拋例外。"""

    def test_win32_available_on_windows(self):
        from cs2_translator import foreground

        if not sys.platform.startswith("win"):
            pytest.skip("僅 Windows")
        api = foreground._win32()
        assert api is not None, "應能載入 user32/kernel32"
        for need in ("ctypes", "wintypes", "user32", "kernel32"):
            assert need in api

    def test_foreground_pid_returns_int_or_none(self):
        from cs2_translator.foreground import foreground_pid

        pid = foreground_pid()
        assert pid is None or (isinstance(pid, int) and pid > 0)

    def test_process_name_of_self(self):
        """查自己的進程名，驗證 QueryFullProcessImageNameW 路徑可用。"""
        import os

        from cs2_translator.foreground import process_name

        name = process_name(os.getpid())
        assert name, "應能取得自身進程名"
        assert name.endswith(".exe") or "." in name

    def test_process_name_of_invalid_pid_is_empty(self):
        from cs2_translator.foreground import process_name

        assert process_name(0) == ""
        assert process_name(999999) == ""

    def test_monitor_emits_only_on_change(self, qapp, monkeypatch):
        from cs2_translator import foreground

        calls = {"n": 0}

        def fake_detect(names=None):
            calls["n"] += 1
            return fake_detect.value

        fake_detect.value = True
        monkeypatch.setattr(foreground, "is_process_foreground", fake_detect)

        monitor = foreground.ForegroundMonitor()
        seen = []
        monitor.state_changed.connect(seen.append)

        # 手動觸發：第一次 True -> 發信號；第二次仍 True -> 不發
        monitor._poll()
        monitor._poll()
        assert seen == [True], f"狀態沒變不該重複發信號，實際 {seen}"

        fake_detect.value = False
        monitor._poll()
        assert seen == [True, False]

        monitor._poll()
        assert seen == [True, False], "再次為 False 也不該重複發"

    def test_monitor_survives_detection_error(self, qapp, monkeypatch):
        from cs2_translator import foreground

        def boom(names=None):
            raise RuntimeError("模擬 Win32 失敗")

        monkeypatch.setattr(foreground, "is_process_foreground", boom)
        monitor = foreground.ForegroundMonitor()
        monitor._poll()   # 不應拋例外
        assert monitor.is_active is False


# ====================================================================
# C. 啟動自檢短超時
# ====================================================================
class TestStartupApiTimeout:
    def test_engine_test_api_accepts_timeout(self):
        """engine.test_api 必須接受 timeout 參數（自檢不沿用 20s）。"""
        import inspect

        from cs2_translator.engine import TranslationEngine

        sig = inspect.signature(TranslationEngine.test_api)
        assert "timeout" in sig.parameters

    def test_controller_test_api_accepts_timeout(self):
        import inspect

        from cs2_translator.ui.workers import EngineController

        sig = inspect.signature(EngineController.test_api)
        assert "timeout" in sig.parameters

    def test_translator_translate_accepts_timeout(self):
        import inspect

        from cs2_translator.translator import OpenAITranslator

        sig = inspect.signature(OpenAITranslator.translate)
        assert "timeout" in sig.parameters

    def test_startup_timeout_is_short(self):
        """啟動自檢的超時常數必須明顯短於翻譯主路徑（20s）。"""
        from cs2_translator.ui.main_window import MainWindow

        assert MainWindow.STARTUP_API_TIMEOUT <= 8.0
        assert MainWindow.STARTUP_API_TIMEOUT < AppConfig().timeout_seconds

    def test_auto_check_api_passes_short_timeout(self, qapp, tmp_path, monkeypatch):
        """_auto_check_api 真的把短超時傳下去（不是只宣告參數）。"""
        from cs2_translator.config import ConfigManager
        from cs2_translator.ui.main_window import MainWindow

        cfg = AppConfig(cs2_log_path=str(tmp_path / "console.log"), api_key="sk",
                        model="gpt-4o-mini", log_poll_interval=0.05)
        win = MainWindow(cfg, ConfigManager(data_dir=tmp_path / "data"))
        win.show()
        captured = {}
        try:
            monkeypatch.setattr(
                win.controller, "test_api",
                lambda timeout=None: captured.setdefault("timeout", timeout),
            )
            win._auto_check_api()
            assert captured.get("timeout") == MainWindow.STARTUP_API_TIMEOUT
        finally:
            win._really_quit = True
            win.close()


# ====================================================================
# D. 日誌讀取退避
# ====================================================================
class TestLogWatcherBackoff:
    """文件不存在時必須指數退避，避免在 CS2 重建日誌的窗口期高頻撞擊。"""

    def _watcher(self, tmp_path, interval=0.25):
        from cs2_translator.log_watcher import LogWatcher

        return LogWatcher(str(tmp_path / "missing.log"), poll_interval=interval)

    def test_backoff_grows_and_caps(self, tmp_path):
        w = self._watcher(tmp_path, 0.25)
        assert w._fail_delay == 0.0

        delays = []
        for _ in range(8):
            w._try_open()
            delays.append(w._fail_delay)

        assert delays[0] == pytest.approx(0.25), f"首次退避應等於輪詢間隔：{delays}"
        assert delays[1] == pytest.approx(0.5), f"應翻倍：{delays}"
        assert delays[2] == pytest.approx(1.0)
        assert max(delays) <= w._backoff_max + 1e-9, "不得超過上限"

    def test_backoff_resets_on_success(self, tmp_path):
        """打开失败后若成功，退避必须归零（否则恢复响应会慢一拍）。"""
        from cs2_translator.log_watcher import LogWatcher

        missing = tmp_path / "later.log"
        w = LogWatcher(str(missing), poll_interval=0.25)
        for _ in range(3):
            w._try_open()          # 文件不存在 -> 失败
        assert w._fail_delay > 0, "失败后应有退避"

        # 文件出现了 -> 成功打开 -> 归零
        missing.write_text("", encoding="utf-8")
        w._try_open()
        assert w._fail_delay == 0.0, "成功打开后必须复位"
        w._close_file()

    def test_next_wait_uses_backoff(self, tmp_path):
        w = self._watcher(tmp_path, 0.25)
        assert w._next_wait() == pytest.approx(0.25)
        w._try_open()   # 失败一次 -> 0.25
        w._try_open()   # 0.5
        assert w._next_wait() == pytest.approx(0.5)

    def test_set_poll_interval_is_runtime_adjustable(self, tmp_path):
        w = self._watcher(tmp_path, 0.25)
        w.set_poll_interval(1.5)
        assert w.poll_interval == pytest.approx(1.5)
        # 非法值应被夹住，不能变成 0 导致忙循环
        w.set_poll_interval(0)
        assert w.poll_interval >= 0.02

    def test_shrink_interval_releases_backoff(self, tmp_path):
        """间隔调小（切回桌面）时退避不该继续压着。"""
        w = self._watcher(tmp_path, 0.25)
        for _ in range(4):
            w._try_open()
        assert w._fail_delay > 1.0

        w.set_poll_interval(0.25)
        assert w._fail_delay <= 0.25 + 1e-9


# ====================================================================
# E. 每秒統計改為快取
# ====================================================================
class TestStatsCache:
    def test_stats_are_cached_within_ttl(self, qapp, tmp_path, monkeypatch):
        from cs2_translator.config import ConfigManager
        from cs2_translator.ui.main_window import MainWindow

        cfg = AppConfig(cs2_log_path=str(tmp_path / "console.log"), api_key="sk",
                        model="gpt-4o-mini", log_poll_interval=0.05)
        win = MainWindow(cfg, ConfigManager(data_dir=tmp_path / "data"))
        win.show()
        calls = {"n": 0}
        try:
            def fake_stats():
                calls["n"] += 1
                return {"cache_hit_rate": 0.5, "saved_requests": 3, "memory_count": 10}

            monkeypatch.setattr(win.context, "memory_stats", fake_stats)
            win._invalidate_stats_cache()

            for _ in range(10):
                win._cached_memory_stats()
            assert calls["n"] == 1, f"TTL 內只能查一次，實際 {calls['n']}"

            win._invalidate_stats_cache()
            win._cached_memory_stats()
            assert calls["n"] == 2, "失效後應重查"
        finally:
            win._really_quit = True
            win.close()

    def test_refresh_status_does_not_query_every_second(self, qapp, tmp_path, monkeypatch):
        """連跑 5 次 _refresh_status，統計查詢最多 1 次。"""
        from cs2_translator.config import ConfigManager
        from cs2_translator.ui.main_window import MainWindow

        cfg = AppConfig(cs2_log_path=str(tmp_path / "console.log"), api_key="sk",
                        model="gpt-4o-mini", log_poll_interval=0.05)
        win = MainWindow(cfg, ConfigManager(data_dir=tmp_path / "data"))
        win.show()
        calls = {"n": 0}
        try:
            def fake_stats():
                calls["n"] += 1
                return {"cache_hit_rate": 0.0, "saved_requests": 0, "memory_count": 0}

            monkeypatch.setattr(win.context, "memory_stats", fake_stats)
            win._invalidate_stats_cache()
            for _ in range(5):
                win._refresh_status()
            assert calls["n"] <= 1, f"每秒刷新不该每秒查 DB，实际 {calls['n']} 次"
        finally:
            win._really_quit = True
            win.close()


# ====================================================================
# 引擎狀態推送降頻（「每秒 2 次重繪」的真正來源）
# ====================================================================
class TestStatusPushThrottling:
    """引擎狀態推送必須在 CS2 前台時降頻。

    真實事故（v1.2.5 發佈後實測）：前台感知只停了 MainWindow 自己的
    _status_timer，但引擎執行緒每 0.5 秒推一次 status，_on_status 收到就做
    整套 _refresh_status。結果「暫停 UI 刷新」等於沒做 —— 窗口仍每秒重繪 2 次，
    CS2 照樣被判 AppHang（01:35:36 事件）。
    修法：狀態推送降到 2 秒，且 _on_status 在前台期間完全不碰 UI。
    """

    @staticmethod
    def _win(tmp_path):
        from cs2_translator.config import ConfigManager
        from cs2_translator.ui.main_window import MainWindow

        cfg = AppConfig(cs2_log_path=str(tmp_path / "console.log"), api_key="sk",
                        model="gpt-4o-mini", log_poll_interval=0.05)
        win = MainWindow(cfg, ConfigManager(data_dir=tmp_path / "data"))
        win.show()
        win._switch_page("live")
        return win

    def test_engine_has_status_interval_setter(self):
        from cs2_translator.engine import TranslationEngine
        from cs2_translator.ui.workers import EngineController

        for cls in (TranslationEngine, EngineController):
            assert hasattr(cls, "set_status_interval"), f"{cls.__name__} 缺少介面"

    def test_status_interval_is_clamped(self):
        from cs2_translator.engine import TranslationEngine

        class Dummy:
            _status_interval = 0.5
            set_status_interval = TranslationEngine.set_status_interval

        d = Dummy()
        TranslationEngine.set_status_interval(d, 99)
        assert d._status_interval <= 5.0
        TranslationEngine.set_status_interval(d, 0)
        assert d._status_interval >= 0.2

    def test_on_status_skips_ui_while_cs2_active(self, qapp, tmp_path):
        """前台期間收到狀態推送，不得更新 UI。"""
        win = self._win(tmp_path)
        calls = []
        try:
            win._refresh_status = lambda *a, **k: calls.append(1)
            win._cs2_active = False
            win._on_status({"api_state": "ok"})
            assert calls == [1], "正常情況應刷新"

            win._cs2_active = True
            win._pending_status_refresh = False
            win._on_status({"api_state": "ok"})
            assert calls == [1], "CS2 在前台時不得再刷新 UI"
            assert win._pending_status_refresh is True, "應記下待補刷"
        finally:
            win._really_quit = True
            win.close()

    def test_foreground_switch_throttles_status_push(self, qapp, tmp_path, monkeypatch):
        win = self._win(tmp_path)
        seen = []
        try:
            monkeypatch.setattr(win.controller, "set_poll_interval", lambda v: None)
            monkeypatch.setattr(win.controller, "set_status_interval",
                                lambda v: seen.append(v))
            win._cs2_active = False

            win._on_cs2_foreground_changed(True)
            assert seen[-1] == pytest.approx(win.CS2_ACTIVE_STATUS_INTERVAL)
            assert win.CS2_ACTIVE_STATUS_INTERVAL >= 1.0, "必須明顯降頻"

            win._on_cs2_foreground_changed(False)
            assert seen[-1] == pytest.approx(0.5), "回到桌面應恢復 0.5 秒"
        finally:
            win._really_quit = True
            win.close()


# ====================================================================
# 前台模式下暫停 UI 更新
# ====================================================================
class TestSuspendedUpdates:
    """CS2 在前台时只入队不渲染，回到桌面一次性补齐（不漏不重）。"""

    @staticmethod
    def _win(qapp, tmp_path):
        from cs2_translator.config import ConfigManager
        from cs2_translator.ui.main_window import MainWindow

        cfg = AppConfig(cs2_log_path=str(tmp_path / "console.log"), api_key="sk",
                        model="gpt-4o-mini", log_poll_interval=0.05)
        win = MainWindow(cfg, ConfigManager(data_dir=tmp_path / "data"))
        win.show()
        win._switch_page("live")
        qapp.processEvents()
        return win

    @staticmethod
    def _msg(i):
        return {"sender": f"p{i}", "source": f"message {i}", "translated": "",
                "channel": "ALL", "time": "12:00", "raw": f"raw {i}"}

    def test_suspended_add_keeps_data_but_defers_render(self, qapp, tmp_path):
        win = self._win(qapp, tmp_path)
        try:
            page = win.live_page
            page.set_live_updates_suspended(True)
            assert page.updates_suspended is True

            for i in range(5):
                page.add_message(self._msg(i))
            assert page.model.rowCount() == 5, "暫停期間資料仍必須完整入隊"
            assert page._pending_flush is True, "應標記待補齊"
        finally:
            win._really_quit = True
            win.close()

    def test_flush_updates_hint_and_clears_flag(self, qapp, tmp_path):
        win = self._win(qapp, tmp_path)
        try:
            page = win.live_page
            page.set_live_updates_suspended(True)
            for i in range(3):
                page.add_message(self._msg(i))

            page.set_live_updates_suspended(False)
            page.flush_suspended_updates()
            qapp.processEvents()

            assert page._pending_flush is False
            assert "3" in page.stream_hint.text(), page.stream_hint.text()
            assert page.list_view.isVisible()
        finally:
            win._really_quit = True
            win.close()

    def test_no_duplicate_after_flush(self, qapp, tmp_path):
        """補齊渲染不得重複插入或遺失訊息。"""
        win = self._win(qapp, tmp_path)
        try:
            page = win.live_page
            page.set_live_updates_suspended(True)
            for i in range(4):
                page.add_message(self._msg(i))
            page.set_live_updates_suspended(False)
            page.flush_suspended_updates()
            page.flush_suspended_updates()   # 重複呼叫應無副作用
            qapp.processEvents()

            rows = [r["source"] for r in page.model.rows()]
            assert rows == [f"message {i}" for i in range(4)], rows
        finally:
            win._really_quit = True
            win.close()

    def test_translation_during_suspension_still_updates_model(self, qapp, tmp_path):
        """暫停期間譯文仍要寫進 model（資料完整），只是不渲染。"""
        win = self._win(qapp, tmp_path)
        try:
            page = win.live_page

            class M:
                def __init__(self, s, c):
                    self.sender, self.content = s, c

            msg = M("p0", "message 0")
            record = {"sender": "p0", "source": "message 0", "translated": "",
                      "channel": "ALL", "time": "12:00", "raw": "raw 0",
                      "message": msg, "state": "pending"}

            page.set_live_updates_suspended(True)
            page.add_message(record)
            # find_by_message 按对象身份匹配，必须复用同一个 message 物件
            page.update_translation(msg, "译文", "api", "done")

            rows = page.model.rows()
            assert rows[0]["translated"] == "译文", "暫停期間譯文仍要寫進 model"
            assert rows[0]["state"] == "done"
            assert page._pending_flush is True, "仍需標記待補齊"
        finally:
            win._really_quit = True
            win.close()

    def test_foreground_switch_changes_poll_interval(self, qapp, tmp_path, monkeypatch):
        """CS2 進前台 -> 降頻；離開 -> 恢復。

        注意：這個測試會受「執行測試時使用者是否正在玩 CS2」影響 ——
        前台監視器啟動時會立刻判一次，如果 CS2 真在前台，狀態已經是 True。
        所以先強制把狀態歸零，再測翻轉。
        """
        win = self._win(qapp, tmp_path)
        observed = []
        try:
            monkeypatch.setattr(win.controller, "set_poll_interval",
                                lambda v: observed.append(v))
            base = win.config.log_poll_interval
            win._cs2_active = False   # 歸零，避免受真實前台狀態干擾

            win._on_cs2_foreground_changed(True)
            assert observed, "應呼叫 set_poll_interval"
            assert observed[-1] == pytest.approx(win.CS2_ACTIVE_POLL_INTERVAL)
            assert win.live_page.updates_suspended is True
            assert not win._status_timer.isActive(), "狀態列刷新必須暫停"

            win._on_cs2_foreground_changed(False)
            assert observed[-1] == pytest.approx(base)
            assert win.live_page.updates_suspended is False
            assert win._status_timer.isActive(), "回到桌面必須恢復"
        finally:
            win._really_quit = True
            win.close()
