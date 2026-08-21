"""LLM 客户端：OpenAI 兼容 /chat/completions 的同步流式调用。

在后台线程中调用（QThread），不阻塞 UI 线程。
支持 SSE 流式输出、连接/读取超时与最多 3 次重试。
"""
import json
import time

import httpx

from src.modules.api_protocols import (
    auth_headers, build_chat_body, chat_endpoint, extract_stream_text,
    models_endpoint, normalize_protocol, parse_model_catalog, validate_stream_response,
)
from src.pages.settings_page import SettingsManager

TIMEOUT = httpx.Timeout(30.0, read=180.0)  # 连接 30s（AGENTS 通用规则），读取放宽给长生成
MAX_RETRIES = 3
DEFAULT_MODEL = "deepseek-chat"


class LLMError(Exception):
    """LLM 调用失败（含未配置 API）。"""


def build_api_config(settings: SettingsManager | None = None) -> dict:
    """从配置读取并解密当前激活提供方的 API 设置。"""
    settings = settings or SettingsManager()
    provider = settings.get_active_provider()
    base_url = (provider.get("base_url") or "").strip().rstrip("/")
    api_key = provider.get("api_key_plain") or ""
    model = ((provider.get("models") or [""])[0] if provider.get("models")
             else "") or DEFAULT_MODEL
    protocol = normalize_protocol(provider.get("protocol", "openai"))
    return {
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
        "protocol": protocol,
    }


class LLMClient:
    """OpenAI 兼容 Chat Completions 客户端（同步、流式）。"""

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
        """按设置截断超长输入，防止小窗口模型上下文溢出。

        未显式配置上限时按模型名推断：GPT 系列较保守（8000），其他 16000。
        """
        try:
            from src.pages.settings_page import SettingsManager
            configured = int(SettingsManager().get("context_chars", 0) or 0)
        except Exception:  # noqa: BLE001  读取失败用默认
            configured = 0
        if configured <= 0:
            model = (self.model or "").lower()
            if "gpt" in model or "o1" in model or "o3" in model:
                configured = 8000
            else:
                configured = 16000
        limit = configured
        if limit <= 0:
            return messages
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
        return clipped

    def chat(self, messages: list[dict], stream_cb=None,
             temperature: float = 0.1, max_tokens: int | None = None,
             timeout=None) -> str:
        """发送对话请求；stream_cb(text_delta) 可选回调累积输出。返回完整文本。

        :param timeout: 可选超时（默认模块级 TIMEOUT）；warmup 等轻量场景传短超时。
        """
        messages = self._clip_messages(messages)
        ok, reason = self.ready()
        if not ok:
            raise LLMError(reason)
        body = build_chat_body(
            self.protocol, self.model, messages, temperature, max_tokens)
        headers = auth_headers(self.protocol, self.api_key)
        timeout = timeout or TIMEOUT
        last_err = "未知错误"
        for attempt in range(MAX_RETRIES):
            try:
                return self._stream_once(body, headers, stream_cb, timeout)
            except httpx.HTTPStatusError as exc:
                try:
                    exc.response.read()
                    detail = exc.response.text[:200]
                except Exception:  # noqa: BLE001  读响应体失败不影响重试
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
            if attempt < MAX_RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))
        raise LLMError(f"LLM 调用失败：{last_err}")

    def _stream_once(self, body: dict, headers: dict, stream_cb,
                     timeout=None) -> str:
        """单次流式请求：解析 SSE 的 data 行并累积 delta 内容。"""
        parts: list[str] = []
        timeout = timeout or TIMEOUT
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            with client.stream("POST", self._endpoint(), json=body, headers=headers) as resp:
                resp.raise_for_status()
                try:
                    validate_stream_response(self.protocol, resp.headers.get("content-type", ""))
                except ValueError as exc:
                    raise LLMError(str(exc)) from exc
                for line in resp.iter_lines():
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

    def list_models(self) -> list[str]:
        """读取当前中转站的模型目录。"""
        ok, reason = self.ready()
        if not ok:
            raise LLMError(reason)
        endpoint = models_endpoint(self.base_url, self.protocol, self.api_key)
        headers = auth_headers(self.protocol, self.api_key)
        try:
            with httpx.Client(
                timeout=httpx.Timeout(15.0, read=30.0),
                follow_redirects=True,
            ) as client:
                response = client.get(endpoint, headers=headers)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.text[:300]
            except Exception:  # noqa: BLE001
                detail = ""
            raise LLMError(
                f"模型目录请求失败（HTTP {exc.response.status_code}）：{detail}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError("读取模型目录超时") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"模型目录网络错误：{exc}") from exc
        except (ValueError, TypeError) as exc:
            raise LLMError("模型目录返回的不是有效 JSON") from exc
        models = parse_model_catalog(self.protocol, payload)
        if not models:
            raise LLMError(
                "中转站没有返回可用模型；请确认协议与 Base URL，或继续手动填写模型 ID"
            )
        return models

    def ping(self) -> str:
        """测试连接：返回模型简短回应。"""
        return self.chat(
            [{"role": "user", "content": "请只回复四个字：连接成功"}],
            temperature=0.0,
            max_tokens=8,
        ).strip() or "连接成功"

    def warmup(self, timeout=None) -> None:
        """网络预热（尽力而为）。

        完成进程内首次 HTTPS 调用，解锁后续线程网络。可在后台线程
        执行并传入短超时，避免阻塞 UI。
        """
        if not self.ready()[0]:
            return
        try:
            self.chat(
                [{"role": "user", "content": "hi"}],
                temperature=0.0,
                max_tokens=16,
                timeout=timeout or httpx.Timeout(3.0, read=4.0),
            )
        except Exception:  # noqa: BLE001  预热失败不影响后续（尽力而为）
            pass
