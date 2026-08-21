"""离屏回归：NPC 编辑对话框在暗色主题下保持可读。"""
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPlainTextEdit

from src.pages.npc_page import _NpcEditDialog


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main():
    """加载暗色主题后断言多行编辑框为深底浅字。"""
    app = QApplication.instance() or QApplication([])
    qss = (PROJECT_ROOT / "src" / "theme" / "dark.qss").read_text(encoding="utf-8")
    app.setStyleSheet(qss)
    dialog = _NpcEditDialog({
        "name": "测试 NPC",
        "motivation": "这是一段用于测试暗色主题的 NPC 动机描述。" * 4,
    })
    dialog.resize(620, 420)
    dialog.show()
    app.processEvents()
    editor = next(widget for widget in dialog.findChildren(QPlainTextEdit))
    image = editor.grab().toImage()
    pixel = image.pixelColor(5, 5).name().lower()
    assert pixel == "#2a2d36", pixel
    print("NPC_EDIT_DARK_THEME_OK")


if __name__ == "__main__":
    main()
