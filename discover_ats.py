"""
Phase 1: finds which ATS each company uses, and with which slug/URL.

For each company it tries, in order, and stops at the first valid result:
  1. fingerprint: looks for ATS fingerprints on the home page and the career page
     (first with requests, then if needed with the headless browser for JavaScript pages);
  2. probe: tries slugs built from the name on the Greenhouse, Lever, Ashby and Workday APIs;
  3. validation: the board must have at least one job ad and the company name must appear.

Usage:
    python discover_ats.py                              # every company (skips recently checked ones)
    python discover_ats.py --only "Company A,Company B"  # only some companies
    python discover_ats.py --force                      # recheck recent ones too
    python discover_ats.py --retry-none                 # retry only those with no ATS found
    python discover_ats.py --no-browser                 # without Playwright (faster, finds less)
    python discover_ats.py --cache                      # on-disk HTTP cache (development)
    python discover_ats.py --report                     # print only the coverage report

data/manual_overrides.csv always wins over automatic discovery.
The result goes to the ats_registry table in data/jobs.db and to data/ats_registry.csv
(both private: they list the companies).
"""

import argparse
import csv
import html as html_lib
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import requests

from lib import db, http
from lib.ats_fetchers import (FETCHERS, count_italy, discover_workday_site, oracle_summary,
                              successfactors_summary, workday_summary)
from lib.ats_fingerprints import PARTIAL_ATS, SUPPORTED_ATS, find_fingerprints, sort_fingerprints
from lib.slugs import candidate_slugs, company_id_from_name, core_tokens, name_match, workday_tenants

SEED_CSV = db.ROOT / "data" / "companies_seed.csv"
OVERRIDES_CSV = db.ROOT / "data" / "manual_overrides.csv"
REGISTRY_CSV = db.ROOT / "data" / "ats_registry.csv"

REGISTRY_COLUMNS = [
    "company_id", "company", "sector", "is_tech", "ats", "slug_or_url", "instance", "supported",
    "detection_method", "confidence", "n_jobs_total", "n_jobs_italy", "careers_url", "evidence",
    "last_checked", "notes",
]

# Links that lead to the career page (in the path or the link text)
CAREERS_HINT_RE = re.compile(
    r"career|carrier[ae]|lavora[\s_-]*con[\s_-]*noi|\bjobs?\b|work[\s_-]*with[\s_-]*us"
    r"|join[\s_-]*(?:us|the[\s_-]*team|our[\s_-]*team)|unisciti|opportunit|posizioni[\s_-]*aperte"
    r"|offerte[\s_-]*di[\s_-]*lavoro|talent|recruit|candidat|open[\s_-]*positions",
    re.IGNORECASE,
)
SKIP_LINK_RE = re.compile(
    r"^(?:mailto|tel|javascript):|linkedin\.com|facebook\.com|instagram\.com|twitter\.com"
    r"|//x\.com|youtube\.com|glassdoor|indeed\.|tiktok\.com|\.pdf(?:$|\?)",
    re.IGNORECASE,
)
ANCHOR_RE = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"'#][^\"']*)[\"'][^>]*>(.*?)</a>",
                       re.IGNORECASE | re.DOTALL)
# If the home page has no link to the career page, try these paths
COMMON_CAREER_PATHS = ["/careers", "/carriere", "/lavora-con-noi", "/jobs", "/work-with-us"]
# Workday servers: the most common first (wd3 and wd103 covered 15 of the first 19 companies found).
# Each try is a single light request (the tenant's robots.txt)
WORKDAY_HOSTS = ["wd3", "wd103", "wd1", "wd5", "wd2", "wd12", "wd10", "wd101", "wd102", "wd105", "wd108"]

# Max time per company (seconds). If a site is slow we do not insist: note it and move on.
MAX_SECONDS = 60
_deadline = 0.0


def start_clock(seconds: int):
    global _deadline
    _deadline = time.time() + seconds


def time_is_up() -> bool:
    return time.time() > _deadline


# ---------------------------------------------------------------- input

def load_seed() -> list[dict]:
    with open(SEED_CSV, encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r.get("name", "").strip()]
    for r in rows:
        r["company_id"] = company_id_from_name(r["name"])
        r["is_tech"] = int(r.get("is_tech") or 0)
        r["website_verified"] = int(r.get("website_verified") or 0)
    return rows


