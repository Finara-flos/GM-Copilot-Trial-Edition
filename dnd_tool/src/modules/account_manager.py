"""本地账户认证与账户级存储路径管理。"""
import base64
import hashlib
import json
import re
import secrets
import shutil
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ACCOUNT_ROOT = PROJECT_ROOT / "data" / "accounts"
USER_STORE_PATH = PROJECT_ROOT / "config" / "users.json"
_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")


class AccountError(ValueError):
    """注册、登录或账户删除失败。"""


def _load_users() -> dict:
    """读取本地账户清单。"""
    try:
        data = json.loads(USER_STORE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"users": {}}
    except (OSError, json.JSONDecodeError):
        return {"users": {}}


def _save_users(data: dict) -> None:
    """安全写入本地账户清单。"""
    USER_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    USER_STORE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _hash_password(password: str, salt: bytes) -> str:
    """使用 PBKDF2-HMAC-SHA256 生成不可逆密码哈希。"""
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240_000)
    return base64.b64encode(derived).decode("ascii")


class AccountManager:
    """管理本地注册账户和当前账户的专属目录。"""

    def __init__(self):
        self.username = ""
        self._guest_dir: Path | None = None

    @staticmethod
    def validate_username(username: str) -> str:
        """验证并规范化用户名。"""
        value = username.strip()
        if not _NAME_RE.fullmatch(value):
            raise AccountError("用户名需为 3-32 位英文、数字、下划线或连字符。")
        return value

    @staticmethod
    def validate_password(password: str) -> None:
        """验证密码基本强度。"""
        if len(password) < 8:
            raise AccountError("密码至少需要 8 位。")

    def register(self, username: str, password: str) -> str:
        """注册账户并创建其隔离数据目录。"""
        username = self.validate_username(username)
        self.validate_password(password)
        data = _load_users()
        users = data.setdefault("users", {})
        if username in users:
            raise AccountError("该用户名已注册。")
        salt = secrets.token_bytes(16)
        users[username] = {
            "salt": base64.b64encode(salt).decode("ascii"),
            "password_hash": _hash_password(password, salt),
        }
        _save_users(data)
        self.login(username, password)
        return username

    def login(self, username: str, password: str) -> str:
        """验证密码并激活账户。"""
        username = self.validate_username(username)
        user = _load_users().get("users", {}).get(username)
        if not user:
            raise AccountError("用户名或密码错误。")
        try:
            salt = base64.b64decode(user["salt"])
            expected = user["password_hash"]
        except (KeyError, ValueError):
            raise AccountError("账户记录损坏。") from None
        actual = _hash_password(password, salt)
        if not secrets.compare_digest(actual, expected):
            raise AccountError("用户名或密码错误。")
        self.username = username
        self.account_dir.mkdir(parents=True, exist_ok=True)
        return username

    def start_guest_session(self) -> None:
        """启动只支持快速本地解析的临时访客会话。"""
        self.username = ""
        self._guest_dir = Path(tempfile.mkdtemp(prefix="gm_copilot_guest_"))

    @property
    def is_guest(self) -> bool:
        """当前是否为未登录的临时本地模式。"""
        return self._guest_dir is not None and not self.username

    @property
    def is_logged_in(self) -> bool:
        """当前是否已激活账户。"""
        return bool(self.username)

    @property
    def account_dir(self) -> Path:
        """返回当前账户或临时访客会话的数据目录。"""
        if self.username:
            return ACCOUNT_ROOT / self.username
        if self._guest_dir is not None:
            return self._guest_dir
        raise AccountError("尚未登录账户。")

    @property
    def settings_path(self) -> Path:
        """当前账户 API 与界面配置路径。"""
        return self.account_dir / "settings.json"

    @property
    def database_path(self) -> Path:
        """当前账户模组数据库路径。"""
        return self.account_dir / "gm_copilot.db"

    @property
    def outputs_dir(self) -> Path:
        """当前账户导出目录。"""
        return self.account_dir / "outputs"

    def end_session(self) -> None:
        """结束访客会话并删除其临时本地解析数据。"""
        if self._guest_dir is not None:
            shutil.rmtree(self._guest_dir, ignore_errors=True)
            self._guest_dir = None

    def delete_current_account(self) -> None:
        """删除当前账户凭据及全部专属数据。"""
        if not self.username:
            return
        username = self.username
        data = _load_users()
        data.get("users", {}).pop(username, None)
        _save_users(data)
        shutil.rmtree(ACCOUNT_ROOT / username, ignore_errors=True)
        self.username = ""
