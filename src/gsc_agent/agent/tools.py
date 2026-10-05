"""Read-only tools. SQL is fixed in this module; arguments are bound parameters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gsc_agent.agent.guard import wrap_untrusted
from gsc_agent.analysis.engine import analyze
from gsc_agent.config import AppConfig
from gsc_agent.db.store import Store
from gsc_agent.errors import DatabaseError, MixedGrainError
from gsc_agent.util import iter_dates, parse_calendar_date

_COVERAGE_NOTE = "某個查詢沒有出現，代表 API 未返回，不代表曝光為 0。明細合計不是網站總覽。"


@dataclass
class ToolContext:
    store: Store
    config: AppConfig
    site_url: str
    property_id: int
    start_date: str
    end_date: str
    target_country: str


def get_data_coverage(ctx: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    start, end = _dates(ctx, arguments)
    rows = ctx.store.coverage(ctx.property_id, start, end)
    expected = iter_dates(start, end)
    return {
        "executed": True,
        "dataset_note": _COVERAGE_NOTE,
        "expected_dates": expected,
        "rows": [
            {
                "dataset": row["dataset"],
                "date": row["coverage_date"],
                "status": row["status"],
                "rows_stored": row["rows_stored"],
            }
            for row in rows
        ],
    }


def get_property_summary(ctx: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    start, end = _dates(ctx, arguments)
    metrics = ctx.store.as_site_total("property_daily", ctx.property_id, start, end)
    return {
        "executed": True,
        "source_dataset": "property_daily",
        "aggregation_type": "byProperty",
        "search_type": "web",
        "data_state": "final",
        "start_date": start,
        "end_date": end,
        "clicks": metrics.clicks,
        "impressions": metrics.impressions,
        "ctr": metrics.ctr,
        "position": metrics.position,
        "note": "這是網站總覽。不要用查詢或明細合計取代它。",
    }


def get_top_queries(ctx: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    start, end = _dates(ctx, arguments)
    limit = _limit(arguments)
    contains = arguments.get("contains")
    contains_map = {"query": str(contains)} if isinstance(contains, str) and contains else None
    rows = ctx.store.grouped_rows(
        "query",
        ctx.property_id,
        start,
        end,
        contains=contains_map,
        limit=limit,
    )
    return {
        "executed": True,
        "source_dataset": "query",
        "not_site_total": True,
        "note": _COVERAGE_NOTE,
        "queries": [_public_query_row(row) for row in rows],
    }


def get_query_pages(ctx: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    start, end = _dates(ctx, arguments)
    equals: dict[str, str] = {}
    country = arguments.get("country")
    if isinstance(country, str) and country.strip():
        equals["country"] = country.strip()
    rows = ctx.store.grouped_rows(
        "query_page_country",
        ctx.property_id,
        start,
        end,
        equals=equals or None,
        limit=_limit(arguments, default=40),
    )
    return {
        "executed": True,
        "source_dataset": "query_page_country",
        "not_site_total": True,
        "note": _COVERAGE_NOTE + " 每一列是一個查詢在一個頁面上的表現，不是網站總數。",
        "rows": [_public_detail_row(row) for row in rows],
    }


def get_page_queries(ctx: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    page = arguments.get("page")
    if not isinstance(page, str) or not page.strip():
        return {"executed": False, "error": "missing_page", "message": "get_page_queries 需要 page。"}
    start, end = _dates(ctx, arguments)
    rows = ctx.store.grouped_rows(
        "query_page_country",
        ctx.property_id,
        start,
        end,
        equals={"page": page},
        limit=_limit(arguments),
    )
    return {
        "executed": True,
        "source_dataset": "query_page_country",
        "not_site_total": True,
        "page": wrap_untrusted(page),
        "note": _COVERAGE_NOTE,
        "rows": [_public_detail_row(row) for row in rows],
    }


def compare_countries(ctx: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    start, end = _dates(ctx, arguments)
    rows = ctx.store.grouped_rows("country", ctx.property_id, start, end)
    target = str(arguments.get("target_country") or ctx.target_country)
    return {
        "executed": True,
        "source_dataset": "country",
        "aggregation_type": "byProperty",
        "not_from_query_page_country": True,
        "target_country": target,
        "rows": [
            {
                "country": row["country"],
                "is_target": str(row["country"]).casefold() == target.casefold(),
                "clicks": row["clicks"],
                "impressions": row["impressions"],
                "ctr": row["ctr"],
                "position": row["position"],
            }
            for row in rows
        ],
    }


def compare_periods(ctx: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        start_a = parse_calendar_date(str(arguments["start_a"]))
        end_a = parse_calendar_date(str(arguments["end_a"]))
        start_b = parse_calendar_date(str(arguments["start_b"]))
        end_b = parse_calendar_date(str(arguments["end_b"]))
        iter_dates(start_a, end_a)
        iter_dates(start_b, end_b)
    except (KeyError, ValueError) as exc:
        return {"executed": False, "error": "invalid_dates", "message": str(exc)}
    first = ctx.store.as_site_total("property_daily", ctx.property_id, start_a, end_a)
    second = ctx.store.as_site_total("property_daily", ctx.property_id, start_b, end_b)
    return {
        "executed": True,
        "source_dataset": "property_daily",
        "period_a": _period_payload(start_a, end_a, first),
        "period_b": _period_payload(start_b, end_b, second),
        "click_delta": second.clicks - first.clicks,
        "impression_delta": second.impressions - first.impressions,
        "note": "比較的是 property 每日總覽，不是把不同維度加總。",
    }


def get_opportunity_candidates(ctx: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    start, end = _dates(ctx, arguments)
    country = str(arguments.get("target_country") or ctx.target_country)
    try:
        result = analyze(ctx.store, ctx.config, ctx.site_url, start, end, target_country=country)
    except (DatabaseError, MixedGrainError) as exc:
        return {"executed": False, "error": "analysis_failed", "message": str(exc)}
    return {
        "executed": True,
        "site_clicks": result.site_totals.clicks,
        "site_impressions": result.site_totals.impressions,
        "site_source": result.site_totals_source,
        "unreturned_query_note": result.unreturned_query_note,
        "ranked_service_opportunities": [_opportunity_payload(item) for item in result.ranked_opportunities],
        "ranked_link_checks": [_opportunity_payload(item) for item in result.ranked_links],
        "low_confidence_count": len(result.low_confidence) + len(result.low_confidence_links),
        "note": "低信心項目沒有排成優先順序。這裡沒有轉換率。",
    }


def _dates(ctx: Any, arguments: dict[str, Any]) -> tuple[str, str]:
    start = parse_calendar_date(str(arguments.get("start_date") or ctx.start_date))
    end = parse_calendar_date(str(arguments.get("end_date") or ctx.end_date))
    iter_dates(start, end)
    return start, end


def _limit(arguments: dict[str, Any], default: int = 20) -> int:
    raw = arguments.get("limit", default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 20
    return max(1, min(value, 50))


def _public_query_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "query": wrap_untrusted(str(row["query"])),
        "clicks": row["clicks"],
        "impressions": row["impressions"],
        "ctr": row["ctr"],
        "position": row["position"],
    }


def _public_detail_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "query": wrap_untrusted(str(row["query"])),
        "page": wrap_untrusted(str(row["page"])),
        "country": row["country"],
        "clicks": row["clicks"],
        "impressions": row["impressions"],
        "ctr": row["ctr"],
        "position": row["position"],
    }


def _period_payload(start: str, end: str, metrics: Any) -> dict[str, Any]:
    return {
        "start_date": start,
        "end_date": end,
        "clicks": metrics.clicks,
        "impressions": metrics.impressions,
        "ctr": metrics.ctr,
        "position": metrics.position,
    }


def _opportunity_payload(item: Any) -> dict[str, Any]:
    return {
        "category": item.category,
        "query": wrap_untrusted(item.query),
        "page": wrap_untrusted(item.page),
        "related_page": wrap_untrusted(item.related_page) if item.related_page else None,
        "country": item.country,
        "start_date": item.start_date,
        "end_date": item.end_date,
        "clicks": item.clicks,
        "impressions": item.impressions,
        "ctr": item.ctr,
        "position": item.position,
        "confidence": item.confidence,
    }


def make_context(
    store: Store,
    config: AppConfig,
    site_url: str,
    start: str,
    end: str,
    *,
    target_country: str | None = None,
) -> ToolContext:
    row = store.require_property(site_url)
    parse_calendar_date(start)
    parse_calendar_date(end)
    iter_dates(start, end)
    return ToolContext(
        store=store,
        config=config,
        site_url=site_url,
        property_id=int(row["id"]),
        start_date=start,
        end_date=end,
        target_country=(target_country or config.analysis.target_country).upper(),
    )
