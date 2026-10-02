"""Fictional Search Console rows for a demo that never contacts Google.

The property, brand, and URLs use the reserved ``.example`` domain. They are
not a real website.
"""

from __future__ import annotations

from pathlib import Path

from gsc_agent.analysis.engine import analyze
from gsc_agent.config import AgentConfig, AnalysisConfig, AppConfig, PositionBand, ScoringConfig
from gsc_agent.db.migrate import open_migrated
from gsc_agent.db.store import Store
from gsc_agent.report.markdown import render_report
from gsc_agent.util import iter_dates

DEMO_SITE = "https://demo-harbour-services.example/"
DEMO_START = "2026-01-05"
DEMO_END = "2026-01-07"
SERVICE_PAGE = "https://demo-harbour-services.example/services/demo-consulting"
ARTICLE_PAGE = "https://demo-harbour-services.example/articles/demo-guide"
OTHER_PAGE = "https://demo-harbour-services.example/about"


def demo_app_config() -> AppConfig:
    return AppConfig(
        analysis=AnalysisConfig(
            target_country="HKG",
            brand_terms=("示範港灣", "Demo Harbour"),
            service_intent_terms=("顧問", "服務", "預約", "收費"),
            informational_terms=("是什麼", "指南", "教學"),
            service_url_patterns=(r"^https://demo-harbour-services\.example/services/",),
            article_url_patterns=(r"^https://demo-harbour-services\.example/articles/",),
        ),
        scoring=ScoringConfig(
            min_impressions_for_ranking=50,
            low_confidence_impressions=30,
            min_article_clicks_for_link=3,
            position_bands=(
                PositionBand(
                    4.0,
                    10.0,
                    5,
                    "排名約 4 到 10 使用這個區間自己的點擊上限，不是全站 CTR 基準。",
                ),
                PositionBand(
                    10.0,
                    20.0,
                    2,
                    "排名約 10 到 20 使用另一個更低的點擊上限。",
                ),
            ),
        ),
        agent=AgentConfig(provider="none", allow_remote=False),
    )


def seed_demo(store: Store) -> str:
    property_id = store.upsert_property(DEMO_SITE, permission_level="siteOwner")
    for day in iter_dates(DEMO_START, DEMO_END):
        _put(store, "property_daily", property_id, day, [_metric(40, 1000, 9.0)], "byProperty")
        _put(
            store,
            "query",
            property_id,
            day,
            [
                _labeled("query", "示範港灣顧問", 10, 100, 1.5),
                _labeled("query", "示範顧問服務", 1, 80, 8.0),
                _labeled("query", "示範服務指南", 5, 60, 6.0),
                _labeled("query", "冷門示範詞", 0, 2, 14.0),
                _labeled("query", "預約示範服務", 0, 4, 9.0),
                _labeled("query", "即時預約服務", 1, 90, 1.8),
                _labeled("query", "示範顧問收費", 6, 40, 5.0),
            ],
            "byProperty",
        )
        _put(
            store,
            "page",
            property_id,
            day,
            [
                _labeled("page", SERVICE_PAGE, 8, 220, 7.0),
                _labeled("page", ARTICLE_PAGE, 12, 190, 6.0),
                _labeled("page", OTHER_PAGE, 1, 30, 15.0),
            ],
            "byPage",
        )
        _put(
            store,
            "country",
            property_id,
            day,
            [
                _labeled("country", "hkg", 30, 700, 8.0),
                _labeled("country", "twn", 6, 200, 11.0),
                _labeled("country", "usa", 4, 100, 14.0),
            ],
            "byProperty",
        )
        _put(
            store,
            "query_page_country",
            property_id,
            day,
            [
                _detail("示範顧問服務", SERVICE_PAGE, "hkg", 1, 80, 8.0),
                _detail("預約示範服務", SERVICE_PAGE, "hkg", 0, 4, 9.0),
                _detail("即時預約服務", SERVICE_PAGE, "hkg", 1, 90, 1.8),
                _detail("示範港灣顧問", SERVICE_PAGE, "hkg", 1, 100, 8.0),
                _detail("示範服務指南", ARTICLE_PAGE, "hkg", 5, 60, 6.0),
                _detail("示範顧問服務", SERVICE_PAGE, "twn", 0, 80, 8.0),
                _detail("示範顧問收費", ARTICLE_PAGE, "hkg", 6, 40, 5.0),
                _detail("示範顧問收費", SERVICE_PAGE, "hkg", 0, 10, 12.0),
            ],
            "byPage",
        )
    return DEMO_SITE


def run_demo(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "demo.sqlite"
    connection = open_migrated(database)
    try:
        store = Store(connection)
        seed_demo(store)
        config = demo_app_config()
        result = analyze(store, config, DEMO_SITE, DEMO_START, DEMO_END, target_country="HKG")
        report = directory / "report.md"
        report.write_text(render_report(result), encoding="utf-8")
    finally:
        connection.close()
    return report


def _put(store: Store, dataset: str, property_id: int, day: str, rows: list[dict], aggregation: str) -> None:
    store.replace_day(
        dataset,
        property_id=property_id,
        day=day,
        rows=rows,
        sync_run_id=0,
        status="completed",
        note="虛構示範資料，不是從 Google 同步。",
        aggregation_type=aggregation,
    )


def _metric(clicks: float, impressions: float, position: float) -> dict:
    return {
        "clicks": clicks,
        "impressions": impressions,
        "ctr": (clicks / impressions) if impressions else 0.0,
        "position": position,
    }


def _labeled(field: str, value: str, clicks: float, impressions: float, position: float) -> dict:
    return {field: value, **_metric(clicks, impressions, position)}


def _detail(query: str, page: str, country: str, clicks: float, impressions: float, position: float) -> dict:
    return {"query": query, "page": page, "country": country, **_metric(clicks, impressions, position)}
