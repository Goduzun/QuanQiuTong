"""双向回复面板：中文输入 -> 目标语言翻译 -> 一键复制。

- 目标语言可选：英语/俄语/日语/韩语及其他（可手动输入任意语言代码）；
- 支持按最近一条已识别聊天消息建议目标语言（用户可手动修改）；
- 翻译失败时保留用户输入草稿，不清空、可重试；
- 常用战术短语收藏与快捷插入；
- 仅本地处理与提交必要文本，不自动发送到游戏。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..language import language_display_name, normalize_language_code
from ..phrases import PhraseBook
from ..reply_translator import COMMON_REPLY_TARGETS
from .memory_dialog import hit_kind_label

logger = logging.getLogger("cs2_translator.ui.reply_panel")


def copy_text_to_clipboard(text: str) -> bool:
    """把文本写入系统剪贴板，返回是否成功。"""
    text = text or ""
    if not text:
        return False
    try:
        clipboard = QGuiApplication.clipboard()
        if clipboard is None:
            return False
        clipboard.setText(text)
        return True
    except Exception:
        logger.exception("写入剪贴板失败")
        return False


class ReplyPanel(QWidget):
    """回复翻译面板。"""

    translate_requested = Signal(str, str)   # (中文原文, 目标语言代码)
    phrase_added = Signal(str, str)          # (中文, 目标语言) 供上层学习译文

    def __init__(self, phrase_book: PhraseBook, parent=None):
        super().__init__(parent)
        self.phrase_book = phrase_book
        self._busy = False
        self._last_translation = ""
        self._build_ui()
        self.reload_phrases()

    # ==================================================================
    # UI 构建
    # ==================================================================
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ---- 目标语言行 ----
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("目标语言:"))
        self.lang_combo = QComboBox()
        self.lang_combo.setEditable(True)
        self.lang_combo.setInsertPolicy(QComboBox.NoInsert)
        for code, name in COMMON_REPLY_TARGETS:
            self.lang_combo.addItem(f"{name} ({code})", code)
        self.lang_combo.setCurrentIndex(0)
        lang_row.addWidget(self.lang_combo, 1)

        self.auto_check = QCheckBox("按最近消息自动建议")
        self.auto_check.setChecked(True)
        self.auto_check.setToolTip("启用后，收到外语聊天时自动把目标语言切换为该语言（仍可手动修改）")
        lang_row.addWidget(self.auto_check)

        self.suggest_label = QLabel("")
        self.suggest_label.setObjectName("hintLabel")
        lang_row.addWidget(self.suggest_label)
        layout.addLayout(lang_row)

        # ---- 输入框 ----
        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText("输入中文回复，例如：冲B点，跟我走！（Ctrl+Enter 翻译）")
        self.input_edit.setFixedHeight(90)
        layout.addWidget(self.input_edit, 1)

        # ---- 按钮行 ----
        button_row = QHBoxLayout()
        self.translate_button = QPushButton("翻译回复")
        self.translate_button.setDefault(True)
        self.translate_button.clicked.connect(self._on_translate_clicked)
        button_row.addWidget(self.translate_button)

        self.copy_button = QPushButton("复制译文")
        self.copy_button.clicked.connect(self.copy_translation)
        self.copy_button.setEnabled(False)
        button_row.addWidget(self.copy_button)

        self.clear_button = QPushButton("清空")
        self.clear_button.clicked.connect(self.clear_input)
        button_row.addWidget(self.clear_button)

        self.favorite_button = QPushButton("收藏为常用短语")
        self.favorite_button.clicked.connect(self.add_current_as_phrase)
        button_row.addWidget(self.favorite_button)

        button_row.addStretch(1)
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("hintLabel")
        button_row.addWidget(self.status_label)
        layout.addLayout(button_row)

        # ---- 结果显示 ----
        result_box = QGroupBox("翻译结果")
        result_layout = QVBoxLayout(result_box)
        self.original_label = QLabel("中文原文：-")
        self.original_label.setWordWrap(True)
        self.original_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        result_layout.addWidget(self.original_label)
        self.translated_label = QLabel("译文：-")
        self.translated_label.setWordWrap(True)
        self.translated_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.translated_label.setObjectName("translatedLabel")
        result_layout.addWidget(self.translated_label)
        layout.addWidget(result_box)

        # ---- 常用短语 ----
        phrase_box = QGroupBox("常用战术短语（双击插入，右键删除自定义项）")
        phrase_layout = QVBoxLayout(phrase_box)
        self.phrase_list = QListWidget()
        self.phrase_list.setMaximumHeight(150)
        self.phrase_list.itemDoubleClicked.connect(self._on_phrase_double_clicked)
        self.phrase_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.phrase_list.customContextMenuRequested.connect(self._on_phrase_context_menu)
        phrase_layout.addWidget(self.phrase_list)
        layout.addWidget(phrase_box)

    # ==================================================================
    # 语言选择
    # ==================================================================
    def current_target_language(self) -> str:
        code = self.lang_combo.currentData()
        if not code:
            code = normalize_language_code(self.lang_combo.currentText())
        return code or "en"

    def set_target_language(self, language: str, from_suggestion: bool = False) -> None:
        language = normalize_language_code(language) or "en"
        index = self.lang_combo.findData(language)
        if index >= 0:
            self.lang_combo.setCurrentIndex(index)
        else:
            self.lang_combo.setEditText(language)
        if from_suggestion:
            self.suggest_label.setText(f"建议: {language_display_name(language)}（可修改）")

    def apply_suggestion(self, language: str) -> None:
        """按最近聊天消息更新建议（仅在自动建议开启时改变选择）。"""
        if not language:
            return
        self.suggest_label.setText(f"建议: {language_display_name(language)}（可修改）")
        if self.auto_check.isChecked():
            self.set_target_language(language)

    def auto_suggest_enabled(self) -> bool:
        return self.auto_check.isChecked()

    # ==================================================================
    # 翻译流程
    # ==================================================================
    def _on_translate_clicked(self) -> None:
        if self._busy:
            return
        text = self.input_edit.toPlainText().strip()
        if not text:
            self.status_label.setText("请输入中文内容")
            return
        self.set_busy(True)
        self.status_label.setText("翻译中…")
        self.translate_requested.emit(text, self.current_target_language())

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.translate_button.setEnabled(not busy)
        self.translate_button.setText("翻译中…" if busy else "翻译回复")

    def show_result(self, result: dict) -> None:
        """展示一次回复翻译结果（成功或失败）。"""
        self.set_busy(False)
        original = result.get("original") or ""
        target = result.get("target") or ""
        if result.get("ok"):
            translated = result.get("translated") or ""
            self._last_translation = translated
            self.original_label.setText(f"中文原文：{original}")
            self.translated_label.setText(f"译文（{language_display_name(target)}）：{translated}")
            hit_kind = result.get("hit_kind", "")
            if hit_kind:
                self.status_label.setText(
                    f"{hit_kind_label(hit_kind)}命中（未请求 API），耗时 {result.get('latency_ms', 0):.0f} ms"
                )
            else:
                self.status_label.setText(
                    f"完成，耗时 {result.get('latency_ms', 0):.0f} ms"
                )
            self.copy_button.setEnabled(bool(translated))
            # 若原文正好是收藏短语，则把译文学习回短语簿
            self.learn_translation(original, target, translated)
            self.phrase_added.emit(original, target)
        else:
            # 失败：保留草稿，仅提示错误
            self.original_label.setText(f"中文原文：{original}")
            self.translated_label.setText("译文：翻译失败")
            self.status_label.setText(f"失败：{result.get('error') or '未知错误'}（草稿已保留）")
            self.copy_button.setEnabled(False)
            self._last_translation = ""

    # ==================================================================
    # 复制 / 清空
    # ==================================================================
    def copy_translation(self) -> bool:
        text = self._last_translation
        if not text:
            # 没有缓存译文时，尝试从标签里取
            label = self.translated_label.text()
            if "：" in label:
                text = label.split("：", 1)[1]
        if not text:
            self.status_label.setText("暂无可复制的译文")
            return False
        if copy_text_to_clipboard(text):
            self.status_label.setText("译文已复制到剪贴板")
            return True
        self.status_label.setText("复制失败：无法访问剪贴板")
        return False

    def clear_input(self) -> None:
        self.input_edit.clear()
        self.original_label.setText("中文原文：-")
        self.translated_label.setText("译文：-")
        self.status_label.setText("就绪")
        self._last_translation = ""
        self.copy_button.setEnabled(False)

    def draft_text(self) -> str:
        return self.input_edit.toPlainText()

    # ==================================================================
    # 短语收藏
    # ==================================================================
    def reload_phrases(self) -> None:
        self.phrase_list.clear()
        target = self.current_target_language()
        for phrase in self.phrase_book.all():
            translation = phrase.translation_for(target)
            text = f"{phrase.zh} → {translation}" if translation else phrase.zh
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, phrase.phrase_id)
            item.setToolTip(f"分类: {phrase.category}｜{'内置' if phrase.builtin else '自定义'}")
            self.phrase_list.addItem(item)

    def _selected_phrase_id(self):
        item = self.phrase_list.currentItem()
        return item.data(Qt.UserRole) if item else None

    def _on_phrase_double_clicked(self, item: QListWidgetItem) -> None:
        phrase_id = item.data(Qt.UserRole)
        phrase = self.phrase_book.get(phrase_id)
        if phrase is None:
            return
        translation = phrase.translation_for(self.current_target_language())
        insert_text = translation if translation else phrase.zh
        self.input_edit.insertPlainText(insert_text)
        self.status_label.setText(f"已插入: {insert_text}")

    def _on_phrase_context_menu(self, position) -> None:
        from PySide6.QtWidgets import QMenu

        item = self.phrase_list.itemAt(position)
        if item is None:
            return
        phrase = self.phrase_book.get(item.data(Qt.UserRole))
        if phrase is None:
            return
        menu = QMenu(self)
        insert_action = menu.addAction("插入到输入框")
        insert_original_action = menu.addAction("插入中文原文")
        delete_action = None if phrase.builtin else menu.addAction("删除该收藏")
        chosen = menu.exec(self.phrase_list.mapToGlobal(position))
        if chosen is insert_action:
            translation = phrase.translation_for(self.current_target_language())
            self.input_edit.insertPlainText(translation if translation else phrase.zh)
        elif chosen is insert_original_action:
            self.input_edit.insertPlainText(phrase.zh)
        elif delete_action is not None and chosen is delete_action:
            if self.phrase_book.remove(phrase.phrase_id):
                self.reload_phrases()
                self.status_label.setText("已删除收藏短语")

    def add_current_as_phrase(self) -> None:
        text = self.input_edit.toPlainText().strip()
        if not text:
            self.status_label.setText("输入为空，无法收藏")
            return
        category, ok = QInputDialog.getText(self, "收藏短语", "分类名称:", text="自定义")
        if not ok:
            return
        try:
            self.phrase_book.add(text, category=category.strip() or "自定义")
        except ValueError as exc:
            QMessageBox.warning(self, "收藏失败", str(exc))
            return
        self.reload_phrases()
        self.status_label.setText("已加入常用短语")

    def learn_translation(self, original: str, target_language: str, translated: str) -> None:
        """把一次成功译文写回对应收藏短语（若存在且为用户自定义项）。"""
        if not original or not translated:
            return
        for phrase in self.phrase_book.all():
            if phrase.zh == original:
                if phrase.builtin:
                    return  # 内置短语使用人工整理的译文，不覆盖
                phrase.translations[target_language] = translated
                self.phrase_book.save()
                self.reload_phrases()
                return

    def set_status_text(self, text: str) -> None:
        self.status_label.setText(text)
