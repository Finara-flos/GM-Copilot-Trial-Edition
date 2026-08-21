"""文化隐喻转译：识别西方文化特有比喻，给出保留原意的中文等效表达。

输出：原文 -> 建议译文；用户可逐条确认（替换进正文）或忽略。
"""
import json
import re
from src.modules.term_checker import extract_json_array


METAPHOR_PROMPT = (
    "你是 DND 模组翻译的文化转译者。下面是模组文本。请找出「西方文化特有」的比喻、"
    "典故或俗语（例如 'Achilles\\' heel' 阿喀琉斯之踵、'crossing the Rubicon' 破釜沉舟、"
    "'skeleton in the closet' 难以启齿的秘密），它们直译会让中文读者费解。\n"
    "输出 JSON 数组：\n"
    '[{"original": "英文原文（或原文句子的比喻部分）", "suggestion": "保留原意的中文等效表达"}]\n'
    "要求：suggestion 是自然的汉语，不直译字面，但保留原意；只输出 JSON 数组。\n\n"
)



async def check_metaphors(client, text: str, sample_chars: int = 16000) -> list[dict]:
    """调用 API 识别文化隐喻，返回 [{"original", "suggestion"}]。"""
    sample = text[:sample_chars]
    result = await client.chat(
        [{"role": "user", "content": METAPHOR_PROMPT + "【模组文本】\n" + sample}],
        temperature=0.1,
    )
    items = []
    for item in extract_json_array(result):
        original = str(item.get("original", "")).strip()
        suggestion = str(item.get("suggestion", "")).strip()
        if original and suggestion:
            items.append({"original": original, "suggestion": suggestion})
    return items


def apply_metaphor(db, file_name: str, original: str, suggestion: str) -> int:
    """把确认的隐喻建议替换进当前模组全部段落；返回受影响段数。"""
    if original == suggestion:
        return 0
    segments = db.get_segments(file_name)
    changed = 0
    for seg in segments:
        if original in seg["content"]:
            new_content = seg["content"].replace(original, suggestion)
            db.update_segment_content(seg["id"], new_content)
            changed += 1
    return changed
