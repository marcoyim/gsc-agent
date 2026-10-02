"""Deterministic Search Console analysis. No model calls happen here."""

from __future__ import annotations

from dataclasses import dataclass, field

from gsc_agent.analysis.classify import (
    classify_brand,
    classify_intent,
    classify_page,
    country_matches,
    matching_band,
)
from gsc_agent.config import AppConfig
from gsc_agent.db.store import Metrics, Store
from gsc_agent.sync.datasets import DATASETS
from gsc_agent.util import fmt_num, iter_dates

VERIFICATION = (
    "在 Search Console 介面用相同資源、太平洋時間日期、國家與查詢核對這些數字；"
    "再人工閱讀頁面，確認標題與內容是否對應。若要判斷是否帶來客戶，需要另行接入詢盤或成交數據。"
    "本工具沒有這些數據，不能把曝光或點擊解讀成詢盤。"
)

NOT_RETURNED_NOTE = "API 未返回此查詢。不能因此斷定它沒有曝光。"


@dataclass
class Opportunity:
    category: str
    query: str
    page: str
    country: str
    start_date: str
    end_date: str
    clicks: float
    impressions: float
    ctr: float
    position: float
    action: str
    evidence: str
    uncertainty: str
    verification: str
    confidence: str
    facts: list[str]
    inferences: list[str]
    brand_class: str
    intent: str
    page_type: str
    related_page: str | None = None


@dataclass
class CoverageDataset:
    completed: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


@dataclass
class AnalysisResult:
    site_url: str
    property_type: str
    start_date: str
    end_date: str
    target_country: str
    site_totals: Metrics
    site_totals_source: str
    returned_query_totals: Metrics
    page_totals: Metrics
    country_totals: Metrics
    detail_totals: Metrics
    unreturned_query_note: str
    expected_dates: list[str]
    coverage: dict[str, CoverageDataset]
    country_rows: list[dict]
    brand_rows: list[dict]
    page_type_rows: list[dict]
    ranked_opportunities: list[Opportunity]
    low_confidence: list[Opportunity]
    ranked_links: list[Opportunity]
    low_confidence_links: list[Opportunity]


def analyze(
    store: Store,
    config: AppConfig,
    site_url: str,
    start: str,
    end: str,
    *,
    target_country: str | None = None,
) -> AnalysisResult:
    property_row = store.require_property(site_url)
    property_id = int(property_row["id"])
    country = (target_country or config.analysis.target_country).upper()
    expected = iter_dates(start, end)
    coverage = _coverage(store, property_id, start, end, expected)
    site_totals = store.as_site_total("property_daily", property_id, start, end)
    returned_query_totals = store.dataset_totals("query", property_id, start, end)
    page_totals = store.dataset_totals("page", property_id, start, end)
    country_totals = store.dataset_totals("country", property_id, start, end)
    detail_totals = store.dataset_totals("query_page_country", property_id, start, end)
    query_rows = store.grouped_rows("query", property_id, start, end)
    page_rows = store.grouped_rows("page", property_id, start, end)
    country_rows = store.grouped_rows("country", property_id, start, end)
    detail_rows = store.grouped_rows("query_page_country", property_id, start, end)
    ranked, low = _service_opportunities(
        detail_rows, config, country, start, end, coverage.get("query_page_country")
    )
    ranked_links, low_links = _link_opportunities(
        detail_rows, config, country, start, end, coverage.get("query_page_country")
    )
    return AnalysisResult(
        site_url=site_url,
        property_type=str(property_row["property_type"]),
        start_date=start,
        end_date=end,
        target_country=country,
        site_totals=site_totals,
        site_totals_source="property_daily",
        returned_query_totals=returned_query_totals,
        page_totals=page_totals,
        country_totals=country_totals,
        detail_totals=detail_totals,
        unreturned_query_note=_unreturned_note(site_totals, returned_query_totals),
        expected_dates=expected,
        coverage=coverage,
        country_rows=_with_share(country_rows, country_totals.impressions),
        brand_rows=_brand_summary(query_rows, config),
        page_type_rows=_page_summary(page_rows, config),
        ranked_opportunities=ranked,
        low_confidence=low,
        ranked_links=ranked_links,
        low_confidence_links=low_links,
    )


