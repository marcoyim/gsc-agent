"""Shared helpers that do not touch the network."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

_SECRET_PATTERNS = (
    re.compile(r"ya29\.[A-Za-z0-9_\-]+"),
    re.compile(r"1//[A-Za-z0-9_\-]+"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]+", re.IGNORECASE),
    re.compile(
        r'"(?:access_token|refresh_token|id_token|client_secret)"\s*:\s*"[^"]*"',
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:access_token|refresh_token|id_token|client_secret)=([^&\s]+)",
        re.IGNORECASE,
    ),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def redact(text: str) -> str:
    """Remove credential-shaped substrings from messages that may be stored or printed."""
    cleaned = text
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned


def parse_calendar_date(value: str) -> str:
    """Accept a YYYY-MM-DD calendar date and return it unchanged.

    Search Console dates are Pacific Time calendar dates. This function does
    not convert from the machine's local timezone.
    """
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"日期必須是 YYYY-MM-DD：{value!r}")
    date.fromisoformat(value)
    return value


def iter_dates(start: str, end: str) -> list[str]:
    start_day = date.fromisoformat(parse_calendar_date(start))
    end_day = date.fromisoformat(parse_calendar_date(end))
    if end_day < start_day:
        raise ValueError("結束日期不可早於開始日期")
    days: list[str] = []
    current = start_day
    while current <= end_day:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def property_kind(site_url: str) -> str:
    if site_url.startswith("sc-domain:"):
        return "domain"
    return "url_prefix"


def fmt_num(value: float | None) -> str:
    if value is None:
        return "未知"
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text
