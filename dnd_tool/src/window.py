"""主窗口：三栏布局（导航栏 / 工作区 / 信息面板）+ 背景图 + 主题切换。"""
from pathlib import Path

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPixmap
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QVBoxLayout, QWidget,
)

from src.modules.database import Database
from src.pages.import_page import ImportPage
from src.pages.npc_page import NpcPage
from src.pages.settings_page import (
    DEFAULT_BACKGROUND_PATH, SettingsManager, SettingsPage,
)
from src.pages.translate_page import TranslatePage
from src.widgets.comparison_view import ComparisonView
from src.widgets.info_panel import InfoPanel
from src.widgets.markdown_view import set_active_scheme
from src.widgets.nav_panel import NavPanel
from src.widgets.workspace import WelcomePage, Workspace

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class BackgroundWidget(QWidget):
    """窗口背景容器：绘制背景图（cover/tile/stretch）与半透明遮罩。"""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("BackgroundWidget")
        self._pixmap: QPixmap | None = None
        self._mode = "cover"
        self._base_color = QColor("#121419")
        self._overlay_color = QColor("#121419")
        self._overlay_alpha = 153  # 0-255，默认 60% => 153

    # ---- 对外设置接口 ----
    def set_background(self, path: str | Path | None) -> None:
        """设置背景图片路径；传入 None 表示仅使用纯色背景。"""
        if path:
            pix = QPixmap(str(path))
            self._pixmap = pix if not pix.isNull() else None
        else:
            self._pixmap = None
        self.update()

    def set_mode(self, mode: str) -> None:
        """设置背景显示模式：cover / tile / stretch。"""
        if mode not in ("cover", "tile", "stretch"):
            mode = "cover"
        self._mode = mode
        self.update()

    def set_overlay_alpha(self, percent: int) -> None:
        """设置遮罩不透明度（0-100，百分比）。"""
        self._overlay_alpha = max(0, min(100, int(percent))) * 255 // 100
        self.update()

    def set_base_color(self, color: QColor) -> None:
        """设置背景底色（无图片或遮罩之下的颜色）。"""
        self._base_color = QColor(color)
        self.update()

    def set_overlay_color(self, color: QColor) -> None:
        """设置遮罩颜色（随主题切换明暗）。"""
        self._overlay_color = QColor(color)
        self.update()

    # ---- 绘制 ----
    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802  Qt 命名约定
        """绘制背景：底色 -> 图片 -> 半透明遮罩。"""
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.fillRect(self.rect(), self._base_color)
        if self._pixmap is not None:
            self._draw_pixmap(painter)
        overlay = QColor(
            self._overlay_color.red(),
            self._overlay_color.green(),
            self._overlay_color.blue(),
            self._overlay_alpha,
        )
        painter.fillRect(self.rect(), overlay)
        painter.end()

    def _draw_pixmap(self, painter: QPainter) -> None:
        """按当前模式绘制背景图。"""
        rect = self.rect()
        if self._mode == "stretch":
            painter.drawPixmap(rect, self._pixmap)
        elif self._mode == "tile":
            pw, ph = self._pixmap.width(), self._pixmap.height()
            for y in range(0, rect.height(), ph):
                for x in range(0, rect.width(), pw):
                    painter.drawPixmap(x, y, self._pixmap)
        else:  # cover：等比放大并居中裁剪
            scaled = self._pixmap.scaled(
                rect.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
            )
            x = (scaled.width() - rect.width()) // 2
            y = (scaled.height() - rect.height()) // 2
            painter.drawPixmap(rect, scaled, QRect(x, y, rect.width(), rect.height()))


