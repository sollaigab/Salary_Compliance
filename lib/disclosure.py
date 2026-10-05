"""
Protezione dell'identità delle aziende nei dati pubblici (controllo della divulgazione statistica).

I dati pubblici contengono SOLO celle aggregate (periodo x macro-settore x una dimensione).
Una cella si pubblica se rispetta tutte queste regole:

1. soglia minima:   almeno MIN_JOBS annunci e MIN_COMPANIES aziende diverse;
2. dominanza:       nessuna azienda supera MAX_SHARE degli annunci della cella
                    (altrimenti il dato della cella è, di fatto, quello di un'azienda);
3. mediane RAL:     solo se le cifre vengono da almeno MIN_JOBS annunci e MIN_COMPANIES aziende,
                    senza un'azienda oltre MAX_SHARE;
4. secondaria:      se in un gruppo (stesso periodo, macro-settore e dimensione) una sola cella
                    è nascosta, si nasconde anche la più piccola delle visibili: altrimenti la cella
                    nascosta si ricaverebbe per differenza dal totale.
"""

MIN_JOBS = 5
MIN_COMPANIES = 3
MAX_SHARE = 0.70


def cell_ok(n_jobs: int, n_companies: int, top_share: float) -> bool:
    return n_jobs >= MIN_JOBS and n_companies >= MIN_COMPANIES and top_share <= MAX_SHARE


def apply_rules(cells: list[dict]) -> list[dict]:
    """Restituisce solo le celle pubblicabili. Ogni cella ha le chiavi:
    period, macro, dim, value, n_jobs, n_companies, top_share,
    n_ral, n_ral_companies, ral_top_share, ral_min_median, ral_max_median (+ altre metriche)."""
    for c in cells:
        c["_hidden"] = not cell_ok(c["n_jobs"], c["n_companies"], c["top_share"])
        if not cell_ok(c["n_ral"], c["n_ral_companies"], c["ral_top_share"]):
            c["ral_min_median"] = c["ral_max_median"] = None

    # soppressione secondaria, gruppo per gruppo
    groups = {}
    for c in cells:
        if c["dim"] != "totale":
            groups.setdefault((c["period"], c["macro"], c["dim"]), []).append(c)
    for group in groups.values():
        hidden = [c for c in group if c["_hidden"]]
        visible = sorted((c for c in group if not c["_hidden"]), key=lambda c: c["n_jobs"])
        if len(hidden) == 1 and visible:
            visible[0]["_hidden"] = True

    public = []
    for c in cells:
        if c.pop("_hidden"):
            continue
        for k in ("top_share", "n_ral_companies", "ral_top_share"):   # servono solo al controllo
            c.pop(k)
        public.append(c)
    return public
