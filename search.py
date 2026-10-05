"""
Fase 3: ricerca negli annunci salvati e storico delle ricerche.

Uso:
    python search.py "data analyst"
    python search.py "data analyst" --location milano       # città, provincia (MI), regione o macro-area
    python search.py "sviluppatore" --location remoto       # annunci da remoto
    python search.py "commerciale" --location lombardia --show 10
    python search.py "contabile" --all                      # anche gli annunci non più attivi

Ogni ricerca salva una riga in `searches`; lo storico si vede con la vista v_search_summary:
    SELECT * FROM v_search_summary;
"""

import argparse
import statistics
import sys

from lib import db

# Annunci che contengono il termine nel titolo o nella descrizione, con filtro opzionale sul luogo.
# Il luogo si confronta con la sede principale e con tutte le sedi dell'annuncio (job_locations).
SEARCH_SQL = """
SELECT j.job_key, j.title, j.url, j.city, j.salary_transparency, j.posting_period,
       j.ral_min_annual, j.ral_max_annual, c.name AS company
FROM jobs j
JOIN companies c ON c.company_id = j.company_id
WHERE (LOWER(j.title) LIKE :term OR LOWER(j.description) LIKE :term)
  AND (:all_jobs = 1 OR j.is_active = 1)
  AND (:period IS NULL OR j.posting_period = :period)
  AND (
        :location IS NULL
     OR (:location IN ('remoto', 'remote') AND j.is_remote = 1)
     OR EXISTS (
            SELECT 1
            FROM job_locations jl
            JOIN locations l ON l.location_id = jl.location_id
            WHERE jl.job_key = j.job_key
              AND (LOWER(l.city) = :location OR LOWER(l.province) = :location
                   OR LOWER(l.province_code) = :location OR LOWER(l.region) = :location
                   OR LOWER(l.macro_area) = :location)
        )
  )
ORDER BY c.name, j.title
"""


def median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values)) if values else None


def main():
    p = argparse.ArgumentParser(description="Cerca negli annunci salvati")
    p.add_argument("term", help='termine da cercare, es. "data analyst"')
    p.add_argument("--location", help="città, sigla provincia, regione, macro-area o 'remoto'")
    p.add_argument("--all", action="store_true", help="includi anche gli annunci non più attivi")
    p.add_argument("--show", type=int, default=0, help="mostra N annunci di esempio")
    p.add_argument("--periodo", choices=["post_legge", "pre_legge", "storico"],
                   help="solo annunci pubblicati dal 7/6/2026 (post_legge), prima, o da oltre 12 mesi (storico)")
    args = p.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    conn = db.connect()
    location = args.location.strip().lower() if args.location else None
    rows = conn.execute(SEARCH_SQL, {"term": f"%{args.term.lower()}%", "location": location,
                                     "all_jobs": int(args.all), "period": args.periodo}).fetchall()

    with_salary = [r for r in rows if r["salary_transparency"] == "cifra"]
    summary = {
        "search_term": args.term,
        "location": args.location,
        "run_at": db.now_iso(),
        "n_jobs": len(rows),
        "n_companies": len({r["company"] for r in rows}),
        "n_jobs_with_salary": len(with_salary),
        "n_jobs_vague": sum(1 for r in rows if r["salary_transparency"] == "vaga"),
        "ral_min_median": median(r["ral_min_annual"] for r in rows),
        "ral_max_median": median(r["ral_max_annual"] for r in rows),
    }
    conn.execute(f"INSERT INTO searches ({', '.join(summary)}) VALUES ({', '.join('?' * len(summary))})",
                 list(summary.values()))
    conn.commit()

    where = f" a/in {args.location}" if args.location else ""
    where += f" [{args.periodo}]" if args.periodo else ""
    print(f'\n"{args.term}"{where}: {summary["n_jobs"]} annunci da {summary["n_companies"]} aziende')
    if not rows:
        return
    pct = lambda n: f"{100 * n / len(rows):.0f}%"
    print(f"  con retribuzione in cifra: {summary['n_jobs_with_salary']} ({pct(summary['n_jobs_with_salary'])})"
          f"  | solo formula vaga: {summary['n_jobs_vague']} ({pct(summary['n_jobs_vague'])})")
    if not args.periodo:
        periods = {}
        for r in rows:
            periods.setdefault(r["posting_period"] or "senza data", []).append(r)
        parts = [f"{k}: {len(v)} ({100 * sum(x['salary_transparency'] == 'cifra' for x in v) / len(v):.0f}% con cifra)"
                 for k, v in sorted(periods.items())]
        print("  per periodo -> " + " | ".join(parts))
    if summary["ral_min_median"]:
        print(f"  RAL mediana: {summary['ral_min_median']:,} - {summary['ral_max_median']:,} €".replace(",", "."))

    # tabella per azienda
    by_company = {}
    for r in rows:
        by_company.setdefault(r["company"], []).append(r)
    print(f"\n  {'Azienda':<34} {'annunci':>7} {'con cifra':>9} {'vaghe':>6}   RAL mediana")
    for company, items in sorted(by_company.items(), key=lambda kv: -len(kv[1])):
        n_sal = sum(1 for r in items if r["salary_transparency"] == "cifra")
        n_vague = sum(1 for r in items if r["salary_transparency"] == "vaga")
        lo, hi = median(r["ral_min_annual"] for r in items), median(r["ral_max_annual"] for r in items)
        ral = f"{lo:,} - {hi:,} €".replace(",", ".") if lo else "-"
        print(f"  {company[:34]:<34} {len(items):>7} {n_sal:>9} {n_vague:>6}   {ral}")

    for r in rows[:args.show]:
        ral = f"{round(r['ral_min_annual']):,}-{round(r['ral_max_annual']):,} €".replace(",", ".") \
            if r["ral_min_annual"] else r["salary_transparency"]
        print(f"\n  - {r['title']} · {r['company']} · {r['city'] or ''} · {ral}\n    {r['url']}")


if __name__ == "__main__":
    main()
