CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS properties (
    id INTEGER PRIMARY KEY,
    site_url TEXT NOT NULL UNIQUE,
    permission_level TEXT,
    property_type TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    property_id INTEGER NOT NULL REFERENCES properties(id),
    dataset TEXT NOT NULL,
    search_type TEXT NOT NULL,
    aggregation_type TEXT NOT NULL,
    data_state TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    status TEXT NOT NULL,
    error_class TEXT,
    error_message TEXT,
    rows_fetched INTEGER NOT NULL DEFAULT 0,
    truncated INTEGER NOT NULL DEFAULT 0,
    coverage_note TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS api_requests (
    id INTEGER PRIMARY KEY,
    sync_run_id INTEGER NOT NULL REFERENCES sync_runs(id),
    request_date TEXT NOT NULL,
    dimensions_json TEXT NOT NULL,
    search_type TEXT NOT NULL,
    aggregation_type TEXT NOT NULL,
    data_state TEXT NOT NULL,
    start_row INTEGER NOT NULL,
    row_limit INTEGER NOT NULL,
    rows_returned INTEGER,
    response_aggregation_type TEXT,
    http_status INTEGER,
    retry_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error_class TEXT,
    error_message TEXT,
    requested_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS day_coverage (
    property_id INTEGER NOT NULL REFERENCES properties(id),
    dataset TEXT NOT NULL,
    search_type TEXT NOT NULL,
    coverage_date TEXT NOT NULL,
    status TEXT NOT NULL,
    rows_stored INTEGER NOT NULL DEFAULT 0,
    sync_run_id INTEGER,
    note TEXT,
    PRIMARY KEY (property_id, dataset, search_type, coverage_date)
);

CREATE TABLE IF NOT EXISTS fact_property_daily (
    property_id INTEGER NOT NULL REFERENCES properties(id),
    coverage_date TEXT NOT NULL,
    search_type TEXT NOT NULL,
    clicks REAL NOT NULL,
    impressions REAL NOT NULL,
    ctr REAL NOT NULL,
    position REAL NOT NULL,
    sync_run_id INTEGER NOT NULL,
    PRIMARY KEY (property_id, coverage_date, search_type)
);

CREATE TABLE IF NOT EXISTS fact_query (
    property_id INTEGER NOT NULL REFERENCES properties(id),
    coverage_date TEXT NOT NULL,
    search_type TEXT NOT NULL,
    query TEXT NOT NULL,
    clicks REAL NOT NULL,
    impressions REAL NOT NULL,
    ctr REAL NOT NULL,
    position REAL NOT NULL,
    sync_run_id INTEGER NOT NULL,
    PRIMARY KEY (property_id, coverage_date, search_type, query)
);

CREATE TABLE IF NOT EXISTS fact_page (
    property_id INTEGER NOT NULL REFERENCES properties(id),
    coverage_date TEXT NOT NULL,
    search_type TEXT NOT NULL,
    page TEXT NOT NULL,
    clicks REAL NOT NULL,
    impressions REAL NOT NULL,
    ctr REAL NOT NULL,
    position REAL NOT NULL,
    sync_run_id INTEGER NOT NULL,
    PRIMARY KEY (property_id, coverage_date, search_type, page)
);

CREATE TABLE IF NOT EXISTS fact_country (
    property_id INTEGER NOT NULL REFERENCES properties(id),
    coverage_date TEXT NOT NULL,
    search_type TEXT NOT NULL,
    country TEXT NOT NULL,
    clicks REAL NOT NULL,
    impressions REAL NOT NULL,
    ctr REAL NOT NULL,
    position REAL NOT NULL,
    sync_run_id INTEGER NOT NULL,
    PRIMARY KEY (property_id, coverage_date, search_type, country)
);

CREATE TABLE IF NOT EXISTS fact_query_page_country (
    property_id INTEGER NOT NULL REFERENCES properties(id),
    coverage_date TEXT NOT NULL,
    search_type TEXT NOT NULL,
    query TEXT NOT NULL,
    page TEXT NOT NULL,
    country TEXT NOT NULL,
    aggregation_type TEXT NOT NULL,
    clicks REAL NOT NULL,
    impressions REAL NOT NULL,
    ctr REAL NOT NULL,
    position REAL NOT NULL,
    sync_run_id INTEGER NOT NULL,
    PRIMARY KEY (property_id, coverage_date, search_type, query, page, country)
);

CREATE INDEX IF NOT EXISTS idx_fact_query_lookup
    ON fact_query (property_id, search_type, coverage_date);
CREATE INDEX IF NOT EXISTS idx_fact_page_lookup
    ON fact_page (property_id, search_type, coverage_date);
CREATE INDEX IF NOT EXISTS idx_fact_country_lookup
    ON fact_country (property_id, search_type, coverage_date);
CREATE INDEX IF NOT EXISTS idx_fact_qpc_lookup
    ON fact_query_page_country (property_id, search_type, coverage_date);
