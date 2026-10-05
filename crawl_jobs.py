"""
Fase 2: scarica gli annunci delle aziende pronte e li salva normalizzati nella tabella `jobs`.

Legge ats_registry (solo supported = 1 con confidenza high o medium), tiene gli annunci in Italia
o da remoto (Italia/Europa), li arricchisce (sede ISTAT, seniority, funzione, contratto, retribuzione)
e aggiorna lo storico: first_seen, last_seen, is_active.

Uso:
    python crawl_jobs.py --cache                      # tutte le board pronte
    python crawl_jobs.py --only "companyb,examplecorp"   # solo alcune aziende
    python crawl_jobs.py --max-jobs 200               # massimo annunci scaricati per board

Le regole di accesso (robots.txt, riserva TDM, pause, stop su richiesta) sono in lib/http.py.
Email e telefoni nelle descrizioni vengono rimossi prima del salvataggio (GDPR).
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
from lib.enrich import (contract_type, description_lang, job_function, posted_date, posting_period,
                        salary_fields, scrub_personal_data, seniority, work_schedule, workplace_type)
from lib.slugs import company_id_from_name

DEFAULT_MAX_JOBS = 400   # per board: tetto di sicurezza sulle richieste di dettaglio


# ---------------------------------------------------------------- dimensione geografica

def ensure_locations(conn) -> loc.LocationIndex:
    """Popola la tabella locations (una volta) e restituisce l'indice per riconoscere le sedi."""
    loc.download_reference(http)
    comuni = loc.load_comuni()
    n = conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0]
    if n < len(comuni):
        for row in comuni + loc.special_locations(comuni):
            db.upsert(conn, "locations", row, key="location_id")
        conn.commit()
        print(f"Tabella locations: {len(comuni)} comuni ISTAT + regioni e righe speciali")
    return loc.LocationIndex(comuni)


# ---------------------------------------------------------------- download

def fetch_board(board: dict, max_jobs: int) -> list[dict]:
    """Annunci della board. Per gli ATS 'pesanti' il filtro Italia si applica prima del dettaglio."""
    ats, target = board["ats"], board["slug_or_url"]
    if ats == "workday":
        jobs, _ = fetch_workday(target, max_jobs=max_jobs, italy_only=True)
    elif ats == "oracle":
        jobs, _ = fetch_oracle(target, italy_only=True)
    elif ats == "successfactors":
        jobs, _ = fetch_successfactors(target, max_jobs=max_jobs, italy_only=True)
    else:
        jobs, _ = FETCHERS[ats](target, board["instance"])
    return jobs


# ---------------------------------------------------------------- normalizzazione

def keep_location(norm: dict) -> bool:
    """Teniamo gli annunci in Italia o da remoto (Italia/Europa)."""
    lid = norm["location_id"] or ""
    return lid.startswith("IT") or lid == "EU-REMOTE"


def build_row(job: dict, board: dict, index: loc.LocationIndex, now: str, first_seen: str | None):
    """Da annuncio dell'ATS a riga della tabella jobs (None se non è in Italia/remoto)."""
    places = [job["location"]] + list(job["locations_all"] or [])
    normalized = [index.normalize(p, job["country"], job["workplace_type"]) for p in places if p]
    if not normalized and job["country"]:
        normalized = [index.normalize("", job["country"], job["workplace_type"])]
    italian = [n for n in normalized if keep_location(n)]
    if not italian:
        return None, []
    main = italian[0]

    description = scrub_personal_data(job["description"] or "")
    title = job["title"] or ""
    level = seniority(title)
    contract = contract_type(job["employment_type"], title, description)
    salary = salary_fields(job["salary_structured"], f"{title} {description}",
                           is_internship=(level == "intern" or contract == "stage"))
    key = f"{board['ats']}|{board['slug_or_url']}|{job['job_id']}"
    row = {
        "job_key": key,
        "ats": board["ats"],
        "slug_or_url": board["slug_or_url"],
        "job_id": str(job["job_id"]),
        "company_id": board["company_id"],
        "title": title,
        "department": job["department"],
        "job_function": job_function(title, job["department"]),
        "seniority": level,
        "contract_type": contract,
        "work_schedule": work_schedule(job["employment_type"], title, description),
        "workplace_type": workplace_type(job["workplace_type"], job["location"], description)
                          or ("remote" if main["is_remote"] else None),
        "location_raw": job["location"],
        "location_id": main["location_id"],
        "city": main["city"],
        "country": main["country"],
        "is_remote": main["is_remote"],
        "n_locations": len(places),
        "url": job["url"],
        "description": description,
        "description_lang": description_lang(description),
        "description_length": len(description),
        "posted_at": job["posted_at"],
        "posted_date": posted_date(job["posted_at"]),
        "posting_period": posting_period(posted_date(job["posted_at"]), first_seen or now),
        "salary_structured_json": json.dumps(job["salary_structured"], ensure_ascii=False)
                                  if job["salary_structured"] else None,
        **{k: v for k, v in salary.items() if k != "salary_text_matches"},
        "salary_text_matches": json.dumps(salary["salary_text_matches"], ensure_ascii=False),
        "first_seen": first_seen or now,
        "last_seen": now,
        "is_active": 1,
        "content_hash": hashlib.sha1(f"{title}\n{description}".encode("utf-8")).hexdigest(),
    }
    place_rows = [{"job_key": key, "location_id": n["location_id"], "location_raw": p}
                  for p, n in zip([p for p in places if p], normalized) if keep_location(n)]
    return row, place_rows


