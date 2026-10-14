"""词汇表页面：游戏术语与固定短语（翻译提示，永久保留）。

以 ``translation_terms`` 表为唯一数据源，分「外语→中文」与「中文→外语」两类。
术语只作为提示注入 prompt，不做输出层面的全局字符串替换，
避免出现「brush B 被 rush B 术语污染」这类错误。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..memory import DIRECTION_INCOMING, DIRECTION_REPLY
from .widgets import (
    Card,
    EmptyState,
    PageHeader,
    StatRow,
    ghost_button,
    hint_label,
    primary_button,
    section_title,
)

logger = logging.getLogger("cs2_translator.ui.page_glossary")

COLUMNS = ["术语", "译文", "方向", "目标语言", "分类", "确认"]


class GlossaryPage(QWidget):
    """词汇表页。"""

    changed = Signal()

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        self._rows: list[dict] = []
        # 搜尋輸入的延遲計時器（單次觸發）
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
            "词汇表",
            "游戏术语与固定短语。它们会作为翻译提示注入，帮助模型稳定输出正确的战术用语。",
        )
        self.add_button = primary_button("添加术语", "plus")
        self.add_button.clicked.connect(self._on_add)
        self.header.add_action(self.add_button)
        self.reload_button = ghost_button("刷新", "refresh")
        self.reload_button.clicked.connect(self.reload)
        self.header.add_action(self.reload_button)
        outer.addWidget(self.header)

        # ---- 统计 ----
        card = Card()
        self.stats = StatRow([
            ("术语总数", "total"),
            ("外→中", "incoming"),
            ("中→外", "reply"),
            ("已确认", "confirmed"),
        ])
        card.add(self.stats)
        outer.addWidget(card)

        # ---- 列表 ----
        table_card = Card()
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(section_title("术语列表"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索术语或译文…")
        self.search_edit.setMaximumWidth(220)
        self.search_edit.setClearButtonEnabled(True)
        # 輸入時延遲再查詢，避免每個按鍵都重建整張表
        self.search_edit.textChanged.connect(self._on_search_changed)
        head.addWidget(self.search_edit)

        self.direction_combo = QComboBox()
        self.direction_combo.addItem("全部方向", None)
        self.direction_combo.addItem("外→中", DIRECTION_INCOMING)
        self.direction_combo.addItem("中→外", DIRECTION_REPLY)
        self.direction_combo.currentIndexChanged.connect(self._on_direction_changed)
        head.addWidget(self.direction_combo)
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
        for i in (2, 3, 4, 5):
            header.setSectionResizeMode(i, QHeaderView.ResizeToContents)
        table_card.add(self.table)

        self.empty_state = EmptyState(
            "没有匹配的术语", "试试换个关键词，或点击「添加术语」新增一条。", "glossary"
        )
        table_card.add(self.empty_state)
        outer.addWidget(table_card, 1)
        outer.addWidget(hint_label(
            "双击编辑术语；右键可切换确认状态或删除。"
            "术语按词边界匹配（rush B 不会命中 brush B），含大写字母时区分大小写（HE 不等于 he）。"
        ))

    # ------------------------------------------------------------------
    def stop_timers(self) -> None:
        """停止延迟定时器（关闭窗口时由外壳调用，避免关闭后仍触发查询）。"""
        timer = getattr(self, "_search_timer", None)
        if timer is not None:
            timer.stop()

    def _on_search_changed(self, _text: str) -> None:
        """搜尋框輸入：延遲 180ms 再查詢，避免每個按鍵都重建表格。"""
        self._search_timer.start(180)

    def _on_direction_changed(self, _index: int) -> None:
        self._search_timer.stop()
        self.reload()

    def reload(self) -> None:
        """重建術語表格。

        效能關鍵：欄位用 ResizeToContents 自動貼合內容時，Qt 會在**每次**
        ``setItem`` 後重算欄寬，153 列 × 6 欄就退化成 O(n²)——
        實測同一份資料要 1300~3200 ms，切到詞彙表頁會明顯卡頓。
        解法：重建期間先把表格隱藏，讓 Qt 延後排版；填完資料再顯示，
        並只呼叫一次 resizeColumnsToContents()。
        實測同一迴圈由 1300~3200 ms 降到 8~14 ms（保留自動欄寬外觀）。
        """
        memory = self.context.memory
        if memory is None:
            return
        direction = self.direction_combo.currentData() if hasattr(self, "direction_combo") else None
        keyword = self.search_edit.text().strip() if hasattr(self, "search_edit") else ""
        try:
            self._rows = memory.list_terms(
                direction=direction,
                keyword=keyword or None,
                limit=2000,
            )
        except Exception:
            logger.exception("读取术语失败")
            self._rows = []

        self._fill_table(self._rows)

        has_rows = bool(self._rows)
        self.table.setVisible(has_rows)
        self.empty_state.setVisible(not has_rows)
        if has_rows:
            # 只调整 ResizeToContents 的栏（2~5），不要用 resizeColumnsToContents()：
            # 那个方法会连 Stretch 栏(0,1) 一起按内容宽度设置，
            # 结果 6 栏合计只有 ~480 px，右边留下 600+ px 空白（「排版很空」的真因）。
            for c in range(2, self.table.columnCount()):
                self.table.resizeColumnToContents(c)

        self._refresh_stats(memory)

    def _fill_table(self, rows: list) -> None:
        """把資料填進表格（重建期間隱藏表格以避開 O(n²) 欄寬重算）。"""
        table = self.table
        was_visible = table.isVisible()
        if was_visible:
            table.setVisible(False)
        try:
            table.setRowCount(len(rows))
            for r, entry in enumerate(rows):
                values = [
                    entry.get("source_term", ""),
                    entry.get("translated_term", ""),
                    "外→中" if entry.get("direction") == DIRECTION_INCOMING else "中→外",
                    entry.get("target_language", ""),
                    entry.get("category", "") or "-",
                    "是" if entry.get("confirmed") else "否",
                ]
                for c, text in enumerate(values):
                    item = QTableWidgetItem(str(text))
                    if c in (2, 3, 5):
                        item.setTextAlignment(Qt.AlignCenter)
                    if c == 0:
                        item.setData(Qt.UserRole, entry.get("id"))
                    table.setItem(r, c, item)
        finally:
            if was_visible:
                table.setVisible(True)

    def _refresh_stats(self, memory) -> None:
        """更新統計。用 SQL 聚合，避免把整表撈進 Python 再逐筆數。"""
        try:
            stats = memory.term_stats()
        except Exception:
            logger.exception("讀取術語統計失敗")
            try:
                all_terms = memory.list_terms(limit=100000)
            except Exception:
                all_terms = []
            stats = {
                "total": len(all_terms),
                "incoming": sum(1 for t in all_terms
                                if t.get("direction") == DIRECTION_INCOMING),
                "reply": sum(1 for t in all_terms
                             if t.get("direction") == DIRECTION_REPLY),
                "confirmed": sum(1 for t in all_terms if t.get("confirmed")),
            }
        self.stats.set_value("total", stats.get("total", 0))
        self.stats.set_value("incoming", stats.get("incoming", 0))
        self.stats.set_value("reply", stats.get("reply", 0))
        self.stats.set_value("confirmed", stats.get("confirmed", 0))

    # ------------------------------------------------------------------
    def _selected(self) -> dict | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self._rows):
            return None
        return self._rows[row]

    def _on_add(self) -> None:
        from .memory_dialog import TermEditDialog

        dialog = TermEditDialog(parent=self)
        if not dialog.exec():
            return
        memory = self.context.memory
        if memory is None:
            return
        memory.add_term(
            dialog.source_term,
            dialog.target_language,
            dialog.translated_term,
            direction=dialog.direction,
            category=dialog.category,
            confirmed=True,
        )
        self.reload()
        self.changed.emit()

    def _on_edit(self) -> None:
        entry = self._selected()
        if entry is None:
            return
        from .memory_dialog import CorrectionEditDialog

        dialog = CorrectionEditDialog(
            entry.get("source_term", ""), entry.get("translated_term", ""), parent=self
        )
        dialog.setWindowTitle("编辑术语译文")
        if not dialog.exec() or not dialog.new_text:
            return
        memory = self.context.memory
        if memory is not None and memory.update_term(
            int(entry.get("id")), translated_term=dialog.new_text
        ):
            self.reload()
            self.changed.emit()

    def _on_context_menu(self, position) -> None:
        entry = self._selected()
        if entry is None:
            return
        menu = QMenu(self)
        menu.addAction("编辑译文…", self._on_edit)
        label = "取消确认" if entry.get("confirmed") else "标记为确认"
        menu.addAction(label, lambda: self._toggle_confirm(entry))
        menu.addSeparator()
        menu.addAction("删除术语", lambda: self._delete(entry))
        menu.exec(self.table.viewport().mapToGlobal(position))

    def _toggle_confirm(self, entry: dict) -> None:
        memory = self.context.memory
        if memory is None:
            return
        memory.update_term(int(entry.get("id")), confirmed=not entry.get("confirmed"))
        self.reload()
        self.changed.emit()

    def _delete(self, entry: dict) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("删除术语")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"确定删除术语「{entry.get('source_term', '')}」吗？")
        box.setInformativeText("删除后该术语不再作为翻译提示注入。")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        if box.exec() != QMessageBox.Yes:
            return
        memory = self.context.memory
        if memory is not None and memory.delete_term(int(entry.get("id"))):
            self.reload()
            self.changed.emit()
