"""翻译腔句式改写：将长被动句、超长定语从句拆分为短句，输出原文/修改对照。

按章节处理（配合任务队列），每章返回 [{"original", "fixed"}] 修改对列表，
供对照视图右栏高亮展示并逐条接受/拒绝/编辑。
"""
import json
import re

FIX_PROMPT = (
    "你是中文小说翻译润色编辑。下面是模组文本。请找出「翻译腔」明显的句子："
    "长被动句、超长定语从句、生硬的「被…所…」、「一个…的…」堆积、欧化长句等。\n"
    "将每个需要修改的句子拆分为更短、更符合中文口语习惯的句子。\n"
    "输出 JSON 数组：\n"
    '[{"original": "修改前的句子", "fixed": "拆分改写后的句子（可含多句）"}]\n'
    "要求：\n"
    "1. original 必须是原文中真实存在的连续文本；\n"
    "2. fixed 保留原意，句子明显变短、更口语化；\n"
    "3. 只输出 JSON 数组，不要解释；没有可改的句子时输出 []。\n\n"
)


def extract_json_array(text: str) -> list:
    """从 LLM 输出中提取 JSON 数组（容忍代码围栏与前后缀）。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    match = re.search(r"\[.*\]", text, re.S)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


async def fix_chapter(client, chapter_text: str) -> list[dict]:
    """调用 API 改写一章，返回 [{"original", "fixed"}] 修改对。"""
    result = await client.chat(
        [{"role": "user", "content": FIX_PROMPT + "【待润色文本】\n" + chapter_text}],
        temperature=0.2,
    )
    pairs = []
    for item in extract_json_array(result):
        original = str(item.get("original", "")).strip()
        fixed = str(item.get("fixed", "")).strip()
        if original and fixed and original != fixed and original in chapter_text:
            pairs.append({"original": original, "fixed": fixed})
    return pairs
