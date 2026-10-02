"""Desktop OAuth for the Search Console readonly scope.

Tokens are written only under the user data directory. This module never logs
the access token, refresh token, or client secret.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from gsc_agent.errors import ConfigError, NotAuthorized, TokenExpired
from gsc_agent.paths import READONLY_SCOPE, restrict_file_permissions
from gsc_agent.util import redact


@dataclass(frozen=True)
class TokenInspection:
    state: str
    scopes: tuple[str, ...] = ()
    has_refresh_token: bool = False
    expiry: str | None = None


def inspect_token(path: Path) -> TokenInspection:
    if not path.is_file():
        return TokenInspection(state="missing")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return TokenInspection(state="unreadable")
    if not isinstance(data, dict):
        return TokenInspection(state="unreadable")
    scopes = tuple(str(item) for item in data.get("scopes") or [])
    has_access = bool(data.get("token"))
    has_refresh = bool(data.get("refresh_token"))
    expiry_text = data.get("expiry")
    expiry = expiry_text if isinstance(expiry_text, str) else None
    if scopes and READONLY_SCOPE not in scopes:
        return TokenInspection(state="wrong_scope", scopes=scopes, has_refresh_token=has_refresh, expiry=expiry)
    if not has_access and not has_refresh:
        return TokenInspection(state="missing", scopes=scopes)
    expired = _is_expired(expiry)
    if expired and not has_refresh:
        return TokenInspection(state="expired", scopes=scopes, has_refresh_token=False, expiry=expiry)
    if expired and has_refresh:
        return TokenInspection(state="needs_refresh", scopes=scopes, has_refresh_token=True, expiry=expiry)
    return TokenInspection(state="valid", scopes=scopes, has_refresh_token=has_refresh, expiry=expiry)


def _is_expired(expiry: str | None) -> bool:
    if not expiry:
        return False
    text = expiry.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= datetime.now(timezone.utc)


def validate_client_secrets(path: Path) -> str:
    """Return 'desktop' or raise ConfigError. Does not return secret values."""
    if not path.is_file():
        raise ConfigError("找不到 OAuth 用戶端 JSON")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError("OAuth 用戶端 JSON 無法解析") from exc
    if not isinstance(data, dict):
        raise ConfigError("OAuth 用戶端 JSON 格式不正確")
    if "installed" in data:
        return "desktop"
    if "web" in data:
        raise ConfigError("這是網頁應用程式用戶端。請改建立 Desktop app 用戶端。")
    raise ConfigError("OAuth 用戶端 JSON 缺少 installed 區段")


def save_credentials_json(payload: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    restrict_file_permissions(path)


def run_local_oauth(client_secrets: Path, token_path: Path) -> None:
    """Open a browser loopback flow and store the readonly token."""
    validate_client_secrets(client_secrets)
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise ConfigError("尚未安裝 Google OAuth 套件。請重新安裝 gsc-agent。") from exc
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes=[READONLY_SCOPE])
    credentials = flow.run_local_server(port=0, open_browser=True, authorization_prompt_message="正在開啟瀏覽器以授權唯讀 Search Console 存取。")
    save_credentials_json(credentials.to_json(), token_path)


def load_credentials(token_path: Path):
    inspection = inspect_token(token_path)
    if inspection.state in {"missing", "unreadable"}:
        raise NotAuthorized("尚未授權。請先執行 gsc-agent auth。")
    if inspection.state == "wrong_scope":
        raise NotAuthorized("token 的權限範圍不是 webmasters.readonly。請重新授權。")
    if inspection.state == "expired":
        raise TokenExpired("token 已過期，而且沒有 refresh token。請重新執行 gsc-agent auth。")
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError as exc:
        raise ConfigError("尚未安裝 Google Auth 套件。請重新安裝 gsc-agent。") from exc
    credentials = Credentials.from_authorized_user_file(str(token_path), scopes=[READONLY_SCOPE])
    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception as exc:
            raise TokenExpired("token 已過期，重新整理失敗。請重新執行 gsc-agent auth。") from exc
        save_credentials_json(credentials.to_json(), token_path)
    elif not credentials.valid:
        raise TokenExpired("token 無效。請重新執行 gsc-agent auth。")
    return credentials


def safe_exception_text(exc: BaseException) -> str:
    return redact(str(exc))[:500]
