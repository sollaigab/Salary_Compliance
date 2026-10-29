"""
Job ad enrichment: the fields used by the web app filters and by the analysis.

Everything uses simple rules (regexes on title, department, contract type and text) that are easy
to read and edit by hand. Rule order matters: the first rule that matches wins.
"""

import re
from datetime import date

from lib.salary import MONTHS_BY_COUNTRY, annualize, find_salary, is_vague, parse_salary
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


def _merge_rules(rules: list, extra: dict) -> list:
    """Adds words in other languages to the first rule with the same label (the order of the rules stays)."""
    out, done = [], set()
    for label, pattern in rules:
        if label in extra and label not in done:
            pattern = rf"(?:{pattern}|\b(?:{extra[label]})\b)"
            done.add(label)
        out.append((label, pattern))
    return out

# German, French and Dutch titles (normalized: no accents, umlauts dropped: Geschäftsführer -> geschaftsfuhrer)
SENIORITY_RULES = _merge_rules(SENIORITY_RULES, {
    "intern": r"praktikum\w*|praktikant\w*|werkstudent\w*|stagiaire|stagiair|abschlussarbeit|masterarbeit"
              r"|bachelorarbeit",
    "executive": r"geschaftsfuhrer\w*|vorstand\w*|directeur general|directrice generale|pdg|algemeen directeur",
    "director": r"direktor\w*|bereichsleit\w*|directeur|directrice|directie",
    "manager": r"leiter\w*|leitung|teamleit\w*|abteilungsleit\w*|gruppenleit\w*|responsable|chef de (?!partie|cuisine|rang)\w+"
               r"|teamleider|afdelingshoofd|leidinggevende",
    "senior": r"erfahren\w*|confirme\w*",
    "junior": r"berufseinsteig\w*|einsteiger\w*|debutant\w*|jeune diplome\w*|starter",
})

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
                   r"|audioprotesist\w*|audiolog\w*|fisioterap\w*|ostetric\w*|odontoiatr\w*|psichiatr\w*)\b"),
    ("hospitality", r"\b(?:barist\w*|bar|cuoc\w*|chef(?! de (?:projet|produit|mission|service|equipe|groupe|marche|secteur|file|chantier|bureau))|commis|camerier\w*|pasticcer\w*|panettier\w*|pizzaiol\w*"
                    r"|sommelier|ristorazion\w*|gastronomia|reception\w*|housekeeping|hotel\w*|hospitality)\b"),
    ("insurance", r"\b(?:claims?|sinistr\w*|liquidator\w*|underwrit\w*|attuar\w*|actuar\w*|riassicur\w*|polizz\w*)\b"),
    ("data", r"\b(?:data|databricks|analytics|business intelligence|bi|machine learning|ml|ai|statistic\w*|data scien\w*)\b"),
    ("engineering", r"\b(?:engineer\w*|engeenier\w*|developer|sviluppat\w*|software|devops|sre|backend|frontend"
                    r"|full ?stack|programmat\w*|sistemist\w*|cloud|cyber\w*|security|it specialist|ict|qa|tester"
                    r"|mobile|soc analyst|incident respon\w*|application support|it support|it application"
                    r"|administrator|sap|erp|as400|system specialist|technology specialist|network|bim|devsecops"
                    r"|progettist\w*|architect|ingegner\w*|configuration management|acoustic\w*|methodist"
                    r"|technical office|ufficio tecnico|r d|ricerca e sviluppo|interoperability|avionic\w*|geometra"
                    r"|salesforce|release|deployment)\b"),
    ("project_management", r"\b(?:project manager|program manager|programme manager|pmo|project management"
                           r"|project coordinator|project specialist|project engineer|projects?)\b"),
    ("product", r"\b(?:product manager|product owner|product)\b"),
    ("design", r"\b(?:design\w*|ux|ui|grafic\w*|creative|art director)\b"),
    ("retail", r"\b(?:store|negozio|addett\w*(?: [aeio])? (?:alla |al )?vendit\w*|sales assistant|cassier\w*|scaffal\w*|banconist\w*"
               r"|commess\w*|retail|boutique|client advisor|visual merchandis\w*|allievo(?: a)? responsabile"
               r"|reparto|punto vendita|supermercat\w*|ipermercat\w*)\b"),
    ("sales", r"\b(?:sales|commercial\w*|account|venditor\w*|business developer|business development|key account"
              r"|agente|consulente finanziari\w*|private banker|relationship manager|venditric\w*|deal maker"
              r"|gestore (?:imprese|small business|famiglie|privati|aziende|corporate|affluent|personal))\b"),
    ("marketing", r"\b(?:marketing|brand|communication\w*|comunicazion\w*|seo|sem|content|social media|pr|crm|digital|ppc|martech"
                  r"|press office|ufficio stampa|media relations|influencer\w*)\b"),
    ("finance", r"\b(?:financ\w*|finanz\w*|accounting|accountant|contabil\w*|controller|controlling|tesorer\w*"
                r"|treasury|audit\w*|tax|fiscal\w*|credit\w*|risk|rischi|bilancio|amministrativ\w*|pricing"
                r"|budget\w*|reporting|investor relations|m&a)\b"),
    ("hr", r"\b(?:hr|human resources|risorse umane|recruit\w*|talent acquisition|payroll|people"
           r"|amministrazione del personale|talent management)\b"),
    ("legal", r"\b(?:legal|legale|avvocat\w*|lawyer|counsel|compliance|privacy|antiriciclaggio|aml|gare|appalt\w*"
              r"|subappalt\w*|contratti|contracts?|permitting|regulatory|affari regolatori|public affairs"
              r"|bid|bidder|bid management|tender\w*|proposal manager|institutional affairs)\b"),
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
                   r"|cantier\w*|packaging|distribuzion\w*|supervisor|capo turno|purchase|sicurezza fisica|hse"
                   r"|giuntist\w*|conducent\w*|capo squadra|metrolog\w*|facilit\w*|assemblagg\w*|montagg\w*)\b"),
    ("consulting", r"\b(?:consultant|consulente|consulting|advisory|business analyst)\b"),
    ("healthcare", r"\b(?:medic\w*|infermier\w*|psicolog\w*|psicoterapeut\w*|farmacist\w*|nurse|clinical|clinic\w*)\b"),
    # broad words, only when no rule above matched (a "Change Management Consultant" stays in consulting)
    ("strategy", r"\b(?:transformation|change management|innovation|economic research|market intelligence"
                 r"|sustainability|esg)\b"),
    ("hr", r"\b(?:training)\b"),
]

