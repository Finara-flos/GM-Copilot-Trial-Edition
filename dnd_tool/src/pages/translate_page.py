"""翻译优化页：术语一致性检查、文化隐喻转译、翻译腔句式改写。

所有 API 密集型操作在后台 QThread 中运行 asyncio 事件循环，
通过 AsyncLLMClient（SSE 流式）+ TaskQueueRunner（章节队列/暂停/断点）执行。
"""
import asyncio
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QProgressBar, QPushButton, QVBoxLayout, QWidget,
)
from PySide6.QtCore import QThread

from src.core.task_queue import Task, TaskQueueRunner
from src.modules.database import Database


class _AsyncWorker(QThread):
    """在独立线程中运行一个 asyncio 协程。"""

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

    def _progress(self, done: int, total: int, status: str) -> None:
        pct = int(done * 100 / max(total, 1))
        self.progress.emit(pct, f"{status}（{done}/{total}）")


class TranslatePage(QWidget):
    """翻译优化页。"""

    segments_changed = Signal()      # 术语/隐喻替换后通知主窗口重载对照

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self._db = db
        self._file = ""
        self._worker: _AsyncWorker | None = None
        self._runner: TaskQueueRunner | None = None
        self._term_groups: list[dict] = []
        self._metaphors: list[dict] = []
        self._last_term_record_ids: list[int] = []
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._build_ui()

    def set_current_file(self, file_name: str) -> None:
        """设置当前处理的模组文件。"""
        self._file = file_name
        self._file_label.setText(f"当前模组：{file_name}" if file_name else "尚未导入模组")
        self._update_enabled()

    # ---- 构建 ----
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(16)

        title = QLabel("翻译优化")
        title.setObjectName("PageTitle")
        self._file_label = QLabel("尚未导入模组")
        self._file_label.setObjectName("PageSubtitle")
        root.addWidget(title)
        root.addWidget(self._file_label)

        # 卡片 1：术语一致性
        term_card = self._build_term_card()
        root.addWidget(term_card)

        # 卡片 2：文化隐喻
        meta_card = self._build_metaphor_card()
        root.addWidget(meta_card)

        # 卡片 3：翻译腔改写
        fix_card = self._build_fix_card()
        root.addWidget(fix_card)

        root.addStretch(1)

    def _card(self, title_text: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        t = QLabel(title_text)
        t.setObjectName("CardTitle")
        layout.addWidget(t)
        return card, layout

    def _build_term_card(self) -> QFrame:
        card, layout = self._card("术语一致性检查")
        row = QHBoxLayout()
        row.setSpacing(8)
        self._term_check_btn = QPushButton("开始检查")
        self._term_apply_btn = QPushButton("应用所选")
        self._term_undo_btn = QPushButton("撤销上次")
        row.addWidget(self._term_check_btn)
        row.addWidget(self._term_apply_btn)
        row.addWidget(self._term_undo_btn)
        row.addStretch(1)
        layout.addLayout(row)
        self._term_list = QListWidget()
        self._term_list.setObjectName("KeywordList")
        self._term_list.setMaximumHeight(130)
        self._term_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self._term_list)
        self._term_status = QLabel("")
        self._term_status.setObjectName("DropHint")
        layout.addWidget(self._term_status)

        self._term_check_btn.clicked.connect(self._on_term_check)
        self._term_apply_btn.clicked.connect(self._on_term_apply)
        self._term_undo_btn.clicked.connect(self._on_term_undo)
        return card

    def _build_metaphor_card(self) -> QFrame:
        card, layout = self._card("文化隐喻转译")
        row = QHBoxLayout()
        row.setSpacing(8)
        self._meta_check_btn = QPushButton("开始分析")
        row.addWidget(self._meta_check_btn)
        row.addStretch(1)
        layout.addLayout(row)
        self._meta_list = QListWidget()
        self._meta_list.setObjectName("KeywordList")
        self._meta_list.setMaximumHeight(150)
        self._meta_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self._meta_list)
        self._meta_status = QLabel("")
        self._meta_status.setObjectName("DropHint")
        layout.addWidget(self._meta_status)

        self._meta_check_btn.clicked.connect(self._on_metaphor_check)
        return card

    def _build_fix_card(self) -> QFrame:
        card, layout = self._card("翻译腔句式改写")
        row = QHBoxLayout()
        row.setSpacing(8)
        self._fix_start_btn = QPushButton("开始改写")
        self._fix_pause_btn = QPushButton("暂停")
        row.addWidget(self._fix_start_btn)
        row.addWidget(self._fix_pause_btn)
        row.addStretch(1)
        layout.addLayout(row)
        self._fix_progress = QProgressBar()
        self._fix_progress.setRange(0, 100)
        self._fix_progress.setValue(0)
        layout.addWidget(self._fix_progress)
        self._fix_status = QLabel("")
        self._fix_status.setObjectName("DropHint")
        layout.addWidget(self._fix_status)

        self._fix_start_btn.clicked.connect(self._on_fix_start)
        self._fix_pause_btn.clicked.connect(self._on_fix_pause)
        self._fix_pause_btn.setEnabled(False)
        return card

    def _update_enabled(self) -> None:
        """按是否已导入模组/API 是否可用启用按钮。"""
        has_file = bool(self._file)
        for btn in (self._term_check_btn, self._meta_check_btn, self._fix_start_btn):
            btn.setEnabled(has_file)

    # ---- 工具 ----
    def _client_ready(self) -> bool:
        """API 是否就绪；未就绪时提示并返回 False。"""
        from src.core.api_client import AsyncLLMClient

        ok, reason = AsyncLLMClient().ready()
        if not ok:
            QMessageBox.information(self, "未配置 API", reason)
        return ok

    def _all_text(self) -> str:
        """当前模组全部段落拼接。"""
        segs = self._db.get_segments(self._file)
        return "\n\n".join(s["content"] for s in segs)

    def _chapters(self) -> list[str]:
        """当前模组章节列表（保持出现顺序）。"""
        seen: list[str] = []
        for seg in self._db.get_segments(self._file):
            ch = seg.get("chapter", "未分章")
            if ch not in seen:
                seen.append(ch)
        return seen

    def _chapter_text(self, chapter: str) -> str:
        segs = [s["content"] for s in self._db.get_segments(self._file)
                if s.get("chapter") == chapter]
        return "\n\n".join(segs)

    # ---- 术语一致性 ----
    def _on_term_check(self) -> None:
        if not self._client_ready():
            return
        self._term_list.clear()
        self._term_status.setText("正在分析全文…")
        self._worker = _AsyncWorker(
            "terms", self._term_check_coro, self)
        self._worker.done.connect(self._on_term_check_done)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    async def _term_check_coro(self):
        from src.core.api_client import AsyncLLMClient
        from src.modules.term_checker import check_terms

        client = AsyncLLMClient()
        return await check_terms(client, self._all_text())

    def _on_term_check_done(self, kind: str, groups: list[dict]) -> None:
        self._term_groups = groups
        self._term_list.clear()
        for group in groups:
            variants = "、".join(group["variants"])
            item = QListWidgetItem(f"{group['concept']}（{variants}）")
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            self._term_list.addItem(item)
        self._term_status.setText(f"发现 {len(groups)} 组不一致术语，勾选后点击「应用所选」")

    # ---- 术语应用/撤销 ----
    def _on_term_apply(self) -> None:
        if not self._term_groups:
            return
        selected = []
        for row, group in enumerate(self._term_groups):
            item = self._term_list.item(row)
            if item and item.checkState() == Qt.Checked:
                selected.append(group)
        if not selected:
            return
        from src.modules.term_checker import apply_term_replacements

        changed = apply_term_replacements(self._db, self._file, selected)
        self._term_status.setText(f"已统一替换 {changed} 个段落")
        self.segments_changed.emit()

    def _on_term_undo(self) -> None:
        from src.modules.term_checker import undo_term_replacement

        records = self._db.get_term_replacements()
        if not records:
            self._term_status.setText("没有可撤销的替换")
            return
        latest = records[-1]
        changed = undo_term_replacement(self._db, self._file, latest["id"])
        self._term_status.setText(f"已撤销「{latest['group_name']}」的替换（{changed} 段）")
        self.segments_changed.emit()

    # ---- 文化隐喻 ----
    def _on_metaphor_check(self) -> None:
        if not self._client_ready():
            return
        self._meta_list.clear()
        self._meta_status.setText("正在分析文化隐喻…")
        self._worker = _AsyncWorker(
            "metaphors", self._metaphor_check_coro, self)
        self._worker.done.connect(self._on_metaphor_check_done)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    async def _metaphor_check_coro(self):
        from src.core.api_client import AsyncLLMClient
        from src.modules.metaphor_translator import check_metaphors

        client = AsyncLLMClient()
        return await check_metaphors(client, self._all_text())

    def _on_metaphor_check_done(self, kind: str, items: list[dict]) -> None:
        self._metaphors = items
        self._meta_list.clear()
        for i, item_data in enumerate(items):
            item = QListWidgetItem()
            widget = QWidget()
            row = QHBoxLayout(widget)
            row.setContentsMargins(6, 4, 6, 4)
            row.setSpacing(8)
            text = QLabel(f"{item_data['original']} → {item_data['suggestion']}")
            text.setWordWrap(True)
            confirm_btn = QPushButton("确认")
            ignore_btn = QPushButton("忽略")
            row.addWidget(text, 1)
            row.addWidget(confirm_btn)
            row.addWidget(ignore_btn)
            confirm_btn.clicked.connect(
                lambda _=False, idx=i: self._on_metaphor_confirm(idx))
            ignore_btn.clicked.connect(
                lambda _=False, idx=i: self._on_metaphor_ignore(idx))
            item.setSizeHint(widget.sizeHint())
            self._meta_list.addItem(item)
            self._meta_list.setItemWidget(item, widget)
        self._meta_status.setText(f"发现 {len(items)} 处文化隐喻，逐条确认或忽略")

    def _on_metaphor_confirm(self, index: int) -> None:
        from src.modules.metaphor_translator import apply_metaphor

        item_data = self._metaphors[index]
        changed = apply_metaphor(self._db, self._file,
                                 item_data["original"], item_data["suggestion"])
        self._meta_status.setText(f"已应用「{item_data['original']}」（{changed} 段）")
        self._metaphors[index] = {**item_data, "applied": True}
        self._refresh_metaphor_row(index)
        self.segments_changed.emit()

    def _on_metaphor_ignore(self, index: int) -> None:
        self._metaphors[index] = {**self._metaphors[index], "ignored": True}
        self._refresh_metaphor_row(index)
        self._meta_status.setText("已忽略该条")

    def _refresh_metaphor_row(self, index: int) -> None:
        """把已处理的行变灰并禁用按钮。"""
        item = self._meta_list.item(index)
        widget = self._meta_list.itemWidget(item)
        if widget is None:
            return
        meta = self._metaphors[index]
        for btn in widget.findChildren(QPushButton):
            btn.setEnabled(False)
        for label in widget.findChildren(QLabel):
            if meta.get("applied"):
                label.setText(f"✓ {label.text()}")
            elif meta.get("ignored"):
                label.setText(f"✗ {label.text()}")

    # ---- 翻译腔改写 ----
    def _on_fix_start(self) -> None:
        if not self._client_ready():
            return
        self._fix_start_btn.setEnabled(False)
        self._fix_pause_btn.setEnabled(True)
        self._fix_pause_btn.setText("暂停")
        self._fix_progress.setValue(0)
        self._fix_status.setText("准备中…")
        self._worker = _AsyncWorker("fixes", self._fix_coro, self)
        self._worker.progress.connect(self._on_fix_progress)
        self._worker.done.connect(self._on_fix_done)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.start()

    async def _fix_coro(self):
        from src.core.api_client import AsyncLLMClient
        from src.modules.translation_fixer import fix_chapter

        chapters = self._chapters()
        if not chapters:
            return {}
        client = AsyncLLMClient()
        tasks = [Task(i, ch, self._file, {}) for i, ch in enumerate(chapters)]

        async def process_fn(task: Task):
            return await fix_chapter(client, self._chapter_text(task.chapter))

        concurrency = int(self._settings_concurrency())
        runner = TaskQueueRunner(
            tasks, process_fn, self._db,
            concurrency=concurrency, task_type="translation_fix")
        self._runner = runner
        results = await runner.run(progress_cb=self._worker._progress)
        # 结果按章节聚合
        fixes_by_chapter: dict[str, list[dict]] = {}
        for index, pairs in results.items():
            chapter = chapters[index]
            fixes_by_chapter[chapter] = [
                {"original": p["original"], "fixed": p["fixed"],
                 "status": "pending"}
                for p in pairs
            ]
        return fixes_by_chapter

    def _settings_concurrency(self) -> int:
        from src.pages.settings_page import SettingsManager

        return int(SettingsManager().get("concurrency", 2) or 2)

    def _on_fix_progress(self, pct: int, status: str) -> None:
        self._fix_progress.setValue(pct)
        self._fix_status.setText(status)

    def _on_fix_done(self, kind: str, fixes_by_chapter: dict) -> None:
        self._fix_start_btn.setEnabled(True)
        self._fix_pause_btn.setEnabled(False)
        if not fixes_by_chapter:
            self._fix_status.setText("没有可改写的章节（可能已完成，可清除任务进度后重试）")
            return
        total = sum(len(v) for v in fixes_by_chapter.values())
        self._fix_status.setText(f"改写完成：{len(fixes_by_chapter)} 章，共 {total} 处修改")
        self.fixes_ready.emit(fixes_by_chapter)

    def _on_fix_pause(self) -> None:
        """暂停/继续切换。"""
        if self._runner is None:
            return
        if self._runner.is_paused():
            self._runner.resume()
            self._fix_pause_btn.setText("暂停")
            self._fix_status.setText("继续处理…")
        else:
            self._runner.pause()
            self._fix_pause_btn.setText("继续")
            self._fix_status.setText("已暂停")

    # ---- 通用 ----
    def _on_worker_failed(self, kind: str, message: str) -> None:
        self._fix_start_btn.setEnabled(True)
        self._fix_pause_btn.setEnabled(False)
        label = {
            "terms": self._term_status,
            "metaphors": self._meta_status,
            "fixes": self._fix_status,
        }.get(kind)
        if label is not None:
            label.setText(f"失败：{message[:120]}")
        QMessageBox.warning(self, "任务失败", message[:300])

    fixes_ready = Signal(object)  # 翻译腔改写完成：{chapter: [fixes]}
