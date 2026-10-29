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
from lib.locations import COUNTRY_NAMES

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


# Published dimensions: each cell is a filter combination x ONE of these dimensions
PUBLIC_DIMS = ["country", "macro_sector", "region", "job_function", "seniority", "contract_type", "workplace_type"]
# Filters on the page: each one a value or 'all'. The dimension a filter fixes is not broken down
# under that filter (with function = data, the function chart has a single bar).
FILTERS = {"country": "country", "macro": "macro_sector", "function": "job_function", "workplace": "workplace_type"}
WORKPLACE_FILTER_VALUES = ["onsite", "hybrid", "remote"]
COUNTRY_CODES = {name: code for code, name in COUNTRY_NAMES.items()}    # "Germany" -> "DE"


def public_cells(con) -> list[dict]:
    """All aggregate cells (before the protection rules): every filter combination x every dimension."""
    import itertools
    import statistics
    from collections import Counter, defaultdict

    macro_case = "CASE " + " ".join(f"WHEN c.sector = '{s}' THEN '{m}'" for s, m in labels.MACRO_OF_SECTOR.items())                  + " ELSE 'other' END"
    jobs = con.execute(f"""
        SELECT j.company_id, j.country, {macro_case} AS macro_sector, j.posting_period,
               COALESCE(l.region, CASE WHEN j.is_remote = 1 THEN 'Remote' ELSE 'Unspecified' END) AS region,
               j.job_function,
               CASE WHEN j.seniority_source = 'not_stated' THEN NULL ELSE j.seniority END AS seniority,
               j.contract_type, j.workplace_type,
               j.salary_transparency, j.ral_min_annual, j.ral_max_annual
        FROM src.jobs j JOIN src.companies c ON c.company_id = j.company_id
        LEFT JOIN src.locations l ON l.location_id = j.location_id
        WHERE j.is_active = 1
          AND j.country IS NOT NULL   -- remote anywhere in Europe: too few ads, their country row would hide another
        """).fetchdf().to_dict("records")

    groups = defaultdict(list)   # (period, country, macro, function, workplace, dim, value) -> jobs
    for j in jobs:
        j = {k: (None if v != v else v) for k, v in j.items()}   # NaN from pandas -> None
        j["country"] = COUNTRY_CODES[j["country"]]
        options = {
            "period": ["all", "post_law"] if j["posting_period"] == "post_law" else ["all"],
            "country": ["all", j["country"]],
            "macro": ["all", j["macro_sector"]],
            "function": ["all", j["job_function"]],
            "workplace": ["all"] + ([j["workplace_type"]] if j["workplace_type"] in WORKPLACE_FILTER_VALUES else []),
        }
        for combo in itertools.product(*options.values()):
            seg = dict(zip(options, combo))
            dims = ["total"] + [d for d in PUBLIC_DIMS
                                if not any(seg[f] != "all" and d == fd for f, fd in FILTERS.items())]
            if seg["period"] == "all":
                dims.append("posting_period")   # before/after the law: only on all ads
            for dim in dims:
                value = "all" if dim == "total" else (j[dim] or "not_stated")
                groups[(*combo, dim, value)].append(j)

    cells = []
    for (period, country, macro, function, workplace, dim, value), rows in groups.items():
        n = len(rows)
        companies = Counter(r["company_id"] for r in rows)
        with_ral = [r for r in rows if r["ral_min_annual"] is not None]
        ral_companies = Counter(r["company_id"] for r in with_ral)
        # like SQL MEDIAN: each bound over the ads that have it
        mins = [r["ral_min_annual"] for r in with_ral]
        maxs = [r["ral_max_annual"] for r in with_ral if r["ral_max_annual"] is not None]
        lo = statistics.median(mins) if mins else None
        hi = statistics.median(maxs) if maxs else None
        cells.append({
            "period": period, "country": country, "macro": macro, "function": function, "workplace": workplace,
            "dim": dim, "value": value, "n_jobs": n, "n_companies": len(companies),
            "pct_with_salary": round(100 * sum(r["salary_transparency"] == "figure" for r in rows) / n, 1),
            "pct_vague": round(100 * sum(r["salary_transparency"] == "vague" for r in rows) / n, 1),
            "n_ral": len(with_ral),
            "ral_min_median": round(lo, -2) if lo is not None else None,
            "ral_max_median": round(hi, -2) if hi is not None else None,
            "top_share": max(companies.values()) / n, "n_ral_companies": len(ral_companies),
            "ral_top_share": max(ral_companies.values()) / len(with_ral) if with_ral else 1.0,
            "_companies": companies,
        })
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
        SELECT MIN(first_seen), MAX(last_seen), COUNT(*) FILTER (WHERE is_active = 1 AND country IS NOT NULL),
               COUNT(DISTINCT company_id) FILTER (WHERE is_active = 1 AND country IS NOT NULL)
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
    # sector and micro-sector stay private for now: too few companies per value to publish safely
    public_labels = {k: v for k, v in labels.ALL.items() if k not in ("sector", "micro_sector")}
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
