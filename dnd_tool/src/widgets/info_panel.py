"""右侧信息面板：卡片式布局，默认欢迎提示，NPC 完整卡片预览。"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)


class InfoPanel(QWidget):
    """右侧信息面板（卡片样式）。"""

    npc_edit_requested = Signal(str)  # 点击 NPC 卡片编辑按钮

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("InfoPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(280)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        # 头部：状态圆点 + 标题 + 状态标签（包在固定高度容器中，防止被垂直拉伸）
        header_wrap = QWidget()
        header_wrap.setFixedHeight(26)
        header = QHBoxLayout(header_wrap)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        dot = QLabel()
        dot.setObjectName("DotCyan")
        dot.setFixedSize(8, 8)
        self._title = QLabel("信息面板")
        self._title.setObjectName("InfoTitle")
        self._title.setFixedHeight(22)
        self._tag = QLabel("欢迎")
        self._tag.setObjectName("TagCyan")
        self._tag.setFixedHeight(22)
        header.addWidget(dot, 0, Qt.AlignVCenter)
        header.addWidget(self._title, 0, Qt.AlignVCenter)
        header.addStretch(1)
        header.addWidget(self._tag, 0, Qt.AlignVCenter)
        layout.addWidget(header_wrap)

        self._body = QLabel("")
        self._body.setObjectName("InfoBody")
        self._body.setWordWrap(True)
        self._body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._body, 0, Qt.AlignTop)

        # NPC 卡片容器（默认隐藏，悬浮 NPC 时显示）
        self._card = QFrame()
        self._card.setObjectName("Card")
        self._card.setVisible(False)
        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        self._avatar = QLabel("?")
        self._avatar.setObjectName("Avatar")
        self._avatar.setFixedSize(48, 48)
        self._avatar.setAlignment(Qt.AlignCenter)
        self._npc_name = QLabel("")
        self._npc_name.setObjectName("CardTitle")
        self._npc_name.setWordWrap(True)
        self._npc_name.setSizePolicy(
            self._npc_name.sizePolicy().horizontalPolicy(),
            self._npc_name.sizePolicy().verticalPolicy(),
        )
        top.addWidget(self._avatar)
        top.addWidget(self._npc_name, 1)
        card_layout.addLayout(top)

        self._npc_motivation = QLabel("")
        self._npc_motivation.setObjectName("InfoBody")
        self._npc_motivation.setWordWrap(True)
        self._npc_secret = QLabel("")
        self._npc_secret.setObjectName("InfoBody")
        self._npc_secret.setWordWrap(True)
        self._npc_line = QLabel("")
        self._npc_line.setObjectName("InfoBody")
        self._npc_line.setWordWrap(True)

        for label, widget in (
            ("动机", self._npc_motivation),
            ("秘密", self._npc_secret),
            ("台词", self._npc_line),
        ):
            row = QHBoxLayout()
            tag = QLabel(label)
            tag.setObjectName("TagCyan")
            tag.setFixedWidth(36)
            tag.setFixedHeight(22)
            widget.setMinimumHeight(0)
            row.addWidget(tag, 0, Qt.AlignTop)
            row.addWidget(widget, 1)
            card_layout.addLayout(row)

        self._edit_btn = QPushButton("编辑 NPC 档案")
        self._edit_btn.setObjectName("PrimaryButton")
        card_layout.addWidget(self._edit_btn)
        layout.addWidget(self._card, 0, Qt.AlignTop)
        self._edit_btn.clicked.connect(
            lambda: self.npc_edit_requested.emit(self._npc_name.text()))

        self.show_welcome()

    def show_welcome(self) -> None:
        """显示默认欢迎提示。"""
        self._title.setText("信息面板")
        self._tag.setText("欢迎")
        self._body.setVisible(True)
        self._card.setVisible(False)
        self._body.setText(
            "这里是信息面板。\n\n"
            "悬停原文中的 NPC 名称，可在此查看完整 NPC 卡片；"
            "NPC 卡片支持编辑档案。"
        )

    def show_npc_card(self, npc: dict | None, dialogue: str = "") -> None:
        """显示 NPC 完整卡片（悬浮联动）。

        :param npc: NPC 档案 dict（来自 npcs 表）
        :param dialogue: 该 NPC 的常用台词（可选，取第一条）
        """
        if not npc:
            return
        self._title.setText("NPC 卡片")
        self._tag.setText("NPC")
        self._tag.setObjectName("TagPurple")
        self._body.setVisible(False)
        self._card.setVisible(True)

        name = npc.get("name", "")
        self._npc_name.setText(name)
        self._avatar.setText((name or "?")[0].upper())
        self._npc_motivation.setText(npc.get("motivation") or "（暂无）")
        self._npc_secret.setText(npc.get("secret") or "（暂无）")
        self._npc_line.setText(dialogue or npc.get("catchphrase") or "（暂无台词）")

    def show_npc_preview(self, name: str, fields: dict) -> None:
        """显示 NPC 卡片预览（简化版：属性列表）。"""
        self._title.setText("NPC 预览")
        self._tag.setText("NPC")
        self._tag.setObjectName("TagPurple")
        self._body.setVisible(True)
        self._card.setVisible(False)

        lines = [f"{k}：{v}" for k, v in fields.items() if v]
        self._body.setText("\n".join(lines) or "暂无信息")
