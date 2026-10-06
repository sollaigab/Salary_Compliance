"""
Phase 3: export the database for analysis and for the web app.

Usage:
    python export.py duckdb      # data/export/observatory.duckdb: full copy for SQL analysis (PRIVATE)
    python export.py public      # webapp/data/aggregates.*: PUBLISHABLE data, read by the web app
    python export.py bigquery --project my-project --dataset observatory   # optional

What can be published (see README, "Protecting the companies"):
- yes: ONLY aggregate cells by period x macro-sector x one dimension, with the protection rules
  in lib/disclosure.py (at least 5 ads and 3 companies, no company above 70%, secondary suppression)
- no: single job ads, company names, titles and links, descriptions -> they stay in the private DB

BigQuery: loads with a "load job" and WRITE_TRUNCATE (full reload), the only method that also works
in the free sandbox (no DML or streaming). Note: sandbox tables expire after 60 days.
"""

import argparse
import csv
import json
import sys

import duckdb

from lib import db, disclosure, labels

EXPORT_DIR = db.ROOT / "data" / "export"
PUBLIC_DIR = db.ROOT / "webapp" / "data"   # publishable data: the web app reads it from here
DUCKDB_FILE = EXPORT_DIR / "observatory.duckdb"
TABLES = ["companies", "ats_registry", "locations", "jobs", "job_locations", "crawl_runs", "searches"]


def duckdb_with_sqlite():
    """In-memory DuckDB connection that reads data/jobs.db directly."""
    con = duckdb.connect()
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{db.DB_PATH.as_posix()}' AS src (TYPE sqlite, READ_ONLY)")
    # the SQLite views (e.g. v_jobs_public) name the tables without a prefix: copy them into memory
    for t in ("jobs", "companies", "locations"):
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t}")
    return con


def export_duckdb():
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    if DUCKDB_FILE.exists():
        DUCKDB_FILE.unlink()
    con = duckdb.connect(str(DUCKDB_FILE))
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{db.DB_PATH.as_posix()}' AS src (TYPE sqlite, READ_ONLY)")
    for t in TABLES:
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t}")
    con.execute("CREATE TABLE jobs_public AS SELECT * FROM src.v_jobs_public")
    con.close()
    print(f"DuckDB written to {DUCKDB_FILE.relative_to(db.ROOT)} (private: it contains the descriptions)")
    print("  try:  duckdb data/export/observatory.duckdb \"SELECT sector, COUNT(*) FROM jobs_public GROUP BY 1\"")


# Published dimensions: each cell is period x macro-sector x ONE of these dimensions
PUBLIC_DIMS = ["region", "job_function", "seniority", "contract_type", "workplace_type"]


def public_cells(con) -> list[dict]:
    """All aggregate cells (before the protection rules), computed in DuckDB."""
    macro_case = "CASE " + " ".join(f"WHEN c.sector = '{s}' THEN '{m}'" for s, m in labels.MACRO_OF_SECTOR.items()) \
                 + " ELSE 'other' END"
    con.execute(f"""CREATE OR REPLACE TABLE base AS
        SELECT j.company_id, {macro_case} AS macro_sector, j.posting_period,
               COALESCE(l.region, CASE WHEN j.is_remote = 1 THEN 'Remote' ELSE 'Unspecified' END) AS region,
               j.job_function,
               CASE WHEN j.seniority_source = 'not_stated' THEN NULL ELSE j.seniority END AS seniority,
               j.contract_type, j.workplace_type,
               j.salary_transparency, j.ral_min_annual, j.ral_max_annual
        FROM src.jobs j JOIN src.companies c ON c.company_id = j.company_id
        LEFT JOIN src.locations l ON l.location_id = j.location_id
        WHERE j.is_active = 1""")

    def cells_for(period, macro, dim):
        where = ["TRUE"]
        if period == "post_law":
            where.append("posting_period = 'post_law'")
        if macro != "all":
            where.append(f"macro_sector = '{macro}'")
        value = "'all'" if dim == "total" else f"COALESCE({dim}, 'not_stated')"
        rows = con.execute(f"""
            WITH t AS (SELECT *, {value} AS value FROM base WHERE {' AND '.join(where)}),
                 per_company AS (SELECT value, company_id, COUNT(*) AS n,
                                        COUNT(ral_min_annual) AS n_ral FROM t GROUP BY ALL)
            SELECT t.value, COUNT(*) AS n_jobs, COUNT(DISTINCT t.company_id) AS n_companies,
                   COUNT(*) FILTER (WHERE salary_transparency = 'figure') AS n_with_salary,
                   COUNT(*) FILTER (WHERE salary_transparency = 'vague') AS n_vague,
                   COUNT(ral_min_annual) AS n_ral,
                   COUNT(DISTINCT t.company_id) FILTER (WHERE ral_min_annual IS NOT NULL) AS n_ral_companies,
                   MEDIAN(ral_min_annual) AS ral_min_median, MEDIAN(ral_max_annual) AS ral_max_median,
                   (SELECT MAX(n) FROM per_company p WHERE p.value = t.value) AS top_n,
                   (SELECT MAX(n_ral) FROM per_company p WHERE p.value = t.value) AS top_ral
            FROM t GROUP BY t.value""").fetchall()
        out = []
        for (value, n_jobs, n_comp, n_sal, n_vague, n_ral, n_ral_comp, lo, hi, top_n, top_ral) in rows:
            out.append({
                "period": period, "macro": macro, "dim": dim, "value": value,
                "n_jobs": n_jobs, "n_companies": n_comp,
                "pct_with_salary": round(100 * n_sal / n_jobs, 1), "pct_vague": round(100 * n_vague / n_jobs, 1),
                "n_ral": n_ral, "ral_min_median": round(lo, -2) if lo is not None else None,
                "ral_max_median": round(hi, -2) if hi is not None else None,
                "top_share": top_n / n_jobs, "n_ral_companies": n_ral_comp,
                "ral_top_share": (top_ral / n_ral) if n_ral else 1.0,
            })
        return out

    cells = []
    for period in ("post_law", "all"):
        cells += cells_for(period, "all", "macro_sector")
        for macro in ["all", *labels.MACRO_SECTOR]:
            cells += cells_for(period, macro, "total")
            for dim in PUBLIC_DIMS:
                cells += cells_for(period, macro, dim)
        # before/after the law: only meaningful on the "all" period
        if period == "all":
            for macro in ["all", *labels.MACRO_SECTOR]:
                cells += cells_for(period, macro, "posting_period")
    return cells


