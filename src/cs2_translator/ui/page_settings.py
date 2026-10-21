"""设置页面：按功能分为 A 翻译 API / B 语言 / C 日志读取 / D 记录与缓存 / E 界面。

设计要点：
- 所有状态文字都来自真实测试或真实计数，未验证的项目明确标注；
- 保存后由外壳统一重建引擎，保证缓存键随目标语言变化而变化；
- 清理操作都有二次确认，并明确说明删除哪一类数据。
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import (
    DEFAULT_ENABLED_SOURCE_LANGUAGES,
    REPLY_TARGET_LANGUAGES,
    AppConfig,
)
from ..language import LANGUAGE_NAMES
from .widgets import (
    Card,
    danger_button,
    ghost_button,
    hint_label,
    primary_button,
    section_title,
)

logger = logging.getLogger("cs2_translator.ui.page_settings")


class SettingsPage(QWidget):
    """设置页。"""

    saved = Signal(object)   # AppConfig

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self._build_ui()
        self.reload()

    # ==================================================================
    # 构建
    # ==================================================================
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        # 不限制宽度：内容必须铺满可用区域（限制宽度会在右侧留下大片空白）
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        layout = QVBoxLayout(inner)
        layout.setContentsMargins(18, 14, 18, 18)
        layout.setSpacing(12)

        # 页面标题由外壳顶部栏统一显示，这里不再重复写一次「设置」。
        # 这一行只放操作按钮（靠右）。
        head = QHBoxLayout()
        head.addStretch(1)
        self.save_button = primary_button("保存设置", "check")
        self.save_button.clicked.connect(self._on_save)
        head.addWidget(self.save_button)
        self.reset_button = ghost_button("放弃修改", "refresh")
        self.reset_button.clicked.connect(self.reload)
        head.addWidget(self.reset_button)
        layout.addLayout(head)

        layout.addWidget(self._section_api())
        layout.addWidget(self._section_language())
        layout.addWidget(self._section_log())
        layout.addWidget(self._section_data())
        layout.addWidget(self._section_ui())
        layout.addStretch(1)

    # ---- A. 翻译 API ----
    def _section_api(self) -> QWidget:
        card = Card()
        card.add(section_title("翻译 API"))
        card.add(hint_label(
            "支持 OpenAI 官方或任意兼容接口。API Key 在 Windows 上使用 DPAPI 加密存储，"
            "不会以明文写入配置文件。"
        ))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(8)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("https://api.openai.com/v1")
        form.addRow("API Base URL", self.base_url_edit)

        key_row = QHBoxLayout()
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("留空表示不修改已保存的 Key")
        key_row.addWidget(self.api_key_edit, 1)
        self.show_key_button = ghost_button("显示")
        self.show_key_button.setCheckable(True)
        self.show_key_button.toggled.connect(self._on_toggle_key)
        key_row.addWidget(self.show_key_button)
        form.addRow("API Key", key_row)

        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText("例如 gpt-4o-mini / deepseek-chat")
        form.addRow("模型", self.model_edit)

        spin_row = QHBoxLayout()
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(1.0, 300.0)
        self.timeout_spin.setSuffix(" 秒")
        spin_row.addWidget(QLabel("超时"))
        spin_row.addWidget(self.timeout_spin)
        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 5)
        self.retries_spin.setSuffix(" 次")
        spin_row.addWidget(QLabel("重试"))
        spin_row.addWidget(self.retries_spin)
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(1, 600)
        self.rate_spin.setSuffix(" 次/分钟")
        spin_row.addWidget(QLabel("限速"))
        spin_row.addWidget(self.rate_spin)
        spin_row.addStretch(1)
        form.addRow("请求", spin_row)
        card.add_layout(form)

        test_row = QHBoxLayout()
        self.test_api_button = ghost_button("测试连接", "send")
        self.test_api_button.clicked.connect(self._on_test_api)
        test_row.addWidget(self.test_api_button)
        self.api_result_label = QLabel("尚未测试")
        self.api_result_label.setObjectName("faintLabel")
        self.api_result_label.setWordWrap(True)
        test_row.addWidget(self.api_result_label, 1)
        card.add_layout(test_row)
        return card

    # ---- B. 语言设置 ----
    def _section_language(self) -> QWidget:
        card = Card()
        card.add(section_title("语言设置"))
        card.add(hint_label(
            "源语言默认自动检测。目标语言变化会影响缓存键，因此中→外与外语→中分别缓存，互不串用。"
        ))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(8)

        self.enabled_langs_edit = QLineEdit()
        self.enabled_langs_edit.setPlaceholderText("en,ru,ja,ko,other")
        form.addRow("启用的源语言", self.enabled_langs_edit)
        card.add_layout(form)

        btn_row = QHBoxLayout()
        self.all_langs_button = ghost_button("全部启用")
        self.all_langs_button.clicked.connect(lambda: self.enabled_langs_edit.setText(
            ",".join(DEFAULT_ENABLED_SOURCE_LANGUAGES)
        ))
        btn_row.addWidget(self.all_langs_button)
        btn_row.addStretch(1)
        card.add_layout(btn_row)

        form2 = QFormLayout()
        form2.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form2.setSpacing(8)
        self.reply_lang_combo = QComboBox()
        self.reply_lang_combo.setEditable(True)
        for code in REPLY_TARGET_LANGUAGES:
            self.reply_lang_combo.addItem(f"{LANGUAGE_NAMES.get(code, code)} ({code})", code)
        form2.addRow("默认回复目标语言", self.reply_lang_combo)

        # 自己在游戏内的昵称：日志里出现这个发送者的消息，说明译文已经发出去了，
        # 程序据此自动清空回复输入框，方便直接输入下一句。
        self.own_name_edit = QLineEdit()
        self.own_name_edit.setPlaceholderText("填你在游戏里的名字（可留空，靠译文比对自动识别）")
        self.own_name_edit.setToolTip(
            "填了它之后，日志里一出现「你发的那条」就会自动清空回复输入框。\n"
            "留空也能工作：程序会拿你刚复制的译文去比对收到的消息。"
        )
        form2.addRow("我的游戏昵称", self.own_name_edit)

        self.auto_suggest_check = QCheckBox("根据最近一条消息自动建议目标语言")
        form2.addRow("", self.auto_suggest_check)
        self.translate_check = QCheckBox("启用接收翻译（外语 → 中文）")
        form2.addRow("", self.translate_check)
        self.read_history_check = QCheckBox("启动时读取日志末尾已有内容")
        form2.addRow("", self.read_history_check)
        card.add_layout(form2)
        return card

    # ---- C. 日志读取 ----
    def _section_log(self) -> QWidget:
        card = Card()
        card.add(section_title("日志读取"))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(8)

        path_row = QHBoxLayout()
        self.log_path_edit = QLineEdit()
        self.log_path_edit.setPlaceholderText("…\\Counter-Strike Global Offensive\\game\\csgo\\console.log")
        path_row.addWidget(self.log_path_edit, 1)
        self.detect_button = ghost_button("重新检测", "refresh")
        self.detect_button.clicked.connect(self._on_detect_log)
        path_row.addWidget(self.detect_button)
        form.addRow("日志路径", path_row)

        self.log_state_label = QLabel("-")
        self.log_state_label.setObjectName("faintLabel")
        self.log_state_label.setWordWrap(True)
        form.addRow("文件状态", self.log_state_label)

        self.log_test_button = ghost_button("测试日志读取")
        self.log_test_button.clicked.connect(self._on_test_log)
        form.addRow("", self.log_test_button)
        card.add_layout(form)

        self.log_result_label = QLabel(
            "提示：真实聊天是否写入日志，必须在真实 CS2 环境中验证（需要 -condebug 启动参数并在游戏内发言）。"
        )
        self.log_result_label.setObjectName("statusWarn")
        self.log_result_label.setWordWrap(True)
        card.add(self.log_result_label)
        return card

    # ---- D. 记录与缓存 ----
    def _section_data(self) -> QWidget:
        card = Card()
        card.add(section_title("记录与缓存"))
        card.add(hint_label(
            "三层数据严格隔离：① 当前局消息（界面显示）② 历史对局（可清理）"
            "③ 长期翻译记忆（永久保留，只有你主动清理才删除）。"
        ))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(8)

        self.match_limit_spin = QSpinBox()
        self.match_limit_spin.setRange(50, 5000)
        self.match_limit_spin.setSuffix(" 条")
        form.addRow("本局消息显示上限", self.match_limit_spin)

        self.history_limit_spin = QSpinBox()
        self.history_limit_spin.setRange(1, 200)
        self.history_limit_spin.setSuffix(" 局")
        form.addRow("历史对局保留", self.history_limit_spin)

        self.auto_cleanup_check = QCheckBox("换局时自动清理超出上限的旧对局")
        form.addRow("", self.auto_cleanup_check)

        self.reset_on_startup_check = QCheckBox(
            "每次启动清空「实时翻译」与「历史对局」（仅保留长期缓存与词汇表）"
        )
        self.reset_on_startup_check.setToolTip(
            "勾选后：重新打开软件时实时消息与历史对局归零，\n"
            "长期翻译记忆、游戏术语、句式模板全部保留。"
        )
        form.addRow("", self.reset_on_startup_check)

        self.auto_match_check = QCheckBox("自动识别新一局（信号尚未经真实多局验证）")
        self.auto_match_check.setToolTip(
            "基于 [HostStateManager] Host activate 信号。该信号在真实日志中被观察到，"
            "但尚未在真实多局对战场景下验证，默认关闭以策安全。"
        )
        form.addRow("", self.auto_match_check)

        self.memory_enabled_check = QCheckBox("启用本地翻译记忆（减少 API 费用）")
        form.addRow("", self.memory_enabled_check)

        self.save_memory_history_check = QCheckBox("记录翻译历史")
        form.addRow("", self.save_memory_history_check)

        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0.0, 10000.0)
        self.price_spin.setDecimals(4)
        self.price_spin.setSuffix(" 元/千token")
        self.price_spin.setSpecialValueText("不估算")
        form.addRow("每千 token 价格", self.price_spin)
        card.add_layout(form)

        # 真实计数
        self.counts_label = QLabel("-")
        self.counts_label.setObjectName("faintLabel")
        self.counts_label.setWordWrap(True)
        card.add(self.counts_label)

        btn_row = QHBoxLayout()
        self.cleanup_match_button = danger_button("清理历史对局")
        self.cleanup_match_button.clicked.connect(self._on_cleanup_history)
        btn_row.addWidget(self.cleanup_match_button)
        self.clear_memory_button = danger_button("清空长期翻译记忆")
        self.clear_memory_button.clicked.connect(self._on_clear_memory)
        btn_row.addWidget(self.clear_memory_button)
        btn_row.addStretch(1)
        card.add_layout(btn_row)

        adv = QFormLayout()
        adv.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        adv.setSpacing(8)
        self.max_len_spin = QSpinBox()
        self.max_len_spin.setRange(20, 4000)
        self.max_len_spin.setSuffix(" 字符")
        adv.addRow("单条消息长度上限", self.max_len_spin)
        self.queue_spin = QSpinBox()
        self.queue_spin.setRange(10, 5000)
        adv.addRow("翻译队列容量", self.queue_spin)
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 8)
        adv.addRow("翻译线程数", self.workers_spin)
        self.save_history_check = QCheckBox("额外保存 JSONL 聊天历史")
        adv.addRow("", self.save_history_check)
        card.add_layout(adv)
        return card

    # ---- E. 界面 ----
    def _section_ui(self) -> QWidget:
        card = Card()
        card.add(section_title("界面"))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(8)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("深色（默认）", "dark")
        self.theme_combo.addItem("浅色", "light")
        form.addRow("主题", self.theme_combo)
        self.order_combo = QComboBox()
        self.order_combo.addItem("最新在底部 + 自动滚动（默认）", "bottom")
        self.order_combo.addItem("最新在顶部（无需滚动，一眼看到新消息）", "top")
        self.order_combo.setToolTip(
            "选择「最新在顶部」后，新消息会插在最上面，\n"
            "永远不会因为没滚动而漏看。"
        )
        form.addRow("消息排列", self.order_combo)
        self.auto_scroll_check = QCheckBox("新消息自动滚动到最新（仅「最新在底部」时生效）")
        form.addRow("", self.auto_scroll_check)
        self.tray_check = QCheckBox("关闭窗口时最小化到系统托盘")
        form.addRow("", self.tray_check)
        self.check_updates_check = QCheckBox("启动时检查更新（只读公开版本号，不上传任何资料）")
        form.addRow("", self.check_updates_check)
        card.add_layout(form)
        return card

    # ==================================================================
    # 读取 / 写入
    # ==================================================================
    def reload(self) -> None:
        cfg = self.context.config
        self.base_url_edit.setText(cfg.api_base_url)
        self.api_key_edit.clear()
        self.api_key_edit.setPlaceholderText(
            "已保存（留空表示不修改）" if cfg.api_key else "尚未配置"
        )
        self.model_edit.setText(cfg.model)
        self.timeout_spin.setValue(float(cfg.timeout_seconds))
        self.retries_spin.setValue(int(cfg.max_retries))
        self.rate_spin.setValue(int(cfg.rate_limit_per_minute))

        self.enabled_langs_edit.setText(",".join(cfg.enabled_source_languages or []))
        self.set_target_language(cfg.reply_target_language)
        self.auto_suggest_check.setChecked(bool(cfg.auto_suggest_reply_language))
        self.own_name_edit.setText(str(getattr(cfg, "own_player_name", "") or ""))
        self.translate_check.setChecked(bool(cfg.translate_enabled))
        self.read_history_check.setChecked(bool(cfg.read_history_on_start))

        self.log_path_edit.setText(cfg.cs2_log_path)
        self._refresh_log_state()

        self.match_limit_spin.setValue(int(getattr(cfg, "match_message_limit", 300)))
        self.history_limit_spin.setValue(int(getattr(cfg, "history_match_limit", 10)))
        self.auto_cleanup_check.setChecked(bool(getattr(cfg, "auto_cleanup_history", True)))
        self.auto_match_check.setChecked(bool(getattr(cfg, "auto_match_detection", False)))
        self.memory_enabled_check.setChecked(bool(cfg.memory_enabled))
        self.save_memory_history_check.setChecked(bool(cfg.save_memory_history))
        self.price_spin.setValue(float(cfg.memory_price_per_1k_tokens))
        self.max_len_spin.setValue(int(cfg.max_message_length))
        self.queue_spin.setValue(int(cfg.queue_max_size))
        self.workers_spin.setValue(int(cfg.translation_workers))
        self.save_history_check.setChecked(bool(cfg.save_history))

        theme = str(getattr(cfg, "theme", "dark"))
        for i in range(self.theme_combo.count()):
            if self.theme_combo.itemData(i) == theme:
                self.theme_combo.setCurrentIndex(i)
                break
        self.auto_scroll_check.setChecked(bool(cfg.auto_scroll))
        idx = self.order_combo.findData("top" if getattr(cfg, "newest_first", False) else "bottom")
        self.order_combo.setCurrentIndex(max(0, idx))
        self.tray_check.setChecked(bool(cfg.minimize_to_tray))
        self.check_updates_check.setChecked(bool(getattr(cfg, "check_updates", True)))
        self.reset_on_startup_check.setChecked(bool(getattr(cfg, "reset_on_startup", True)))

        self._refresh_counts()

    def set_target_language(self, code: str) -> None:
        for i in range(self.reply_lang_combo.count()):
            if self.reply_lang_combo.itemData(i) == code:
                self.reply_lang_combo.setCurrentIndex(i)
                return
        self.reply_lang_combo.setEditText(str(code or "en"))

    def _current_target_language(self) -> str:
        data = self.reply_lang_combo.currentData()
        if data:
            return str(data)
        text = self.reply_lang_combo.currentText().strip()
        if "(" in text and text.endswith(")"):
            return text[text.rfind("(") + 1:-1].strip() or "en"
        return text or "en"

    def _refresh_log_state(self) -> None:
        from pathlib import Path

        path = self.log_path_edit.text().strip()
        if not path:
            self.log_state_label.setText("未配置日志路径")
            self.log_state_label.setObjectName("statusWarn")
            self._repolish(self.log_state_label)
            return
        target = Path(path)
        if target.is_file():
            try:
                stat = target.stat()
                modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                self.log_state_label.setText(
                    f"存在 · {stat.st_size / 1024:.1f} KB · 最后修改 {modified}"
                )
            except OSError as exc:
                self.log_state_label.setText(f"无法读取文件属性: {exc}")
            self.log_state_label.setObjectName("statusOk")
        else:
            self.log_state_label.setText("文件不存在（请确认 CS2 已用 -condebug 启动过）")
            self.log_state_label.setObjectName("statusWarn")
        self._repolish(self.log_state_label)

    @staticmethod
    def _repolish(widget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _refresh_counts(self) -> None:
        matches = self.context.matches
        memory = self.context.memory
        parts = []
        try:
            parts.append(f"本局消息 {matches.message_count if matches else '-'} 条")
        except Exception:
            parts.append("本局消息 -")
        try:
            parts.append(f"历史对局 {matches.count_ended_matches() if matches else '-'} 局")
        except Exception:
            parts.append("历史对局 -")
        try:
            parts.append(f"长期翻译记忆 {memory.count_memory() if memory else '-'} 条")
        except Exception:
            parts.append("长期翻译记忆 -")
        try:
            parts.append(f"游戏术语 {len(memory.list_terms(limit=100000)) if memory else '-'} 条")
        except Exception:
            parts.append("游戏术语 -")
        self.counts_label.setText("当前：" + " · ".join(parts))

    # ==================================================================
    # 交互
    # ==================================================================
    def _on_toggle_key(self, shown: bool) -> None:
        self.api_key_edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)
        self.show_key_button.setText("隐藏" if shown else "显示")

    def _on_detect_log(self) -> None:
        try:
            candidates = self.context.config_manager.auto_detect_log_paths()
        except Exception as exc:
            QMessageBox.warning(self, "检测失败", str(exc))
            return
        if not candidates:
            QMessageBox.information(
                self, "未找到日志",
                "没有自动找到 console.log。请手动填入路径，"
                "或确认 CS2 已使用 -condebug 参数启动过。",
            )
            return
        self.log_path_edit.setText(str(candidates[0]))
        self._refresh_log_state()
        if len(candidates) > 1:
            QMessageBox.information(
                self, "检测到多个候选",
                "已选择第一个：\n" + str(candidates[0]) + "\n\n其他候选：\n"
                + "\n".join(str(c) for c in candidates[1:6]),
            )

    def _on_test_log(self) -> None:
        self.log_test_button.setEnabled(False)
        self.log_result_label.setObjectName("faintLabel")
        self.log_result_label.setText("正在本地分析日志（只读取，不上传）…")
        self._repolish(self.log_result_label)
        # 控制器在后台线程执行，结果通过 log_test_done 信号回到 GUI 线程
        try:
            self.context.controller.test_log_read(self.log_path_edit.text().strip())
        except Exception as exc:
            self.log_test_button.setEnabled(True)
            self.log_result_label.setObjectName("statusError")
            self.log_result_label.setText(f"日志读取失败：{exc}")
            self._repolish(self.log_result_label)

    def _apply_log_test_result(self, result: dict) -> None:
        if not result.get("ok"):
            self.log_result_label.setObjectName("statusError")
            self.log_result_label.setText(f"日志读取失败：{result.get('error', '未知错误')}")
        else:
            messages = result.get("chat_messages", 0)
            total = result.get("total_lines", 0)
            self.log_result_label.setObjectName("statusOk" if messages else "statusWarn")
            text = (
                f"读取成功：共 {total} 行，识别到 {messages} 条聊天消息。"
            )
            if messages == 0:
                text += (
                    "  未识别到聊天 —— 请确认游戏内确有发言，且日志格式与预期一致。"
                    "（真实聊天是否写入日志只能在真实 CS2 环境中验证）"
                )
            else:
                text += "  注意：这是在本地日志文件上验证，不代表真实对局中的实时性。"
            self.log_result_label.setText(text)
        self._repolish(self.log_result_label)

    def _on_test_api(self) -> None:
        self.test_api_button.setEnabled(False)
        self.api_result_label.setObjectName("faintLabel")
        self.api_result_label.setText("正在测试…")
        self._repolish(self.api_result_label)
        # 控制器在后台线程执行，结果通过 api_test_done 信号回到 GUI 线程
        try:
            self.context.controller.test_api()
        except Exception as exc:
            self.on_api_test_done(False, str(exc))

    def on_api_test_done(self, ok: bool, message: str) -> None:
        """由外壳在 GUI 线程调用（EngineSignals.api_test_done）。"""
        self.test_api_button.setEnabled(True)
        self.api_result_label.setObjectName("statusOk" if ok else "statusError")
        stamp = datetime.now().strftime("%H:%M:%S")
        self.api_result_label.setText(f"[{stamp}] {message}")
        self._repolish(self.api_result_label)

    def on_log_test_done(self, result: dict) -> None:
        """由外壳在 GUI 线程调用（EngineSignals.log_test_done）。"""
        self.log_test_button.setEnabled(True)
        self._apply_log_test_result(result)

    def _on_cleanup_history(self) -> None:
        matches = self.context.matches
        if matches is None:
            return
        limit = int(self.history_limit_spin.value())
        box = QMessageBox(self)
        box.setWindowTitle("清理历史对局")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"只保留最近 {limit} 局历史对局，其余删除。")
        box.setInformativeText(
            "删除内容：历史对局记录 + 对应局内消息。\n"
            "保留内容：长期翻译记忆、游戏术语、句式模板、累计统计。"
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return
        removed = matches.cleanup_history(keep=limit)
        self._refresh_counts()
        QMessageBox.information(
            self, "清理完成", f"已删除 {removed} 个历史对局。\n长期翻译记忆未受影响。"
        )

    def _on_clear_memory(self) -> None:
        memory = self.context.memory
        if memory is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("清空长期翻译记忆")
        box.setIcon(QMessageBox.Critical)
        box.setText("确定清空全部长期翻译记忆吗？此操作不可撤销。")
        box.setInformativeText(
            "删除内容：所有缓存译文（含人工修正）。\n"
            "保留内容：游戏术语、句式模板、历史对局、累计统计。\n\n"
            "清空后所有句子都会重新请求在线 API（可能产生费用）。"
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return
        result = memory.clear_cache()
        self._refresh_counts()
        QMessageBox.information(
            self, "已清空",
            f"删除 {result.get('memory_cleared', 0)} 条长期记忆、"
            f"{result.get('history_cleared', 0)} 条翻译历史。",
        )

    # ==================================================================
    # 保存
    # ==================================================================
    def _collect(self) -> AppConfig:
        cfg = AppConfig()
        # 保留原有密钥与首次运行标记
        cfg.api_key = self.api_key_edit.text().strip() or self.context.config.api_key
        cfg.first_run_completed = True

        cfg.api_base_url = self.base_url_edit.text().strip()
        cfg.model = self.model_edit.text().strip()
        cfg.timeout_seconds = float(self.timeout_spin.value())
        cfg.max_retries = int(self.retries_spin.value())
        cfg.rate_limit_per_minute = int(self.rate_spin.value())

        langs = [x.strip() for x in self.enabled_langs_edit.text().split(",") if x.strip()]
        cfg.enabled_source_languages = langs
        cfg.reply_target_language = self._current_target_language()
        cfg.auto_suggest_reply_language = bool(self.auto_suggest_check.isChecked())
        cfg.own_player_name = self.own_name_edit.text().strip()
        cfg.translate_enabled = bool(self.translate_check.isChecked())
        cfg.read_history_on_start = bool(self.read_history_check.isChecked())

        cfg.cs2_log_path = self.log_path_edit.text().strip()

        cfg.match_message_limit = int(self.match_limit_spin.value())
        cfg.history_match_limit = int(self.history_limit_spin.value())
        cfg.auto_cleanup_history = bool(self.auto_cleanup_check.isChecked())
        cfg.auto_match_detection = bool(self.auto_match_check.isChecked())
        cfg.memory_enabled = bool(self.memory_enabled_check.isChecked())
        cfg.save_memory_history = bool(self.save_memory_history_check.isChecked())
        cfg.memory_price_per_1k_tokens = float(self.price_spin.value())
        cfg.max_message_length = int(self.max_len_spin.value())
        cfg.queue_max_size = int(self.queue_spin.value())
        cfg.translation_workers = int(self.workers_spin.value())
        cfg.save_history = bool(self.save_history_check.isChecked())

        cfg.theme = str(self.theme_combo.currentData() or "dark")
        cfg.sidebar_collapsed = bool(getattr(self.context.config, "sidebar_collapsed", False))
        cfg.auto_scroll = bool(self.auto_scroll_check.isChecked())
        cfg.newest_first = (self.order_combo.currentData() == "top")
        cfg.check_updates = bool(self.check_updates_check.isChecked())
        cfg.minimize_to_tray = bool(self.tray_check.isChecked())
        cfg.reset_on_startup = bool(self.reset_on_startup_check.isChecked())
        cfg._coerce()
        return cfg

    def _validate(self, cfg: AppConfig) -> list[str]:
        problems = []
        if not cfg.cs2_log_path:
            problems.append("尚未设置 CS2 日志路径（可在 C 区点击「重新检测」）")
        if not cfg.api_key:
            problems.append("尚未设置 API Key")
        if not cfg.model:
            problems.append("模型名称不能为空")
        if not cfg.enabled_source_languages:
            problems.append("至少需要启用一种源语言")
        return problems

    def _on_save(self) -> None:
        cfg = self._collect()
        problems = self._validate(cfg)
        if problems:
            box = QMessageBox(self)
            box.setWindowTitle("配置不完整")
            box.setIcon(QMessageBox.Warning)
            box.setText("以下项目需要处理：")
            box.setInformativeText("\n".join(f"• {p}" for p in problems))
            box.setStandardButtons(QMessageBox.Save | QMessageBox.Cancel)
            box.setDefaultButton(QMessageBox.Save)
            if box.exec() != QMessageBox.Save:
                return
        try:
            self.context.config_manager.save(cfg)
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", f"无法写入配置：{exc}")
            return
        self.context.replace_config(cfg)
        self.reload()
        self.saved.emit(cfg)
