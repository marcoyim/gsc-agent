"""Map Search Console HTTP failures onto typed errors without leaking tokens."""

from __future__ import annotations

from gsc_agent.errors import (
    ApiError,
    NetworkError,
    NotAuthorized,
    PermissionDenied,
    QuotaExceeded,
    TokenExpired,
    TransientServerError,
)
from gsc_agent.util import redact

_NETWORK_NAMES = {
    "ServerNotFoundError",
    "HttpLib2Error",
    "TimeoutError",
    "Timeout",
    "ConnectionError",
    "ConnectionResetError",
    "URLError",
    "SSLError",
    "ProtocolError",
}


def classify_status(status: int, body_text: str) -> Exception:
    body = redact(body_text)[:500]
    if status == 401:
        return TokenExpired("Search Console 拒絕了憑證。請重新執行 gsc-agent auth。")
    if status == 429 or _looks_like_quota(body):
        return QuotaExceeded("Search Console 配額或速率限制。請稍後再同步。")
    if status in {500, 502, 503, 504}:
        return TransientServerError(f"Search Console 暫時無法回應（HTTP {status}）。")
    if status == 403:
        return PermissionDenied("這個 Google 帳戶無法讀取該 Search Console 資源。")
    if status == 0:
        return NetworkError("無法連線到 Search Console。")
    return ApiError(f"Search Console API 錯誤（HTTP {status}）。")


def classify_exception(exc: Exception) -> Exception:
    status = getattr(getattr(exc, "resp", None), "status", None)
    content = getattr(exc, "content", b"")
    if isinstance(content, bytes):
        text = content.decode("utf-8", errors="replace")
    else:
        text = str(content or exc)
    if status is None and type(exc).__name__ in _NETWORK_NAMES:
        return NetworkError("無法連線到 Search Console。")
    if status is None:
        return ApiError(redact(str(exc))[:500])
    mapped = classify_status(int(status), text)
    return mapped


def _looks_like_quota(body: str) -> bool:
    lowered = body.lower()
    return any(token in lowered for token in ("quotaexceeded", "ratelimitexceeded", "userratelimitexceeded", "quota"))


def is_not_authorized(exc: Exception) -> bool:
    return isinstance(exc, (NotAuthorized, TokenExpired))
