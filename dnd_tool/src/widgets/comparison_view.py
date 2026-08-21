"""原文对照视图：左栏原文（可折叠分段），右栏扩写结果/翻译腔改写对照。"""
import html
import re

from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QMenu, QPushButton,
    QScrollArea, QSplitter, QToolButton, QVBoxLayout, QWidget, QWidgetAction,
)

from src.widgets.keyword_highlight import KeywordHighlighter
from src.widgets.markdown_view import MarkdownView

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?])\s*")

# 修改状态高亮底色（随主题可读）
_FIX_COLOR_PENDING = "rgba(255, 199, 90, 0.42)"
_FIX_COLOR_ACCEPTED = "rgba(74, 222, 128, 0.35)"
_FIX_COLOR_EDITED = "rgba(96, 165, 250, 0.40)"


def _first_sentence(text: str, limit: int = 60) -> str:
    """提取首句摘要。"""
    text = text.strip().replace("\n", " ")
    pieces = _SENTENCE_SPLIT.split(text)
    summary = pieces[0] if pieces else text
    return (summary[:limit] + "…") if len(summary) > limit else summary


class SegmentWidget(QFrame):
    """单个可折叠段落：箭头 + 标题/摘要 + 正文（Markdown）。"""

    clicked = Signal(int, str, str)  # segment_id, chapter, content
    npc_hovered = Signal(str)        # 悬停正文中的 NPC 名称

    def __init__(self, index: int, chapter: str, content: str,
                 highlighter: KeywordHighlighter, resolver,
                 segment_id: int = 0, parent=None):
        super().__init__(parent)
        self.setObjectName("SegmentCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._collapsed = False
        self._segment_id = segment_id
        self._chapter = chapter
        self._content = content
        self.setCursor(Qt.PointingHandCursor)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)

        # 头部：箭头 + 章节/标题 + 首句摘要
        header = QHBoxLayout()
        header.setSpacing(8)
        self._arrow = QToolButton()
        self._arrow.setObjectName("FoldArrow")
        self._arrow.setText("▾")
        self._arrow.setCursor(Qt.PointingHandCursor)
        self._arrow.setAutoRaise(True)
        title = QLabel(f"{chapter} · 第 {index} 段")
        title.setObjectName("SegmentTitle")
        title.setWordWrap(True)
        summary = QLabel(_first_sentence(content))
        summary.setObjectName("SegmentSummary")
        summary.setWordWrap(True)
        header.addWidget(self._arrow)
        header.addWidget(title, 0)
        header.addWidget(summary, 1)
        root.addLayout(header)

        # 正文
        self._body = MarkdownView(self)
        self._body.set_keyword_resolver(resolver)
        self._body.set_auto_height(True)
        self._body.npc_hovered.connect(self.npc_hovered)
        self._body.content_clicked.connect(
            lambda: self.clicked.emit(self._segment_id, self._chapter, self._content))
        self._body.set_markdown(content, highlighter)
        root.addWidget(self._body)

        self._arrow.clicked.connect(self.toggle)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        """点击卡片：选中该段（查看扩写版本）。"""
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self._segment_id, self._chapter, self._content)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        """高亮当前选中的段落卡片。"""
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def showEvent(self, event) -> None:  # noqa: N802  Qt 命名约定
        """首次显示时激活内部布局，确保正文宽度与卡片一致。"""
        super().showEvent(event)
        if self.layout() is not None:
            self.layout().activate()
        if not self._collapsed:
            QTimer.singleShot(0, self._body._refit)

    def toggle(self) -> None:
        """折叠/展开本段。"""
        self.set_collapsed(not self._collapsed)

    def set_collapsed(self, collapsed: bool) -> None:
        """设置折叠状态：仅显示标题与首句摘要。"""
        self._collapsed = collapsed
        self._arrow.setText("▸" if collapsed else "▾")
        self._body.setVisible(not collapsed)
        if not collapsed:
            # 展开后重排正文高度（隐藏期间不触发 resizeEvent）
            QTimer.singleShot(0, self._body._refit)

    def resizeEvent(self, event) -> None:  # noqa: N802  Qt 命名约定
        """宽度变化时重排正文，保证文本按新宽度换行。"""
        super().resizeEvent(event)
        if not self._collapsed and self.width() > 0:
            QTimer.singleShot(0, self._body._refit)


