"""左侧导航栏：模组目录树 + 功能模块入口，支持折叠/展开。

视觉风格：深色 SaaS 仪表盘（参考图）——贴合背景、选中项提亮、
底部"当前焦点"卡片与用户卡片。模组以目录树呈现，可收起避免占满。
"""
from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QButtonGroup, QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
    QSizePolicy, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

NAV_EXPANDED_WIDTH = 232
NAV_COLLAPSED_WIDTH = 56

# (key, 显示名, 单色图标字形)
NAV_MODULE_ITEMS = [
    ("import", "模组导入", "▤"),
    ("translate", "翻译优化", "✎"),
    ("npc", "NPC", "✦"),
]
NAV_SETTINGS_ITEM = ("settings", "设置", "⚙︎")


class NavItem(QPushButton):
    """导航条目按钮：展开显示"图标 名称"，折叠时仅显示图标。"""

    def __init__(self, key: str, label: str, icon: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._key = key
        self._label = label
        self._icon = icon
        self.setCheckable(True)
        self.setObjectName("NavItem")
        self.setCursor(Qt.PointingHandCursor)
        self.set_expanded(True)

    def key(self) -> str:
        """返回条目唯一标识。"""
        return self._key

    def set_expanded(self, expanded: bool) -> None:
        """在展开/折叠两种形态间切换按钮文本。"""
        if expanded:
            self.setText(f"{self._icon}   {self._label}")
            self.setToolTip("")
        else:
            self.setText(self._icon)
            self.setToolTip(self._label)


class NavPanel(QWidget):
    """左侧导航栏面板。"""

    navigate = Signal(str)            # 用户点击功能模块/设置条目时发射其 key
    chapter_selected = Signal(str, str)  # 用户点击章节时发射 (文件名, 章节名)
    module_selected = Signal(str)     # 用户点击模组节点时发射文件名（切换当前模组）
    module_delete_requested = Signal(str)  # 用户请求删除某模组记录

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("NavPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._collapsed = False
        self._items: dict[str, NavItem] = {}
        self._sections: list[QLabel] = []
        self._bottom_cards: list[QFrame] = []
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._current_module = ""
        self._build_ui()
        self.setFixedWidth(NAV_EXPANDED_WIDTH)
        self._init_collapse_animation()

    # ---- 构建 ----
    def _section_label(self, text: str) -> QLabel:
        """创建并登记一个分区标题。"""
        label = QLabel(text)
        label.setObjectName("NavSection")
        self._sections.append(label)
        return label

    def _build_ui(self) -> None:
        """构建导航栏内部布局。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 14, 10, 12)
        root.setSpacing(4)

        # 顶部：Logo 方块 + 标题 + 折叠按钮
        header = QHBoxLayout()
        header.setSpacing(8)
        logo = QLabel("✦")
        logo.setObjectName("StatIconCyan")
        logo.setFixedSize(30, 30)
        self._title = QLabel("GM Copilot")
        self._title.setObjectName("NavTitle")
        self._collapse_btn = QToolButton()
        self._collapse_btn.setObjectName("CollapseButton")
        self._collapse_btn.setText("◀")
        self._collapse_btn.setToolTip("折叠导航栏")
        self._collapse_btn.setCursor(Qt.PointingHandCursor)
        self._collapse_btn.clicked.connect(self.on_collapse_btn_clicked)
        header.addWidget(logo)
        header.addWidget(self._title)
        header.addStretch(1)
        header.addWidget(self._collapse_btn)
        root.addLayout(header)
        root.addSpacing(8)

        # 模组目录树（导入后由 set_modules 填充；每个模组为可收起的一级节点）
        root.addWidget(self._section_label("模组库"))
        self._chapter_empty = QLabel("尚未导入模组\n导入后在此生成模组目录")
        self._chapter_empty.setObjectName("NavEmpty")
        self._chapter_empty.setWordWrap(True)
        root.addWidget(self._chapter_empty)
        self._tree = QTreeWidget()
        self._tree.setObjectName("NavTree")
        self._tree.setHeaderHidden(True)
        self._tree.setIndentation(14)
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.setRootIsDecorated(True)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._tree.setTextElideMode(Qt.ElideRight)
        self._tree.itemClicked.connect(self._on_tree_item_clicked)
        self._tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self._tree.customContextMenuRequested.connect(self._on_tree_context_menu)
        root.addWidget(self._tree)
        root.addSpacing(8)

        # 功能模块入口
        root.addWidget(self._section_label("功能模块"))
        for key, label, icon in NAV_MODULE_ITEMS:
            self._add_item(key, label, icon, root)

        root.addStretch(1)

        # 系统（设置）
        root.addWidget(self._section_label("系统"))
        key, label, icon = NAV_SETTINGS_ITEM
        self._add_item(key, label, icon, root)
        root.addSpacing(8)

        # 底部：当前焦点卡片 + 用户卡片
        self._focus_card = self._make_focus_card()
        self._user_card = self._make_user_card()
        root.addWidget(self._focus_card)
        root.addWidget(self._user_card)

    def _add_item(self, key: str, label: str, icon: str, root: QVBoxLayout) -> None:
        """创建功能模块条目并接入信号。"""
        item = NavItem(key, label, icon)
        item.clicked.connect(lambda _checked=False, k=key: self.navigate.emit(k))
        self._button_group.addButton(item)
        root.addWidget(item)
        self._items[key] = item

    def set_ai_enabled(self, enabled: bool) -> None:
        """切换依赖 API 的功能入口；导入与设置始终保留。"""
        for key in ("translate", "npc"):
            item = self._items.get(key)
            if item is not None:
                item.setEnabled(enabled)
                if not enabled:
                    item.setToolTip("登录账户并配置 API 后可用")

    # ---- 模组目录树 ----
    def set_modules(self, modules: list[dict]) -> None:
        """重建"模组库"目录树。

        :param modules: [{"file": 文件名, "chapters": [章节名, ...]}]
        """
        self._tree.clear()
        if not modules:
            self._chapter_empty.setVisible(not self._collapsed)
            self._tree.setVisible(False)
            return
        self._chapter_empty.setVisible(False)
        self._tree.setVisible(not self._collapsed)
        for module in modules:
            top = QTreeWidgetItem([module["file"]])
            top.setData(0, Qt.UserRole, ("module", module["file"]))
            for chapter in module.get("chapters", []):
                child = QTreeWidgetItem([chapter])
                child.setData(0, Qt.UserRole, ("chapter", module["file"], chapter))
                top.addChild(child)
            self._tree.addTopLevelItem(top)
            top.setExpanded(False)  # 默认收起，避免挤满侧边栏

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        """点击模组节点：切换当前模组；点击章节：展开并发射 chapter_selected。"""
        role = item.data(0, Qt.UserRole)
        if not role:
            return
        if role[0] == "module":
            file_name = role[1]
            item.setExpanded(not item.isExpanded())
            # 切换当前模组（供翻译/NPC 等功能作用于该模组）
            if file_name != self._current_module:
                self.set_current_module(file_name)
                self.module_selected.emit(file_name)
        elif role[0] == "chapter":
            self.chapter_selected.emit(role[1], role[2])

    def set_current_module(self, file_name: str) -> None:
        """高亮当前模组节点（切换模组时由主窗口调用）。"""
        self._current_module = file_name
        for i in range(self._tree.topLevelItemCount()):
            top = self._tree.topLevelItem(i)
            role = top.data(0, Qt.UserRole)
            is_current = bool(role and role[0] == "module" and role[1] == file_name)
            top.setForeground(0, QBrush(QColor("#22D3EE"))
                              if is_current else QBrush(QColor("#E5E7EB")))
            font = top.font(0)
            font.setBold(is_current)
            top.setFont(0, font)

    def _on_tree_context_menu(self, pos: QPoint) -> None:
        """右键模组节点：删除该模组记录。"""
        item = self._tree.itemAt(pos)
        if item is None:
            return
        role = item.data(0, Qt.UserRole)
        if not role or role[0] != "module":
            return
        menu = QMenu(self)
        delete_action = menu.addAction("🗑 删除该模组记录")
        if menu.exec(self._tree.viewport().mapToGlobal(pos)) == delete_action:
            self.module_delete_requested.emit(role[1])

    def _make_focus_card(self) -> QFrame:
        """创建底部"当前焦点"卡片。"""
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        top = QHBoxLayout()
        dot = QLabel()
        dot.setObjectName("DotCyan")
        dot.setFixedSize(8, 8)
        tag = QLabel("当前焦点")
        tag.setObjectName("TagCyan")
        top.addWidget(dot)
        top.addSpacing(6)
        top.addWidget(tag)
        top.addStretch(1)
        title = QLabel("欢迎，开始导入模组吧")
        title.setObjectName("CardTitle")
        title.setWordWrap(True)
        sub = QLabel("导入后这里会显示当前正在进行的章节")
        sub.setObjectName("CardLabel")
        sub.setWordWrap(True)
        layout.addLayout(top)
        layout.addWidget(title)
        layout.addWidget(sub)
        self._bottom_cards.append(card)
        return card

    def _make_user_card(self) -> QFrame:
        """创建底部用户卡片。"""
        card = QFrame()
        card.setObjectName("Card")
        layout = QHBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        avatar = QLabel("GM")
        avatar.setObjectName("Avatar")
        avatar.setFixedSize(28, 28)
        avatar.setAlignment(Qt.AlignCenter)
        info = QVBoxLayout()
        info.setSpacing(2)
        self._user_name = QLabel("本地主持人")
        self._user_name.setObjectName("CardTitle")
        self._user_status = QLabel("本地模式 · 未登录")
        self._user_status.setObjectName("CardLabel")
        info.addWidget(self._user_name)
        info.addWidget(self._user_status)
        status = QLabel()
        status.setObjectName("DotGreen")
        status.setFixedSize(8, 8)
        layout.addWidget(avatar)
        layout.addLayout(info, 1)
        layout.addWidget(status)
        self._bottom_cards.append(card)
        return card

    def set_account_status(self, username: str) -> None:
        """更新底部用户卡片的登录状态。"""
        if username:
            self._user_name.setText(username)
            self._user_status.setText("本地账户 · 已登录")
        else:
            self._user_name.setText("本地快速模式")
            self._user_status.setText("未登录 · API 功能已禁用")

    # ---- 折叠/展开 ----
    def _init_collapse_animation(self) -> None:
        """初始化宽度动画（同时驱动最小/最大宽度）。"""
        self._min_anim = QPropertyAnimation(self, b"minimumWidth", self)
        self._max_anim = QPropertyAnimation(self, b"maximumWidth", self)
        for anim in (self._min_anim, self._max_anim):
            anim.setDuration(220)
            anim.setEasingCurve(QEasingCurve.InOutCubic)
            anim.finished.connect(self._lock_width)

    def _lock_width(self) -> None:
        """动画结束后固定面板宽度。"""
        target = NAV_COLLAPSED_WIDTH if self._collapsed else NAV_EXPANDED_WIDTH
        self.setFixedWidth(target)

    def on_collapse_btn_clicked(self) -> None:
        """折叠按钮点击：切换折叠状态。"""
        self.toggle_collapsed()

    def toggle_collapsed(self) -> None:
        """切换折叠状态。"""
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        """设置折叠状态（带宽度动画与内容形态切换）。"""
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        target = NAV_COLLAPSED_WIDTH if collapsed else NAV_EXPANDED_WIDTH
        current = self.width()
        for anim in (self._min_anim, self._max_anim):
            anim.stop()
            anim.setStartValue(current)
            anim.setEndValue(target)
            anim.start()

        # 内容形态切换
        self._title.setVisible(not collapsed)
        self._collapse_btn.setText("▶" if collapsed else "◀")
        self._collapse_btn.setToolTip("展开导航栏" if collapsed else "折叠导航栏")
        for section in self._sections:
            section.setVisible(not collapsed)
        self._chapter_empty.setVisible(not collapsed)
        self._tree.setVisible(not collapsed)
        for card in self._bottom_cards:
            card.setVisible(not collapsed)
        for item in self._items.values():
            item.set_expanded(not collapsed)

        # 折叠时忽略子控件最小宽度，使面板可收窄
        policy = QSizePolicy.Ignored if collapsed else QSizePolicy.Preferred
        for index in range(self.layout().count()):
            widget = self.layout().itemAt(index).widget()
            if widget is not None:
                widget.setSizePolicy(policy, widget.sizePolicy().verticalPolicy())
        margins = (6, 10, 6, 10) if collapsed else (10, 14, 10, 12)
        self.layout().setContentsMargins(*margins)

    def is_collapsed(self) -> bool:
        """是否处于折叠状态。"""
        return self._collapsed

    # ---- 高亮 ----
    def set_active(self, key: str) -> None:
        """高亮指定导航条目；key 不存在时取消全部高亮。"""
        if key in self._items:
            self._items[key].setChecked(True)
        else:
            for item in self._items.values():
                item.setChecked(False)
