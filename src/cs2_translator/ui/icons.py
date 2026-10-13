"""线性图标：用 QPainter 程序化绘制，避免引入图标依赖。

设计约束（与设计系统一致）：
- 统一 24x24 逻辑画布，线宽 1.6，圆角端点与连接；
- 只使用当前强调色/文本色，支持禁用态变淡；
- 全部为线性（描边）风格，不填充，保证视觉统一。

用法::

    icon = make_icon("chat", color)          # QIcon
    button.setIcon(make_icon("settings", color))
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

# 24x24 逻辑画布
_CANVAS = 24.0
_STROKE = 1.7


def _pen(color: QColor, width: float = _STROKE) -> QPen:
    pen = QPen(color)
    pen.setWidthF(width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _draw_chat(p: QPainter, pen: QPen) -> None:
    """对话气泡。"""
    p.setPen(pen)
    path = QPainterPath()
    path.addRoundedRect(QRectF(3.5, 5.0, 17.0, 12.0), 2.5, 2.5)
    p.drawPath(path)
    # 尾巴
    p.drawLine(QPointF(8.0, 17.0), QPointF(8.0, 20.0))
    p.drawLine(QPointF(8.0, 20.0), QPointF(11.5, 17.0))
    # 内部文本线
    p.drawLine(QPointF(7.0, 9.5), QPointF(17.0, 9.5))
    p.drawLine(QPointF(7.0, 12.5), QPointF(13.5, 12.5))


def _draw_history(p: QPainter, pen: QPen) -> None:
    """时钟 + 列表，表示历史对局。"""
    p.setPen(pen)
    p.drawEllipse(QPointF(12.0, 12.0), 8.0, 8.0)
    p.drawLine(QPointF(12.0, 7.5), QPointF(12.0, 12.0))
    p.drawLine(QPointF(12.0, 12.0), QPointF(15.5, 13.8))


def _draw_memory(p: QPainter, pen: QPen) -> None:
    """数据库堆栈，表示长期翻译记忆。"""
    p.setPen(pen)
    p.drawEllipse(QPointF(12.0, 6.5), 7.0, 2.8)
    p.drawLine(QPointF(5.0, 6.5), QPointF(5.0, 12.0))
    p.drawLine(QPointF(19.0, 6.5), QPointF(19.0, 12.0))
    path = QPainterPath()
    path.moveTo(5.0, 12.0)
    path.arcTo(QRectF(5.0, 9.2, 14.0, 5.6), 180.0, 180.0)
    p.drawPath(path)
    p.drawLine(QPointF(5.0, 12.0), QPointF(5.0, 17.5))
    p.drawLine(QPointF(19.0, 12.0), QPointF(19.0, 17.5))
    path2 = QPainterPath()
    path2.moveTo(5.0, 17.5)
    path2.arcTo(QRectF(5.0, 14.7, 14.0, 5.6), 180.0, 180.0)
    p.drawPath(path2)


def _draw_glossary(p: QPainter, pen: QPen) -> None:
    """书本，表示词汇表。"""
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(4.5, 5.0)
    path.lineTo(11.5, 5.0)
    path.lineTo(11.5, 19.0)
    path.lineTo(4.5, 19.0)
    path.closeSubpath()
    p.drawPath(path)
    path2 = QPainterPath()
    path2.moveTo(19.5, 5.0)
    path2.lineTo(12.5, 5.0)
    path2.lineTo(12.5, 19.0)
    path2.lineTo(19.5, 19.0)
    path2.closeSubpath()
    p.drawPath(path2)
    p.drawLine(QPointF(12.0, 5.0), QPointF(12.0, 19.0))


def _draw_settings(p: QPainter, pen: QPen) -> None:
    """齿轮（简化：内圆 + 8 条齿）。"""
    p.setPen(pen)
    p.drawEllipse(QPointF(12.0, 12.0), 3.2, 3.2)
    p.drawEllipse(QPointF(12.0, 12.0), 7.8, 7.8)
    for i in range(8):
        angle = math.radians(i * 45.0)
        x1 = 12.0 + 7.8 * math.cos(angle)
        y1 = 12.0 + 7.8 * math.sin(angle)
        x2 = 12.0 + 10.2 * math.cos(angle)
        y2 = 12.0 + 10.2 * math.sin(angle)
        p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _draw_copy(p: QPainter, pen: QPen) -> None:
    """复制：两个错位矩形。"""
    p.setPen(pen)
    p.drawRoundedRect(QRectF(8.5, 8.5, 11.0, 11.0), 2.0, 2.0)
    path = QPainterPath()
    path.moveTo(15.5, 8.5)
    path.lineTo(15.5, 6.0)
    path.lineTo(4.5, 6.0)
    path.lineTo(4.5, 15.5)
    path.lineTo(8.5, 15.5)
    p.drawPath(path)


def _draw_check(p: QPainter, pen: QPen) -> None:
    """对勾，表示人工确认。"""
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(5.0, 12.5)
    path.lineTo(10.0, 17.5)
    path.lineTo(19.0, 6.5)
    p.drawPath(path)


def _draw_edit(p: QPainter, pen: QPen) -> None:
    """铅笔，表示编辑。"""
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(16.5, 4.5)
    path.lineTo(19.5, 7.5)
    path.lineTo(9.0, 18.0)
    path.lineTo(4.5, 19.5)
    path.lineTo(6.0, 15.0)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(14.0, 7.0), QPointF(17.0, 10.0))


def _draw_trash(p: QPainter, pen: QPen) -> None:
    """垃圾桶，表示删除。"""
    p.setPen(pen)
    p.drawLine(QPointF(4.5, 6.5), QPointF(19.5, 6.5))
    p.drawLine(QPointF(9.5, 6.5), QPointF(9.5, 4.5))
    p.drawLine(QPointF(9.5, 4.5), QPointF(14.5, 4.5))
    p.drawLine(QPointF(14.5, 4.5), QPointF(14.5, 6.5))
    path = QPainterPath()
    path.moveTo(6.5, 6.5)
    path.lineTo(7.5, 19.5)
    path.lineTo(16.5, 19.5)
    path.lineTo(17.5, 6.5)
    p.drawPath(path)


def _draw_plus(p: QPainter, pen: QPen) -> None:
    p.setPen(pen)
    p.drawLine(QPointF(12.0, 5.5), QPointF(12.0, 18.5))
    p.drawLine(QPointF(5.5, 12.0), QPointF(18.5, 12.0))


def _draw_search(p: QPainter, pen: QPen) -> None:
    p.setPen(pen)
    p.drawEllipse(QPointF(10.5, 10.5), 5.5, 5.5)
    p.drawLine(QPointF(14.6, 14.6), QPointF(19.0, 19.0))


def _draw_refresh(p: QPainter, pen: QPen) -> None:
    """刷新：圆弧 + 箭头。"""
    p.setPen(pen)
    path = QPainterPath()
    path.arcMoveTo(QRectF(4.5, 4.5, 15.0, 15.0), 60.0)
    path.arcTo(QRectF(4.5, 4.5, 15.0, 15.0), 60.0, 280.0)
    p.drawPath(path)
    p.drawLine(QPointF(16.2, 3.6), QPointF(17.4, 8.2))
    p.drawLine(QPointF(17.4, 8.2), QPointF(13.0, 7.6))


def _draw_play(p: QPainter, pen: QPen) -> None:
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(7.0, 4.5)
    path.lineTo(18.5, 12.0)
    path.lineTo(7.0, 19.5)
    path.closeSubpath()
    p.drawPath(path)


def _draw_pause(p: QPainter, pen: QPen) -> None:
    p.setPen(pen)
    p.drawLine(QPointF(9.0, 5.0), QPointF(9.0, 19.0))
    p.drawLine(QPointF(15.0, 5.0), QPointF(15.0, 19.0))


def _draw_stop(p: QPainter, pen: QPen) -> None:
    p.setPen(pen)
    p.drawRoundedRect(QRectF(6.0, 6.0, 12.0, 12.0), 2.0, 2.0)


def _draw_chevron_left(p: QPainter, pen: QPen) -> None:
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(14.5, 5.5)
    path.lineTo(8.5, 12.0)
    path.lineTo(14.5, 18.5)
    p.drawPath(path)


def _draw_chevron_right(p: QPainter, pen: QPen) -> None:
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(9.5, 5.5)
    path.lineTo(15.5, 12.0)
    path.lineTo(9.5, 18.5)
    p.drawPath(path)


def _draw_chevron_down(p: QPainter, pen: QPen) -> None:
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(5.5, 9.5)
    path.lineTo(12.0, 15.5)
    path.lineTo(18.5, 9.5)
    p.drawPath(path)


def _draw_file(p: QPainter, pen: QPen) -> None:
    """文档。"""
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(6.0, 3.5)
    path.lineTo(14.0, 3.5)
    path.lineTo(18.5, 8.0)
    path.lineTo(18.5, 20.5)
    path.lineTo(6.0, 20.5)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(14.0, 3.5), QPointF(14.0, 8.0))
    p.drawLine(QPointF(14.0, 8.0), QPointF(18.5, 8.0))
    p.drawLine(QPointF(9.0, 12.5), QPointF(15.5, 12.5))
    p.drawLine(QPointF(9.0, 16.0), QPointF(15.5, 16.0))


def _draw_send(p: QPainter, pen: QPen) -> None:
    """发送/翻译箭头。"""
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(4.5, 12.0)
    path.lineTo(19.5, 4.5)
    path.lineTo(14.5, 19.5)
    path.lineTo(12.0, 13.5)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(12.0, 13.5), QPointF(19.5, 4.5))


def _draw_alert(p: QPainter, pen: QPen) -> None:
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(12.0, 4.0)
    path.lineTo(21.0, 19.5)
    path.lineTo(3.0, 19.5)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QPointF(12.0, 9.5), QPointF(12.0, 14.0))
    p.drawPoint(QPointF(12.0, 16.8))


def _draw_info(p: QPainter, pen: QPen) -> None:
    p.setPen(pen)
    p.drawEllipse(QPointF(12.0, 12.0), 8.0, 8.0)
    p.drawLine(QPointF(12.0, 11.0), QPointF(12.0, 16.0))
    p.drawPoint(QPointF(12.0, 8.2))


def _draw_layers(p: QPainter, pen: QPen) -> None:
    """图层，表示对局隔离/分层数据。"""
    p.setPen(pen)
    p.drawLine(QPointF(12.0, 3.5), QPointF(20.5, 8.0))
    p.drawLine(QPointF(20.5, 8.0), QPointF(12.0, 12.5))
    p.drawLine(QPointF(12.0, 12.5), QPointF(3.5, 8.0))
    p.drawLine(QPointF(3.5, 8.0), QPointF(12.0, 3.5))
    path = QPainterPath()
    path.moveTo(3.5, 12.0)
    path.lineTo(12.0, 16.5)
    path.lineTo(20.5, 12.0)
    p.drawPath(path)
    path2 = QPainterPath()
    path2.moveTo(3.5, 16.0)
    path2.lineTo(12.0, 20.5)
    path2.lineTo(20.5, 16.0)
    p.drawPath(path2)


def _draw_external(p: QPainter, pen: QPen) -> None:
    p.setPen(pen)
    path = QPainterPath()
    path.moveTo(13.0, 4.5)
    path.lineTo(19.5, 4.5)
    path.lineTo(19.5, 11.0)
    p.drawPath(path)
    p.drawLine(QPointF(19.5, 4.5), QPointF(11.0, 13.0))
    path2 = QPainterPath()
    path2.moveTo(17.0, 14.0)
    path2.lineTo(17.0, 19.5)
    path2.lineTo(4.5, 19.5)
    path2.lineTo(4.5, 7.0)
    path2.lineTo(10.0, 7.0)
    p.drawPath(path2)


_PAINTERS = {
    "chat": _draw_chat,
    "history": _draw_history,
    "memory": _draw_memory,
    "glossary": _draw_glossary,
    "settings": _draw_settings,
    "copy": _draw_copy,
    "check": _draw_check,
    "edit": _draw_edit,
    "trash": _draw_trash,
    "plus": _draw_plus,
    "search": _draw_search,
    "refresh": _draw_refresh,
    "play": _draw_play,
    "pause": _draw_pause,
    "stop": _draw_stop,
    "chevron-left": _draw_chevron_left,
    "chevron-right": _draw_chevron_right,
    "chevron-down": _draw_chevron_down,
    "file": _draw_file,
    "send": _draw_send,
    "alert": _draw_alert,
    "info": _draw_info,
    "layers": _draw_layers,
    "external": _draw_external,
}

ICON_NAMES = sorted(_PAINTERS.keys())


def make_pixmap(name: str, color: str = "#e6e8ee", size: int = 18, width: float | None = None) -> QPixmap:
    """绘制单个图标为 QPixmap。

    Args:
        name: 图标名（见 ``ICON_NAMES``）。
        color: 描边颜色。
        size: 输出像素尺寸（正方形）。
        width: 可选线宽覆盖。
    """
    painter_fn = _PAINTERS.get(name)
    # 以 devicePixelRatio=1 渲染，交由 Qt 在高 DPI 下缩放
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    if painter_fn is None:
        return pm

    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    scale = size / _CANVAS
    p.scale(scale, scale)
    pen = _pen(QColor(color), width if width is not None else _STROKE)
    try:
        painter_fn(p, pen)
    finally:
        p.end()
    return pm


def make_icon(name: str, color: str = "#e6e8ee", size: int = 18) -> QIcon:
    """绘制单个图标为 QIcon，并附带禁用态（变淡）版本。"""
    icon = QIcon()
    icon.addPixmap(make_pixmap(name, color, size), QIcon.Normal)
    # 禁用态：低透明度
    muted = QColor(color)
    muted.setAlpha(90)
    icon.addPixmap(make_pixmap(name, muted.name(QColor.HexArgb), size), QIcon.Disabled)
    return icon


def make_icon_multi(name: str, colors: dict, size: int = 18) -> QIcon:
    """按状态生成多态图标，``colors`` 形如 ``{"normal": "#fff", "active": "#6d7cf5"}``。"""
    icon = QIcon()
    icon.addPixmap(make_pixmap(name, colors.get("normal", "#e6e8ee"), size), QIcon.Normal)
    icon.addPixmap(
        make_pixmap(name, colors.get("active", colors.get("normal", "#e6e8ee")), size),
        QIcon.Normal,
        QIcon.On,
    )
    return icon
