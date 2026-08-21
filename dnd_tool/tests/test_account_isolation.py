"""离线回归：本地账户认证、隔离数据与注销清理。"""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from src.modules import account_manager as account_module
from src.modules.account_manager import AccountManager


class Paths:
    """在临时目录中替换账户持久化路径。"""

    def __init__(self, root: Path):
        self.root = root
        self.accounts = root / "accounts"
        self.users = root / "users.json"

    def __enter__(self):
        self._patches = [
            patch.object(account_module, "ACCOUNT_ROOT", self.accounts),
            patch.object(account_module, "USER_STORE_PATH", self.users),
        ]
        for item in self._patches:
            item.start()
        return self

    def __exit__(self, *_args):
        for item in reversed(self._patches):
            item.stop()


def main():
    """验证注册、登录、访客隔离和注销删除。"""
    with tempfile.TemporaryDirectory() as temp_dir, Paths(Path(temp_dir)) as paths:
        manager = AccountManager()
        assert manager.register("alice", "password123") == "alice"
        assert manager.is_logged_in
        manager.settings_path.write_text('{"providers": [{"api_key": "secret"}]}', encoding="utf-8")
        manager.database_path.write_bytes(b"module-data")
        manager.outputs_dir.mkdir(parents=True)
        (manager.outputs_dir / "module.md").write_text("export", encoding="utf-8")

        guest = AccountManager()
        guest.start_guest_session()
        assert guest.is_guest and not guest.is_logged_in
        assert guest.settings_path != manager.settings_path
        assert not guest.settings_path.exists()
        guest.settings_path.write_text("{}", encoding="utf-8")
        guest.end_session()
        assert not guest.account_dir.exists() if guest.is_guest else True

        second = AccountManager()
        assert second.login("alice", "password123") == "alice"
        assert json.loads(second.settings_path.read_text(encoding="utf-8"))["providers"]
        account_dir = second.account_dir
        second.delete_current_account()
        assert not account_dir.exists()
        users = json.loads(paths.users.read_text(encoding="utf-8"))
        assert "alice" not in users["users"]
    print("ACCOUNT_ISOLATION_OK")


if __name__ == "__main__":
    main()
