"""Local storage for sync metadata and Search Console facts.

Fact tables are independent. Only ``property_daily`` may be read as the site total.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from gsc_agent.db.migrate import SEARCH_TYPE
from gsc_agent.errors import DatabaseError, MixedGrainError
from gsc_agent.util import property_kind, utc_now

TRUSTED_STATUSES = ("completed", "truncated")

_FACT_SPECS: dict[str, dict[str, Any]] = {
    "property_daily": {
        "table": "fact_property_daily",
        "dimensions": (),
    },
    "query": {
        "table": "fact_query",
        "dimensions": ("query",),
    },
    "page": {
        "table": "fact_page",
        "dimensions": ("page",),
    },
    "country": {
        "table": "fact_country",
        "dimensions": ("country",),
    },
    "query_page_country": {
        "table": "fact_query_page_country",
        "dimensions": ("query", "page", "country"),
        "extra": ("aggregation_type",),
    },
}


@dataclass(frozen=True)
class Metrics:
    clicks: float
    impressions: float
    ctr: float
    position: float

    @classmethod
    def empty(cls) -> "Metrics":
        return cls(0.0, 0.0, 0.0, 0.0)


def combine_metrics(left: Metrics, right: Metrics) -> Metrics:
    clicks = left.clicks + right.clicks
    impressions = left.impressions + right.impressions
    ctr = (clicks / impressions) if impressions else 0.0
    weighted = left.position * left.impressions + right.position * right.impressions
    position = (weighted / impressions) if impressions else 0.0
    return Metrics(clicks=clicks, impressions=impressions, ctr=ctr, position=position)


class Store:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.conn = connection

    def upsert_property(self, site_url: str, permission_level: str | None = None) -> int:
        now = utc_now()
        kind = property_kind(site_url)
        self.conn.execute(
            """
            INSERT INTO properties (site_url, permission_level, property_type, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(site_url) DO UPDATE SET
                permission_level = COALESCE(excluded.permission_level, properties.permission_level),
                property_type = excluded.property_type,
                last_seen_at = excluded.last_seen_at
            """,
            (site_url, permission_level, kind, now, now),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM properties WHERE site_url = ?", (site_url,)).fetchone()
        if row is None:
            raise DatabaseError("無法寫入 property")
        return int(row["id"])

    def get_property(self, site_url: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM properties WHERE site_url = ?", (site_url,)).fetchone()

    def require_property(self, site_url: str) -> sqlite3.Row:
        row = self.get_property(site_url)
        if row is None:
            raise DatabaseError(f"資料庫沒有這個資源，請先同步：{site_url}")
        return row

    def start_sync_run(
        self,
        *,
        property_id: int,
        dataset: str,
        aggregation_type: str,
        data_state: str,
        start_date: str,
        end_date: str,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO sync_runs (
                property_id, dataset, search_type, aggregation_type, data_state,
                start_date, end_date, status, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)
            """,
            (property_id, dataset, SEARCH_TYPE, aggregation_type, data_state, start_date, end_date, utc_now()),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def finish_sync_run(
        self,
        sync_run_id: int,
        *,
        status: str,
        rows_fetched: int,
        truncated: bool,
        error_class: str | None = None,
        error_message: str | None = None,
        coverage_note: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            UPDATE sync_runs
            SET status = ?, rows_fetched = ?, truncated = ?, error_class = ?,
                error_message = ?, coverage_note = ?, finished_at = ?
            WHERE id = ?
            """,
            (
                status,
                rows_fetched,
                1 if truncated else 0,
                error_class,
                error_message,
                coverage_note,
                utc_now(),
                sync_run_id,
            ),
        )
        self.conn.commit()

    def insert_api_request(self, **fields: Any) -> None:
        self.conn.execute(
            """
            INSERT INTO api_requests (
                sync_run_id, request_date, dimensions_json, search_type, aggregation_type,
                data_state, start_row, row_limit, rows_returned, response_aggregation_type,
                http_status, retry_count, status, error_class, error_message, requested_at
            ) VALUES (
                :sync_run_id, :request_date, :dimensions_json, :search_type, :aggregation_type,
                :data_state, :start_row, :row_limit, :rows_returned, :response_aggregation_type,
                :http_status, :retry_count, :status, :error_class, :error_message, :requested_at
            )
            """,
            fields,
        )
        self.conn.commit()

    def replace_day(
        self,
        dataset: str,
        *,
        property_id: int,
        day: str,
        rows: list[dict[str, Any]],
        sync_run_id: int,
        status: str,
        note: str,
        aggregation_type: str | None = None,
    ) -> int:
        spec = _FACT_SPECS[dataset]
        table = spec["table"]
        dimensions: tuple[str, ...] = spec["dimensions"]
        collapsed = _collapse_rows(rows, dimensions)
        try:
            self.conn.execute(
                f"DELETE FROM {table} WHERE property_id = ? AND coverage_date = ? AND search_type = ?",
                (property_id, day, SEARCH_TYPE),
            )
            for row in collapsed:
                self._insert_fact(dataset, property_id, day, row, sync_run_id, aggregation_type)
            self._upsert_coverage(
                property_id=property_id,
                dataset=dataset,
                day=day,
                status=status,
                rows_stored=len(collapsed),
                sync_run_id=sync_run_id,
                note=note,
            )
            self.conn.commit()
        except sqlite3.Error as exc:
            self.conn.rollback()
            raise DatabaseError("寫入本地資料失敗") from exc
        return len(collapsed)

    def mark_day_failed(
        self,
        dataset: str,
        *,
        property_id: int,
        day: str,
        sync_run_id: int,
        note: str,
    ) -> None:
        spec = _FACT_SPECS[dataset]
        existing = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM {spec['table']} WHERE property_id = ? AND coverage_date = ? AND search_type = ?",
            (property_id, day, SEARCH_TYPE),
        ).fetchone()
        self._upsert_coverage(
            property_id=property_id,
            dataset=dataset,
            day=day,
            status="failed",
            rows_stored=int(existing["n"]),
            sync_run_id=sync_run_id,
            note=note,
        )
        self.conn.commit()

    def _insert_fact(
        self,
        dataset: str,
        property_id: int,
        day: str,
        row: dict[str, Any],
        sync_run_id: int,
        aggregation_type: str | None,
    ) -> None:
        spec = _FACT_SPECS[dataset]
        columns = [
            "property_id",
            "coverage_date",
            "search_type",
            *spec["dimensions"],
            "clicks",
            "impressions",
            "ctr",
            "position",
            "sync_run_id",
        ]
        values: list[Any] = [property_id, day, SEARCH_TYPE]
        for name in spec["dimensions"]:
            values.append(row[name])
        clicks = float(row["clicks"])
        impressions = float(row["impressions"])
        ctr = (clicks / impressions) if impressions else float(row.get("ctr") or 0.0)
        position = float(row["position"])
        values.extend([clicks, impressions, ctr, position, sync_run_id])
        if "extra" in spec:
            columns.append("aggregation_type")
            values.append(aggregation_type or row.get("aggregation_type") or "")
        placeholders = ", ".join("?" for _ in columns)
        self.conn.execute(
            f"INSERT INTO {spec['table']} ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )

    def _upsert_coverage(
        self,
        *,
        property_id: int,
        dataset: str,
        day: str,
        status: str,
        rows_stored: int,
        sync_run_id: int,
        note: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO day_coverage (
                property_id, dataset, search_type, coverage_date, status, rows_stored, sync_run_id, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(property_id, dataset, search_type, coverage_date) DO UPDATE SET
                status = excluded.status,
                rows_stored = excluded.rows_stored,
                sync_run_id = excluded.sync_run_id,
                note = excluded.note
            """,
            (property_id, dataset, SEARCH_TYPE, day, status, rows_stored, sync_run_id, note),
        )

    def coverage(self, property_id: int, start: str, end: str, dataset: str | None = None) -> list[sqlite3.Row]:
        sql = """
            SELECT * FROM day_coverage
            WHERE property_id = ? AND search_type = ? AND coverage_date >= ? AND coverage_date <= ?
        """
        params: list[Any] = [property_id, SEARCH_TYPE, start, end]
        if dataset is not None:
            sql += " AND dataset = ?"
            params.append(dataset)
        sql += " ORDER BY dataset, coverage_date"
        return list(self.conn.execute(sql, params))

    def site_totals(self, property_id: int, start: str, end: str) -> Metrics:
        return self._sum_trusted("property_daily", property_id, start, end)

    def as_site_total(self, dataset: str, property_id: int, start: str, end: str) -> Metrics:
        if dataset != "property_daily":
            raise MixedGrainError(
                f"資料集 {dataset} 的曝光或點擊不能加總成網站總數。網站總數只來自 property_daily。"
            )
        return self.site_totals(property_id, start, end)

    def dataset_totals(self, dataset: str, property_id: int, start: str, end: str) -> Metrics:
        """Sum one dataset. Callers must not label a non-property dataset as the site total."""
        return self._sum_trusted(dataset, property_id, start, end)

    def _sum_trusted(self, dataset: str, property_id: int, start: str, end: str) -> Metrics:
        spec = _FACT_SPECS[dataset]
        row = self.conn.execute(
            f"""
            SELECT
                COALESCE(SUM(f.clicks), 0) AS clicks,
                COALESCE(SUM(f.impressions), 0) AS impressions,
                COALESCE(SUM(f.position * f.impressions), 0) AS weighted_position
            FROM {spec["table"]} AS f
            JOIN day_coverage AS c
              ON c.property_id = f.property_id
             AND c.coverage_date = f.coverage_date
             AND c.search_type = f.search_type
             AND c.dataset = ?
            WHERE f.property_id = ?
              AND f.search_type = ?
              AND f.coverage_date >= ?
              AND f.coverage_date <= ?
              AND c.status IN ('completed', 'truncated')
            """,
            (dataset, property_id, SEARCH_TYPE, start, end),
        ).fetchone()
        return _metrics_from_sum(row)

    def grouped_rows(
        self,
        dataset: str,
        property_id: int,
        start: str,
        end: str,
        *,
        equals: dict[str, str] | None = None,
        limit: int | None = None,
        contains: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        spec = _FACT_SPECS[dataset]
        dimensions: tuple[str, ...] = spec["dimensions"]
        if not dimensions:
            metrics = self._sum_trusted(dataset, property_id, start, end)
            return [
                {
                    "clicks": metrics.clicks,
                    "impressions": metrics.impressions,
                    "ctr": metrics.ctr,
                    "position": metrics.position,
                }
            ]
        select_dims = ", ".join(f"f.{name}" for name in dimensions)
        group_dims = ", ".join(f"f.{name}" for name in dimensions)
        sql = f"""
            SELECT {select_dims},
                SUM(f.clicks) AS clicks,
                SUM(f.impressions) AS impressions,
                SUM(f.position * f.impressions) AS weighted_position
            FROM {spec["table"]} AS f
            JOIN day_coverage AS c
              ON c.property_id = f.property_id
             AND c.coverage_date = f.coverage_date
             AND c.search_type = f.search_type
             AND c.dataset = ?
            WHERE f.property_id = ?
              AND f.search_type = ?
              AND f.coverage_date >= ?
              AND f.coverage_date <= ?
              AND c.status IN ('completed', 'truncated')
        """
        params: list[Any] = [dataset, property_id, SEARCH_TYPE, start, end]
        for key, value in (equals or {}).items():
            if key not in dimensions:
                raise DatabaseError("篩選欄位不存在於這個資料集")
            if key == "country":
                sql += " AND LOWER(f.country) = LOWER(?)"
            else:
                sql += f" AND f.{key} = ?"
            params.append(value)
        for key, value in (contains or {}).items():
            if key not in dimensions:
                raise DatabaseError("篩選欄位不存在於這個資料集")
            sql += f" AND f.{key} LIKE ? ESCAPE '\\'"
            params.append(_like_contains(value))
        sql += f" GROUP BY {group_dims} ORDER BY clicks DESC, impressions DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = []
        for row in self.conn.execute(sql, params):
            item = {name: row[name] for name in dimensions}
            metrics = _metrics_from_sum(row)
            item.update(
                {
                    "clicks": metrics.clicks,
                    "impressions": metrics.impressions,
                    "ctr": metrics.ctr,
                    "position": metrics.position,
                }
            )
            rows.append(item)
        return rows

    def find_query(self, property_id: int, query: str, start: str, end: str) -> dict[str, Any] | None:
        rows = self.grouped_rows("query", property_id, start, end, equals={"query": query}, limit=1)
        return rows[0] if rows else None

    def table_names(self) -> list[str]:
        return [
            row["name"]
            for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
        ]

    def fact_count(self, dataset: str) -> int:
        table = _FACT_SPECS[dataset]["table"]
        row = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"])

    def latest_sync_runs(self, property_id: int) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT * FROM sync_runs
                WHERE property_id = ?
                ORDER BY id DESC
                """,
                (property_id,),
            )
        )


def _metrics_from_sum(row: sqlite3.Row | None) -> Metrics:
    if row is None:
        return Metrics.empty()
    clicks = float(row["clicks"] or 0)
    impressions = float(row["impressions"] or 0)
    weighted = float(row["weighted_position"] or 0)
    ctr = (clicks / impressions) if impressions else 0.0
    position = (weighted / impressions) if impressions else 0.0
    return Metrics(clicks=clicks, impressions=impressions, ctr=ctr, position=position)


def _collapse_rows(rows: list[dict[str, Any]], dimensions: tuple[str, ...]) -> list[dict[str, Any]]:
    collapsed: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(name) for name in dimensions)
        current = collapsed.get(key)
        if current is None:
            collapsed[key] = {
                **row,
                "clicks": float(row["clicks"]),
                "impressions": float(row["impressions"]),
                "position": float(row["position"]),
            }
            continue
        left = Metrics(current["clicks"], current["impressions"], 0.0, current["position"])
        right = Metrics(float(row["clicks"]), float(row["impressions"]), 0.0, float(row["position"]))
        merged = combine_metrics(left, right)
        current["clicks"] = merged.clicks
        current["impressions"] = merged.impressions
        current["ctr"] = merged.ctr
        current["position"] = merged.position
    return list(collapsed.values())


def dimensions_json(dimensions: tuple[str, ...]) -> str:
    return json.dumps(list(dimensions), ensure_ascii=False)


def _like_contains(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
