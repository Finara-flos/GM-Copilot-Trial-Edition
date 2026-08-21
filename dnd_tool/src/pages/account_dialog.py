"""本地账户注册与登录对话框。"""
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from src.modules.account_manager import AccountError, AccountManager


class AccountDialog(QDialog):
    """启动时要求注册或登录本地账户。"""

    def __init__(self, manager: AccountManager, parent=None):
        super().__init__(parent)
        self._manager = manager
        self.setWindowTitle("GM Copilot 本地账户")
        self.setModal(True)
        self.setMinimumWidth(400)
        self._build_ui()

    def _build_ui(self) -> None:
        """构建登录和注册表单。"""
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)
        title = QLabel("GM Copilot")
        title.setObjectName("PageTitle")
        subtitle = QLabel("登录本地账户以访问你的模组、API 配置和导出记录。")
        subtitle.setObjectName("PageSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        self._stack = QStackedWidget(self)
        self._stack.addWidget(self._build_login_page())
        self._stack.addWidget(self._build_register_page())
        guest = QPushButton("仅使用本地快速解析")
        guest.clicked.connect(self._on_guest)
        root.addWidget(guest)
        root.addWidget(self._stack)

    def _build_login_page(self) -> QWidget:
        """构建登录表单。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self._login_name = QLineEdit()
        self._login_name.setPlaceholderText("用户名")
        self._login_password = QLineEdit()
        self._login_password.setPlaceholderText("密码")
        self._login_password.setEchoMode(QLineEdit.Password)
        form.addRow("用户名：", self._login_name)
        form.addRow("密码：", self._login_password)
        layout.addLayout(form)
        row = QHBoxLayout()
        register = QPushButton("注册账户")
        login = QPushButton("登录")
        login.setObjectName("PrimaryButton")
        row.addWidget(register)
        row.addStretch(1)
        row.addWidget(login)
        layout.addLayout(row)
        register.clicked.connect(lambda: self._stack.setCurrentIndex(1))
        login.clicked.connect(self._on_login)
        self._login_password.returnPressed.connect(self._on_login)
        return page

    def _build_register_page(self) -> QWidget:
        """构建注册表单。"""
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self._register_name = QLineEdit()
        self._register_name.setPlaceholderText("3-32 位英文、数字、下划线或连字符")
        self._register_password = QLineEdit()
        self._register_password.setPlaceholderText("至少 8 位密码")
        self._register_password.setEchoMode(QLineEdit.Password)
        self._register_confirm = QLineEdit()
        self._register_confirm.setPlaceholderText("再次输入密码")
        self._register_confirm.setEchoMode(QLineEdit.Password)
        form.addRow("用户名：", self._register_name)
        form.addRow("密码：", self._register_password)
        form.addRow("确认密码：", self._register_confirm)
        layout.addLayout(form)
        row = QHBoxLayout()
        back = QPushButton("返回登录")
        submit = QPushButton("创建账户")
        submit.setObjectName("PrimaryButton")
        row.addWidget(back)
        row.addStretch(1)
        row.addWidget(submit)
        layout.addLayout(row)
        back.clicked.connect(lambda: self._stack.setCurrentIndex(0))
        submit.clicked.connect(self._on_register)
        self._register_confirm.returnPressed.connect(self._on_register)
        return page

    def _on_guest(self) -> None:
        """进入不保存 API 数据的本地快速解析模式。"""
        self._manager.start_guest_session()
        self.accept()

    def _on_login(self) -> None:
        """验证登录凭据。"""
        try:
            self._manager.login(self._login_name.text(), self._login_password.text())
        except AccountError as exc:
            QMessageBox.warning(self, "登录失败", str(exc))
            return
        self.accept()

    def _on_register(self) -> None:
        """验证注册字段并创建账户。"""
        if self._register_password.text() != self._register_confirm.text():
            QMessageBox.warning(self, "注册失败", "两次输入的密码不一致。")
            return
        try:
            self._manager.register(self._register_name.text(), self._register_password.text())
        except AccountError as exc:
            QMessageBox.warning(self, "注册失败", str(exc))
            return
        self.accept()
