"""
Job ad enrichment: the fields used by the web app filters and by the analysis.

Everything uses simple rules (regexes on title, department, contract type and text) that are easy
to read and edit by hand. Rule order matters: the first rule that matches wins.
"""

import re
from datetime import date

from lib.salary import annualize, find_salary, is_vague, parse_salary
from lib.text import normalize_text

# ---------------------------------------------------------------- privacy

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE_RE = re.compile(r"(?:\+|00)\d{2}[\s./-]?\d{2,4}[\s./-]?\d{3,4}[\s./-]?\d{2,4}|\b3\d{2}[\s.-]?\d{3}[\s.-]?\d{3,4}\b")


def scrub_personal_data(text: str) -> str:
    """Removes email addresses and phone numbers (often the recruiter's): they are never stored (GDPR)."""
    return PHONE_RE.sub("[phone]", EMAIL_RE.sub("[email]", text or ""))


# ---------------------------------------------------------------- open applications

# "Candidatura spontanea", "Talent community": always-open forms, not job ads with a position to fill
OPEN_APPLICATION_RE = re.compile(r"\b(?:candidatur\w* spontane\w*|autocandidatura|spontaneous applications?"
                                 r"|open applications?|talent (?:community|pool))\b")


def is_open_application(title: str) -> bool:
    return bool(OPEN_APPLICATION_RE.search(normalize_text(title)))


# ---------------------------------------------------------------- seniority

SENIORITY_RULES = [
    ("intern", r"\b(?:intern|internship|stage|stagista|tirocini\w*|trainee|curricular)\b"),
    # executives: only the actual role ("Chief Financial Officer", "CFO"), not "Chief Engineer"
    ("executive", r"\b(?:chief(?: \w+){1,3} officer|ceo|cfo|cto|coo|cio|cmo|ciso|chro|managing director"
                  r"|amministratore delegato|direttore generale|general manager)\b"),
    ("manager", r"\b(?:deputy|vice|assistant|assistente)(?: \w+)? (?:director|direttore)\b"),
    ("director", r"\b(?:director|direttore|head of|vp|vice president)\b"),
    ("manager", r"\b(?:manager|responsabile|coordinator\w*|coordinat\w*|supervisor|capo)\b"),
    ("lead", r"\b(?:lead|principal|staff engineer|team leader|tech lead|architect|architetto|chief engineer)\b"),
    ("senior", r"\b(?:senior|sr|esperto|experienced|expert)\b"),
    ("junior", r"\b(?:junior|jr|entry level|graduate|neolaureat\w*|apprendist\w*|apprentice|associate)\b"),
]

# practice or office names that contain an executive acronym but are not a role
# ("CFO Services - Senior Consultant", "CEO Office Analyst")
PRACTICE_RE = re.compile(r"\b(?:cfo|ceo|cio|cto|coo|cmo|chro)\s+(?:services?|advisory|office|agenda"
                         r"|transformation|practice|program\w*|solutions?)\b")
# explicit grade at the end of the title, after a dash ("... - Senior Consultant"): the most reliable signal
GRADE_ONLY_RE = re.compile(r"^(?:consultant|analyst|specialist|associate|consulente|analista|specialista)$")


def seniority(title: str) -> str:
    def classify(text):
        t = PRACTICE_RE.sub(" ", normalize_text(text))
        if GRADE_ONLY_RE.match(t.strip()):
            return "mid"
        for label, pattern in SENIORITY_RULES:
            if re.search(pattern, t):
                return label
        return None

    parts = re.split(r"\s[-–—|]\s", title or "")
    if len(parts) > 1:
        level = classify(parts[-1])
        if level:
            return level
    return classify(title or "") or "mid"


# ---------------------------------------------------------------- seniority from years of experience

_YEARS = r"(?:anni|anno|years?|year's|yrs)"
# a standalone number (not "15" inside "150"), optionally followed by the upper end of a range
_NUM_RANGE = r"(?<!\d)(\d{1,2})(?!\d)\s*(?:\+|-|–|\s+a\s+|\s+to\s+|\s+o\s+più|\s+or\s+more)?\s*(?:\d{1,2}(?!\d))?"
EXPERIENCE_RES = [
    # "3-5 anni di esperienza", "da 3 a 6 anni di esperienza", "4+ years of experience", "[4-10 years] of experience"
    re.compile(rf"\b{_NUM_RANGE}\]?\s*{_YEARS}\b[^.;\n]{{0,40}}?(?:esperienz\w*|experience|seniority)", re.I),
    # "esperienza di almeno 5 anni", "experience: 3-5 years", "esperienza superiore a 3 anni"
    re.compile(rf"(?:esperienz\w*|experience|seniority)[^.;\n\d]{{0,40}}?{_NUM_RANGE}\s*{_YEARS}\b", re.I),
    # "at least 3 years", "minimum of 4 years", "almeno 5 anni"
    re.compile(rf"(?:at least|minimum(?: of)?|almeno|minimo(?: di)?)\s*(\d{{1,2}})(?!\d)\s*\+?\s*{_YEARS}\b", re.I),
]
MAX_PLAUSIBLE_YEARS = 15     # "150 anni di esperienza" is company history, not a requirement


