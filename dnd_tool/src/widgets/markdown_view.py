"""Markdown 渲染引擎：Markdown -> HTML -> QTextBrowser。

支持：标题、列表、代码块、表格、链接、引用、分隔线、行内加粗/斜体/代码；
图片暂时渲染为占位符。关键词（kw:// 锚点）悬浮时弹出信息卡片。
配色随主题切换（暗色/亮色）。
"""
import html
import re

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtWidgets import QTextBrowser, QTextEdit, QToolTip

from src.widgets.keyword_highlight import keyword_tooltip_html

# 主题配色方案（Markdown 内嵌样式的取色来源）
SCHEMES = {
    "dark": {
        "text": "#E5E7EB",
        "muted": "#8A8F99",
        "accent": "#22D3EE",
        "code_bg": "#23262F",
        "code_text": "#E5E7EB",
        "border": "#3F434E",
    },
    "light": {
        "text": "#1C1917",
        "muted": "#78716C",
        "accent": "#0891B2",
        "code_bg": "#F0ECE2",
        "code_text": "#1C1917",
        "border": "#E4E0D6",
    },
}

_active_scheme_name = "dark"


def set_active_scheme(name: str) -> None:
    """设置当前主题（由主窗口在切换主题时调用）。"""
    global _active_scheme_name
    if name in SCHEMES:
        _active_scheme_name = name


def active_scheme() -> dict:
    """当前主题配色。"""
    return SCHEMES.get(_active_scheme_name, SCHEMES["dark"])


_IMG_PLACEHOLDER_RE = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")

_CODE_FENCE_RE = re.compile(r"^```(\w*)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_HR_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")
_UL_ITEM_RE = re.compile(r"^[-*+]\s+(.*)$")
_OL_ITEM_RE = re.compile(r"^\d+[.、]\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|[\s:|:-]*$")


def _image_placeholder(alt: str, src: str, sch: dict) -> str:
    """图片占位块。"""
    label = html.escape(alt or src or "图片")
    return (
        f'<div style="background:{sch["code_bg"]};border-radius:8px;padding:10px 14px;'
        f'color:{sch["muted"]};margin:6px 0;font-size:12px;">'
        f'🖼 图片占位：{label}</div>'
    )


def _inline_md(text: str, sch: dict) -> str:
    """行内语法：图片占位、链接、行内代码、加粗、斜体。"""
    text = html.escape(text, quote=False)
    text = _IMG_PLACEHOLDER_RE.sub(
        lambda m: _image_placeholder(m.group(1), m.group(2), sch), text)
    text = _LINK_RE.sub(
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}">'
                  f'{html.escape(m.group(1))}</a>',
        text)
    text = _INLINE_CODE_RE.sub(
        lambda m: f'<code style="background:{sch["code_bg"]};border-radius:4px;'
                  f'padding:1px 5px;font-family:Consolas,monospace;'
                  f'color:{sch["accent"]};">{m.group(1)}</code>',
        text)
    text = _BOLD_RE.sub(r"<b>\1</b>", text)
    text = _ITALIC_RE.sub(r"<i>\1</i>", text)
    return text


def _code_block(code: str, lang: str, sch: dict) -> str:
    """代码块：语言标签 + pre。"""
    safe = html.escape(code)
    label = (
        f'<div style="color:{sch["muted"]};font-size:11px;margin-bottom:4px;">'
        f'{html.escape(lang or "代码")}</div>'
        if lang else ""
    )
    return (
        f'<div style="background:{sch["code_bg"]};border-radius:8px;'
        f'padding:10px 12px;margin:6px 0;">'
        f'{label}<pre style="font-family:Consolas,monospace;font-size:12px;'
        f'color:{sch["code_text"]};margin:0;white-space:pre-wrap;">{safe}</pre></div>'
    )


def _table(lines: list[str], sch: dict) -> str:
    """Markdown 表格 -> HTML 表格。"""
    def cells(row: str) -> list[str]:
        return [c.strip() for c in row.strip().strip("|").split("|")]

    header = cells(lines[0])
    body_rows = [cells(row) for row in lines[2:]] if len(lines) > 2 else []
    thead = "".join(f"<th>{_inline_md(c, sch)}</th>" for c in header)
    tbody = "".join(
        "<tr>" + "".join(f"<td>{_inline_md(c, sch)}</td>" for c in row) + "</tr>"
        for row in body_rows
    )
    return (
        '<table border="1" cellspacing="0" cellpadding="6" '
        f'style="border-collapse:collapse;color:{sch["text"]};margin:6px 0;">'
        f"<thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>"
    )


