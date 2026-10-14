"""翻译记忆页面：长期翻译记忆（永久保留，直到用户主动清理）。

这是 C 层数据：新一局开始绝不会自动清空；下一局再次出现相同文本时继续命中缓存。
人工确认的译文优先级最高，且不会被后续 AI 结果覆盖。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .context import format_datetime
from .design import DARK
from .widgets import (
    Card,
    EmptyState,
    PageHeader,
    StatRow,
    danger_button,
    ghost_button,
    hint_label,
    section_title,
)

logger = logging.getLogger("cs2_translator.ui.page_memory")

COLUMNS = ["原文", "译文", "方向", "目标语言", "来源", "命中次数", "最后使用"]

_DIRECTION_LABELS = {"incoming": "外→中", "reply": "中→外"}


class MemoryPage(QWidget):
    """长期翻译记忆页。"""

    changed = Signal()

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self._rows: list[dict] = []
        self._confirmed_only = False
        # 搜寻输入的防抖计时器（单次触发）
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.reload)
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(12)

        self.header = PageHeader(
            "翻译记忆",
            "长期本地缓存：新一局开始不会清空，相同文本会直接复用译文，从而减少 API 费用。",
        )
        self.export_button = ghost_button("导出", "external")
        self.export_button.clicked.connect(self._on_export)
        self.header.add_action(self.export_button)
        self.clear_button = danger_button("清空翻译记忆", "trash")
        self.clear_button.clicked.connect(self._on_clear)
        self.header.add_action(self.clear_button)
        self.reload_button = ghost_button("刷新", "refresh")
        self.reload_button.clicked.connect(self.reload)
        self.header.add_action(self.reload_button)
        outer.addWidget(self.header)

        # ---- 真实统计 ----
        stats_card = Card()
        stats_card.add(section_title("缓存统计（基于真实程序事件）"))
        self.stats = StatRow([
            ("长期记忆条数", "memory_count"),
            ("API 调用", "api_calls"),
            ("缓存命中", "cache_hits"),
            ("命中率", "hit_rate"),
            ("节省请求", "saved"),
        ])
        stats_card.add(self.stats)
        self.terms_hint = hint_label("")
        stats_card.add(self.terms_hint)
        outer.addWidget(stats_card)

        # ---- 列表 ----
        table_card = Card()
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(section_title("记忆条目"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索原文或译文…")
        self.search_edit.setMaximumWidth(240)
        self.search_edit.setClearButtonEnabled(True)
        # 输入时延迟再查询，避免每个按键都重建整张表（词汇表页同样的坑）
        self.search_edit.textChanged.connect(self._on_search_changed)
        head.addWidget(self.search_edit)

        self.confirmed_button = QPushButton("仅看人工确认")
        self.confirmed_button.setCheckable(True)
        self.confirmed_button.setCursor(Qt.PointingHandCursor)
        self.confirmed_button.toggled.connect(self._on_toggle_confirmed)
        head.addWidget(self.confirmed_button)
        head.addStretch(1)
        table_card.add_layout(head)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.doubleClicked.connect(lambda _i: self._on_edit())
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        for i in (2, 3, 4, 5, 6):
            header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        table_card.add(self.table)

        self.empty_state = EmptyState(
            "还没有翻译记忆",
            "翻译成功后会自动写入这里。你也可以在实时翻译页对译文做人工修正。",
            "memory",
        )
        table_card.add(self.empty_state)
        outer.addWidget(table_card, 1)
        outer.addWidget(hint_label(
            "双击编辑译文并保存为人工修正；右键可标记为正确 / 恢复上一版本 / 删除。"
        ))

    # ------------------------------------------------------------------
    def _on_toggle_confirmed(self, checked: bool) -> None:
        self._confirmed_only = bool(checked)
        self.reload()

    def stop_timers(self) -> None:
        """停止延迟定时器（关闭窗口时由外壳调用，避免关闭后仍触发查询）。"""
        timer = getattr(self, "_search_timer", None)
        if timer is not None:
            timer.stop()

    def reload(self) -> None:
        """重建翻译记忆表格。

        效能关键（与词汇表页同一个坑）：栏位用 ResizeToContents 时，Qt 会在
        **每次** setItem 后重算栏宽，1000 列 × 7 栏就退化成 O(n²)。
        解法：重建期间隐藏表格，填完再显示，并且只呼叫一次
        resizeColumnsToContents()。实测同份资料由数秒降到数十毫秒。
        """
        memory = self.context.memory
        if memory is None:
            return

        keyword = self.search_edit.text().strip() if hasattr(self, "search_edit") else ""
        try:
            self._rows = memory.list_memory(
                keyword=keyword or None,
                confirmed_only=self._confirmed_only,
                limit=1000,
            )
        except Exception:
            logger.exception("读取翻译记忆失败")
            self._rows = []

        self._fill_table(self._rows)

        has_rows = bool(self._rows)
        self.table.setVisible(has_rows)
        self.empty_state.setVisible(not has_rows)
        if has_rows:
            # 只调整 ResizeToContents 的栏（2~6）。
            # 不要用 resizeColumnsToContents()：它会连 Stretch 栏(0,1) 一起
            # 按内容宽度设置，导致表格右侧留下大片空白（「排版很空」的真因）。
            for c in range(2, self.table.columnCount()):
                self.table.resizeColumnToContents(c)
        self._reload_stats()

    def _on_search_changed(self, _text: str) -> None:
        """搜寻框输入：延迟 180ms 再查询。

        用 QTimer 做防抖，避免使用者每敲一个字就重建 1000 列的表格
        （timer 在 _build_ui 建立；没有就退回直接重建）。
        """
        timer = getattr(self, "_search_timer", None)
        if timer is None:
            self.reload()
            return
        timer.start(180)

    def _fill_table(self, rows: list) -> None:
        """把资料填进表格（重建期间隐藏，避开 O(n²) 栏宽重算）。"""
        table = self.table
        was_visible = table.isVisible()
        if was_visible:
            table.setVisible(False)
        try:
            table.setRowCount(len(rows))
            for r, entry in enumerate(rows):
                values = [
                    entry.get("original_source", ""),
                    entry.get("translated_text", ""),
                    _DIRECTION_LABELS.get(entry.get("direction", ""),
                                          entry.get("direction", "")),
                    entry.get("target_language", ""),
                    "人工修正" if entry.get("confirmed") else "在线 API",
                    str(entry.get("hit_count", 0)),
                    format_datetime(entry.get("updated_at", "")),
                ]
                for c, text in enumerate(values):
                    item = QTableWidgetItem(str(text))
                    if c in (4, 5):
                        item.setTextAlignment(Qt.AlignCenter)
                    if c == 0:
                        item.setData(Qt.UserRole, entry.get("id"))
                    table.setItem(r, c, item)
        finally:
            if was_visible:
                table.setVisible(True)

    def _reload_stats(self) -> None:
        memory = self.context.memory
        if memory is None:
            return
        try:
            stats = memory.get_stats(
                price_per_1k_tokens=float(
                    getattr(self.context.config, "memory_price_per_1k_tokens", 0.0) or 0.0
                )
            )
        except Exception:
            logger.exception("读取缓存统计失败")
            return
        self.stats.set_value("memory_count", stats.get("memory_count", memory.count_memory()))
        self.stats.set_value("api_calls", stats.get("api_calls", 0))
        self.stats.set_value("cache_hits", stats.get("cache_hits", 0))
        self.stats.set_value("hit_rate", f"{stats.get('cache_hit_rate', 0.0) * 100:.0f}%")
        self.stats.set_value("saved", stats.get("saved_requests", 0))

        # 用 SQL 聚合取数量，不要 list_terms(limit=100000) 捞进 Python 数长度
        try:
            terms = memory.term_stats().get("total", 0)
        except Exception:
            terms = len(memory.list_terms(limit=100000))
        try:
            templates = memory.template_count()
        except Exception:
            templates = len(memory.list_templates(limit=100000))
        savings = stats.get("estimated_savings")
        extra = f"术语 {terms} 条 · 句式模板 {templates} 条"
        if savings is not None:
            extra += f" · 预计节省 {savings}"
        self.terms_hint.setText(extra)

    # ------------------------------------------------------------------
    def _selected_entry(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row]

    def _on_context_menu(self, position) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        menu = QMenu(self)
        menu.addAction("编辑译文并保存为人工修正…", self._on_edit)
        if not entry.get("confirmed"):
            menu.addAction("标记为正确译文", self._on_confirm)
        menu.addAction("恢复上一版本", self._on_restore)
        menu.addSeparator()
        menu.addAction("删除该条记忆", self._on_delete)
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _on_edit(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        from .memory_dialog import CorrectionEditDialog

        dialog = CorrectionEditDialog(
            entry.get("original_source", ""), entry.get("translated_text", ""), parent=self
        )
        if not dialog.exec() or not dialog.new_text:
            return
        memory = self.context.memory
        if memory is None:
            return
        memory_id = memory.confirm_translation(
            entry.get("original_source", ""),
            entry.get("target_language", ""),
            entry.get("direction", "incoming"),
            new_text=dialog.new_text,
        )
        if memory_id:
            self.reload()
            self.changed.emit()

    def _on_confirm(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        memory = self.context.memory
        if memory is None:
            return
        memory.confirm_translation(
            entry.get("original_source", ""),
            entry.get("target_language", ""),
            entry.get("direction", "incoming"),
        )
        self.reload()
        self.changed.emit()

    def _on_restore(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        memory = self.context.memory
        if memory is None:
            return
        restored = memory.restore_previous(int(entry.get("id")))
        if restored is None:
            QMessageBox.information(self, "无法恢复", "这条记忆没有更早的版本。")
            return
        self.reload()
        self.changed.emit()

    def _on_delete(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("删除记忆")
        box.setIcon(QMessageBox.Warning)
        box.setText("确定删除这条翻译记忆吗？")
        box.setInformativeText(
            f"原文：{entry.get('original_source', '')[:80]}\n\n"
            "删除后该句会重新请求在线 API（可能产生费用）。"
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return
        memory = self.context.memory
        if memory is not None and memory.delete_memory(int(entry.get("id"))):
            self.reload()
            self.changed.emit()

    def _on_export(self) -> None:
        memory = self.context.memory
        if memory is None:
            return
        path, selected = QFileDialog.getSaveFileName(
            self, "导出翻译记忆", "translation_memory.csv",
            "CSV (*.csv);;JSON (*.json)",
        )
        if not path:
            return
        try:
            if path.lower().endswith(".json"):
                count = memory.export_json(path)
            else:
                count = memory.export_csv(path)
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"已导出 {count} 条记录到：\n{path}")

    def _on_clear(self) -> None:
        memory = self.context.memory
        if memory is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle("清空翻译记忆")
        box.setIcon(QMessageBox.Warning)
        box.setText("确定要清空全部长期翻译记忆吗？")
        box.setInformativeText(
            "将删除：所有缓存的译文（含人工修正）。\n"
            "将保留：游戏术语、句式模板、累计统计、历史对局。\n\n"
            "清空后所有句子都会重新请求在线 API，可能产生费用。"
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return
        result = memory.clear_cache()
        self.reload()
        self.changed.emit()
        QMessageBox.information(
            self, "已清空",
            f"已删除 {result.get('memory_cleared', 0)} 条记忆、"
            f"{result.get('history_cleared', 0)} 条翻译历史。\n"
            "术语、模板、统计与历史对局均已保留。",
        )