FUNCTION_RULES = _merge_rules(FUNCTION_RULES, {
    "healthcare": r"arzt|arztin|pflege\w*|krankenpfleg\w*|apotheker\w*|medecin\w*|pharmacien\w*|aide soignant\w*"
                  r"|verpleegkundig\w*|verzorgend\w*",
    "hospitality": r"koch|kochin|kellner\w*|cuisinier\w*|serveur\w*|serveuse\w*|kok|bediening\w*|horeca",
    "engineering": r"entwickler\w*|softwareentwickl\w*|ingenieur\w*|informatik\w*|developpeur\w*|informaticien\w*"
                   r"|ontwikkelaar\w*",
    "project_management": r"projektleit\w*|projektmanag\w*|chef de projet|cheffe de projet|projectleider\w*",
    "retail": r"verkaufer\w*|kassierer\w*|filialleit\w*|filialmitarbeit\w*|vendeur\w*|vendeuse\w*|caissier\w*"
              r"|employe\w* de magasin|conseiller\w* de vente|verkoopmedewerker\w*|kassamedewerker\w*"
              r"|winkelmedewerker\w*|filiaalmanager\w*|bedrijfsleider",
    "sales": r"vertrieb\w*|aussendienst\w*|innendienst\w*|vente\w*|charge d affaires|verkoop\w*|accountmanager\w*",
    "marketing": r"kommunikation\w*|presse\w*|communicatie\w*",
    "finance": r"buchhalt\w*|rechnungswesen|steuer\w*|comptab\w*|controle de gestion|controleur de gestion|tresorerie"
               r"|boekhoud\w*|financieel|financiele",
    "hr": r"personalreferent\w*|personalwesen|personalabteilung|personalsachbearbeit\w*|personalentwickl\w*"
          r"|ressources humaines|recrutement|recruteur\w*|personeelszaken|werving",
    "legal": r"rechtsanwalt\w*|syndikus\w*|jurist\w*|juriste\w*|juridique|bedrijfsjurist\w*",
    "admin": r"sachbearbeit\w*|\w*assistenz|sekretar\w*|secretaire\w*|assistante?s? de direction"
             r"|secretaresse\w*|administratief medewerker\w*|officemanager\w*",
    "customer_service": r"kundenservice|kundenbetreuung|kundendienst|service client\w*|relation client\w*"
                        r"|klantenservice|klantcontact\w*",
    "operations": r"lager\w*|logistik\w*|produktion\w*|production|fertigung\w*|monteur\w*|mechatronik\w*"
                  r"|techniker\w*|instandhaltung|qualitat\w*|einkauf\w*|fahrer\w*|preparateur\w*|cariste\w*"
                  r"|magasinier\w*|technicien\w*|achat\w*|acheteur\w*|logistique|chauffeur\w*|magazijn\w*"
                  r"|logistiek\w*|inkoop\w*|productie\w*|onderhoud\w*",
    "consulting": r"berater\w*|unternehmensberat\w*|adviseur\w*",
})


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

