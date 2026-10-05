"""
Fase 3: esporta il database per analisi e web app.

Uso:
    python export.py duckdb      # data/export/osservatorio.duckdb: copia completa per analisi SQL (PRIVATA)
    python export.py public      # webapp/data/*.parquet: dati PUBBLICABILI, letti dalla web app
    python export.py bigquery --project mio-progetto --dataset osservatorio   # facoltativo

Cosa è pubblicabile (vedi README, "Pubblicazione dei risultati"):
- sì: SOLO celle aggregate per periodo x macro-settore x una dimensione, con le regole di protezione
  di lib/disclosure.py (almeno 5 annunci e 3 aziende, nessuna azienda oltre il 70%, soppressione secondaria)
- no: annunci singoli, nomi, titoli e link delle aziende, descrizioni -> restano solo nel DB privato

BigQuery: carica con "load job" e WRITE_TRUNCATE (ricarica completa), l'unico modo che funziona anche
nella sandbox gratuita (niente DML/streaming). Attenzione: nella sandbox le tabelle scadono dopo 60 giorni.
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import duckdb

from lib import db, disclosure, labels

EXPORT_DIR = db.ROOT / "data" / "export"
PUBLIC_DIR = db.ROOT / "webapp" / "data"   # dati pubblicabili: la web app li legge da qui
DUCKDB_FILE = EXPORT_DIR / "osservatorio.duckdb"
TABLES = ["companies", "ats_registry", "locations", "jobs", "job_locations", "crawl_runs", "searches"]
PUBLIC_COMPANY_COLUMNS = "company_id, name, website, sector, is_tech, company_type, hq_city, size_band"


def duckdb_with_sqlite():
    """Connessione DuckDB in memoria che legge direttamente data/jobs.db."""
    con = duckdb.connect()
    con.execute("INSTALL sqlite; LOAD sqlite;")
    con.execute(f"ATTACH '{db.DB_PATH.as_posix()}' AS src (TYPE sqlite, READ_ONLY)")
    # le viste SQLite (es. v_jobs_public) citano le tabelle senza prefisso: le copiamo in memoria
    for t in ("jobs", "companies", "locations"):
        con.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t}")
    return con


def load_aggregates() -> dict:
    """Legge sql/aggregates.sql: ogni blocco '-- name: xxx' è una query."""
    text = (db.SQL_DIR / "aggregates.sql").read_text(encoding="utf-8")
    blocks = re.split(r"^-- name:\s*(\w+)\s*$", text, flags=re.MULTILINE)
    return {blocks[i]: blocks[i + 1].strip().rstrip(";") for i in range(1, len(blocks), 2)}


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
    for name, sql in load_aggregates().items():
        con.execute(f"CREATE VIEW {name} AS {sql}")
    con.close()
    print(f"DuckDB scritto in {DUCKDB_FILE.relative_to(db.ROOT)} (privato: contiene le descrizioni)")
    print("  prova:  duckdb data/export/osservatorio.duckdb \"SELECT * FROM transparency_by_sector\"")


# Dimensioni pubblicate: ogni cella è periodo x macro-settore x UNA di queste dimensioni
PUBLIC_DIMS = ["region", "job_function", "seniority", "contract_type", "workplace_type"]


def public_cells(con) -> list[dict]:
    """Tutte le celle aggregate (prima delle regole di protezione), calcolate in DuckDB."""
    macro_case = "CASE " + " ".join(f"WHEN c.sector = '{s}' THEN '{m}'" for s, m in labels.MACRO_OF_SECTOR.items()) \
                 + " ELSE 'altro' END"
    con.execute(f"""CREATE OR REPLACE TABLE base AS
        SELECT j.company_id, {macro_case} AS macro_sector, j.posting_period,
               COALESCE(l.region, CASE WHEN j.is_remote = 1 THEN 'Remoto' ELSE 'Sede non specificata' END) AS region,
               j.job_function, j.seniority, j.contract_type, j.workplace_type,
               j.salary_transparency, j.ral_min_annual, j.ral_max_annual
        FROM src.jobs j JOIN src.companies c ON c.company_id = j.company_id
        LEFT JOIN src.locations l ON l.location_id = j.location_id
        WHERE j.is_active = 1""")

    def cells_for(period, macro, dim):
        where = ["TRUE"]
        if period == "post_legge":
            where.append("posting_period = 'post_legge'")
        if macro != "tutti":
            where.append(f"macro_sector = '{macro}'")
        value = "'tutti'" if dim == "totale" else f"COALESCE({dim}, 'non_indicato')"
        rows = con.execute(f"""
            WITH t AS (SELECT *, {value} AS value FROM base WHERE {' AND '.join(where)}),
                 per_company AS (SELECT value, company_id, COUNT(*) AS n,
                                        COUNT(ral_min_annual) AS n_ral FROM t GROUP BY ALL)
            SELECT t.value, COUNT(*) AS n_jobs, COUNT(DISTINCT t.company_id) AS n_companies,
                   COUNT(*) FILTER (WHERE salary_transparency = 'cifra') AS n_with_salary,
                   COUNT(*) FILTER (WHERE salary_transparency = 'vaga') AS n_vague,
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
    for period in ("post_legge", "tutti"):
        cells += cells_for(period, "tutti", "macro_sector")
        for macro in ["tutti", *labels.MACRO_SECTOR]:
            cells += cells_for(period, macro, "totale")
            for dim in PUBLIC_DIMS:
                cells += cells_for(period, macro, dim)
        # confronto prima/dopo la legge: ha senso solo sul periodo "tutti"
        if period == "tutti":
            for macro in ["tutti", *labels.MACRO_SECTOR]:
                cells += cells_for(period, macro, "posting_period")
    return cells