def explain_query(store: Store, site_url: str, query: str, start: str, end: str) -> dict:
    property_id = int(store.require_property(site_url)["id"])
    found = store.find_query(property_id, query, start, end)
    if found is None:
        return {
            "query": query,
            "status": "not_returned",
            "clicks": None,
            "impressions": None,
            "ctr": None,
            "position": None,
            "note": NOT_RETURNED_NOTE,
        }
    return {
        "query": query,
        "status": "returned",
        "clicks": found["clicks"],
        "impressions": found["impressions"],
        "ctr": found["ctr"],
        "position": found["position"],
        "note": "此查詢出現在資料集 query 的 API 返回列中。這仍不是完整查詢全集。",
    }


def _coverage(
    store: Store,
    property_id: int,
    start: str,
    end: str,
    expected: list[str],
) -> dict[str, CoverageDataset]:
    grouped: dict[str, CoverageDataset] = {spec.name: CoverageDataset() for spec in DATASETS}
    seen: dict[str, set[str]] = {spec.name: set() for spec in DATASETS}
    for row in store.coverage(property_id, start, end):
        dataset = str(row["dataset"])
        if dataset not in grouped:
            continue
        day = str(row["coverage_date"])
        status = str(row["status"])
        seen[dataset].add(day)
        bucket = getattr(grouped[dataset], status, None)
        if isinstance(bucket, list):
            bucket.append(day)
    for name, dates in seen.items():
        grouped[name].missing = [day for day in expected if day not in dates]
    return grouped


def _unreturned_note(site: Metrics, queries: Metrics) -> str:
    gap = site.impressions - queries.impressions
    if gap > 0:
        return (
            f"資料集 property_daily 的曝光比資料集 query 多 {fmt_num(gap)}。"
            "差額代表 API 沒有返回的查詢曝光（隱私過濾、匿名查詢或列數上限），"
            "不能分配給任何一個沒有出現的查詢，也不能把沒出現的查詢當成曝光 0。"
        )
    if gap < 0:
        return (
            "資料集 query 的曝光高於 property_daily。兩者聚合方式不同，"
            "這個差額不能解讀成負的遺漏查詢，也不能把 query 合計當成網站總數。"
        )
    return "這段範圍內 property_daily 與 query 的曝光相同。這仍不代表查詢清單完整。"


def _with_share(rows: list[dict], total_impressions: float) -> list[dict]:
    enriched = []
    for row in rows:
        share = (row["impressions"] / total_impressions) if total_impressions else 0.0
        enriched.append({**row, "impression_share_within_country_dataset": share})
    return enriched


def _brand_summary(rows: list[dict], config: AppConfig) -> list[dict]:
    buckets: dict[str, list[Metrics]] = {"brand": [], "non_brand": [], "brand_unknown": []}
    for row in rows:
        label = classify_brand(str(row["query"]), config.analysis.brand_terms)
        buckets[label].append(Metrics(row["clicks"], row["impressions"], row["ctr"], row["position"]))
    summary = []
    for label, metrics_rows in buckets.items():
        total = _sum_metrics(metrics_rows)
        summary.append(
            {
                "brand_class": label,
                "query_count": len(metrics_rows),
                "clicks": total.clicks,
                "impressions": total.impressions,
                "ctr": total.ctr,
                "position": total.position,
                "source_dataset": "query",
            }
        )
    return summary


def _page_summary(rows: list[dict], config: AppConfig) -> list[dict]:
    buckets: dict[str, list[Metrics]] = {"service": [], "article": [], "other": []}
    for row in rows:
        label = classify_page(str(row["page"]), config.analysis)
        buckets[label].append(Metrics(row["clicks"], row["impressions"], row["ctr"], row["position"]))
    summary = []
    for label, metrics_rows in buckets.items():
        total = _sum_metrics(metrics_rows)
        summary.append(
            {
                "page_type": label,
                "page_count": len(metrics_rows),
                "clicks": total.clicks,
                "impressions": total.impressions,
                "ctr": total.ctr,
                "position": total.position,
                "source_dataset": "page",
            }
        )
    return summary


