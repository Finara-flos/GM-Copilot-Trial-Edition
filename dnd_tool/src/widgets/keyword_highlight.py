"""关键词高亮引擎：保护原文 -> 渲染为带 kw:// 锚点的彩色 span。

词库来源：SQLite keywords 表（初始为空，由导入页词库管理或后续 AI 填充）。
颜色：NPC 红、地名蓝、物品绿。
"""
import html
import re

KIND_COLORS = {
    "npc": "#EF4444",    # 红
    "place": "#3B82F6",  # 蓝
    "item": "#22C55E",   # 绿
}
KIND_LABELS = {
    "npc": "NPC",
    "place": "地名",
    "item": "物品",
}


class KeywordHighlighter:
    """把词库关键词包装成可悬浮查询的高亮锚点。"""

    def __init__(self):
        self._pattern: re.Pattern | None = None
        self._kind_map: dict[str, str] = {}

    def set_keywords(self, keywords: list[dict]) -> None:
        """设置词库：[{name, kind}]。空列表则不高亮。"""
        if not keywords:
            self._pattern = None
            self._kind_map = {}
            return
        names = sorted({k["name"] for k in keywords if k.get("name")}, key=len, reverse=True)
        self._kind_map = {k["name"]: k["kind"] for k in keywords if k.get("name")}
        if not names:
            self._pattern = None
            return
        # ASCII 词边界：避免命中英文单词内部；中文相邻字符不阻断
        pattern = r"(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(n) for n in names) + r")(?![A-Za-z0-9_])"
        self._pattern = re.compile(pattern)

    def protect(self, text: str) -> tuple[str, dict]:
        """把关键词替换为不可被 Markdown 解析破坏的占位令牌。

        :return: (标记文本, {令牌: (名称, 类型)})
        """
        if self._pattern is None:
            return text, {}
        token_map: dict[str, tuple[str, str]] = {}
        counter = [0]

        def repl(match: re.Match) -> str:
            name = match.group(0)
            counter[0] += 1
            token = f"\x00KW{counter[0]}\x00"
            token_map[token] = (name, self._kind_map.get(name, "npc"))
            return token

        marked = self._pattern.sub(repl, text)
        return marked, token_map

    def render(self, html_text: str, token_map: dict) -> str:
        """把占位令牌替换为高亮锚点 span。"""
        for token, (name, kind) in token_map.items():
            color = KIND_COLORS.get(kind, "#22D3EE")
            safe_name = html.escape(name)
            # 用 kw: 前缀（非 kw://）：避免 QUrl 把中文名称转成 punycode
            anchor = (
                f'<a href="kw:{safe_name}" style="color:{color};'
                f'text-decoration:none;font-weight:600;">{safe_name}</a>'
            )
            html_text = html_text.replace(token, anchor)
        return html_text


def keyword_tooltip_html(keyword: dict, scheme: dict | None = None) -> str:
    """生成悬浮卡片 HTML（QToolTip 风格；配色跟随主题）。"""
    kind = keyword.get("kind", "npc")
    color = KIND_COLORS.get(kind, "#22D3EE")
    label = KIND_LABELS.get(kind, kind)
    name = html.escape(keyword.get("name", ""))
    detail = html.escape(keyword.get("detail") or "暂无详细信息")
    muted = (scheme or {}).get("muted", "#9CA3AF")
    text = (scheme or {}).get("text", "#FFFFFF")
    return (
        f'<div style="min-width:170px;max-width:300px;padding:2px;">'
        f'<div style="font-size:13px;font-weight:700;color:{text};">'
        f'<span style="display:inline-block;width:8px;height:8px;border-radius:4px;'
        f'background:{color};margin-right:6px;"></span>{name}</div>'
        f'<div style="font-size:11px;color:{color};margin:2px 0 6px 14px;">{label}</div>'
        f'<div style="font-size:12px;color:{muted};">{detail}</div>'
        f'</div>'
    )
