"""
Validazione manuale dell'estrazione della retribuzione.

1) Crea il campione da etichettare:
       python validate_salary.py sample            # ~200 annunci -> data/validation/salary_sample_<data>.csv
2) Apri il CSV con Excel e compila le colonne in fondo:
       giudizio      ok        = cifra estratta corretta (min, max e periodo)
                     errato    = cifra estratta sbagliata (es. bonus, buono pasto, fatturato)
                     parziale  = cifra giusta ma min/max o periodo sbagliati
                     mancata   = l'annuncio indica una cifra ma lo script non l'ha trovata
                     nessuna   = l'annuncio davvero non indica una cifra
       note          libero
3) Calcola precisione e recall:
       python validate_salary.py score data/validation/salary_sample_<data>.csv

Il CSV contiene frasi degli annunci: resta privato (data/validation/ è escluso da git).
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
QUOTAS = {"text": 120, "structured": 20, "vaga": 30, "assente": 30}
MAX_PER_COMPANY = 8
KEYWORD_RE = re.compile(r"retribu|\bRAL\b|salar|stipend|compens|€|\bEUR\b|CCNL|commisurat", re.IGNORECASE)
COLUMNS = ["id", "company", "ats", "title", "salary_source", "salary_transparency", "salary_min", "salary_max",
           "salary_period", "salary_gross_net", "ral_min_annual", "ral_max_annual", "frasi_trovate",
           "contesto", "url", "giudizio", "note"]


def context(description: str, width: int = 220) -> str:
    """Il pezzo di testo intorno alla prima parola chiave sulla retribuzione (per giudicare senza aprire il sito)."""
    m = KEYWORD_RE.search(description or "")
    if not m:
        return ""
    start = max(0, m.start() - 60)
    return re.sub(r"\s+", " ", description[start:m.start() + width]).strip()


def pick(rows: list, n: int, rng: random.Random) -> list:
    """n righe a caso, al massimo MAX_PER_COMPANY per azienda."""
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
    """URL degli annunci già etichettati (colonna giudizio compilata) nei CSV indicati."""
    urls = set()
    for path in paths or []:
        urls |= {r["url"] for r in read_labeled(path) if (r.get("giudizio") or "").strip()}
    return urls


def make_sample(seed: int, exclude: list[str] = None):
    conn = db.connect()
    skip = already_checked(exclude)
    if skip:
        print(f"  esclusi {len(skip)} annunci già controllati")
    rows = [dict(r) for r in conn.execute("""
        SELECT j.job_key, c.name AS company, j.ats, j.title, j.description, j.url,
               j.salary_source, j.salary_transparency, j.salary_min, j.salary_max, j.salary_period,
               j.salary_gross_net, j.ral_min_annual, j.ral_max_annual, j.salary_text_matches
        FROM jobs j JOIN companies c ON c.company_id = j.company_id
        WHERE j.is_active = 1""") if r["url"] not in skip]
    groups = {
        "text": [r for r in rows if r["salary_source"] == "text"],
        "structured": [r for r in rows if r["salary_source"] == "structured"],
        "vaga": [r for r in rows if r["salary_transparency"] == "vaga"],
        "assente": [r for r in rows if r["salary_transparency"] == "assente"],
    }
    rng = random.Random(seed)
    sample = []
    for name, quota in QUOTAS.items():
        chosen = pick(groups[name], quota, rng)
        print(f"  {name:<11} {len(chosen):>4} su {len(groups[name])} disponibili")
        sample += chosen

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"salary_sample_{date.today().isoformat()}.csv"
    n = 2
    while path.exists() or path.with_suffix(".csv.csv").exists():   # non sovrascrivere un campione esistente
        path = OUT_DIR / f"salary_sample_{date.today().isoformat()}_v{n}.csv"
        n += 1
    with open(path, "w", encoding="utf-8-sig", newline="") as f:   # utf-8-sig: Excel legge gli accenti
        w = csv.writer(f, delimiter=";")                              # ';' = Excel in italiano
        w.writerow(COLUMNS)
        for i, r in enumerate(sample, 1):
            w.writerow([i, r["company"], r["ats"], r["title"], r["salary_source"], r["salary_transparency"],
                        r["salary_min"], r["salary_max"], r["salary_period"], r["salary_gross_net"],
                        r["ral_min_annual"], r["ral_max_annual"], r["salary_text_matches"],
                        context(r["description"]), r["url"], "", ""])
    print(f"\nCampione di {len(sample)} annunci in {path.relative_to(db.ROOT)}")


def read_labeled(path: str) -> list[dict]:
    """Legge il CSV comunque Excel l'abbia salvato: UTF-8 o codifica Windows, separatore ';' o ','."""
    raw = open(path, "rb").read()
    # Excel a volte salva un file "misto": testo originale in UTF-8 e caratteri digitati (è, à...)
    # nella codifica di Windows. I byte che non sono UTF-8 valido si leggono come Windows (cp1252).
    codecs.register_error("cp1252_fallback",
                          lambda e: (e.object[e.start:e.end].decode("cp1252", errors="replace"), e.end))
    text = raw.decode("utf-8-sig", errors="cp1252_fallback")
    delimiter = ";" if text.splitlines()[0].count(";") >= text.splitlines()[0].count(",") else ","
    return list(csv.DictReader(text.splitlines(), delimiter=delimiter))


