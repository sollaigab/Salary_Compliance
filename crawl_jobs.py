"""
Phase 2: downloads the job ads of the ready companies and stores them, normalized, in the `jobs` table.

Reads ats_registry (only supported = 1 with high or medium confidence), keeps the ads located in the
observed countries (Italy, Germany, the Netherlands, France) or remote in Europe, enriches them
(ISTAT/Eurostat location, seniority, function, contract, pay) and updates the history: first_seen,
last_seen, is_active.

Usage:
    python crawl_jobs.py --cache                          # every ready board
    python crawl_jobs.py --only "Company A,Company B"     # only some companies
    python crawl_jobs.py --max-jobs 200                   # max ads downloaded per board and country

The access rules (robots.txt, TDM reservation, pauses, stop on request) live in lib/http.py.
Emails and phone numbers in the descriptions are removed before saving (GDPR).
"""

import argparse
import hashlib
import json
import sys
import time

import requests

from lib import db, http
from lib import locations as loc
from lib.ats_fetchers import FETCHERS, fetch_oracle, fetch_successfactors, fetch_workday
from lib.enrich import (contract_type, description_lang, is_open_application, job_function, posted_date, posting_period,
                        salary_fields, scrub_personal_data, seniority_with_source, work_schedule,
                        workplace_with_source)
from lib.slugs import company_id_from_name

COUNTRY_CODES = {name: code for code, name in loc.COUNTRY_NAMES.items()}   # "Germany" -> "DE"

# ATSs that expose a workplace field (on site / hybrid / remote) in their public data
ATS_WITH_WORKPLACE_FIELD = {"lever", "ashby", "workday", "oracle", "teamtailor", "recruitee", "workable"}
DEFAULT_MAX_JOBS = 400   # per board and country (Workday, SuccessFactors): safety cap on detail requests


# ---------------------------------------------------------------- geography

def ensure_locations(conn) -> loc.LocationIndex:
    """Fills the locations table (once) and returns the index used to recognize locations."""
    loc.download_reference(http)
    comuni = loc.load_comuni()
    foreign, foreign_special = loc.load_foreign()
    n = conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    if n < len(comuni) + len(foreign):
        for row in comuni + loc.special_locations(comuni) + foreign + foreign_special:
            db.upsert(conn, "locations", row, key="location_id")
        conn.commit()
        print(f"locations table: {len(comuni)} ISTAT and {len(foreign)} Eurostat municipalities + regions and special rows")
    return loc.LocationIndex(comuni, foreign)


# ---------------------------------------------------------------- download

def fetch_board(board: dict, max_jobs: int) -> list[dict]:
    """The board's ads. For the 'heavy' ATSs the country filter runs before fetching details."""
    ats, target = board["ats"], board["slug_or_url"]
    if ats == "workday":
        jobs, _ = fetch_workday(target, max_jobs=max_jobs, scope_only=True)
    elif ats == "oracle":
        jobs, _ = fetch_oracle(target, scope_only=True)
    elif ats == "successfactors":
        jobs, _ = fetch_successfactors(target, max_jobs=max_jobs, scope_only=True)
    else:
        jobs, _ = FETCHERS[ats](target, board["instance"])
    return jobs


# ---------------------------------------------------------------- normalization

def keep_location(norm: dict) -> bool:
    """Keep ads located in the observed countries (Italy, Germany, the Netherlands, France) or remote in Europe."""
    lid = norm["location_id"] or ""
    return lid.split("-")[0] in loc.COUNTRY_NAMES or lid == "EU-REMOTE"


def derived_fields(title: str, description: str, department, employment_type, structured,
                   posted_at, first_seen: str, country: str = "Italy") -> dict:
    """Fields computed by the rules in lib/enrich.py: used by the crawl and by --reprocess.
    country: the English name of the ad's country (monthly pay is annualized by country)."""
    level, level_source, years = seniority_with_source(title, description)
    contract = contract_type(employment_type, title, description)
    salary = salary_fields(structured, f"{title} {description}",
                           is_internship=(level == "intern" or contract == "internship"),
                           country=COUNTRY_CODES.get(country, "IT"))
    return {
        "job_function": job_function(title, department),
        "seniority": level,
        "seniority_source": level_source,
        "experience_years": years,
        "contract_type": contract,
        "work_schedule": work_schedule(employment_type, title, description),
        "description_lang": description_lang(description),
        "posted_date": posted_date(posted_at),
        "posting_period": posting_period(posted_date(posted_at), first_seen),
        **{k: v for k, v in salary.items() if k != "salary_text_matches"},
        "salary_text_matches": json.dumps(salary["salary_text_matches"], ensure_ascii=False),
    }


def workplace_fields(ats_value, location, description: str, is_remote, title: str = "") -> dict:
    """workplace_type and where it comes from: the ATS field, the description, a remote-only location,
    or a title that can only be done in person."""
    value, source = workplace_with_source(ats_value, location, description, title)
    if not value and is_remote:
        value, source = "remote", "location"
    return {"workplace_type": value, "workplace_source": source}


