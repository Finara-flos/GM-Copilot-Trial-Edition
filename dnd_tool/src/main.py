"""GM Copilot 应用入口。

用法：
    cd dnd_tool
    python src/main.py
"""
import sys
from pathlib import Path

# 将项目根目录加入 sys.path，保证“python src/main.py”与“python -m src.main”均可运行
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src.modules.account_manager import AccountManager  # noqa: E402
from src.pages.account_dialog import AccountDialog  # noqa: E402
from src.widgets.markdown_view import set_active_scheme  # noqa: E402
from src.window import MainWindow  # noqa: E402

# UI 字体候选（按优先级降序）
UI_FONTS = ["Noto Sans SC", "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", "Segoe UI"]


def _pick_font(preferred: list[str]) -> str:
    """返回第一个系统可用的字体族名；全部不可用时返回空字符串。"""
    available = set(QFontDatabase.families())
    for name in preferred:
        if name in available:
            return name
    return ""


def _apply_fonts(app: QApplication) -> None:
    """设置全局 UI 字体；正文衬线字体由 QSS 中的 font-family 决定。"""
    ui = _pick_font(UI_FONTS)
    if ui:
        app.setFont(QFont(ui, 10))


def _load_initial_theme(app: QApplication) -> None:
    """启动时加载暗色 QSS，让登录对话框就拥有与主窗口一致的样式。

    登录成功后 MainWindow._apply_theme 会根据用户偏好重新加载，覆盖此处。
    """
    qss_path = PROJECT_ROOT / "src" / "theme" / "dark.qss"
    try:
        qss = qss_path.read_text(encoding="utf-8")
    except OSError:
        qss = ""
    app.setStyleSheet(qss)
    set_active_scheme("dark")


def main() -> int:
    """启动 GM Copilot 应用。"""
    app = QApplication(sys.argv)
    app.setApplicationName("GM Copilot")
    app.setOrganizationName("GM Copilot")
    _apply_fonts(app)
    _load_initial_theme(app)
    account = AccountManager()
    dialog = AccountDialog(account)
    if dialog.exec() != AccountDialog.Accepted:
        return 0
    window = MainWindow(account)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
