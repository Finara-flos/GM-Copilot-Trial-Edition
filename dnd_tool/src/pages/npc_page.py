"""NPC 页面：自动扫描与档案补全、台词库、关系网可视化、卡片编辑。

流程：
1. 用户点击「扫描 NPC」→ 后台 worker 扫描名称 + 补全档案（并发）。
2. 档案列表展示；点击 NPC → 右侧显示完整卡片（可编辑）。
3. 「生成台词」→ 为当前模组全部 NPC 生成台词库。
4. 关系图：展示 NPC 关系网，节点可拖动，点击联动卡片。
5. 卡片「编辑」按钮 → 弹窗修改档案字段。
"""
import asyncio

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
    QTextEdit, QVBoxLayout, QWidget,
)

from src.modules.database import Database


class _NpcWorker(QThread):
    """后台 NPC 处理线程（扫描/补全/台词/关系分析）。"""

    progress = Signal(int, str)
    done = Signal(str, object)     # (kind, result)
    failed = Signal(str, str)      # (kind, message)

    def __init__(self, kind: str, coro_factory, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._coro_factory = coro_factory

    def run(self) -> None:
        try:
            result = asyncio.run(self._coro_factory())
            self.done.emit(self._kind, result)
        except Exception as exc:  # noqa: BLE001  统一上报
            self.failed.emit(self._kind, str(exc))


class _EditableCard(QFrame):
    """NPC 详情卡片：字段可编辑，保存后写回数据库。"""

    saved = Signal(str)  # NPC 名称

    def __init__(self, db: Database, parent=None):
        super().__init__(parent)
        self._db = db
        self.setObjectName("Card")
        # 垂直不拉伸：卡片内容自适应高度，避免 QSplitter 拉高产生空白
        from PySide6.QtWidgets import QSizePolicy
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self._name = ""
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # 头部行：包进固定高度容器，防止被垂直拉伸
        head_wrap = QWidget()
        head_wrap.setFixedHeight(28)
        head = QHBoxLayout(head_wrap)
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        self._name_label = QLabel("选择 NPC 查看档案")
        self._name_label.setObjectName("CardTitle")
        head.addWidget(self._name_label, 1)
        self._edit_btn = QPushButton("编辑")
        self._edit_btn.setEnabled(False)
        head.addWidget(self._edit_btn)
        layout.addWidget(head_wrap)

        self._appearance = QLabel("")
        self._appearance.setObjectName("Avatar")
        self._appearance.setFixedSize(56, 56)
        self._appearance.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._appearance, 0, Qt.AlignHCenter)

        self._motivation = QLabel("")
        self._motivation.setWordWrap(True)
        self._secret = QLabel("")
        self._secret.setWordWrap(True)
        self._catchphrase = QLabel("")
        self._catchphrase.setWordWrap(True)
        self._flaw = QLabel("")
        self._flaw.setWordWrap(True)
        self._backstory = QLabel("")
        self._backstory.setWordWrap(True)
        self._dialogue_combo = QComboBox()
        self._dialogue_combo.setVisible(False)
        self._dialogue_text = QLabel("")
        self._dialogue_text.setWordWrap(True)
        self._dialogue_text.setVisible(False)

        # 台词控件：垂直不拉伸（隐藏时也不占拉伸空间）
        from PySide6.QtWidgets import QSizePolicy
        self._dialogue_combo.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._dialogue_text.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Maximum)

        # 字段行：文本控件垂直不拉伸（保持自然高度，避免卡片被拉高后产生空白）
        from PySide6.QtWidgets import QSizePolicy
        for label, widget in (
            ("动机", self._motivation),
            ("秘密", self._secret),
            ("习惯用语", self._catchphrase),
            ("性格弱点", self._flaw),
            ("背景故事", self._backstory),
        ):
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
            row = QHBoxLayout()
            row.setSpacing(8)
            tag = QLabel(label)
            tag.setObjectName("TagCyan")
            tag.setFixedWidth(64)
            tag.setFixedHeight(22)
            row.addWidget(tag, 0, Qt.AlignTop)
            row.addWidget(widget, 1)
            layout.addLayout(row)

        # 台词区
        self._dialogue_text.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout.addWidget(self._dialogue_combo)
        layout.addWidget(self._dialogue_text)
        layout.addStretch(1)
        self._edit_btn.clicked.connect(self._on_edit)
        self._dialogue_combo.currentIndexChanged.connect(self._on_scene_changed)
    # ---- 显示 ----
    def show_npc(self, npc: dict, dialogues: list[dict]) -> None:
        """显示 NPC 档案与台词。"""
        if not npc:
            return
        self._name = npc.get("name", "")
        self._name_label.setText(self._name)
        initial = (npc.get("name") or "?")[0].upper()
        self._appearance.setText(initial)
        self._motivation.setText(npc.get("motivation") or "（暂无）")
        self._secret.setText(npc.get("secret") or "（暂无）")
        self._catchphrase.setText(npc.get("catchphrase") or "（暂无）")
        self._flaw.setText(npc.get("flaw") or "（暂无）")
        self._backstory.setText(npc.get("backstory") or "（暂无）")
        self._edit_btn.setEnabled(True)

        self._dialogue_combo.blockSignals(True)
        self._dialogue_combo.clear()
        if dialogues:
            for d in dialogues:
                self._dialogue_combo.addItem(d["scene"], d["line"])
            self._dialogue_combo.setVisible(True)
            self._dialogue_text.setVisible(True)
            self._dialogue_combo.setCurrentIndex(0)
            self._dialogue_text.setText(dialogues[0]["line"])
        else:
            self._dialogue_combo.setVisible(False)
            self._dialogue_text.setVisible(False)
        self._dialogue_combo.blockSignals(False)

    def clear(self) -> None:
        """清空当前档案，恢复未选择状态。"""
        self._name = ""
        self._name_label.setText("选择 NPC 查看档案")
        self._appearance.clear()
        for label in (
            self._motivation, self._secret, self._catchphrase,
            self._flaw, self._backstory, self._dialogue_text,
        ):
            label.clear()
        self._dialogue_combo.clear()
        self._dialogue_combo.setVisible(False)
        self._dialogue_text.setVisible(False)
        self._edit_btn.setEnabled(False)

    def _on_scene_changed(self, index: int) -> None:
        """切换场景显示对应台词。"""
        data = self._dialogue_combo.itemData(index)
        if data:
            self._dialogue_text.setText(data)

    # ---- 编辑 ----
    def _on_edit(self) -> None:
        """弹窗编辑 NPC 档案字段。"""
        if not self._name:
            return
        npc = self._db.get_npc(self._name)
        if not npc:
            return
        dialog = _NpcEditDialog(npc, self)
        if dialog.exec():
            fields = dialog.collect()
            self._db.update_npc(self._name, fields)
            self.show_npc(self._db.get_npc(self._name),
                          self._db.get_npc_dialogues(self._name))
            self.saved.emit(self._name)


