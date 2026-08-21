"""离线回归：OpenAI 流式响应防护与空扩写保护。"""
import asyncio
import json

import httpx

from src.core.api_client import AsyncLLMClient
from src.core.expansion_pipeline import expand_segment
from src.modules.api_protocols import validate_stream_response
from src.modules.llm_client import LLMClient, LLMError


class AsyncReasoningOnlyClient:
    """模拟只输出推理字段、没有正文的服务。"""

    async def chat(self, *_args, **_kwargs):
        return ""


def response(status, headers, body):
    """创建 HTTPX 测试响应。"""
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    return httpx.Response(status, headers=headers, content=body, request=request)


def test_protocol_validation():
    """HTML 和非 SSE 响应必须失败；标准 SSE 必须通过。"""
    try:
        validate_stream_response("openai", "text/html; charset=utf-8")
        raise AssertionError("HTML response was accepted")
    except ValueError as exc:
        assert "/v1" in str(exc)
    try:
        validate_stream_response("openai", "application/json")
        raise AssertionError("Non-SSE response was accepted")
    except ValueError:
        pass
    validate_stream_response("openai", "text/event-stream; charset=utf-8")


def test_sync_client():
    """同步客户端应读取正常正文，并拒绝 HTML 和空 SSE。"""
    from unittest.mock import patch

    good = b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n'
    html = b"<!doctype html><html><body>gateway</body></html>"
    empty = b'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\n\ndata: [DONE]\n\n'
    client = LLMClient({"base_url": "https://example.test/v1", "api_key": "key", "model": "m", "protocol": "openai"})
    for body, content_type, expected in ((good, "text/event-stream", "OK"), (html, "text/html", "html"), (empty, "text/event-stream", "empty")):
        with patch("httpx.Client.stream") as stream:
            stream.return_value.__enter__.return_value = response(200, {"content-type": content_type}, body)
            try:
                result = client._stream_once({}, {}, None)
                assert expected == "OK" and result == "OK"
            except LLMError as exc:
                assert expected != "OK"
                assert ("HTML" in str(exc)) if expected == "html" else ("正文 content" in str(exc))


async def test_async_client():
    """异步客户端应同样拒绝只含推理字段的 SSE。"""
    from unittest.mock import patch

    empty = b'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}\n\ndata: [DONE]\n\n'
    client = AsyncLLMClient({"base_url": "https://example.test/v1", "api_key": "key", "model": "m", "protocol": "openai"})

    class StreamContext:
        async def __aenter__(self):
            return response(200, {"content-type": "text/event-stream"}, empty)

        async def __aexit__(self, *_args):
            return False

    with patch("httpx.AsyncClient.stream", return_value=StreamContext()):
        try:
            await client._stream_once({}, {}, None, httpx.Timeout(1.0))
            raise AssertionError("Empty async stream was accepted")
        except LLMError as exc:
            assert "正文 content" in str(exc)


def test_expansion_guard():
    """扩写器不得将空模型回答填充成空版本。"""
    try:
        asyncio.run(expand_segment(AsyncReasoningOnlyClient(), "原文", 2))
        raise AssertionError("Empty expansion was accepted")
    except ValueError as exc:
        assert "空版本" in str(exc)


if __name__ == "__main__":
    test_protocol_validation()
    test_sync_client()
    asyncio.run(test_async_client())
    test_expansion_guard()
    print("API_STREAM_GUARD_OK")