def load_overrides() -> dict:
    """company_id -> manual correction. The `company` column can hold the name or the company_id."""
    if not OVERRIDES_CSV.exists():
        return {}
    with open(OVERRIDES_CSV, encoding="utf-8") as f:
        return {company_id_from_name(r["company"]): r
                for r in csv.DictReader(f) if r.get("company", "").strip()}


def save_companies(conn, companies: list[dict]):
    for c in companies:
        db.upsert(conn, "companies", {
            "company_id": c["company_id"], "name": c["name"], "website": c["website"],
            "website_verified": c["website_verified"], "sector": c["sector"],
            "is_tech": c["is_tech"], "company_type": c.get("company_type"),
            "hq_city": c.get("hq_city"), "size_band": c.get("size_band"),
            "source": c.get("source"), "updated_at": db.now_iso(),
        }, key="company_id")
    conn.commit()


# ---------------------------------------------------------------- 1. fingerprint

def fetch_page(url: str, notes: list):
    """Downloads a page of the company website (respecting robots.txt). (final_url, html) or None."""
    try:
        r = http.request("GET", url, check_robots=True, retries=1,
                         headers={"Accept": "text/html,application/xhtml+xml"})
    except http.RobotsDisallowed:
        notes.append(f"robots.txt disallows {url}")
        return None
    except requests.RequestException as e:
        notes.append(f"error on {url}: {type(e).__name__}")
        return None
    if r.status_code >= 400 or "html" not in r.headers.get("Content-Type", "html"):
        return None
    return r.url, r.text


def find_career_links(base_url: str, anchors, max_links: int = 3) -> list[str]:
    """From the page links (href, text) picks those that seem to lead to the job ads."""
    scores = {}
    for href, text in anchors:
        if not isinstance(href, str):
            continue
        href = html_lib.unescape(href.strip())
        if not href or SKIP_LINK_RE.search(href):
            continue
        url = urljoin(base_url, href)
        if not url.startswith("http") or url.rstrip("/") == base_url.rstrip("/"):
            continue
        parsed = urlparse(url)
        text = re.sub(r"<[^>]+>", " ", text or "")
        score = 0
        if CAREERS_HINT_RE.search(parsed.netloc + parsed.path):
            score += 2
        if CAREERS_HINT_RE.search(text):
            score += 2
        if re.match(r"(?:careers?|jobs|carriere|lavoraconnoi)\.", parsed.netloc):
            score += 2
        if score:
            scores[url] = max(score, scores.get(url, 0))
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    return [url for url, _ in ranked[:max_links]]


def scan_static(company: dict, notes: list):
    """Home + career pages fetched with requests. Returns (fingerprints, careers_url, career_links)."""
    home = fetch_page(company["website"], notes)
    if not home:
        return [], None, []
    home_url, home_html = home

    found = find_fingerprints(home_html)
    links = find_career_links(home_url, ANCHOR_RE.findall(home_html))
    if any(f["supported"] for f in found):
        return found, None, links

    to_visit = links or [urljoin(home_url, p) for p in COMMON_CAREER_PATHS]
    careers_url, visited = None, {home_url}
    for url in to_visit[:4]:
        if time_is_up():
            notes.append("time up during the career pages")
            break
        page = fetch_page(url, notes)
        if not page or page[0] in visited:
            continue
        visited.add(page[0])
        careers_url = careers_url or page[0]
        found += find_fingerprints(page[0] + "\n" + page[1])
        # second level: inside the career page the "open positions" link often leads to the ATS
        if not found:
            for sub in find_career_links(page[0], ANCHOR_RE.findall(page[1]), max_links=2):
                if sub in visited:
                    continue
                visited.add(sub)
                found += find_fingerprints(sub)
                sub_page = fetch_page(sub, notes)
                if sub_page:
                    found += find_fingerprints(sub_page[0] + "\n" + sub_page[1])
        if any(f["supported"] for f in found):
            break
    return merge_fingerprints(found), careers_url, links


