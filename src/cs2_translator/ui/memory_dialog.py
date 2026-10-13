"""翻译记忆库管理与人工纠错对话框。

- CorrectionEditDialog：编辑某条译文并保存为“已确认”的人工修正；
- MemoryManagerDialog：缓存记录 / 术语 / 模板的查看、导出、清空、备份，
  以及命中统计展示。

所有变更直接写入 SQLite（引擎持有的连接通过 WAL 可见）。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..memory import DIRECTION_INCOMING, DIRECTION_REPLY, TranslationMemory
from ..memory.templates import TemplateError, compile_template

logger = logging.getLogger("cs2_translator.ui.memory_dialog")

DIRECTION_LABELS = {
    DIRECTION_INCOMING: "外语→中文",
    DIRECTION_REPLY: "中文→外语",
}

HIT_KIND_LABELS = {
    "correction": "人工修正",
    "exact": "缓存",
    "term": "术语",
    "template": "模板",
}


def hit_kind_label(kind: str) -> str:
    """命中来源的界面展示文本（含在线 API）。"""
    return HIT_KIND_LABELS.get(kind or "", "在线 API")


class CorrectionEditDialog(QDialog):
    """编辑译文并保存为已确认的人工修正。"""

    def __init__(self, original: str, current: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑译文（保存为人工修正）")
        self.resize(520, 320)
        self.new_text = None

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("原文："))
        original_edit = QPlainTextEdit()
        original_edit.setPlainText(original)
        original_edit.setReadOnly(True)
        original_edit.setMaximumHeight(90)
        layout.addWidget(original_edit)

        layout.addWidget(QLabel("修正后的译文（保存后优先级最高，立即生效）："))
        self.translation_edit = QPlainTextEdit()
        self.translation_edit.setPlainText(current or "")
        layout.addWidget(self.translation_edit, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        text = self.translation_edit.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "提示", "译文不能为空。")
            return
        self.new_text = text
        self.accept()


class MemoryManagerDialog(QDialog):
    """翻译记忆库管理：统计 / 缓存 / 术语 / 模板。"""

    def __init__(self, config: AppConfig, data_dir, parent=None):
        super().__init__(parent)
        self.config = config
        self.data_dir = data_dir
        self.setWindowTitle("翻译记忆库管理")
        self.resize(920, 620)
        self.memory = TranslationMemory(
            data_dir / "memory.db",
            enabled=True,
            history_enabled=config.save_memory_history,
        )
        self._build_ui()
        self.reload_all()

    # ==================================================================
    # UI 构建
    # ==================================================================
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        # ---- 统计 ----
        stats_group = QGroupBox("命中统计（重启后保留）")
        stats_layout = QGridLayout(stats_group)
        self.stats_labels = {}
        stats_items = [
            ("exact_hits", "精确缓存命中"),
            ("term_hits", "术语命中"),
            ("template_hits", "模板命中"),
            ("api_calls", "API 调用"),
            ("successes", "翻译成功"),
            ("failures", "翻译失败"),
            ("cache_hit_rate", "缓存命中率"),
            ("saved_requests", "节省请求数"),
            ("estimated_savings", "预计节省费用"),
        ]
        for index, (key, label) in enumerate(stats_items):
            name_label = QLabel(label + ":")
            value_label = QLabel("-")
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.stats_labels[key] = value_label
            stats_layout.addWidget(name_label, index // 3, (index % 3) * 2)
            stats_layout.addWidget(value_label, index // 3, (index % 3) * 2 + 1)
        layout.addWidget(stats_group)

        # ---- 选项卡 ----
        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_cache_tab(), "缓存记录")
        self.tabs.addTab(self._build_terms_tab(), "游戏术语")
        self.tabs.addTab(self._build_templates_tab(), "句式模板")
        layout.addWidget(self.tabs, 1)

        # ---- 底部按钮 ----
        bottom = QHBoxLayout()
        self.export_json_button = QPushButton("导出 JSON")
        self.export_json_button.clicked.connect(self._on_export_json)
        bottom.addWidget(self.export_json_button)
        self.export_csv_button = QPushButton("导出 CSV")
        self.export_csv_button.clicked.connect(self._on_export_csv)
        bottom.addWidget(self.export_csv_button)
        self.backup_button = QPushButton("备份数据库")
        self.backup_button.clicked.connect(self._on_backup)
        bottom.addWidget(self.backup_button)
        bottom.addStretch(1)
        self.clear_button = QPushButton("清空缓存")
        self.clear_button.setObjectName("danger")
        self.clear_button.clicked.connect(self._on_clear)
        bottom.addWidget(self.clear_button)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        bottom.addWidget(close_button)
        layout.addLayout(bottom)

    # ---------------- 缓存页 ----------------
    def _build_cache_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        search_row = QHBoxLayout()
        self.cache_search = QLineEdit()
        self.cache_search.setPlaceholderText("搜索原文或译文…")
        self.cache_search.textChanged.connect(self.reload_cache)
        search_row.addWidget(self.cache_search)
        self.cache_confirmed_only = QPushButton("仅看已确认")
        self.cache_confirmed_only.setCheckable(True)
        self.cache_confirmed_only.toggled.connect(self.reload_cache)
        search_row.addWidget(self.cache_confirmed_only)
        search_row.addStretch(1)
        layout.addLayout(search_row)

        self.cache_table = QTableWidget(0, 7)
        self.cache_table.setHorizontalHeaderLabels(
            ["原文", "译文", "方向", "目标语言", "已确认", "命中次数", "更新时间"]
        )
        self.cache_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cache_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cache_table.setAlternatingRowColors(True)
        self.cache_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.cache_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.cache_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cache_table.customContextMenuRequested.connect(self._on_cache_context_menu)
        self.cache_table.doubleClicked.connect(self._on_cache_edit)
        layout.addWidget(self.cache_table, 1)

        hint = QLabel("提示：双击行可编辑译文（保存为人工修正）；右键可删除记忆或恢复上一版本。")
        hint.setObjectName("hintLabel")
        layout.addWidget(hint)
        return widget

    # ---------------- 术语页 ----------------
    def _build_terms_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        row = QHBoxLayout()
        self.term_search = QLineEdit()
        self.term_search.setPlaceholderText("搜索术语…")
        self.term_search.textChanged.connect(self.reload_terms)
        row.addWidget(self.term_search)
        add_button = QPushButton("添加术语")
        add_button.clicked.connect(self._on_term_add)
        row.addWidget(add_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.terms_table = QTableWidget(0, 6)
        self.terms_table.setHorizontalHeaderLabels(
            ["术语原文", "译文", "方向", "目标语言", "分类", "已确认"]
        )
        self.terms_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.terms_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.terms_table.setAlternatingRowColors(True)
        self.terms_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.terms_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.terms_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.terms_table.customContextMenuRequested.connect(self._on_term_context_menu)
        layout.addWidget(self.terms_table, 1)
        return widget

    # ---------------- 模板页 ----------------
    def _build_templates_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        row = QHBoxLayout()
        hint = QLabel(
            "模板示例：They're pushing {site}! → 他们正在进攻 {site} 点！"
            "（{site} 为点位变量，仅已确认模板会被复用）"
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        row.addWidget(hint, 1)
        add_button = QPushButton("添加模板")
        add_button.clicked.connect(self._on_template_add)
        row.addWidget(add_button)
        layout.addLayout(row)

        self.templates_table = QTableWidget(0, 6)
        self.templates_table.setHorizontalHeaderLabels(
            ["原文模板", "译文模板", "方向", "目标语言", "已确认", "使用次数"]
        )
        self.templates_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.templates_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.templates_table.setAlternatingRowColors(True)
        self.templates_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.templates_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.templates_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.templates_table.customContextMenuRequested.connect(self._on_template_context_menu)
        layout.addWidget(self.templates_table, 1)
        return widget

    # ==================================================================
    # 数据加载
    # ==================================================================
    def reload_all(self) -> None:
        self.reload_stats()
        self.reload_cache()
        self.reload_terms()
        self.reload_templates()

    def reload_stats(self) -> None:
        stats = self.memory.get_stats(self.config.memory_price_per_1k_tokens)
        for key in (
            "exact_hits", "term_hits", "template_hits",
            "api_calls", "successes", "failures", "saved_requests",
        ):
            self.stats_labels[key].setText(str(stats[key]))
        self.stats_labels["cache_hit_rate"].setText(f"{stats['cache_hit_rate'] * 100:.1f}%")
        savings = stats.get("estimated_savings")
        if savings is None:
            self.stats_labels["estimated_savings"].setText("未配置价格，仅统计请求数")
        else:
            self.stats_labels["estimated_savings"].setText(f"约 ¥{savings:.4f}")

    def reload_cache(self) -> None:
        keyword = self.cache_search.text().strip() or None
        rows = self.memory.list_memory(
            keyword=keyword,
            confirmed_only=self.cache_confirmed_only.isChecked(),
            limit=1000,
        )
        self.cache_table.setRowCount(len(rows))
        for row_index, entry in enumerate(rows):
            values = [
                entry["original_source"],
                entry["translated_text"],
                DIRECTION_LABELS.get(entry["direction"], entry["direction"]),
                entry["target_language"],
                "是" if entry["confirmed"] else "否",
                str(entry["hit_count"]),
                str(entry["updated_at"])[:19].replace("T", " "),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, entry["id"])
                self.cache_table.setItem(row_index, column, item)

    def reload_terms(self) -> None:
        keyword = self.term_search.text().strip() or None
        rows = self.memory.list_terms(keyword=keyword, limit=1000)
        self.terms_table.setRowCount(len(rows))
        for row_index, entry in enumerate(rows):
            values = [
                entry["source_term"],
                entry["translated_term"],
                DIRECTION_LABELS.get(entry["direction"], entry["direction"]),
                entry["target_language"],
                entry["category"],
                "是" if entry["confirmed"] else "否",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, entry["id"])
                self.terms_table.setItem(row_index, column, item)

    def reload_templates(self) -> None:
        rows = self.memory.list_templates(limit=1000)
        self.templates_table.setRowCount(len(rows))
        for row_index, entry in enumerate(rows):
            values = [
                entry["source_pattern"],
                entry["translated_pattern"],
                DIRECTION_LABELS.get(entry["direction"], entry["direction"]),
                entry["target_language"],
                "是" if entry["confirmed"] else "否",
                str(entry["usage_count"]),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.UserRole, entry["id"])
                self.templates_table.setItem(row_index, column, item)

    # ==================================================================
    # 缓存操作
    # ==================================================================
    def _selected_cache_row_data(self):
        row = self.cache_table.currentRow()
        if row < 0:
            return None
        id_item = self.cache_table.item(row, 0)
        if id_item is None:
            return None
        return {
            "id": id_item.data(Qt.UserRole),
            "original": id_item.text(),
            "translated": (self.cache_table.item(row, 1).text()
                           if self.cache_table.item(row, 1) else ""),
            "direction": self.cache_table.item(row, 2).text(),
            "target": self.cache_table.item(row, 3).text(),
        }

    def _on_cache_edit(self) -> None:
        data = self._selected_cache_row_data()
        if not data:
            return
        dialog = CorrectionEditDialog(data["original"], data["translated"], parent=self)
        if dialog.exec() and dialog.new_text:
            direction = (
                DIRECTION_INCOMING if data["direction"] == DIRECTION_LABELS[DIRECTION_INCOMING]
                else DIRECTION_REPLY
            )
            self.memory.confirm_translation(
                data["original"], data["target"], direction, new_text=dialog.new_text
            )
            self.reload_all()

    def _on_cache_context_menu(self, position) -> None:
        data = self._selected_cache_row_data()
        if not data:
            return
        menu = QMenu(self)
        edit_action = menu.addAction("编辑译文并保存为人工修正…")
        confirm_action = menu.addAction("标记为正确译文")
        restore_action = menu.addAction("恢复上一版本")
        delete_action = menu.addAction("删除该条记忆")
        chosen = menu.exec(self.cache_table.viewport().mapToGlobal(position))
        if chosen == edit_action:
            self._on_cache_edit()
        elif chosen == confirm_action:
            direction = (
                DIRECTION_INCOMING if data["direction"] == DIRECTION_LABELS[DIRECTION_INCOMING]
                else DIRECTION_REPLY
            )
            self.memory.confirm_translation(data["original"], data["target"], direction)
            self.reload_all()
        elif chosen == restore_action:
            restored = self.memory.restore_previous(data["id"])
            if restored is None:
                QMessageBox.information(self, "提示", "没有可恢复的历史版本。")
            else:
                QMessageBox.information(self, "已恢复", f"已恢复为上一版本：\n{restored}")
            self.reload_all()
        elif chosen == delete_action:
            answer = QMessageBox.question(
                self,
                "删除记忆",
                f"确定删除该条翻译记忆？\n\n原文：{data['original']}\n\n下次遇到该句将重新请求在线 API。",
            )
            if answer == QMessageBox.Yes:
                self.memory.delete_memory(data["id"])
                self.reload_all()

    # ==================================================================
    # 术语操作
    # ==================================================================
    def _on_term_add(self) -> None:
        dialog = TermEditDialog(parent=self)
        if dialog.exec():
            try:
                self.memory.add_term(
                    dialog.source_term,
                    dialog.target_language,
                    dialog.translated_term,
                    direction=dialog.direction,
                    category=dialog.category,
                    confirmed=True,
                )
                self.reload_terms()
                self.reload_stats()
            except ValueError as exc:
                QMessageBox.warning(self, "添加失败", str(exc))

    def _on_term_context_menu(self, position) -> None:
        row = self.terms_table.currentRow()
        if row < 0:
            return
        id_item = self.terms_table.item(row, 0)
        if id_item is None:
            return
        term_id = id_item.data(Qt.UserRole)
        menu = QMenu(self)
        edit_action = menu.addAction("编辑译文…")
        confirm_action = menu.addAction("切换确认状态")
        delete_action = menu.addAction("删除术语")
        chosen = menu.exec(self.terms_table.viewport().mapToGlobal(position))
        if chosen == edit_action:
            current = self.terms_table.item(row, 1).text()
            source = id_item.text()
            dialog = CorrectionEditDialog(source, current, parent=self)
            dialog.setWindowTitle("编辑术语译文")
            if dialog.exec() and dialog.new_text:
                self.memory.update_term(term_id, translated_term=dialog.new_text)
                self.reload_terms()
        elif chosen == confirm_action:
            confirmed_text = self.terms_table.item(row, 5).text()
            self.memory.update_term(term_id, confirmed=(confirmed_text != "是"))
            self.reload_terms()
        elif chosen == delete_action:
            answer = QMessageBox.question(
                self, "删除术语", f"确定删除术语「{id_item.text()}」？"
            )
            if answer == QMessageBox.Yes:
                self.memory.delete_term(term_id)
                self.reload_terms()

    # ==================================================================
    # 模板操作
    # ==================================================================
    def _on_template_add(self) -> None:
        dialog = TemplateEditDialog(parent=self)
        if dialog.exec():
            try:
                self.memory.add_template(
                    dialog.source_pattern,
                    dialog.translated_pattern,
                    dialog.target_language,
                    direction=dialog.direction,
                    confirmed=True,
                )
                self.reload_templates()
            except (TemplateError, ValueError) as exc:
                QMessageBox.warning(
                    self,
                    "模板非法",
                    f"模板未保存：{exc}\n\n"
                    "允许的变量类型：site（点位）、number（数字）、weapon（武器）、"
                    "direction（方向）、team（阵营）、player（玩家）。",
                )

    def _on_template_context_menu(self, position) -> None:
        row = self.templates_table.currentRow()
        if row < 0:
            return
        id_item = self.templates_table.item(row, 0)
        if id_item is None:
            return
        template_id = id_item.data(Qt.UserRole)
        menu = QMenu(self)
        confirm_action = menu.addAction("切换确认状态")
        delete_action = menu.addAction("删除模板")
        chosen = menu.exec(self.templates_table.viewport().mapToGlobal(position))
        if chosen == confirm_action:
            confirmed_text = self.templates_table.item(row, 4).text()
            self.memory.update_template(template_id, confirmed=(confirmed_text != "是"))
            self.reload_templates()
        elif chosen == delete_action:
            answer = QMessageBox.question(
                self, "删除模板", f"确定删除模板「{id_item.text()}」？"
            )
            if answer == QMessageBox.Yes:
                self.memory.delete_template(template_id)
                self.reload_templates()

    # ==================================================================
    # 导出 / 清空 / 备份
    # ==================================================================
    def _on_export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出缓存为 JSON", "translation_memory.json", "JSON (*.json)"
        )
        if not path:
            return
        try:
            count = self.memory.export_json(path)
            QMessageBox.information(self, "导出成功", f"已导出 {count} 条记录到：\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _on_export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出缓存为 CSV", "translation_memory.csv", "CSV (*.csv)"
        )
        if not path:
            return
        try:
            count = self.memory.export_csv(path)
            QMessageBox.information(self, "导出成功", f"已导出 {count} 条记录到：\n{path}")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _on_backup(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "备份数据库", "memory_backup.db", "SQLite (*.db)"
        )
        if not path:
            return
        try:
            dest = self.memory.backup(path)
            QMessageBox.information(self, "备份成功", f"数据库已备份到：\n{dest}")
        except OSError as exc:
            QMessageBox.warning(self, "备份失败", str(exc))

    def _on_clear(self) -> None:
        answer = QMessageBox.question(
            self,
            "清空缓存",
            "确定清空全部翻译缓存与历史记录？\n\n"
            "此操作不可撤销。术语、句式模板和累计统计会保留。\n"
            "清空后所有翻译将重新请求在线 API（产生费用）。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        result = self.memory.clear_cache()
        QMessageBox.information(
            self,
            "已清空",
            f"已清空 {result['memory_cleared']} 条缓存与 {result['history_cleared']} 条历史。",
        )
        self.reload_all()

    def closeEvent(self, event) -> None:
        try:
            self.memory.close()
        except Exception:
            pass
        super().closeEvent(event)


class TermEditDialog(QDialog):
    """添加术语的小对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加术语")
        self.setModal(True)
        self.resize(420, 260)

        form = QFormLayout(self)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("例如：rush B 或 保枪")
        form.addRow("术语原文:", self.source_edit)

        self.translated_edit = QLineEdit()
        self.translated_edit.setPlaceholderText("例如：冲B点 或 save")
        form.addRow("术语译文:", self.translated_edit)

        self.direction_combo = QComboBox()
        self.direction_combo.addItem("外语→中文（接收翻译）", DIRECTION_INCOMING)
        self.direction_combo.addItem("中文→外语（回复翻译）", DIRECTION_REPLY)
        form.addRow("方向:", self.direction_combo)

        self.target_edit = QLineEdit("zh")
        self.target_edit.setPlaceholderText("目标语言代码，如 zh/en/ru/ja/ko")
        form.addRow("目标语言:", self.target_edit)

        self.category_edit = QLineEdit()
        self.category_edit.setPlaceholderText("如：战术 / 武器 / 点位 / 道具")
        form.addRow("分类(可选):", self.category_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_accept(self) -> None:
        if not self.source_edit.text().strip() or not self.translated_edit.text().strip():
            QMessageBox.warning(self, "提示", "术语原文与译文不能为空。")
            return
        self.accept()

    @property
    def source_term(self) -> str:
        return self.source_edit.text().strip()

    @property
    def translated_term(self) -> str:
        return self.translated_edit.text().strip()

    @property
    def direction(self) -> str:
        return self.direction_combo.currentData()

    @property
    def target_language(self) -> str:
        return self.target_edit.text().strip().lower() or "zh"

    @property
    def category(self) -> str:
        return self.category_edit.text().strip()


class TemplateEditDialog(QDialog):
    """添加句式模板的小对话框（提交时校验变量合法性）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加句式模板")
        self.setModal(True)
        self.resize(480, 240)

        form = QFormLayout(self)
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("例如：They're pushing {site}!")
        form.addRow("原文模板:", self.source_edit)

        self.translated_edit = QLineEdit()
        self.translated_edit.setPlaceholderText("例如：他们正在进攻 {site} 点！")
        form.addRow("译文模板:", self.translated_edit)

        self.direction_combo = QComboBox()
        self.direction_combo.addItem("外语→中文（接收翻译）", DIRECTION_INCOMING)
        self.direction_combo.addItem("中文→外语（回复翻译）", DIRECTION_REPLY)
        form.addRow("方向:", self.direction_combo)

        self.target_edit = QLineEdit("zh")
        self.target_edit.setPlaceholderText("目标语言代码，如 zh/en/ru")
        form.addRow("目标语言:", self.target_edit)

        hint = QLabel("允许变量：{site} 点位 / {number} 数字 / {weapon} 武器 / {direction} 方向 / {team} 阵营 / {player} 玩家")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        form.addRow(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _on_accept(self) -> None:
        source = self.source_edit.text().strip()
        translated = self.translated_edit.text().strip()
        if not source or not translated:
            QMessageBox.warning(self, "提示", "原文模板与译文模板不能为空。")
            return
        try:
            compile_template(source, translated, self.target_language, self.direction)
        except TemplateError as exc:
            QMessageBox.warning(self, "模板非法", str(exc))
            return
        self.accept()

    @property
    def source_pattern(self) -> str:
        return self.source_edit.text().strip()

    @property
    def translated_pattern(self) -> str:
        return self.translated_edit.text().strip()

    @property
    def direction(self) -> str:
        return self.direction_combo.currentData()

    @property
    def target_language(self) -> str:
        return self.target_edit.text().strip().lower() or "zh"
