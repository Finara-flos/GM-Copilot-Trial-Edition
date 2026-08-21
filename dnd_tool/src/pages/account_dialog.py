"""本地账户注册与登录对话框（无边框 + 卡片悬浮 + 内嵌标题栏）。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainterPath, QRegion
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from src.modules.account_manager import AccountError, AccountManager

_CARD_RADIUS = 14
_SHADOW_MARGIN = (20, 20, 20, 28)  # 左 上 右 下，给阴影留出空间
_WIN_BTN_W = 36  # 窗口控制按钮宽高


class _TitleBarButton(QPushButton):
    """标题栏方形按钮：默认半透明，hover 高对比度。"""

    def __init__(self, text: str, tooltip: str, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)
        self.setFixedSize(_WIN_BTN_W, _WIN_BTN_W)
        self.setFocusPolicy(Qt.NoFocus)


class _TitleBar(QFrame):
    """内嵌标题栏：承载居中标题与右上角窗口按钮，整行可拖动对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_pos = None

    def mousePressEvent(self, event):  # noqa: N802  Qt 命名约定
        """开始拖动对话框。"""
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.window().pos()
            event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802  Qt 命名约定
        """拖动中：移动对话框窗口。"""
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.window().move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):  # noqa: N802  Qt 命名约定
        self._drag_pos = None


