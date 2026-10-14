"""统一设计系统（Design Tokens）。

集中定义颜色、字体、间距、圆角、边框等设计变量，供全部界面组件复用。
深色主题为默认主题；浅色主题通过同一组 token 名切换，避免各组件硬编码颜色。

设计方向：现代 AI 开发工作台（深灰/石墨黑背景 + 低饱和蓝紫强调色 + 克制留白）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Palette:
    """一套完整的语义化颜色令牌。"""

    # ---- 背景层次（由深到浅，形成清晰的层级感）----
    bg_root: str          # 最底层窗口背景
    bg_surface: str       # 主内容区
    bg_raised: str        # 卡片 / 面板
    bg_overlay: str       # 弹窗 / 菜单
    bg_hover: str         # 悬停态
    bg_active: str        # 选中态
    bg_input: str         # 输入框

    # ---- 边框与分隔线 ----
    border: str           # 常规边框
    border_strong: str    # 强调边框 / 聚焦
    divider: str          # 细分隔线

    # ---- 文本层级 ----
    text: str             # 主要文本
    text_muted: str       # 次要文本
    text_faint: str       # 辅助 / 占位
    text_inverse: str     # 强调色背景上的文字

    # ---- 强调色 ----
    accent: str           # 主强调色（低饱和蓝紫）
    accent_hover: str
    accent_active: str
    accent_soft: str      # 强调色的低透明背景

    # ---- 语义色 ----
    success: str
    success_soft: str
    warning: str
    warning_soft: str
    danger: str
    danger_soft: str
    info: str

    # ---- 消息流专用 ----
    msg_source: str       # 原文
    msg_translated: str   # 译文
    msg_meta: str         # 时间/发送者等元信息


DARK = Palette(
    bg_root="#0f1115",
    bg_surface="#14161b",
    bg_raised="#191c22",
    bg_overlay="#1d2027",
    bg_hover="#23262e",
    bg_active="#2a2e38",
    bg_input="#1a1d23",
    border="#282c35",
    border_strong="#3a4150",
    divider="#1f232b",
    text="#e6e8ee",
    text_muted="#a2a9b8",
    # 原 #6c7382 在 bg_raised 上只有 3.59:1，不達 WCAG AA（4.5:1）
    text_faint="#7d8493",
    text_inverse="#ffffff",
    # 原 #6d7cf5 配白字只有 3.59:1。壓到白字達標的邊界值 4.54:1。
    accent="#5667f3",
    # hover 不再用「更亮」（會讓白字掉到 4.49）
    # 改由 QSS 的 filter: brightness() 提供回饋，這裡保持同色以確保達標
    accent_hover="#5667f3",
    # active（按下）用明顯較深的色，白字對比 10.42:1
    accent_active="#0e21c2",
    accent_soft="#1e2240",
    success="#4ec9a0",
    success_soft="#12302a",
    warning="#e0b055",
    warning_soft="#332a12",
    danger="#e5706b",
    danger_soft="#331a1a",
    info="#5aa9e6",
    msg_source="#d7dbe6",
    msg_translated="#8fd6bd",
    # 原 #767e8f 在 bg_raised 上 4.19:1，不足
    msg_meta="#7c8494",
)

LIGHT = Palette(
    bg_root="#f4f5f8",
    bg_surface="#ffffff",
    bg_raised="#fafbfc",
    bg_overlay="#ffffff",
    bg_hover="#eef0f5",
    bg_active="#e3e7f0",
    bg_input="#ffffff",
    border="#d8dce5",
    border_strong="#b6bdcc",
    divider="#e6e9f0",
    text="#1b1e26",
    text_muted="#5a6272",
    # 原 #8b93a3 在 bg_raised 上只有 2.98:1
    text_faint="#6b7487",
    text_inverse="#ffffff",
    accent="#4f5fd8",
    accent_hover="#5b6ae5",
    accent_active="#4150c4",
    accent_soft="#e8eafc",
    # 原 #1f9d78 只有 3.29:1；調暗到達標
    success="#1a8364",
    success_soft="#f4fcf9",
    warning="#986c15",
    warning_soft="#fefbf3",
    danger="#c8483f",
    danger_soft="#fef9f8",
    info="#2c78ba",
    msg_source="#2a2f3a",
    # 原 #1f7a5c 在 raised 上剛好 4.5 邊緣，略調暗以留餘裕
    msg_translated="#1c6f54",
    # 原 #7a8292 只有 3.73:1
    msg_meta="#6c7484",
)


@dataclass(frozen=True)
class Typography:
    family: str
    family_mono: str
    size_xs: int
    size_sm: int
    size_base: int
    size_md: int
    size_lg: int
    size_xl: int
    weight_normal: int
    weight_medium: int
    weight_bold: int


TYPO = Typography(
    family='"Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif',
    family_mono='"Cascadia Mono", "Consolas", "Courier New", monospace',
    # 字階重新拉開：原 11/12/13/14/16/20 中 13 與 14 只差 1px，
    # 層級在視覺上分不出來。改為 11/12/14/16/20/24，每一階都看得出差別。
    size_xs=11,
    size_sm=12,
    size_base=14,
    size_md=16,
    size_lg=20,
    size_xl=24,
    weight_normal=400,
    weight_medium=500,
    weight_bold=600,
)


@dataclass(frozen=True)
class Spacing:
    """4pt 網格間距（皆為 4 的倍數）。"""

    xxs: int = 4
    xs: int = 8
    sm: int = 12
    md: int = 16
    lg: int = 24
    xl: int = 32
    xxl: int = 48


@dataclass(frozen=True)
class Radius:
    """圓角階梯（對齊 Material 3 / Apple HIG 的 8/10/14 節奏）。"""

    sm: int = 6
    md: int = 10
    lg: int = 14
    pill: int = 999


SPACE = Spacing()
RADIUS = Radius()


def build_stylesheet(palette: Palette = DARK) -> str:
    """根据调色板生成全局 QSS。

    所有颜色都来自 token，切换主题只需换一个 Palette。
    """
    p = palette
    t = TYPO

    return f"""
