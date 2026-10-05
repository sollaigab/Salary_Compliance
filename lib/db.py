"""
Connessione al database SQLite locale (data/jobs.db) e creazione dello schema.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "jobs.db"
SQL_DIR = ROOT / "sql"


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    """Apre il DB (lo crea se manca) e si assicura che tabelle e viste esistano."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)   # se un altro script sta scrivendo, aspetta fino a 60 s
    conn.row_factory = sqlite3.Row          # righe leggibili per nome di colonna
    conn.execute("PRAGMA journal_mode=WAL")  # si può leggere (search.py) mentre il crawler scrive
    conn.executescript((SQL_DIR / "schema.sql").read_text(encoding="utf-8"))
    add_missing_columns(conn)
    conn.executescript((SQL_DIR / "views.sql").read_text(encoding="utf-8"))
    return conn


# Colonne aggiunte dopo la prima versione dello schema: su un DB già esistente vanno aggiunte a mano
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
    """Timestamp UTC in formato ISO-8601, compatibile con TIMESTAMP di BigQuery."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def upsert(conn: sqlite3.Connection, table: str, row: dict, key: str):
    """INSERT, oppure UPDATE di tutte le colonne se la chiave esiste già."""
    cols = list(row)
    placeholders = ", ".join(f":{c}" for c in cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != key)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({key}) DO UPDATE SET {updates}",
        row,
    )
