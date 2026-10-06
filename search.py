"""
Phase 3: search the stored job ads and keep a search history.

Usage:
    python search.py "data analyst"
    python search.py "data analyst" --location milano       # city, province (MI), region or macro-area
    python search.py "sviluppatore" --location remote       # remote ads
    python search.py "commerciale" --location lombardia --show 10
    python search.py "contabile" --all                      # inactive ads too

Each search saves a row in `searches`; the history is in the v_search_summary view:
    SELECT * FROM v_search_summary;
"""

import argparse
import statistics
import sys

from lib import db

# Ads containing the term in the title or description, with an optional location filter.
# The location is matched against the main location and every location of the ad (job_locations).
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
    p = argparse.ArgumentParser(description="Search the stored job ads")
    p.add_argument("term", help='term to search for, e.g. "data analyst"')
    p.add_argument("--location", help="city, province code, region, macro-area or 'remote'")
    p.add_argument("--all", action="store_true", help="include inactive ads too")
    p.add_argument("--show", type=int, default=0, help="show N example ads")
    p.add_argument("--period", choices=["post_law", "pre_law", "old"],
                   help="only ads published from 7/6/2026 (post_law), before it, or over 12 months ago (old)")
    args = p.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    conn = db.connect()
    location = args.location.strip().lower() if args.location else None
    rows = conn.execute(SEARCH_SQL, {"term": f"%{args.term.lower()}%", "location": location,
                                     "all_jobs": int(args.all), "period": args.period}).fetchall()

    with_salary = [r for r in rows if r["salary_transparency"] == "figure"]
    summary = {
        "search_term": args.term,
        "location": args.location,
        "run_at": db.now_iso(),
        "n_jobs": len(rows),
        "n_companies": len({r["company"] for r in rows}),
        "n_jobs_with_salary": len(with_salary),
        "n_jobs_vague": sum(1 for r in rows if r["salary_transparency"] == "vague"),
        "ral_min_median": median(r["ral_min_annual"] for r in rows),
        "ral_max_median": median(r["ral_max_annual"] for r in rows),
    }
    conn.execute(f"INSERT INTO searches ({', '.join(summary)}) VALUES ({', '.join('?' * len(summary))})",
                 list(summary.values()))
    conn.commit()

    where = f" in {args.location}" if args.location else ""
    where += f" [{args.period}]" if args.period else ""
    print(f'\n"{args.term}"{where}: {summary["n_jobs"]} ads from {summary["n_companies"]} companies')
    if not rows:
        return
    pct = lambda n: f"{100 * n / len(rows):.0f}%"
    print(f"  with a pay figure: {summary['n_jobs_with_salary']} ({pct(summary['n_jobs_with_salary'])})"
          f"  | vague wording only: {summary['n_jobs_vague']} ({pct(summary['n_jobs_vague'])})")
    if not args.period:
        periods = {}
        for r in rows:
            periods.setdefault(r["posting_period"] or "no date", []).append(r)
        parts = [f"{k}: {len(v)} ({100 * sum(x['salary_transparency'] == 'figure' for x in v) / len(v):.0f}% with figure)"
                 for k, v in sorted(periods.items())]
        print("  by period -> " + " | ".join(parts))
    if summary["ral_min_median"]:
        print(f"  median annual salary: €{summary['ral_min_median']:,} - €{summary['ral_max_median']:,}")

    # table by company
    by_company = {}
    for r in rows:
        by_company.setdefault(r["company"], []).append(r)
    print(f"\n  {'Company':<34} {'ads':>7} {'figure':>9} {'vague':>6}   median salary")
    for company, items in sorted(by_company.items(), key=lambda kv: -len(kv[1])):
        n_sal = sum(1 for r in items if r["salary_transparency"] == "figure")
        n_vague = sum(1 for r in items if r["salary_transparency"] == "vague")
        lo, hi = median(r["ral_min_annual"] for r in items), median(r["ral_max_annual"] for r in items)
        ral = f"€{lo:,} - €{hi:,}" if lo else "-"
        print(f"  {company[:34]:<34} {len(items):>7} {n_sal:>9} {n_vague:>6}   {ral}")

    for r in rows[:args.show]:
        ral = f"€{round(r['ral_min_annual']):,}-{round(r['ral_max_annual']):,}" \
            if r["ral_min_annual"] else r["salary_transparency"]
        print(f"\n  - {r['title']} · {r['company']} · {r['city'] or ''} · {ral}\n    {r['url']}")


if __name__ == "__main__":
    main()
