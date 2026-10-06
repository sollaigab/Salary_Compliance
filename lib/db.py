"""
Connection to the local SQLite database (data/jobs.db) and schema creation.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "jobs.db"
SQL_DIR = ROOT / "sql"


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    """Opens the DB (creating it if missing) and makes sure tables and views exist."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)   # if another script is writing, wait up to 60 s
    conn.row_factory = sqlite3.Row          # rows readable by column name
    conn.execute("PRAGMA journal_mode=WAL")  # search.py can read while the crawler writes
    conn.executescript((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
    add_missing_columns(conn)
    conn.executescript((SQL_DIR / "views.sql").read_text(encoding="utf-8"))
    return conn


# Columns added after the first schema version: an existing DB needs them added explicitly
LATER_COLUMNS = {
    "locations": {"region_code": "TEXT"},
    "jobs": {"posted_date": "TEXT", "posting_period": "TEXT", "seniority_source": "TEXT",
             "experience_years": "INTEGER"},
}


def add_missing_columns(conn: sqlite3.Connection):
    for table, columns in LATER_COLUMNS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, sql_type in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {sql_type}")


def now_iso() -> str:
    """UTC timestamp in ISO-8601 format, compatible with BigQuery TIMESTAMP."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert(conn: sqlite3.Connection, table: str, row: dict, key: str):
    """INSERT, or UPDATE every column if the key already exists."""
    cols = list(row)
    placeholders = ", ".join(f":{c}" for c in cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != key)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({key}) DO UPDATE SET {updates}",
        row,
    )
