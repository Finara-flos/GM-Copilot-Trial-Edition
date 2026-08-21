"""NPC 自动扫描与背景补全。

两阶段：
1. 扫描：把模组全文交给 LLM，提取全部 NPC 名称（含扮演提示，如
   "酒馆老板" 等未具名角色），返回 JSON 列表。
2. 补全：对每个 NPC 调用 API 生成完整档案：动机、秘密、习惯用语、
   性格弱点、外貌特征、背景故事。结果写入 SQLite npcs 表。

设计：纯函数 + 异步；由主窗口的 NPC worker 驱动，支持并发补全与
断点续传（跳过已有档案的 NPC）。
"""
import asyncio
import json
import re
from src.modules.term_checker import extract_json_array

# 扫描提示词：要求返回 JSON 数组，只含 NPC 名称
_SCAN_PROMPT = (
    "从下面的 DND 跑团模组文本中提取所有 NPC（非玩家角色）。\n"
    "规则：\n"
    "1. 包含有名字的角色（如 哈维尔、艾拉）；\n"
    "2. 也包含没有名字但有明确身份的 NPC（如 酒馆老板、守墓人、商人），"
    "以「身份称呼」作为名称；\n"
    "3. 不包括玩家角色（PC）、生物（怪物/动物）、神祇或抽象概念；\n"
    "4. 去除重复，只输出 JSON 字符串数组，如 [\"哈维尔\",\"酒馆老板\"]，"
    "不要其他文字或解释。\n\n"
    "【模组文本】\n"
)

# 档案补全提示词：要求返回 JSON 对象
_PROFILE_PROMPT = (
    "你是 DND 跑团模组的 NPC 档案生成助手。我会给你一位 NPC 的名字，"
    "以及模组中与该 NPC 相关的上下文片段。请为这位 NPC 生成完整档案，"
    "只输出 JSON 对象，不要其他文字或解释，字段如下：\n"
    "{\n"
    '  "motivation": "动机：这位NPC想要什么、为什么（1-2句）",\n'
    '  "secret": "秘密：ta隐藏的秘密（1-2句）",\n'
    '  "catchphrase": "习惯用语：ta常挂在嘴边的一句话",\n'
    '  "flaw": "性格弱点：ta的性格缺陷或可利用的弱点（1句）",\n'
    '  "appearance": "外貌特征：体貌、穿着、神态（1-2句）",\n'
    '  "backstory": "背景故事：ta的背景与来历（2-3句）"\n'
    "}\n"
    "要求：与上下文保持一致，不编造与原文冲突的事实；若某字段在原文中"
    "没有依据，用合理推测并保持中立；使用与模组相同的语言（中文模组用中文）。"
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


def _extract_json_array(text: str) -> list:
    """从模型输出中解析 NPC 名称数组。"""
    return extract_json_array(text)


def _extract_json_object(text: str) -> dict:
    """从 LLM 输出中解析 JSON 对象（容错）。"""
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
    allowed = {"motivation", "secret", "catchphrase", "flaw",
               "appearance", "backstory"}
    return {k: str(v).strip() for k, v in data.items()
            if k in allowed and str(v).strip()}


def _extract_context(full_text: str, name: str, radius: int = 500) -> str:
    """在全文里找到与 NPC 名称相关的一段上下文。

    :param radius: 名称前后各取多少字符
    :return: 上下文片段（可能为空）
    """
    idx = full_text.find(name)
    if idx < 0:
        return ""
    start = max(0, idx - radius)
    end = min(len(full_text), idx + len(name) + radius)
    return full_text[start:end]


async def scan_npcs(client, full_text: str) -> list[str]:
    """扫描全文，返回去重的 NPC 名称列表（异步）。"""
    user = _SCAN_PROMPT + full_text[:12000]
    result = await client.chat(
        [{"role": "user", "content": user}],
        retries=2,
        temperature=0.0,
    )
    names = _extract_json_array(result)
    # 有些模型把名称包装为 {"name": "..."}；统一成字符串列表。
    names = [item.get("name", "") if isinstance(item, dict) else item for item in names]
    # 过滤过短/过长/含符号的噪声名称
    cleaned = []
    for n in names:
        n = n.strip()
        if not (1 < len(n) <= 24):
            continue
        if re.search(r"[{}[\]\"\\|]", n):
            continue
        cleaned.append(n)
    return cleaned


async def build_npc_profile(client, name: str, full_text: str) -> dict:
    """为单个 NPC 生成完整档案（异步）。

    :return: {name, motivation, secret, catchphrase, flaw, appearance, backstory}
    """
    context = _extract_context(full_text, name)
    user = (
        f"【NPC 名称】\n{name}\n\n"
        f"【模组上下文】\n{context or '（未在原文中找到该NPC的明确上下文，请基于名称合理推测）'}\n\n"
        "请生成该 NPC 的完整档案。"
    )
    result = await client.chat(
        [{"role": "system", "content": _PROFILE_PROMPT},
         {"role": "user", "content": user}],
        retries=2,
        temperature=0.7,
    )
    profile = _extract_json_object(result)
    profile["name"] = name
    return profile


async def scan_and_build_all(
    client,
    full_text: str,
    db,
    original_file: str,
    progress_cb=None,
    concurrency: int = 3,
) -> list[dict]:
    """扫描 + 补全全部 NPC 档案，写入数据库。

    :param client: AsyncLLMClient
    :param full_text: 模组全文
    :param db: Database
    :param original_file: 模组文件名
    :param progress_cb: (done, total, status) 回调
    :param concurrency: 补全并发数
    :return: 成功写入的 NPC 档案列表
    """
    names = await scan_npcs(client, full_text)
    if not names:
        return []
    # 断点续传：跳过已有档案的 NPC
    existing = {n["name"] for n in db.get_npcs(original_file)}
    pending = [n for n in names if n not in existing]
    if not pending:
        return db.get_npcs(original_file)

    semaphore = asyncio.Semaphore(max(1, int(concurrency)))
    done_count = [0]
    total = len(pending)
    results: list[dict] = []

    async def build_one(name: str) -> dict | None:
        async with semaphore:
            try:
                profile = await build_npc_profile(client, name, full_text)
            except Exception:  # noqa: BLE001  单个 NPC 失败不阻断整体
                done_count[0] += 1
                if progress_cb:
                    progress_cb(done_count[0], total, f"{name} 档案生成失败")
                return None
            db.upsert_npc(profile, original_file)
            done_count[0] += 1
            if progress_cb:
                progress_cb(done_count[0], total, f"正在生成 {name} 的档案…")
            return profile

    batch = await asyncio.gather(*(build_one(n) for n in pending))
    results = [r for r in batch if r]
    return results
