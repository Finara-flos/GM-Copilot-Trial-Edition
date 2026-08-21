"""异步 API 客户端：OpenAI 兼容 /chat/completions 的 asyncio 流式调用。

与第二阶段同步版共用同一套配置（Base URL / 加密 Key / 模型 / 超时 / 重试），
仅将网络层换为 httpx.AsyncClient + SSE 流式（resp.aiter_lines()）。
在任务队列所在的事件循环中调用，不阻塞 UI。
"""
import asyncio
import json

import httpx

from src.modules.api_protocols import (
    auth_headers, build_chat_body, chat_endpoint, extract_stream_text,
    normalize_protocol, validate_stream_response,
)
from src.modules.llm_client import (
    DEFAULT_MODEL,
    MAX_RETRIES,
    TIMEOUT,
    LLMError,
    build_api_config,
)

__all__ = ["AsyncLLMClient", "LLMError", "build_api_config"]


class AsyncLLMClient:
    """OpenAI 兼容 Chat Completions 异步客户端（SSE 流式）。"""

    def __init__(self, config: dict | None = None):
        cfg = config or build_api_config()
        self.base_url = cfg.get("base_url", "")
        self.api_key = cfg.get("api_key", "")
        self.model = cfg.get("model") or DEFAULT_MODEL
        self.protocol = normalize_protocol(cfg.get("protocol", "openai"))

    def ready(self) -> tuple[bool, str]:
        """是否可调用；返回 (可用, 不可用原因)。"""
        if not self.base_url or not self.api_key:
            return False, "未配置 API（请到设置页填写 Base URL 与 API Key）"
        if "://" not in self.base_url:
            return False, f"Base URL 无效：{self.base_url}"
        return True, ""

    def _endpoint(self) -> str:
        return chat_endpoint(self.base_url, self.protocol, self.model)

    def _clip_messages(self, messages: list[dict]) -> list[dict]:
        """按设置截断 user 内容，防止小窗口模型上下文溢出。

        仅截断最长的 user 消息；若总长仍超限则从尾部逐步裁剪，保证
        system 提示与最近内容保留。模型未显式配置上限时，按模型名
        推断安全默认（GPT 系列较保守）。
        """
        try:
            from src.pages.settings_page import SettingsManager
            configured = int(SettingsManager().get("context_chars", 0) or 0)
        except Exception:  # noqa: BLE001  读取失败用默认
            configured = 0
        if configured <= 0:
            # 未配置时按模型推断
            model = (self.model or "").lower()
            if "gpt" in model or "o1" in model or "o3" in model:
                configured = 8000
            else:
                configured = 16000
        limit = configured
        total = sum(len(m.get("content") or "") for m in messages)
        if total <= limit:
            return messages
        clipped = []
        sys_len = sum(len(m.get("content") or "")
                      for m in messages if m.get("role") == "system")
        available = max(limit - sys_len, 1000)
        for m in messages:
            content = m.get("content") or ""
            if m.get("role") == "system":
                clipped.append(m)
                continue
            if len(content) > available:
                content = content[:available]
                content += "\n\n…（内容过长已截断，请缩小输入或降低上下文上限）"
            clipped.append({**m, "content": content})
        # 若仍超限（多段大内容），从尾部整体裁剪
        total2 = sum(len(m.get("content") or "") for m in clipped)
        if total2 > limit:
            result = []
            used = 0
            for m in clipped:
                content = m.get("content") or ""
                if used + len(content) > limit and result:
                    result.append({
                        **m,
                        "content": content[: max(limit - used, 100)]
                        + "\n\n…（内容过长已截断）",
                    })
                    used = limit
                    break
                result.append(m)
                used += len(content)
            clipped = result
        return clipped

    async def chat(
        self,
        messages: list[dict],
        stream_cb=None,
        temperature: float = 0.1,
        max_tokens: int | None = None,
        timeout: httpx.Timeout | None = None,
        retries: int | None = None,
    ) -> str:
        """发送对话请求；stream_cb(text_delta) 可选回调累积输出。返回完整文本。

        支持超时与重试（500/超时/网络错误自动重试；retries 缺省用 MAX_RETRIES）。
        自动按设置截断超长输入，避免小上下文窗口模型（如部分 GPT 模型）溢出。
        """
        messages = self._clip_messages(messages)
        ok, reason = self.ready()
        if not ok:
            raise LLMError(reason)
        body = build_chat_body(
            self.protocol, self.model, messages, temperature, max_tokens)
        headers = auth_headers(self.protocol, self.api_key)
        timeout = timeout or TIMEOUT
        # retries 表示重试次数（初始 1 次 + retries 次重试）；缺省保持 MAX_RETRIES 次尝试
        if retries is None:
            max_attempts = MAX_RETRIES
        else:
            max_attempts = max(1, int(retries)) + 1
        last_err = "未知错误"
        for attempt in range(max_attempts):
            try:
                return await self._stream_once(body, headers, stream_cb, timeout)
            except httpx.HTTPStatusError as exc:
                try:
                    await exc.response.aread()
                    detail = exc.response.text[:200]
                except Exception:  # noqa: BLE001
                    detail = ""
                last_err = f"HTTP {exc.response.status_code}: {detail}"
            except httpx.TimeoutException:
                last_err = "请求超时"
            except httpx.HTTPError as exc:
                last_err = str(exc)
            except LLMError:
                raise
            except Exception as exc:  # noqa: BLE001  网络/解析异常统一重试
                last_err = str(exc)
            if attempt < max_attempts - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
        raise LLMError(f"API 调用失败：{last_err or '未知错误'}")

    async def _stream_once(
        self,
        body: dict,
        headers: dict,
        stream_cb,
        timeout: httpx.Timeout,
    ) -> str:
        """单次异步流式请求：解析 SSE 的 data 行并累积 delta 内容。"""
        parts: list[str] = []
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream(
                "POST", self._endpoint(), json=body, headers=headers
            ) as resp:
                resp.raise_for_status()
                try:
                    validate_stream_response(self.protocol, resp.headers.get("content-type", ""))
                except ValueError as exc:
                    raise LLMError(str(exc)) from exc
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if not payload or payload == "[DONE]":
                        continue
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        continue
                    content = extract_stream_text(self.protocol, obj)
                    if content:
                        parts.append(content)
                        if stream_cb:
                            stream_cb(content)
        result = "".join(parts)
        if not result.strip():
            raise LLMError("API 流式响应未包含正文 content；服务可能只返回推理过程或中途终止。")
        return result