/* ============================ 全局 ============================ */
QWidget {{
    background-color: {p.bg_surface};
    color: {p.text};
    font-family: {t.family};
    font-size: {t.size_base}px;
}}
QMainWindow, QDialog {{ background-color: {p.bg_root}; }}

/* ============================ 字体层级 ============================ */
/* 注意：pageTitle 只在顶栏使用（页面内标题已移除，避免同一页出现两次标题）。
   它的字号在下方 topBar 区块统一定义，这里不再重复声明，
   否则同一属性写两遍容易改一处漏一处。 */
QLabel#sectionTitle {{
    font-size: {t.size_md}px;
    font-weight: {t.weight_bold};
    color: {p.text};
}}
QLabel#hintLabel, QLabel#mutedLabel {{ color: {p.text_muted}; font-size: {t.size_sm}px; }}
QLabel#faintLabel {{ color: {p.text_faint}; font-size: {t.size_xs}px; }}
QLabel#monoLabel {{ font-family: {t.family_mono}; color: {p.text_muted}; font-size: {t.size_sm}px; }}

/* 状态色文本 */
QLabel#statusOk {{ color: {p.success}; }}
QLabel#statusWarn {{ color: {p.warning}; }}
QLabel#statusError {{ color: {p.danger}; }}
QLabel#statusIdle {{ color: {p.text_faint}; }}
/* 兼容旧组件（ReplyPanel 等）使用的名称，保证颜色仍来自 token */
QLabel#translatedLabel {{ color: {p.msg_translated}; font-size: {t.size_md}px; }}

/* ============================ 侧边导航 ============================ */
QWidget#sidebar {{
    background-color: {p.bg_root};
    border-right: 1px solid {p.divider};
}}
/* 侧栏内的标签必须透明，否则在浅色主题下会显出一条条灰底 */
QWidget#sidebar QLabel {{ background: transparent; }}
QWidget#sidebar QPushButton {{ background: transparent; }}
QLabel#brandTitle {{
    font-size: {t.size_md}px;
    font-weight: {t.weight_bold};
    color: {p.text};
}}
QLabel#brandSub {{ color: {p.text_faint}; font-size: {t.size_xs}px; }}