# "unbefristet" (permanent) is not matched by "befristet": \b needs a non-letter before it
CONTRACT_RULES = _merge_rules(CONTRACT_RULES, {
    "internship": r"praktikum (?:im|in|als|bereich)|pflichtpraktikum|praktikant\w*|werkstudent\w*|stagiaire|stagiair\w*",
    "apprenticeship": r"(?<!abgeschlossene )(?<!abgeschlossener )ausbildung (?:zum|zur)|^ausbildung\b|ausbildungsplatz"
                      r"|auszubildend\w*|azubi\w*|duales studium|alternance|alternant\w*"
                      r"|apprentissage|contrat de professionnalisation|leerwerktraject|bbl",
    "fixed_term": r"befristet\w*|elternzeitvertretung|(?:en|un|contrat|poste en) cdd|cdd (?:de )?\d+ mois"
                  r"|contrat a duree determinee|tijdelijk\w*|bepaalde tijd"
                  r"|jaarcontract",
    "permanent": r"unbefristet\w*|festanstellung|(?:en|un|contrat|poste en) cdi|contrat a duree indeterminee|vast contract|vaste aanstelling"
                 r"|onbepaalde tijd",
    "freelance": r"freiberuf\w*|auto entrepreneur|freelancer|zzp\w*",
    "agency": r"arbeitnehmeruberlassung|zeitarbeit|mission d interim|travail temporaire|uitzend\w*",
})


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
        if re.search(r"\b(?:part ?time|parttime|tempo parziale|teilzeit|temps partiel|deeltijd)\b", t):
            return "part_time"
        if re.search(r"\b(?:full ?time|fulltime|tempo pieno|vollzeit|temps plein|voltijd)\b", t):
            return "full_time"
    return None


WORKPLACE_VALUES = [
    ("remote", r"full ?remote|fully remote|100 remote|remote|da remoto|telelavoro|telearbeit|teletravail|thuiswerken"),
    ("hybrid", r"hybrid|ibrid\w*|mista|flessibile|hybride"),
    ("onsite", r"on ?site|in sede|in presenza|in ufficio|in office|presso (?:la sede|gli uffici)|vor ort|sur site"
               r"|presentiel|op locatie|op kantoor"),
]

# 1) a label followed by its value: "Modalità di lavoro: ibrida", "Hybrid working: Onsite", "Work mode: on site"
WORKPLACE_LABEL_RE = re.compile(
    r"\b(?:modalita(?: e sede)?(?: di lavoro)?|tipologia di lavoro|work(?:ing)? (?:mode|model|arrangement|type|style)"
    r"|hybrid working|workplace(?: type)?|location type|remote status"
    r"|arbeitsmodell|arbeitsform|mode de travail|modalite de travail|werkmodel|werkvorm)\s+"
    r"(full ?remote|fully remote|remote|da remoto|hybrid|ibrid\w*|hybride|on ?site|in sede|in presenza|in ufficio"
    r"|in office|vor ort|sur site|presentiel|op locatie|op kantoor)\b")

# 2) full remote, stated as such
REMOTE_RE = re.compile(r"\b(?:full ?remote|fully remote|100 (?:da )?remot\w*|completamente da remoto|remote first"
                       r"|komplett remote|vollstandig remote|100 teletravail|teletravail (?:a )?100|full teletravail"
                       r"|volledig remote|volledig thuis\w*"
                       r"|work from anywhere|lavoro da remoto al 100)\b")

