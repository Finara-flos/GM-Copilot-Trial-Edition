"""情境化台词库生成：为每个 NPC 生成 5 种常见场景的台词。

场景：谈判 / 战斗 / 欺骗 / 求助 / 闲聊。
若某 NPC 不可能触发某场景（如无战斗能力的平民），则不生成该场景台词。

台词以 JSON 存储（SQLite npc_dialogues 表），UI 提供下拉场景选择。
"""
import asyncio
import json
import re

SCENES = ["谈判", "战斗", "欺骗", "求助", "闲聊"]

_SCENE_PROMPT = (
    "你是 DND 跑团模组的 NPC 台词写手。我会给你一位 NPC 的档案，"
    "请为这位 NPC 生成五种常见场景的台词。\n"
    "场景：谈判、战斗、欺骗、求助、闲聊。\n"
    "规则：\n"
    "1. 每句台词都要符合该 NPC 的性格、动机与习惯用语（如有）；\n"
    "2. 台词要口语化、有角色感，符合 DND 世界观；\n"
    "3. 如果该 NPC 不可能触发某个场景（例如没有任何战斗能力或理由的平民"
    "不应有「战斗」台词），就跳过该场景，不要硬编；\n"
    "4. 只输出 JSON 对象，键为场景名（谈判/战斗/欺骗/求助/闲聊），"
    "值为一句台词，例如：\n"
    '{"谈判": "……", "战斗": "……"}'
    "不要输出其他文字或解释。\n\n"
    "【NPC 档案】\n"
)


def _strip_code_fence(text: str) -> str:
    """去掉 LLM 输出可能包裹的 ```json 围栏。"""
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


def _extract_scene_json(text: str) -> dict:
    """从 LLM 输出解析 {场景: 台词} 对象（容错）。"""
    text = _strip_code_fence(text.strip())
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for scene in SCENES:
        value = data.get(scene) or data.get(f"{scene}：") or data.get(f"{scene}:")
        if isinstance(value, str) and value.strip():
            out[scene] = value.strip()
        elif isinstance(value, list) and value and isinstance(value[0], str):
            out[scene] = value[0].strip()
    return out


def format_npc_context(npc: dict) -> str:
    """把 NPC 档案格式化为提示词上下文。"""
    fields = [
        ("名称", npc.get("name", "")),
        ("动机", npc.get("motivation", "")),
        ("秘密", npc.get("secret", "")),
        ("习惯用语", npc.get("catchphrase", "")),
        ("性格弱点", npc.get("flaw", "")),
        ("外貌特征", npc.get("appearance", "")),
        ("背景故事", npc.get("backstory", "")),
    ]
    lines = [f"{label}：{value}" for label, value in fields if value]
    return "\n".join(lines) or npc.get("name", "")


async def generate_dialogues(
    client,
    npc: dict,
    db=None,
    progress_cb=None,
) -> dict:
    """为单个 NPC 生成台词并写入数据库。

    :param client: AsyncLLMClient
    :param npc: NPC 档案 dict
    :param db: Database（可选，写入台词表）
    :return: {场景: 台词}（实际生成的场景）
    """
    name = npc.get("name", "")
    user = (
        f"{format_npc_context(npc)}\n\n"
        f"请为 NPC「{name}」生成五种场景的台词。"
    )
    result = await client.chat(
        [{"role": "system", "content": _SCENE_PROMPT},
         {"role": "user", "content": user}],
        retries=2,
        temperature=0.8,
    )
    dialogues = _extract_scene_json(result)
    if db is not None:
        for scene, line in dialogues.items():
            db.save_npc_dialogue(name, scene, line)
    if progress_cb:
        progress_cb(name, list(dialogues.keys()))
    return dialogues


async def generate_all_dialogues(
    client,
    npcs: list[dict],
    db,
    progress_cb=None,
    concurrency: int = 3,
) -> int:
    """为一批 NPC 生成台词（并发），返回生成台词条数。"""
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))
    done = [0]
    total = len(npcs)
    count = [0]

    async def one(npc: dict) -> None:
        async with semaphore:
            try:
                dialogues = await generate_dialogues(client, npc, db)
                count[0] += len(dialogues)
            except Exception:  # noqa: BLE001  单个失败不阻断
                pass
            done[0] += 1
            if progress_cb:
                progress_cb(done[0], total, f"生成台词：{npc.get('name', '')}")

    await asyncio.gather(*(one(n) for n in npcs))
    return count[0]
