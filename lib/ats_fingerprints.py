"""
Impronte degli ATS: pattern da cercare in HTML, iframe, script e URL di rete delle pagine carriere.

Per ogni impronta trovata si salva: ats, slug (o URL per Workday), istanza us/eu, se è supportato
dal crawler, quante volte compare e un pezzo di testo come prova.
"""

import re
from collections import Counter
from urllib.parse import unquote

from lib.ats_fetchers import parse_workday_url, workday_board_url

# Pezzi di URL che non sono slug di aziende
NOT_SLUGS = {
    "www", "app", "api", "apply", "assets", "cdn", "static", "careers", "career", "jobs",
    "job", "help", "support", "status", "embed", "v0", "v1", "js", "css", "img", "images",
    "favicon", "boards", "job-boards", "login", "signin", "widget", "widgets", "media",
    "files", "eu", "us", "en", "it", "de", "fr", "privacy", "terms", "developers", "docs",
    "posting-api", "job-board", "scripts", "fonts", "cookies", "blog", "j",
    "analytics", "tracking", "metrics", "cdn-assets", "inrecruiting", "tt", "zinrec",   # sottodomini tecnici condivisi (es. analytics.altamirahrm.com)
}

# (ats, supportato dal crawler, regex). Gruppo "slug" = identificativo; gruppo "eu" = istanza europea.
FINGERPRINTS = [
    # --- supportati ---
    ("greenhouse", True,
     r"(?:job-)?boards(?:-api)?(?P<eu>\.eu)?\.greenhouse\.io/"
     r"(?:v1/boards/|embed/job_(?:board|app)(?:/js)?\?(?:[^\"'\s<>]*?&(?:amp;)?)?for=)?(?P<slug>[\w-]+)"),
    ("lever", True, r"(?:jobs|api)(?P<eu>\.eu)?\.lever\.co/(?:v0/postings/)?(?P<slug>[\w.-]+)"),
    ("ashby", True, r"(?:jobs|api)\.ashbyhq\.com/(?:posting-api/job-board/)?(?P<slug>[\w.%-]+)"),
    # Workday è gestito a parte (WORKDAY_RE) perché serve l'URL completo

    # --- non supportati (li rileviamo per misurare la copertura) ---
    # (workable, recruitee e personio sono supportati: API/feed pubblici, vedi lib/ats_fetchers.py)
    ("smartrecruiters", False, r"(?:careers|jobs)\.smartrecruiters\.com/(?P<slug>[\w-]+)"),
    ("smartrecruiters", False, r"api\.smartrecruiters\.com/v1/companies/(?P<slug>[\w-]+)"),
    ("workable", True, r"apply\.workable\.com/(?P<slug>[\w-]+)"),
    ("workable", True, r"(?P<slug>[\w-]+)\.workable\.com"),
    ("recruitee", True, r"(?P<slug>[\w-]+)\.recruitee\.com"),
    ("teamtailor", True, r"(?P<slug>[\w-]+)\.teamtailor\.com"),
    ("teamtailor", True, r"teamtailor(?:-cdn)?\.com"),   # anche senza slug aziendale
    ("personio", True, r"(?P<slug>[\w-]+)\.jobs\.personio\.(?:de|com)"),
    ("successfactors", True, r"successfactors\.(?:com|eu)/[^\"'\s<>]*?company=(?P<slug>[\w-]+)"),
    # jobs2web: il sottodominio (es. rmk-map-12) è un server condiviso, non l'azienda
    ("successfactors", True, r"[\w-]+\.jobs2web\.com"),
    ("successfactors", True, r"rmkcdn\.successfactors\.com"),
    # Oracle: anche il numero del sito, se c'è (…/CandidateExperience/it/sites/CX_1)
    ("oracle", True, r"(?P<slug>[\w-]+\.fa\.[\w-]+\.oraclecloud\.com/hcmUI/CandidateExperience"
                     r"(?:/[a-z]{2}(?:-[A-Z]{2})?)?(?:/sites/[\w-]+)?)"),
    ("taleo", False, r"(?P<slug>[\w-]+)\.taleo\.net"),
    ("icims", False, r"(?P<slug>[\w-]+)\.icims\.com"),
    ("avature", False, r"(?P<slug>[\w-]+)\.avature\.net"),
    # Avature su dominio proprio (es. jobs.companyc.com/en_US/careers/JobOpenings): si riconosce dal percorso
    ("avature", False, r"//(?P<slug>[\w.-]+\.[a-z]{2,})/[a-z]{2}_[A-Z]{2}/careers/(?:JobOpenings|SearchJobs|JobDetail|Home)"),
    ("inrecruiting", False, r"(?P<slug>[\w-]+)\.(?:intervieweb\.it|inrecruiting\.com)"),
    ("inrecruiting", False, r"(?:intervieweb\.it|inrecruiting\.com)"),   # anche senza slug aziendale
    ("altamira", False, r"(?P<slug>[\w-]+)\.altamirahrm\.com"),
    ("altamira", False, r"altamirahrm\.com"),     # ATS riconosciuto anche se il sottodominio non è lo slug
    ("jobvite", False, r"jobs\.jobvite\.com/(?P<slug>[\w-]+)"),
    ("eightfold", False, r"(?P<slug>[\w-]+)\.eightfold\.ai"),
    ("phenom", False, r"phenompeople\.com"),
    ("cornerstone", False, r"(?P<slug>[\w-]+)\.csod\.com"),
    ("factorial", False, r"(?P<slug>[\w-]+)\.factorialhr\.(?:com|it|es)"),
]
COMPILED = [(ats, supported, re.compile(rx, re.IGNORECASE)) for ats, supported, rx in FINGERPRINTS]

