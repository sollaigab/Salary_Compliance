"""
Protecting company identities in the public data (statistical disclosure control).

The public data contains ONLY aggregate cells. A cell is a filter combination (period, country, macro-sector,
job function, workplace type: each one a value or 'all') crossed with ONE dimension.
A cell is published if it passes all of these rules:

1. threshold:       at least MIN_JOBS job ads and MIN_COMPANIES distinct companies;
2. dominance:       no company holds more than MAX_SHARE of the cell's ads
                    (otherwise the cell would in practice describe one company);
3. salary medians:  only if the figures come from at least MIN_JOBS ads and MIN_COMPANIES companies,
                    with no company above MAX_SHARE;
4. differences:     a filtered cell and the same cell without that filter must not differ by a small
                    group: "Finance, Lombardia" minus "Finance, data roles, Lombardia" gives the
                    non-data ads, and that difference must pass rules 1 and 2 as well (or be empty);
5. secondary:       if exactly one cell in a group (same filters and dimension) is hidden,
                    the smallest visible one is hidden too: otherwise the hidden cell could
                    be worked out by subtracting from the total.
"""

MIN_JOBS = 5
MIN_COMPANIES = 3
MAX_SHARE = 0.70

# filter axes and the value meaning "no filter"; post_law ads are a subset of all ads, so the
# period is a filter too
AXES = {"period": "all", "country": "all", "macro": "all", "function": "all", "workplace": "all"}
# when a difference is too small one of the two cells goes: usually the filtered one, but for the
# period the post-law cell is the main result, so the cell over all ads is hidden instead
HIDE_PARENT_ON = {"period"}
CONTROL_FIELDS = ("top_share", "n_ral_companies", "ral_top_share", "_companies")


def cell_ok(n_jobs: int, n_companies: int, top_share: float) -> bool:
    return n_jobs >= MIN_JOBS and n_companies >= MIN_COMPANIES and top_share <= MAX_SHARE


def _key(c: dict, **override) -> tuple:
    return tuple(override.get(a, c.get(a, unfiltered)) for a, unfiltered in AXES.items()) + (c["dim"], c["value"])


def _difference_ok(child: dict, parent: dict) -> bool:
    """The ads in parent but not in child (per company) must be none, or a publishable group."""
    rest = {k: n - child["_companies"].get(k, 0) for k, n in parent["_companies"].items()}
    rest = {k: n for k, n in rest.items() if n > 0}
    total = sum(rest.values())
    return total == 0 or cell_ok(total, len(rest), max(rest.values()) / total)


def apply_rules(cells: list[dict]) -> list[dict]:
    """Returns only the publishable cells. Each cell has the keys:
    period, macro, dim, value (optionally function, workplace), n_jobs, n_companies, top_share,
    n_ral, n_ral_companies, ral_top_share, ral_min_median, ral_max_median (+ other metrics), and
    optionally _companies {company: n_jobs}, needed for rule 4."""
    for c in cells:
        c["_hidden"] = not cell_ok(c["n_jobs"], c["n_companies"], c["top_share"])
        if not cell_ok(c["n_ral"], c["n_ral_companies"], c["ral_top_share"]):
            c["ral_min_median"] = c["ral_max_median"] = None

    # differences between a filtered cell and the same cell with one filter removed
    by_key = {_key(c): c for c in cells if "_companies" in c}
    for c in by_key.values():
        if c["_hidden"]:
            continue
        for axis, unfiltered in AXES.items():
            if c.get(axis, unfiltered) == unfiltered:
                continue
            parent = by_key.get(_key(c, **{axis: unfiltered}))
            if parent and not parent["_hidden"] and not _difference_ok(c, parent):
                (parent if axis in HIDE_PARENT_ON else c)["_hidden"] = True
                if axis not in HIDE_PARENT_ON:
                    break

    # secondary suppression, group by group
    groups = {}
    for c in cells:
        if c["dim"] != "total":
            groups.setdefault(_key(c)[:-1], []).append(c)
    for group in groups.values():
        hidden = [c for c in group if c["_hidden"]]
        visible = sorted((c for c in group if not c["_hidden"]), key=lambda c: c["n_jobs"])
        if len(hidden) == 1 and visible:
            visible[0]["_hidden"] = True

    public = []
    for c in cells:
        if c.pop("_hidden"):
            continue
        for k in CONTROL_FIELDS:   # only needed for the checks
            c.pop(k, None)
        public.append(c)
    return public