def build_row(job: dict, board: dict, index: loc.LocationIndex, now: str, first_seen: str | None):
    """From an ATS job ad to a jobs table row (None if outside the observed countries, or an open application)."""
    if is_open_application(job["title"]):
        return None, []
    places = [job["location"]] + list(job["locations_all"] or [])
    normalized = [index.normalize(p, job["country"], job["workplace_type"]) for p in places if p]
    if not normalized and job["country"]:
        normalized = [index.normalize("", job["country"], job["workplace_type"])]
    kept = [n for n in normalized if keep_location(n)]
    if not kept:
        return None, []
    main = kept[0]

    description = scrub_personal_data(job["description"] or "")
    title = job["title"] or ""
    key = f"{board['ats']}|{board['slug_or_url']}|{job['job_id']}"
    row = {
        "job_key": key,
        "ats": board["ats"],
        "slug_or_url": board["slug_or_url"],
        "job_id": str(job["job_id"]),
        "company_id": board["company_id"],
        "title": title,
        "department": job["department"],
        **derived_fields(title, description, job["department"], job["employment_type"],
                         job["salary_structured"], job["posted_at"], first_seen or now, main["country"]),
        "workplace_ats": job["workplace_type"],
        **workplace_fields(job["workplace_type"], job["location"], description, main["is_remote"], title),
        "location_raw": job["location"],
        "location_id": main["location_id"],
        "city": main["city"],
        "country": main["country"],
        "is_remote": main["is_remote"],
        "n_locations": len(places),
        "url": job["url"],
        "description": description,
        "description_length": len(description),
        "posted_at": job["posted_at"],
        "salary_structured_json": json.dumps(job["salary_structured"], ensure_ascii=False)
                                  if job["salary_structured"] else None,
        "first_seen": first_seen or now,
        "last_seen": now,
        "is_active": 1,
        "content_hash": hashlib.sha1(f"{title}\n{description}".encode("utf-8")).hexdigest(),
    }
    place_rows = [{"job_key": key, "location_id": n["location_id"], "location_raw": p}
                  for p, n in zip([p for p in places if p], normalized) if keep_location(n)]
    return row, place_rows


# ---------------------------------------------------------------- saving

def save_board(conn, board: dict, rows: list[dict], places: list[dict]) -> int:
    """Saves the board's ads and marks the ones that disappeared as inactive. Returns the new count."""
    for r in rows:
        db.upsert(conn, "jobs", r, key="job_key")
        conn.execute("DELETE FROM job_locations WHERE job_key = ?", (r["job_key"],))
    for p in places:
        conn.execute("INSERT OR IGNORE INTO job_locations (job_key, location_id, location_raw) VALUES (?, ?, ?)",
                     (p["job_key"], p["location_id"], p["location_raw"]))
    seen = [r["job_key"] for r in rows]
    placeholders = ",".join("?" * len(seen)) or "''"
    conn.execute(f"""UPDATE jobs SET is_active = 0
                     WHERE ats = ? AND slug_or_url = ? AND job_key NOT IN ({placeholders})""",
                 [board["ats"], board["slug_or_url"], *seen])
    return sum(1 for r in rows if r["first_seen"] == r["last_seen"])


def crawl_board(conn, board: dict, index, max_jobs: int) -> dict:
    started = db.now_iso()
    cur = conn.execute("INSERT INTO crawl_runs (company_id, ats, slug_or_url, started_at, status) "
                       "VALUES (?, ?, ?, ?, 'running')",
                       (board["company_id"], board["ats"], board["slug_or_url"], started))
    run_id = cur.lastrowid
    conn.commit()   # no open writes during the download (it can take minutes)
    try:
        jobs = fetch_board(board, max_jobs)
    except (requests.RequestException, ValueError, http.RobotsDisallowed) as e:
        conn.execute("UPDATE crawl_runs SET finished_at = ?, status = 'error', error = ? WHERE run_id = ?",
                     (db.now_iso(), f"{type(e).__name__}: {str(e)[:200]}", run_id))
        conn.commit()
        return {"error": type(e).__name__, "downloaded": 0, "kept": 0, "new": 0}

    known = dict(conn.execute("SELECT job_key, first_seen FROM jobs WHERE ats = ? AND slug_or_url = ?",
                              (board["ats"], board["slug_or_url"])).fetchall())
    now = db.now_iso()
    rows, places = [], []
    for job in jobs:
        if not job.get("job_id"):
            continue
        key = f"{board['ats']}|{board['slug_or_url']}|{job['job_id']}"
        row, job_places = build_row(job, board, index, now, known.get(key))
        if row:
            rows.append(row)
            places += job_places
    new = save_board(conn, board, rows, places)
    conn.execute("UPDATE crawl_runs SET finished_at = ?, status = 'ok', n_jobs_seen = ?, n_jobs_new = ? "
                 "WHERE run_id = ?", (db.now_iso(), len(rows), new, run_id))
    conn.commit()
    with_salary = sum(1 for r in rows if r["salary_transparency"] == "figure")
    vague = sum(1 for r in rows if r["salary_transparency"] == "vague")
    return {"error": None, "downloaded": len(jobs), "kept": len(rows), "new": new,
            "with_salary": with_salary, "vague": vague}