def scan_browser(browser, company: dict, start_urls: list[str], notes: list):
    """Like scan_static but with the headless browser: it also sees widgets loaded by JavaScript."""
    found, careers_url = [], None
    queue = list(dict.fromkeys(start_urls[:2] or [company["website"]]))
    seen = set()
    while queue and len(seen) < 3:
        url = queue.pop(0)
        if url in seen:
            continue
        if time_is_up():
            notes.append("time up during the browser pass")
            break
        seen.add(url)
        try:
            page = browser.render(url)
        except http.RobotsDisallowed:
            notes.append(f"robots.txt disallows {url}")
            continue
        if not page:
            notes.append(f"browser: page not loaded {url}")
            continue
        blob = "\n".join([page["url"], page["html"], *page["network"], *page["frames"]])
        found += find_fingerprints(blob)
        if any(f["supported"] for f in found):
            careers_url = page["url"]
            break
        if CAREERS_HINT_RE.search(page["url"]):
            careers_url = careers_url or page["url"]
        queue += [u for u in find_career_links(page["url"], page["links"], max_links=2) if u not in seen]
    return merge_fingerprints(found), careers_url


def board_target(fingerprint: dict, careers_url: str | None) -> str | None:
    """What to pass to the fetcher. SuccessFactors uses the career site (sitemap); Teamtailor without
    a slug uses the career site on the company's own domain (/jobs.rss feed)."""
    if fingerprint["ats"] == "successfactors":
        return careers_url
    if fingerprint["ats"] == "teamtailor" and not fingerprint["slug_or_url"]:
        return careers_url
    return fingerprint["slug_or_url"]


def prefer_own_slug(found: list[dict], company: dict) -> list[dict]:
    """Within the same ATS, puts first the slug that contains the company name.

    E.g. a page that mentions another company's tenant and its own: its own wins.
    """
    names = [n for n in (company_id_from_name(company["name"]).replace("-", ""),
                         *core_tokens(company["name"])) if len(n) >= 4]
    own = lambda f: bool(f["slug_or_url"]) and any(n in f["slug_or_url"].lower() for n in names)
    top_ats = found[0]["ats"]
    same = [f for f in found if f["ats"] == top_ats]
    rest = [f for f in found if f["ats"] != top_ats]
    return sorted(same, key=own, reverse=True) + rest


def merge_fingerprints(found: list[dict]) -> list[dict]:
    """Merges identical fingerprints found on different pages, adding up the counts."""
    merged = {}
    for f in found:
        key = (f["ats"], f["slug_or_url"], f["instance"])
        if key in merged:
            merged[key]["count"] += f["count"]
        else:
            merged[key] = dict(f)
    return sort_fingerprints(list(merged.values()))


# ---------------------------------------------------------------- 2-3. probe and validation

SUMMARIES = {"workday": workday_summary, "oracle": oracle_summary,
             "successfactors": successfactors_summary}


def inspect_board(company: dict, ats: str, slug_or_url: str, instance: str = None) -> dict:
    """Downloads the board and measures: total ads, ads in Italy, whether the company name appears."""
    italy_note = None
    try:
        if ats in SUMMARIES:
            # large or slow boards: a light summary instead of downloading everything
            s = SUMMARIES[ats](slug_or_url)
            total, italy, texts, slug_or_url, instance = s["total"], s["italy"], s["texts"], s["url"], None
            if s["italy_estimated"]:
                italy_note = "n_jobs_italy estimated from a sample of ads"
        else:
            jobs, meta = FETCHERS[ats](slug_or_url, instance)
            total, italy = len(jobs), count_italy(jobs)
            instance = meta.get("instance")
            # only board name, titles and descriptions: NOT the URLs, which contain the slug being tested
            texts = [meta.get("board_name")] + [f"{j['title']} {j['description'][:3000]}" for j in jobs[:25]]
    except http.RobotsDisallowed:
        return {"ok": False, "error": "RobotsDisallowed"}
    except (requests.RequestException, ValueError) as e:
        if isinstance(e, http.HostBlocked):
            return {"ok": False, "error": "HostBlocked"}
        code = getattr(getattr(e, "response", None), "status_code", None)
        return {"ok": False, "error": f"HTTP {code}" if code else type(e).__name__}
    return {
        "ok": True, "slug_or_url": slug_or_url, "instance": instance,
        "n_jobs_total": total, "n_jobs_italy": italy,
        "match": name_match(company["name"], company["website"], texts),
        "italy_note": italy_note,
    }