# Varianti accettate per ogni giudizio (maschile/femminile, sinonimi)
LABEL_ALIASES = {
    "ok": {"ok", "corretto", "corretta", "giusto", "giusta", "si", "sì", "vero"},
    "errato": {"errato", "errata", "sbagliato", "sbagliata", "no", "falso"},
    "parziale": {"parziale", "parzialmente", "quasi"},
    "mancata": {"mancata", "mancato", "persa", "perso", "non trovata"},
    "nessuna": {"nessuna", "nessuno", "assente", "niente"},
}


def normalize_label(value: str) -> str | None:
    v = (value or "").strip().lower()
    for label, aliases in LABEL_ALIASES.items():
        if v in aliases:
            return label
    return None


def score(path: str):
    rows = [r for r in read_labeled(path) if (r.get("giudizio") or "").strip()]
    if not rows:
        print("Nessuna riga compilata nella colonna 'giudizio'.")
        return
    unknown = Counter(r["giudizio"].strip() for r in rows if not normalize_label(r["giudizio"]))
    if unknown:
        print(f"ATTENZIONE: giudizi non riconosciuti (esclusi dal calcolo): {dict(unknown)}\n")
    for r in rows:
        label = normalize_label(r["giudizio"]) or ""
        # Righe in cui lo script NON ha trovato una cifra: "ok" = giusto che non ci sia (nessuna),
        # "errato"/"parziale" = la cifra c'era ma è stata persa (mancata)
        if not (r.get("salary_min") or "").strip():
            label = {"ok": "nessuna", "errato": "mancata", "parziale": "mancata"}.get(label, label)
        r["giudizio"] = label
    rows = [r for r in rows if r["giudizio"]]
    labels = lambda rs: Counter(r["giudizio"] for r in rs)

    def report(title, rs):
        c = labels(rs)
        extracted = c["ok"] + c["errato"] + c["parziale"]      # righe in cui lo script ha trovato una cifra
        precision = c["ok"] / extracted if extracted else None
        real = c["ok"] + c["parziale"] + c["mancata"]           # annunci che una cifra la indicano davvero
        recall = (c["ok"] + c["parziale"]) / real if real else None
        fmt = lambda x: f"{100 * x:.0f}%" if x is not None else "-"
        print(f"  {title:<28} n={len(rs):>4}  precisione {fmt(precision):>5}  recall {fmt(recall):>5}  {dict(c)}")

    print(f"Righe etichettate: {len(rows)}\n")
    report("TOTALE", rows)
    print("\nPer fonte:")
    by = defaultdict(list)
    for r in rows:
        by[r["salary_source"]].append(r)
    for k, rs in sorted(by.items()):
        report(k, rs)
    print("\nPer ATS:")
    by = defaultdict(list)
    for r in rows:
        by[r["ats"]].append(r)
    for k, rs in sorted(by.items()):
        report(k, rs)
    print("\nPrecisione = cifre estratte corrette / cifre estratte;  recall = cifre trovate / cifre presenti.")


def refresh(path: str):
    """Aggiorna i valori estratti nelle righe NON ancora giudicate (dopo un miglioramento delle regole).
    Le righe già giudicate restano com'erano: il giudizio si riferisce a quei valori."""
    rows = read_labeled(path)
    conn = db.connect()
    cols = ["salary_source", "salary_transparency", "salary_min", "salary_max", "salary_period",
            "salary_gross_net", "ral_min_annual", "ral_max_annual"]
    changed = 0
    for r in rows:
        if (r.get("giudizio") or "").strip():
            continue
        db_row = conn.execute(f"SELECT {', '.join(cols)}, salary_text_matches FROM jobs WHERE url = ?",
                              (r["url"],)).fetchone()
        if not db_row:
            continue
        new = {c: "" if db_row[c] is None else str(db_row[c]) for c in cols}
        new["frasi_trovate"] = db_row["salary_text_matches"] or ""
        if any(r[k] != v for k, v in new.items()):
            changed += 1
        r.update(new)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, delimiter=";", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"Aggiornate {changed} righe non ancora giudicate in {path}")


def main():
    p = argparse.ArgumentParser(description="Validazione manuale dell'estrazione della retribuzione")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample", help="crea il CSV da etichettare")
    s.add_argument("--seed", type=int, default=42)
    s.add_argument("--exclude", nargs="*", help="CSV già etichettati: i loro annunci non vengono ripresi")
    sc = sub.add_parser("score", help="calcola precisione e recall da un CSV etichettato")
    sc.add_argument("path")
    rf = sub.add_parser("refresh", help="aggiorna i valori estratti nelle righe non ancora giudicate")
    rf.add_argument("path")
    args = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.cmd == "sample":
        make_sample(args.seed, args.exclude)
    elif args.cmd == "refresh":
        refresh(args.path)
    else:
        score(args.path)


if __name__ == "__main__":
    main()
