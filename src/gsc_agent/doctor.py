"""Environment checks. A missing token is reported as not connected."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass

from gsc_agent.auth.oauth import inspect_token, validate_client_secrets
from gsc_agent.db.migrate import open_migrated
from gsc_agent.errors import (
    ConfigError,
    GscAgentError,
    NetworkError,
    NotAuthorized,
    PermissionDenied,
    QuotaExceeded,
    TokenExpired,
)
from gsc_agent.gsc.client import SiteEntry
from gsc_agent.paths import RuntimePaths, is_private_file
from gsc_agent.util import redact

ApiProbe = Callable[[], list[SiteEntry]]


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str
    remediation: str = ""


def run_doctor(paths: RuntimePaths, *, api_probe: ApiProbe | None = None) -> list[Check]:
    checks = [
        _python_check(),
        _database_check(paths),
        _oauth_check(paths),
        _token_check(paths),
    ]
    checks.extend(_api_checks(paths, api_probe=api_probe, token_state=checks[-1].status))
    return checks


def format_doctor(checks: list[Check]) -> str:
    lines: list[str] = []
    for check in checks:
        label = {"pass": "通過", "fail": "失敗", "skip": "略過"}[check.status]
        lines.append(f"[{label}] {check.name}：{check.detail}")
        if check.remediation:
            lines.append(f"       修復：{check.remediation}")
    return "\n".join(lines)


def format_property_line(site: SiteEntry) -> str:
    if site.kind == "domain":
        kind = "網域資源（sc-domain）"
    else:
        kind = "網址前綴資源（URL-prefix）"
    permission = site.permission_level or "未知"
    return f"{site.site_url} | {kind} | 權限 {permission}"


def doctor_failed(checks: list[Check]) -> bool:
    return any(check.status == "fail" for check in checks)


def _python_check() -> Check:
    version = sys.version.split()[0]
    if sys.version_info >= (3, 11):
        return Check("Python", "pass", f"Python {version}")
    return Check("Python", "fail", f"Python {version} 低於 3.11", "請改用 Python 3.11 或更新版本。")


def _database_check(paths: RuntimePaths) -> Check:
    try:
        connection = open_migrated(paths.database)
        row = connection.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        connection.close()
    except Exception:
        return Check(
            "本地資料庫",
            "fail",
            "無法建立或讀取 SQLite 資料庫。",
            f"確認這個路徑可以寫入：{paths.database}",
        )
    return Check("本地資料庫", "pass", f"schema version {row['version']}，路徑 {paths.database}")


def _oauth_check(paths: RuntimePaths) -> Check:
    try:
        validate_client_secrets(paths.client_secrets)
    except ConfigError as exc:
        return Check(
            "OAuth 設定",
            "fail",
            str(exc),
            "在 Google Cloud 建立 Desktop app OAuth 用戶端，只啟用 Search Console API，"
            f"把 JSON 存到 {paths.client_secrets}。不要把這個檔案提交到 Git。",
        )
    return Check("OAuth 設定", "pass", "已找到 Desktop app 用戶端 JSON。內容不會顯示。")


def _token_check(paths: RuntimePaths) -> Check:
    inspection = inspect_token(paths.token)
    messages = {
        "missing": "找不到 token，尚未授權。",
        "unreadable": "token 檔無法解析。",
        "wrong_scope": "token 的範圍不是 webmasters.readonly。",
        "expired": "token 已過期，而且不能重新整理。",
        "needs_refresh": "access token 已過期，仍有 refresh token。",
        "valid": "token 存在且未過期。內容不會顯示。",
    }
    detail = messages.get(inspection.state, "token 狀態不明。")
    if inspection.state == "valid" and not is_private_file(paths.token):
        return Check(
            "Token 狀態",
            "fail",
            "token 檔可以被同機其他使用者讀取。",
            f"請把 {paths.token} 的權限設為只有本人可讀。",
        )
    if inspection.state in {"valid", "needs_refresh"}:
        return Check("Token 狀態", "pass", detail)
    remediation = "執行 gsc-agent auth。授權只會要求 webmasters.readonly，token 會存在使用者資料目錄。"
    return Check("Token 狀態", "fail", detail, remediation)


def _api_checks(paths: RuntimePaths, *, api_probe: ApiProbe | None, token_state: str) -> list[Check]:
    if token_state != "pass":
        return [
            Check(
                "GSC API",
                "fail",
                "API 未連接：尚未完成有效授權，因此沒有呼叫 Search Console。",
                "先修正 token，再執行 gsc-agent auth 或 gsc-agent doctor。",
            ),
            Check(
                "可存取的資源",
                "fail",
                "尚未授權，沒有可列出的資源。",
                "授權完成後執行 gsc-agent properties。",
            ),
        ]
    try:
        sites = _load_sites(paths, api_probe)
    except PermissionDenied:
        return [
            Check(
                "GSC API",
                "fail",
                "API 呼叫被拒絕：這個帳戶沒有 Search Console 資源的讀取權限。",
                "確認登入的 Google 帳戶是該資源的使用者，而且 OAuth 範圍是 webmasters.readonly。",
            ),
            Check("可存取的資源", "fail", "無權限，無法列出資源。", "改用有權限的帳戶重新執行 gsc-agent auth。"),
        ]
    except TokenExpired:
        return [
            Check(
                "GSC API",
                "fail",
                "API 未連接：token 已過期或無法重新整理。",
                "重新執行 gsc-agent auth。",
            ),
            Check("可存取的資源", "fail", "token 無效，沒有列出資源。", "重新執行 gsc-agent auth。"),
        ]
    except NotAuthorized:
        return [
            Check("GSC API", "fail", "API 未連接：尚未授權。", "執行 gsc-agent auth。"),
            Check("可存取的資源", "fail", "尚未授權，沒有可列出的資源。", "執行 gsc-agent auth。"),
        ]
    except QuotaExceeded:
        return [
            Check("GSC API", "fail", "已授權，但 Search Console 回報配額或速率限制。", "稍後再執行 gsc-agent doctor。"),
            Check("可存取的資源", "fail", "配額限制期間無法列出資源。", "稍後再執行 gsc-agent properties。"),
        ]
    except NetworkError:
        return [
            Check("GSC API", "fail", "無法連線到 Search Console。", "檢查網路後再執行 gsc-agent doctor。"),
            Check("可存取的資源", "fail", "網絡錯誤，沒有列出資源。", "檢查網路後再執行 gsc-agent properties。"),
        ]
    except GscAgentError as exc:
        return [
            Check("GSC API", "fail", f"API 未連接：{redact(str(exc))[:200]}", "查看上一行的原因後再試。"),
            Check("可存取的資源", "fail", "這次沒有取得資源清單。", "修正 API 錯誤後再執行 gsc-agent properties。"),
        ]
    lines = [format_property_line(site) for site in sites]
    if not lines:
        return [
            Check("GSC API", "pass", "API 已連接。此帳戶目前沒有可讀取的 Search Console 資源。"),
            Check("可存取的資源", "fail", "API 已連接，但資源清單是空的。", "在 Search Console 加入資源，或換一個有權限的帳戶。"),
        ]
    preview = "；".join(lines)
    return [
        Check("GSC API", "pass", f"API 已連接。可讀取 {len(sites)} 個資源。"),
        Check("可存取的資源", "pass", preview),
    ]


def _load_sites(paths: RuntimePaths, api_probe: ApiProbe | None) -> list[SiteEntry]:
    if api_probe is not None:
        return api_probe()
    from gsc_agent.auth.oauth import load_credentials
    from gsc_agent.gsc.client import GoogleSearchConsoleClient

    credentials = load_credentials(paths.token)
    return GoogleSearchConsoleClient(credentials).list_sites()
