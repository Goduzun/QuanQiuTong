"""实时翻译主页面：对局信息 + 消息流（模型/代理/委托）+ 中文回复区。

相比旧版的关键改进：
- 用 ``QAbstractListModel`` + ``QListView`` + 自定义委托替代 ``QTableWidget``，
  新消息到达是 O(1) 追加，不再每次全表重扫（旧版每条消息都跑一次 O(n) 过滤）；
- 显示上限由配置驱动（默认 300），超出淘汰最旧项，界面不会无限增长；
- 只显示「当前对局」的消息；换局后列表立即清空，上一局的迟到结果被丢弃；
- 消息卡片：原文在上、译文在下，来源用徽章标记，支持复制与人工修正。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPlainTextEdit,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..language import LANGUAGE_NAMES
from . import icons
from .context import format_clock
from .design import DARK, RADIUS, TYPO, active_palette
from .widgets import (
    Badge,
    Card,
    EmptyState,
    PageHeader,
    StatRow,
    danger_button,
    ghost_button,
    hint_label,
    primary_button,
    section_title,
)

logger = logging.getLogger("cs2_translator.ui.page_live")

# 行内角色
ROLE_MESSAGE = Qt.UserRole + 1
ROLE_DATA = Qt.UserRole + 2

# 卡片视觉常量
CARD_PADDING_V = 7
CARD_PADDING_H = 12
LINE_SPACING = 4


def _normalize_for_match(text: str) -> str:
    """归一化文本，用于「自己发出的消息」与「刚复制的译文」比对。

    去掉空白、引号与常见标点，统一小写。这样即使游戏里粘贴时多了空格，
    或者在首尾带了引号，也仍然能认出来是同一条内容。
    """
    if not text:
        return ""
    drop = set(" \t\r\n\u00a0\"'“”‘’「」『』.,!?;:、。，！？；：…-—_()（）[]【】")
    return "".join(ch for ch in str(text).casefold() if ch not in drop)


class MessageStreamModel(QAbstractListModel):
    """消息流模型：持有当前对局的显示记录。

    ``newest_first=False``（默认）：新消息追加在**底部**，配合自动滚动到底。
    ``newest_first=True``：新消息插在**顶部**，永远第一眼就看到最新内容，
    不需要滚动也不会漏看。
    """

    def __init__(self, limit: int = 300, parent=None, newest_first: bool = False):
        super().__init__(parent)
        self.limit = max(50, int(limit))
        self._rows: list[dict] = []
        self.newest_first = bool(newest_first)

    # ---- Qt 接口 ----
    def rowCount(self, parent=QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        if role == ROLE_DATA:
            return row
        if role == Qt.DisplayRole:
            return row.get("source", "")
        if role == Qt.ToolTipRole:
            return row.get("raw") or row.get("source", "")
        return None

    # ---- 业务接口 ----
    def set_newest_first(self, value: bool) -> None:
        """切换排列顺序（保持现有内容不丢）。"""
        value = bool(value)
        if value == self.newest_first:
            return
        self.beginResetModel()
        self.newest_first = value
        self._rows.reverse()
        self.endResetModel()

    def append(self, record: dict) -> None:
        """新增一条消息记录（依排列顺序插在顶部或追加到底部）。"""
        pos = 0 if self.newest_first else len(self._rows)
        self.beginInsertRows(QModelIndex(), pos, pos)
        self._rows.insert(pos, record)
        self.endInsertRows()
        self._enforce_limit()

    def _enforce_limit(self) -> None:
        overflow = len(self._rows) - self.limit
        if overflow <= 0:
            return
        # 无论哪种排列顺序，被淘汰的都是「最旧」的那一端：
        # 新消息在底部时最旧的在开头；新消息在顶部时最旧的在结尾。
        if self.newest_first:
            first = len(self._rows) - overflow
            last = len(self._rows) - 1
        else:
            first, last = 0, overflow - 1
        self.beginRemoveRows(QModelIndex(), first, last)
        del self._rows[first:last + 1]
        self.endRemoveRows()

    def newest_index(self) -> int:
        """最新一条所在的行号（-1 表示空）。"""
        if not self._rows:
            return -1
        return 0 if self.newest_first else len(self._rows) - 1

    def clear(self) -> None:
        if not self._rows:
            return
        self.beginResetModel()
        self._rows.clear()
        self.endResetModel()

    def rows(self) -> list[dict]:
        return self._rows

    def find_by_message(self, message) -> int:
        """按对象身份查找行号（迟到结果需要回填到正确的行）。"""
        for i, row in enumerate(self._rows):
            if row.get("message") is message:
                return i
        return -1

    def update_row(self, row_index: int, translated: str, hit_kind: str, state: str) -> None:
        if not (0 <= row_index < len(self._rows)):
            return
        row = self._rows[row_index]
        row["translated"] = translated
        row["hit_kind"] = hit_kind
        row["state"] = state
        idx = self.index(row_index, 0)
        self.dataChanged.emit(idx, idx)

    def set_filter(self, keyword: str) -> None:
        self._filter = keyword


class MessageDelegate(QStyledItemDelegate):
    """消息卡片委托：原文 / 译文 / 元信息 / 来源徽章。

    行内按钮（复制 / 修正）的矩形以「行内相对坐标」记录在 ``_hit_rects``，
    由视图用 ``visualRect(index)`` 换算成视口坐标后做命中判定。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # 跟随当前主题（自定义委托无法读取 QSS，必须显式取调色板）
        self.palette = active_palette()
        # {(row, kind): QRect}  行内相对坐标
        self._hit_rects: dict[tuple[int, str], QRect] = {}

    def set_palette(self, palette) -> None:
        """主题切换后更新配色并重绘。"""
        self.palette = palette
        parent = self.parent()
        if parent is not None and hasattr(parent, "viewport"):
            parent.viewport().update()

    def hit_rect(self, row: int, kind: str) -> QRect | None:
        return self._hit_rects.get((row, kind))

    def hit_at(self, row: int, pos) -> str | None:
        """给定视口坐标与行号，返回命中的按钮类型（'copy' / 'edit'）。"""
        for kind in ("copy", "edit"):
            rect = self._hit_rects.get((row, kind))
            if rect is not None and rect.contains(pos):
                return kind
        return None

    def _badge_text(self, kind: str) -> tuple[str, str]:
        mapping = {
            "api": ("API", self.palette.accent_hover, self.palette.accent_soft),
            "exact": ("缓存", self.palette.success, self.palette.success_soft),
            "cache": ("缓存", self.palette.success, self.palette.success_soft),
            "term": ("术语", self.palette.success, self.palette.success_soft),
            "template": ("模板", self.palette.success, self.palette.success_soft),
            "correction": ("人工", self.palette.warning, self.palette.warning_soft),
            "manual": ("人工", self.palette.warning, self.palette.warning_soft),
            "skip": ("原文", self.palette.text_faint, self.palette.bg_active),
            "skipped": ("原文", self.palette.text_faint, self.palette.bg_active),
            "pending": ("翻译中", self.palette.text_muted, self.palette.bg_active),
            "error": ("失败", self.palette.danger, self.palette.danger_soft),
            "dropped": ("丢弃", self.palette.text_faint, self.palette.bg_active),
        }
        return mapping.get(str(kind or "api").lower(), mapping["api"])

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        row = index.data(ROLE_DATA) or {}
        width = max(240, option.rect.width() - 8)
        source_font = QFont(option.font)
        source_font.setPixelSize(TYPO.size_base)
        trans_font = QFont(option.font)
        trans_font.setPixelSize(TYPO.size_md)

        src_h = self._text_height(row.get("source", ""), source_font, width - CARD_PADDING_H * 2)
        trans = row.get("translated") or ""
        trans_h = self._text_height(trans, trans_font, width - CARD_PADDING_H * 2) if trans else 0
        meta_h = 15
        height = CARD_PADDING_V * 2 + meta_h + LINE_SPACING + src_h
        if trans_h:
            height += LINE_SPACING + trans_h
        return QSize(width, max(52, height))

    @staticmethod
    def _text_height(text: str, font: QFont, width: int) -> int:
        if not text:
            return 14
        from PySide6.QtGui import QFontMetrics

        fm = QFontMetrics(font)
        rect = fm.boundingRect(QRect(0, 0, max(40, width), 10000),
                               int(Qt.TextWordWrap) | int(Qt.AlignLeft), text)
        return max(fm.height(), rect.height())

    def paint(self, painter: QPainter, option, index) -> None:
        row = index.data(ROLE_DATA) or {}
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = option.rect.adjusted(4, 2, -4, -2)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)

        # 卡片底
        bg = QColor(self.palette.bg_active if selected else
                    (self.palette.bg_hover if hovered else self.palette.bg_raised))
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, RADIUS.md, RADIUS.md)

        # 左侧状态竖条（按状态着色）
        state = row.get("state", "pending")
        bar_color = {
            "pending": self.palette.text_faint,
            "done": self.palette.success,
            "error": self.palette.danger,
            "skipped": self.palette.text_faint,
            "dropped": self.palette.text_faint,
        }.get(state, self.palette.text_faint)
        painter.setBrush(QColor(bar_color))
        painter.drawRoundedRect(QRect(rect.left(), rect.top() + 4, 3, rect.height() - 8), 1.5, 1.5)

        x = rect.left() + CARD_PADDING_H
        inner_w = rect.width() - CARD_PADDING_H * 2
        y = rect.top() + CARD_PADDING_V

        # ---- 元信息行：时间 · 发送者 · 频道 · 徽章 ----
        meta_font = QFont(option.font)
        meta_font.setPixelSize(TYPO.size_xs)
        painter.setFont(meta_font)
        meta_text = f"{format_clock(row.get('timestamp'))}   {row.get('sender', '')}"
        channel = row.get("channel", "")
        if channel:
            meta_text += f"   [{channel}]"
        painter.setPen(QColor(self.palette.msg_meta))
        meta_rect = QRect(x, y, inner_w, 15)
        painter.drawText(meta_rect, int(Qt.AlignLeft | Qt.AlignVCenter), meta_text)

        # 右侧：语言方向 + 徽章 + 操作按钮
        badge_text, badge_fg, badge_bg = self._badge_text(row.get("hit_kind") or state)
        lang = row.get("source_language") or ""

        rx = rect.right() - CARD_PADDING_H
        row_no = index.row()
        self._hit_rects.pop((row_no, "copy"), None)
        self._hit_rects.pop((row_no, "edit"), None)
        if row.get("translated"):
            btn = QRect(rx - 24, y - 1, 22, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self.palette.bg_active))
            painter.drawRoundedRect(btn, RADIUS.sm, RADIUS.sm)
            painter.setPen(QColor(self.palette.text_muted))
            painter.setFont(meta_font)
            painter.drawText(btn, int(Qt.AlignCenter), "复制")
            self._hit_rects[(row_no, "copy")] = btn
            rx -= 28

        if row.get("state") in ("done", "error", "skipped", "dropped"):
            btn2 = QRect(rx - 24, y - 1, 22, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self.palette.bg_active))
            painter.drawRoundedRect(btn2, RADIUS.sm, RADIUS.sm)
            painter.setPen(QColor(self.palette.text_muted))
            painter.drawText(btn2, int(Qt.AlignCenter), "改")
            self._hit_rects[(row_no, "edit")] = btn2
            rx -= 28

        # 来源徽章（唯一来源标识，不再重复显示语言代码，避免 "API API" 这类重复）
        if row.get("state") != "pending":
            fm_badge = painter.fontMetrics()
            bw = fm_badge.horizontalAdvance(badge_text) + 14
            badge_rect = QRect(rx - bw, y - 1, bw, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(badge_bg))
            painter.drawRoundedRect(badge_rect, RADIUS.sm, RADIUS.sm)
            painter.setPen(QColor(badge_fg))
            painter.drawText(badge_rect, int(Qt.AlignCenter), badge_text)
            rx -= bw + 6

        # 语言方向（仅在需要区分外语时显示，且不含重复的"API"字样）
        if lang and state not in ("skipped",):
            painter.setPen(QColor(self.palette.msg_meta))
            painter.setFont(meta_font)
            lang_text = LANGUAGE_NAMES.get(lang, lang)
            lang_w = painter.fontMetrics().horizontalAdvance(lang_text) + 4
            painter.drawText(QRect(rx - lang_w, y, lang_w, 15),
                             int(Qt.AlignRight | Qt.AlignVCenter), lang_text)

        # ---- 原文 ----
        src_font = QFont(option.font)
        src_font.setPixelSize(TYPO.size_base)
        painter.setFont(src_font)
        y += 15 + LINE_SPACING
        src_h = self._text_height(row.get("source", ""), src_font, inner_w)
        painter.setPen(QColor(self.palette.msg_source))
        painter.drawText(QRect(x, y, inner_w, src_h),
                         int(Qt.TextWordWrap) | int(Qt.AlignLeft | Qt.AlignTop),
                         row.get("source", ""))

        # ---- 译文 ----
        translated = row.get("translated")
        if translated:
            y += src_h + LINE_SPACING
            trans_font = QFont(option.font)
            trans_font.setPixelSize(TYPO.size_md)
            painter.setFont(trans_font)
            if state == "error":
                painter.setPen(QColor(self.palette.danger))
            elif state in ("skipped", "pending"):
                painter.setPen(QColor(self.palette.text_faint))
            else:
                painter.setPen(QColor(self.palette.msg_translated))
            trans_h = self._text_height(translated, trans_font, inner_w)
            painter.drawText(QRect(x, y, inner_w, trans_h),
                             int(Qt.TextWordWrap) | int(Qt.AlignLeft | Qt.AlignTop),
                             translated)

        painter.restore()


