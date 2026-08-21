"""中央工作区：QStackedWidget 多页面 + 淡入淡出切换动画。

页面视觉：深色 SaaS 仪表盘风格——卡片化布局、霓虹状态色。
"""
from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QStackedWidget,
    QVBoxLayout, QWidget,
)

# 统计卡：图标方块样式名, 字形, 数值, 标签
WELCOME_STATS = [
    ("StatIconCyan", "▤", "0", "已导入模组"),
    ("StatIconPurple", "≡", "0", "章节"),
    ("StatIconPink", "✦", "0", "NPC"),
    ("StatIconGreen", "✎", "0", "扩写段落"),
]

# 快速开始步骤
QUICK_START_STEPS = [
    ("1", "导入模组", "导入 PDF / Word 模组文档，自动解析章节与设定"),
    ("2", "生成章节", "章节列表将出现在左侧导航栏"),
    ("3", "开始创作", "扩写 NPC、润色文本、掌控跑团节奏"),
]


def _make_stat_card(icon_style: str, glyph: str, value: str, label: str) -> QFrame:
    """创建统计卡：霓虹图标方块 + 数值 + 说明文字。"""
    card = QFrame()
    card.setObjectName("Card")
    card.setFixedHeight(92)
    layout = QHBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(12)
    icon = QLabel(glyph)
    icon.setObjectName(icon_style)
    icon.setFixedSize(40, 40)
    text = QVBoxLayout()
    text.setSpacing(2)
    value_label = QLabel(value)
    value_label.setObjectName("CardValue")
    caption = QLabel(label)
    caption.setObjectName("CardLabel")
    text.addWidget(value_label)
    text.addWidget(caption)
    layout.addWidget(icon)
    layout.addLayout(text, 1)
    return card


class WelcomePage(QWidget):
    """欢迎页：标题 + 统计卡行 + 快速开始卡片。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)

        title = QLabel("欢迎使用 GM Copilot")
        title.setObjectName("PageTitle")
        subtitle = QLabel("导入模组文档，开启你的跑团之旅")
        subtitle.setObjectName("PageSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(8)

        # 统计卡行
        stats_row = QHBoxLayout()
        stats_row.setSpacing(12)
        for icon_style, glyph, value, label in WELCOME_STATS:
            stats_row.addWidget(_make_stat_card(icon_style, glyph, value, label), 1)
        layout.addLayout(stats_row)

        # 快速开始卡片
        start_card = QFrame()
        start_card.setObjectName("Card")
        start_layout = QVBoxLayout(start_card)
        start_layout.setContentsMargins(20, 18, 20, 18)
        start_layout.setSpacing(12)
        card_title = QLabel("快速开始")
        card_title.setObjectName("CardTitle")
        start_layout.addWidget(card_title)
        for num, step_title, step_desc in QUICK_START_STEPS:
            row = QHBoxLayout()
            row.setSpacing(12)
            num_label = QLabel(num)
            num_label.setObjectName("TagCyan")
            num_label.setFixedWidth(24)
            text_col = QVBoxLayout()
            text_col.setSpacing(2)
            t = QLabel(step_title)
            t.setObjectName("CardTitle")
            d = QLabel(step_desc)
            d.setObjectName("CardLabel")
            d.setWordWrap(True)
            text_col.addWidget(t)
            text_col.addWidget(d)
            row.addWidget(num_label, 0, Qt.AlignTop)
            row.addLayout(text_col, 1)
            start_layout.addLayout(row)
        layout.addWidget(start_card)

        layout.addStretch(1)


class PlaceholderPage(QWidget):
    """功能占位页：卡片式展示模块标题与说明（具体功能后续阶段实现）。"""

    def __init__(self, title: str, description: str, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 28, 28, 28)
        layout.setSpacing(16)

        card = QFrame()
        card.setObjectName("Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 24, 24, 24)
        card_layout.setSpacing(12)

        icon = QLabel("✦")
        icon.setObjectName("StatIconPurple")
        icon.setFixedSize(40, 40)
        title_label = QLabel(title)
        title_label.setObjectName("PlaceholderTitle")
        desc = QLabel(description)
        desc.setObjectName("InfoBody")
        desc.setWordWrap(True)
        tag = QLabel("后续阶段开放")
        tag.setObjectName("TagOrange")

        card_layout.addWidget(icon)
        card_layout.addWidget(title_label)
        card_layout.addWidget(desc)
        card_layout.addWidget(tag)
        card_layout.addStretch(1)

        layout.addWidget(card, 1)
        layout.addStretch(0)


class Workspace(QWidget):
    """中央工作区：承载多个页面并支持淡入淡出切换。"""

    page_changed = Signal(str)  # 切换完成后发射当前页面 key

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Workspace")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._keys: list[str] = []
        self._pages: dict[str, QWidget] = {}
        self._current_key = ""
        self._phase = "idle"
        self._target_index = 0

        self._stack = QStackedWidget(self)
        self._opacity = QGraphicsOpacityEffect(self._stack)
        self._opacity.setOpacity(1.0)
        self._stack.setGraphicsEffect(self._opacity)

        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.finished.connect(self._on_fade_finished)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

    # ---- 页面管理 ----
    def add_page(self, key: str, widget: QWidget) -> None:
        """注册页面：key 为唯一标识，widget 为页面控件。"""
        self._keys.append(key)
        self._pages[key] = widget
        self._stack.addWidget(widget)
        if not self._current_key:
            self._current_key = key
            self._stack.setCurrentWidget(widget)

    @property
    def current_key(self) -> str:
        """当前显示页面的 key。"""
        return self._current_key

    def show_page(self, key: str) -> None:
        """切换到指定页面，带淡出-淡入动画。"""
        if key == self._current_key or key not in self._pages:
            return
        if self._phase != "idle":
            self._anim.stop()
            self._opacity.setOpacity(1.0)
            self._phase = "idle"
        self._target_index = self._keys.index(key)
        self._phase = "fading_out"
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.start()

    # ---- 动画回调 ----
    def _on_fade_finished(self) -> None:
        """淡出完成后切换页面并淡入。"""
        if self._phase == "fading_out":
            self._stack.setCurrentIndex(self._target_index)
            self._current_key = self._keys[self._target_index]
            self._phase = "fading_in"
            self._anim.setStartValue(0.0)
            self._anim.setEndValue(1.0)
            self._anim.start()
        elif self._phase == "fading_in":
            self._phase = "idle"
            self.page_changed.emit(self._current_key)