def confidence_for(method: str, info: dict) -> str:
    """high = ready for the crawler; medium = likely; low = manual review."""
    if not info["ok"] and info.get("error") == "HostBlocked" and method == "fingerprint":
        return "medium"     # ATS clear from the site, but the service asked for a pause: validate later
    if info["ok"] and not info["n_jobs_total"] and method == "fingerprint":
        return "medium"     # official board and valid API, but no ads today: the crawler keeps it
    if not info["ok"] or not info["n_jobs_total"]:
        return "low"
    if info["match"] == "strong":
        return "high"
    if method == "fingerprint":
        return "medium"     # the fingerprint is on the official site: strong evidence even without the name
    return "low"


def no_italy(info: dict) -> bool:
    """The board has ads but none in Italy (count known and equal to zero)."""
    return info.get("n_jobs_italy") == 0


def workday_probe_plan(tenants: list[str]) -> list[tuple[str, str]]:
    """(tenant, server) pairs to try, most likely first: the best 2 names on every server,
    then the next 2 names only on the 4 most used servers. At most 30 tries (~1 s each)."""
    plan = [(t, wd) for t in tenants[:2] for wd in WORKDAY_HOSTS]
    plan += [(t, wd) for t in tenants[2:4] for wd in WORKDAY_HOSTS[:4]]
    return plan


def probe_workday(company: dict, notes: list):
    """Tries {tenant}.wdN.myworkdayjobs.com (one request to the tenant's robots.txt per try)."""
    for tenant, wd in workday_probe_plan(workday_tenants(company["name"], company["website"])):
        if time_is_up():
            notes.append("time up during the Workday probe")
            return None
        host = f"{tenant}.{wd}.myworkdayjobs.com"
        try:
            site = discover_workday_site(host, retries=0)
        except requests.RequestException:
            continue
        if site:
            return f"https://{host}/{site}"
    return None


def probe(company: dict, notes: list):
    """Tries the candidate slugs. Returns (ats, info, confidence) or None."""
    slugs = candidate_slugs(company["name"], company["website"])
    weak = None
    rejected = []
    # Workable and Recruitee: only the 3 most likely slugs (few customers among large companies).
    # Personio is not probed: a missing slug redirects to Personio's marketing site.
    plan = [("greenhouse", slugs), ("lever", slugs), ("ashby", slugs),
            ("workable", slugs[:3]), ("recruitee", slugs[:3])]
    for ats, ats_slugs in plan:
        for slug in ats_slugs:
            if time_is_up():
                notes.append(f"time up during the probe ({ats})")
                return weak
            # Greenhouse: the main API also answers for EU boards, one request is enough
            info = inspect_board(company, ats, slug, "us" if ats == "greenhouse" else None)
            if not info["ok"] or not info["n_jobs_total"]:
                continue
            if no_italy(info):
                # name found but no ads in Italy: typical of namesakes (e.g. "google", "indigo")
                if weak is None:
                    weak = (ats, info, "low")
                notes.append(f"{ats}/{slug}: no ads in Italy, possibly a namesake")
                continue
            if info["match"] == "strong":
                return ats, info, "high"
            if info["match"] == "weak" and weak is None:
                weak = (ats, info, "low")
            else:
                rejected.append(f"{ats}/{slug}")

    if time_is_up():
        notes.append("time up before the Workday probe")
        return weak
    url = probe_workday(company, notes)
    if url:
        info = inspect_board(company, "workday", url)
        if info["ok"] and info["n_jobs_total"]:
            if no_italy(info):
                notes.append("workday: no ads in Italy, possibly a namesake or the wrong site")
                return weak or ("workday", info, "low")
            if info["match"] == "strong":
                return "workday", info, "high"
            # the Workday tenant is built from the company name: namesakes are rare, but check it
            if weak is None:
                weak = ("workday", info, "medium" if info["match"] == "weak" else "low")

    if rejected:
        notes.append("rejected (name not found in the ads): " + ", ".join(rejected[:5]))
    return weak


# ---------------------------------------------------------------- orchestration

def empty_row(company: dict) -> dict:
    row = dict.fromkeys(REGISTRY_COLUMNS)
    row.update(company_id=company["company_id"], company=company["name"],
               sector=company["sector"], is_tech=company["is_tech"],
               last_checked=db.now_iso())
    return row