class LivePage(QWidget):
    """实时翻译页。"""

    # 请求人工修正某条译文：(原文, 目标语言, 方向, 现有译文)
    correction_requested = Signal(str, str, str, str)
    copy_requested = Signal(str)
    reply_requested = Signal(str, str)          # (中文, 目标语言)
    new_match_requested = Signal()
    clear_view_requested = Signal()
    pause_toggled = Signal(bool)

    def __init__(self, context, parent=None):
        super().__init__(parent)
        self.context = context
        # 使用者是否主动离开了「最新」位置（往上翻看旧消息）。
        # 用旗标追踪而不是每次由 value/maximum 现算：消息刚插入时滚动条
        # 范围尚未更新，现算会把「其实还在底部」误判为「已离开」。
        self._user_scrolled_away = False
        # CS2 在前台时暂停非必要 UI 更新：消息仍照常入队（数据不丢），
        # 回到桌面后一次性补齐渲染，避免在游戏全屏时持续重绘。
        self._updates_suspended = False
        self._pending_flush = False
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(12)

        # ---- 页头 ----
        self.header = PageHeader("实时翻译", "只读 CS2 控制台日志，实时把外语聊天翻译成中文")
        self.new_match_button = primary_button("开始新一局", "plus")
        self.new_match_button.clicked.connect(self.new_match_requested.emit)
        self.header.add_action(self.new_match_button)
        self.clear_view_button = ghost_button("清空本局显示", "trash")
        self.clear_view_button.clicked.connect(self.clear_view_requested.emit)
        self.header.add_action(self.clear_view_button)
        self.pause_button = ghost_button("暂停翻译", "pause")
        self.pause_button.setCheckable(True)
        self.pause_button.toggled.connect(self._on_pause_toggled)
        self.header.add_action(self.pause_button)
        outer.addWidget(self.header)

        # ---- 对局信息条 ----
        self.match_card = Card()
        match_row = QHBoxLayout()
        match_row.setSpacing(20)
        self.match_stats = StatRow([
            ("对局 ID", "match_id"),
            ("开始时间", "started"),
            ("本局消息", "messages"),
            ("待翻译", "pending"),
        ])
        match_row.addWidget(self.match_stats, 1)
        # 对局识别模式单独用一行小字说明（不占统计项宽度，避免大片空白）
        self.mode_label = hint_label("", wrap=False)
        match_row.addWidget(self.mode_label)
        self.match_card.add_layout(match_row)
        outer.addWidget(self.match_card)

        # ---- 消息流 ----
        stream_card = Card()
        stream_head = QHBoxLayout()
        stream_head.setSpacing(8)
        stream_head.addWidget(section_title("消息流"))
        self.stream_hint = hint_label("", wrap=False)
        stream_head.addWidget(self.stream_hint)
        stream_head.addStretch(1)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索当前对局消息…")
        self.search_edit.setMaximumWidth(230)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_filter_changed)
        stream_head.addWidget(self.search_edit)

        self.new_message_button = QPushButton("↓ 回到最新")
        self.new_message_button.setObjectName("primary")
        self.new_message_button.setCursor(Qt.PointingHandCursor)
        self.new_message_button.setVisible(False)
        self.new_message_button.clicked.connect(self.scroll_to_newest)
        stream_head.addWidget(self.new_message_button)
        stream_card.add_layout(stream_head)

        self.model = MessageStreamModel(
            limit=self._configured_limit(),
            newest_first=bool(getattr(self.context.config, "newest_first", False)),
        )
        self.list_view = QListView()
        self.list_view.setModel(self.model)
        self.delegate = MessageDelegate(self.list_view)
        self.list_view.setItemDelegate(self.delegate)
        self.list_view.setSelectionMode(QListView.SingleSelection)
        self.list_view.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_view.setMouseTracking(True)
        self.list_view.setSpacing(0)
        self.list_view.setUniformItemSizes(False)
        self.list_view.clicked.connect(self._on_row_clicked)
        self.list_view.verticalScrollBar().valueChanged.connect(self._on_scroll)
        stream_card.add(self.list_view)

        self.empty_state = EmptyState(
            "等待聊天消息",
            "正在监听 CS2 日志。请确认已在 Steam 为 CS2 添加启动参数 -condebug，"
            "并在游戏内产生聊天。",
            "chat",
        )
        stream_card.add(self.empty_state)
        self.empty_state.setVisible(True)
        self.list_view.setVisible(False)
        outer.addWidget(stream_card, 1)

        # ---- 中文回复区 ----
        outer.addWidget(self._build_reply_bar())

    def _build_reply_bar(self) -> QWidget:
        card = Card()
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(section_title("回复翻译"))
        self.reply_status = hint_label("输入中文，翻译成目标语言后复制到游戏", wrap=False)
        head.addWidget(self.reply_status)
        head.addStretch(1)

        head.addWidget(QLabel("目标语言"))
        self.lang_combo = QComboBox()
        self.lang_combo.setEditable(True)
        self.lang_combo.setInsertPolicy(QComboBox.NoInsert)
        self.lang_combo.setMinimumWidth(160)
        for code in ("en", "ru", "ja", "ko", "de", "fr", "es", "pt", "it", "tr",
                     "uk", "pl", "vi", "th", "ar", "hi", "id", "nl", "sv"):
            self.lang_combo.addItem(f"{LANGUAGE_NAMES.get(code, code)} ({code})", code)
        head.addWidget(self.lang_combo)

        self.reply_auto_check = QPushButton("自动建议")
        self.reply_auto_check.setCheckable(True)
        self.reply_auto_check.setChecked(True)
        self.reply_auto_check.setToolTip("根据最近一条消息自动建议目标语言")
        head.addWidget(self.reply_auto_check)
        card.add_layout(head)

        body = QHBoxLayout()
        body.setSpacing(10)
        self.reply_input = QPlainTextEdit()
        self.reply_input.setPlaceholderText("输入中文回复，例如：冲B点，跟我走！（Ctrl+Enter 翻译）")
        self.reply_input.setFixedHeight(72)
        body.addWidget(self.reply_input, 1)

        buttons = QVBoxLayout()
        buttons.setSpacing(6)
        self.reply_translate_button = primary_button("翻译并复制", "send")
        self.reply_translate_button.setToolTip(
            "翻译成功后会自动复制到剪贴板，你直接进游戏 Ctrl+V 粘贴即可。\n"
            "本程序不会自动发送、也不会模拟按键。"
        )
        self.reply_translate_button.clicked.connect(self._on_reply_translate)
        buttons.addWidget(self.reply_translate_button)
        buttons.addStretch(1)
        body.addLayout(buttons)
        card.add_layout(body)

        self.reply_result_label = QLabel("")
        self.reply_result_label.setObjectName("msgTranslated")
        self.reply_result_label.setWordWrap(True)
        self.reply_result_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.reply_result_label.setVisible(False)
        card.add(self.reply_result_label)

        # ---- 状态提示（翻译成功 = 已自动复制，去游戏粘贴即可）----
        # 去掉了「复制译文」按钮（翻译成功即已复制）与「我已发送」确认框
        # （程序本就不负责发送，无需使用者回报）。
        self.state_label = QLabel("等待翻译")
        self.state_label.setObjectName("statePending")
        self.state_label.setWordWrap(True)
        card.add(self.state_label)
        self._reply_state = "pending"
        self._last_reply_translation = ""

        # Ctrl+Enter 快捷键（旧版在占位文本里承诺过却从未实现）
        from PySide6.QtGui import QKeySequence, QShortcut

        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.reply_input)
        shortcut.activated.connect(self._on_reply_translate)
        shortcut2 = QShortcut(QKeySequence("Ctrl+Enter"), self.reply_input)
        shortcut2.activated.connect(self._on_reply_translate)
        self._reply_shortcut = shortcut
        self._reply_shortcut2 = shortcut2
        self._last_reply_translation = ""
        return card

    # ------------------------------------------------------------------
    # 配置
    # ------------------------------------------------------------------
    def _configured_limit(self) -> int:
        return int(getattr(self.context.config, "match_message_limit", 300) or 300)

    def apply_config(self) -> None:
        """配置变更后同步界面（例如消息上限、目标语言）。"""
        self.model.limit = max(50, self._configured_limit())
        self.model._enforce_limit()
        target = getattr(self.context.config, "reply_target_language", "en") or "en"
        self.set_target_language(target)
        self.reply_auto_check.setChecked(
            bool(getattr(self.context.config, "auto_suggest_reply_language", True))
        )

    # ------------------------------------------------------------------
    # 对局信息
    # ------------------------------------------------------------------
    def refresh_match_info(self) -> None:
        matches = self.context.matches
        if matches is None:
            return
        info = matches.current
        if info is None:
            return
        self.match_stats.set_value("match_id", info.match_id)
        self.match_stats.set_value("started", format_clock(info.started_at))
        self.match_stats.set_value("messages", matches.message_count)
        mode = info.source_label if info.source_label else "手动"
        if getattr(self.context.config, "auto_match_detection", False):
            mode += "（自动识别，信号未验证）"
        else:
            mode += "（自动识别关闭）"
        self.mode_label.setText(f"识别模式：{mode}")
        self.refresh_pending()

    def refresh_pending(self) -> None:
        """待翻译任务数来自真实队列长度。"""
        try:
            status = self.context.controller.engine.status()
            queue = status.get("queue", {}) or {}
            self.match_stats.set_value("pending", queue.get("queue_length", 0))
        except Exception:
            self.match_stats.set_value("pending", "-")

    def update_stream_hint(self) -> None:
        count = len(self.model.rows())
        self.stream_hint.setText(f"显示 {count} / 上限 {self.model.limit} 条 · 仅当前对局")

    # ------------------------------------------------------------------
    # 消息流
    # ------------------------------------------------------------------
    def set_live_updates_suspended(self, suspended: bool) -> None:
        """暂停/恢复实时页的非必要 UI 更新。

        CS2 在前台（尤其全屏）时暂停：外部窗口持续重绘会打断游戏的独占全屏
        swapchain，表现为 CS2 周期性「无响应」。暂停期间消息仍然进入 model
        （数据完整、不漏不重），只是不做滚动、不刷计数、不重排布局。
        """
        suspended = bool(suspended)
        if suspended == self._updates_suspended:
            return
        self._updates_suspended = suspended
        if suspended:
            self._pending_flush = True

    @property
    def updates_suspended(self) -> bool:
        return self._updates_suspended

    def flush_suspended_updates(self) -> None:
        """恢复渲染：一次性补齐暂停期间累积的变化。"""
        if not self._pending_flush:
            return
        self._pending_flush = False
        self.update_stream_hint()
        self.refresh_match_info()
        if self.model.rowCount() > 0:
            self.empty_state.setVisible(False)
            self.list_view.setVisible(True)
        self._maybe_autoscroll()

    def add_message(self, record: dict) -> None:
        """追加一条待翻译消息（原文立即可见，译文稍后回填）。"""
        self.model.append(record)
        if self._updates_suspended:
            # 只入队，不渲染；等回到桌面一次补齐
            self._pending_flush = True
            return
        self.empty_state.setVisible(False)
        self.list_view.setVisible(True)
        self.update_stream_hint()
        self._maybe_autoscroll()

    def update_translation(self, message, translated: str, hit_kind: str, state: str) -> None:
        row_index = self.model.find_by_message(message)
        if row_index < 0:
            return
        self.model.update_row(row_index, translated, hit_kind, state)
        if self._updates_suspended:
            self._pending_flush = True
            return
        self.update_stream_hint()

    def apply_display_order(self, newest_first: bool) -> None:
        """切换「最新在顶部 / 底部」，保留现有消息内容。"""
        newest_first = bool(newest_first)
        if bool(getattr(self.model, "newest_first", False)) == newest_first:
            return
        self.model.set_newest_first(newest_first)
        self._user_scrolled_away = False
        self._on_filter_changed(self.search_edit.text() if hasattr(self, "search_edit") else "")
        self.update_stream_hint()
        self.scroll_to_newest()

    def clear_stream(self) -> None:
        self.model.clear()
        self._user_scrolled_away = False
        self.empty_state.setVisible(True)
        self.list_view.setVisible(False)
        self.update_stream_hint()

    def _maybe_autoscroll(self) -> None:
        """新消息到达时把视图带到最新一条。

        两种排列顺序：
        - 最新在底部：滚动到底，除非使用者正在往上翻（此时显示「回到最新」按钮）。
        - 最新在顶部：新消息插在 row 0，本来就在视野内，只需确保不在滚动中途
          被推走，因此滚到顶部。

        判定「使用者在不在底部」用 _user_scrolled_away 旗标追踪，
        而不是每次用 value/maximum 現算 —— 因为消息刚插入时滚动条范围还
        没更新，現算会把「其实在底部」误判成「已离开底部」，导致自动滚动
        从此失效（真实使用者感受就是「必须手动往下滑」）。
        """
        newest_first = bool(getattr(self.model, "newest_first", False))
        if not bool(getattr(self.context.config, "auto_scroll", True)):
            return
        if newest_first:
            # 最新在最上方，永远可见；顺手滚到顶避免被旧内容推走
            if not self._user_scrolled_away:
                self.list_view.scrollToTop()
            self.new_message_button.setVisible(False)
            return
        if not self._user_scrolled_away:
            self.scroll_to_bottom()
        else:
            self.new_message_button.setVisible(True)

    def scroll_to_bottom(self) -> None:
        self.list_view.scrollToBottom()
        self._user_scrolled_away = False
        self.new_message_button.setVisible(False)

    def scroll_to_newest(self) -> None:
        """滚到最新一条（依排列顺序决定是顶部还是底部）。"""
        if bool(getattr(self.model, "newest_first", False)):
            self.list_view.scrollToTop()
            self._user_scrolled_away = False
            self.new_message_button.setVisible(False)
        else:
            self.scroll_to_bottom()

    def _on_scroll(self, value: int) -> None:
        """记录使用者是否主动离开了最新位置。"""
        bar = self.list_view.verticalScrollBar()
        if bool(getattr(self.model, "newest_first", False)):
            away = value > 4
        else:
            away = not (bar.maximum() == 0 or value >= bar.maximum() - 4)
        self._user_scrolled_away = away
        if not away:
            self.new_message_button.setVisible(False)

    def _on_filter_changed(self, keyword: str) -> None:
        """过滤当前对局消息（按行隐藏，不重建列表）。"""
        keyword = (keyword or "").strip().lower()
        for i, row in enumerate(self.model.rows()):
            haystack = " ".join([
                str(row.get("sender", "")),
                str(row.get("source", "")),
                str(row.get("translated", "") or ""),
                str(row.get("channel", "")),
            ]).lower()
            self.list_view.setRowHidden(i, bool(keyword) and keyword not in haystack)

    def _on_row_clicked(self, index: QModelIndex) -> None:
        if not index.isValid():
            return
        row_data = index.data(ROLE_DATA) or {}
        # 用真实点击坐标判断是否命中行内按钮
        from PySide6.QtGui import QCursor

        item_rect = self.list_view.visualRect(index)
        local = self.list_view.viewport().mapFromGlobal(QCursor.pos())
        rel = local - item_rect.topLeft()
        hit = self.delegate.hit_at(index.row(), rel)

        if hit == "copy" and row_data.get("translated"):
            self.copy_requested.emit(row_data.get("translated", ""))
        elif hit == "edit":
            self.correction_requested.emit(
                row_data.get("source", ""),
                "zh",
                "incoming",
                row_data.get("translated", "") or "",
            )

    # ------------------------------------------------------------------
    # 回复
    # ------------------------------------------------------------------
    def current_target_language(self) -> str:
        data = self.lang_combo.currentData()
        if data:
            return str(data)
        text = self.lang_combo.currentText().strip()
        if "(" in text and text.endswith(")"):
            return text[text.rfind("(") + 1:-1].strip() or "en"
        return text or "en"

    def set_target_language(self, language: str) -> None:
        for i in range(self.lang_combo.count()):
            if self.lang_combo.itemData(i) == language:
                self.lang_combo.setCurrentIndex(i)
                return

    def apply_suggested_language(self, language: str | None) -> None:
        if not language or not self.reply_auto_check.isChecked():
            return
        self.set_target_language(language)

    def _on_reply_translate(self) -> None:
        text = self.reply_input.toPlainText().strip()
        if not text:
            self._reply_state = "empty"
            self._refresh_state_label()
            return
        self.reply_translate_button.setEnabled(False)
        self.reply_result_label.setVisible(False)
        self._reply_state = "translating"
        self._refresh_state_label()
        self.reply_requested.emit(text, self.current_target_language())

    def show_reply_result(self, result: dict) -> None:
        """显示回复翻译结果，成功时**自动复制**到剪贴板。

        设计逻辑：翻译成功 → 自动复制 → 使用者直接进游戏 Ctrl+V 粘贴。
        因此不再需要「复制译文」按钮，也不再有「我已发送」确认框
        （程序本就不负责发送，无需使用者回报状态）。

        失败时保留输入草稿，让使用者可以直接重试。
        """
        self.reply_translate_button.setEnabled(True)
        if result.get("ok"):
            translated = result.get("translated", "")
            self._last_reply_translation = translated
            self.reply_result_label.setText(translated)
            self.reply_result_label.setVisible(True)
            if translated:
                # 自动复制：这是「翻译并复制」的核心行为
                self.copy_requested.emit(translated)
                self._reply_state = "copied"
            else:
                self._reply_state = "empty"
            hit = result.get("hit_kind") or "api"
            latency = result.get("latency_ms") or 0
            source = {"correction": "人工修正", "exact": "缓存", "term": "术语",
                      "template": "模板"}.get(hit, "在线 API")
            self.reply_status.setText(f"{source} · {latency:.0f} ms")
        else:
            self.reply_result_label.setVisible(False)
            self._reply_state = "failed"
            self.reply_status.setText(result.get("error", "未知错误"))
        self._refresh_state_label()

    # ------------------------------------------------------------------
    # 识别「自己发出的消息」→ 清空回复输入框
    # ------------------------------------------------------------------
    def note_incoming_for_own_reply(self, sender: str, content: str) -> bool:
        """若这条消息是「本人刚发出的」，就清空回复输入框。

        真实动线：翻译 → 自动复制 → 进游戏 Ctrl+V 发出 → 日志里出现自己这条
        → 此时输入框已经没用了，清空它，下一句可以直接输入。

        返回是否清空了输入框（方便调用方打日志/测试断言）。

        两种识别方式，任一命中即算自己发的：
          1. 发送者等于设置里的「我的游戏昵称」(config.own_player_name)
          2. 没有配置昵称时，把消息内容和「刚翻译出来的那段译文」比对
             （归一化后相等/互相包含即视为命中）
        第 2 种是兜底：使用者往往懒得填昵称，而刚复制出去的译文是已知的。

        **前置条件**：必须存在「已翻译并复制待发出」的译文。
        否则使用者刚打了草稿还没翻译，自己在游戏里随手说句话就会把草稿清掉 ——
        那是丢数据，不是便利。使用者的原话也是「翻译之后复制粘贴之后」再清空。
        """
        if not (self._last_reply_translation or "").strip():
            return False
        if not self._looks_like_own_message(sender, content):
            return False

        had_text = bool(self.reply_input.toPlainText().strip())
        self.reply_input.clear()
        self.reply_result_label.setVisible(False)
        self._last_reply_translation = ""
        self._reply_state = "sent"
        self._refresh_state_label()
        return had_text or True

    def _looks_like_own_message(self, sender: str, content: str) -> bool:
        """判断一条聊天消息是否出自本人。"""
        own = str(getattr(self.context.config, "own_player_name", "") or "").strip()
        if own and str(sender or "").strip().casefold() == own.casefold():
            return True

        # 兜底：与刚发出的译文比对
        last = str(self._last_reply_translation or "").strip()
        if not last:
            return False
        a = _normalize_for_match(content)
        b = _normalize_for_match(last)
        if not a or not b:
            return False
        return b == a or b in a or a in b

    # ------------------------------------------------------------------
    # 回复状态提示
    # ------------------------------------------------------------------
    def mark_copied(self) -> None:
        """标记译文已复制（翻译成功时自动调用）。"""
        if not self._last_reply_translation:
            return
        self._reply_state = "copied"
        self._refresh_state_label()

    def _refresh_state_label(self) -> None:
        """刷新状态提示。

        每种状态由「圆点 + 主文字 + 副说明」组成，用 HTML 富文本在一个
        QLabel 里排版，避免多个控件挤在一行（旧版三个控件并排很难看）。
        """
        p = active_palette()
        dot = {
            "copied": p.success,
            "sent": p.info,
            "translating": p.warning,
            "failed": p.danger,
            "empty": p.text_faint,
        }.get(self._reply_state, p.text_faint)

        if self._reply_state == "copied":
            main = "译文已复制到剪贴板"
            sub = "切回游戏按 Ctrl+V 粘贴即可发送（本程序不会自动发送）"
            main_color = p.success
        elif self._reply_state == "sent":
            main = "已识别到你发出这条消息，输入框已清空"
            sub = "可以直接输入下一句"
            main_color = p.info
        elif self._reply_state == "translating":
            main = "正在翻译…"
            sub = ""
            main_color = p.warning
        elif self._reply_state == "failed":
            main = "翻译失败，草稿已保留"
            sub = "可直接修改后重试"
            main_color = p.danger
        elif self._reply_state == "empty":
            main = "请先输入要翻译的中文"
            sub = ""
            main_color = p.text_faint
        else:
            main = "等待翻译"
            sub = "输入中文后按 Ctrl+Enter 或点「翻译并复制」"
            main_color = p.text_muted

        html = (
            f'<span style="color:{dot};">●</span>&nbsp;'
            f'<span style="color:{main_color};font-weight:600;">{main}</span>'
        )
        if sub:
            html += f'&nbsp;&nbsp;<span style="color:{p.text_faint};font-size:12px;">{sub}</span>'
        self.state_label.setText(html)

    def _on_pause_toggled(self, paused: bool) -> None:
        self.pause_button.setText("继续翻译" if paused else "暂停翻译")
        self.pause_button.setIcon(
            icons.make_icon("play" if paused else "pause", DARK.text_muted, 15)
        )
        self.pause_toggled.emit(paused)

    # ---- 供外壳转发状态 ----
    def set_listening_state(self, state: str, text: str) -> None:
        self.header.set_subtitle(text)
