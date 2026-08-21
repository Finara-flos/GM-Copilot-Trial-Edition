"""常见 LLM API 协议的请求构建与流式响应解析。"""
from urllib.parse import quote

PROTOCOL_OPENAI = "openai"
PROTOCOL_ANTHROPIC = "anthropic"
PROTOCOL_GEMINI = "gemini"
DEFAULT_PROTOCOL = PROTOCOL_OPENAI

PROTOCOL_OPTIONS = (
    ("OpenAI Chat Completions（兼容中转站）", PROTOCOL_OPENAI),
    ("Anthropic Messages", PROTOCOL_ANTHROPIC),
    ("Google Gemini generateContent", PROTOCOL_GEMINI),
)


def normalize_protocol(protocol: str) -> str:
    """返回受支持的协议标识，未知值回退 OpenAI。"""
    value = (protocol or DEFAULT_PROTOCOL).strip().lower()
    if value not in {PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC, PROTOCOL_GEMINI}:
        return DEFAULT_PROTOCOL
    return value


def _api_root(base_url: str, protocol: str) -> str:
    """清除用户可能粘贴的具体调用端点，得到 API 根地址。"""
    base = (base_url or "").strip().rstrip("/")
    suffixes = {
        PROTOCOL_OPENAI: ("/chat/completions", "/models"),
        PROTOCOL_ANTHROPIC: ("/messages", "/models"),
    }
    for suffix in suffixes.get(protocol, ()):
        if base.endswith(suffix):
            return base[:-len(suffix)]
    return base


def chat_endpoint(base_url: str, protocol: str, model: str) -> str:
    """构建当前协议的流式对话端点。"""
    protocol = normalize_protocol(protocol)
    root = _api_root(base_url, protocol)
    if protocol == PROTOCOL_OPENAI:
        return root + "/chat/completions"
    if protocol == PROTOCOL_ANTHROPIC:
        return root + "/messages"
    model_id = model.removeprefix("models/")
    return f"{root}/models/{quote(model_id, safe='-._')}:streamGenerateContent?alt=sse"


def models_endpoint(base_url: str, protocol: str, api_key: str) -> str:
    """构建当前协议的模型目录端点。"""
    protocol = normalize_protocol(protocol)
    root = _api_root(base_url, protocol)
    if protocol in {PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC}:
        return root + "/models"
    separator = "&" if "?" in root else "?"
    return f"{root}/models{separator}key={quote(api_key, safe='')}"


def auth_headers(protocol: str, api_key: str) -> dict[str, str]:
    """构建协议鉴权头。"""
    protocol = normalize_protocol(protocol)
    headers = {"Content-Type": "application/json"}
    if protocol == PROTOCOL_OPENAI:
        headers["Authorization"] = f"Bearer {api_key}"
    elif protocol == PROTOCOL_ANTHROPIC:
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    else:
        headers["x-goog-api-key"] = api_key
    return headers


def build_chat_body(protocol: str, model: str, messages: list[dict],
                    temperature: float, max_tokens: int | None) -> dict:
    """把统一 messages 转换成目标协议请求体。"""
    protocol = normalize_protocol(protocol)
    if protocol == PROTOCOL_OPENAI:
        body = {
            "model": model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        return body

    system_parts = [m.get("content", "") for m in messages
                    if m.get("role") == "system" and m.get("content")]
    dialogue = [m for m in messages if m.get("role") != "system"]
    if protocol == PROTOCOL_ANTHROPIC:
        body = {
            "model": model,
            "messages": [
                {
                    "role": "assistant" if m.get("role") == "assistant" else "user",
                    "content": m.get("content", ""),
                }
                for m in dialogue
            ],
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens or 4096,
        }
        if system_parts:
            body["system"] = "\n\n".join(system_parts)
        return body

    body = {
        "contents": [
            {
                "role": "model" if m.get("role") == "assistant" else "user",
                "parts": [{"text": m.get("content", "")}],
            }
            for m in dialogue
        ],
        "generationConfig": {"temperature": temperature},
    }
    if max_tokens:
        body["generationConfig"]["maxOutputTokens"] = max_tokens
    if system_parts:
        body["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
    return body


def validate_stream_response(protocol: str, content_type: str) -> None:
    """验证流式响应类型，避免 HTML 网页被静默当成空回答。"""
    value = (content_type or "").lower()
    protocol = normalize_protocol(protocol)
    if "text/html" in value:
        raise ValueError(
            "API 返回网页 HTML 而非接口响应；请检查 Base URL 是否缺少 /v1 或填入了管理页面地址。")
    if protocol in {PROTOCOL_OPENAI, PROTOCOL_ANTHROPIC} and "text/event-stream" not in value:
        raise ValueError(f"API 未返回 SSE 流（Content-Type: {content_type or '未知'}）")


def extract_completion_text(protocol: str, payload: dict) -> str:
    """从非流式完成响应中提取正文，供兼容或诊断场景使用。"""
    protocol = normalize_protocol(protocol)
    if protocol == PROTOCOL_OPENAI:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        return message.get("content") or ""
    if protocol == PROTOCOL_ANTHROPIC:
        blocks = payload.get("content") or []
        return "".join(block.get("text", "") for block in blocks if isinstance(block, dict))
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    return "".join(part.get("text", "") for part in parts if isinstance(part, dict))


def extract_stream_text(protocol: str, payload: dict) -> str:
    """从一个 SSE JSON 事件中提取增量文本。"""
    protocol = normalize_protocol(protocol)
    if protocol == PROTOCOL_OPENAI:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        return delta.get("content") or ""
    if protocol == PROTOCOL_ANTHROPIC:
        if payload.get("type") != "content_block_delta":
            return ""
        return (payload.get("delta") or {}).get("text") or ""
    candidates = payload.get("candidates") or []
    if not candidates:
        return ""
    parts = ((candidates[0].get("content") or {}).get("parts") or [])
    return "".join(part.get("text", "") for part in parts if isinstance(part, dict))


def parse_model_catalog(protocol: str, payload: dict) -> list[str]:
    """解析模型目录响应，返回去重后的模型 ID。"""
    protocol = normalize_protocol(protocol)
    rows = payload.get("models") if protocol == PROTOCOL_GEMINI else payload.get("data")
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        model_id = row.get("name") if protocol == PROTOCOL_GEMINI else row.get("id")
        model_id = str(model_id or "").strip()
        if protocol == PROTOCOL_GEMINI:
            model_id = model_id.removeprefix("models/")
            methods = row.get("supportedGenerationMethods") or []
            if methods and "generateContent" not in methods:
                continue
        if model_id and model_id not in result:
            result.append(model_id)
    return sorted(result, key=str.lower)
