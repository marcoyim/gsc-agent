"""Sync five independent Search Analytics datasets into SQLite."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from gsc_agent.db.store import Store, dimensions_json
from gsc_agent.errors import FATAL_SYNC_TYPES, GscAgentError
from gsc_agent.sync.datasets import DAILY_ROW_CAP, DATA_STATE, DATASETS, ROW_LIMIT, SEARCH_TYPE, DatasetSpec
from gsc_agent.sync.retry import call_with_retry
from gsc_agent.util import iter_dates, redact, utc_now

Sleep = Callable[[float], None]


@dataclass
class DatasetSyncResult:
    dataset: str
    status: str
    rows_fetched: int = 0
    truncated: bool = False
    error_class: str | None = None
    error_message: str | None = None
    days: dict[str, str] = field(default_factory=dict)


@dataclass
class SyncResult:
    site_url: str
    start_date: str
    end_date: str
    datasets: list[DatasetSyncResult]

    @property
    def ok(self) -> bool:
        return all(item.status in {"completed", "empty", "truncated"} for item in self.datasets)


class Syncer:
    def __init__(
        self,
        store: Store,
        client: Any,
        *,
        row_limit: int = ROW_LIMIT,
        daily_row_cap: int = DAILY_ROW_CAP,
        sleep: Sleep | None = None,
        max_attempts: int = 5,
    ) -> None:
        if not 1 <= row_limit <= ROW_LIMIT:
            raise ValueError("rowLimit 必須介於 1 與 25000")
        self.store = store
        self.client = client
        self.row_limit = row_limit
        self.daily_row_cap = daily_row_cap
        self.sleep = sleep
        self.max_attempts = max_attempts

    def sync(self, site_url: str, start: str, end: str) -> SyncResult:
        days = iter_dates(start, end)
        property_id = self.store.upsert_property(site_url)
        results: list[DatasetSyncResult] = []
        for spec in DATASETS:
            result = self._sync_dataset(property_id, site_url, spec, days, start, end)
            results.append(result)
            if result.error_class in {"not_authorized", "token_expired", "permission_denied", "quota", "network"}:
                break
        return SyncResult(site_url=site_url, start_date=start, end_date=end, datasets=results)

    def _sync_dataset(
        self,
        property_id: int,
        site_url: str,
        spec: DatasetSpec,
        days: list[str],
        start: str,
        end: str,
    ) -> DatasetSyncResult:
        sync_run_id = self.store.start_sync_run(
            property_id=property_id,
            dataset=spec.name,
            aggregation_type=spec.aggregation_type,
            data_state=DATA_STATE,
            start_date=start,
            end_date=end,
        )
        result = DatasetSyncResult(dataset=spec.name, status="running")
        fatal: GscAgentError | None = None
        for day in days:
            try:
                fetched = self._fetch_day(sync_run_id, site_url, spec, day)
            except GscAgentError as exc:
                message = redact(str(exc))[:500]
                error_class = _error_class(exc)
                self._record_failed_request(sync_run_id, spec, day, error_class, message)
                self.store.mark_day_failed(
                    spec.name,
                    property_id=property_id,
                    day=day,
                    sync_run_id=sync_run_id,
                    note=f"本次請求失敗，保留此日舊資料，分析時不採用。{message}",
                )
                result.days[day] = "failed"
                result.error_class = error_class
                result.error_message = message
                if isinstance(exc, FATAL_SYNC_TYPES) or error_class in {"quota", "network"}:
                    fatal = exc
                    break
                continue
            stored = self.store.replace_day(
                spec.name,
                property_id=property_id,
                day=day,
                rows=fetched.rows,
                sync_run_id=sync_run_id,
                status="truncated" if fetched.truncated else ("empty" if not fetched.rows else "completed"),
                note=fetched.note,
                aggregation_type=spec.aggregation_type,
            )
            result.rows_fetched += stored
            result.truncated = result.truncated or fetched.truncated
            result.days[day] = "truncated" if fetched.truncated else ("empty" if stored == 0 else "completed")
        result.status = _summarize(result, fatal)
        self.store.finish_sync_run(
            sync_run_id,
            status=result.status,
            rows_fetched=result.rows_fetched,
            truncated=result.truncated,
            error_class=result.error_class,
            error_message=result.error_message,
            coverage_note=_coverage_note(result),
        )
        return result

    def _fetch_day(self, sync_run_id: int, site_url: str, spec: DatasetSpec, day: str) -> "DayFetch":
        rows: list[dict[str, Any]] = []
        start_row = 0
        truncated = False
        while True:
            if len(rows) >= self.daily_row_cap:
                truncated = True
                break
            remaining = self.daily_row_cap - len(rows)
            page_limit = min(self.row_limit, remaining)
            body = {
                "startDate": day,
                "endDate": day,
                "dimensions": list(spec.dimensions),
                "type": SEARCH_TYPE,
                "aggregationType": spec.aggregation_type,
                "rowLimit": page_limit,
                "startRow": start_row,
                "dataState": DATA_STATE,
            }

            def _call(body: dict[str, Any] = body) -> dict[str, Any]:
                return self.client.query_search_analytics(site_url, body)

            retry_kwargs: dict[str, Any] = {"max_attempts": self.max_attempts}
            if self.sleep is not None:
                retry_kwargs["sleep"] = self.sleep
            response, retry_count = call_with_retry(_call, **retry_kwargs)
            batch = response.get("rows") or []
            response_aggregation = response.get("responseAggregationType")
            self.store.insert_api_request(
                sync_run_id=sync_run_id,
                request_date=day,
                dimensions_json=dimensions_json(spec.dimensions),
                search_type=SEARCH_TYPE,
                aggregation_type=spec.aggregation_type,
                data_state=DATA_STATE,
                start_row=start_row,
                row_limit=page_limit,
                rows_returned=len(batch),
                response_aggregation_type=response_aggregation,
                http_status=200,
                retry_count=retry_count,
                status="ok",
                error_class=None,
                error_message=None,
                requested_at=utc_now(),
            )
            if not batch:
                break
            rows.extend(_normalize_rows(spec, day, batch))
            if len(batch) < page_limit:
                break
            start_row += page_limit
            if len(rows) >= self.daily_row_cap:
                truncated = True
                break
        note = "API 成功但沒有列。" if not rows else "已寫入 API 返回的列。"
        if truncated:
            note = (
                "此日返回列數達到 Search Analytics 每日上限（約 50,000）或本次同步上限。"
                "結果是按點擊排序的前列，不是完整查詢全集。"
            )
        return DayFetch(rows=rows, truncated=truncated, note=note)

    def _record_failed_request(
        self,
        sync_run_id: int,
        spec: DatasetSpec,
        day: str,
        error_class: str,
        message: str,
    ) -> None:
        self.store.insert_api_request(
            sync_run_id=sync_run_id,
            request_date=day,
            dimensions_json=dimensions_json(spec.dimensions),
            search_type=SEARCH_TYPE,
            aggregation_type=spec.aggregation_type,
            data_state=DATA_STATE,
            start_row=0,
            row_limit=self.row_limit,
            rows_returned=None,
            response_aggregation_type=None,
            http_status=None,
            retry_count=0,
            status="error",
            error_class=error_class,
            error_message=message,
            requested_at=utc_now(),
        )


@dataclass
class DayFetch:
    rows: list[dict[str, Any]]
    truncated: bool
    note: str


def _normalize_rows(spec: DatasetSpec, day: str, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw in batch:
        keys = list(raw.get("keys") or [])
        if spec.name == "property_daily":
            item: dict[str, Any] = {}
        else:
            if len(keys) < len(spec.dimensions):
                continue
            item = {dimension: keys[index] for index, dimension in enumerate(spec.dimensions)}
        item.update(
            {
                "clicks": float(raw.get("clicks") or 0),
                "impressions": float(raw.get("impressions") or 0),
                "ctr": float(raw.get("ctr") or 0),
                "position": float(raw.get("position") or 0),
                "source_date": keys[0] if spec.name == "property_daily" and keys else day,
            }
        )
        normalized.append(item)
    return normalized


def _summarize(result: DatasetSyncResult, fatal: GscAgentError | None) -> str:
    statuses = set(result.days.values())
    if fatal is not None and not statuses.intersection({"completed", "empty", "truncated"}):
        return "failed"
    if not statuses:
        return "failed"
    if statuses == {"empty"}:
        return "empty"
    if "failed" in statuses:
        return "partial" if statuses.intersection({"completed", "empty", "truncated"}) else "failed"
    if "truncated" in statuses:
        return "truncated"
    return "completed"


def _coverage_note(result: DatasetSyncResult) -> str:
    parts = [f"{day}:{status}" for day, status in result.days.items()]
    return "、".join(parts)


def _error_class(exc: GscAgentError) -> str:
    name = type(exc).__name__
    return {
        "NotAuthorized": "not_authorized",
        "TokenExpired": "token_expired",
        "PermissionDenied": "permission_denied",
        "QuotaExceeded": "quota",
        "NetworkError": "network",
        "TransientServerError": "network",
        "ApiError": "api",
    }.get(name, "api")