def fill_from_info(row: dict, ats: str, info: dict, method: str, confidence: str, notes: list = None):
    row.update(ats=ats, supported=1, detection_method=method, confidence=confidence,
               slug_or_url=info.get("slug_or_url", row.get("slug_or_url")),
               instance=info.get("instance"),
               n_jobs_total=info.get("n_jobs_total"), n_jobs_italy=info.get("n_jobs_italy"))
    if notes is not None and info.get("italy_note"):
        notes.append(info["italy_note"])


def apply_override(company: dict, override: dict) -> dict:
    """The manual correction always wins. If the ATS is supported, the ads are still counted."""
    row = empty_row(company)
    ats = override["ats"].strip().lower()
    row.update(ats=ats, slug_or_url=override.get("slug_or_url") or None,
               instance=override.get("instance") or None,
               supported=int(ats in SUPPORTED_ATS), detection_method="manual",
               confidence=override.get("confidence") or "high",
               notes=override.get("notes") or None)
    if ats in SUPPORTED_ATS and row["slug_or_url"]:
        info = inspect_board(company, ats, row["slug_or_url"], row["instance"])
        if info["ok"]:
            row.update(n_jobs_total=info["n_jobs_total"], n_jobs_italy=info["n_jobs_italy"],
                       instance=info["instance"] or row["instance"])
        else:
            row["notes"] = f"{row['notes'] or ''} [override unreachable: {info['error']}]".strip()
    return row


def join_notes(notes: list) -> str | None:
    """Notes without duplicates, each shortened: they must stay readable in the CSV."""
    unique = [n if len(n) <= 140 else n[:137] + "..." for n in dict.fromkeys(notes)]
    return "; ".join(unique)[:600] or None


def discover(company: dict, browser) -> dict:
    row = empty_row(company)
    notes = []

    # 1) fingerprint: requests, then the browser if no supported ATS was found
    found, careers_url, links = scan_static(company, notes)
    if browser and not any(f["supported"] for f in found) and not time_is_up():
        b_found, b_url = scan_browser(browser, company, links, notes)
        found = merge_fingerprints(found + b_found)
        careers_url = careers_url or b_url
    row["careers_url"] = careers_url

    if found:
        found = prefer_own_slug(found, company)
        best = found[0]
        others = [f"{f['ats']}/{f['slug_or_url']}" for f in found[1:4]]
        if others:
            notes.append("other fingerprints: " + ", ".join(others))
        row["evidence"] = best["evidence"]

        if best["supported"]:
            target = board_target(best, careers_url)
            info = inspect_board(company, best["ats"], target, best["instance"]) if target else                 {"ok": False, "error": "career page not found"}
            if best["ats"] in PARTIAL_ATS and not info["ok"] and info.get("error") != "HostBlocked":
                # e.g. "classic" SuccessFactors without a job sitemap: known ATS but not downloadable
                row.update(ats=best["ats"], slug_or_url=best["slug_or_url"] or careers_url, supported=0,
                           detection_method="fingerprint", confidence="medium")
                notes.append(f"{best['ats']}: no public job list ({info.get('error')})")
                row["notes"] = join_notes(notes)
                return row
            confidence = confidence_for("fingerprint", info)
            if confidence != "low" and not info["ok"]:
                row.update(ats=best["ats"], slug_or_url=best["slug_or_url"], supported=1,
                           instance=best["instance"], detection_method="fingerprint", confidence=confidence)
                notes.append("validation postponed: the ATS service asked for a pause")
                row["notes"] = join_notes(notes)
                return row
            if confidence != "low":
                if not info["n_jobs_total"]:
                    notes.append("board empty at the moment")
                fill_from_info(row, best["ats"], info, "fingerprint", confidence, notes)
                row["notes"] = join_notes(notes)
                return row
            notes.append(f"fingerprint {best['ats']}/{best['slug_or_url']} not validated "
                         f"({info.get('error') or 'empty board'})")
        else:
            # unsupported ATS: no API to query, but the fingerprint is on the official site;
            # without a slug (e.g. only the SuccessFactors script) keep the career page as a reference
            row.update(ats=best["ats"], slug_or_url=best["slug_or_url"] or careers_url, supported=0,
                       detection_method="fingerprint", confidence="medium")
            row["notes"] = join_notes(notes)
            return row

    # 2) probe the candidate slugs
    result = probe(company, notes)
    if result:
        ats, info, confidence = result
        fill_from_info(row, ats, info, "probe", confidence, notes)
        if info["match"] != "strong":
            notes.append(f"name only partly found ({info['match'] or 'missing'})")
        row["notes"] = join_notes(notes)
        return row

    # 3) nothing found
    if found:   # supported fingerprint but not validated: keep it for manual review
        best = found[0]
        row.update(ats=best["ats"], slug_or_url=best["slug_or_url"], instance=best["instance"],
                   supported=1, detection_method="fingerprint", confidence="low")
    else:
        row.update(ats="none", supported=0, confidence="low")
        if not careers_url:
            notes.append("career page not found")
    row["notes"] = join_notes(notes)
    return row