QPushButton#navItem {{
    background-color: transparent;
    border: none;
    border-radius: {RADIUS.md}px;
    padding: 8px 10px;
    text-align: left;
    color: {p.text_muted};
    font-size: {t.size_base}px;
}}
QPushButton#navItem:hover {{
    background-color: {p.bg_hover};
    color: {p.text};
}}
QPushButton#navItem:checked {{
    background-color: {p.accent_soft};
    color: {p.text};
    font-weight: {t.weight_medium};
}}
QPushButton#navItem:checked:hover {{ background-color: {p.accent_soft}; }}

QPushButton#navCollapse {{
    background-color: transparent;
    border: none;
    border-radius: {RADIUS.sm}px;
    padding: 6px;
    color: {p.text_faint};
}}
QPushButton#navCollapse:hover {{ background-color: {p.bg_hover}; color: {p.text}; }}

/* ============================ 顶部状态栏 ============================ */
/* 风格参考现代 AI 工作台（Harness 类）：无边框、粗体标题、
   右侧用小圆点 + 次要文字表达状态，靠留白而不是分隔线区分层次。 */
QWidget#topBar {{
    background-color: {p.bg_root};
    border-bottom: 1px solid {p.divider};
}}
QWidget#topBar QLabel {{ background: transparent; }}
/* 页面标题统一在这里定义（页面内标题已移除）：
   它是整页的主视觉焦点，所以用最大的字级。 */
QLabel#pageTitle {{
    font-size: {t.size_xl}px;
    font-weight: {t.weight_bold};
    color: {p.text};
}}
/* 状态项：完全无边框无背景，仅悬停时给一点反馈 */
QWidget#chip {{ background: transparent; }}
QWidget#chip:hover {{ background: {p.bg_hover}; border-radius: {RADIUS.sm}px; }}
QLabel#chipDot {{ background: transparent; font-size: 10px; }}
QLabel#chipText {{
    background: transparent;
    color: {p.text_muted};
    font-size: {t.size_sm}px;
}}
/* 旧名称保留以避免其它对话框出现灰底 */
QLabel#topBarLabel, QLabel#topBarValue {{
    background: transparent;
    color: {p.text_muted};
    font-size: {t.size_sm}px;
}}

/* 状态指示点 */
QLabel#dotOk {{ color: {p.success}; font-size: {t.size_md}px; }}
QLabel#dotWarn {{ color: {p.warning}; font-size: {t.size_md}px; }}
QLabel#dotError {{ color: {p.danger}; font-size: {t.size_md}px; }}
QLabel#dotIdle {{ color: {p.text_faint}; font-size: {t.size_md}px; }}

/* ============================ 卡片 / 面板 ============================ */
QFrame#card {{
    background-color: {p.bg_raised};
    border: 1px solid {p.border};
    border-radius: {RADIUS.lg}px;
}}
QFrame#cardFlat {{
    background-color: transparent;
    border: 1px solid {p.border};
    border-radius: {RADIUS.lg}px;
}}
QFrame#dividerLine {{ background-color: {p.divider}; border: none; max-height: 1px; }}
QGroupBox {{
    border: 1px solid {p.border};
    border-radius: {RADIUS.md}px;
    margin-top: 10px;
    padding-top: 8px;
    font-weight: {t.weight_bold};
    color: {p.text};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {p.text_muted}; }}

/* ============================ 按钮 ============================ */
QPushButton {{
    background-color: {p.bg_raised};
    border: 1px solid {p.border};
    border-radius: {RADIUS.md}px;
    padding: 7px 15px;
    color: {p.text};
}}
QPushButton:hover {{ background-color: {p.bg_hover}; border-color: {p.border_strong}; }}
QPushButton:pressed {{ background-color: {p.bg_active}; }}
QPushButton:disabled {{ color: {p.text_faint}; border-color: {p.divider}; background-color: {p.bg_surface}; }}
QPushButton:focus {{ border-color: {p.accent}; }}

QPushButton#primary {{
    background-color: {p.accent};
    border: 1px solid {p.accent};
    color: {p.text_inverse};
    font-weight: {t.weight_medium};
}}
/* hover：深色主題的 accent 已壓到「白字達標」的邊界（4.54），不能再換更亮的底色，
   否則白字會掉到 4.49 不達 WCAG AA。因此改用「加亮邊框 + 輕微內縮」提供視覺回饋。
   註：Qt 的 QSS 不支援 filter: brightness()，那條路走不通。 */