WORKDAY_RE = re.compile(
    r"(?:[\w-]+\.wd\d+\.myworkdayjobs\.com|wd\d+\.myworkdaysite\.com)(?:/[^\s\"'<>?#\\]*)?",
    re.IGNORECASE
)

SUPPORTED_ATS = {"greenhouse", "lever", "ashby", "workday", "workable", "personio", "recruitee",
                 "oracle", "teamtailor", "successfactors"}
# ATS supportati solo in parte: se il sito non espone il feed (es. SuccessFactors senza sitemap),
# l'azienda resta registrata come "non supportata" invece che "da rivedere"
PARTIAL_ATS = {"successfactors"}


def find_fingerprints(text: str) -> list[dict]:
    """Tutte le impronte ATS nel testo, ordinate: prima i supportati, poi le più frequenti."""
    # URL dentro JSON/script: "https://x.intervieweb.it" o "https:\/\/..." -> "/"
    text = text.replace("\\u002F", "/").replace("\\u002f", "/").replace("\\/", "/")
    # URL codificati dentro altri URL (es. ?redirect=https%3A%2F%2Fx.intervieweb.it) e entità HTML
    text = unquote(text).replace("&#x2F;", "/")
    counts = Counter()
    evidence = {}

    def add(key, snippet):
        counts[key] += 1
        evidence.setdefault(key, snippet[:150])

    for ats, supported, rx in COMPILED:
        for m in rx.finditer(text):
            slug = m.groupdict().get("slug")
            if slug:
                slug = unquote(slug).strip(".-")
                if slug.lower() in NOT_SLUGS:
                    continue
            instance = None
            if ats in ("greenhouse", "lever"):
                instance = "eu" if m.groupdict().get("eu") else "us"
            add((ats, slug or None, instance, supported), m.group(0))

    for m in WORKDAY_RE.finditer(text):
        host, tenant, site = parse_workday_url(m.group(0))
        if not tenant:
            continue
        url = workday_board_url(host, tenant, site) if site else f"https://{host}"
        add(("workday", url, None, True), m.group(0))

    results = []
    for (ats, slug, instance, supported), n in counts.items():
        results.append({"ats": ats, "slug_or_url": slug, "instance": instance,
                        "supported": supported, "count": n,
                        "evidence": evidence[(ats, slug, instance, supported)]})
    # Workday: un URL con il nome del sito vale più del solo dominio
    has_slug = lambda r: r["slug_or_url"] is not None and r["slug_or_url"].count("/") != 2
    results.sort(key=lambda r: (r["supported"], has_slug(r), r["count"]), reverse=True)
    return results
