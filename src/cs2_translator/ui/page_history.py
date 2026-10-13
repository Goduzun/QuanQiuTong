"""历史对局页面：查看历史对局列表与其保留策略。

与长期翻译记忆完全隔离：本页的清理操作只删除「历史对局」数据，
绝不触碰 translation_memory / translation_terms / translation_templates。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .context import format_datetime
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

logger = logging.getLogger("cs2_translator.ui.page_history")

COLUMNS = ["对局 ID", "开始时间", "结束时间", "消息数", "状态", "来源"]


class HistoryPage(QWidget):
    """历史对局页。"""

    view_requested = Signal(str)   # match_id

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(12)

        self.header = PageHeader(
            "历史对局",
            "每次「开始新一局」都会把上一局归档到这里。历史对局的清理不影响长期翻译记忆。",
        )
        self.cleanup_button = danger_button("清理过期历史对局", "trash")
        self.cleanup_button.clicked.connect(self._on_cleanup)
        self.header.add_action(self.cleanup_button)
        self.reload_button = ghost_button("刷新", "refresh")
        self.reload_button.clicked.connect(self.reload)
        self.header.add_action(self.reload_button)
        outer.addWidget(self.header)

        # ---- 统计 + 保留策略 ----
        card = Card()
        row = QHBoxLayout()
        row.setSpacing(24)
        self.stats = StatRow([
            ("历史对局数", "ended"),
            ("全部对局数", "total"),
            ("保留上限", "limit"),
        ])
        row.addWidget(self.stats, 1)

        policy = QVBoxLayout()
        policy.setSpacing(4)
        policy.addWidget(section_title("保留策略"))
        limit_row = QHBoxLayout()
        limit_row.setSpacing(6)
        limit_row.addWidget(QLabel("保留最近"))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(1, 200)
        self.limit_spin.setSuffix(" 局")
        self.limit_spin.setValue(int(getattr(self.context.config, "history_match_limit", 10)))
        limit_row.addWidget(self.limit_spin)
        self.apply_limit_button = ghost_button("应用")
        self.apply_limit_button.clicked.connect(self._on_apply_limit)
        limit_row.addWidget(self.apply_limit_button)
        limit_row.addStretch(1)
        policy.addLayout(limit_row)
        policy.addWidget(hint_label("超出上限时按最旧优先删除历史对局", wrap=False))
        row.addLayout(policy)
        card.add_layout(row)
        outer.addWidget(card)

        # ---- 表格 ----
        table_card = Card()
        table_head = QHBoxLayout()
        table_head.addWidget(section_title("对局列表"))
        table_head.addStretch(1)
        table_card.add_layout(table_head)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self._on_double_click)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for i in (1, 2, 3, 4, 5):
            header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        table_card.add(self.table)

        self.empty_state = EmptyState(
            "还没有历史对局",
            "当你在实时翻译页点击「开始新一局」时，上一局会被归档到这里。",
            "history",
        )
        table_card.add(self.empty_state)
        outer.addWidget(table_card, 1)
        outer.addWidget(hint_label(
            "双击某一行可查看该局的完整消息（原文 + 译文）。"
            "「清理过期历史对局」只删除对局与局内消息，长期翻译记忆与词汇表完整保留。"
        ))

    # ------------------------------------------------------------------
    def reload(self) -> None:
        matches = self.context.matches
        if matches is None:
            return
        try:
            entries = matches.list_matches(limit=500)
        except Exception:
            logger.exception("读取历史对局失败")
            return

        self.table.setRowCount(len(entries))
        for r, info in enumerate(entries):
            values = [
                info.match_id,
                format_datetime(info.started_at),
                format_datetime(info.ended_at) if info.ended_at else "-",
                str(info.message_count),
                info.status_label,
                info.source_label,
            ]
            for c, text in enumerate(values):
                item = QTableWidgetItem(text)
                if c in (3,):
                    item.setTextAlignment(Qt.AlignCenter)
                if c == 0:
                    item.setData(Qt.UserRole, info.match_id)
                self.table.setItem(r, c, item)

        has_rows = bool(entries)
        self.table.setVisible(has_rows)
        self.empty_state.setVisible(not has_rows)
        self.history_rows = entries

        try:
            stats = matches.stats()
            self.stats.set_value("ended", stats.get("history_match_count", 0))
            self.stats.set_value("total", stats.get("total_match_count", 0))
            self.stats.set_value("limit", stats.get("history_limit", "-"))
        except Exception:
            logger.exception("读取对局统计失败")

    # ------------------------------------------------------------------
    def _on_apply_limit(self) -> None:
        value = int(self.limit_spin.value())
        self.context.config.history_match_limit = value
        try:
            self.context.config_manager.save(self.context.config)
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", f"无法保存配置：{exc}")
            return
        matches = self.context.matches
        if matches is not None:
            matches.history_limit = value
            removed = matches.cleanup_history(keep=value)
            self.reload()
            QMessageBox.information(
                self, "已应用",
                f"保留上限设为 {value} 局，本次清理了 {removed} 个过期对局。\n\n"
                "长期翻译记忆未受影响。",
            )

    def _on_cleanup(self) -> None:
        matches = self.context.matches
        if matches is None:
            return
        limit = int(getattr(self.context.config, "history_match_limit", 10))
        box = QMessageBox(self)
        box.setWindowTitle("清理过期历史对局")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"将只保留最近 {limit} 局历史对局，其余全部删除。")
        box.setInformativeText(
            "只删除「历史对局」与对应的局内消息。\n"
            "长期翻译记忆、游戏术语、句式模板均不会被删除。"
        )
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return
        removed = matches.cleanup_history(keep=limit)
        self.reload()
        QMessageBox.information(
            self, "清理完成",
            f"已删除 {removed} 个过期历史对局。\n长期翻译记忆保持不变。",
        )

    def _on_double_click(self, index) -> None:
        item = self.table.item(index.row(), 0)
        if item is None:
            return
        match_id = item.data(Qt.UserRole) or item.text()
        self.view_requested.emit(str(match_id))