def export_public():
    """Dati PUBBLICI: solo celle aggregate che rispettano le regole di lib/disclosure.py.
    Nessun annuncio singolo, nessun nome, titolo o link di azienda."""
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    for old in PUBLIC_DIR.glob("*.parquet"):      # versioni precedenti con dati per annuncio/azienda
        old.unlink()
    con = duckdb_with_sqlite()
    cells = public_cells(con)
    public = disclosure.apply_rules([dict(c) for c in cells])
    (PUBLIC_DIR / "aggregati.json").write_text(json.dumps(public, ensure_ascii=False), encoding="utf-8")
    with open(PUBLIC_DIR / "aggregati.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(public[0]))
        w.writeheader()
        w.writerows(public)
    print(f"  webapp/data/aggregati.json e .csv: {len(public)} celle pubblicate su {len(cells)} "
          f"({len(cells) - len(public)} nascoste dalle regole di protezione)")
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
    print(f"  {meta_path.relative_to(db.ROOT)}: date e conteggi per la pagina metodologia")
    labels_path = PUBLIC_DIR / "labels.json"
    public_labels = {k: v for k, v in labels.ALL.items() if k not in ("sector", "company_type")}
    public_labels["region"] = labels.REGION_EXTRA
    labels_path.write_text(json.dumps(public_labels, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"  {labels_path.relative_to(db.ROOT)}: etichette leggibili per i filtri")
    print("File pubblicabili pronti: solo aggregati, nessuna azienda identificabile.")


def export_bigquery(project: str, dataset: str):
    import json
    import tempfile

    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    client.create_dataset(f"{project}.{dataset}", exists_ok=True)
    con = duckdb_with_sqlite()
    con.execute("CREATE TABLE jobs_public AS SELECT * FROM src.v_jobs_public")
    sources = {t: f"SELECT * FROM src.{t}" for t in TABLES if t != "jobs"}
    sources["jobs"] = "SELECT * EXCLUDE (description) FROM src.jobs"   # testo integrale: resta locale
    for name, sql in sources.items():
        rows = con.execute(sql).fetchall()
        cols = [d[0] for d in con.description]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(dict(zip(cols, r)), ensure_ascii=False, default=str) + "\n")
            tmp = Path(f.name)
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,   # ricarica completa
            autodetect=True,
        )
        with open(tmp, "rb") as fh:
            client.load_table_from_file(fh, f"{project}.{dataset}.{name}", job_config=job_config).result()
        tmp.unlink()
        print(f"  {dataset}.{name}: {len(rows)} righe")


def main():
    p = argparse.ArgumentParser(description="Esporta il database")
    p.add_argument("target", choices=["duckdb", "public", "bigquery"])
    p.add_argument("--project", help="progetto Google Cloud (solo bigquery)")
    p.add_argument("--dataset", default="osservatorio_ral", help="dataset BigQuery")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.target == "duckdb":
        export_duckdb()
    elif args.target == "public":
        export_public()
    else:
        if not args.project:
            p.error("--project è obbligatorio per bigquery")
        export_bigquery(args.project, args.dataset)


if __name__ == "__main__":
    main()