def reprocess(conn):
    """Recomputes the derived fields (function, seniority, contract, pay...) from the stored data,
    without downloading anything. Use it after improving the rules in lib/enrich.py or lib/salary.py."""
    rows = conn.execute("SELECT job_key, ats, title, department, description, salary_structured_json, "
                        "workplace_type, workplace_ats, location_raw, is_remote, posted_at, first_seen, country FROM jobs").fetchall()
    for r in rows:
        title, description = r["title"] or "", r["description"] or ""
        structured = json.loads(r["salary_structured_json"]) if r["salary_structured_json"] else None
        update = derived_fields(title, description, r["department"], None, structured,
                                r["posted_at"], r["first_seen"], r["country"])
        # rows saved before workplace_ats existed: for the ATSs with a workplace field, the stored value
        # is the best guess of what the ATS said (the next crawl replaces it with the real field)
        ats_value = r["workplace_ats"]
        if ats_value is None and r["ats"] in ATS_WITH_WORKPLACE_FIELD:
            ats_value = r["workplace_type"]
        update.update(workplace_fields(ats_value, r["location_raw"], description, r["is_remote"], title))
        sets = ", ".join(f"{k} = :{k}" for k in update)
        conn.execute(f"UPDATE jobs SET {sets} WHERE job_key = :job_key", {**update, "job_key": r["job_key"]})
        if is_open_application(title):      # not a job ad: drop it from the analysis
            conn.execute("UPDATE jobs SET is_active = 0 WHERE job_key = ?", (r["job_key"],))
    conn.commit()
    print(f"Recomputed {len(rows)} job ads.")


def main():
    p = argparse.ArgumentParser(description="Job ad crawler (Phase 2)")
    p.add_argument("--only", help="comma-separated companies (names as in the seed list)")
    p.add_argument("--max-jobs", type=int, default=DEFAULT_MAX_JOBS,
                   help=f"max ads per board and country when each detail costs a request (default {DEFAULT_MAX_JOBS})")
    p.add_argument("--cache", action="store_true", help="on-disk HTTP cache (development)")
    p.add_argument("--reprocess", action="store_true",
                   help="downloads nothing: recomputes the derived fields of the stored ads")
    args = p.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    conn = db.connect()
    if args.reprocess:
        reprocess(conn)
        return
    http.init(use_cache=args.cache)
    index = ensure_locations(conn)

    boards = [dict(r) for r in conn.execute("""
        SELECT r.company_id, r.company, r.ats, r.slug_or_url, r.instance, r.n_jobs_italy
        FROM ats_registry r
        WHERE r.supported = 1 AND r.confidence IN ('high', 'medium') AND r.slug_or_url IS NOT NULL
        ORDER BY r.company""")]
    # the same board under two companies (a group site, e.g. a subsidiary using the parent's career
    # site) would count its ads twice: keep the first and say so, to be fixed in manual_overrides.csv
    seen = {}
    for b in list(boards):
        key = b["slug_or_url"].strip().lower()
        if key in seen:
            print(f"WARNING: {b['company']} uses the same board as {seen[key]}: skipped ({b['slug_or_url']})")
            boards.remove(b)
        else:
            seen[key] = b["company"]
    if args.only:
        wanted = {company_id_from_name(n) for n in args.only.split(",")}
        boards = [b for b in boards if b["company_id"] in wanted]
    print(f"Boards to download: {len(boards)}")

    started = time.time()
    totals = {"kept": 0, "new": 0, "with_salary": 0, "vague": 0, "errors": 0}
    for i, board in enumerate(boards, 1):
        t0 = time.time()
        res = crawl_board(conn, board, index, args.max_jobs)
        eta = (time.time() - started) / i * (len(boards) - i) / 60
        if res["error"]:
            totals["errors"] += 1
            print(f"[{i}/{len(boards)}] {board['company']:<32} {board['ats']:<14} ERROR {res['error']}  "
                  f"({time.time() - t0:.0f}s, about {eta:.0f} min left)")
            continue
        for k in ("kept", "new", "with_salary", "vague"):
            totals[k] += res[k]
        pct = lambda n: f"{100 * n / res['kept']:.0f}%" if res["kept"] else "-"
        print(f"[{i}/{len(boards)}] {board['company']:<32} {board['ats']:<14} downloaded {res['downloaded']:>4}  "
              f"kept {res['kept']:>4} (new {res['new']:>4})  with figure {pct(res['with_salary']):>4}  "
              f"vague {pct(res['vague']):>4}  ({time.time() - t0:.0f}s, about {eta:.0f} min left)")

    k = totals["kept"]
    print(f"\nTotal ads saved: {k}  (new {totals['new']}, boards with errors {totals['errors']})")
    if k:
        print(f"With a pay figure: {totals['with_salary']} ({100 * totals['with_salary'] / k:.1f}%)  "
              f"| vague wording only: {totals['vague']} ({100 * totals['vague'] / k:.1f}%)")


if __name__ == "__main__":
    main()
