"""设置对话框：首次运行引导 + 常规配置。"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import APP_DISPLAY_NAME
from ..chat_parser import ChatParser
from ..config import DEFAULT_API_BASE_URL, DEFAULT_MODEL, AppConfig
from ..language import LANGUAGE_NAMES
from ..reply_translator import COMMON_REPLY_TARGETS
from ..translator import OpenAITranslator, SAMPLE_TRANSLATION_TEXT, TranslatorError

logger = logging.getLogger("cs2_translator.ui.settings_dialog")


class _AsyncTestSignal(QWidget):
    """占位信号载体（QWidget 才能定义 Signal）。"""


class SettingsDialog(QDialog):
    """配置界面。支持首次运行引导与后续修改。"""

    def __init__(self, config_manager, config: AppConfig, parent=None, first_run: bool = False):
        super().__init__(parent)
        self.config_manager = config_manager
        self.config = config
        self.first_run = first_run
        self.setWindowTitle(f"首次运行配置 - {APP_DISPLAY_NAME}" if first_run else "设置")
        self.setMinimumWidth(620)
        self._test_busy = False
        self._build_ui()
        self._load_values()

    # ==================================================================
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        form_host = QVBoxLayout(inner)

        # ---------------- 日志来源 ----------------
        log_group = QGroupBox("CS2 日志来源")
        log_form = QFormLayout(log_group)
        self.log_path_edit = QLineEdit()
        self.log_path_edit.setPlaceholderText(r"例如 C:\Program Files (x86)\Steam\steamapps\common\Counter-Strike Global Offensive\game\csgo\console.log")
        log_form.addRow("日志文件路径:", self.log_path_edit)
        log_buttons = QHBoxLayout()
        self.detect_button = QPushButton("自动检测")
        self.detect_button.clicked.connect(self._on_auto_detect)
        self.browse_button = QPushButton("手动选择…")
        self.browse_button.clicked.connect(self._on_browse)
        self.test_log_button = QPushButton("测试日志读取")
        self.test_log_button.clicked.connect(self._on_test_log)
        log_buttons.addWidget(self.detect_button)
        log_buttons.addWidget(self.browse_button)
        log_buttons.addWidget(self.test_log_button)
        log_buttons.addStretch(1)
        log_form.addRow("", log_buttons)
        self.log_test_label = QLabel("")
        self.log_test_label.setWordWrap(True)
        log_form.addRow("测试结果:", self.log_test_label)
        log_note = QLabel(
            "说明：需要先在 Steam 中为 CS2 添加启动参数 <b>-condebug</b>"
            "（右键 CS2 → 属性 → 启动选项）。本工具只读取该日志，不会修改游戏文件。"
        )
        log_note.setWordWrap(True)
        log_form.addRow("", log_note)
        form_host.addWidget(log_group)

        # ---------------- 翻译 API ----------------
        api_group = QGroupBox("在线翻译 API")
        api_form = QFormLayout(api_group)
        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText(DEFAULT_API_BASE_URL)
        api_form.addRow("API Base URL:", self.base_url_edit)
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        api_form.addRow("API Key:", self.api_key_edit)
        self.show_key_check = QCheckBox("显示 Key")
        self.show_key_check.toggled.connect(self._on_toggle_key_visibility)
        api_form.addRow("", self.show_key_check)
        self.model_edit = QLineEdit()
        self.model_edit.setPlaceholderText(DEFAULT_MODEL)
        api_form.addRow("模型名称:", self.model_edit)
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(1.0, 300.0)
        self.timeout_spin.setSuffix(" 秒")
        api_form.addRow("请求超时:", self.timeout_spin)
        self.retries_spin = QSpinBox()
        self.retries_spin.setRange(0, 5)
        api_form.addRow("失败重试次数:", self.retries_spin)
        self.rate_spin = QSpinBox()
        self.rate_spin.setRange(1, 600)
        self.rate_spin.setSuffix(" 次/分钟")
        api_form.addRow("翻译频率限制:", self.rate_spin)
        api_buttons = QHBoxLayout()
        self.test_api_button = QPushButton("测试 API 连接")
        self.test_api_button.clicked.connect(self._on_test_api)
        api_buttons.addWidget(self.test_api_button)
        api_buttons.addStretch(1)
        api_form.addRow("", api_buttons)
        self.api_test_label = QLabel("")
        self.api_test_label.setWordWrap(True)
        api_form.addRow("测试结果:", self.api_test_label)
        key_note = QLabel(
            "API Key 不会写入普通配置文件：Windows 上使用 DPAPI 加密保存在用户目录，"
            "也不会出现在日志或错误信息中。默认使用 OpenAI 官方接口，也可填写任何兼容 "
            "OpenAI 协议的服务地址。"
        )
        key_note.setWordWrap(True)
        api_form.addRow("", key_note)
        form_host.addWidget(api_group)

        # ---------------- 接收翻译 ----------------
        in_group = QGroupBox("接收翻译（外语 → 中文）")
        in_form = QFormLayout(in_group)
        self.history_check = QCheckBox("启动时读取日志中的历史消息")
        in_form.addRow("", self.history_check)
        self.enabled_langs_edit = QLineEdit()
        self.enabled_langs_edit.setPlaceholderText("en,ru,ja,ko,other")
        in_form.addRow("启用的来源语言:", self.enabled_langs_edit)
        self.translate_check = QCheckBox("启用接收翻译")
        in_form.addRow("", self.translate_check)
        form_host.addWidget(in_group)

        # ---------------- 回复翻译 ----------------
        reply_group = QGroupBox("回复翻译（中文 → 目标语言）")
        reply_form = QFormLayout(reply_group)
        self.reply_lang_combo = QComboBox()
        self.reply_lang_combo.setEditable(True)
        for code, name in COMMON_REPLY_TARGETS:
            self.reply_lang_combo.addItem(f"{name} ({code})", code)
        reply_form.addRow("默认目标语言:", self.reply_lang_combo)
        self.auto_suggest_check = QCheckBox("根据最近一条聊天消息建议目标语言（可手动修改）")
        reply_form.addRow("", self.auto_suggest_check)
        form_host.addWidget(reply_group)

        # ---------------- 翻译记忆库 ----------------
        memory_group = QGroupBox("翻译记忆库（本地 SQLite 缓存，减少 API 费用）")
        memory_form = QFormLayout(memory_group)
        self.memory_enabled_check = QCheckBox("启用本地翻译记忆（已翻译的句子不再重复请求 API）")
        memory_form.addRow("", self.memory_enabled_check)
        self.memory_history_check = QCheckBox("记录翻译历史（关闭后缓存仍然生效，仅不再保留日志表）")
        memory_form.addRow("", self.memory_history_check)
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0.0, 10000.0)
        self.price_spin.setDecimals(4)
        self.price_spin.setSuffix(" 元/千token")
        self.price_spin.setSpecialValueText("不估算")
        memory_form.addRow("每千 token 价格(可选):", self.price_spin)
        memory_buttons = QHBoxLayout()
        self.memory_manage_button = QPushButton("打开记忆库管理…")
        self.memory_manage_button.clicked.connect(self._on_open_memory_manager)
        memory_buttons.addWidget(self.memory_manage_button)
        memory_buttons.addStretch(1)
        memory_form.addRow("", memory_buttons)
        memory_note = QLabel(
            "记忆库保存已翻译句子的原文与译文（不含 API Key），数据库位于用户数据目录 "
            "memory.db。人工修正的译文优先级最高且不会被 AI 结果覆盖；"
            "清空缓存前会二次确认。"
        )
        memory_note.setWordWrap(True)
        memory_form.addRow("", memory_note)
        form_host.addWidget(memory_group)

        # ---------------- 高级 ----------------
        adv_group = QGroupBox("高级")
        adv_form = QFormLayout(adv_group)
        self.max_len_spin = QSpinBox()
        self.max_len_spin.setRange(20, 4000)
        adv_form.addRow("单条消息长度上限:", self.max_len_spin)
        self.queue_spin = QSpinBox()
        self.queue_spin.setRange(10, 5000)
        adv_form.addRow("翻译队列上限:", self.queue_spin)
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 8)
        adv_form.addRow("翻译并发数:", self.workers_spin)
        self.cache_spin = QSpinBox()
        self.cache_spin.setRange(0, 86400)
        self.cache_spin.setSuffix(" 秒")
        adv_form.addRow("翻译缓存时间:", self.cache_spin)
        self.poll_spin = QDoubleSpinBox()
        self.poll_spin.setRange(0.05, 10.0)
        self.poll_spin.setSuffix(" 秒")
        adv_form.addRow("日志轮询间隔:", self.poll_spin)
        self.generic_check = QCheckBox("启用无频道标签的 “玩家名: 消息” 兜底识别（默认关闭）")
        adv_form.addRow("", self.generic_check)
        self.extra_patterns_edit = QPlainTextEdit()
        self.extra_patterns_edit.setMaximumHeight(70)
        self.extra_patterns_edit.setPlaceholderText("每行一条正则，用于扩展自定义日志格式（留空则不使用）")
        adv_form.addRow("自定义聊天正则:", self.extra_patterns_edit)
        self.save_history_check = QCheckBox("保存聊天历史到本地文件（默认关闭）")
        adv_form.addRow("", self.save_history_check)
        self.tray_check = QCheckBox("最小化到系统托盘（关闭窗口时）")
        adv_form.addRow("", self.tray_check)
        form_host.addWidget(adv_group)

        scroll.setWidget(inner)
        layout.addWidget(scroll)

        # ---------------- 按钮 ----------------
        buttons = QDialogButtonBox()
        self.save_button = buttons.addButton("保存设置", QDialogButtonBox.AcceptRole)
        cancel_button = buttons.addButton("取消", QDialogButtonBox.RejectRole)
        self.save_button.clicked.connect(self._on_save)
        cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    # ==================================================================
    def _load_values(self) -> None:
        cfg = self.config
        self.log_path_edit.setText(cfg.cs2_log_path)
        self.base_url_edit.setText(cfg.api_base_url)
        self.api_key_edit.setText(cfg.api_key)
        self.model_edit.setText(cfg.model)
        self.timeout_spin.setValue(cfg.timeout_seconds)
        self.retries_spin.setValue(cfg.max_retries)
        self.rate_spin.setValue(cfg.rate_limit_per_minute)
        self.history_check.setChecked(cfg.read_history_on_start)
        self.enabled_langs_edit.setText(",".join(cfg.enabled_source_languages))
        self.translate_check.setChecked(cfg.translate_enabled)
        index = self.reply_lang_combo.findData(cfg.reply_target_language)
        if index >= 0:
            self.reply_lang_combo.setCurrentIndex(index)
        else:
            self.reply_lang_combo.setEditText(cfg.reply_target_language)
        self.auto_suggest_check.setChecked(cfg.auto_suggest_reply_language)
        self.memory_enabled_check.setChecked(cfg.memory_enabled)
        self.memory_history_check.setChecked(cfg.save_memory_history)
        self.price_spin.setValue(cfg.memory_price_per_1k_tokens)
        self.max_len_spin.setValue(cfg.max_message_length)
        self.queue_spin.setValue(cfg.queue_max_size)
        self.workers_spin.setValue(cfg.translation_workers)
        self.cache_spin.setValue(cfg.cache_ttl_seconds)
        self.poll_spin.setValue(cfg.log_poll_interval)
        self.generic_check.setChecked(cfg.enable_generic_name_pattern)
        self.extra_patterns_edit.setPlainText("\n".join(cfg.extra_chat_patterns))
        self.save_history_check.setChecked(cfg.save_history)
        self.tray_check.setChecked(cfg.minimize_to_tray)

    def _collect(self) -> AppConfig:
        cfg = AppConfig()
        cfg.cs2_log_path = self.log_path_edit.text().strip()
        cfg.api_base_url = self.base_url_edit.text().strip() or DEFAULT_API_BASE_URL
        cfg.api_key = self.api_key_edit.text().strip()
        cfg.model = self.model_edit.text().strip() or DEFAULT_MODEL
        cfg.timeout_seconds = self.timeout_spin.value()
        cfg.max_retries = self.retries_spin.value()
        cfg.rate_limit_per_minute = self.rate_spin.value()
        cfg.read_history_on_start = self.history_check.isChecked()
        langs = [x.strip() for x in self.enabled_langs_edit.text().split(",") if x.strip()]
        cfg.enabled_source_languages = langs or ["en", "ru", "ja", "ko", "other"]
        cfg.translate_enabled = self.translate_check.isChecked()
        data = self.reply_lang_combo.currentData()
        cfg.reply_target_language = data or self.reply_lang_combo.currentText().strip() or "en"
        cfg.auto_suggest_reply_language = self.auto_suggest_check.isChecked()
        cfg.memory_enabled = self.memory_enabled_check.isChecked()
        cfg.save_memory_history = self.memory_history_check.isChecked()
        cfg.memory_price_per_1k_tokens = self.price_spin.value()
        cfg.max_message_length = self.max_len_spin.value()
        cfg.queue_max_size = self.queue_spin.value()
        cfg.translation_workers = self.workers_spin.value()
        cfg.cache_ttl_seconds = self.cache_spin.value()
        cfg.log_poll_interval = self.poll_spin.value()
        cfg.enable_generic_name_pattern = self.generic_check.isChecked()
        cfg.extra_chat_patterns = [
            line.strip()
            for line in self.extra_patterns_edit.toPlainText().splitlines()
            if line.strip()
        ]
        cfg.save_history = self.save_history_check.isChecked()
        cfg.minimize_to_tray = self.tray_check.isChecked()
        cfg.first_run_completed = True
        cfg._coerce()
        return cfg

    def _validate(self, cfg: AppConfig) -> list:
        errors = []
        if not cfg.cs2_log_path:
            errors.append("请填写 CS2 日志文件路径。")
        if not cfg.api_key:
            errors.append("请填写 API Key。")
        if not cfg.model.strip():
            errors.append("请填写模型名称。")
        if cfg.timeout_seconds <= 0:
            errors.append("请求超时必须大于 0。")
        if cfg.rate_limit_per_minute <= 0:
            errors.append("翻译频率限制必须大于 0。")
        return errors

    # ==================================================================
    def _on_save(self) -> None:
        cfg = self._collect()
        errors = self._validate(cfg)
        if errors:
            QMessageBox.warning(self, "配置不完整", "\n".join(errors))
            return
        try:
            self.config_manager.save(cfg)
        except OSError as exc:
            QMessageBox.critical(self, "保存失败", f"无法写入配置文件: {exc}")
            return
        self.config = cfg
        self.accept()

    def _on_toggle_key_visibility(self, checked: bool) -> None:
        self.api_key_edit.setEchoMode(QLineEdit.Normal if checked else QLineEdit.Password)

    def _on_open_memory_manager(self) -> None:
        from .memory_dialog import MemoryManagerDialog

        dialog = MemoryManagerDialog(self.config, self.config_manager.data_dir, parent=self)
        dialog.exec()

    def _on_auto_detect(self) -> None:
        candidates = self.config_manager.auto_detect_log_paths()
        if not candidates:
            QMessageBox.information(
                self, "自动检测", "未找到 Steam 库文件夹，请手动选择日志文件。"
            )
            return
        existing = [c for c in candidates if __import__("os").path.isfile(c)]
        chosen = existing[0] if existing else candidates[0]
        self.log_path_edit.setText(chosen)
        if existing:
            self.log_test_label.setText(f"已检测到日志文件: {chosen}")
        else:
            self.log_test_label.setText(
                "未发现已生成的 console.log（CS2 可能尚未以 -condebug 启动）。\n"
                f"已填入最可能的路径: {chosen}"
            )

    def _on_browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 CS2 console.log", "", "日志文件 (console.log);;所有文件 (*.*)"
        )
        if path:
            self.log_path_edit.setText(path)

    # ==================================================================
    def _on_test_api(self) -> None:
        if self._test_busy:
            return
        cfg = self._collect()
        errors = self._validate(cfg)
        if errors:
            QMessageBox.warning(self, "配置不完整", "\n".join(errors))
            return
        self._test_busy = True
        self.test_api_button.setEnabled(False)
        self.api_test_label.setText("正在测试…")

        def work():
            ok, message = False, ""
            try:
                translator = OpenAITranslator(cfg, api_key=cfg.api_key)
                translated = translator.translate(SAMPLE_TRANSLATION_TEXT)
                ok, message = True, f"连接正常，测试译文: {translated}"
            except TranslatorError as exc:
                ok, message = False, str(exc)
            except Exception as exc:  # noqa: BLE001
                ok, message = False, f"未预期的错误: {exc}"
            finally:
                self._test_busy = False
                self.test_api_button.setEnabled(True)
                self.api_test_label.setText(("✓ " if ok else "✗ ") + message)

        threading.Thread(target=work, name="settings-api-test", daemon=True).start()

    def _on_test_log(self) -> None:
        import os

        path = self.log_path_edit.text().strip()
        if not path:
            self.log_test_label.setText("请先填写日志路径。")
            return
        if not os.path.isfile(path):
            self.log_test_label.setText(
                "✗ 文件不存在。请确认 CS2 已使用 -condebug 启动且路径正确。"
            )
            return
        try:
            with open(path, "rb") as fh:
                data = fh.read(2_000_000)
        except OSError as exc:
            self.log_test_label.setText(f"✗ 无法读取文件: {exc}")
            return
        lines = data.decode("utf-8", errors="replace").splitlines()
        parser = ChatParser()
        count = sum(1 for line in lines if parser.parse(line) is not None)
        self.log_test_label.setText(
            f"✓ 可读，共 {len(lines)} 行，识别到 {count} 条聊天消息。"
            if count
            else f"✓ 可读，共 {len(lines)} 行，但未识别到聊天消息（请确认游戏内确有聊天）。"
        )