def experience_years(text: str) -> int | None:
    """Minimum years of experience required, if the text states them."""
    for rx in EXPERIENCE_RES:
        for m in rx.finditer(text or ""):
            years = int(m.group(1))
            if 0 < years <= MAX_PLAUSIBLE_YEARS:
                return years
    return None


def seniority_from_years(years: int | None) -> str | None:
    if years is None:
        return None
    if years < 2:
        return "junior"
    if years < 5:
        return "mid"
    return "senior"


def seniority_with_source(title: str, description: str) -> tuple[str, str, int | None]:
    """(level, source, years). The title wins; if it says nothing, the required years decide.
    source: 'title' / 'experience' / 'not_stated'."""
    years = experience_years(description)
    level = seniority(title)
    if level != "mid" or re.search(r"\b(?:mid|intermedi\w*|middle)\b", normalize_text(title)):
        return level, "title", years
    from_years = seniority_from_years(years)
    if from_years:
        return from_years, "experience", years
    return "mid", "not_stated", years


# ---------------------------------------------------------------- job function

FUNCTION_RULES = [
    # very recognizable trades before the generic rules
    ("healthcare", r"\b(?:medic\w*|infermier\w*|psicolog\w*|psicoterapeut\w*|farmacist\w*|nurse|clinical|clinic\w*"
                   r"|audioprotesist\w*|audiolog\w*|fisioterap\w*|ostetric\w*|odontoiatr\w*)\b"),
    ("hospitality", r"\b(?:barist\w*|bar|cuoc\w*|chef|commis|camerier\w*|pasticcer\w*|panettier\w*|pizzaiol\w*"
                    r"|sommelier|ristorazion\w*|gastronomia|reception\w*|housekeeping|hotel\w*|hospitality)\b"),
    ("insurance", r"\b(?:claims?|sinistr\w*|liquidator\w*|underwrit\w*|attuar\w*|actuar\w*|riassicur\w*|polizz\w*)\b"),
    ("data", r"\b(?:data|analytics|business intelligence|bi|machine learning|ml|ai|statistic\w*|data scien\w*)\b"),
    ("engineering", r"\b(?:engineer\w*|engeenier\w*|developer|sviluppat\w*|software|devops|sre|backend|frontend"
                    r"|full ?stack|programmat\w*|sistemist\w*|cloud|cyber\w*|security|it specialist|ict|qa|tester"
                    r"|mobile|soc analyst|incident respon\w*|application support|it support|it application"
                    r"|administrator|sap|erp|as400|system specialist|technology specialist|network|bim|devsecops"
                    r"|progettist\w*|architect|ingegner\w*|configuration management|acoustic\w*|methodist"
                    r"|technical office|ufficio tecnico|r d|ricerca e sviluppo|interoperability|avionic\w*)\b"),
    ("project_management", r"\b(?:project manager|program manager|programme manager|pmo|project management"
                           r"|project coordinator|project specialist|project engineer|projects?)\b"),
    ("product", r"\b(?:product manager|product owner|product)\b"),
    ("design", r"\b(?:design\w*|ux|ui|grafic\w*|creative|art director)\b"),
    ("retail", r"\b(?:store|negozio|addett\w*(?: a)? (?:alla |al )?vendit\w*|sales assistant|cassier\w*|scaffal\w*|banconist\w*"
               r"|commess\w*|retail|boutique|client advisor|visual merchandis\w*|allievo(?: a)? responsabile"
               r"|reparto|punto vendita|supermercat\w*|ipermercat\w*)\b"),
    ("sales", r"\b(?:sales|commercial\w*|account|venditor\w*|business developer|business development|key account"
              r"|agente|consulente finanziari\w*|private banker|relationship manager)\b"),
    ("marketing", r"\b(?:marketing|brand|communication\w*|comunicazion\w*|seo|sem|content|social media|pr|crm|digital)\b"),
    ("finance", r"\b(?:financ\w*|finanz\w*|accounting|accountant|contabil\w*|controller|controlling|tesorer\w*"
                r"|treasury|audit\w*|tax|fiscal\w*|credit\w*|risk|rischi|bilancio|amministrativ\w*|pricing"
                r"|budget\w*|reporting|investor relations|m&a)\b"),
    ("hr", r"\b(?:hr|human resources|risorse umane|recruit\w*|talent acquisition|payroll|people)\b"),
    ("legal", r"\b(?:legal|legale|avvocat\w*|lawyer|counsel|compliance|privacy|antiriciclaggio|aml|gare|appalt\w*"
              r"|subappalt\w*|contratti|contracts?|permitting|regulatory|affari regolatori|public affairs)\b"),
    ("strategy", r"\b(?:strateg\w*|corporate development|business planning|planning|pianificazion\w*"
                 r"|ceo office|business transformation|m a)\b"),
    ("admin", r"\b(?:segreteri\w*|segretari\w*|administration support|administrative assistant|office assistant"
              r"|executive assistant|assistant|assistente di direzione|back office|front office|data entry)\b"),
    ("customer_service", r"\b(?:customer|assistenza clienti|relazion\w* clienti|call center|contact center"
                         r"|help ?desk|service desk|client service)\b"),
    ("operations", r"\b(?:operation\w*|logistic\w*|supply chain|magazzin\w*|warehouse|produzion\w*|operai\w*"
                   r"|operator\w*|manufactur\w*|quality|qualita|maintenance|manutenzion\w*|manutentor\w*|tecnic\w*"
                   r"|technician|impiant\w*|plant|procurement|acquist\w*|buyer|autist\w*|driver|movimentazion\w*"
                   r"|rifiuti|contatori|misurator\w*|esercizio|conduzion\w*|teleriscaldament\w*|planner|sorveglianz\w*"
                   r"|vigilanz\w*|lavorazion\w*|carrellist\w*|saldator\w*|elettricist\w*|meccanic\w*|installator\w*"
                   r"|cantier\w*|packaging|distribuzion\w*|supervisor|capo turno|purchase|sicurezza fisica|hse)\b"),
    ("consulting", r"\b(?:consultant|consulente|consulting|advisory|business analyst)\b"),
    ("healthcare", r"\b(?:medic\w*|infermier\w*|psicolog\w*|psicoterapeut\w*|farmacist\w*|nurse|clinical|clinic\w*)\b"),
]


