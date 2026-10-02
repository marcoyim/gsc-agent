"""SQLite schema versioning."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from gsc_agent.errors import DatabaseError
from gsc_agent.util import utc_now

CURRENT_VERSION = 1
SEARCH_TYPE = "web"


def connect(path: Path) -> sqlite3.Connection:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
    except sqlite3.Error as exc:
        raise DatabaseError(f"無法開啟資料庫：{path}") from exc
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def schema_sql() -> str:
    return Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")


def migrate(connection: sqlite3.Connection) -> int:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    row = connection.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
    version = int(row["version"] or 0)
    if version < 1:
        connection.executescript(schema_sql())
        connection.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (1, ?)",
            (utc_now(),),
        )
        version = 1
    connection.commit()
    return version


def open_migrated(path: Path) -> sqlite3.Connection:
    connection = connect(path)
    migrate(connection)
    return connection
