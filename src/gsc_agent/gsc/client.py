"""Readonly Search Console client.

The client lists properties and queries Search Analytics. It has no write methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from gsc_agent.gsc.errors import classify_exception
from gsc_agent.util import property_kind


@dataclass(frozen=True)
class SiteEntry:
    site_url: str
    permission_level: str
    kind: str


class SearchConsoleClient(Protocol):
    def list_sites(self) -> list[SiteEntry]:
        """Return properties visible to the authorized account."""

    def query_search_analytics(self, site_url: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST searchAnalytics.query. ``body`` uses the official request fields."""


class GoogleSearchConsoleClient:
    def __init__(self, credentials: Any) -> None:
        try:
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise RuntimeError("尚未安裝 Google API 套件。請重新安裝 gsc-agent。") from exc
        self._service = build("searchconsole", "v1", credentials=credentials, cache_discovery=False)

    def list_sites(self) -> list[SiteEntry]:
        try:
            response = self._service.sites().list().execute()
        except Exception as exc:
            raise classify_exception(exc) from None
        entries: list[SiteEntry] = []
        for item in response.get("siteEntry") or []:
            site_url = str(item.get("siteUrl") or "")
            if not site_url:
                continue
            entries.append(
                SiteEntry(
                    site_url=site_url,
                    permission_level=str(item.get("permissionLevel") or ""),
                    kind=property_kind(site_url),
                )
            )
        return entries

    def query_search_analytics(self, site_url: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._service.searchanalytics().query(siteUrl=site_url, body=body).execute()
        except Exception as exc:
            raise classify_exception(exc) from None