# 3) part of the time remote: hybrid ("smart working fino a 8 giorni al mese", "6 days per month remotely")
HYBRID_RE = re.compile(
    r"\b(?:hybrid (?:work\w*|model|role|position|setup|schedule|mode|arrangement|policy)"
    r"|(?:modalita|formula|modello|lavoro|formule di lavoro) ibrid\w*|in modalita ibrida"
    r"|smart ?working|lavoro agile"
    r"|\d+ (?:days?|giorn\w*) (?:a|per|al|alla|di|in) (?:week|month|settimana|mese)\w* (?:\w+ )?(?:remot\w*|da remoto|from home|in smart)"
    r"|(?:giornate|giorni) da remoto|lavorare da remoto|remote (?:work|working)(?: options| flexibility| days)?"
    r"|flexible working (?:arrangements|model|approach)|remote and on ?site days"
    r"|hybrides arbeiten|mobiles arbeiten|mobile arbeit|homeoffice|home office|teletravail|travail hybride"
    r"|hybride werken|thuiswerken|deels thuis\w*)\b")

# 4) on site: explicit only ("in sede di intervista", "in presenza di esperienza" and "onsite canteen" are not)
ONSITE_RE = re.compile(
    r"\b(?:(?:lavoro|attivita|presenza) (?:in sede|in presenza)|100 (?:in sede|on ?site|in presenza)"
    r"|full ?time (?:in office|on ?site|in sede)|on ?site (?:role|position|presence|work\w*|full time)"
    r"|office based|in presenza presso|(?:lavoro|turni) (?:su )?turni|turni (?:rotativi|h24|a ciclo|avvicendati))\b")


# 5) jobs that can only be done in person (stores, kitchens, plants, networks, clinics), read from the TITLE.
# Used only when the ATS and the description say nothing: source 'role', so it stays separate from what the ad states.
ONSITE_ROLE_RE = re.compile(
    r"\b(?:camerier\w*|waiter|waitress|barista|barman|bartender|lavapiatti|cuoc[oa]|cuochi|chef(?! de (?:projet|produit|mission|service|equipe|groupe|marche|secteur|file|chantier|bureau))|pizzaiol\w*"
    r"|aiuto cuoco|commis|receptionist|housekeep\w*|governant\w*|portier\w*|facchin\w*"
    r"|addett\w* (?:\w+ ){0,2}(?:vendita|cassa|casse|banco|scaffal\w*|magazzino|reparto|produzione|linea"
    r"|manutenzione|distribuzione|lavorazione|impiant\w*|sala|cucina|pulizi\w*|rifornimento|carico)"
    r"|commess[oi]|cassier\w*|sales assistant|store (?:manager|assistant)|shop assistant|floor manager"
    r"|operai\w*|operaio|operator\w* (?:operatric\w* )?(?:\w+ ){0,2}(?:impiant\w*|produzione|macchin\w*|reti|linea"
    r"|ecologic\w*|servizi ambientali|raccolta|cernita|movimentazione|spazzamento)"
    r"|production operator|machine operator|warehouse (?:operator|worker|specialist|associate)"
    r"|magazzinier\w*|carrellist\w*|mulettist\w*|manutentor\w*|giuntist\w*|elettricist\w*|saldator\w*"
    r"|tornitor\w*|fresator\w*|idraulico|conducent\w*|autist\w*|driver|rider|capoturno|capo turno|capo reparto"
    r"|plant manager|tecnic\w* (?:\w+ ){0,2}(?:di rete|reparto|di campo|manutenzione|laboratorio)"
    r"|tecnic\w* (?:\w+ )?impiantist\w*|field (?:service )?(?:technician|engineer|representative)"
    r"|medic[oi]|infermier\w*|nurse|oss|operatore socio sanitario|fisioterapist\w*|farmacist\w*|audioprotesist\w*"
    r"|ottic[oi] optometrist\w*|optometrist\w*|tecnic\w* di radiologia|ostetric\w*|veterinar\w*"
    r"|pilot\w*|assistente di volo|flight attendant|cabin crew|capotreno|macchinist\w*"
    r"|tailor|sart[oa]|sarte|indossat\w*|fit model|guardia giurata|vigilant\w*"
    # German, French, Dutch
    r"|verkaufer\w*|kassierer\w*|filialmitarbeit\w*|lagermitarbeit\w*|lagerist\w*|kommissionier\w*"
    r"|staplerfahrer\w*|koch|kochin|kellner\w*|servicekraft|monteur\w*|mechatroniker\w*|elektroniker\w*"
    r"|schlosser\w*|schweisser\w*|produktionsmitarbeit\w*|maschinenfuhrer\w*|anlagenfuhrer\w*|pflegefachkraft\w*"
    r"|pflegekraft\w*|krankenpfleg\w*|altenpfleg\w*|arzt|arztin|apotheker\w*|berufskraftfahrer\w*|lkw fahrer\w*"
    r"|vendeur\w*|vendeuse\w*|caissier\w*|employe\w* de magasin|preparat\w* de commandes|cariste\w*|magasinier\w*"
    r"|cuisinier\w*|serveur\w*|serveuse\w*|technicien\w* de maintenance|electricien\w*|soudeur\w*"
    r"|operat\w* de production|conducteur\w* de ligne|infirmier\w*|aide soignant\w*|pharmacien\w*"
    r"|verkoopmedewerker\w*|kassamedewerker\w*|winkelmedewerker\w*|magazijnmedewerker\w*|orderpicker\w*"
    r"|heftruckchauffeur\w*|kok|bedieningsmedewerker\w*|productiemedewerker\w*|verpleegkundig\w*"
    r"|verzorgend\w*|vrachtwagenchauffeur\w*)\b")