def discover_workday_only(company: dict, existing: dict) -> dict:
    """Only the Workday probe, for companies already analyzed without finding an ATS (--workday-only)."""
    row = dict(existing)
    row["last_checked"] = db.now_iso()
    notes = []
    url = probe_workday(company, notes)
    info = inspect_board(company, "workday", url) if url else None
    if not info or not info["ok"] or not info["n_jobs_total"]:
        msg = ("wide Workday probe: no tenant found" if not url
               else f"wide Workday probe: {url} without valid ads")
        row["notes"] = join_notes([n for n in [existing.get("notes"), *notes, msg] if n])
        return row
    if no_italy(info):
        confidence = "low"
        notes.append("workday: no ads in Italy, possibly a namesake")
    else:
        confidence = {"strong": "high", "weak": "medium"}.get(info["match"], "low")
    new = empty_row(company)
    new["careers_url"] = existing.get("careers_url")
    fill_from_info(new, "workday", info, "probe", confidence, notes)
    new["notes"] = join_notes(notes)
    return new


def is_fresh(last_checked: str, max_age_days: int) -> bool:
    if not last_checked:
        return False
    checked = datetime.strptime(last_checked, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - checked < timedelta(days=max_age_days)


def override_changed(override: dict, existing: dict) -> bool:
    if not override:
        return False
    return (existing["detection_method"] != "manual"
            or existing["ats"] != override["ats"].strip().lower()
            or (existing["slug_or_url"] or "") != (override.get("slug_or_url") or ""))


def export_csv(conn):
    rows = conn.execute(
        f"SELECT {', '.join(REGISTRY_COLUMNS)} FROM ats_registry ORDER BY is_tech DESC, company"
    ).fetchall()
    with open(REGISTRY_CSV, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(REGISTRY_COLUMNS)
        writer.writerows([tuple(r) for r in rows])


def print_report(conn):
    print("\n=== Coverage ===")
    cols = ["group_name", "companies", "with_ats", "supported_ready", "supported_to_review",
            "not_supported", "no_ats"]
    print(" | ".join(f"{c:>22}" if i else f"{c:<10}" for i, c in enumerate(cols)))
    order = {"tech": 0, "non tech": 1, "TOTAL": 2}
    for r in sorted(conn.execute("SELECT * FROM v_coverage").fetchall(), key=lambda r: order[r["group_name"]]):
        print(" | ".join(f"{r[c]:>22}" if i else f"{r[c]:<10}" for i, c in enumerate(cols)))

    print("\n=== By ATS ===")
    for r in conn.execute("""
        SELECT ats, supported, COUNT(*) AS n,
               SUM(CASE WHEN confidence = 'low' THEN 1 ELSE 0 END) AS to_review
        FROM ats_registry GROUP BY ats, supported ORDER BY supported DESC, n DESC"""):
        flag = "supported" if r["supported"] else "not supported"
        print(f"  {r['ats']:<16} {flag:<15} {r['n']:>4} companies  (to review: {r['to_review']})")


def eta_minutes(started: float, done: int, total: int) -> float:
    """Estimated minutes left, from the average time per company so far."""
    return (time.time() - started) / done * (total - done) / 60


def main():
    p = argparse.ArgumentParser(description="Automatic discovery of ATS and slugs")
    p.add_argument("--only", help="comma-separated names (as in the seed list)")
    p.add_argument("--force", action="store_true", help="recheck recently checked companies too")
    p.add_argument("--max-age-days", type=int, default=7,
                   help="skip companies checked in the last N days (default 7)")
    p.add_argument("--workday-only", action="store_true",
                   help="for companies with no ATS found: run only the wide Workday probe")
    p.add_argument("--retry-none", action="store_true",
                   help="retry companies with no ATS found, even if checked recently")
    p.add_argument("--max-seconds", type=int, default=MAX_SECONDS,
                   help=f"max time per company in seconds (default {MAX_SECONDS})")
    p.add_argument("--no-browser", action="store_true", help="do not use the headless browser")
    p.add_argument("--cache", action="store_true", help="on-disk HTTP cache (development)")
    p.add_argument("--report", action="store_true", help="print only the coverage report")
    args = p.parse_args()

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    http.init(use_cache=args.cache)
    conn = db.connect()

    if args.report:
        print_report(conn)
        return

    companies = load_seed()
    save_companies(conn, companies)
    overrides = load_overrides()
    unknown = set(overrides) - {c["company_id"] for c in companies}
    if unknown:
        print(f"WARNING: overrides for companies missing from the seed list: {', '.join(sorted(unknown))}")

    if args.only:
        wanted = {company_id_from_name(n) for n in args.only.split(",")}
        companies = [c for c in companies if c["company_id"] in wanted]

    existing = {r["company_id"]: dict(r) for r in conn.execute("SELECT * FROM ats_registry")}
    if args.workday_only:
        companies = [c for c in companies if existing.get(c["company_id"], {}).get("ats") == "none"]
    todo = []
    for c in companies:
        ex = existing.get(c["company_id"])
        ov = overrides.get(c["company_id"])
        # ATS that became supported after the last check (e.g. Workable): reassess it
        # (except the non-downloadable variants already checked, e.g. SuccessFactors without a sitemap)
        newly_supported = (ex and ex["ats"] in SUPPORTED_ATS and not ex["supported"]
                           and not (ex["ats"] in PARTIAL_ATS and "no public job list" in (ex["notes"] or "")))
        retry_none = args.retry_none and ex and ex["ats"] == "none"
        # supported board without an Italy count (e.g. Workday without a country filter): recompute it
        missing_italy = ex and ex["supported"] and ex["n_jobs_total"] and ex["n_jobs_italy"] is None
        if args.workday_only:
            todo.append(c)
            continue
        if (not args.force and ex and is_fresh(ex["last_checked"], args.max_age_days)
                and not override_changed(ov, ex)
                and not (newly_supported or retry_none or missing_italy)):
            continue
        todo.append(c)
    print(f"Companies to check: {len(todo)} (skipped as recent: {len(companies) - len(todo)})")

    browser = None
    if not args.no_browser and not args.workday_only and todo:
        from lib.browser import Browser
        browser = Browser().__enter__()
    started = time.time()
    try:
        for i, c in enumerate(todo, 1):
            t0 = time.time()
            start_clock(args.max_seconds)
            ov = overrides.get(c["company_id"])
            try:
                if args.workday_only:
                    row = discover_workday_only(c, existing[c["company_id"]])
                else:
                    row = apply_override(c, ov) if ov else discover(c, browser)
            except Exception as e:      # one failing company must not stop the whole run
                row = empty_row(c)
                row.update(ats="none", supported=0, confidence="low",
                           notes=f"unexpected error: {type(e).__name__}: {str(e)[:150]}")
            db.upsert(conn, "ats_registry", row, key="company_id")
            conn.commit()     # save right away: if the script stops, it resumes from here
            print(f"[{i}/{len(todo)}] {c['name']:<32} {row['ats']:<15} {str(row['slug_or_url'] or ''):<55} "
                  f"{row['confidence'] or '':<6} jobs={row['n_jobs_total']} it={row['n_jobs_italy']}  "
                  f"({time.time() - t0:.0f}s, about {eta_minutes(started, i, len(todo)):.0f} min left)")
    finally:
        if browser:
            browser.__exit__(None, None, None)

    export_csv(conn)
    print(f"\nRegistry exported to {REGISTRY_CSV.relative_to(db.ROOT)}")
    print_report(conn)


if __name__ == "__main__":
    main()
