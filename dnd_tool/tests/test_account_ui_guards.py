"""离屏烟测：登录界面与未登录 API 限制。"""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.modules import account_manager as account_module
from src.modules.account_manager import AccountManager
from src.pages.account_dialog import AccountDialog
from src.pages.settings_page import SettingsManager, SettingsPage


def main():
    """验证访客状态禁用 API 配置而注册账户可初始化专属设置。"""
    app = QApplication.instance() or QApplication([])
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        with patch.object(account_module, "ACCOUNT_ROOT", root / "accounts"), patch.object(
            account_module, "USER_STORE_PATH", root / "users.json"
        ):
            guest = AccountManager()
            dialog = AccountDialog(guest)
            dialog._on_guest()
            assert guest.is_guest
            SettingsManager.configure_path(guest.settings_path)
            guest_page = SettingsPage(SettingsManager(), api_enabled=False)
            assert not guest_page._provider_combo.isEnabled()
            assert not guest_page._test_btn.isEnabled()

            account = AccountManager()
            account.register("alice", "password123")
            SettingsManager.configure_path(account.settings_path)
            account_page = SettingsPage(SettingsManager(), api_enabled=True)
            assert account_page._provider_combo.isEnabled()
    print("ACCOUNT_UI_GUARD_OK")


if __name__ == "__main__":
    main()