class AccountDialog(QDialog):
    """启动时要求注册或登录本地账户；无边框卡片悬浮样式。

    - 无系统边框（FramelessWindowHint + 透明背景）
    - 内部为一张 QFrame#LoginCard，带圆角与阴影
    - 顶部 _TitleBar 承载居中标题与最小化/关闭按钮，可拖动窗口
    - 副标题位于密码栏与操作按钮之间，小号字体单行居中
    """

    def __init__(self, manager: AccountManager, parent=None):
        super().__init__(parent)
        self._manager = manager
        self.setWindowTitle("GM Copilot · 账户")
        self.setModal(True)
        # 无边框 + 透明背景，让卡片成为纯粹的功能矩形
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # 对话框尺寸 = 卡片尺寸 + 阴影边距；高度由内容自然撑开
        self.resize(480, 340)
        self.setMinimumSize(440, 320)
        self._build_ui()
        self._apply_local_style()
        self.adjustSize()  # 让高度跟随内容收缩，避免无谓的空白

    def _build_ui(self) -> None:
        """构建卡片 + 顶部背景图条带 + 表单。"""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*_SHADOW_MARGIN)
        outer.setSpacing(0)

        # 卡片：占据对话框内容区，圆角 + 阴影
        self._card = QFrame(self)
        self._card.setObjectName("LoginCard")
        self._card.setAttribute(Qt.WA_StyledBackground, True)
        outer.addWidget(self._card)

        # 卡片阴影：在透明边距区域显示
        shadow = QGraphicsDropShadowEffect(self._card)
        shadow.setBlurRadius(32)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 12)
        self._card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(self._card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        # 内嵌标题栏：居中标题 + 右上角窗口按钮，整行可拖动
        self._title_bar = _TitleBar(self._card)
        bar = QHBoxLayout(self._title_bar)
        bar.setContentsMargins(20, 10, 12, 6)
        bar.setSpacing(8)
        # 左侧占位 = 右侧按钮总宽，使标题严格居中于卡片
        left_balance = QWidget(self._title_bar)
        left_balance.setFixedWidth(_WIN_BTN_W * 2 + 8)
        bar.addWidget(left_balance)
        bar.addStretch(1)
        title = QLabel("GM Copilot")
        title.setObjectName("PageTitle")
        bar.addWidget(title)
        bar.addStretch(1)
        self._min_btn = _TitleBarButton("\u2014", "最小化", self._title_bar)
        self._min_btn.setObjectName("LoginWindowBtn")
        self._close_btn = _TitleBarButton("\u2715", "关闭", self._title_bar)
        self._close_btn.setObjectName("LoginCloseBtn")
        bar.addWidget(self._min_btn)
        bar.addWidget(self._close_btn)
        card_layout.addWidget(self._title_bar)

        # 表单区
        form_container = QWidget(self._card)
        form_layout = QVBoxLayout(form_container)
        form_layout.setContentsMargins(28, 14, 28, 18)
        form_layout.setSpacing(12)

        # 内联错误提示
        self._error_label = QLabel("")
        self._error_label.setObjectName("LoginError")
        self._error_label.setWordWrap(True)
        self._error_label.setAlignment(Qt.AlignCenter)
        self._error_label.setVisible(False)
        form_layout.addWidget(self._error_label)

        # 登录 / 注册表单切换
        self._stack = QStackedWidget(form_container)
        self._stack.addWidget(self._build_login_page())
        self._stack.addWidget(self._build_register_page())
        form_layout.addWidget(self._stack, 1)

        # 底部：游客入口
        guest = QPushButton("仅使用本地快速解析 \u2192")
        guest.setObjectName("LoginGuestBtn")
        guest.setCursor(Qt.PointingHandCursor)
        guest.clicked.connect(self._on_guest)
        form_layout.addWidget(guest, 0, Qt.AlignCenter)

        card_layout.addWidget(form_container, 1)

        # 窗口控制按钮：最小化 / 关闭
        self._min_btn.clicked.connect(self.showMinimized)
        self._close_btn.clicked.connect(self.reject)

    def _apply_local_style(self) -> None:
        """局部 QSS：仅作用于登录对话框，避免污染主窗口样式。"""
        self._card.setStyleSheet(f"""
            QFrame#LoginCard {{
                background-color: #1C1F26;
                border-radius: {_CARD_RADIUS}px;
            }}
            QLabel#LoginSubtitle {{
                color: #8A8F99;
                font-size: 12px;
                padding: 0 4px;
                letter-spacing: 0.5px;
            }}
            QLabel#LoginError {{
                color: #F87171;
                font-size: 12px;
                padding: 6px 8px;
                background: rgba(248, 113, 113, 0.10);
                border-radius: 6px;
                border: 1px solid rgba(248, 113, 113, 0.25);
            }}
            QPushButton#LoginGuestBtn {{
                background: transparent;
                color: #8A8F99;
                border: none;
                font-size: 12px;
                padding: 4px;
            }}
            QPushButton#LoginGuestBtn:hover {{
                color: #22D3EE;
            }}
            /* 标题栏按钮：默认半透明，hover 时高对比度 */
            QPushButton#LoginWindowBtn, QPushButton#LoginCloseBtn {{
                background: transparent;
                border: none;
                border-radius: 8px;
                color: rgba(255, 255, 255, 0.65);
                font-size: 14px;
                font-weight: 600;
                padding: 0;
            }}
            QPushButton#LoginWindowBtn:hover {{
                background: rgba(255, 255, 255, 0.18);
                color: #FFFFFF;
            }}
            QPushButton#LoginWindowBtn:pressed {{
                background: rgba(255, 255, 255, 0.28);
            }}
            QPushButton#LoginCloseBtn:hover {{
                background: #EF4444;
                color: #FFFFFF;
            }}
            QPushButton#LoginCloseBtn:pressed {{
                background: #DC2626;
            }}
        """)

    def resizeEvent(self, event):  # noqa: N802  Qt 命名约定
        """圆角 mask：裁剪卡片外区域，让阴影能正确显示。"""
        super().resizeEvent(event)
        path = QPainterPath()
        path.addRoundedRect(
            self._card.rect().adjusted(0, 0, -1, -1),
            _CARD_RADIUS, _CARD_RADIUS,
        )
        # mask 让标题栏顶部超出圆角的部分被裁剪
        self._card.setMask(QRegion(path.toFillPolygon().toPolygon()))

    # ---- 错误提示 ----
    def _show_error(self, msg: str) -> None:
        self._error_label.setText(msg)
        self._error_label.setVisible(True)

    def _clear_error(self) -> None:
        self._error_label.setVisible(False)
        self._error_label.setText("")

    def _make_subtitle(self) -> QLabel:
        """副标题：小号字体、单行居中，放于输入栏与操作按钮之间。"""
        label = QLabel("登录本地账户以访问你的模组、API 配置和导出记录。")
        label.setObjectName("LoginSubtitle")
        label.setAlignment(Qt.AlignCenter)
        label.setWordWrap(False)
        return label

    # ---- 登录页 ----
    def _build_login_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        form = QFormLayout()
        form.setSpacing(10)
        self._login_name = QLineEdit()
        self._login_name.setPlaceholderText("用户名")
        self._login_password = QLineEdit()
        self._login_password.setPlaceholderText("密码")
        self._login_password.setEchoMode(QLineEdit.Password)
        form.addRow("用户名", self._login_name)
        form.addRow("密码", self._login_password)
        layout.addLayout(form)

        # 副标题：密码栏与操作按钮之间
        self._login_subtitle = self._make_subtitle()
        layout.addWidget(self._login_subtitle)

        row = QHBoxLayout()
        row.setSpacing(8)
        register = QPushButton("注册账户")
        login = QPushButton("登录")
        login.setObjectName("PrimaryButton")
        row.addWidget(register)
        row.addStretch(1)
        row.addWidget(login)
        layout.addLayout(row)

        register.clicked.connect(lambda: self._switch_to(1))
        login.clicked.connect(self._on_login)
        self._login_password.returnPressed.connect(self._on_login)
        self._login_name.textChanged.connect(self._clear_error)
        self._login_password.textChanged.connect(self._clear_error)
        return page

    # ---- 注册页 ----
    def _build_register_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setSpacing(10)
        form = QFormLayout()
        form.setSpacing(10)
        self._register_name = QLineEdit()
        self._register_name.setPlaceholderText("3-32 位英文、数字、下划线或连字符")
        self._register_password = QLineEdit()
        self._register_password.setPlaceholderText("至少 8 位密码")
        self._register_password.setEchoMode(QLineEdit.Password)
        self._register_confirm = QLineEdit()
        self._register_confirm.setPlaceholderText("再次输入密码")
        self._register_confirm.setEchoMode(QLineEdit.Password)
        form.addRow("用户名", self._register_name)
        form.addRow("密码", self._register_password)
        form.addRow("确认", self._register_confirm)
        layout.addLayout(form)

        # 副标题：确认密码栏与操作按钮之间
        self._register_subtitle = self._make_subtitle()
        layout.addWidget(self._register_subtitle)

        row = QHBoxLayout()
        row.setSpacing(8)
        back = QPushButton("返回登录")
        submit = QPushButton("创建账户")
        submit.setObjectName("PrimaryButton")
        row.addWidget(back)
        row.addStretch(1)
        row.addWidget(submit)
        layout.addLayout(row)

        back.clicked.connect(lambda: self._switch_to(0))
        submit.clicked.connect(self._on_register)
        self._register_confirm.returnPressed.connect(self._on_register)
        self._register_name.textChanged.connect(self._clear_error)
        self._register_password.textChanged.connect(self._clear_error)
        return page

    def _switch_to(self, index: int) -> None:
        self._clear_error()
        self._stack.setCurrentIndex(index)

    def _on_guest(self) -> None:
        self._manager.start_guest_session()
        self.accept()

    def _on_login(self) -> None:
        try:
            self._manager.login(self._login_name.text(), self._login_password.text())
        except AccountError as exc:
            self._show_error(str(exc))
            return
        self.accept()

    def _on_register(self) -> None:
        if self._register_password.text() != self._register_confirm.text():
            self._show_error("两次输入的密码不一致。")
            return
        try:
            self._manager.register(
                self._register_name.text(), self._register_password.text())
        except AccountError as exc:
            self._show_error(str(exc))
            return
        self.accept()