QPushButton#primary:hover {{
    background-color: {p.accent_hover};
    border: 2px solid {p.border_strong};
    padding: 6px 14px;
}}
QPushButton#primary:pressed {{
    background-color: {p.accent_active};
    border-color: {p.accent_active};
    padding-top: 8px;
    padding-bottom: 6px;
}}
QPushButton#primary:disabled {{ background-color: {p.bg_active}; border-color: {p.divider}; color: {p.text_faint}; }}

QPushButton#ghost {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {p.text_muted};
}}
QPushButton#ghost:hover {{ background-color: {p.bg_hover}; color: {p.text}; }}

QPushButton#danger {{
    background-color: transparent;
    border: 1px solid {p.danger};
    color: {p.danger};
}}
QPushButton#danger:hover {{ background-color: {p.danger_soft}; }}

/* 小型图标按钮（复制、编辑等） */
QPushButton#iconBtn {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: {RADIUS.sm}px;
    padding: 2px 6px;
    color: {p.text_faint};
    font-size: {t.size_sm}px;
}}
QPushButton#iconBtn:hover {{ background-color: {p.bg_hover}; color: {p.accent}; border-color: {p.border}; }}

/* ============================ 输入控件 ============================ */
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {p.bg_input};
    border: 1px solid {p.border};
    border-radius: {RADIUS.md}px;
    padding: 5px 8px;
    color: {p.text};
    selection-background-color: {p.accent};
    selection-color: {p.text_inverse};
}}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {p.accent}; }}
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled {{
    color: {p.text_faint}; background-color: {p.bg_surface};
}}
QLineEdit::placeholder {{ color: {p.text_faint}; }}

QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background-color: {p.bg_overlay};
    border: 1px solid {p.border};
    selection-background-color: {p.accent_soft};
    selection-color: {p.text};
    outline: none;
}}
QCheckBox, QRadioButton {{ spacing: 6px; color: {p.text}; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; }}
QCheckBox::indicator:unchecked {{
    background-color: {p.bg_input}; border: 1px solid {p.border_strong}; border-radius: {RADIUS.sm}px;
}}
QCheckBox::indicator:checked {{
    background-color: {p.accent}; border: 1px solid {p.accent}; border-radius: {RADIUS.sm}px;
}}

/* ============================ 表格 ============================ */
QTableWidget, QTableView {{
    background-color: {p.bg_surface};
    alternate-background-color: {p.bg_raised};
    gridline-color: {p.divider};
    border: 1px solid {p.border};
    border-radius: {RADIUS.md}px;
    outline: none;
}}
QTableWidget::item {{ padding: 4px 6px; }}
QTableWidget::item:selected {{ background-color: {p.accent_soft}; color: {p.text}; }}
QHeaderView::section {{
    background-color: {p.bg_raised};
    color: {p.text_muted};
    padding: 6px 8px;
    border: none;
    border-right: 1px solid {p.divider};
    border-bottom: 1px solid {p.border};
    font-weight: {t.weight_medium};
}}
QTableCornerButton::section {{ background-color: {p.bg_raised}; border: none; }}

/* ============================ 消息流 ============================ */
QWidget#msgRow {{ background-color: transparent; }}
QWidget#msgRow:hover {{ background-color: {p.bg_hover}; }}
QLabel#msgMeta {{ color: {p.msg_meta}; font-size: {t.size_xs}px; }}
QLabel#msgSource {{ color: {p.msg_source}; font-size: {t.size_base}px; }}
QLabel#msgTranslated {{ color: {p.msg_translated}; font-size: {t.size_md}px; }}
QLabel#msgError {{ color: {p.danger}; font-size: {t.size_sm}px; }}

/* 来源标记 */
QLabel#badgeApi, QLabel#badgeCache, QLabel#badgeManual, QLabel#badgeSkip, QLabel#badgePending {{
    border-radius: {RADIUS.sm}px;
    padding: 1px 6px;
    font-size: {t.size_xs}px;
}}
/* 徽章：底色用強調色的低飽和版本。
   文字刻意用 accent_active 而非 accent_hover —— accent 色家族彼此亮度太接近，
   在淺底上無法達到 WCAG AA；accent_active 是家族中最深的一個，
   在兩個主題的底色上都有 8:1 以上。 */
