"""
Nomi di azienda -> identificativi, slug candidati per gli ATS, verifica del nome negli annunci.
"""

import re
from urllib.parse import urlparse

from lib.text import normalize_text

# Parole che negli slug spesso mancano o compaiono come suffisso
LEGAL_WORDS = {
    "spa", "srl", "sb", "sa", "ag", "se", "nv", "inc", "ltd", "co",
    "group", "gruppo", "italia", "italy", "holding", "the", "company",
}

# Parole troppo comuni per dire "questo annuncio è di quell'azienda"
GENERIC_WORDS = LEGAL_WORDS | {
    "banca", "bank", "banco", "assicurazioni", "insurance", "energia", "energy",
    "international", "servizi", "services", "italiana", "italiano", "di", "del",
    "della", "dei", "and", "e", "per", "popolare", "credito", "consulting",
    "partners", "digital", "media", "tech", "technologies", "solutions",
    "systems", "global", "world", "fashion", "milano", "roma", "torino",
}


def name_tokens(name: str) -> list[str]:
    """'acme S.p.A.' -> ['acme', 'spa']  (s p a viene ricomposto)."""
    text = normalize_text(name)
    text = re.sub(r"\bs p a\b", "spa", text)
    text = re.sub(r"\bs r l\b", "srl", text)
    return text.split()


def company_id_from_name(name: str) -> str:
    """Identificativo stabile: 'examplegroup' -> 'examplegroup'."""
    return "-".join(name_tokens(name))


def core_tokens(name: str) -> list[str]:
    """Nome senza forma giuridica e suffissi tipo group/italia."""
    words = name_tokens(name)
    return [w for w in words if w not in LEGAL_WORDS] or words


def domain_root(website: str) -> str | None:
    """'https://group.examplegroup.com/it' -> 'examplegroup'."""
    if not website:
        return None
    if not website.startswith("http"):
        website = "https://" + website
    labels = urlparse(website).netloc.lower().split(".")
    labels = [l for l in labels if l and l != "www"]
    if len(labels) < 2:
        return None
    return labels[-2].replace("-", "")


def candidate_slugs(name: str, website: str = None, max_n: int = 8) -> list[str]:
    """Slug da provare sugli ATS, dal più probabile al meno probabile."""
    words = name_tokens(name)
    core = core_tokens(name)
    joined, hyphen = "".join(core), "-".join(core)

    candidates = [joined, hyphen, domain_root(website), "".join(words), "-".join(words)]
    for suffix in ("italia", "spa", "srl", "group"):
        candidates += [joined + suffix, f"{hyphen}-{suffix}"]

    unique = []
    for c in candidates:
        if c and len(c) >= 2 and c not in unique:
            unique.append(c)
    return unique[:max_n]


def workday_tenants(name: str, website: str = None, max_n: int = 6) -> list[str]:
    """Tenant Workday candidati, dal più probabile.

    Esempi reali: examplecorp, examplecorp, examplebank, tenantd, tenante.
    """
    core = core_tokens(name)
    joined = "".join(core)
    root = domain_root(website)
    first = core[0] if core and len(core[0]) >= 4 and core[0] not in GENERIC_WORDS else None
    tenants = [joined, root, joined + "group", (root or joined) + "group", joined + "company",
               first, joined + "spa", joined + "italia"]
    unique = []
    for t in tenants:
        if t and len(t) >= 2 and t not in unique:
            unique.append(t)
    return unique[:max_n]


def name_variants(name: str, website: str = None) -> tuple[set, set]:
    """Varianti 'forti' (nome intero, dominio) e 'deboli' (singole parole non generiche)."""
    core = core_tokens(name)
    full = " ".join(core)
    squeezed = full.replace(" ", "")
    strong, weak = set(), set()
    if len(squeezed) >= 6:
        strong.update({full, squeezed})
    root = domain_root(website)
    if root and len(root) >= 6:
        strong.add(root)
    weak.update(w for w in core if len(w) >= 3 and w not in GENERIC_WORDS)
    if len(squeezed) < 6:
        weak.add(full)
    return strong, weak


def name_match(name: str, website: str, texts: list) -> str | None:
    """Il nome dell'azienda compare nei testi degli annunci?

    'strong' = nome intero o dominio; 'weak' = solo una parola del nome; None = assente.
    Non passare qui gli URL che contengono lo slug provato, altrimenti il controllo è circolare.
    """
    blob = normalize_text(" ".join(t for t in texts if t))
    squeezed = blob.replace(" ", "")
    strong, weak = name_variants(name, website)
    for v in strong:
        if re.search(rf"\b{re.escape(v)}\b", blob) or (len(v) >= 8 and " " not in v and v in squeezed):
            return "strong"
    for v in weak:
        if re.search(rf"\b{re.escape(v)}\b", blob):
            return "weak"
    return None
