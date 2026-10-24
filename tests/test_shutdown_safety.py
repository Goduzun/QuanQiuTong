# -*- coding: utf-8 -*-
"""新增：關閉流程不得阻塞 UI 執行緒（AppHang 回歸測試）。"""
import time

import pytest

from cs2_translator.config import AppConfig


class TestShutdownIsBounded:
    """關閉視窗必須在 Windows 的 5 秒 AppHang 閾值內完成。

    真實事故：engine.stop() 原本每步等 10 秒（watcher + queue）+ reply_pool
    等齊 + join(2s)，最壞 22 秒阻塞 UI 執行緒。Windows 對超過 5 秒不回應的
    **可見視窗**判 AppHang，事件日誌留下
    「程序全球通.exe 停止与 Windows 交互并已关闭」（v1.2.4 實測踩到）。

    修法：① 先隱藏視窗再收尾 ② 每步短上限 ③ 執行緒池不等（daemon）。
    """

    @staticmethod
    @staticmethod
    def _win(tmp_path):
        from cs2_translator.config import ConfigManager
        from cs2_translator.ui.main_window import MainWindow

        cfg = AppConfig(cs2_log_path=str(tmp_path / "console.log"), api_key="sk",
                        model="gpt-4o-mini", log_poll_interval=0.05)
        win = MainWindow(cfg, ConfigManager(data_dir=tmp_path / "data"))
        win.show()
        return win

    def test_engine_stop_signature_accepts_timeout(self):
        import inspect

        from cs2_translator.engine import TranslationEngine
        from cs2_translator.ui.workers import EngineController

        assert "timeout" in inspect.signature(TranslationEngine.stop).parameters
        assert "timeout" in inspect.signature(EngineController.stop).parameters

    def test_engine_stop_caps_each_step(self):
        """stop() 內部每步上限必須遠小於 5 秒，不得沿用預設 10 秒。"""
        import inspect

        from cs2_translator.engine import TranslationEngine

        src = inspect.getsource(TranslationEngine.stop)
        assert "wait=False" in src, "執行緒池不該 wait=True 卡住關閉流程"
        assert "min(float(timeout), 1.5)" in src or "per_step" in src, \
            "每一步都要有短上限"

    def test_shutdown_hides_window_before_stopping_engine(self, qapp, tmp_path):
        """_shutdown 必須先隱藏視窗，再做耗時收尾。"""
        win = self._win(tmp_path)
        order = []
        try:
            win.hide = lambda: order.append("hide")
            original_stop = win.controller.stop

            def fake_stop(timeout=10.0):
                order.append(("stop", timeout))

            win.controller.stop = fake_stop
            win._really_quit = True
            win._shutdown()
            assert order and order[0] == "hide", f"必須先隱藏視窗，實際順序 {order}"
            stops = [o for o in order if isinstance(o, tuple) and o[0] == "stop"]
            assert stops, "仍必須停止引擎"
            assert stops[0][1] <= 2.0, f"關閉時引擎停止上限應很短，實際 {stops[0][1]}"
        finally:
            win.controller.stop = original_stop
            win._really_quit = True
            win.close()

    def test_shutdown_completes_within_hang_threshold(self, qapp, tmp_path):
        """真實關閉一次，耗時必須 < 5 秒（AppHang 判定線）。"""
        win = self._win(tmp_path)
        qapp.processEvents()
        try:
            started = time.monotonic()
            win._really_quit = True
            win._shutdown()
            elapsed = time.monotonic() - started
            assert elapsed < 5.0, f"關閉耗時 {elapsed:.1f}s，超過 AppHang 閾值"
        finally:
            win.close()

    def test_foreground_monitor_stopped_on_shutdown(self, qapp, tmp_path):
        """收尾時前台監視器要停，否則它還會繼續查前台窗口。"""
        win = self._win(tmp_path)
        monitor = getattr(win, "_foreground_monitor", None)
        try:
            win._really_quit = True
            win._shutdown()
            if monitor is not None:
                assert monitor._timer.isActive() is False, "前台監視器應已停止"
        finally:
            win.close()