# ---------------------------------------------------------------- salvataggio

def save_board(conn, board: dict, rows: list[dict], places: list[dict]) -> int:
    """Salva gli annunci della board e segna come non attivi quelli spariti. Restituisce i nuovi."""
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
    conn.commit()   # niente scritture aperte durante il download (che può durare minuti)
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
    with_salary = sum(1 for r in rows if r["salary_transparency"] == "cifra")
    vague = sum(1 for r in rows if r["salary_transparency"] == "vaga")
    return {"error": None, "downloaded": len(jobs), "kept": len(rows), "new": new,
            "with_salary": with_salary, "vague": vague}


def reprocess(conn):
    """Ricalcola i campi derivati (funzione, seniority, contratto, retribuzione...) dai dati già salvati,
    senza scaricare nulla. Da usare quando si migliorano le regole di lib/enrich.py o lib/salary.py."""
    rows = conn.execute("SELECT job_key, title, department, description, salary_structured_json, "
                        "workplace_type, location_raw, is_remote, posted_at, first_seen FROM jobs").fetchall()
    for r in rows:
        title, description = r["title"] or "", r["description"] or ""
        structured = json.loads(r["salary_structured_json"]) if r["salary_structured_json"] else None
        level = seniority(title)
        contract = contract_type(None, title, description)
        salary = salary_fields(structured, f"{title} {description}",
                               is_internship=(level == "intern" or contract == "stage"))
        update = {
            "job_function": job_function(title, r["department"]),
            "seniority": level,
            "contract_type": contract,
            "work_schedule": work_schedule(None, title, description),
            "description_lang": description_lang(description),
            "posted_date": posted_date(r["posted_at"]),
            "posting_period": posting_period(posted_date(r["posted_at"]), r["first_seen"]),
            **{k: v for k, v in salary.items() if k != "salary_text_matches"},
            "salary_text_matches": json.dumps(salary["salary_text_matches"], ensure_ascii=False),
        }
        sets = ", ".join(f"{k} = :{k}" for k in update)
        conn.execute(f"UPDATE jobs SET {sets} WHERE job_key = :job_key", {**update, "job_key": r["job_key"]})
    conn.commit()
    print(f"Ricalcolati {len(rows)} annunci.")


def main():
    p = argparse.ArgumentParser(description="Crawler degli annunci (Fase 2)")
    p.add_argument("--only", help="aziende separate da virgola (nomi come nel seed)")
    p.add_argument("--max-jobs", type=int, default=DEFAULT_MAX_JOBS,
                   help=f"massimo annunci per board con dettaglio a pagamento di richieste (default {DEFAULT_MAX_JOBS})")
    p.add_argument("--cache", action="store_true", help="cache HTTP su disco (sviluppo)")
    p.add_argument("--reprocess", action="store_true",
                   help="non scarica nulla: ricalcola i campi derivati sugli annunci già salvati")
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
    if args.only:
        wanted = {company_id_from_name(n) for n in args.only.split(",")}
        boards = [b for b in boards if b["company_id"] in wanted]
    print(f"Board da scaricare: {len(boards)}")

    started = time.time()
    totals = {"kept": 0, "new": 0, "with_salary": 0, "vague": 0, "errors": 0}
    for i, board in enumerate(boards, 1):
        t0 = time.time()
        res = crawl_board(conn, board, index, args.max_jobs)
        eta = (time.time() - started) / i * (len(boards) - i) / 60
        if res["error"]:
            totals["errors"] += 1
            print(f"[{i}/{len(boards)}] {board['company']:<32} {board['ats']:<14} ERRORE {res['error']}  "
                  f"({time.time() - t0:.0f}s, fine stimata tra {eta:.0f} min)")
            continue
        for k in ("kept", "new", "with_salary", "vague"):
            totals[k] += res[k]
        pct = lambda n: f"{100 * n / res['kept']:.0f}%" if res["kept"] else "-"
        print(f"[{i}/{len(boards)}] {board['company']:<32} {board['ats']:<14} scaricati {res['downloaded']:>4}  "
              f"in Italia {res['kept']:>4} (nuovi {res['new']:>4})  con cifra {pct(res['with_salary']):>4}  "
              f"vaghe {pct(res['vague']):>4}  ({time.time() - t0:.0f}s, fine stimata tra {eta:.0f} min)")

    k = totals["kept"]
    print(f"\nTotale annunci salvati: {k}  (nuovi {totals['new']}, board con errore {totals['errors']})")
    if k:
        print(f"Con retribuzione in cifra: {totals['with_salary']} ({100 * totals['with_salary'] / k:.1f}%)  "
              f"| solo formula vaga: {totals['vague']} ({100 * totals['vague'] / k:.1f}%)")


if __name__ == "__main__":
    main()