def _sum_metrics(rows: list[Metrics]) -> Metrics:
    total = Metrics.empty()
    for row in rows:
        clicks = total.clicks + row.clicks
        impressions = total.impressions + row.impressions
        weighted = total.position * total.impressions + row.position * row.impressions
        total = Metrics(
            clicks=clicks,
            impressions=impressions,
            ctr=(clicks / impressions) if impressions else 0.0,
            position=(weighted / impressions) if impressions else 0.0,
        )
    return total


def _service_opportunities(
    rows: list[dict],
    config: AppConfig,
    target_country: str,
    start: str,
    end: str,
    coverage: CoverageDataset | None,
) -> tuple[list[Opportunity], list[Opportunity]]:
    ranked: list[Opportunity] = []
    low: list[Opportunity] = []
    truncated = bool(coverage and coverage.truncated)
    for row in rows:
        if not country_matches(str(row["country"]), target_country):
            continue
        query = str(row["query"])
        page = str(row["page"])
        brand = classify_brand(query, config.analysis.brand_terms)
        intent = classify_intent(query, config.analysis)
        page_type = classify_page(page, config.analysis)
        if page_type != "service" or brand != "non_brand" or intent not in {"service", "mixed"}:
            continue
        if row["impressions"] <= 0:
            continue
        band = matching_band(float(row["position"]), config.scoring.position_bands)
        if band is None or float(row["clicks"]) > band.max_clicks:
            continue
        confidence = "ranked"
        sample_notes: list[str] = []
        if intent == "mixed":
            confidence = "low"
            sample_notes.append("查詢同時符合服務意圖與資訊意圖，不強行排名。")
        if row["impressions"] < config.scoring.min_impressions_for_ranking:
            confidence = "low"
            sample_notes.append(
                f"曝光 {fmt_num(row['impressions'])} 低於排名門檻 "
                f"{config.scoring.min_impressions_for_ranking}，樣本不足。"
            )
        opportunity = _build_service_opportunity(
            row, start, end, target_country, brand, intent, page_type, band.reason, confidence, sample_notes, truncated
        )
        if confidence == "ranked":
            ranked.append(opportunity)
        else:
            low.append(opportunity)
    ranked.sort(key=lambda item: item.impressions, reverse=True)
    return ranked, low


def _build_service_opportunity(
    row: dict,
    start: str,
    end: str,
    target_country: str,
    brand: str,
    intent: str,
    page_type: str,
    band_reason: str,
    confidence: str,
    sample_notes: list[str],
    truncated: bool,
) -> Opportunity:
    facts = [
        f"點擊 {fmt_num(row['clicks'])}、曝光 {fmt_num(row['impressions'])}、CTR {fmt_num(row['ctr'])}、平均排名 {fmt_num(row['position'])}。",
        f"國家 {row['country']}，頁面類型 {page_type}，品牌分類 {brand}，意圖 {intent}。",
        "這些數字來自資料集 query_page_country（byPage），不是網站總覽。",
    ]
    inferences = [
        "規則判斷：目標國家、非品牌、服務頁、服務意圖與該排名區間的點擊上限同時成立，才列入人工檢查。這不是單看高曝光低 CTR。",
        band_reason,
        *sample_notes,
    ]
    uncertainty = "平均排名是曝光加權位置，不是固定名次。API 返回的是受隱私與列數限制的列，不是全部搜尋字。"
    if truncated:
        uncertainty += " 這段日期有被列數上限截斷的同步日，覆蓋不完整。"
    if confidence == "low":
        uncertainty += " 此項標為低信心，不進入排名。"
    return Opportunity(
        category="service_page_review",
        query=query_group_label(str(row["query"])),
        page=str(row["page"]),
        country=str(row["country"]),
        start_date=start,
        end_date=end,
        clicks=float(row["clicks"]),
        impressions=float(row["impressions"]),
        ctr=float(row["ctr"]),
        position=float(row["position"]),
        action="人工檢查此服務頁的標題、開頭內容與搜尋結果摘要是否對應這個非品牌查詢。這不是優化保證，也不是成交預測。",
        evidence=(
            f"資料集 query_page_country；aggregationType=byPage；searchType=web；dataState=final；"
            f"日期 {start} 至 {end}（太平洋時間，未經本機時區換算）；目標國家 {target_country}。"
        ),
        uncertainty=uncertainty,
        verification=VERIFICATION,
        confidence=confidence,
        facts=facts,
        inferences=inferences,
        brand_class=brand,
        intent=intent,
        page_type=page_type,
    )