def job_function(title: str, department: str = None) -> str:
    for text in (title, department):
        t = normalize_text(text)
        if not t:
            continue
        for label, pattern in FUNCTION_RULES:
            if re.search(pattern, t):
                return label
    return "other"


# ---------------------------------------------------------------- contract, schedule, workplace

CONTRACT_RULES = [
    # not intern\w*: it would match "internazionale", "international", "interna"
    ("internship", r"\b(?:interns?|internships?|stage|stagista|tirocini\w*|traineeship)\b"),
    ("apprenticeship", r"\b(?:apprendist\w*|apprentice\w*)\b"),
    # "sostituzione" alone also means replacing parts ("sostituzione di tubazioni"): only staff cover
    ("fixed_term", r"\b(?:tempo determinato|fixed[ -]term|temporary|temporaneo|contratto a termine"
                    r"|(?:in|per|contratto di) sostituzione|sostituzione maternit\w*|maternity cover|seasonal"
                    r"|stagional\w*)\b"),
    ("permanent", r"\b(?:tempo indeterminato|permanent|regular|indeterminato|fulltime permanent)\b"),
    # "collaborazione" alone is teamwork ("in collaborazione con il team"): only the contract forms
    ("freelance", r"\b(?:freelance|partita iva|p iva|contractor|consulenza autonoma|co co co|cococo"
                  r"|(?:contratto|rapporto) di collaborazione|collaborazione (?:coordinata|occasionale))\b"),
    ("agency", r"\b(?:somministrazione|interinale|agency worker)\b"),
]


def contract_type(employment_type: str, title: str, description: str) -> str | None:
    # first the ATS structured field, then the title, then the start of the description
    for text in (employment_type, title, (description or "")[:3000]):
        t = normalize_text(text)
        if not t:
            continue
        for label, pattern in CONTRACT_RULES:
            if re.search(pattern, t):
                return label
    return None


def work_schedule(employment_type: str, title: str, description: str) -> str | None:
    for text in (employment_type, title, (description or "")[:3000]):
        t = normalize_text(text)
        if re.search(r"\b(?:part ?time|parttime|tempo parziale)\b", t):
            return "part_time"
        if re.search(r"\b(?:full ?time|fulltime|tempo pieno)\b", t):
            return "full_time"
    return None


