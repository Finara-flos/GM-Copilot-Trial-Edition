"""导入页：拖拽/点击导入文件、进度展示、关键词词库管理。"""
from pathlib import Path

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QProgressBar, QPushButton,
    QVBoxLayout, QWidget,
)
from PySide6.QtCore import QThread

from src.modules.database import Database
from src.modules.importer import SUPPORTED_EXTS, import_document
from src.widgets.keyword_highlight import KIND_COLORS, KIND_LABELS

SUPPORTED_DESC = "、".join(sorted(SUPPORTED_EXTS))


class DropZone(QFrame):
    """可拖拽/点击的文件投放区。"""

    file_dropped = Signal(str)
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setMinimumHeight(180)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 24, 20, 24)
        layout.setSpacing(8)
        icon = QLabel("⤓")
        icon.setObjectName("DropIcon")
        icon.setAlignment(Qt.AlignCenter)
        text = QLabel("拖拽模组文件到这里，或点击选择文件")
        text.setObjectName("DropText")
        text.setAlignment(Qt.AlignCenter)
        hint = QLabel(f"支持格式：{SUPPORTED_DESC}")
        hint.setObjectName("DropHint")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(icon)
        layout.addWidget(text)
        layout.addWidget(hint)

    def _set_hover(self, hovered: bool) -> None:
        """切换悬停高亮（QSS 动态属性）。"""
        self.setProperty("hover", hovered)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        """接受含受支持文件拖拽。"""
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            if any(Path(p).suffix.lower() in SUPPORTED_EXTS for p in paths):
                self._set_hover(True)
                event.acceptProposedAction()
                return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        """离开时取消悬停高亮。"""
        self._set_hover(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        """接收第一个受支持文件。"""
        self._set_hover(False)
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if Path(path).suffix.lower() in SUPPORTED_EXTS:
                self.file_dropped.emit(path)
                event.acceptProposedAction()
                return
        event.ignore()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """点击选择文件。"""
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class ImportWorker(QThread):
    """后台导入线程：驱动进度信号，避免阻塞 UI。

    PDF 解析统一在独立子进程中执行（fitz 会污染本进程其他线程的网络），
    结果经 stdout 传回；非 PDF 文件（TXT/MD/DOCX）进程内解析。
    """

    progress = Signal(int, str)
    succeeded = Signal(str, int)
    failed = Signal(str)
    fell_back = Signal(str)  # 智能导入失败回退本地解析时的说明

    def __init__(self, path: str, db: Database, smart: bool = False, parent=None):
        super().__init__(parent)
        self._path = path
        self._db = db
        self._smart = smart

    def run(self) -> None:
        try:
            if Path(self._path).suffix.lower() == ".pdf":
                self._run_pdf_subprocess()
            else:
                from src.modules.importer import parse_document

                segments, filename, _ = parse_document(
                    self._path, self._emit_progress)
                self._db.clear_file(filename)
                self._db.insert_segments(segments, filename)
                self.succeeded.emit(filename, len(segments))
        except Exception as exc:  # noqa: BLE001  导入失败统一上报
            self.failed.emit(str(exc))

    def _run_pdf_subprocess(self) -> None:
        """在子进程中解析 PDF（快速本地解析，立即入库显示）。

        智能精修由主窗口在导入完成后另行启动（RefineWorker，后台异步）。
        """
        import json as _json
        import os
        import subprocess
        import sys as _sys

        root = Path(__file__).resolve().parent.parent.parent
        config: dict = {"path": self._path, "mode": "fast", "api": None}

        env = dict(os.environ)
        env["PYTHONPATH"] = str(root) + os.pathsep + env.get("PYTHONPATH", "")
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.Popen(
            [_sys.executable, "-m", "src.modules.pdf_worker_cli"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            cwd=str(root), env=env,
        )
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(_json.dumps(config))
        proc.stdin.close()

        result_data = None
        error = None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if line.startswith("PROGRESS|"):
                _, pct, status = line.split("|", 2)
                try:
                    self.progress.emit(int(pct), status)
                except ValueError:
                    pass
            elif line.startswith("RESULT|"):
                try:
                    result_data = _json.loads(line[len("RESULT|"):])
                except _json.JSONDecodeError as exc:
                    error = f"子进程结果解析失败：{exc}"
            elif line.startswith("ERROR|"):
                error = line[len("ERROR|"):]
        proc.wait(timeout=600)
        stderr_tail = (proc.stderr.read() if proc.stderr else "")[-2000:]

        if error:
            self.failed.emit(error)
            return
        if proc.returncode != 0 or result_data is None:
            self.failed.emit(
                f"PDF 解析子进程异常（退出码 {proc.returncode}）\n{stderr_tail}")
            return

        filename = result_data["filename"]
        segments = result_data.get("segments") or []
        keywords = result_data.get("keywords") or []
        self._db.clear_file(filename)
        self._db.insert_segments(segments, filename)
        added = sum(
            1 for kw in keywords
            if self._db.add_keyword(kw.get("name", ""), kw.get("kind", ""),
                                    kw.get("detail", ""), filename))
        if added:
            self.progress.emit(97, f"词库新增 {added} 条")
        self.succeeded.emit(filename, len(segments))

    def _run_fast_in_process(self) -> None:
        """回退：进程内本地解析（PDF 子进程失败时）。"""
        from src.modules.importer import import_document

        segments, filename = import_document(
            self._path, self._emit_progress, db=self._db)
        self.succeeded.emit(filename, len(segments))

    def _emit_progress(self, percent: int, status: str) -> None:
        self.progress.emit(percent, status)


class ImportPage(QWidget):
    """模组导入页：拖拽导入 + 进度条 + 关键词词库。"""

    import_finished = Signal(str, int)   # 文件名, 分段数
    keywords_changed = Signal()

    def __init__(self, db: Database, parent: QWidget | None = None):
        super().__init__(parent)
        self._db = db
        self._worker: ImportWorker | None = None
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._build_ui()
        self.refresh_keyword_list()

    # ---- 构建 ----
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 28)
        root.setSpacing(16)

        title = QLabel("模组导入")
        title.setObjectName("PageTitle")
        subtitle = QLabel("导入 PDF / TXT / MD / DOCX 模组文档，自动解析章节与分段")
        subtitle.setObjectName("PageSubtitle")
        root.addWidget(title)
        root.addWidget(subtitle)

        # 导入卡片
        import_card = QFrame()
        import_card.setObjectName("Card")
        import_layout = QVBoxLayout(import_card)
        import_layout.setContentsMargins(20, 18, 20, 18)
        import_layout.setSpacing(14)

        # 导入模式选择
        mode_row = QHBoxLayout()
        mode_row.setSpacing(10)
        mode_label = QLabel("导入模式：")
        mode_label.setObjectName("DropHint")
        self._mode_combo = QComboBox(import_card)
        self._mode_combo.addItem("智能（AI 精修，推荐）", "smart")
        self._mode_combo.addItem("快速（本地解析）", "fast")
        self._mode_hint = QLabel("AI 修复双栏乱序、清除广告页眉、识别章节并自动抽取词库")
        self._mode_hint.setObjectName("DropHint")
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self._mode_combo)
        mode_row.addWidget(self._mode_hint, 1)
        import_layout.addLayout(mode_row)

        self._dropzone = DropZone(import_card)
        self._dropzone.file_dropped.connect(self._on_file_selected)
        self._dropzone.clicked.connect(self._on_click_select)
        import_layout.addWidget(self._dropzone)

        self._progress_bar = QProgressBar(import_card)
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.hide()
        self._progress_status = QLabel("")
        self._progress_status.setObjectName("DropHint")
        self._progress_status.setWordWrap(True)
        self._progress_status.hide()
        import_layout.addWidget(self._progress_bar)
        import_layout.addWidget(self._progress_status)

        root.addWidget(import_card)

        # 词库卡片
        lexicon_card = QFrame()
        lexicon_card.setObjectName("Card")
        lexicon_layout = QVBoxLayout(lexicon_card)
        lexicon_layout.setContentsMargins(20, 18, 20, 18)
        lexicon_layout.setSpacing(12)

        head = QHBoxLayout()
        lexicon_title = QLabel("关键词词库")
        lexicon_title.setObjectName("CardTitle")
        self._keyword_file_combo = QComboBox(lexicon_card)
        self._keyword_file_combo.addItem("尚未导入模组", "")
        self._keyword_file_combo.setMinimumWidth(220)
        lexicon_hint = QLabel("NPC 红 · 地名蓝 · 物品绿")
        lexicon_hint.setObjectName("DropHint")
        head.addWidget(lexicon_title)
        head.addWidget(self._keyword_file_combo)
        head.addWidget(lexicon_hint, 1)
        lexicon_layout.addLayout(head)

        self._keyword_list = QListWidget(lexicon_card)
        self._keyword_list.setObjectName("KeywordList")
        self._keyword_list.setMaximumHeight(140)
        self._keyword_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        lexicon_layout.addWidget(self._keyword_list)

        add_row = QHBoxLayout()
        add_row.setSpacing(8)
        self._kw_name = QLineEdit(lexicon_card)
        self._kw_name.setPlaceholderText("词条名称，如：哈维尔")
        self._kw_kind = QComboBox(lexicon_card)
        self._kw_kind.addItem("NPC", "npc")
        self._kw_kind.addItem("地名", "place")
        self._kw_kind.addItem("物品", "item")
        self._kw_detail = QLineEdit(lexicon_card)
        self._kw_detail.setPlaceholderText("详细信息（可选）")
        add_btn = QPushButton("添加", lexicon_card)
        add_btn.setObjectName("PrimaryButton")
        del_btn = QPushButton("删除选中", lexicon_card)
        add_row.addWidget(self._kw_name, 1)
        add_row.addWidget(self._kw_kind)
        add_row.addWidget(self._kw_detail, 2)
        add_row.addWidget(add_btn)
        add_row.addWidget(del_btn)
        lexicon_layout.addLayout(add_row)

        self._keyword_file_combo.currentIndexChanged.connect(self.refresh_keyword_list)
        add_btn.clicked.connect(self._on_add_keyword)
        del_btn.clicked.connect(self._on_remove_keyword)
        self._kw_name.returnPressed.connect(add_btn.click)

        root.addWidget(lexicon_card)
        root.addStretch(1)

    def set_ai_enabled(self, enabled: bool) -> None:
        """切换智能导入可用性；本地快速解析始终保留。"""
        smart_index = self._mode_combo.findData("smart")
        if smart_index >= 0:
            item = self._mode_combo.model().item(smart_index)
            if item is not None:
                item.setEnabled(enabled)
        if not enabled and self._mode_combo.currentData() == "smart":
            self._mode_combo.setCurrentIndex(self._mode_combo.findData("fast"))
        if not enabled:
            self._mode_hint.setText("未登录：仅可使用快速（本地解析）模式")

    # ---- 导入 ----
    def _on_click_select(self) -> None:
        """文件对话框选择文件。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择模组文件", str(Path.home()),
            f"模组文件 (*{' *'.join(sorted(SUPPORTED_EXTS))})",
        )
        if path:
            self._start_import(path)

    def _on_file_selected(self, path: str) -> None:
        """拖拽投递文件。"""
        self._start_import(path)

    def _start_import(self, path: str) -> None:
        """启动后台导入线程（先快速入库立即显示；智能模式随后后台精修）。"""
        if self._worker is not None and self._worker.isRunning():
            return
        smart = self._mode_combo.currentData() == "smart"
        self._smart_requested = smart
        if smart:
            from src.modules.llm_client import LLMClient
            ok, reason = LLMClient().ready()
            if not ok:
                QMessageBox.information(
                    self, "未配置 API",
                    f"{reason}\n\n将使用「快速（本地解析）」导入。\n"
                    "可到 设置 → API 配置 填写 Base URL 与 API Key 后使用智能导入。",
                )
                self._smart_requested = False
        self._dropzone.setEnabled(False)
        self._mode_combo.setEnabled(False)
        self._progress_bar.setValue(0)
        self._progress_bar.show()
        self._progress_status.setText(
            "正在打开文件…" + ("（先显示本地解析结果，AI 精修将在后台进行）" if smart else ""))
        self._progress_status.show()

        self._worker = ImportWorker(path, self._db, smart=False, parent=self)
        self._worker.progress.connect(self._on_progress)
        self._worker.succeeded.connect(self._on_import_succeeded)
        self._worker.failed.connect(self._on_import_failed)
        self._worker.start()

    def was_smart(self) -> bool:
        """本次导入是否请求了智能精修（用于主窗口启动后台精修）。"""
        return bool(getattr(self, "_smart_requested", False))

    def _on_progress(self, percent: int, status: str) -> None:
        """更新进度条与状态文字。"""
        self._progress_bar.setValue(percent)
        self._progress_status.setText(status)

    def _on_fell_back(self, reason: str) -> None:
        """智能导入失败：回退本地解析并提示。"""
        self._progress_status.setText(
            f"AI 精修不可用（{reason[:120]}），已回退到本地解析…")

    def _on_import_succeeded(self, filename: str, count: int) -> None:
        """导入完成：恢复界面、刷新词库并通知主窗口跳转。"""
        self._dropzone.setEnabled(True)
        self._mode_combo.setEnabled(True)
        self._progress_bar.setValue(100)
        self._progress_status.setText(f"导入完成：{filename}（{count} 段）")
        self.set_current_file(filename)
        self.refresh_keyword_list()   # 智能导入可能自动新增了词条
        self.keywords_changed.emit()
        self.import_finished.emit(filename, count)

    def _on_import_failed(self, message: str) -> None:
        """导入失败提示。"""
        self._dropzone.setEnabled(True)
        self._mode_combo.setEnabled(True)
        self._progress_bar.hide()
        self._progress_status.hide()
        QMessageBox.warning(self, "导入失败", message)

    # ---- 词库 ----
    def set_current_file(self, file_name: str) -> None:
        """更新词库模组筛选，并默认选中当前模组。"""
        current = file_name or self._keyword_file_combo.currentData() or ""
        files = self._db.files()
        self._keyword_file_combo.blockSignals(True)
        self._keyword_file_combo.clear()
        if not files:
            self._keyword_file_combo.addItem("尚未导入模组", "")
        else:
            for name in files:
                self._keyword_file_combo.addItem(name, name)
            index = self._keyword_file_combo.findData(current)
            self._keyword_file_combo.setCurrentIndex(max(index, 0))
        self._keyword_file_combo.blockSignals(False)
        self.refresh_keyword_list()

    def _selected_keyword_file(self) -> str:
        """返回词库下拉框当前选择的模组名。"""
        return str(self._keyword_file_combo.currentData() or "")

    def refresh_keyword_list(self) -> None:
        """刷新词库列表（按类型着色）。"""
        self._keyword_list.clear()
        for kw in self._db.get_keywords(self._selected_keyword_file()):
            color = KIND_COLORS.get(kw["kind"], "#22D3EE")
            label = KIND_LABELS.get(kw["kind"], kw["kind"])
            item = QListWidgetItem(f"{kw['name']}  [{label}]")
            item.setForeground(QColor(color))
            item.setToolTip(kw["detail"] or "暂无详细信息")
            self._keyword_list.addItem(item)

    def _on_add_keyword(self) -> None:
        """添加关键词并通知刷新高亮。"""
        name = self._kw_name.text().strip()
        if not name:
            return
        file_name = self._selected_keyword_file()
        if not file_name:
            QMessageBox.information(self, "提示", "请先选择一个已导入的模组。")
            return
        ok = self._db.add_keyword(name, self._kw_kind.currentData(),
                                  self._kw_detail.text().strip(), file_name)
        if not ok:
            QMessageBox.information(self, "提示", "该词条已存在或类型无效。")
            return
        self._kw_name.clear()
        self._kw_detail.clear()
        self.refresh_keyword_list()
        self.keywords_changed.emit()

    def _on_remove_keyword(self) -> None:
        """删除选中的关键词。"""
        item = self._keyword_list.currentItem()
        if item is None:
            return
        name = item.text().split("  [")[0]
        self._db.remove_keyword(name, self._selected_keyword_file())
        self.refresh_keyword_list()
        self.keywords_changed.emit()
