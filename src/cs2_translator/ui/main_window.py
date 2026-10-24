"""主窗口外壳：左侧导航 + 顶部状态栏 + 页面堆栈。

架构变化（相对旧版单页 600 行 MainWindow）：
- ``QStackedWidget`` 承载 5 个独立页面，导航切换不重建页面；
- 顶部状态栏全部绑定真实运行数据（引擎 status / 对局管理器 / 配置），
  不存在"只改颜色没有数据来源"的假状态；
- 页面通过 ``AppContext`` 共享依赖，彼此不互相引用。

信号流：
    TranslationEngine --(线程回调)--> EngineSignals --(Qt 队列)--> 本窗口槽函数
                                                              |
                                          +-------------------+-------------------+
                                          v                   v                   v
                                     LivePage            HistoryPage         状态栏
"""

from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .. import APP_DISPLAY_NAME, __version__, asset_path
from ..config import AppConfig, ConfigManager
from ..memory import DIRECTION_INCOMING, MatchManager
from . import icons
from .context import AppContext
from .design import DARK, TYPO, active_palette, palette_for
from .page_glossary import GlossaryPage
from .page_history import HistoryPage
from .page_live import LivePage
from .page_memory import MemoryPage
from .page_settings import SettingsPage
from .widgets import StatusDot

logger = logging.getLogger("cs2_translator.ui.main_window")

# 导航项：(key, 标签, 图标)
NAV_ITEMS = [
    ("live", "实时翻译", "chat"),
    ("history", "历史对局", "history"),
    ("memory", "翻译记忆", "memory"),
    ("glossary", "词汇表", "glossary"),
    ("settings", "设置", "settings"),
]

PAGE_TITLES = {key: label for key, label, _ in NAV_ITEMS}


