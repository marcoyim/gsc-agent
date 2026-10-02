"""Dataset definitions aligned with the Search Analytics query method.

Dates are sent as the YYYY-MM-DD strings the user supplied. The API treats
those dates as Pacific Time (America/Los_Angeles). This package does not
convert them from the local timezone.

rowLimit maximum is 25,000. Pagination uses startRow until a response contains
zero rows. A separate daily cap of 50,000 rows per search type is recorded as
truncation; hitting it does not mean the query universe is complete.
"""

from __future__ import annotations

from dataclasses import dataclass

SEARCH_TYPE = "web"
DATA_STATE = "final"
ROW_LIMIT = 25_000
DAILY_ROW_CAP = 50_000


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    dimensions: tuple[str, ...]
    aggregation_type: str
    description: str


DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec("property_daily", ("date",), "byProperty", "每日 property 總覽"),
    DatasetSpec("query", ("query",), "byProperty", "查詢"),
    DatasetSpec("page", ("page",), "byPage", "頁面"),
    DatasetSpec("country", ("country",), "byProperty", "國家或地區"),
    DatasetSpec("query_page_country", ("query", "page", "country"), "byPage", "查詢、頁面與國家明細"),
)

DATASET_BY_NAME = {spec.name: spec for spec in DATASETS}
