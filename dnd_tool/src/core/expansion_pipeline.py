"""扩写流水线：单段多版本生成（含流式输出与版本解析）。

系统提示词：你是 DND 模组扩写助手，保留所有数值和规则，增加细节描写。
"""
import re

EXPAND_SYSTEM_PROMPT = (
    "你是 DND 模组扩写助手。请扩写用户给出的模组段落，"
    "**保留所有数值和规则**（AC/HP/DC/豁免/技能/骰子等一律不变），"
    "在保留原意的基础上增加细节描写：环境氛围、动作描写、感官细节、角色反应等。"
    "语言为自然流畅的中文（若原文为英文则保留英文风格扩写）。"
)

_EXPAND_USER_PROMPT = (
    "请将下面段落扩写为 {num} 个不同的版本（每个版本独立成稿，侧重点可不同："
    "氛围、动作、对话等），输出 JSON 数组：\n"
    '["版本1文本", "版本2文本", "版本3文本"]\n'
    "要求：每个版本都比原文更长更详细；只输出 JSON 数组，不要解释。\n\n"
    "【待扩写段落】\n{content}"
)

_VERSION_SPLIT = re.compile(r"\n\s*(?:版本\s*[一二三123]\s*[：:]\s*|VERSION\s*\d+\s*[：:]\s*|---+\s*$)", re.I)


def _extract_versions(text: str, num: int) -> list[str]:
    """从 LLM 输出中提取 N 个版本文本（优先 JSON，其次分隔符）。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # 优先 JSON 数组
    match = re.search(r"\[.*\]", text, re.S)
    if match:
        try:
            import json

            data = json.loads(match.group(0))
            if isinstance(data, list):
                versions = [str(v).strip() for v in data if str(v).strip()]
                if versions:
                    return versions[:num]
        except json.JSONDecodeError:
            pass

    # 分隔符拆分（版本 1：… 版本 2：…）
    parts = [p.strip() for p in _VERSION_SPLIT.split(text) if p.strip()]
    if len(parts) >= 2:
        return parts[:num]
    return [text]


async def expand_segment(
    client,
    content: str,
    num_versions: int = 3,
    stream_cb=None,
) -> list[str]:
    """扩写一个分段，返回 N 个版本文本。

    :param client: AsyncLLMClient
    :param stream_cb: 流式回调（收到原始输出增量）
    """
    user = _EXPAND_USER_PROMPT.format(num=num_versions, content=content)
    result = await client.chat(
        [{"role": "system", "content": EXPAND_SYSTEM_PROMPT},
         {"role": "user", "content": user}],
        stream_cb=stream_cb,
        temperature=0.8,
    )
    versions = _extract_versions(result, num_versions)
    if not versions or any(not version.strip() for version in versions):
        raise ValueError("扩写 API 返回了空版本，未保存任何扩写结果。")
    # 补足数量
    while len(versions) < num_versions:
        versions.append(versions[-1])
    return versions
