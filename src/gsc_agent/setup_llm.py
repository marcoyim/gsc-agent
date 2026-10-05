"""Save a language-model choice without putting the API key in config.toml."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from gsc_agent.agent.llm import _is_local
from gsc_agent.config import load_config, load_dotenv_file
from gsc_agent.errors import ConfigError


@dataclass(frozen=True)
class PlatformChoice:
    key: str
    label: str
    kind: str
    base_url: str = ""
    model: str = ""
    key_hint: str = ""


PLATFORMS: tuple[PlatformChoice, ...] = (
    PlatformChoice(
        "ollama",
        "本機 Ollama（不需要 key，搜尋資料留在這台電腦）",
        "ollama",
        "http://127.0.0.1:11434",
        "llama3.1",
    ),
    PlatformChoice(
        "deepseek",
        "DeepSeek",
        "remote",
        "https://api.deepseek.com/v1",
        "deepseek-v4-flash",
        "在 platform.deepseek.com 建立的 key",
    ),
    PlatformChoice(
        "gemini",
        "Google Gemini",
        "remote",
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini-3.8-flash",
        "在 aistudio.google.com 建立的 key",
    ),
    PlatformChoice(
        "qwen-hk",
        "通義千問 Qwen（香港節點）",
        "remote",
        "https://cn-hongkong.dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen-plus",
        "阿里雲 Model Studio 香港地區的 key",
    ),
    PlatformChoice(
        "custom",
        "其他相容網址（自己貼上）",
        "custom",
        "",
        "",
        "該平台的 API key",
    ),
)

_MAX_TOOL_CALLS = "max_tool_calls = 12"
_TIMEOUT_SECONDS = "timeout_seconds = 180"


def llm_is_ready(config_path: Path, env_path: Path | None = None) -> bool:
    load_dotenv_file(env_path or Path(".env"))
    if not config_path.is_file():
        return False
    try:
        loaded = load_config(config_path)
    except ConfigError:
        return False
    agent = loaded.agent
    provider = agent.provider.strip().lower()
    if provider == "ollama":
        if not agent.ollama_host.strip() or not agent.ollama_model.strip():
            return False
        return _is_local(agent.ollama_host) or agent.allow_remote
    if provider != "openai_compatible":
        return False
    return bool(
        os.environ.get("OPENAI_API_KEY", "").strip()
        and agent.openai_base_url.strip()
        and agent.openai_model.strip()
        and agent.allow_remote
    )


def save_llm_settings(
    config_path: Path,
    env_path: Path,
    *,
    base_url: str,
    model: str,
    api_key: str | None = None,
    provider: str = "openai_compatible",
    example_path: Path | None = None,
) -> None:
    kind = provider.strip().lower()
    if kind not in {"openai_compatible", "ollama"}:
        raise ConfigError("只支援 ollama 或 openai_compatible。")
    url = _clean_url(base_url)
    model_name = _clean_model(model)
    _ensure_config(config_path, example_path)
    text = config_path.read_text(encoding="utf-8")
    text = _replace_line(text, "max_tool_calls", _MAX_TOOL_CALLS)
    text = _replace_line(text, "timeout_seconds", _TIMEOUT_SECONDS)
    if kind == "ollama":
        remote = not _is_local(url)
        text = _replace_line(text, "provider", 'provider = "ollama"')
        text = _replace_line(text, "allow_remote", f"allow_remote = {'true' if remote else 'false'}")
        text = _replace_line(text, "ollama_host", f"ollama_host = {_toml_string(url)}")
        text = _replace_line(text, "ollama_model", f"ollama_model = {_toml_string(model_name)}")
        config_path.write_text(text, encoding="utf-8")
        return
    if not api_key:
        raise ConfigError("這個平台需要 API key。")
    key = _clean_key(api_key)
    text = _replace_line(text, "provider", 'provider = "openai_compatible"')
    text = _replace_line(text, "allow_remote", "allow_remote = true")
    text = _replace_line(text, "openai_base_url", f"openai_base_url = {_toml_string(url)}")
    text = _replace_line(text, "openai_model", f"openai_model = {_toml_string(model_name)}")
    config_path.write_text(text, encoding="utf-8")
    _write_api_key(env_path, key)


def _ensure_config(config_path: Path, example_path: Path | None) -> None:
    if config_path.is_file():
        return
    source = example_path or Path("config.example.toml")
    if not source.is_file():
        raise ConfigError(f"找不到設定範本：{source}")
    config_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")


def _clean_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("AI 網址必須是 http 或 https，例如 https://api.example.com/v1")
    if any(char in url for char in "\n\r\"'"):
        raise ConfigError("AI 網址不能包含引號或換行。")
    return url


def _clean_model(value: str) -> str:
    model = value.strip()
    if not model or any(char in model for char in "\n\r\"'"):
        raise ConfigError("模型名稱不能是空白，也不能包含引號或換行。")
    return model


def _clean_key(value: str) -> str:
    key = value.strip()
    if not key or any(char in key for char in "\n\r"):
        raise ConfigError("API key 不能是空白，也不能包含換行。")
    if "'" in key or '"' in key:
        raise ConfigError("API key 不能包含引號。請改在 .env 手動寫入。")
    return key


def _toml_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _replace_line(text: str, key: str, rendered: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}\s*=.*$", re.MULTILINE)
    if pattern.search(text) is None:
        raise ConfigError(f"設定檔缺少 {key}。請從 config.example.toml 重新複製。")
    return pattern.sub(rendered, text, count=1)


def _write_api_key(env_path: Path, api_key: str) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    assignment = f"OPENAI_API_KEY='{api_key}'"
    replaced = False
    updated: list[str] = []
    for line in lines:
        if line.startswith("OPENAI_API_KEY="):
            updated.append(assignment)
            replaced = True
        else:
            updated.append(line)
    if not replaced:
        if updated and updated[-1] != "":
            updated.append("")
        updated.append(assignment)
    env_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
    env_path.chmod(0o600)
