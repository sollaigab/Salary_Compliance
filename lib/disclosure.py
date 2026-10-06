"""
Protecting company identities in the public data (statistical disclosure control).

The public data contains ONLY aggregate cells (period x macro-sector x one dimension).
A cell is published if it passes all of these rules:

1. threshold:       at least MIN_JOBS job ads and MIN_COMPANIES distinct companies;
2. dominance:       no company holds more than MAX_SHARE of the cell's ads
                    (otherwise the cell would in practice describe one company);
3. salary medians:  only if the figures come from at least MIN_JOBS ads and MIN_COMPANIES companies,
                    with no company above MAX_SHARE;
4. secondary:       if exactly one cell in a group (same period, macro-sector and dimension) is
                    hidden, the smallest visible one is hidden too: otherwise the hidden cell could
                    be worked out by subtracting from the total.
"""

MIN_JOBS = 5
MIN_COMPANIES = 3
MAX_SHARE = 0.70


def cell_ok(n_jobs: int, n_companies: int, top_share: float) -> bool:
    return n_jobs >= MIN_JOBS and n_companies >= MIN_COMPANIES and top_share <= MAX_SHARE


def apply_rules(cells: list[dict]) -> list[dict]:
    """Returns only the publishable cells. Each cell has the keys:
    period, macro, dim, value, n_jobs, n_companies, top_share,
    n_ral, n_ral_companies, ral_top_share, ral_min_median, ral_max_median (+ other metrics)."""
    for c in cells:
        c["_hidden"] = not cell_ok(c["n_jobs"], c["n_companies"], c["top_share"])
        if not cell_ok(c["n_ral"], c["n_ral_companies"], c["ral_top_share"]):
            c["ral_min_median"] = c["ral_max_median"] = None

    # secondary suppression, group by group
    groups = {}
    for c in cells:
        if c["dim"] != "total":
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
        for k in ("top_share", "n_ral_companies", "ral_top_share"):   # only needed for the checks
            c.pop(k)
        public.append(c)
    return public