class _NpcEditDialog(QDialog):
    """NPC 档案编辑弹窗。"""

    def __init__(self, npc: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"编辑 NPC：{npc.get('name', '')}")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self._edits: dict[str, QLineEdit | QPlainTextEdit] = {}
        for key, label in (
            ("motivation", "动机"),
            ("secret", "秘密"),
            ("catchphrase", "习惯用语"),
            ("flaw", "性格弱点"),
            ("appearance", "外貌特征"),
            ("backstory", "背景故事"),
        ):
            value = npc.get(key, "")
            if len(value) > 60:
                edit = QPlainTextEdit(value)
                edit.setFixedHeight(70)
            else:
                edit = QLineEdit(value)
            edit.setObjectName("NpcProfileEdit")
            self._edits[key] = edit
            form.addRow(label, edit)
        layout.addLayout(form)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        cancel_btn = QPushButton("取消")
        save_btn = QPushButton("保存")
        save_btn.setObjectName("PrimaryButton")
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        layout.addLayout(btn_row)
        save_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)

    def collect(self) -> dict:
        """收集编辑后的字段。"""
        return {k: w.toPlainText().strip() if isinstance(w, QPlainTextEdit)
                else w.text().strip() for k, w in self._edits.items()}


class NpcPage(QWidget):
    """NPC 管理页。"""

    npcs_changed = Signal()  # 通知主窗口刷新关键词高亮

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self._db = db
        self._file = ""
        self._worker: _NpcWorker | None = None
        self._npcs: list[dict] = []
        self._selected: dict | None = None
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._build_ui()

    def set_current_file(self, file_name: str) -> None:
        """设置当前模组文件。"""
        self._file = file_name
        self._file_label.setText(f"当前模组：{file_name}" if file_name else "尚未导入模组")
        self.refresh()
        self._update_enabled()

    # ---- 构建 ----
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(16)

        title = QLabel("NPC 系统")
        title.setObjectName("PageTitle")
        self._file_label = QLabel("尚未导入模组")
        self._file_label.setObjectName("PageSubtitle")
        root.addWidget(title)
        root.addWidget(self._file_label)

        # 操作行
        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self._scan_btn = QPushButton("扫描 NPC")
        self._dialogue_btn = QPushButton("生成台词库")
        self._clear_btn = QPushButton("清空 NPC")
        action_row.addWidget(self._scan_btn)
        action_row.addWidget(self._dialogue_btn)
        action_row.addWidget(self._clear_btn)
        action_row.addStretch(1)
        root.addLayout(action_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.hide()
        self._status = QLabel("")
        self._status.setObjectName("DropHint")
        self._status.setWordWrap(True)
        root.addWidget(self._progress)
        root.addWidget(self._status)

        # 主区分栏：左列表 + 右卡片
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(12)

        # 左：NPC 列表
        left = QFrame()
        left.setObjectName("Card")
        left.setMinimumWidth(220)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 12, 14, 12)
        left_layout.setSpacing(8)
        left_title = QLabel("NPC 列表")
        left_title.setObjectName("CardTitle")
        left_layout.addWidget(left_title)
        self._npc_list = _NpcListWidget(self)
        self._npc_list.setObjectName("KeywordList")
        self._npc_list.npc_selected.connect(self._on_npc_selected)
        left_layout.addWidget(self._npc_list, 1)
        splitter.addWidget(left)

        # 右：卡片（包进容器 AlignTop：卡片内容紧凑顶部对齐，
        # 防止 QSplitter 拉伸文本行产生大片假空白）
        card_container = QWidget()
        card_outer = QVBoxLayout(card_container)
        card_outer.setContentsMargins(0, 0, 0, 0)
        self._card = _EditableCard(self._db)
        card_outer.addWidget(self._card, 0, Qt.AlignTop)
        card_outer.addStretch(1)
        splitter.addWidget(card_container)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        splitter.setSizes([260, 640])
        root.addWidget(splitter, 1)

        self._scan_btn.clicked.connect(self._on_scan)
        self._dialogue_btn.clicked.connect(self._on_generate_dialogues)
        self._clear_btn.clicked.connect(self._on_clear)
        self._card.saved.connect(lambda _name: self.refresh())

    # ---- 状态 ----
    def _update_enabled(self) -> None:
        has_file = bool(self._file)
        self._scan_btn.setEnabled(has_file)
        self._dialogue_btn.setEnabled(has_file and bool(self._npcs))
        self._clear_btn.setEnabled(has_file)

    def refresh(self) -> None:
        """从数据库刷新 NPC 列表，并确保详情区始终有明确状态。"""
        self._npcs = self._db.get_npcs(self._file) if self._file else []
        self._npc_list.set_npcs(self._npcs)
        if not self._npcs:
            self._selected = None
            self._card.clear()
            self._update_enabled()
            return

        selected_name = self._selected.get("name", "") if self._selected else ""
        npc = next(
            (item for item in self._npcs if item.get("name") == selected_name),
            self._npcs[0],
        )
        if selected_name:
            self._selected = npc
            self._npc_list.select_npc(npc["name"])
        else:
            self._npc_list.clear_selection()
        self._card.show_npc(npc, self._db.get_npc_dialogues(npc["name"]))
        self._update_enabled()

    # ---- 操作 ----
    def _on_scan(self) -> None:
        """扫描并补全 NPC 档案。"""
        if not self._client_ready():
            return
        self._set_running(True, "正在扫描 NPC…")
        self._worker = _NpcWorker("scan", self._scan_coro, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_scan_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_generate_dialogues(self) -> None:
        """为全部 NPC 生成台词库。"""
        if not self._client_ready():
            return
        if not self._npcs:
            return
        self._set_running(True, "正在生成台词库…")
        self._worker = _NpcWorker("dialogues", self._dialogue_coro, self)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(self._on_dialogue_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_clear(self) -> None:
        """清空当前模组的 NPC 档案与台词。"""
        reply = QMessageBox.question(
            self, "清空 NPC",
            "确定清空当前模组的全部 NPC 档案与台词吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        npc_names = [npc["name"] for npc in self._npcs]
        self._db.clear_npcs(self._file)
        for name in npc_names:
            self._db.clear_npc_dialogues(name)
        self._npcs = []
        self._selected = None
        self.refresh()
        self.npcs_changed.emit()

    # ---- worker 协程 ----
    def _client_ready(self) -> bool:
        from src.core.api_client import AsyncLLMClient
        ok, reason = AsyncLLMClient().ready()
        if not ok:
            QMessageBox.information(self, "未配置 API", reason)
        return ok

    def _all_text(self) -> str:
        segs = self._db.get_segments(self._file)
        return "\n\n".join(s["content"] for s in segs)

    async def _scan_coro(self):
        from src.core.api_client import AsyncLLMClient
        from src.modules.npc_extractor import scan_and_build_all
        from src.pages.settings_page import SettingsManager

        client = AsyncLLMClient()
        concurrency = int(SettingsManager().get("concurrency", 2) or 2)

        def progress(done, total, status):
            self._worker.progress.emit(
                int(done * 100 / max(total, 1)), f"{status}（{done}/{total}）")

        return await scan_and_build_all(
            client, self._all_text(), self._db, self._file,
            progress_cb=progress, concurrency=concurrency)

    async def _dialogue_coro(self):
        from src.core.api_client import AsyncLLMClient
        from src.modules.npc_dialogue import generate_all_dialogues
        from src.pages.settings_page import SettingsManager

        client = AsyncLLMClient()
        concurrency = int(SettingsManager().get("concurrency", 2) or 2)
        npcs = self._db.get_npcs(self._file)
        count = await generate_all_dialogues(
            client, npcs, self._db,
            progress_cb=lambda d, t, s: self._worker.progress.emit(
                int(d * 100 / max(t, 1)), s),
            concurrency=concurrency)
        return count

    # ---- 回调 ----
    def _on_progress(self, pct: int, status: str) -> None:
        self._progress.setValue(pct)
        self._status.setText(status)

    def _on_scan_done(self, kind: str, npcs: list) -> None:
        self._set_running(False)
        self._status.setText(f"扫描完成：共 {len(npcs)} 位 NPC")
        self.refresh()
        self.npcs_changed.emit()

    def _on_dialogue_done(self, kind: str, count: int) -> None:
        self._set_running(False)
        self._status.setText(f"台词库生成完成：共 {count} 条台词")
        if self._selected:
            npc = self._db.get_npc(self._selected.get("name", ""))
            if npc:
                self._card.show_npc(npc, self._db.get_npc_dialogues(npc["name"]))
        self._update_enabled()

    def _on_failed(self, kind: str, message: str) -> None:
        self._set_running(False)
        QMessageBox.warning(self, "NPC 处理失败", message[:400])

    def _set_running(self, running: bool, status: str = "") -> None:
        """切换忙碌状态。"""
        for btn in (self._scan_btn, self._dialogue_btn, self._clear_btn):
            btn.setEnabled(not running)
        self._progress.setVisible(running)
        self._progress.setValue(0 if running else 100)
        if running:
            self._status.setText(status)

    # ---- 列表联动 ----
    def _on_npc_selected(self, name: str) -> None:
        npc = self._db.get_npc(name)
        if npc:
            self._selected = npc
            self._npc_list.select_npc(name)
            self._card.show_npc(npc, self._db.get_npc_dialogues(name))

    def select_npc_by_name(self, name: str) -> None:
        """外部（信息面板悬浮联动）选中 NPC。"""
        npc = self._db.get_npc(name)
        if npc:
            self._selected = npc
            self._npc_list.select_npc(name)
            self._card.show_npc(npc, self._db.get_npc_dialogues(name))


class _NpcListWidget(QWidget):
    """NPC 名称列表（简单垂直按钮列表，避免 QListWidget 长名截断问题）。"""

    npc_selected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self._buttons: list[QPushButton] = []
        self._empty = QLabel("尚未扫描 NPC")
        self._empty.setObjectName("DropHint")
        self._empty.setWordWrap(True)
        self._layout.addWidget(self._empty)
        self._layout.addStretch(1)

    def set_npcs(self, npcs: list[dict]) -> None:
        """重建列表。"""
        for btn in self._buttons:
            self._layout.removeWidget(btn)
            btn.deleteLater()
        self._buttons.clear()
        if not npcs:
            self._empty.setVisible(True)
            return
        self._empty.setVisible(False)
        for npc in npcs:
            name = npc.get("name", "")
            btn = QPushButton(name)
            btn.setObjectName("NpcListItem")
            btn.setToolTip(npc.get("motivation") or name)
            btn.clicked.connect(lambda _=False, n=name: self.npc_selected.emit(n))
            self._layout.insertWidget(self._layout.count() - 1, btn)
            self._buttons.append(btn)

    def clear_selection(self) -> None:
        """取消列表高亮，不改变右侧预览卡片。"""
        for btn in self._buttons:
            btn.setProperty("selected", False)
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def select_npc(self, name: str) -> None:
        """高亮指定 NPC 按钮。"""
        for btn in self._buttons:
            selected = btn.text() == name
            btn.setProperty("selected", selected)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
