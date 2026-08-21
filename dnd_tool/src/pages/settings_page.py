"""设置页：API 配置、主题切换、背景图管理；所有修改自动保存。"""
import json
import shutil
from datetime import datetime
from pathlib import Path

from cryptography.fernet import Fernet
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QRadioButton, QScrollArea, QSlider, QSpinBox, QVBoxLayout,
    QWidget,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_BACKGROUND_PATH = "assets/backgrounds/bg_dusk_peak.png"
DEFAULT_MODELS = [
    "agnes-2.5-flash",
    "agnes-2.5-pro",
    "agnes-2.0-flash",
    "deepseek-chat",
    "deepseek-reasoner",
    "gpt-4o-mini",
    "claude-3-5-sonnet-20241022",
]


class SettingsManager:
    """读写 config/settings.json；API Key 使用 Fernet 加密后落盘。"""

    CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"
    KEY_PATH = PROJECT_ROOT / "config" / ".fernet_key"

    @classmethod
    def configure_path(cls, path: str | Path) -> None:
        """将后续设置读写切换到当前登录账户的专属配置文件。"""
        cls.CONFIG_PATH = Path(path)

    def __init__(self):
        self._data: dict = self._load()
        self._fernet = self._load_fernet()

    @classmethod
    def _load(cls) -> dict:
        """读取配置文件；不存在或损坏时返回空字典。"""
        try:
            with open(cls.CONFIG_PATH, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _load_fernet(self) -> Fernet:
        """加载或生成 Fernet 密钥（存于 config/.fernet_key）。"""
        if self.KEY_PATH.exists():
            key = self.KEY_PATH.read_bytes()
        else:
            key = Fernet.generate_key()
            self.KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
            self.KEY_PATH.write_bytes(key)
        return Fernet(key)

    # ---- 通用读写 ----
    def get(self, key: str, default=None):
        """读取配置项。"""
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        """写入配置项并立即保存到磁盘。"""
        self._data[key] = value
        self.save()

    def save(self) -> None:
        """将当前配置写入 config/settings.json。"""
        self.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(self.CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(self._data, fh, ensure_ascii=False, indent=2)

    # ---- API Key 加密 ----
    def encrypt_api_key(self, plain: str) -> str:
        """加密 API Key，返回可安全存储的密文。"""
        return self._fernet.encrypt(plain.encode("utf-8")).decode("ascii")

    def decrypt_api_key(self, token: str) -> str:
        """解密 API Key 密文。"""
        return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")

    # ---- 多提供方 ----
    def get_providers(self) -> list[dict]:
        """返回提供方列表；旧版 api 单配置自动迁移为默认提供方。"""
        providers = self.get("providers", [])
        if isinstance(providers, list) and providers:
            return providers
        # 迁移旧版 "api" 配置为单个默认提供方
        api = self.get("api", {}) or {}
        if isinstance(api, dict) and (api.get("base_url") or api.get("api_key")):
            provider = {
                "id": "default",
                "name": "默认",
                "base_url": api.get("base_url", ""),
                "api_key": api.get("api_key", ""),
                "models": [api.get("model", "")] if api.get("model") else [],
            }
            providers = [provider]
            self.set("providers", providers)
            if not self.get("active_provider"):
                self.set("active_provider", "default")
        return providers

    def get_active_provider(self) -> dict:
        """当前激活的提供方（含解密后的 key）。"""
        providers = self.get_providers()
        active_id = self.get("active_provider", "")
        provider = next((p for p in providers if p.get("id") == active_id), None)
        if provider is None:
            provider = providers[0] if providers else {}
        if not provider:
            return {}
        result = dict(provider)
        key = result.get("api_key", "")
        if key:
            try:
                result["api_key_plain"] = self.decrypt_api_key(key)
            except Exception:  # noqa: BLE001  密文损坏视为未配置
                result["api_key_plain"] = ""
        return result

    def save_providers(self, providers: list[dict]) -> None:
        """保存提供方列表（api_key 字段为密文或空）。"""
        self.set("providers", providers)
        active_id = self.get("active_provider", "")
        if not any(p.get("id") == active_id for p in providers):
            if providers:
                self.set("active_provider", providers[0]["id"])
            else:
                self.set("active_provider", "")
        self.save()

    def set_active_provider(self, provider_id: str) -> None:
        """设置当前激活的提供方。"""
        self.set("active_provider", provider_id)


class _TestThread(QThread):
    """后台测试 API 连接。"""

    result_ready = Signal(str)
    failed = Signal(str)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self._client = client

    def run(self) -> None:
        try:
            from src.modules.llm_client import LLMError
            reply = self._client.ping()
            self.result_ready.emit(reply)
        except LLMError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001  测试失败统一上报
            self.failed.emit(str(exc))


class _CatalogThread(QThread):
    """后台读取提供方模型目录。"""

    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, client, parent=None):
        super().__init__(parent)
        self._client = client

    def run(self) -> None:
        try:
            self.result_ready.emit(self._client.list_models())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class SettingsPage(QWidget):
    """设置页面：API 配置 / 主题切换 / 背景图管理，改动即自动保存。"""

    theme_changed = Signal(str)          # 主题名：dark / light
    background_changed = Signal(object)  # {"path", "mode", "opacity"}
    logout_requested = Signal()

    def __init__(self, settings: SettingsManager, parent: QWidget | None = None,
                 api_enabled: bool = True):
        super().__init__(parent)
        self._settings = settings
        self._api_enabled = api_enabled
        self._loading = True
        self._key_dirty = False
        self._bg_path: Path | None = None
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._build_ui()
        self._load_values()
        self._loading = False

    # ---- 构建 ----
    def _build_ui(self) -> None:
        """构建设置页界面。"""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)

        container = QWidget()
        container.setObjectName("PageRoot")
        scroll.setWidget(container)
        root = QVBoxLayout(container)
        root.setContentsMargins(32, 28, 32, 28)
        root.setSpacing(16)

        page_title = QLabel("设置")
        page_title.setObjectName("PageTitle")
        root.addWidget(page_title)
        root.addWidget(self._build_api_group())
        root.addWidget(self._build_theme_group())
        root.addWidget(self._build_advance_group())
        root.addWidget(self._build_background_group())
        root.addWidget(self._build_account_group())
        root.addStretch(1)

    def _build_account_group(self) -> QGroupBox:
        """构建本地账户注销操作。"""
        group = QGroupBox("本地账户")
        layout = QHBoxLayout(group)
        hint = QLabel("注销会永久清除该账户的 API 配置、模组、词库、NPC 与导出文件。")
        hint.setObjectName("KeyHint")
        hint.setWordWrap(True)
        self._logout_btn = QPushButton("注销并清除数据")
        self._logout_btn.setVisible(self._api_enabled)
        layout.addWidget(hint, 1)
        layout.addWidget(self._logout_btn)
        self._logout_btn.clicked.connect(self._on_logout)
        return group

    def _on_logout(self) -> None:
        """请求主窗口确认并清除当前本地账户。"""
        reply = QMessageBox.warning(
            self, "注销并清除数据",
            "这会永久删除当前账户的 API 配置、已导入模组、词库、NPC、任务记录和导出文件。\n\n确定继续吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.logout_requested.emit()

    def _build_api_group(self) -> QGroupBox:
        """API 配置分组：多提供方管理（类似 DSH 的自定义提供方）。"""
        group = QGroupBox("API 提供方")
        outer = QVBoxLayout(group)
        outer.setSpacing(8)

        # 提供方选择行：下拉 + 添加 + 删除
        sel_row = QHBoxLayout()
        sel_row.setSpacing(8)
        sel_row.addWidget(QLabel("当前提供方："))
        self._provider_combo = QComboBox()
        self._provider_combo.setMinimumWidth(200)
        sel_row.addWidget(self._provider_combo)
        self._add_provider_btn = QPushButton("＋ 添加提供方")
        self._del_provider_btn = QPushButton("删除")
        sel_row.addWidget(self._add_provider_btn)
        sel_row.addWidget(self._del_provider_btn)
        sel_row.addStretch(1)
        outer.addLayout(sel_row)

        # 提供方编辑表单
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self._provider_name = QLineEdit()
        self._provider_name.setPlaceholderText("提供方名称，如：我的网关")
        self._base_url = QLineEdit()
        self._base_url.setPlaceholderText("https://api.example.com/v1")
        self._protocol_combo = QComboBox()
        from src.modules.api_protocols import PROTOCOL_OPTIONS
        for label, value in PROTOCOL_OPTIONS:
            self._protocol_combo.addItem(label, value)
        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.Password)
        self._api_key.setPlaceholderText("sk-...（保存时加密存储）")
        self._model_combo = QComboBox()
        self._model_combo.setEditable(True)
        self._model_combo.addItems(DEFAULT_MODELS)
        self._catalog_btn = QPushButton("读取模型目录")
        self._catalog_btn.setToolTip("按当前协议从中转站读取可用模型")
        self._catalog_status = QLabel("可手动填写模型 ID，或从中转站读取目录")
        self._catalog_status.setObjectName("KeyHint")
        model_row = QHBoxLayout()
        model_row.setSpacing(8)
        model_row.addWidget(self._model_combo, 1)
        model_row.addWidget(self._catalog_btn)
        self._key_hint = QLabel("")
        self._key_hint.setObjectName("KeyHint")
        self._test_btn = QPushButton("测试连接")
        self._test_btn.setToolTip("向当前提供方发送一次极简请求，验证 API 可用性")

        form.addRow("名称：", self._provider_name)
        form.addRow("API 协议：", self._protocol_combo)
        form.addRow("Base URL：", self._base_url)
        form.addRow("API Key：", self._api_key)
        form.addRow("模型：", model_row)
        form.addRow("", self._catalog_status)
        form.addRow("", self._key_hint)
        form.addRow("", self._test_btn)
        outer.addLayout(form)

        self._provider_combo.currentIndexChanged.connect(
            self._on_provider_switched)
        self._provider_name.textEdited.connect(self._on_provider_name_edited)
        self._protocol_combo.currentIndexChanged.connect(self._on_protocol_changed)
        self._base_url.textEdited.connect(self._on_base_url_edited)
        self._api_key.textEdited.connect(self._on_api_key_edited)
        self._model_combo.currentTextChanged.connect(self._on_model_combo_changed)
        self._catalog_btn.clicked.connect(self._on_fetch_models)
        self._add_provider_btn.clicked.connect(self._on_add_provider)
        self._del_provider_btn.clicked.connect(self._on_delete_provider)
        self._test_btn.clicked.connect(self._on_test_connection)
        if not self._api_enabled:
            group.setEnabled(False)
            group.setTitle("API 提供方（登录后可配置）")
        return group

    # ---- 测试连接 ----
    def _on_fetch_models(self) -> None:
        """后台读取当前提供方的模型目录。"""
        if self._loading:
            return
        self._save_provider()
        from src.modules.llm_client import LLMClient

        client = LLMClient()
        ok, reason = client.ready()
        if not ok:
            QMessageBox.warning(self, "无法读取模型目录", reason)
            return
        self._catalog_btn.setEnabled(False)
        self._catalog_btn.setText("读取中…")
        self._catalog_status.setText("正在连接中转站并读取模型目录…")
        self._catalog_thread = _CatalogThread(client, self)
        self._catalog_thread.result_ready.connect(self._on_catalog_result)
        self._catalog_thread.failed.connect(self._on_catalog_failed)
        self._catalog_thread.start()

    def _on_catalog_result(self, models: list[str]) -> None:
        """模型目录读取成功：刷新下拉框并保存。"""
        current = self._model_combo.currentText().strip()
        ordered = list(models)
        if current and current in ordered:
            ordered.remove(current)
            ordered.insert(0, current)
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItems(ordered)
        self._model_combo.setCurrentIndex(0)
        self._model_combo.blockSignals(False)

        providers = self._settings.get_providers()
        pid = self._current_provider_id()
        provider = next((p for p in providers if p.get("id") == pid), None)
        if provider is not None:
            provider["models"] = ordered
            self._settings.save_providers(providers)
        self._catalog_btn.setEnabled(True)
        self._catalog_btn.setText("读取模型目录")
        self._catalog_status.setText(f"读取成功：中转站提供 {len(ordered)} 个模型")

    def _on_catalog_failed(self, message: str) -> None:
        """模型目录读取失败：保留手填模型并显示原因。"""
        self._catalog_btn.setEnabled(True)
        self._catalog_btn.setText("读取模型目录")
        self._catalog_status.setText("目录读取失败，仍可手动填写模型 ID")
        QMessageBox.warning(self, "模型目录读取失败", message[:500])

    def _on_test_connection(self) -> None:
        """后台测试 API 连接，避免阻塞 UI。"""
        from src.modules.llm_client import LLMClient, LLMError

        if self._loading:
            return
        client = LLMClient()
        ok, reason = client.ready()
        if not ok:
            QMessageBox.warning(self, "无法测试", reason)
            return
        self._test_btn.setEnabled(False)
        self._test_btn.setText("测试中…")
        self._test_thread = _TestThread(client, self)
        self._test_thread.result_ready.connect(self._on_test_result)
        self._test_thread.failed.connect(self._on_test_failed)
        self._test_thread.start()

    def _on_test_result(self, reply: str) -> None:
        """测试成功。"""
        self._test_btn.setEnabled(True)
        self._test_btn.setText("测试连接")
        QMessageBox.information(
            self, "连接成功",
            f"模型回应：{reply}\n（可正常使用智能导入）")

    def _on_test_failed(self, message: str) -> None:
        """测试失败。"""
        self._test_btn.setEnabled(True)
        self._test_btn.setText("测试连接")
        QMessageBox.warning(self, "连接失败", message)

    def _build_theme_group(self) -> QGroupBox:
        """主题切换分组。"""
        group = QGroupBox("主题")
        layout = QHBoxLayout(group)
        layout.setSpacing(24)
        self._theme_dark = QRadioButton("暗色")
        self._theme_light = QRadioButton("亮色")
        self._theme_group = QButtonGroup(self)
        self._theme_group.addButton(self._theme_dark)
        self._theme_group.addButton(self._theme_light)
        layout.addWidget(self._theme_dark)
        layout.addWidget(self._theme_light)
        layout.addStretch(1)
        self._theme_dark.toggled.connect(self._on_theme_radio_toggled)
        self._theme_light.toggled.connect(self._on_theme_radio_toggled)
        return group

    def _build_advance_group(self) -> QGroupBox:
        """高级设置：任务并发数 + 扩写行为。"""
        group = QGroupBox("高级")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)
        row1 = QHBoxLayout()
        row1.setSpacing(12)
        label = QLabel("任务并发数：")
        self._concurrency_spin = QSpinBox()
        self._concurrency_spin.setRange(1, 8)
        self._concurrency_spin.setValue(int(self._settings.get("concurrency", 2) or 2))
        hint = QLabel("（翻译腔改写等批量任务同时处理的章节数）")
        hint.setObjectName("KeyHint")
        row1.addWidget(label)
        row1.addWidget(self._concurrency_spin)
        row1.addWidget(hint, 1)
        layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(12)
        label2 = QLabel("扩写版本数：")
        self._num_versions_spin = QSpinBox()
        self._num_versions_spin.setRange(2, 3)
        self._num_versions_spin.setValue(int(self._settings.get("num_versions", 3) or 3))
        self._ask_each_check = QCheckBox("扩写时总是询问采纳哪个版本")
        self._ask_each_check.setChecked(bool(self._settings.get("ask_each_version", False)))
        row2.addWidget(label2)
        row2.addWidget(self._num_versions_spin)
        row2.addWidget(self._ask_each_check)
        row2.addStretch(1)
        layout.addLayout(row2)

        # 输入上下文上限（字符）：防止小窗口模型（如部分 GPT 模型）溢出
        row4 = QHBoxLayout()
        row4.setSpacing(12)
        label4 = QLabel("输入上下文上限：")
        self._context_spin = QSpinBox()
        self._context_spin.setRange(2000, 60000)
        self._context_spin.setSingleStep(1000)
        self._context_spin.setValue(int(
            self._settings.get("context_chars", 16000) or 16000))
        hint4 = QLabel("（单次请求发送的最大字符数，超长自动截断）")
        hint4.setObjectName("KeyHint")
        row4.addWidget(label4)
        row4.addWidget(self._context_spin)
        row4.addWidget(hint4, 1)
        layout.addLayout(row4)

        self._concurrency_spin.valueChanged.connect(self._on_concurrency_changed)
        self._num_versions_spin.valueChanged.connect(self._on_num_versions_changed)
        self._ask_each_check.toggled.connect(self._on_ask_each_changed)
        self._context_spin.valueChanged.connect(self._on_context_changed)
        return group

    def _on_concurrency_changed(self, value: int) -> None:
        """并发数变化：自动保存。"""
        if self._loading:
            return
        self._settings.set("concurrency", value)

    def _on_num_versions_changed(self, value: int) -> None:
        """扩写版本数变化：自动保存。"""
        if self._loading:
            return
        self._settings.set("num_versions", value)

    def _on_ask_each_changed(self, checked: bool) -> None:
        """"总是询问"开关：自动保存。"""
        if self._loading:
            return
        self._settings.set("ask_each_version", bool(checked))

    def _on_context_changed(self, value: int) -> None:
        """输入上下文上限：自动保存。"""
        if self._loading:
            return
        self._settings.set("context_chars", int(value))

    def _build_background_group(self) -> QGroupBox:
        """背景图管理分组。"""
        group = QGroupBox("背景图")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)

        self._upload_btn = QPushButton("上传图片…")
        self._clear_btn = QPushButton("恢复默认")
        self._mode_combo = QComboBox()
        self._mode_combo.addItem("覆盖（cover）", "cover")
        self._mode_combo.addItem("平铺（tile）", "tile")
        self._mode_combo.addItem("拉伸（stretch）", "stretch")
        self._opacity_slider = QSlider(Qt.Horizontal)
        self._opacity_slider.setRange(0, 100)
        self._opacity_label = QLabel("60%")
        self._preview = QLabel()
        self._preview.setObjectName("BgPreview")
        self._preview.setFixedSize(260, 150)
        self._preview.setAlignment(Qt.AlignCenter)
        self._preview.setText("暂无预览")

        grid.addWidget(QLabel("显示模式："), 0, 0)
        grid.addWidget(self._mode_combo, 0, 1)
        grid.addWidget(QLabel("遮罩透明度："), 1, 0)
        grid.addWidget(self._opacity_slider, 1, 1)
        grid.addWidget(self._opacity_label, 1, 2)
        grid.addWidget(self._upload_btn, 2, 0)
        grid.addWidget(self._clear_btn, 2, 1)
        grid.addWidget(self._preview, 0, 3, 3, 1)

        self._upload_btn.clicked.connect(self._on_upload_btn_clicked)
        self._clear_btn.clicked.connect(self._on_clear_btn_clicked)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_combo_changed)
        self._opacity_slider.valueChanged.connect(self._on_opacity_slider_changed)
        return group

    # ---- 加载 ----
    def _load_values(self) -> None:
        """从配置加载并回显各控件。"""
        # 提供方下拉
        self._reload_provider_combo()

        theme = self._settings.get("theme", "dark")
        (self._theme_dark if theme == "dark" else self._theme_light).setChecked(True)

        background = self._settings.get("background", {}) or {}
        path = background.get("path") or DEFAULT_BACKGROUND_PATH
        self._bg_path = PROJECT_ROOT / path
        self._update_preview(self._bg_path)
        mode = background.get("mode", "cover")
        mode_index = self._mode_combo.findData(mode)
        self._mode_combo.setCurrentIndex(mode_index if mode_index >= 0 else 0)
        opacity = int(background.get("opacity", 60))
        self._opacity_slider.setValue(opacity)
        self._opacity_label.setText(f"{opacity}%")

    # ---- 多提供方 ----
    def _reload_provider_combo(self) -> None:
        """重建提供方下拉框并回显当前提供方表单。"""
        self._provider_combo.blockSignals(True)
        self._provider_combo.clear()
        providers = self._settings.get_providers()
        active_id = self._settings.get("active_provider", "")
        current_index = 0
        for i, p in enumerate(providers):
            name = p.get("name") or p.get("id") or f"提供方 {i + 1}"
            self._provider_combo.addItem(name, p.get("id", ""))
            if p.get("id") == active_id:
                current_index = i
        self._provider_combo.setCurrentIndex(current_index)
        self._provider_combo.blockSignals(False)
        self._del_provider_btn.setEnabled(len(providers) > 1)
        self._show_provider_form(providers[current_index] if providers else {})

    def _show_provider_form(self, provider: dict) -> None:
        """把提供方字段回显到表单。"""
        self._provider_name.setText(provider.get("name", "") or provider.get("id", ""))
        self._base_url.setText(provider.get("base_url", ""))
        protocol = provider.get("protocol", "openai")
        protocol_index = self._protocol_combo.findData(protocol)
        self._protocol_combo.blockSignals(True)
        self._protocol_combo.setCurrentIndex(protocol_index if protocol_index >= 0 else 0)
        self._protocol_combo.blockSignals(False)
        self._update_protocol_hint()
        encrypted = provider.get("api_key", "")
        self._api_key.setText(encrypted if encrypted else "")
        self._key_hint.setText("API Key 已加密存储" if encrypted else "")
        models = provider.get("models") or []
        model = models[0] if models else ""
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItems(models or DEFAULT_MODELS)
        index = self._model_combo.findText(model)
        if index >= 0:
            self._model_combo.setCurrentIndex(index)
        elif model:
            self._model_combo.setEditText(model)
        else:
            self._model_combo.setCurrentIndex(0)
        self._model_combo.blockSignals(False)
        if models:
            self._catalog_status.setText(f"已保存 {len(models)} 个模型，可重新读取目录")
        else:
            self._catalog_status.setText("可手动填写模型 ID，或从中转站读取目录")

    def _current_provider_id(self) -> str:
        """当前下拉选中的提供方 id。"""
        return self._provider_combo.currentData() or ""

    def _on_provider_switched(self) -> None:
        """切换提供方：保存 active 并回显表单。"""
        if self._loading:
            return
        pid = self._current_provider_id()
        if pid:
            self._settings.set_active_provider(pid)
            providers = self._settings.get_providers()
            provider = next((p for p in providers if p.get("id") == pid), {})
            self._show_provider_form(provider)
            self._del_provider_btn.setEnabled(len(providers) > 1)

    def _on_add_provider(self) -> None:
        """添加自定义提供方（类似 DSH 的 CustomProviderCard）。"""
        providers = self._settings.get_providers()
        used = {p.get("id") for p in providers}
        n = 1
        new_id = f"custom{n}"
        while new_id in used:
            n += 1
            new_id = f"custom{n}"
        providers.append({
            "id": new_id,
            "name": "新提供方",
            "base_url": "",
            "api_key": "",
            "protocol": "openai",
            "models": [],
        })
        self._settings.save_providers(providers)
        self._settings.set_active_provider(new_id)
        self._reload_provider_combo()
        # 聚焦名称输入框，方便用户编辑
        self._provider_name.setFocus()
        self._provider_name.selectAll()

    def _on_delete_provider(self) -> None:
        """删除当前提供方。"""
        providers = self._settings.get_providers()
        if len(providers) <= 1:
            return
        pid = self._current_provider_id()
        reply = QMessageBox.question(
            self, "删除提供方",
            f"确定删除提供方「{pid}」吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        providers = [p for p in providers if p.get("id") != pid]
        self._settings.save_providers(providers)
        self._reload_provider_combo()

    def _on_provider_name_edited(self) -> None:
        """提供方名称编辑：自动保存。"""
        self._save_provider()

    def _update_protocol_hint(self) -> None:
        """按协议更新 Base URL 示例与目录说明。"""
        protocol = self._protocol_combo.currentData() or "openai"
        hints = {
            "openai": (
                "https://api.example.com/v1",
                "目录端点：Base URL + /models；对话端点：+ /chat/completions",
            ),
            "anthropic": (
                "https://api.anthropic.com/v1",
                "目录端点：Base URL + /models；对话端点：+ /messages",
            ),
            "gemini": (
                "https://generativelanguage.googleapis.com/v1beta",
                "目录与对话端点按 Gemini generateContent 协议构建",
            ),
        }
        placeholder, tooltip = hints.get(protocol, hints["openai"])
        self._base_url.setPlaceholderText(placeholder)
        self._base_url.setToolTip(tooltip)

    def _on_protocol_changed(self) -> None:
        """API 协议变化：自动保存并提示重新读取模型目录。"""
        if self._loading:
            return
        self._update_protocol_hint()
        self._catalog_status.setText("协议已变化，请重新读取模型目录")
        self._save_provider()

    def _on_base_url_edited(self) -> None:
        """Base URL 被编辑：自动保存。"""
        self._save_provider()

    def _on_api_key_edited(self) -> None:
        """API Key 被编辑：标记待加密并自动保存。"""
        self._key_dirty = True
        self._save_provider()

    def _on_model_combo_changed(self) -> None:
        """模型下拉框变化：自动保存。"""
        self._save_provider()

    def _save_provider(self) -> None:
        """保存当前提供方的编辑；API Key 仅在用户修改时重新加密。"""
        if self._loading:
            return
        providers = self._settings.get_providers()
        pid = self._current_provider_id()
        provider = next((p for p in providers if p.get("id") == pid), None)
        if provider is None:
            return
        provider["name"] = self._provider_name.text().strip()
        provider["base_url"] = self._base_url.text().strip()
        provider["protocol"] = self._protocol_combo.currentData() or "openai"
        model = self._model_combo.currentText().strip()
        models = [m for m in provider.get("models", []) if m]
        if model and (not models or models[0] != model):
            models = [model] + [m for m in models if m != model]
        provider["models"] = models
        if self._key_dirty:
            plain = self._api_key.text()
            provider["api_key"] = (
                self._settings.encrypt_api_key(plain) if plain else "")
            self._key_dirty = False
            self._key_hint.setText("API Key 已加密存储" if provider["api_key"] else "")
        self._settings.save_providers(providers)
        # 同步下拉显示名
        self._provider_combo.blockSignals(True)
        idx = self._provider_combo.currentIndex()
        if idx >= 0:
            self._provider_combo.setItemText(
                idx, provider["name"] or provider["id"] or "提供方")
        self._provider_combo.blockSignals(False)

    # ---- 主题相关槽 ----
    def _on_theme_radio_toggled(self, checked: bool) -> None:
        """主题单选变化：立即保存并广播主题名。"""
        if self._loading or not checked:
            return
        theme = "dark" if self._theme_dark.isChecked() else "light"
        self._settings.set("theme", theme)
        self.theme_changed.emit(theme)

    # ---- 背景图相关槽 ----
    def _on_upload_btn_clicked(self) -> None:
        """选择并导入背景图片（jpg/png），复制到 assets/backgrounds。"""
        path_str, _ = QFileDialog.getOpenFileName(
            self, "选择背景图片", str(PROJECT_ROOT), "图片文件 (*.jpg *.jpeg *.png)"
        )
        if not path_str:
            return
        dest = self._copy_to_assets(Path(path_str))
        self._set_bg_path(dest)

    def _on_clear_btn_clicked(self) -> None:
        """恢复默认内置背景。"""
        self._set_bg_path(PROJECT_ROOT / DEFAULT_BACKGROUND_PATH)

    def _copy_to_assets(self, src: Path) -> Path:
        """将用户图片复制进 assets/backgrounds 并返回目标路径。"""
        bg_dir = PROJECT_ROOT / "assets" / "backgrounds"
        bg_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = bg_dir / f"user_{src.stem}_{stamp}{src.suffix.lower()}"
        shutil.copy2(src, dest)
        return dest

    def _set_bg_path(self, path: Path) -> None:
        """设置背景路径并保存、预览、广播。"""
        self._bg_path = path
        self._update_preview(path)
        self._save_background()

    def _on_opacity_slider_changed(self, value: int) -> None:
        """透明度滑块变化：更新标签并自动保存。"""
        self._opacity_label.setText(f"{value}%")
        if not self._loading:
            self._save_background()

    def _on_mode_combo_changed(self) -> None:
        """背景模式变化：自动保存。"""
        self._save_background()

    def _save_background(self) -> None:
        """保存背景配置并广播给主窗口立即应用。"""
        if self._loading:
            return
        relative = ""
        if self._bg_path is not None:
            try:
                relative = str(self._bg_path.relative_to(PROJECT_ROOT))
            except ValueError:
                relative = str(self._bg_path)
        background = {
            "path": relative,
            "mode": self._mode_combo.currentData(),
            "opacity": self._opacity_slider.value(),
        }
        self._settings.set("background", background)
        self.background_changed.emit(background)

    def _update_preview(self, path: Path) -> None:
        """更新预览缩略图。"""
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self._preview.setText("无法加载图片")
            self._preview.setPixmap(QPixmap())
            return
        self._preview.setText("")
        self._preview.setPixmap(
            pixmap.scaled(
                self._preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )
