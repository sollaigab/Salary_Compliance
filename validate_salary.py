"""
Manual validation of the pay extraction.

1) Create the sample to label:
       python validate_salary.py sample            # ~200 ads -> data/validation/salary_sample_<date>.csv
       python validate_salary.py sample --size 20  # a smaller batch
2) Open the CSV in Excel and fill in the last columns:
       verdict       ok        = extracted figure is correct (min, max and period)
                     wrong     = extracted figure is wrong (e.g. bonus, meal voucher, revenue)
                     partial   = right figure but wrong min/max or period
                     missed    = the ad states a figure but the script did not find it
                     none      = the ad really states no figure
       notes         free text
   Italian labels and column names from earlier samples (giudizio, ok/errato/parziale/mancata/nessuna)
   are still accepted.
3) Compute precision and recall:
       python validate_salary.py score data/validation/salary_sample_<date>.csv

The CSV contains phrases from the job ads: it stays private (data/validation/ is excluded from git).
"""

import argparse
import codecs
import csv
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import date

from lib import db

OUT_DIR = db.ROOT / "data" / "validation"
QUOTAS = {"text": 120, "structured": 20, "vague": 30, "none": 30}   # 200 ads; --size scales them
MAX_PER_COMPANY = 8
KEYWORD_RE = re.compile(r"retribu|\bRAL\b|salar|stipend|compens|€|\bEUR\b|CCNL|commisurat", re.IGNORECASE)
COLUMNS = ["id", "company", "ats", "title", "salary_source", "salary_transparency", "salary_min", "salary_max",
           "salary_period", "salary_gross_net", "ral_min_annual", "ral_max_annual", "matched_phrases",
           "context", "url", "verdict", "notes"]
# Column names used by the first samples (in Italian)
OLD_COLUMNS = {"giudizio": "verdict", "note": "notes", "frasi_trovate": "matched_phrases", "contesto": "context"}


def context(description: str, width: int = 220) -> str:
    """The text around the first pay keyword (to judge without opening the site)."""
    m = KEYWORD_RE.search(description or "")
    if not m:
        return ""
    start = max(0, m.start() - 60)
    return re.sub(r"\s+", " ", description[start:m.start() + width]).strip()


def pick(rows: list, n: int, rng: random.Random) -> list:
    """n random rows, at most MAX_PER_COMPANY per company."""
    rng.shuffle(rows)
    per_company, chosen = Counter(), []
    for r in rows:
        if per_company[r["company"]] < MAX_PER_COMPANY:
            chosen.append(r)
            per_company[r["company"]] += 1
        if len(chosen) == n:
            break
    return chosen


def already_checked(paths: list[str]) -> set[str]:
    """URLs of the ads already labeled (verdict filled in) in the given CSVs."""
    urls = set()
    for path in paths or []:
        urls |= {r["url"] for r in read_labeled(path) if (r.get("verdict") or "").strip()}
    return urls


def make_sample(seed: int, exclude: list[str] = None, size: int = 200):
    conn = db.connect()
    skip = already_checked(exclude)
    if skip:
        print(f"  excluded {len(skip)} ads already checked")
    rows = [dict(r) for r in conn.execute("""
        SELECT j.job_key, c.name AS company, j.ats, j.title, j.description, j.url,
               j.salary_source, j.salary_transparency, j.salary_min, j.salary_max, j.salary_period,
               j.salary_gross_net, j.ral_min_annual, j.ral_max_annual, j.salary_text_matches
        FROM jobs j JOIN companies c ON c.company_id = j.company_id
        WHERE j.is_active = 1""") if r["url"] not in skip]
    groups = {
        "text": [r for r in rows if r["salary_source"] == "text"],
        "structured": [r for r in rows if r["salary_source"] == "structured"],
        "vague": [r for r in rows if r["salary_transparency"] == "vague"],
        "none": [r for r in rows if r["salary_transparency"] == "none"],
    }
    rng = random.Random(seed)
    sample = []
    for name, quota in QUOTAS.items():
        quota = max(1, round(quota * size / sum(QUOTAS.values())))
        chosen = pick(groups[name], quota, rng)
        print(f"  {name:<11} {len(chosen):>4} of {len(groups[name])} available")
        sample += chosen

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"salary_sample_{date.today().isoformat()}.csv"
    n = 2
    while path.exists() or path.with_suffix(".csv.csv").exists():   # never overwrite an existing sample
        path = OUT_DIR / f"salary_sample_{date.today().isoformat()}_v{n}.csv"
        n += 1
    with open(path, "w", encoding="utf-8-sig", newline="") as f:   # utf-8-sig: Excel reads accents
        w = csv.writer(f, delimiter=";")                              # ';' = Excel with Italian locale
        w.writerow(COLUMNS)
        for i, r in enumerate(sample, 1):
            w.writerow([i, r["company"], r["ats"], r["title"], r["salary_source"], r["salary_transparency"],
                        r["salary_min"], r["salary_max"], r["salary_period"], r["salary_gross_net"],
                        r["ral_min_annual"], r["ral_max_annual"], r["salary_text_matches"],
                        context(r["description"]), r["url"], "", ""])
    print(f"\nSample of {len(sample)} ads in {path.relative_to(db.ROOT)}")


def read_labeled(path: str) -> list[dict]:
    """Reads the CSV however Excel saved it: UTF-8 or Windows encoding, ';' or ',' separator."""
    raw = open(path, "rb").read()
    # Excel sometimes saves a "mixed" file: original text in UTF-8 and typed characters (è, à...)
    # in the Windows encoding. Bytes that are not valid UTF-8 are read as Windows (cp1252).
    codecs.register_error("cp1252_fallback",
                          lambda e: (e.object[e.start:e.end].decode("cp1252", errors="replace"), e.end))
    text = raw.decode("utf-8-sig", errors="cp1252_fallback")
    delimiter = ";" if text.splitlines()[0].count(";") >= text.splitlines()[0].count(",") else ","
    rows = list(csv.DictReader(text.splitlines(), delimiter=delimiter))
    return [{OLD_COLUMNS.get(k, k): v for k, v in r.items()} for r in rows]


# Accepted variants for each verdict (English, and Italian from the first samples)
LABEL_ALIASES = {
    "ok": {"ok", "correct", "right", "yes", "corretto", "corretta", "giusto", "giusta", "si", "sì", "vero"},
    "wrong": {"wrong", "no", "errato", "errata", "sbagliato", "sbagliata", "falso"},
    "partial": {"partial", "partly", "parziale", "parzialmente", "quasi"},
    "missed": {"missed", "missing", "mancata", "mancato", "persa", "perso", "non trovata"},
    "none": {"none", "nessuna", "nessuno", "assente", "niente"},
}


def normalize_label(value: str) -> str | None:
    v = (value or "").strip().lower()
    for label, aliases in LABEL_ALIASES.items():
        if v in aliases:
            return label
    return None


def score(path: str):
    rows = [r for r in read_labeled(path) if (r.get("verdict") or "").strip()]
    if not rows:
        print("No row has the 'verdict' column filled in.")
        return
    unknown = Counter(r["verdict"].strip() for r in rows if not normalize_label(r["verdict"]))
    if unknown:
        print(f"WARNING: unrecognized verdicts (left out of the calculation): {dict(unknown)}\n")
    for r in rows:
        label = normalize_label(r["verdict"]) or ""
        # Rows where the script found NO figure: "ok" = rightly empty (none),
        # "wrong"/"partial" = there was a figure and it was lost (missed)
        if not (r.get("salary_min") or "").strip():
            label = {"ok": "none", "wrong": "missed", "partial": "missed"}.get(label, label)
        r["verdict"] = label
    rows = [r for r in rows if r["verdict"]]
    labels = lambda rs: Counter(r["verdict"] for r in rs)

    def report(title, rs):
        c = labels(rs)
        extracted = c["ok"] + c["wrong"] + c["partial"]      # rows where the script found a figure
        precision = c["ok"] / extracted if extracted else None
        real = c["ok"] + c["partial"] + c["missed"]          # ads that actually state a figure
        recall = (c["ok"] + c["partial"]) / real if real else None
        fmt = lambda x: f"{100 * x:.0f}%" if x is not None else "-"
        print(f"  {title:<28} n={len(rs):>4}  precision {fmt(precision):>5}  recall {fmt(recall):>5}  {dict(c)}")

    print(f"Labeled rows: {len(rows)}\n")
    report("TOTAL", rows)
    print("\nBy source:")
    by = defaultdict(list)
    for r in rows:
        by[r["salary_source"]].append(r)
    for k, rs in sorted(by.items()):
        report(k, rs)
    print("\nBy ATS:")
    by = defaultdict(list)
    for r in rows:
        by[r["ats"]].append(r)
    for k, rs in sorted(by.items()):
        report(k, rs)
    print("\nPrecision = correct extracted figures / extracted figures;  recall = figures found / figures present.")


def refresh(path: str):
    """Updates the extracted values in the rows NOT yet judged (after improving the rules).
    Rows already judged stay as they were: the verdict refers to those values."""
    rows = read_labeled(path)
    conn = db.connect()
    cols = ["salary_source", "salary_transparency", "salary_min", "salary_max", "salary_period",
            "salary_gross_net", "ral_min_annual", "ral_max_annual"]
    changed = 0
    for r in rows:
        if (r.get("verdict") or "").strip():
            continue
        db_row = conn.execute(f"SELECT {', '.join(cols)}, salary_text_matches FROM jobs WHERE url = ?",
                              (r["url"],)).fetchone()
        if not db_row:
            continue
        new = {c: "" if db_row[c] is None else str(db_row[c]) for c in cols}
        new["matched_phrases"] = db_row["salary_text_matches"] or ""
        if any(r.get(k) != v for k, v in new.items()):
            changed += 1
        r.update(new)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"Updated {changed} rows not yet judged in {path}")


def main():
    p = argparse.ArgumentParser(description="Manual validation of the pay extraction")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample", help="create the CSV to label")
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--size", type=int, default=200, help="about how many ads (default 200)")
    s.add_argument("--exclude", nargs="*", help="CSVs already labeled: their ads are not picked again")
    sc = sub.add_parser("score", help="compute precision and recall from a labeled CSV")
    sc.add_argument("path")
    rf = sub.add_parser("refresh", help="update the extracted values in rows not yet judged")
    rf.add_argument("path")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.cmd == "sample":
        make_sample(args.seed, args.exclude, args.size)
    elif args.cmd == "refresh":
        refresh(args.path)
    else:
        score(args.path)


if __name__ == "__main__":
    main()