class ComparisonView(QWidget):
    """原文对照：左栏原文档分段列表，右栏扩写结果（后续阶段填充）。"""

    expansion_changed = Signal()
    npc_hovered = Signal(str)  # 悬停原文中的 NPC 名称（转发自 MarkdownView）

    def __init__(self, db, parent: QWidget | None = None):
        super().__init__(parent)
        self._db = db
        self._file = ""
        self._segments: list[dict] = []
        self._segment_widgets: list[SegmentWidget] = []
        self._highlighter = KeywordHighlighter()
        self._highlighter.set_keywords(self._db.get_keywords())
        # 翻译腔改写：{chapter: [{original, fixed, status, edited}]}
        self._fixes: dict[str, list[dict]] = {}
        self._fix_chapter = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        # 头部
        header = QHBoxLayout()
        header.setSpacing(12)
        self._title = QLabel("原文对照")
        self._title.setObjectName("PageTitle")
        self._meta = QLabel("尚未导入模组")
        self._meta.setObjectName("PageSubtitle")
        header.addWidget(self._title)
        header.addWidget(self._meta, 1)
        root.addLayout(header)

        # 左右分栏
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(12)

        # 左栏：原文分段滚动列表
        self._left_scroll = QScrollArea()
        self._left_scroll.setWidgetResizable(True)
        self._left_scroll.setFrameShape(QScrollArea.NoFrame)
        self._left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._left_scroll.setMinimumWidth(300)
        self._left_container = QWidget()
        self._left_layout = QVBoxLayout(self._left_container)
        self._left_layout.setContentsMargins(0, 0, 8, 0)
        self._left_layout.setSpacing(10)
        self._left_layout.addStretch(1)
        self._left_scroll.setWidget(self._left_container)

        # 右栏：扩写结果 / 翻译腔改写对照 / 扩写版本
        self._right_card = QFrame()
        self._right_card.setObjectName("Card")
        self._right_card.setMinimumWidth(200)
        right_layout = QVBoxLayout(self._right_card)
        right_layout.setContentsMargins(18, 16, 18, 16)
        right_layout.setSpacing(10)
        self._right_title = QLabel("扩写结果")
        self._right_title.setObjectName("CardTitle")
        # 版本行：下拉切换 + 采纳按钮
        version_row = QHBoxLayout()
        version_row.setSpacing(8)
        version_label = QLabel("版本：")
        version_label.setObjectName("DropHint")
        self._version_combo = QComboBox()
        self._version_combo.setEnabled(False)
        self._adopt_btn = QPushButton("采纳此版本")
        self._adopt_btn.setEnabled(False)
        self._adopt_btn.setObjectName("PrimaryButton")
        self._adopt_btn.setToolTip("把当前显示版本设为该段采纳的扩写")
        version_row.addWidget(version_label)
        version_row.addWidget(self._version_combo)
        version_row.addWidget(self._adopt_btn)
        version_row.addStretch(1)
        self._right_view = MarkdownView(self._right_card)
        self._right_view.setMinimumWidth(0)
        self._right_view.npc_hovered.connect(self.npc_hovered)
        self._right_view.set_markdown(
            "_尚未扩写。_\n\n导入模组后自动扩写；点击左栏段落可查看该段的扩写版本。"
        )
        self._right_view.fix_clicked.connect(self._on_fix_clicked)
        right_layout.addWidget(self._right_title)
        right_layout.addLayout(version_row)
        right_layout.addWidget(self._right_view, 1)

        self._version_combo.currentIndexChanged.connect(self._on_version_changed)
        self._adopt_btn.clicked.connect(self._on_adopt_clicked)
        self._selected_segment: dict | None = None
        self._selected_versions: list[dict] = []

        splitter.addWidget(self._left_scroll)
        splitter.addWidget(self._right_card)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([430, 220])
        root.addWidget(splitter, 1)

    # ---- 数据加载 ----
    def clear(self) -> None:
        """清空对照视图（删除模组记录后调用）。"""
        self._file = ""
        self._segments = []
        self._rebuild()
        self._right_view.set_markdown(
            "_尚未扩写。_\n\n导入模组后，可在此查看 AI 扩写结果（后续阶段开放）。"
        )

    def load_segments(self, segments: list[dict], original_file: str) -> None:
        """加载某文件的分段并重建左栏。"""
        self._file = original_file
        self._segments = list(segments)
        self._highlighter.set_keywords(self._db.get_keywords(original_file))
        self._fixes = {}
        self._fix_chapter = ""
        self._right_title.setText("扩写结果")
        self._rebuild()
        self._select_first_expanded_segment()

    def refresh_keywords(self) -> None:
        """词库变化后重新高亮渲染当前模组。"""
        self._highlighter.set_keywords(self._db.get_keywords(self._file))
        if self._segments:
            self._rebuild()

    def show_expansion(self, markdown: str) -> None:
        """右栏显示扩写结果（后续阶段调用）。"""
        self._fixes = {}
        self._fix_chapter = ""
        self._right_title.setText("扩写结果")
        self._right_view.set_markdown(markdown)

    # ---- 翻译腔改写：修改对照 ----
    def set_fixes(self, fixes_by_chapter: dict[str, list[dict]]) -> None:
        """接收每章的修改对（translation_fixer 输出）并展示。"""
        self._fixes = fixes_by_chapter
        self._right_title.setText("翻译腔改写对照（点击高亮处审阅）")
        chapter = self._current_fix_chapter()
        if chapter:
            self._render_right_panel(chapter)

    def _current_fix_chapter(self) -> str:
        """右栏当前应展示的章节：优先展开的章节，否则第一个有修改的章节。"""
        for seg in self._segments:
            ch = seg.get("chapter", "未分章")
            if ch in self._fixes:
                return ch
        return ""

    def _chapter_text(self, chapter: str) -> str:
        """拼接某章节的全部段落文本。"""
        parts = [seg["content"] for seg in self._segments
                 if seg.get("chapter") == chapter]
        return "\n\n".join(parts)

    def _render_right_panel(self, chapter: str) -> None:
        """把该章的修改对渲染为"修改后全文"（高亮标记 + fix 锚点）。"""
        fixes = self._fixes.get(chapter, [])
        if not fixes:
            return
        text = self._chapter_text(chapter)
        escaped = html.escape(text)
        for index, fix in enumerate(fixes):
            status = fix.get("status", "pending")
            display = fix.get("fixed", "")
            if fix.get("edited"):
                display = fix["edited"]
            if status == "rejected":
                continue  # 拒绝：保留原文，不高亮
            if status == "accepted":
                color = _FIX_COLOR_ACCEPTED
            elif status == "edited":
                color = _FIX_COLOR_EDITED
            else:
                color = _FIX_COLOR_PENDING
            span = (
                f'<a href="fix://{index}" style="background-color:{color};'
                f'color:#1C1917;text-decoration:none;border-radius:3px;'
                f'padding:1px 2px;" title="点击审阅此修改">{html.escape(display)}</a>'
            )
            escaped = escaped.replace(html.escape(fix.get("original", "")), span, 1)
        self._right_view.set_html(escaped)

    def _on_fix_clicked(self, fix_id: str) -> None:
        """点击修改锚点：弹出审阅气泡。"""
        chapter = self._current_fix_chapter()
        fixes = self._fixes.get(chapter, [])
        try:
            index = int(fix_id)
        except ValueError:
            return
        if not (0 <= index < len(fixes)):
            return
        pos = self._right_view.viewport().mapToGlobal(
            self._right_view.cursor().pos() + QPoint(8, 8))
        self._show_fix_bubble(chapter, index, pos)

    def _show_fix_bubble(self, chapter: str, index: int, pos: QPoint) -> None:
        """气泡：原文 vs 修改后 + 接受/拒绝/编辑。"""
        fixes = self._fixes.get(chapter, [])
        fix = fixes[index]
        status = fix.get("status", "pending")
        display = fix.get("edited") or fix.get("fixed", "")

        menu = QMenu(self)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        original_label = QLabel(f"原文：{fix.get('original', '')}")
        original_label.setWordWrap(True)
        original_label.setStyleSheet("color:#8A8F99;font-size:12px;")
        fixed_label = QLabel(f"修改后：{display}")
        fixed_label.setWordWrap(True)
        fixed_label.setStyleSheet("color:#E5E7EB;font-size:12px;")
        status_label = QLabel(f"状态：{status}")
        status_label.setStyleSheet("color:#22D3EE;font-size:11px;")
        layout.addWidget(original_label)
        layout.addWidget(fixed_label)
        layout.addWidget(status_label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        def make_button(text, on_click):
            from PySide6.QtWidgets import QPushButton
            btn = QPushButton(text)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(on_click)
            btn_row.addWidget(btn)
            return btn

        def accept():
            fix["status"] = "accepted"
            fix.pop("edited", None)
            menu.close()
            self._render_right_panel(chapter)

        def reject():
            fix["status"] = "rejected"
            fix.pop("edited", None)
            menu.close()
            self._render_right_panel(chapter)

        def edit():
            text, ok = QInputDialog.getText(
                self, "编辑修改", "修改后文本：", text=display)
            if ok and text.strip():
                fix["status"] = "edited"
                fix["edited"] = text.strip()
                menu.close()
                self._render_right_panel(chapter)

        make_button("接受", accept)
        make_button("拒绝", reject)
        make_button("编辑", edit)
        layout.addLayout(btn_row)

        action = QWidgetAction(menu)
        action.setDefaultWidget(container)
        menu.addAction(action)
        menu.exec(pos)

    # ---- 扩写版本展示 ----
    def _reset_expansion_panel(self) -> None:
        """清空右栏扩写面板。"""
        self._selected_segment = None
        self._selected_versions = []
        self._version_combo.clear()
        self._version_combo.setEnabled(False)
        self._adopt_btn.setEnabled(False)
        for widget in self._segment_widgets:
            widget.set_selected(False)

    def _select_first_expanded_segment(self) -> bool:
        """选中并展示第一个已有扩写版本的片段。"""
        for seg in self._segments:
            segment_id = seg.get("id", 0)
            versions = self._db.get_expanded_versions(segment_id)
            if not versions:
                continue
            self._on_segment_clicked(
                segment_id,
                seg.get("chapter", "未分章"),
                seg.get("content", ""),
            )
            return True
        return False

    def _on_segment_clicked(self, segment_id: int, chapter: str, content: str) -> None:
        """点击段落：在右栏展示其扩写版本。"""
        for widget, seg in zip(self._segment_widgets, self._segments):
            widget.set_selected(seg.get("id") == segment_id)
        self._selected_segment = {
            "id": segment_id, "chapter": chapter, "content": content}
        self._selected_versions = self._db.get_expanded_versions(segment_id)
        self._render_expansion_panel()

    def _render_expansion_panel(self) -> None:
        """渲染右栏：版本下拉 + 内容 + 采纳按钮。"""
        seg = self._selected_segment
        self._version_combo.blockSignals(True)
        self._version_combo.clear()
        if seg is None:
            self._version_combo.setEnabled(False)
            self._adopt_btn.setEnabled(False)
            self._version_combo.blockSignals(False)
            return
        if self._selected_versions:
            for v in self._selected_versions:
                self._version_combo.addItem(f"版本 {v['version_index'] + 1}")
            # 选中当前采纳版本
            selected_index = next(
                (i for i, v in enumerate(self._selected_versions) if v["is_selected"]), 0)
            self._version_combo.setCurrentIndex(selected_index)
            self._version_combo.setEnabled(True)
            self._adopt_btn.setEnabled(True)
            content = self._selected_versions[selected_index]["content"]
            self._right_title.setText(f"扩写：{seg['chapter']} · 第 {seg['id']} 段")
            self._right_view.set_markdown(content)
        else:
            self._version_combo.setEnabled(False)
            self._adopt_btn.setEnabled(False)
            self._right_title.setText(f"原文：{seg['chapter']} · 第 {seg['id']} 段")
            self._right_view.set_markdown(
                f"{seg['content']}\n\n_（该段暂无扩写版本：可能无需扩写或尚未生成）_"
            )
        self._version_combo.blockSignals(False)

    def _on_version_changed(self, index: int) -> None:
        """切换版本下拉：显示对应版本。"""
        if not self._selected_versions or index < 0:
            return
        content = self._selected_versions[index]["content"]
        self._right_view.set_markdown(content)

    def _on_adopt_clicked(self) -> None:
        """采纳当前显示版本。"""
        if self._selected_segment is None or not self._selected_versions:
            return
        index = self._version_combo.currentIndex()
        if index < 0:
            return
        version_index = self._selected_versions[index]["version_index"]
        self._db.select_expanded_version(
            self._selected_segment["id"], version_index)
        for v in self._selected_versions:
            v["is_selected"] = 1 if v["version_index"] == version_index else 0
        self._adopt_btn.setText("✓ 已采纳")
        QTimer.singleShot(1200, lambda: self._adopt_btn.setText("采纳此版本"))

    def refresh_segment_expansion(self, segment_id: int) -> None:
        """某段扩写完成后刷新（若正在查看该段）。"""
        if self._selected_segment is not None \
                and self._selected_segment["id"] == segment_id:
            self._selected_versions = self._db.get_expanded_versions(segment_id)
            self._render_expansion_panel()

    def refresh_expansions(self) -> None:
        """扩写全部完成后刷新当前片段，或自动展示首个成功结果。"""
        if self._selected_segment is not None:
            self._selected_versions = self._db.get_expanded_versions(
                self._selected_segment["id"])
            if self._selected_versions:
                self._render_expansion_panel()
                return
        self._select_first_expanded_segment()

    # ---- 内部 ----
    def _rebuild(self) -> None:
        """重建左栏分段列表。"""
        for widget in self._segment_widgets:
            widget.setParent(None)
            widget.deleteLater()
        self._segment_widgets.clear()

        # 移除旧 stretch
        while self._left_layout.count():
            item = self._left_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        resolver = lambda name: self._db.get_keyword(name, self._file)
        for index, seg in enumerate(self._segments, start=1):
            widget = SegmentWidget(
                index, seg.get("chapter", "未分章"), seg.get("content", ""),
                self._highlighter, resolver, segment_id=seg.get("id", 0),
            )
            widget.clicked.connect(self._on_segment_clicked)
            widget.npc_hovered.connect(self.npc_hovered)
            self._segment_widgets.append(widget)
            self._left_layout.addWidget(widget)
        self._left_layout.addStretch(1)

        self._meta.setText(
            f"{self._file} · 共 {len(self._segments)} 段"
            if self._file else "尚未导入模组"
        )
        self._left_scroll.verticalScrollBar().setValue(0)
        self._reset_expansion_panel()
        # 强制重算布局：确保卡片宽度不超过视口（避免首次显示时水平撑宽）
        for widget in self._segment_widgets:
            widget.layout().activate()
            widget.setMinimumWidth(0)
        self._left_layout.activate()
        self._left_container.updateGeometry()

    def show_chapter(self, chapter: str) -> None:
        """展开指定章节的段落，折叠其余，并滚动到该章节首段。"""
        first_index = None
        for widget, seg in zip(self._segment_widgets, self._segments):
            is_target = seg.get("chapter") == chapter
            widget.set_collapsed(not is_target)
            if is_target and first_index is None:
                first_index = self._segment_widgets.index(widget)
        if first_index is not None:
            bar = self._left_scroll.verticalScrollBar()
            target = sum(
                self._segment_widgets[i].height() + 10
                for i in range(first_index)
            )
            bar.setValue(target)
        # 右栏展示该章节的改写对照
        if chapter in self._fixes:
            self._render_right_panel(chapter)