def workplace_type(ats_value: str, location: str, description: str) -> str | None:
    v = normalize_text(ats_value)
    if re.search(r"remote|remoto", v) and "hybrid" not in v:
        return "remote"
    if re.search(r"hybrid|ibrid", v):
        return "hybrid"
    if re.search(r"on ?site|in office|onsite", v):
        return "onsite"
    text = normalize_text(f"{location} {(description or '')[:4000]}")
    if re.search(r"\b(?:full remote|fully remote|100 remote|completamente da remoto|remote first)\b", text):
        return "remote"
    if re.search(r"\b(?:hybrid|ibrid\w*|smart working|lavoro agile)\b", text):
        return "hybrid"
    return None


# ---------------------------------------------------------------- language

IT_WORDS = {"il", "la", "di", "che", "per", "con", "una", "del", "della", "sono", "nel", "alla", "lavoro", "esperienza"}
EN_WORDS = {"the", "and", "of", "to", "with", "you", "our", "for", "we", "are", "your", "will", "experience"}


def description_lang(text: str) -> str | None:
    words = normalize_text((text or "")[:3000]).split()
    it = sum(w in IT_WORDS for w in words)
    en = sum(w in EN_WORDS for w in words)
    if it + en < 5:
        return None
    return "it" if it >= en else "en"


# ---------------------------------------------------------------- pay

PERIODS = {
    "year": "year", "yearly": "year", "annual": "year", "1 year": "year", "per-year-salary": "year",
    "month": "month", "monthly": "month", "1 month": "month", "per-month-salary": "month",
    "hour": "hour", "hourly": "hour", "1 hour": "hour", "per-hour-wage": "hour",
}


def parse_structured_salary(obj):
    """Normalizes the structured pay of the various ATSs into {min, max, currency, period}.

    Lever: {min, max, currency, interval}; Recruitee: {min, max, currency, period};
    Ashby: summaryComponents list [{compensationType, interval, currencyCode, minValue, maxValue}]
           or a summary string ("€35K – €45K").
    """
    if not obj:
        return None
    if isinstance(obj, str):
        parsed = parse_salary(obj)
        return {k: parsed[k] for k in ("min", "max", "currency", "period")} if parsed else None
    if isinstance(obj, list):
        salary = next((c for c in obj if str(c.get("compensationType", "")).lower() in ("salary", "base salary")), None)
        if not salary:
            return None
        obj = {"min": salary.get("minValue"), "max": salary.get("maxValue"),
               "currency": salary.get("currencyCode"), "period": salary.get("interval")}
    low, high = obj.get("min"), obj.get("max")
    if low is None and high is None:
        return None
    period = str(obj.get("period") or obj.get("interval") or "").lower()
    return {"min": low if low is not None else high, "max": high if high is not None else low,
            "currency": (obj.get("currency") or None), "period": PERIODS.get(period, period or None)}


def salary_fields(salary_structured, description: str, is_internship: bool = False) -> dict:
    """All pay fields of the jobs table. For internships the annual salary uses 12 monthly payments."""
    months = 12 if is_internship else 14
    structured = parse_structured_salary(salary_structured)
    from_text = parse_salary(description)
    chosen, source = (structured, "structured") if structured else (from_text, "text") if from_text else (None, "none")
    gross_net = from_text["gross_net"] if from_text and source == "text" else None
    vague = is_vague(description)
    if chosen:
        transparency = "figure"
    elif vague:
        transparency = "vague"
    else:
        transparency = "none"
    return {
        "salary_source": source,
        "salary_min": chosen["min"] if chosen else None,
        "salary_max": chosen["max"] if chosen else None,
        "salary_currency": chosen["currency"] if chosen else None,
        "salary_period": chosen["period"] if chosen else None,
        "salary_gross_net": gross_net,
        "ral_min_annual": annualize(chosen["min"], chosen["period"], chosen["currency"], gross_net, months)
                          if chosen else None,
        "ral_max_annual": annualize(chosen["max"], chosen["period"], chosen["currency"], gross_net, months)
                          if chosen else None,
        "salary_text_matches": find_salary(description),
        "salary_vague": int(vague),
        "salary_transparency": transparency,
    }


# ---------------------------------------------------------------- period relative to the law

LAW_START = date(2026, 6, 7)     # D.Lgs. 96/2026: job ads must state the pay
HISTORIC_DAYS = 365              # ads online for over a year: almost always "evergreen" positions


def posted_date(posted_at) -> str | None:
    """Publication date as YYYY-MM-DD (the ATSs use different formats)."""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(posted_at or ""))
    return f"{m[1]}-{m[2]}-{m[3]}" if m else None


def posting_period(posted: str | None, observed: str) -> str | None:
    """post_law (from 7/6/2026), pre_law (earlier, but within 12 months of observation), old (older)."""
    if not posted:
        return None
    d = date.fromisoformat(posted)
    if d >= LAW_START:
        return "post_law"
    if (date.fromisoformat(observed[:10]) - d).days > HISTORIC_DAYS:
        return "old"
    return "pre_law"