class MainWindow(QMainWindow):
    # CS2 在前台时的日志轮询间隔。翻译延迟 1.5s 对聊天完全够用，
    # 而文件系统活动降到原配置（默认 0.25s）的 1/6。
    CS2_ACTIVE_POLL_INTERVAL = 1.5
    # 启动自检的 API 超时：不要沿用翻译的 20s
    STARTUP_API_TIMEOUT = 5.0
    # CS2 在前台时，引擎状态推送间隔（默认 0.5s → 2s）。
    # 状态推送会驱动整套 UI 刷新，是「每秒 2 次重绘」的真正来源。
    CS2_ACTIVE_STATUS_INTERVAL = 2.0

    def __init__(self, config: AppConfig, config_manager: ConfigManager, parent=None,
                 instance_guard=None):
        super().__init__(parent)
        self.config = config
        self.config_manager = config_manager
        self._really_quit = False
        # 前台感知状态：True = CS2 在全屏/前台，需要降频并暂停非必要 UI 更新
        self._cs2_active = False
        # CS2 在前台期间引擎推来的状态先存着，回到桌面补刷一次
        self._pending_status_refresh = False
        # 缓存统计的 TTL 与快照。状态栏每秒刷新，但 memory_stats() 是一次 DB 查询，
        # 每秒查一次既浪费又是句柄数异常偏高的嫌疑来源之一。
        self._stats_cache: dict | None = None
        self._stats_cache_at = 0.0
        self._stats_cache_ttl = 5.0
        self._instance_guard = instance_guard

        self.setWindowTitle(APP_DISPLAY_NAME)
        self.setMinimumSize(940, 620)
        self.resize(1280, 820)
        self._titlebar_applied = False

        self._build_ui()
        self._build_tray()
        self._create_controller()
        self.context = AppContext(
            config=self.config,
            config_manager=self.config_manager,
            controller=self.controller,
            matches=self.controller.engine.matches,
            data_dir=self.config_manager.data_dir,
        )
        self._build_pages()
        # 让自定义委托与图标使用配置中的主题
        from .design import active_palette, set_active_theme

        set_active_theme(getattr(self.config, "theme", "dark"))
        if hasattr(self.live_page, "delegate"):
            self.live_page.delegate.set_palette(active_palette())
        self.controller.start()

        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start()

        self._switch_page("live")
        self._refresh_status()
        # 单实例守卫：第二个实例启动时，把本实例的窗口拉到前台
        self._wire_instance_guard()
        # 前台感知：CS2 在全屏时降频并暂停非必要 UI 更新
        self._start_foreground_monitor()
        # 启动后自动测一次 API（已有 key 才测）
        self._schedule_startup_api_check()
        # 启动后检查更新（可在设置里关闭）
        self._schedule_update_check()

    # ------------------------------------------------------------------
    # 单实例 / 前台感知
    # ------------------------------------------------------------------
    def _wire_instance_guard(self) -> None:
        guard = self._instance_guard
        if guard is None:
            return
        try:
            guard.activated.connect(self._on_second_instance)
        except Exception:
            logger.exception("连接单实例信号失败")

    def _on_second_instance(self) -> None:
        """另一个实例被启动：把本窗口显示出来并置前。"""
        logger.info("收到第二实例唤醒，显示主窗口")
        self._on_show_window()

    def _start_foreground_monitor(self) -> None:
        """启动前台监视器（1.5s 一次）。

        只在状态翻转时才动 UI/轮询，不会自己变成新的高频源。
        """
        try:
            from ..foreground import ForegroundMonitor

            self._foreground_monitor = ForegroundMonitor(parent=self)
            self._foreground_monitor.state_changed.connect(self._on_cs2_foreground_changed)
            self._foreground_monitor.start()
        except Exception:
            logger.exception("启动前台监视失败")
            self._foreground_monitor = None

    def _on_cs2_foreground_changed(self, active: bool) -> None:
        """CS2 进入/离开前台：降频 + 暂停非必要 UI 更新。

        这是本次修复的核心一条 —— 直接消除「外部窗口在 CS2 全屏时持续重绘与活动」。
        暂停期间消息照常入队（数据不丢），回到桌面时一次性补齐渲染。
        """
        active = bool(active)
        if active == self._cs2_active:
            return
        self._cs2_active = active
        try:
            if active:
                logger.info("CS2 进入前台：降频轮询并暂停 UI 刷新")
                self._status_timer.stop()
                self.controller.set_poll_interval(self.CS2_ACTIVE_POLL_INTERVAL)
                # 关键：引擎线程每 0.5 秒推一次 status，不降频的话
                # 「暂停 UI 刷新」只是停了自己的 QTimer，照样每秒重绘 2 次。
                self.controller.set_status_interval(self.CS2_ACTIVE_STATUS_INTERVAL)
                self.live_page.set_live_updates_suspended(True)
            else:
                logger.info("CS2 离开前台：恢复正常轮询并补齐渲染")
                self.controller.set_poll_interval(self.config.log_poll_interval)
                self.controller.set_status_interval(0.5)
                self.live_page.set_live_updates_suspended(False)
                self.live_page.flush_suspended_updates()
                self._invalidate_stats_cache()
                self._status_timer.start()
                self._pending_status_refresh = False
                self._refresh_status()
        except Exception:
            logger.exception("切换前台模式失败")

    # ==================================================================
    # 外壳构建
    # ==================================================================
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)
        right_layout.addWidget(self._build_topbar())

        self.stack = QStackedWidget()
        right_layout.addWidget(self.stack, 1)
        root.addWidget(right, 1)

    def _build_sidebar(self) -> QWidget:
        self.sidebar = QWidget()
        self.sidebar.setObjectName("sidebar")
        self.sidebar.setFixedWidth(206)
        layout = QVBoxLayout(self.sidebar)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(4)

        # ---- 品牌区 ----
        brand = QHBoxLayout()
        brand.setSpacing(8)
        logo = QLabel()
        logo.setPixmap(icons.make_pixmap("layers", DARK.accent, 20))
        brand.addWidget(logo)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        self.brand_title = QLabel(APP_DISPLAY_NAME)
        self.brand_title.setObjectName("brandTitle")
        brand_text.addWidget(self.brand_title)
        self.brand_sub = QLabel(f"v{__version__}")
        self.brand_sub.setObjectName("brandSub")
        brand_text.addWidget(self.brand_sub)
        brand.addLayout(brand_text)
        brand.addStretch(1)
        layout.addLayout(brand)
        layout.addSpacing(10)

        # ---- 导航 ----
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        for key, label, icon_name in NAV_ITEMS:
            btn = QPushButton(f"  {label}")
            btn.setObjectName("navItem")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setIcon(icons.make_icon(icon_name, DARK.text_muted, 17))
            btn.setIconSize(btn.iconSize())
            btn.clicked.connect(lambda _checked=False, k=key: self._switch_page(k))
            self.nav_group.addButton(btn)
            self.nav_buttons[key] = btn
            layout.addWidget(btn)

        layout.addStretch(1)

        # ---- 底部状态区（全部绑定真实数据）----
        line = QFrame()
        line.setObjectName("dividerLine")
        line.setFixedHeight(1)
        layout.addWidget(line)
        layout.addSpacing(6)

        self.api_dot = StatusDot("API 未测试", "idle")
        self.api_dot.setObjectName("dotIdle")
        layout.addWidget(self.api_dot)

        self.watch_dot = StatusDot("日志未连接", "idle")
        layout.addWidget(self.watch_dot)

        self.match_dot = StatusDot("对局: -", "idle")
        layout.addWidget(self.match_dot)

        self.warn_label = QLabel("")
        self.warn_label.setObjectName("statusWarn")
        self.warn_label.setWordWrap(True)
        self.warn_label.setVisible(False)
        layout.addWidget(self.warn_label)

        layout.addSpacing(6)
        version_label = QLabel(f"版本 v{__version__}")
        version_label.setObjectName("faintLabel")
        layout.addWidget(version_label)
        return self.sidebar

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(58)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(24, 0, 20, 0)
        layout.setSpacing(18)

        self.page_title_label = QLabel("实时翻译")
        self.page_title_label.setObjectName("pageTitle")
        # 显式设置字号字重：避免不同页面间出现字号不一致
        title_font = self.page_title_label.font()
        title_font.setPixelSize(TYPO.size_lg)
        title_font.setBold(True)
        self.page_title_label.setFont(title_font)
        layout.addWidget(self.page_title_label)
        layout.addStretch(1)

        # 右侧状态区：无边框的小圆点 + 次要文字，风格对齐 Harness 的状态行
        self.chip_log = self._make_chip("log")
        self.chip_match = self._make_chip("match")
        self.chip_target = self._make_chip("target")
        self.chip_api = self._make_chip("api")
        for chip in (self.chip_log, self.chip_match, self.chip_target, self.chip_api):
            layout.addWidget(chip)

        layout.addSpacing(4)
        self.settings_button = QPushButton("设置")
        self.settings_button.setObjectName("ghost")
        self.settings_button.setCursor(Qt.PointingHandCursor)
        self.settings_button.setIcon(icons.make_icon("settings", DARK.text_muted, 15))
        self.settings_button.clicked.connect(lambda: self._switch_page("settings"))
        layout.addWidget(self.settings_button)
        return bar

    def _make_chip(self, key: str) -> QWidget:
        """顶部状态项：小圆点 + 次要文字（无边框，贴近 Harness 的状态行）。"""
        chip = QWidget()
        chip.setObjectName("chip")
        lay = QHBoxLayout(chip)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)

        dot = QLabel("○")
        dot.setObjectName("chipDot")
        lay.addWidget(dot)

        value = QLabel("-")
        value.setObjectName("chipText")
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(value)

        setattr(self, f"_chip_{key}_dot", dot)
        setattr(self, f"_chip_{key}_value", value)
        # 兼容旧属性名（_top_log 等），避免其它代码失效
        setattr(self, f"_top_{key}", value)
        return chip

    def _set_chip(self, key: str, state: str, text: str, tooltip: str = "") -> None:
        """更新顶部状态项：只有小圆点带状态色，文字保持次要色（Harness 式克制）。"""
        palette = active_palette()
        colors = {
            "ok": palette.success,
            "warn": palette.warning,
            "error": palette.danger,
            "idle": palette.text_faint,
        }
        marks = {"ok": "●", "warn": "●", "error": "●", "idle": "○"}
        value = getattr(self, f"_chip_{key}_value", None)
        dot = getattr(self, f"_chip_{key}_dot", None)
        if value is not None:
            value.setText(text)
        if dot is not None:
            dot.setText(marks.get(state, "○"))
            dot.setStyleSheet(f"color: {colors.get(state, palette.text_faint)};")
        chip = value.parentWidget() if value is not None else None
        if chip is not None:
            chip.setToolTip(tooltip or text)

    # ==================================================================
    # 页面
    # ==================================================================
    def _build_pages(self) -> None:
        self.pages: dict[str, QWidget] = {}
        self.live_page = LivePage(self.context)
        self.live_page.new_match_requested.connect(self._on_new_match)
        self.live_page.clear_view_requested.connect(self._on_clear_view)
        self.live_page.pause_toggled.connect(self._on_pause_toggled)
        self.live_page.copy_requested.connect(self._on_copy_text)
        self.live_page.correction_requested.connect(self._on_correction)
        self.live_page.reply_requested.connect(self._on_reply_requested)
        self.pages["live"] = self.live_page

        self.history_page = HistoryPage(self.context)
        self.history_page.view_requested.connect(self._on_view_history_match)
        self.pages["history"] = self.history_page

        self.memory_page = MemoryPage(self.context)
        self.memory_page.changed.connect(self._on_memory_changed)
        self.pages["memory"] = self.memory_page

        self.glossary_page = GlossaryPage(self.context)
        self.glossary_page.changed.connect(self._on_memory_changed)
        self.pages["glossary"] = self.glossary_page

        self.settings_page = SettingsPage(self.context)
        self.settings_page.saved.connect(self._on_settings_saved)
        self.pages["settings"] = self.settings_page

        for key, _label, _icon in NAV_ITEMS:
            self.stack.addWidget(self.pages[key])

        self.live_page.apply_config()
        self.live_page.refresh_match_info()
        self.live_page.update_stream_hint()

    def _switch_page(self, key: str) -> None:
        page = self.pages.get(key)
        if page is None:
            return
        self.stack.setCurrentWidget(page)
        btn = self.nav_buttons.get(key)
        if btn is not None and not btn.isChecked():
            btn.setChecked(True)
        self.page_title_label.setText(PAGE_TITLES.get(key, ""))
        if key == "history":
            self.history_page.reload()
        elif key == "memory":
            self.memory_page.reload()
        elif key == "glossary":
            self.glossary_page.reload()
        elif key == "settings":
            self.settings_page.reload()

    # ==================================================================
    # 引擎控制器
    # ==================================================================
    def _create_controller(self) -> None:
        from .workers import EngineController

        self.controller = EngineController(
            self.config, data_dir=self.config_manager.data_dir
        )
        signals = self.controller.signals
        signals.chat.connect(self._on_chat)
        signals.translation.connect(self._on_translation)
        signals.status.connect(self._on_status)
        signals.log_event.connect(self._on_log_event)
        signals.reply_result.connect(self._on_reply_result)
        signals.memory_changed.connect(self._on_memory_changed)
        signals.api_test_done.connect(self._on_api_test_done)
        signals.log_test_done.connect(self._on_log_test_done)
        signals.match_changed.connect(self._on_match_changed)

    def _schedule_startup_api_check(self) -> None:
        """启动后自动测试一次 API，让状态栏的「API 未测试」自己变成结果。

        为什么：状态栏一直显示「API 未测试」没有意义 —— 使用者得手动去设置页
        点一次才知道 API 到底通不通。改成启动后延迟自动测一次，结果直接反映在
        状态栏与设置页；之后仍可在设置页手动重测。
        延迟 1.5 秒是为了让主窗口先画出来，不让网络请求卡住启动。
        """
        if not getattr(self.config, "api_key", ""):
            return
        QTimer.singleShot(1500, self._auto_check_api)

    def _auto_check_api(self) -> None:
        """启动自检：用短超时（5s），不沿用翻译的 20s。

        即使 test_api 已经在后台线程里跑（UI 不会被冻住），自检用的是
        「一次性短超时 + 不重试」，这样最坏情况 5 秒内必有结论，
        不会让一个后台线程拿 20 秒在那等。
        """
        try:
            self.controller.test_api(timeout=self.STARTUP_API_TIMEOUT)
        except Exception:
            logger.exception("启动自动测试 API 失败")

    def _schedule_update_check(self) -> None:
        """启动后检查是否有新版本（可在设置里关闭）。

        隐私：只读 GitHub 公开的 Release 版本号，不发送任何本地资料。
        关闭 config.check_updates 后，程式完全不连 GitHub。
        """
        if not bool(getattr(self.config, "check_updates", True)):
            return
        try:
            from .. import __version__
            from ..update_check import UpdateCheckWorker

            self._update_worker = UpdateCheckWorker(__version__, self)
            self._update_worker.finished_check.connect(self._on_update_checked)
            self._update_worker.start()
        except Exception:
            logger.exception("启动检查更新失败")

    def _on_update_checked(self, has_new: bool, version: str, page: str) -> None:
        if not has_new:
            return
        self.statusBar().showMessage(
            f"发现新版本 v{version}（当前 {__import__('cs2_translator').__version__}）"
            f" · 下载：{page}",
            20000,
        )
        logger.info("发现新版本 v%s，下载页 %s", version, page)

    def _on_match_changed(self, info: dict) -> None:
        """对局切换（含自动识别）时清空消息流并提示。

        引擎内部换局（例如自动识别到新地图）不会走手动按钮那条路径，
        若无此回调，界面会一直停留在上一局的聊天记录上。
        """
        event = info.get("event", "")
        if event != "started":
            return
        self.live_page.clear_stream()
        self.live_page.refresh_match_info()
        self._refresh_status()
        source = info.get("source", "")
        note = info.get("note", "")
        if source == "auto":
            self.statusBar().showMessage(
                f"自动识别到新对局 {info.get('match_id') or ''}（{note}）；"
                "已清空上局显示，历史对局与长期翻译记忆均保留", 8000,
            )

    def _on_api_test_done(self, ok: bool, message: str) -> None:
        page = self.pages.get("settings") if hasattr(self, "pages") else None
        if page is not None:
            page.on_api_test_done(ok, message)
        self._refresh_status()

    def _on_log_test_done(self, result: dict) -> None:
        page = self.pages.get("settings") if hasattr(self, "pages") else None
        if page is not None:
            page.on_log_test_done(result)

    def _rebuild_controller(self, config: AppConfig) -> None:
        old = self.controller
        try:
            old.stop()
        except Exception:
            logger.exception("停止旧引擎失败")
        # 断开旧信号，避免旧的迟到结果进入界面（旧版从未断开，会显示过期译文）
        for sig in (
            old.signals.chat,
            old.signals.translation,
            old.signals.status,
            old.signals.log_event,
            old.signals.reply_result,
            old.signals.memory_changed,
            old.signals.api_test_done,
            old.signals.log_test_done,
            old.signals.match_changed,
        ):
            try:
                sig.disconnect()
            except (RuntimeError, TypeError):
                pass
        self.config = config
        self._create_controller()
        if hasattr(self, "context"):
            self.context.replace_config(config)
            self.context.controller = self.controller
            self.context.matches = self.controller.engine.matches
        self.controller.start()

    # ==================================================================
    # 槽：消息流
    # ==================================================================
    def _on_chat(self, message) -> None:
        self.live_page.add_message({
            "message": message,
            "timestamp": message.timestamp or "",
            "sender": message.sender or "",
            "channel": message.channel_label or message.channel or "",
            "source": message.content or "",
            "translated": "",
            "hit_kind": "",
            "state": "pending",
            "source_language": "",
            "raw": message.raw or "",
        })
        self.live_page.refresh_match_info()
        # 若这条是「本人刚发出的」——说明译文已经粘贴发出去了，
        # 清空回复输入框，让使用者可以直接输入下一句。
        try:
            if self.live_page.note_incoming_for_own_reply(
                message.sender or "", message.content or ""
            ):
                logger.info("检测到本人发言，已清空回复输入框")
                return
        except Exception:
            logger.exception("处理本人发言时出错")
        # 依据最近一条消息建议回复目标语言
        try:
            suggested = self.controller.suggest_reply_language()
            self.live_page.apply_suggested_language(suggested)
        except Exception:
            pass

    def _on_translation(self, message, translated, latency_ms, error, skipped, meta=None) -> None:
        meta = meta or {}
        if meta.get("stale"):
            # 上一局的迟到结果：丢弃，不进入新一局消息流
            logger.info("丢弃上一局的迟到翻译结果 (match=%s)", meta.get("match_id"))
            self._refresh_status()
            return

        if error is not None:
            hit_kind = "error"
            state = "error"
            text = f"翻译失败：{error}"
        elif skipped:
            hit_kind = "skip"
            state = "skipped"
            text = translated
        else:
            hit_kind = meta.get("hit_kind") or "api"
            state = "done"
            text = translated
        self.live_page.update_translation(message, text, hit_kind, state)
        self._refresh_status()

    def _on_status(self, status: dict) -> None:
        """引擎状态回调（来自引擎线程，每 0.5 秒一次）。

        ⚠ 这里才是「每秒 2 次重绘」的真正来源。

        真实事故：前台感知原本只停了 _status_timer（UI 自己的 1 秒 QTimer），
        但引擎线程每 0.5 秒推一次 status，收到就做整套 _refresh_status ——
        暂停等于没做，CS2 全屏时窗口仍在每秒重绘 2 次，卡顿照旧（实测 01:35 复现）。
        现在：CS2 在前台时只记录状态、不碰 UI，回到桌面补刷一次。
        """
        self._last_status = status
        if self._cs2_active:
            self._pending_status_refresh = True
            return
        self._refresh_status(status)

    def _on_log_event(self, event: dict) -> None:
        etype = event.get("type")
        if etype in ("error", "overlong_line"):
            self.warn_label.setText(f"日志读取异常: {etype}")
            self.warn_label.setVisible(True)
        elif etype == "opened":
            self.warn_label.setVisible(False)

    def _on_reply_result(self, result: dict) -> None:
        self.live_page.show_reply_result(result)

    def _on_reply_requested(self, text: str, target_language: str) -> None:
        try:
            self.controller.translate_reply(text, target_language)
        except Exception as exc:
            self.live_page.show_reply_result({"ok": False, "error": str(exc)})

    # ==================================================================
    # 槽：对局与记忆
    # ==================================================================
    def _on_new_match(self) -> None:
        matches = self.context.matches
        if matches is None:
            return
        try:
            self.controller.engine.start_new_match(source="manual")
        except Exception as exc:
            QMessageBox.warning(self, "切换对局失败", str(exc))
            return
        # 清空界面显示，开启新一局
        self.live_page.clear_stream()
        self.live_page.refresh_match_info()
        self._refresh_status()
        self.statusBar().showMessage(
            f"已开始新一局（{matches.current_match_id if matches.current else '-'}）；"
            "历史对局与长期翻译记忆均已保留", 6000,
        )

    def _on_clear_view(self) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("清空本局显示")
        box.setIcon(QMessageBox.Question)
        box.setText("要清空当前对局的显示吗？")
        box.setInformativeText(
            "仅清空界面上的消息列表。\n"
            "历史对局与长期翻译记忆不受影响。\n\n"
            "是否同时删除数据库里本局的消息记录？"
        )
        only_view = box.addButton("仅清空显示", QMessageBox.AcceptRole)
        also_delete = box.addButton("同时删除本局记录", QMessageBox.DestructiveRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is only_view:
            self.context.matches.clear_current_view(delete_records=False)
        elif clicked is also_delete:
            removed = self.context.matches.clear_current_view(delete_records=True)
            self.statusBar().showMessage(f"已删除本局 {removed} 条记录（长期记忆未受影响）", 5000)
        else:
            return
        self.live_page.clear_stream()
        self.live_page.refresh_match_info()

    def _on_pause_toggled(self, paused: bool) -> None:
        try:
            self.controller.set_paused(paused)
        except Exception:
            logger.exception("切换暂停状态失败")
        self.statusBar().showMessage("已暂停翻译" if paused else "已恢复翻译", 3000)

    def _on_copy_text(self, text: str) -> None:
        if not text:
            return
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
            self.statusBar().showMessage("已复制到剪贴板", 2500)

    def _on_correction(self, source: str, target: str, direction: str, current: str) -> None:
        from .memory_dialog import CorrectionEditDialog

        dialog = CorrectionEditDialog(source, current, parent=self)
        if dialog.exec() and dialog.new_text:
            memory_id = self.controller.save_correction(
                source, target, direction, new_text=dialog.new_text
            )
            if memory_id:
                self.statusBar().showMessage("已保存人工修正（优先于缓存与在线结果）", 4000)
                self.live_page.refresh_match_info()
                self._on_memory_changed()
            else:
                QMessageBox.warning(self, "保存失败", "无法写入翻译记忆库。")

    def _on_memory_changed(self) -> None:
        self._refresh_status()

    def _on_view_history_match(self, match_id: str) -> None:
        """在只读对话框中查看某个历史对局的消息。"""
        matches = self.context.matches
        messages = matches.list_messages(match_id, limit=matches.message_limit)

        box = QMessageBox(self)
        box.setWindowTitle(f"历史对局 {match_id}")
        box.setIcon(QMessageBox.Information)
        if not messages:
            box.setText("该对局没有消息记录。")
        else:
            lines = []
            for m in messages[:40]:
                lines.append(f"[{m.timestamp or '-'}] {m.sender} [{m.channel}]")
                lines.append(f"  原文: {m.content}")
                lines.append(f"  译文: {m.translated_text or '(无)'}")
            if len(messages) > 40:
                lines.append(f"… 其余 {len(messages) - 40} 条省略")
            box.setText(f"共 {len(messages)} 条消息")
            box.setDetailedText("\n".join(lines))
        box.exec()

    # ==================================================================
    # 设置
    # ==================================================================
    def _on_settings_saved(self, config: AppConfig) -> None:
        self._rebuild_controller(config)
        self.config = config
        self.live_page.apply_config()
        self.live_page.refresh_match_info()
        # 换局保留上限变更后立即生效
        try:
            matches = self.context.matches
            matches.history_limit = int(getattr(config, "history_match_limit", 10))
            if getattr(config, "auto_cleanup_history", True):
                matches.cleanup_history()
            engine = self.controller.engine
            engine.message_limit = int(getattr(config, "match_message_limit", 300))
            engine.matches.message_limit = int(getattr(config, "match_message_limit", 300))
        except Exception:
            logger.exception("应用对局保留设置失败")
        theme = getattr(config, "theme", "dark")
        self.apply_theme(theme)
        # 套用消息排列顺序（最新在顶部 / 底部）
        try:
            self.live_page.apply_display_order(
                bool(getattr(config, "newest_first", False)))
        except Exception:
            logger.exception("套用消息排列顺序失败")
        self.statusBar().showMessage("设置已保存并重新加载引擎", 5000)
        self._refresh_status()

    def showEvent(self, event) -> None:  # noqa: N802
        """窗口首次显示时应用原生标题栏配色（需要已创建原生窗口句柄）。"""
        super().showEvent(event)
        if not self._titlebar_applied:
            self._titlebar_applied = True
            self._apply_titlebar_theme()

    def _apply_titlebar_theme(self) -> None:
        """让系统标题栏跟随当前主题（深色主题下不再是刺眼的白色横条）。"""
        from .win_theme import apply_titlebar_theme

        palette = active_palette()
        dark = str(getattr(self.config, "theme", "dark")).lower() != "light"
        apply_titlebar_theme(
            self,
            dark=dark,
            caption_color=palette.bg_root,
            text_color=palette.text,
        )

    def apply_theme(self, theme: str) -> None:
        """切换主题：更新 QSS + 自定义委托配色 + 图标颜色。"""
        from .design import build_stylesheet, set_active_theme

        palette = set_active_theme(theme)
        app = QGuiApplication.instance()
        if app is not None:
            app.setStyleSheet(build_stylesheet(palette))
        # 自定义委托必须显式更新（QSS 管不到 QPainter 绘制的内容）
        delegate = getattr(self.live_page, "delegate", None)
        if delegate is not None:
            delegate.set_palette(palette)
        self._recolor_icons(palette)
        # 原生标题栏也要跟着换色
        self._apply_titlebar_theme()

    def _recolor_icons(self, palette) -> None:
        """主题切换后重绘导航与按钮图标，避免浅色背景下图标不可见。"""
        for key, _label, icon_name in NAV_ITEMS:
            btn = self.nav_buttons.get(key)
            if btn is not None:
                btn.setIcon(icons.make_icon(icon_name, palette.text_muted, 17))
        for btn, icon_name in (
            (getattr(self, "settings_button", None), "settings"),
        ):
            if btn is not None:
                btn.setIcon(icons.make_icon(icon_name, palette.text_muted, 15))

    # ==================================================================
    # 状态刷新（全部来自真实数据）
    # ==================================================================
    def _refresh_status(self, status: dict | None = None) -> None:
        status = status if status is not None else getattr(self, "_last_status", None)
        if not status:
            try:
                status = self.controller.engine.status()
            except Exception:
                status = {}

        log_path = status.get("log_path") or ""
        log_exists = bool(status.get("log_exists"))
        watcher = status.get("watcher", {}) or {}
        lines_read = watcher.get("lines", 0)

        if not log_path:
            self._set_chip("log", "warn", "日志未配置")
            self.watch_dot.set_state("warn", "日志未配置")
        elif log_exists:
            name = Path(log_path).name
            self._set_chip("log", "ok" if lines_read else "idle",
                           f"{name} · {lines_read} 行", f"正在监听：{log_path}")
            self.watch_dot.set_state("ok" if lines_read else "idle",
                                     f"监听中 · {lines_read} 行")
        else:
            self._set_chip("log", "warn", "日志不存在", f"文件不存在：{log_path}")
            self.watch_dot.set_state("warn", "日志文件不存在")

        api_state = status.get("api_state", "unknown")
        last_error = status.get("last_error")
        api_text = {"ok": "正常", "error": "错误", "unknown": "未测试"}.get(api_state, "未测试")
        dot_state = {"ok": "ok", "error": "error"}.get(api_state, "idle")
        self._set_chip(
            "api", dot_state, f"API {api_text}",
            f"API 状态：{api_text}" + (f"\n{last_error}" if last_error else ""),
        )
        self.api_dot.set_state(dot_state, f"API {api_text}")

        matches = self.context.matches
        if matches is not None and matches.current is not None:
            info = matches.current
            self._set_chip("match", "ok", f"对局 {info.match_id[-6:]}",
                           f"对局 ID：{info.match_id}\n本局消息 {matches.message_count} 条")
            self.match_dot.set_state("ok", f"对局 {info.match_id[-6:]}")
        else:
            self._set_chip("match", "idle", "无对局")
            self.match_dot.set_state("idle", "对局: -")

        target = str(getattr(self.config, "reply_target_language", "en") or "en")
        self._set_chip("target", "idle", f"→ {target}", f"回复翻译目标语言：{target}")

        queue = status.get("queue", {}) or {}
        self.live_page.match_stats.set_value("pending", queue.get("queue_length", 0))
        self.live_page.update_stream_hint()

        if api_state == "error" and last_error:
            self.warn_label.setText(f"API 错误：{str(last_error)[:120]}")
            self.warn_label.setVisible(True)

        # 缓存状态展示在实时页副标题
        try:
            stats = self._cached_memory_stats()
            rate = stats.get("cache_hit_rate", 0.0) * 100
            saved = stats.get("saved_requests", 0)
            subtitle = (
                f"缓存命中率 {rate:.0f}% · 已节省 {saved} 次请求 · "
                f"长期记忆 {stats.get('memory_count', '-')} 条"
            )
            # 只在文字真的变化时才 setText：QLabel.setText 会触发布局与重绘，
            # 每秒无条件写一次等于每秒强制重绘一次表头。
            if self.live_page.header.subtitle_label.text() != subtitle:
                self.live_page.header.set_subtitle(subtitle)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _invalidate_stats_cache(self) -> None:
        """让下一次 _refresh_status 重新查一次统计。"""
        self._stats_cache = None
        self._stats_cache_at = 0.0

    def _cached_memory_stats(self) -> dict:
        """带 TTL 的统计快照。

        状态栏每秒刷新，但 memory_stats() 每次都做一次 DB 查询（并在引擎侧
        聚合）。实测现场单进程句柄数到过 1989（正常 300~800），每秒一次的
        查询是被怀疑的来源之一。这里把频率降到每 5 秒最多一次。
        """
        now = time.monotonic()
        if (self._stats_cache is not None
                and now - self._stats_cache_at < self._stats_cache_ttl):
            return self._stats_cache
        stats = self.context.memory_stats() or {}
        self._stats_cache = stats
        self._stats_cache_at = now
        return stats

    @staticmethod
    def _short_path(path: str) -> str:
        text = str(path)
        return text if len(text) <= 42 else "…" + text[-40:]

    # ==================================================================
    # 托盘
    # ==================================================================
    def _build_tray(self) -> None:
        self.tray_icon = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        icon_path = asset_path("icon.png")
        icon = QIcon(str(icon_path)) if icon_path else QIcon()
        self.setWindowIcon(icon)
        self.tray_icon = QSystemTrayIcon(icon, self)

        # 注意：QSystemTrayIcon.contextMenu() 在未显式设置前返回 None
        menu = QMenu(self)
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self._on_show_window)
        menu.addAction(show_action)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self._on_quit)
        menu.addAction(quit_action)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._on_tray_activated)
        self.tray_icon.show()

    def _on_show_window(self) -> None:
        self.showNormal()
        self.activateWindow()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger and self.tray_icon is not None:
            menu = self.tray_icon.contextMenu()
            if menu is not None:
                menu.popup(QGuiApplication.primaryScreen().availableGeometry().center())

    def _on_quit(self) -> None:
        self._really_quit = True
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        if (
            not self._really_quit
            and getattr(self.config, "minimize_to_tray", True)
            and self.tray_icon is not None
            and QSystemTrayIcon.isSystemTrayAvailable()
        ):
            event.ignore()
            self.hide()
            self.tray_icon.showMessage(
                APP_DISPLAY_NAME, "程序已最小化到托盘，仍在后台监听日志。", 2000
            )
            return
        self._shutdown()
        event.accept()

    def _shutdown(self) -> None:
        """关闭流程。

        ⚠ 顺序很重要：**先把窗口藏起来**，再做耗时的收尾。

        真实事故：engine.stop() 原本最坏可阻塞 UI 线程 22 秒，Windows 对超过
        5 秒不响应的窗口判 AppHang，事件日志出现
        「程序全球通.exe 停止与 Windows 交互并已关闭」（用户实际碰到过）。
        现在：① 立刻隐藏窗口（没有可见窗口就不会被判 AppHang）
             ② 停掉所有定时器，避免收尾期间再触发数据库查询
             ③ 引擎停止用短上限（见 engine.stop）
        """
        self._status_timer.stop()
        # 前台监视器也要停，否则收尾期间还会去查前台窗口
        monitor = getattr(self, "_foreground_monitor", None)
        if monitor is not None:
            try:
                monitor.stop()
            except Exception:
                logger.debug("停止前台监视失败", exc_info=True)
        # 先藏窗口：AppHang 判定的是「可见窗口停止响应」
        try:
            self.hide()
        except Exception:
            logger.debug("隐藏窗口失败", exc_info=True)
        # 停止各页面的延迟定时器（搜寻防抖等）。
        # 否则它们可能在引擎关闭后触发，对已关闭的数据库查询。
        for page in getattr(self, "pages", {}).values():
            stopper = getattr(page, "stop_timers", None)
            if callable(stopper):
                try:
                    stopper()
                except Exception:
                    logger.exception("停止页面定时器失败")
        # 等待检查更新的后台线程结束（否则 Qt 会警告线程仍在使用中）
        worker = getattr(self, "_update_worker", None)
        if worker is not None and worker.isRunning():
            try:
                worker.quit()
                worker.wait(1000)
            except Exception:
                logger.exception("等待更新检查线程结束失败")
        # 结束当前对局，保证历史对局的 ended_at 被正确写入
        try:
            if self.context.matches is not None:
                self.context.matches.end_current_match()
        except Exception:
            logger.exception("结束对局失败")
        # 短上限：整段收尾必须远低于 Windows 的 5 秒 AppHang 阈值
        started = time.monotonic()
        try:
            self.controller.stop(timeout=1.5)
        except Exception:
            logger.exception("停止引擎失败")
        elapsed = time.monotonic() - started
        if elapsed > 3.0:
            logger.warning("关闭耗时才 %.1fs，接近 AppHang 阈值，需继续优化", elapsed)
        else:
            logger.info("关闭耗时 %.2fs", elapsed)
        if self.tray_icon is not None:
            self.tray_icon.hide()