def _workplace_value(text: str) -> str | None:
    for label, pattern in WORKPLACE_VALUES:
        if re.fullmatch(pattern, text):
            return label
    return None


def workplace_with_source(ats_value: str, location: str, description: str,
                          title: str = "") -> tuple[str | None, str | None]:
    """(onsite / hybrid / remote, source). The ATS field wins; then the description: an explicit label,
    then full remote, hybrid and on-site wording; last, a title that can only be done in person.
    source: 'ats' / 'text' / 'role' / None."""
    v = normalize_text(ats_value)
    if re.search(r"remote|remoto", v) and "hybrid" not in v:
        return "remote", "ats"
    if re.search(r"hybrid|ibrid", v):
        return "hybrid", "ats"
    if re.search(r"on ?site|in office|onsite", v):
        return "onsite", "ats"
    text = normalize_text(f"{location} {description or ''}")
    m = WORKPLACE_LABEL_RE.search(text)
    if m and _workplace_value(m.group(1)):
        return _workplace_value(m.group(1)), "text"
    if REMOTE_RE.search(text):
        return "remote", "text"
    if HYBRID_RE.search(text):
        return "hybrid", "text"
    if ONSITE_RE.search(text):
        return "onsite", "text"
    if title and ONSITE_ROLE_RE.search(normalize_text(title)):
        return "onsite", "role"
    return None, None


def workplace_type(ats_value: str, location: str, description: str, title: str = "") -> str | None:
    return workplace_with_source(ats_value, location, description, title)[0]


# ---------------------------------------------------------------- language

# frequent words that belong to one language only (shared ones such as "la", "per", "de" are left out)
LANG_WORDS = {
    "it": {"il", "di", "che", "con", "una", "del", "della", "sono", "nel", "alla", "lavoro", "esperienza"},
    "en": {"the", "and", "of", "to", "with", "you", "our", "for", "we", "are", "your", "will", "experience"},
    "de": {"und", "der", "die", "das", "mit", "fur", "wir", "sie", "ist", "auf", "bei", "unser", "ihre", "erfahrung"},
    "fr": {"et", "les", "des", "pour", "avec", "vous", "nous", "une", "sur", "dans", "est", "votre", "experience"},
    "nl": {"en", "het", "van", "voor", "met", "je", "wij", "een", "zijn", "jouw", "bij", "ons", "ervaring"},
}


def description_lang(text: str) -> str | None:
    words = normalize_text((text or "")[:3000]).split()
    counts = {lang: sum(w in vocab for w in words) for lang, vocab in LANG_WORDS.items()}
    lang, best = max(counts.items(), key=lambda kv: kv[1])
    return lang if best >= 5 else None


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


def salary_fields(salary_structured, description: str, is_internship: bool = False, country: str = "IT") -> dict:
    """All pay fields of the jobs table. Monthly figures: x the monthly payments of the country
    (14 in Italy, 12 elsewhere); internships always 12."""
    months = 12 if is_internship else MONTHS_BY_COUNTRY.get(country or "IT", 12)
    structured = parse_structured_salary(salary_structured)
    from_text = parse_salary(description, country)
    chosen, source = (structured, "structured") if structured else (from_text, "text") if from_text else (None, "none")
    gross_net = from_text["gross_net"] if from_text and source == "text" else None
    vague = is_vague(description)
    # all observed countries use the euro: a figure in dollars or francs is the pay of another
    # location of the same role ("Salary Chicago: $160,000"), not of this job
    if chosen and chosen["currency"] in (None, "EUR"):
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