def _link_opportunities(
    rows: list[dict],
    config: AppConfig,
    target_country: str,
    start: str,
    end: str,
    coverage: CoverageDataset | None,
) -> tuple[list[Opportunity], list[Opportunity]]:
    by_query: dict[str, list[dict]] = {}
    for row in rows:
        if not country_matches(str(row["country"]), target_country):
            continue
        by_query.setdefault(str(row["query"]), []).append(row)
    ranked: list[Opportunity] = []
    low: list[Opportunity] = []
    truncated = bool(coverage and coverage.truncated)
    for query, grouped in by_query.items():
        articles = [row for row in grouped if classify_page(str(row["page"]), config.analysis) == "article"]
        services = [row for row in grouped if classify_page(str(row["page"]), config.analysis) == "service"]
        if not articles or not services:
            continue
        service = max(services, key=lambda item: item["impressions"])
        for article in articles:
            if article["clicks"] <= 0 and article["impressions"] <= 0:
                continue
            confidence = "ranked"
            notes: list[str] = []
            if article["clicks"] < config.scoring.min_article_clicks_for_link:
                confidence = "low"
                notes.append("文章點擊低於設定的連結檢查門檻。")
            if article["impressions"] < config.scoring.min_impressions_for_ranking:
                confidence = "low"
                notes.append("文章曝光低於排名門檻，不強行排名。")
            opportunity = Opportunity(
                category="internal_link_review",
                query=query_group_label(query),
                page=str(article["page"]),
                related_page=str(service["page"]),
                country=str(article["country"]),
                start_date=start,
                end_date=end,
                clicks=float(article["clicks"]),
                impressions=float(article["impressions"]),
                ctr=float(article["ctr"]),
                position=float(article["position"]),
                action=(
                    "人工檢查這篇有點擊的文章是否適合連到相關服務頁。"
                    "共同查詢只代表搜尋字重疊，不代表讀者會經由連結詢盤。"
                ),
                evidence=(
                    f"資料集 query_page_country；同一查詢同時出現在文章頁與服務頁 {service['page']}；"
                    f"服務頁點擊 {fmt_num(service['clicks'])}、曝光 {fmt_num(service['impressions'])}、"
                    f"平均排名 {fmt_num(service['position'])}；日期 {start} 至 {end}；國家 {article['country']}。"
                    "明細合計不是網站總覽。"
                ),
                uncertainty=(
                    "這是人工檢查建議，不是內部連結效益的預測。"
                    + (" 同步日有截斷。" if truncated else "")
                    + (" 低信心，不進入排名。" if confidence == "low" else "")
                ),
                verification=VERIFICATION,
                confidence=confidence,
                facts=[
                    f"文章點擊 {fmt_num(article['clicks'])}、曝光 {fmt_num(article['impressions'])}、CTR {fmt_num(article['ctr'])}、平均排名 {fmt_num(article['position'])}。",
                    f"服務頁 {service['page']} 也有同一查詢的 API 返回列。",
                ],
                inferences=[
                    "規則判斷：文章與服務頁共享查詢，所以值得人工看是否建立內部連結。",
                    *notes,
                ],
                brand_class=classify_brand(query, config.analysis.brand_terms),
                intent=classify_intent(query, config.analysis),
                page_type="article",
            )
            if confidence == "ranked":
                ranked.append(opportunity)
            else:
                low.append(opportunity)
    ranked.sort(key=lambda item: item.clicks, reverse=True)
    return ranked, low


def query_group_label(query: str) -> str:
    return query