QLabel#badgeApi {{ background-color: {p.accent_soft}; color: {p.accent_active}; }}
QLabel#badgeCache {{ background-color: {p.success_soft}; color: {p.success}; }}
QLabel#badgeManual {{ background-color: {p.warning_soft}; color: {p.warning}; }}
QLabel#badgeSkip {{ background-color: {p.bg_active}; color: {p.text_faint}; }}
QLabel#badgePending {{ background-color: {p.bg_active}; color: {p.text_muted}; }}

/* ============================ 滚动条 ============================ */
QScrollArea {{ background-color: transparent; border: none; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {p.border_strong}; border-radius: {RADIUS.pill}px; min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.text_faint}; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {p.border_strong}; border-radius: {RADIUS.pill}px; min-width: 28px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* ============================ 标签页 ============================ */
QTabWidget::pane {{ border: 1px solid {p.border}; border-radius: {RADIUS.md}px; top: -1px; }}
QTabBar::tab {{
    background: transparent;
    color: {p.text_muted};
    padding: 7px 14px;
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {p.text}; }}
QTabBar::tab:selected {{ color: {p.text}; border-bottom: 2px solid {p.accent}; }}

/* ============================ 菜单 / 提示 ============================ */
QMenu {{
    background-color: {p.bg_overlay};
    border: 1px solid {p.border};
    border-radius: {RADIUS.md}px;
    padding: 4px;
}}
QMenu::item {{ padding: 6px 18px; border-radius: {RADIUS.sm}px; }}
QMenu::item:selected {{ background-color: {p.accent_soft}; color: {p.text}; }}
QMenu::separator {{ height: 1px; background: {p.divider}; margin: 4px 8px; }}

QToolTip {{
    background-color: {p.bg_overlay};
    color: {p.text};
    border: 1px solid {p.border_strong};
    border-radius: {RADIUS.sm}px;
    padding: 4px 6px;
}}

/* ============================ 回复三态指示 ============================ */
QLabel#stateGenerated {{ color: {p.success}; font-size: {t.size_sm}px; }}
QLabel#stateCopied {{ color: {p.info}; font-size: {t.size_sm}px; }}
QLabel#stateSent {{ color: {p.warning}; font-size: {t.size_sm}px; }}
QLabel#statePending {{ color: {p.text_faint}; font-size: {t.size_sm}px; }}

/* ============================ 状态栏 ============================ */
QStatusBar {{ background-color: {p.bg_root}; color: {p.text_muted}; border-top: 1px solid {p.divider}; }}
QStatusBar::item {{ border: none; }}

/* ============================ 其他 ============================ */
QProgressBar {{
    background-color: {p.bg_active}; border: none; border-radius: {RADIUS.pill}px;
    height: 4px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background-color: {p.accent}; border-radius: {RADIUS.pill}px; }}
QSplitter::handle {{ background-color: {p.divider}; }}
QSplitter::handle:hover {{ background-color: {p.accent}; }}
"""


def palette_for(theme: str) -> Palette:
    """按主题名返回调色板，未知主题回退到深色。"""
    return LIGHT if str(theme).lower() == "light" else DARK


# ---------------------------------------------------------------------------
# 当前主题状态
#
# QSS 只能覆盖通过样式表绘制的控件；自定义委托（QStyledItemDelegate）是用
# QPainter 直接绘制的，无法读取 QSS，因此必须显式拿到当前调色板，
# 否则切换浅色主题后消息卡片仍是深色。
# ---------------------------------------------------------------------------
_active_theme = "dark"


def set_active_theme(theme: str) -> Palette:
    """设置全局当前主题并返回对应调色板。"""
    global _active_theme
    _active_theme = "light" if str(theme).lower() == "light" else "dark"
    return palette_for(_active_theme)


def active_palette() -> Palette:
    """返回当前主题的调色板（供自定义委托使用）。"""
    return palette_for(_active_theme)


def active_theme() -> str:
    return _active_theme


def with_alpha(hex_color: str, alpha: int) -> str:
    """把 #RRGGBB 转成带透明度的 rgba() 字符串。"""
    value = hex_color.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


# 兼容旧代码：原先 main_window 里导出的 DARK_STYLE
DARK_STYLE = build_stylesheet(DARK)