def export_public():
    """PUBLIC data: only aggregate cells that pass the rules in lib/disclosure.py.
    No single job ads, no company names, titles or links."""
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    for old in [*PUBLIC_DIR.glob("*.parquet"), *PUBLIC_DIR.glob("aggregati.*")]:   # earlier versions
        old.unlink()
    con = duckdb_with_sqlite()
    cells = public_cells(con)
    public = disclosure.apply_rules([dict(c) for c in cells])
    (PUBLIC_DIR / "aggregates.json").write_text(json.dumps(public, ensure_ascii=False), encoding="utf-8")
    with open(PUBLIC_DIR / "aggregates.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(public[0]))
        w.writeheader()
        w.writerows(public)
    print(f"  webapp/data/aggregates.json and .csv: {len(public)} cells published out of {len(cells)} "
          f"({len(cells) - len(public)} hidden by the protection rules)")
    con.execute("CREATE TABLE jobs_public AS SELECT * FROM src.v_jobs_public")
    meta = con.execute("""
        SELECT MIN(first_seen), MAX(last_seen), COUNT(*) FILTER (WHERE is_active = 1),
               COUNT(DISTINCT company_id) FILTER (WHERE is_active = 1)
        FROM jobs_public""").fetchone()
    meta_path = PUBLIC_DIR / "meta.json"
    meta_path.write_text(json.dumps({
        "observed_from": str(meta[0])[:10], "last_update": str(meta[1])[:10],
        "n_jobs_active": meta[2], "n_companies_active": meta[3],
        "n_companies_seed": con.execute("SELECT COUNT(*) FROM src.companies").fetchone()[0],
        "law_start": "2026-06-07",
        "rules": {"min_jobs": disclosure.MIN_JOBS, "min_companies": disclosure.MIN_COMPANIES,
                  "max_share": disclosure.MAX_SHARE},
    }, indent=1), encoding="utf-8")
    print(f"  {meta_path.relative_to(db.ROOT)}: dates and counts for the methodology section")
    labels_path = PUBLIC_DIR / "labels.json"
    public_labels = {k: v for k, v in labels.ALL.items() if k != "sector"}
    public_labels["region"] = labels.REGION_EXTRA
    labels_path.write_text(json.dumps(public_labels, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {labels_path.relative_to(db.ROOT)}: readable labels for the filters")
    print("Publishable files ready: aggregates only, no identifiable company.")


def export_bigquery(project: str, dataset: str):
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    client.create_dataset(f"{project}.{dataset}", exists_ok=True)
    con = duckdb_with_sqlite()
    sources = {t: f"SELECT * FROM src.{t}" for t in TABLES if t != "jobs"}
    sources["jobs"] = "SELECT * EXCLUDE (description) FROM src.jobs"   # full text stays local
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    for name, sql in sources.items():
        tmp = EXPORT_DIR / f"bq_{name}.json"
        con.execute(f"COPY ({sql}) TO '{tmp.as_posix()}' (FORMAT JSON)")   # one JSON line per record
        rows = con.execute(f"SELECT COUNT(*) FROM ({sql})").fetchone()[0]
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,   # full reload
            autodetect=True,
        )
        with open(tmp, "rb") as fh:
            client.load_table_from_file(fh, f"{project}.{dataset}.{name}", job_config=job_config).result()
        tmp.unlink()
        print(f"  {dataset}.{name}: {rows} rows")


def main():
    p = argparse.ArgumentParser(description="Export the database")
    p.add_argument("target", choices=["duckdb", "public", "bigquery"])
    p.add_argument("--project", help="Google Cloud project (bigquery only)")
    p.add_argument("--dataset", default="salary_observatory", help="BigQuery dataset")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.target == "duckdb":
        export_duckdb()
    elif args.target == "public":
        export_public()
    else:
        if not args.project:
            p.error("--project is required for bigquery")
        export_bigquery(args.project, args.dataset)


if __name__ == "__main__":
    main()