def markdown_to_html(md_text: str, scheme: dict | None = None) -> str:
    """Markdown 文本 -> HTML 片段（不含外层容器）。"""
    sch = scheme or active_scheme()
    lines = md_text.splitlines()
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue

        # 代码块
        fence = _CODE_FENCE_RE.match(stripped)
        if fence:
            lang = fence.group(1)
            buf: list[str] = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过结束 ```（可能不存在）
            out.append(_code_block("\n".join(buf), lang, sch))
            continue

        # 标题
        heading = _HEADING_RE.match(stripped)
        if heading:
            level = min(len(heading.group(1)), 6)
            color = sch["text"]
            if level == 1:
                color = sch["accent"]
            out.append(
                f'<h{level} style="color:{color};margin:10px 0 6px 0;">'
                f'{_inline_md(heading.group(2), sch)}</h{level}>')
            i += 1
            continue

        # 分隔线
        if _HR_RE.match(stripped):
            out.append(
                f"<hr style='border:none;border-top:1px solid {sch['border']};"
                f"margin:10px 0;'/>")
            i += 1
            continue

        # 引用
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            inner = "<br/>".join(_inline_md(b, sch) for b in buf)
            out.append(
                f'<blockquote style="border-left:3px solid {sch["accent"]};'
                f'margin:6px 0;padding:2px 0 2px 10px;color:{sch["muted"]};">'
                f"{inner}</blockquote>")
            continue

        # 列表
        ul_match = _UL_ITEM_RE.match(stripped)
        ol_match = _OL_ITEM_RE.match(stripped)
        if ul_match or ol_match:
            is_ol = ol_match is not None
            tag = "ol" if is_ol else "ul"
            items: list[str] = []
            while i < n:
                s = lines[i].strip()
                mu = _UL_ITEM_RE.match(s)
                mo = _OL_ITEM_RE.match(s)
                if is_ol and mo:
                    items.append(mo.group(1))
                    i += 1
                elif not is_ol and mu:
                    items.append(mu.group(1))
                    i += 1
                else:
                    break
            li = "".join(f"<li>{_inline_md(item, sch)}</li>" for item in items)
            out.append(f'<{tag} style="margin:6px 0;padding-left:20px;">{li}</{tag}>')
            continue

        # 表格
        if "|" in stripped and i + 1 < n and _TABLE_SEP_RE.match(lines[i + 1].strip()):
            buf = []
            while i < n and "|" in lines[i]:
                buf.append(lines[i])
                i += 1
            out.append(_table(buf, sch))
            continue

        # 普通段落（连续非空行）
        buf = [line]
        i += 1
        while i < n and lines[i].strip():
            buf.append(lines[i])
            i += 1
        para = "<br/>".join(_inline_md(b, sch) for b in buf)
        out.append(f"<p style='margin:6px 0;'>{para}</p>")

    return "\n".join(out)


class MarkdownView(QTextBrowser):
    """Markdown 渲染视图：支持关键词悬浮卡片与自适应高度。"""

    fix_clicked = Signal(str)  # 用户点击 fix://N 锚点时发射 N
    npc_hovered = Signal(str)  # 鼠标悬停 NPC 关键词时发射名称
    content_clicked = Signal()  # 点击普通正文（非自定义链接）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("MarkdownView")
        # 关闭外部链接自动打开：由 _on_anchor_clicked 自行分发，
        # 避免自定义 scheme（kw:）触发系统"打开应用"提示
        self.setOpenExternalLinks(False)
        self.setFrameShape(QTextBrowser.NoFrame)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._keyword_resolver = None
        self._auto_height = False
        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)
        self.anchorClicked.connect(self._on_anchor_clicked)

    # ---- 对外接口 ----
    def set_keyword_resolver(self, resolver) -> None:
        """设置关键词查询回调：name -> dict(kind, detail) 或 None。"""
        self._keyword_resolver = resolver

    def set_auto_height(self, enabled: bool) -> None:
        """开启自适应高度（段落体内使用，随宽度自动重排）。"""
        self._auto_height = enabled

    def setSource(self, url) -> bool:  # noqa: N802  Qt 命名约定
        """拦截 QTextBrowser 的链接导航。

        Qt 在点击链接时会调用 setSource；对未识别的自定义 scheme（kw:/fix:）
        默认会清空文档并导航失败。这里拦截这些 scheme 不做任何导航，
        避免点击 NPC 名称后正文被清空；http(s) 交给系统浏览器打开。
        """
        href = url.toString()
        if href.startswith("kw:") or href.startswith("fix://"):
            return True  # 已由 anchorClicked/_on_anchor_clicked 处理
        if href.startswith("http://") or href.startswith("https://"):
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl(href))
            return True
        return super().setSource(url)

    def sizeHint(self):  # noqa: N802  Qt 命名约定
        """自适应高度模式：允许布局压缩宽度（文档宽度由视口决定）。"""
        if self._auto_height:
            hint = super().sizeHint()
            hint.setWidth(120)
            return hint
        return super().sizeHint()

    def minimumSizeHint(self):  # noqa: N802  Qt 命名约定
        """自适应高度模式：最小宽度放宽，避免撑开卡片/出现横向滚动条。"""
        if self._auto_height:
            hint = super().minimumSizeHint()
            hint.setWidth(60)
            return hint
        return super().minimumSizeHint()

    def set_markdown(self, md_text: str, highlighter=None) -> None:
        """渲染 Markdown；可传入 KeywordHighlighter 进行关键词高亮。"""
        marked = md_text
        token_map: dict = {}
        if highlighter is not None:
            marked, token_map = highlighter.protect(md_text)
        sch = active_scheme()
        body = markdown_to_html(marked, sch)
        if token_map:
            body = highlighter.render(body, token_map)
        self.setHtml(
            f'<div style="color:{sch["text"]};font-size:13px;'
            f'font-family:&quot;Noto Sans SC&quot;,&quot;Microsoft YaHei&quot;,sans-serif;">'
            f"{body}</div>"
        )
        if self._auto_height:
            self._refit()

    def set_html(self, body: str) -> None:
        """直接渲染 HTML 片段（对照视图的修改高亮等场景）。"""
        sch = active_scheme()
        self.setHtml(
            f'<div style="color:{sch["text"]};font-size:13px;'
            f'font-family:&quot;Noto Sans SC&quot;,&quot;Microsoft YaHei&quot;,sans-serif;">'
            f"{body}</div>"
        )
        if self._auto_height:
            self._refit()

    def _refit(self) -> None:
        """按当前宽度重排并固定高度。"""
        # 用控件自身宽度（resizeEvent 触发时已更新；viewport 在部分平台会滞后）
        width = max(self.width(), self.viewport().width())
        self.document().setTextWidth(max(width - 2, 40))
        height = int(self.document().size().height()) + 12
        self.setFixedHeight(max(40, min(height, 480)))

    def resizeEvent(self, event) -> None:  # noqa: N802
        """宽度变化时自动重排高度。"""
        super().resizeEvent(event)
        if self._auto_height:
            self._refit()

    # ---- 悬浮与点击 ----
    def _on_anchor_clicked(self, url) -> None:
        """处理锚点点击：fix:// 发射信号；kw: 若为 NPC 则联动信息面板；http(s) 用系统浏览器打开。

        注意：本视图关闭了 setOpenExternalLinks，所有链接在此统一分发，
        避免自定义 scheme（kw:）被系统当作未关联应用而弹出提示。
        """
        href = url.toString()
        if href.startswith("fix://"):
            self.fix_clicked.emit(href[len("fix://"):])
        elif href.startswith("kw:"):
            name = href[len("kw:"):]
            keyword = None
            if self._keyword_resolver is not None:
                keyword = self._keyword_resolver(name)
            if keyword and keyword.get("kind") == "npc":
                # 点击 NPC 名称：发射信号（信息面板显示完整卡片）
                self.npc_hovered.emit(keyword.get("name", ""))
        elif href.startswith("http://") or href.startswith("https://"):
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl

            QDesktopServices.openUrl(QUrl(href))

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        """viewport 事件处理：鼠标移动显示 kw: 提示；鼠标释放拦截自定义链接。"""
        if obj is self.viewport():
            if event.type() == QEvent.MouseMove:
                anchor = self.anchorAt(event.position().toPoint())
                if anchor.startswith("kw:"):
                    name = anchor[len("kw:"):]
                    keyword = None
                    if self._keyword_resolver is not None:
                        keyword = self._keyword_resolver(name)
                    if keyword:
                        QToolTip.showText(
                            event.globalPosition().toPoint() + QPoint(14, 18),
                            keyword_tooltip_html(keyword, active_scheme()),
                            self.viewport(),
                        )
                        # NPC 悬停联动：发射信号（信息面板显示完整卡片）
                        if keyword.get("kind") == "npc":
                            self.npc_hovered.emit(keyword.get("name", ""))
                        return False
                QToolTip.hideText()
            elif event.type() == QEvent.MouseButtonRelease:
                # 点击自定义链接（kw:/fix:）：自行分发并阻止 Qt 默认导航，
                # 避免 QTextBrowser 因无法解析 scheme 而清空文档
                anchor = self.anchorAt(event.position().toPoint())
                if anchor.startswith("kw:") or anchor.startswith("fix://"):
                    self._on_anchor_clicked_url(anchor)
                    return True
                self.content_clicked.emit()
        return super().eventFilter(obj, event)

    def _on_anchor_clicked_url(self, anchor: str) -> None:
        """按锚点文本分发（供事件过滤器调用，anchor 为原始 href 文本）。"""
        if anchor.startswith("fix://"):
            self.fix_clicked.emit(anchor[len("fix://"):])
        elif anchor.startswith("kw:"):
            name = anchor[len("kw:"):]
            keyword = None
            if self._keyword_resolver is not None:
                keyword = self._keyword_resolver(name)
            if keyword and keyword.get("kind") == "npc":
                self.npc_hovered.emit(keyword.get("name", ""))
