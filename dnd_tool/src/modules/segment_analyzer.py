"""智能分段识别：判断每段是否需要扩写。

规则：
- 字数 < 200 且信息密度低（关键词少）且不含数值/规则引用 -> need_expand
- 字数 > 500 或包含复杂规则（数值、豁免、技能、CR 等）-> keep（保留原样）
- 其余情况按信息密度决定
"""
import re

# 数值 / 规则引用模式（命中即认为含关键数据）
_RULE_PATTERNS = [
    re.compile(r"\b\d{1,3}\b"),                      # 数字
    re.compile(r"\b(?:AC|HP|DC|CR|XP)\b", re.I),     # 数值字段
    re.compile(r"豁免|检定|命中|伤害|抗性|免疫|法术位|等级|属性"),  # 规则词
    re.compile(r"Fort|Ref|Will|Init", re.I),
    re.compile(r"d\d+\s*(\+\s*\d+)?", re.I),         # 骰子 2d6+2
]

MIN_EXPAND_CHARS = 200
KEEP_CHARS = 500


def count_rule_hits(content: str) -> int:
    """统计文本中数值/规则引用命中次数。"""
    return sum(len(pattern.findall(content)) for pattern in _RULE_PATTERNS)


def analyze_segment(
    content: str,
    keyword_count: int = 0,
    expand_threshold: int = MIN_EXPAND_CHARS,
) -> str:
    """判断分段是否需要扩写。

    :param content: 分段正文
    :param keyword_count: 该段命中的关键词数量（信息密度）
    :return: "need_expand" 或 "keep"
    """
    length = len(content)
    rule_hits = count_rule_hits(content)

    # 过长或含复杂规则 -> 保留
    if length > KEEP_CHARS or rule_hits >= 3:
        return "keep"

    # 过短且信息稀疏 -> 需要扩写
    if length < expand_threshold and keyword_count <= 1 and rule_hits == 0:
        return "need_expand"

    # 中等长度：信息密度低则扩写
    if keyword_count == 0 and rule_hits == 0:
        return "need_expand"

    return "keep"


def analyze_segments(segments: list[dict], db=None) -> list[dict]:
    """批量分析分段，为每段附加 decision 字段。

    :param segments: [{id, chapter, content, ...}]
    :param db: 可选 Database（用于统计每段命中关键词数）
    """
    keywords = db.get_keywords() if db is not None else []
    names = [k["name"] for k in keywords]
    result = []
    for seg in segments:
        content = seg.get("content", "")
        kw_count = sum(1 for name in names if name and name in content)
        result.append({
            **seg,
            "decision": analyze_segment(content, kw_count),
            "keyword_count": kw_count,
        })
    return result
