"""可复用界面组件：卡片、状态点、徽章、统计项、页面标题、空状态等。

所有组件只通过 ``objectName`` 引用设计系统里的样式，不硬编码颜色，
因此切换主题（深色/浅色）时自动跟随。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import icons
from .design import DARK, palette_for

# 状态 -> objectName 映射（与 design.py 的 QSS 对应）
_STATUS_OBJECT = {
    "ok": "statusOk",
    "warn": "statusWarn",
    "error": "statusError",
    "idle": "statusIdle",
}
_DOT_OBJECT = {
    "ok": "dotOk",
    "warn": "dotWarn",
    "error": "dotError",
    "idle": "dotIdle",
}
# 状态 -> 指示点字符
_DOT_CHAR = {"ok": "●", "warn": "●", "error": "●", "idle": "○"}


class Card(QFrame):
    """带边框圆角的内容分组卡片。"""

    def __init__(self, parent=None, *, flat: bool = False):
        super().__init__(parent)
        self.setObjectName("cardFlat" if flat else "card")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(14, 12, 14, 12)
        self._layout.setSpacing(10)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget) -> None:
        self._layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self._layout.addLayout(layout)


class StatusDot(QLabel):
    """状态指示点 + 文本。

    状态必须绑定真实数据来源，调用方负责传入真实状态（``ok``/``warn``/``error``/``idle``）。
    """

    def __init__(self, text: str = "", state: str = "idle", parent=None):
        super().__init__(parent)
        self._text = text
        self.setTextFormat(Qt.PlainText)
        self.set_state(state, text)

    def set_state(self, state: str, text: str | None = None) -> None:
        if text is not None:
            self._text = text
        state = state if state in _STATUS_OBJECT else "idle"
        self.setObjectName(_DOT_OBJECT[state])
        self.setText(f"{_DOT_CHAR[state]} {self._text}")
        # 重新应用样式（objectName 变化后需要 polish）
        self.style().unpolish(self)
        self.style().polish(self)


class Badge(QLabel):
    """译文来源徽章：在线 API / 缓存 / 人工修正 / 保留原文 / 待翻译。"""

    _KINDS = {
        "api": ("在线 API", "badgeApi"),
        "cache": ("缓存", "badgeCache"),
        "exact": ("缓存", "badgeCache"),
        "term": ("术语", "badgeCache"),
        "template": ("模板", "badgeCache"),
        "correction": ("人工修正", "badgeManual"),
        "manual": ("人工修正", "badgeManual"),
        "skip": ("保留原文", "badgeSkip"),
        "skipped": ("保留原文", "badgeSkip"),
        "pending": ("翻译中", "badgePending"),
        "error": ("失败", "badgeManual"),
        "dropped": ("已丢弃", "badgeSkip"),
    }

    def __init__(self, kind: str = "pending", parent=None):
        super().__init__(parent)
        self.setTextFormat(Qt.PlainText)
        self.set_kind(kind)

    def set_kind(self, kind: str) -> None:
        label, obj = self._KINDS.get(str(kind or "pending").lower(), self._KINDS["api"])
        self.setObjectName(obj)
        self.setText(label)
        self.style().unpolish(self)
        self.style().polish(self)


class StatItem(QWidget):
    """单个统计项：大号数值 + 说明文字。

    数值必须来自真实计数器，绝不写死。
    """

    def __init__(self, label: str, value: str = "-", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("statValue")
        font = self.value_label.font()
        font.setPointSize(max(12, font.pointSize() + 4))
        font.setBold(True)
        self.value_label.setFont(font)

        self.text_label = QLabel(label)
        self.text_label.setObjectName("faintLabel")
        self.text_label.setWordWrap(True)

        layout.addWidget(self.value_label)
        layout.addWidget(self.text_label)

    def set_value(self, value) -> None:
        self.value_label.setText(str(value))


class StatRow(QWidget):
    """一行统计项，自动均分宽度。"""

    def __init__(self, items: list[tuple[str, str]] | None = None, parent=None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(18)
        self.items: dict[str, StatItem] = {}
        for label, key in items or []:
            item = StatItem(label, "-")
            self.items[key] = item
            self._layout.addWidget(item)
        self._layout.addStretch(1)

    def set_value(self, key: str, value) -> None:
        item = self.items.get(key)
        if item is not None:
            item.set_value(value)


class PageHeader(QWidget):
    """页面标题 + 副标题 + 右侧操作区。"""

    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("pageTitle")
        row.addWidget(self.title_label)
        row.addStretch(1)

        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        row.addLayout(self.actions)
        outer.addLayout(row)

        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("hintLabel")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setVisible(bool(subtitle))
        outer.addWidget(self.subtitle_label)

    def set_subtitle(self, text: str) -> None:
        self.subtitle_label.setText(text)
        self.subtitle_label.setVisible(bool(text))

    def add_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)


class EmptyState(QWidget):
    """空状态：图标 + 标题 + 说明。"""

    def __init__(self, title: str, description: str = "", icon_name: str = "chat", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 36, 24, 36)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignCenter)

        self.icon_label = QLabel()
        self.icon_label.setPixmap(icons.make_pixmap(icon_name, DARK.text_faint, 40))
        self.icon_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.icon_label)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("sectionTitle")
        self.title_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title_label)

        self.desc_label = QLabel(description)
        self.desc_label.setObjectName("hintLabel")
        self.desc_label.setAlignment(Qt.AlignCenter)
        self.desc_label.setWordWrap(True)
        layout.addWidget(self.desc_label)


def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionTitle")
    return label


def hint_label(text: str, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hintLabel")
    label.setWordWrap(wrap)
    return label


def faint_label(text: str, wrap: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName("faintLabel")
    label.setWordWrap(wrap)
    return label


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("dividerLine")
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    return line


def icon_button(icon_name: str, tooltip: str, parent=None) -> QPushButton:
    """小型线性图标按钮（复制 / 编辑 / 删除等）。"""
    btn = QPushButton(parent)
    btn.setObjectName("iconBtn")
    btn.setIcon(icons.make_icon(icon_name, DARK.text_faint, 15))
    btn.setToolTip(tooltip)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setFixedSize(26, 24)
    return btn


def primary_button(text: str, icon_name: str | None = None, parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("primary")
    btn.setCursor(Qt.PointingHandCursor)
    if icon_name:
        btn.setIcon(icons.make_icon(icon_name, "#ffffff", 15))
    return btn


def ghost_button(text: str, icon_name: str | None = None, parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("ghost")
    btn.setCursor(Qt.PointingHandCursor)
    if icon_name:
        btn.setIcon(icons.make_icon(icon_name, DARK.text_muted, 15))
    return btn


def danger_button(text: str, icon_name: str | None = None, parent=None) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("danger")
    btn.setCursor(Qt.PointingHandCursor)
    if icon_name:
        btn.setIcon(icons.make_icon(icon_name, DARK.danger, 15))
    return btn


class ClickableRow(QWidget):
    """可点击的行容器（用于消息卡片等），发出 clicked 信号。"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):  # noqa: N802 (Qt 命名)
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


def apply_theme(app, theme: str) -> None:
    """把主题应用到 QApplication（深色为默认）。"""
    from .design import build_stylesheet

    app.setStyleSheet(build_stylesheet(palette_for(theme)))