class MainWindow(QMainWindow):
    """GM Copilot 主窗口：导航栏 + 工作区 + 信息面板三栏布局。"""

    def __init__(self, account):
        super().__init__()
        from src.modules.database import configure_default_path
        from src.modules.document_exporter import configure_output_dir

        self._account = account
        SettingsManager.configure_path(account.settings_path)
        configure_default_path(account.database_path)
        configure_output_dir(account.outputs_dir)
        self.setWindowTitle("GM Copilot — DND 跑团模组辅助工具")
        self.resize(1280, 800)
        self._theme = "dark"
        self._settings = SettingsManager()
        self.db = Database()
        self._current_file = ""
        self._build_ui()
        self._load_persisted()
        self._connect_signals()
        self._restore_last_import()
        self.nav.set_account_status(self._account.username)
        self.nav.set_ai_enabled(self._account.is_logged_in)
        if self._account.is_logged_in:
            QTimer.singleShot(400, self._warmup_network)

    def _on_logout_requested(self) -> None:
        """清除当前账户数据并返回注册/登录界面。"""
        if not self._account.is_logged_in:
            return
        for worker_name in ("_expand_worker", "_refine_worker"):
            worker = getattr(self, worker_name, None)
            if worker is not None and worker.isRunning():
                worker.requestInterruption()
                worker.wait(1500)
        self.db.close()
        self._account.delete_current_account()
        from src.pages.account_dialog import AccountDialog

        dialog = AccountDialog(self._account, self)
        if dialog.exec() == AccountDialog.Accepted:
            self._replacement_window = MainWindow(self._account)
            self._replacement_window.show()
        self.close()

    # ---- 网络预热 ----
    def _warmup_network(self) -> None:
        """后台线程执行一次轻量 API 请求，避免阻塞主线程 UI。

        原实现（主线程同步 warmup）会在 API 响应慢时冻结整个界面；
        改为后台线程 + 短超时（warmup 默认 4 秒读取），仅用于解锁
        进程内首次 HTTPS。
        """
        import threading

        def _do_warmup() -> None:
            try:
                from src.modules.llm_client import LLMClient

                LLMClient().warmup()
            except Exception:  # noqa: BLE001  预热失败不影响使用
                pass

        threading.Thread(target=_do_warmup, daemon=True).start()

    # ---- UI 构建 ----
    def _build_ui(self) -> None:
        """构建三栏布局与页面注册。"""
        self.bg = BackgroundWidget(self)
        self.setCentralWidget(self.bg)

        self.nav = NavPanel(self)
        self.workspace = Workspace(self)
        self.info = InfoPanel(self)

        # 顶部：扩写进度条（平时隐藏）
        self._build_expand_bar()

        outer = QVBoxLayout(self.bg)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(8)
        outer.addWidget(self._expand_bar)

        hbox = QHBoxLayout()
        hbox.setSpacing(12)
        hbox.addWidget(self.nav)
        hbox.addWidget(self.workspace, 1)
        hbox.addWidget(self.info)
        outer.addLayout(hbox, 1)

        # 中央工作区页面注册
        self.workspace.add_page("welcome", WelcomePage(self.workspace))
        self.import_page = ImportPage(self.db, self.workspace)
        self.import_page.set_ai_enabled(self._account.is_logged_in)
        self.workspace.add_page("import", self.import_page)
        self.comparison = ComparisonView(self.db, self.workspace)
        self.workspace.add_page("comparison", self.comparison)
        self.translate_page = TranslatePage(self.db, self.workspace)
        self.workspace.add_page("translate", self.translate_page)
        self.npc_page = NpcPage(self.db, self.workspace)
        self.workspace.add_page("npc", self.npc_page)
        self.settings_page = SettingsPage(
            self._settings, self.workspace, api_enabled=self._account.is_logged_in)
        self.workspace.add_page("settings", self.settings_page)
        self.workspace.show_page("welcome")

    def _build_expand_bar(self) -> None:
        """顶部扩写进度条：总进度 + 当前段落 + 流式文字 + 控制按钮。"""
        from PySide6.QtWidgets import QProgressBar, QPushButton

        self._expand_bar = QFrame()
        self._expand_bar.setObjectName("ExpandBar")
        self._expand_bar.setAttribute(Qt.WA_StyledBackground, True)
        bar_layout = QVBoxLayout(self._expand_bar)
        bar_layout.setContentsMargins(16, 10, 16, 10)
        bar_layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(10)
        self._expand_title = QLabel("AI 扩写")
        self._expand_title.setObjectName("CardTitle")
        self._expand_progress = QProgressBar()
        self._expand_progress.setRange(0, 100)
        self._expand_progress.setValue(0)
        self._expand_status = QLabel("准备中…")
        self._expand_status.setObjectName("DropHint")
        self._expand_pause_btn = QPushButton("暂停")
        self._expand_export_btn = QPushButton("导出完整文档")
        self._expand_cancel_btn = QPushButton("停止")
        row.addWidget(self._expand_title)
        row.addWidget(self._expand_progress, 1)
        row.addWidget(self._expand_status)
        row.addWidget(self._expand_pause_btn)
        row.addWidget(self._expand_cancel_btn)
        row.addWidget(self._expand_export_btn)
        bar_layout.addLayout(row)

        self._expand_stream = QLabel("")
        self._expand_stream.setObjectName("DropHint")
        self._expand_stream.setWordWrap(True)
        self._expand_stream.setMaximumHeight(54)
        bar_layout.addWidget(self._expand_stream)

        # 总是询问模式：版本确认按钮行
        self._expand_confirm_row = QHBoxLayout()
        self._expand_confirm_row.setSpacing(8)
        confirm_hint = QLabel("请选择采纳的扩写版本：")
        confirm_hint.setObjectName("DropHint")
        self._expand_confirm_row.addWidget(confirm_hint)
        self._expand_confirm_buttons: list[QPushButton] = []
        for i in range(1, 4):
            btn = QPushButton(f"采纳版本 {i}")
            btn.clicked.connect(lambda _=False, idx=i - 1: self._on_expand_confirm(idx))
            self._expand_confirm_buttons.append(btn)
            self._expand_confirm_row.addWidget(btn)
        self._expand_confirm_row.addStretch(1)
        bar_layout.addLayout(self._expand_confirm_row)
        self._set_confirm_row_visible(False)

        self._expand_pause_btn.clicked.connect(self._on_expand_pause)
        self._expand_cancel_btn.clicked.connect(self._on_expand_cancel)
        self._expand_export_btn.clicked.connect(self._on_export_clicked)
        self._expand_bar.hide()

    def _set_confirm_row_visible(self, visible: bool) -> None:
        """显示/隐藏版本确认按钮行。"""
        for i in range(self._expand_confirm_row.count()):
            item = self._expand_confirm_row.itemAt(i)
            if item.widget():
                item.widget().setVisible(visible)

    def _connect_signals(self) -> None:
        """连接导航、页面切换与设置信号。"""
        self.nav.navigate.connect(self.workspace.show_page)
        self.workspace.page_changed.connect(self.nav.set_active)
        self.settings_page.theme_changed.connect(self._apply_theme)
        self.settings_page.background_changed.connect(self._apply_background)
        self.settings_page.logout_requested.connect(self._on_logout_requested)
        self.import_page.import_finished.connect(self._on_import_finished)
        self.import_page.keywords_changed.connect(self._on_keywords_changed)
        self.nav.chapter_selected.connect(self._on_chapter_selected)
        self.nav.module_selected.connect(self._on_module_selected)
        self.nav.module_delete_requested.connect(self._on_module_delete)
        self.translate_page.segments_changed.connect(self._on_translate_segments_changed)
        self.translate_page.fixes_ready.connect(self._on_fixes_ready)
        self.comparison.npc_hovered.connect(self._on_npc_hovered)
        self.info.npc_edit_requested.connect(self._on_npc_edit_requested)
        self.npc_page.npcs_changed.connect(self._on_npcs_changed)

    # ---- 扩写流水线 ----
    def _start_expansion(self, file_name: str = "") -> None:
        """自动触发扩写：导入清洗完成后调用。"""
        file_name = file_name or self._current_file
        if not file_name:
            return
        if getattr(self, "_expand_worker", None) is not None \
                and self._expand_worker.isRunning():
            return
        from src.core.expansion_worker import ExpansionWorker
        from src.pages.settings_page import SettingsManager

        sm = SettingsManager()
        ask_each = bool(sm.get("ask_each_version", False))
        num_versions = int(sm.get("num_versions", 3) or 3)
        self._expand_worker = ExpansionWorker(
            file_name, self.db, ask_each=ask_each, num_versions=num_versions, parent=self)
        worker = self._expand_worker
        worker.progress.connect(self._on_expand_progress)
        worker.stream.connect(self._on_expand_stream)
        worker.segment_done.connect(self._on_expand_segment_done)
        worker.awaiting_confirm.connect(self._on_expand_awaiting)
        worker.finished_ok.connect(self._on_expand_finished)
        worker.failed.connect(self._on_expand_failed)
        self._expand_bar.show()
        self._expand_progress.setValue(0)
        self._expand_status.setText("正在分析分段…")
        self._expand_stream.setText("")
        worker.start()

    def _on_expand_progress(self, done: int, total: int, status: str) -> None:
        """更新总进度。"""
        if total:
            self._expand_progress.setValue(int(done * 100 / total))
        self._expand_status.setText(f"{status}（{done}/{total}）")

    def _on_expand_stream(self, delta: str) -> None:
        """打字机效果：追加当前流式文本（截断过长）。"""
        text = self._expand_stream.text() + delta
        if len(text) > 200:
            text = text[-200:]
        self._expand_stream.setText(text)

    def _on_expand_segment_done(self, segment_id: int, versions: list) -> None:
        """一段扩写完成：刷新对照视图对应段。"""
        self._expand_stream.setText("")
        if self._current_file:
            self.comparison.refresh_segment_expansion(segment_id)

    def _on_expand_awaiting(self, segment_id: int, versions: list) -> None:
        """总是询问模式：显示版本采纳按钮。"""
        for i, btn in enumerate(self._expand_confirm_buttons):
            btn.setVisible(i < len(versions))
        self._set_confirm_row_visible(True)

    def _on_expand_confirm(self, version_index: int) -> None:
        """用户采纳版本。"""
        self._set_confirm_row_visible(False)
        if getattr(self, "_expand_worker", None) is not None:
            self._expand_worker.confirm_version(version_index)

    def _on_expand_finished(self, file_name: str, count: int) -> None:
        """扩写完成：导出文档并跳转对照视图（部分失败时仍导出已完成段）。"""
        self._set_confirm_row_visible(False)
        partial = ""
        worker = getattr(self, "_expand_worker", None)
        if worker is not None and getattr(worker, "_partial_error", ""):
            partial = worker._partial_error
        self._expand_status.setText(
            f"扩写完成：{count} 段已生成多版本"
            + ("（部分段落失败）" if partial else ""))
        self._expand_pause_btn.setText("暂停")
        if count:
            try:
                from src.modules.document_exporter import export_expanded_document
                from src.modules.llm_client import build_api_config

                path = export_expanded_document(
                    self.db, file_name, model=build_api_config().get("model", ""))
                self._expand_export_btn.setToolTip(str(path))
                self._expand_status.setText(
                    f"扩写完成，已导出：{path.name}"
                    + ("（部分失败）" if partial else ""))
                QMessageBox.information(
                    self, "扩写完成",
                    f"已为 {count} 个段落生成扩写版本。\n\n"
                    f"完整文档已导出到：\n{path}"
                    + (f"\n\n注意：{partial[:200]}" if partial else ""))
            except Exception as exc:  # noqa: BLE001  导出失败不影响提示
                self._expand_status.setText(f"扩写完成（导出失败：{exc}）")
        self.workspace.show_page("comparison")
        self.comparison.refresh_expansions()
    def _on_expand_failed(self, message: str) -> None:
        """扩写失败（如 API 不可用）。"""
        self._set_confirm_row_visible(False)
        self._expand_status.setText(f"扩写失败：{message[:120]}")
        QMessageBox.warning(self, "扩写失败", message[:400])

    def _on_expand_pause(self) -> None:
        """暂停/继续切换。"""
        worker = getattr(self, "_expand_worker", None)
        if worker is None:
            return
        if worker.is_paused():
            worker.resume()
            self._expand_pause_btn.setText("暂停")
            self._expand_status.setText("继续扩写…")
        else:
            worker.pause()
            self._expand_pause_btn.setText("继续")
            self._expand_status.setText("已暂停（可随时继续）")

    def _on_expand_cancel(self) -> None:
        """停止扩写（已完成段落保留，断点续传）。"""
        worker = getattr(self, "_expand_worker", None)
        if worker is not None:
            worker._cancelled = True
            worker.cancel_ask()
            worker.resume()
        self._expand_status.setText("已停止（下次导入或重启后可从断点继续）")
        self._set_confirm_row_visible(False)

    def _on_export_clicked(self) -> None:
        """手动导出完整扩写文档。"""
        if not self._current_file:
            QMessageBox.information(self, "提示", "尚未导入模组。")
            return
        try:
            from src.modules.document_exporter import export_expanded_document
            from src.modules.llm_client import build_api_config

            path = export_expanded_document(
                self.db, self._current_file, model=build_api_config().get("model", ""))
            QMessageBox.information(self, "导出成功", f"已导出到：\n{path}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(exc))

    # ---- 导入联动 ----
    def _restore_last_import(self) -> None:
        """启动时恢复上次导入的文件（不自动跳页）。"""
        last_file = self._settings.get("last_imported_file", "")
        if last_file and last_file in self.db.files():
            self._current_file = last_file
            segments = self.db.get_segments(last_file)
            self.comparison.load_segments(segments, last_file)
            self.import_page.set_current_file(last_file)
            self.translate_page.set_current_file(last_file)
            self.npc_page.set_current_file(last_file)
        self._refresh_nav_modules()
        self._sync_npc_keywords()
        if self._current_file:
            self.nav.set_current_module(self._current_file)

    def _refresh_nav_modules(self) -> None:
        """按数据库重建侧边栏模组目录树。"""
        modules = []
        for file_name in self.db.files():
            segments = self.db.get_segments(file_name)
            modules.append({
                "file": file_name,
                "chapters": self._chapters_of(segments),
            })
        self.nav.set_modules(modules)
        if self._current_file:
            self.nav.set_current_module(self._current_file)

    def _on_import_finished(self, filename: str, count: int) -> None:
        """导入完成：加载对照视图、刷新目录树并自动跳转。"""
        self._current_file = filename
        segments = self.db.get_segments(filename)
        self.comparison.load_segments(segments, filename)
        self.import_page.set_current_file(filename)
        self.translate_page.set_current_file(filename)
        self.npc_page.set_current_file(filename)
        self._refresh_nav_modules()
        self._settings.set("last_imported_file", filename)
        self.workspace.show_page("comparison")
        if self._account.is_logged_in:
            if self.import_page.was_smart():
                QTimer.singleShot(300, lambda: self._start_refine(filename))
            else:
                QTimer.singleShot(600, lambda: self._start_expansion(filename))

    # ---- 后台精修（智能导入） ----
    def _start_refine(self, file_name: str) -> None:
        """后台 AI 精修：并发批量清洗，完成后替换分段并触发扩写。"""
        from src.core.refine_worker import RefineWorker

        self._refine_worker = RefineWorker(file_name, self.db, parent=self)
        worker = self._refine_worker
        worker.progress.connect(self._on_refine_progress)
        worker.finished.connect(self._on_refine_finished)
        worker.failed.connect(self._on_refine_failed)
        self._expand_title.setText("AI 精修")
        self._expand_bar.show()
        self._expand_progress.setValue(0)
        self._expand_status.setText("正在提取文本…")
        self._expand_stream.setText("")
        self._expand_pause_btn.setEnabled(False)
        worker.start()

    def _on_refine_progress(self, pct: int, status: str) -> None:
        """精修进度。"""
        self._expand_progress.setValue(pct)
        self._expand_status.setText(status)

    def _on_refine_finished(self, file_name: str, count: int) -> None:
        """精修完成：刷新对照/目录，清除旧扩写后重新扩写。"""
        if file_name == self._current_file:
            self.comparison.load_segments(self.db.get_segments(file_name), file_name)
            self.comparison.refresh_keywords()
            self.translate_page.set_current_file(file_name)
        self._refresh_nav_modules()
        self._expand_title.setText("AI 扩写")
        self._expand_status.setText(f"AI 精修完成（{count} 段），开始自动扩写…")
        self._expand_pause_btn.setEnabled(True)
        # 精修替换了分段，旧扩写版本失效，清除后重新扩写
        self.db.clear_expanded(file_name)
        self.db.clear_task_progress(file_name=file_name)
        QTimer.singleShot(400, lambda: self._start_expansion(file_name))

    def _on_refine_failed(self, message: str) -> None:
        """精修失败：保留本地解析结果，仍按原文本扩写。"""
        self._expand_title.setText("AI 扩写")
        self._expand_status.setText(f"AI 精修失败：{message[:100]}（保留本地解析结果）")
        self._expand_pause_btn.setEnabled(True)
        QTimer.singleShot(400, lambda: self._start_expansion(self._current_file))

    def _switch_module(self, file_name: str) -> None:
        """切换当前模组：同步各功能页面与导航高亮。

        所有功能（对照视图/翻译/NPC）都作用于当前模组。
        """
        if not file_name or file_name == self._current_file:
            return
        self._current_file = file_name
        self.comparison.load_segments(self.db.get_segments(file_name), file_name)
        self.import_page.set_current_file(file_name)
        self.translate_page.set_current_file(file_name)
        self.npc_page.set_current_file(file_name)
        self._refresh_nav_modules()
        self.nav.set_current_module(file_name)
        self._settings.set("last_imported_file", file_name)

    def _on_module_selected(self, file_name: str) -> None:
        """点击导航树模组节点：切换当前模组（不跳页）。"""
        self._switch_module(file_name)

    def _on_chapter_selected(self, file_name: str, chapter: str) -> None:
        """点击章节：必要时切换模组，展开对应段落并跳转。"""
        if file_name != self._current_file:
            self._switch_module(file_name)
        self.comparison.show_chapter(chapter)
        self.workspace.show_page("comparison")

    def _on_module_delete(self, file_name: str) -> None:
        """删除某模组的导入记录（数据库 + 界面）。"""
        reply = QMessageBox.question(
            self, "删除模组",
            f"确定删除「{file_name}」的导入记录吗？\n（数据库中的分段与章节将一并移除）",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.db.clear_file(file_name)
        self._refresh_nav_modules()
        self.import_page.set_current_file(self._current_file if file_name != self._current_file else "")
        if file_name == self._current_file:
            # 删除的是当前模组：自动切换到剩余的第一个模组，或清空
            remaining = self.db.files()
            if remaining:
                self._switch_module(remaining[0])
            else:
                self._current_file = ""
                self.comparison.clear()
                self.import_page.set_current_file("")
                self.translate_page.set_current_file("")
                self.npc_page.set_current_file("")
                self._settings.set("last_imported_file", "")
        self.workspace.show_page("welcome" if not self._current_file else "comparison")
        self.import_page.set_current_file(self._current_file)

    def _on_keywords_changed(self) -> None:
        """词库变化：刷新对照视图高亮。"""
        self.comparison.refresh_keywords()

    # ---- NPC 联动 ----
    def _on_npc_hovered(self, name: str) -> None:
        """悬停原文中的 NPC 名称：信息面板显示完整卡片。"""
        npc = self.db.get_npc(name)
        if not npc:
            return
        dialogues = self.db.get_npc_dialogues(name)
        line = dialogues[0]["line"] if dialogues else ""
        self.info.show_npc_card(npc, line)

    def _on_npc_edit_requested(self, name: str) -> None:
        """点击信息面板 NPC 卡片的编辑按钮：跳转 NPC 页并选中该 NPC。"""
        self.workspace.show_page("npc")
        self.npc_page.select_npc_by_name(name)

    def _on_npcs_changed(self) -> None:
        """NPC 档案变化（扫描完成）：把 NPC 名称并入关键词库用于高亮。"""
        self._sync_npc_keywords()

    def _sync_npc_keywords(self) -> None:
        """把当前模组 NPC 名称同步为该模组的高亮关键词。"""
        if not self._current_file:
            return
        for npc in self.db.get_npcs(self._current_file):
            if npc.get("name"):
                self.db.add_keyword(npc["name"], "npc",
                                    npc.get("motivation") or "", self._current_file)
        self.comparison.refresh_keywords()

    # ---- 翻译优化联动 ----
    def _on_translate_segments_changed(self) -> None:
        """术语/隐喻替换后重载对照视图。"""
        if self._current_file:
            self.comparison.load_segments(
                self.db.get_segments(self._current_file), self._current_file)
            self._refresh_nav_modules()

    def _on_fixes_ready(self, fixes_by_chapter: dict) -> None:
        """翻译腔改写完成：展示修改对照并跳转。"""
        self.comparison.set_fixes(fixes_by_chapter)
        self.workspace.show_page("comparison")
        # 展开有修改的第一章
        if fixes_by_chapter:
            self.comparison.show_chapter(next(iter(fixes_by_chapter)))

    @staticmethod
    def _chapters_of(segments: list[dict]) -> list[str]:
        """提取分段中的章节列表（保持出现顺序）。"""
        seen: list[str] = []
        for seg in segments:
            chapter = seg.get("chapter", "未分章")
            if chapter not in seen:
                seen.append(chapter)
        return seen

    # ---- 设置加载与主题/背景 ----
    def _load_persisted(self) -> None:
        """读取上次保存的主题与背景配置。"""
        theme = self._settings.get("theme", "dark")
        self._apply_theme(theme)
        background = self._settings.get("background", {})
        self._apply_background(background)

    def _apply_theme(self, name: str) -> None:
        """应用主题：加载 QSS 并同步背景底色、遮罩与 Markdown 配色。"""
        if name not in ("dark", "light"):
            name = "dark"
        self._theme = name
        qss_path = PROJECT_ROOT / "src" / "theme" / f"{name}.qss"
        try:
            qss = qss_path.read_text(encoding="utf-8")
        except OSError:
            qss = ""
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(qss)
        set_active_scheme(name)
        if name == "dark":
            base = QColor("#121419")
        else:
            base = QColor("#F5F1E8")
        self.bg.set_base_color(base)
        self.bg.set_overlay_color(base)
        # 重新渲染对照视图以匹配新配色
        if hasattr(self, "comparison") and self.comparison is not None:
            self.comparison.refresh_keywords()

    def _apply_background(self, background: dict) -> None:
        """应用背景配置：{path, mode, opacity}，path 为空时使用内置默认图。"""
        path = background.get("path") or DEFAULT_BACKGROUND_PATH
        bg_path = PROJECT_ROOT / path
        if bg_path.exists():
            self.bg.set_background(bg_path)
        self.bg.set_mode(str(background.get("mode", "cover")))
        self.bg.set_overlay_alpha(int(background.get("opacity", 60)))
