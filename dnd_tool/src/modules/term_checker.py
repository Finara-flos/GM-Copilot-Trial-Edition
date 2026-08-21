"""术语一致性检查：找出同一概念的不同译名，供用户确认后统一替换。

流程：API 分析全文 -> 候选术语组 [{concept, variants}] -> 用户确认
-> apply_term_replacements 全文替换并记录到 term_replacements 表 -> 支持撤销。
"""
import json
import re

TERM_CHECK_PROMPT = (
    "你是 DND 模组翻译术语审查员。下面是模组文本。请找出表达「同一概念」却被翻译成"
    "不同写法的术语（例如：精灵/艾尔夫、矮人/杜瓦夫、银剑/秘银剑），"
    "输出 JSON 数组，每项包含：\n"
    '[{"concept": "统一后的术语名", "variants": ["变体1", "变体2"]}]\n'
    "要求：\n"
    "1. variants 列出原文中出现过的所有变体写法（至少 2 个才算不一致）；\n"
    "2. concept 是推荐的统一写法（通常是出现最多的或最规范的译名）；\n"
    "3. 只输出 JSON 数组，不要解释。\n\n"
)


def extract_json_array(text: str) -> list:
    """从模型输出提取首个有效 JSON 数组，兼容围栏和对象包装。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1:].count("```") else lines[1:]).strip()
    normalized = text.translate(str.maketrans({"“": '"', "”": '"', "，": ",", "：": ":"}))
    decoder = json.JSONDecoder()
    for index, char in enumerate(normalized):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(normalized[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for key in ("items", "data", "results", "terms", "groups"):
                if isinstance(value.get(key), list):
                    return value[key]
    return []


async def check_terms(client, text: str, sample_chars: int = 16000) -> list[dict]:
    """调用 API 分析全文，返回候选术语组列表。"""
    sample = text[:sample_chars]
    result = await client.chat(
        [{"role": "user", "content": TERM_CHECK_PROMPT + "【模组文本】\n" + sample}],
        temperature=0.1,
    )
    groups = []
    for item in extract_json_array(result):
        variants = [str(v).strip() for v in item.get("variants", []) if str(v).strip()]
        concept = str(item.get("concept", "")).strip()
        if concept and len(variants) >= 2:
            groups.append({"concept": concept, "variants": variants})
    return groups


def apply_term_replacements(
    db, file_name: str, groups: list[dict],
    apply_map: dict[str, str] | None = None,
) -> int:
    """把选中的术语组统一替换到 segments 全文，并记录到 term_replacements。

    :param apply_map: {concept: 实际替换成的写法}；缺省用 concept 本身。
    :return: 替换的段落数
    """
    segments = db.get_segments(file_name)
    mapping = []  # (original, replacement, group_name)
    for group in groups:
        target = (apply_map or {}).get(group["concept"], group["concept"])
        for variant in group["variants"]:
            if variant != target:
                mapping.append((variant, target, group["concept"]))
    if not mapping:
        return 0

    changed = 0
    for seg in segments:
        content = seg["content"]
        new_content = content
        for original, replacement, _group in mapping:
            new_content = new_content.replace(original, replacement)
        if new_content != content:
            db.update_segment_content(seg["id"], new_content)
            changed += 1
    # 记录替换（可撤销）
    for original, replacement, group in mapping:
        db.add_term_replacement(group, original, replacement)
    return changed


def undo_term_replacement(db, file_name: str, replacement_id: int) -> int:
    """撤销一条术语替换：把 replacement 恢复回 original。"""
    record = next(
        (r for r in db.get_term_replacements() if r["id"] == replacement_id), None)
    if not record:
        return 0
    segments = db.get_segments(file_name)
    changed = 0
    for seg in segments:
        content = seg["content"]
        if record["replacement"] in content:
            new_content = content.replace(record["replacement"], record["original"])
            db.update_segment_content(seg["id"], new_content)
            changed += 1
    db.remove_term_replacement(replacement_id)
    return changed
