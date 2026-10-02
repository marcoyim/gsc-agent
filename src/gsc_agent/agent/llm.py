"""LLM providers. The default is no model. Remote providers require an explicit opt-in."""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from gsc_agent.config import AppConfig
from gsc_agent.errors import LLMNotConfigured, RemoteLLMNotAllowed
from gsc_agent.util import redact

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass
class ScriptedLLM:
    """Test double. Not a config provider, so a public config cannot select it by accident."""

    replies: list[str]
    remote: bool = False

    def complete(self, messages: list[dict[str, str]]) -> str:
        del messages
        if not self.replies:
            return '{"thought":"沒有更多步驟","final":"沒有更多腳本回覆。"}'
        return self.replies.pop(0)


class OllamaLLM:
    remote = False

    def __init__(self, host: str, model: str, timeout: float) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.timeout = timeout

    def complete(self, messages: list[dict[str, str]]) -> str:
        try:
            response = httpx.post(
                f"{self.host}/api/chat",
                json={"model": self.model, "messages": messages, "stream": False},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise LLMNotConfigured(f"無法呼叫本機 Ollama：{redact(str(exc))[:300]}") from None
        message = payload.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise LLMNotConfigured("Ollama 沒有回傳文字。")
        return content


class OpenAICompatibleLLM:
    remote = True

    def __init__(self, base_url: str, model: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def complete(self, messages: list[dict[str, str]]) -> str:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise LLMNotConfigured("未設定 OPENAI_API_KEY。")
        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"model": self.model, "messages": messages, "temperature": 0},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise LLMNotConfigured(f"遠端 LLM 呼叫失敗：{redact(str(exc))[:300]}") from None
        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMNotConfigured("遠端 LLM 的回應格式無法解讀。") from exc
        if not isinstance(content, str):
            raise LLMNotConfigured("遠端 LLM 沒有回傳文字。")
        return content


def prepare_llm(config: AppConfig, *, allow_remote_flag: bool):
    provider = config.agent.provider.strip().lower()
    timeout = float(config.agent.timeout_seconds)
    if provider in {"", "none"}:
        raise LLMNotConfigured("沒有設定 LLM。analyze 與 report 不需要模型，仍可使用。若要跑 agent，請把 provider 設為本機 ollama。")
    if provider == "ollama":
        local = _is_local(config.agent.ollama_host)
        if not local:
            _require_remote(config, allow_remote_flag)
        client = OllamaLLM(config.agent.ollama_host, config.agent.ollama_model, timeout)
        client.remote = not local
        return client
    if provider == "openai_compatible":
        _require_remote(config, allow_remote_flag)
        if not config.agent.openai_base_url or not config.agent.openai_model:
            raise LLMNotConfigured("openai_compatible 需要 openai_base_url 與 openai_model。")
        return OpenAICompatibleLLM(config.agent.openai_base_url, config.agent.openai_model, timeout)
    raise LLMNotConfigured(f"不支援的 LLM provider：{provider}")


def _require_remote(config: AppConfig, allow_remote_flag: bool) -> None:
    if config.agent.allow_remote and allow_remote_flag:
        return
    raise RemoteLLMNotAllowed(
        "遠端 LLM 會把查詢字、網址與 Search Console 指標送出本機。"
        "預設禁止。請在設定檔把 allow_remote 設為 true，並在指令加上 --allow-remote-llm。"
    )


def _is_local(url: str) -> bool:
    host = urlparse(url).hostname
    return host in _LOCAL_HOSTS
