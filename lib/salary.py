"""
Extracting pay from the job ad text.

- find_salary(): the phrases that contain a figure
- VAGUE_SALARY_RE: wording that mentions pay without giving a figure
- parse_salary(): from free text to {min, max, currency, period, gross/net}
- annualize(): converts everything to gross annual salary in euro (monthly x the payments of the country)

The patterns match Italian, English, German, French and Dutch job ads, so they contain words in those
languages on purpose.
"""

import re

# Finds euro / RAL amounts in the text: "RAL 35.000", "€40k", "30.000 - 40.000 EUR", ...
# \b around RAL, otherwise "generale 2" would count as RAL.
_DIGITS = r"\d[\d.,]*(?:[ \u00a0\u202f]\d{3})*"
SALARY_RE = re.compile(
    r"(?:\bRAL\b|retribu\w*|salary|compenso|compensation|stipendio|\bpay\b|lordo annuo|annuo lordo"
    r"|\w*gehalt\w*|verg[uü]tung|salaire|r[ée]mun[ée]ration|salaris|\bloon\b)"
    rf"[^.\n]{{0,60}}?{_DIGITS}\s?(?:k|K|mila)?"
    rf"|(?:€|\bEUR\b)\s?{_DIGITS}\s?(?:k|K)?"
    rf"|{_DIGITS}\s?(?:k|K)?\s?(?:€|\bEUR\b|\beuro)",
    re.IGNORECASE,
)

# Wording that "talks about" pay without giving a figure (transparency index)
VAGUE_SALARY_RE = re.compile(
    r"commisurat\w* (?:all.|al |alle )(?:esperienz|competenz|profil|capacit)"
    r"|(?:retribuzione|pacchetto retributivo|trattamento economico|compenso)"
    r" (?:competitiv|adeguat|interessant|in linea|commisurat|di sicuro interesse)"
    r"|(?:secondo|come da|previsto dal|in base al|da) (?:il |l.)?CCNL"
    r"|inquadramento[^.\n]{0,40}CCNL"
    r"|in accordance with the CCNL"
    r"|competitive (?:\w+ )?(?:salary|compensation|pay|package)"
    r"|(?:salary|compensation) (?:commensurate|based on experience|DOE)"
    r"|commensurate with experience"
    # German, French, Dutch
    r"|(?:attraktiv|wettbewerbsf[äa]hig|leistungsgerecht|fair|marktgerecht|überdurchschnittlich)\w* "
    r"(?:verg[uü]tung|gehalt|bezahlung)"
    r"|nach (?:tarif|tarifvertrag)|gem[äa][ßs] tarif\w*|tarifliche verg[uü]tung"
    r"|(?:r[ée]mun[ée]ration|salaire) (?:attracti\w*|motivant\w*|comp[ée]titi\w*|selon)"
    r"|selon (?:profil|exp[ée]rience)|selon (?:la )?convention collective"
    r"|(?:competitief|marktconform|aantrekkelijk|goed|uitstekend)e? salaris|salaris (?:in overleg|marktconform)"
    r"|volgens (?:de )?cao|cao.?conform",
    re.IGNORECASE,
)

# A number: "35.000", "35,000", "1.800,50", "40,5", "35000"
_NUM = r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d{1,2})?|\d{1,3}(?:[.,']\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?"
_AMOUNT_RE = re.compile(rf"(?P<num>{_NUM})\s?(?P<mult>k\b|mila\b)?", re.IGNORECASE)

# "orario" alone does NOT mean hourly pay ("Orario full time"): explicit phrases are required
_HOUR_RE = re.compile(r"/\s?h\b|/\s?ora\b|all.ora|(?:paga|retribuzione|tariffa|compenso) orari[ao]"
                      r"|per hour|hourly|an hour|pro stunde|stundenlohn|/\s?std\b|de l.heure|par heure|/\s?heure"
                      r"|per uur|uurloon|/\s?uur\b", re.I)
_DAY_RE = re.compile(r"per day|al giorno|/\s?giorno|giornalier\w*|\bdaily\b|a giornata|pro tag|par jour|per dag",
                     re.I)
# "annuo/annua/annui/annue", not "annu" alone: it would also match "annuncio" (job ad)
_YEAR_RE = re.compile(r"\bRAL\b|\bannu[oaie]\b|all.anno|/\s?anno\b|per anno|\byear|annual|p\.\s?a\.|/\s?y\b"
                      r"|jahresgehalt|jahresbrutto|bruttojahres\w*|pro jahr|im jahr|j[äa]hrlich|/\s?jahr\b"
                      r"|par an\b|annuel\w*|/\s?an\b|per jaar|jaarsalaris|jaarinkomen|/\s?jaar\b", re.I)
# "14 mensilità" (14 monthly payments) means an annual salary, not a monthly one: only "mensile/mensili"
_MONTH_RE = re.compile(r"mensil[ei]\b|al mese|/\s?mese\b|per month|a month|monthly|/\s?month"
                       r"|monatlich|pro monat|im monat|/\s?monat\b|monatsgehalt|par mois|/\s?mois\b|mensuel\w*"
                       r"|per maand|/\s?maand\b|maandsalaris|maandelijks|maandloon", re.I)
YEARLY_FROM = 10_000   # a figure of €10,000 or more is annual, whatever words surround it
_CORPORATE_RE = re.compile(
    r"\b(?:mld|mln|miliard\w*|milion\w*|billion|million|bn|ricavi|fatturato|revenue\w*|turnover|ordini"
    r"|investit\w*|capitalizzazione|milliard\w*|mrd|millionen|umsatz\w*|chiffre d.affaires|omzet|miljard\w*"
    r"|miljoen\w*)\b", re.IGNORECASE)
# benefit amounts ("meal vouchers €8/day", "€1,200/year travel discount", "€1,000 bonus for your wedding"):
# skipped when the phrase itself names no pay keyword
_BENEFIT_RE = re.compile(
    r"\b(?:\w*vouchers?|\w*zulage|buon[io] pasto|ticket restaurant|discount|scont\w*|welfare|wedding|matrimonio"
    r"|parent\w*|genitori\w*|referral|bonus (?:for|per)"
    r"|jobticket|essenszuschuss|essensgutschein\w*|fahrtkosten\w*|zuschuss|tickets? restaurant|mutuelle"
    r"|prime de (?:cooptation|parrainage)|reiskosten\w*|thuiswerkvergoeding|onkostenvergoeding"
    r"|\w*pr[äa]mie|mitarbeiter werben|rabatt\w*|jobrad|corporate benefits"
    r"|forfait mobilit\w*|titre de transport|salle de sport|fitness|gym)\b", re.IGNORECASE)
_PAY_WORD_RE = re.compile(r"\bRAL\b|retribu|salar|stipend|compens|\bpay\b|lord|rimborso|reimburs|indennit|allowance"
                          r"|\bstage\b|tirocin|intern|monthly|mensil|al mese"
                          r"|gehalt|verg[uü]tung|salaire|r[ée]mun[ée]ration|brutto|bruto|\bbrut|praktik|werkstudent"
                          r"|stagiaire|gratification|alternance|stagevergoeding|monatlich|par mois|per maand",
                          re.IGNORECASE)


# an amount in millions or billions written with a letter right after it: "€800M+ in ARR", "€2B", "5 Mio. €"
_MILLIONS_AFTER_RE = re.compile(r"\s?(?:[MB]\b|bn\b|mio\b|mrd\b|M\+)", re.IGNORECASE)
# deal and account sizes in sales ads: "accounts spending €10K or more in annual recurring revenue",
# "accounts at €10K+ ARR" (case-sensitive: "arr." is a Paris arrondissement), "fixed-price packages (Lite from €350K"
_DEAL_RE = re.compile(r"\b(?:recurring revenue|(?-i:ARR)|fixed[- ]price|deal size|contract value)\b", re.IGNORECASE)


def _is_corporate(text: str, m) -> bool:
    """Company figures (revenue, ARR, orders), not pay; or a match inside a link
    ("https://app.notion.com/p/Compensation-Alan-9a9f...")."""
    word_start = max(text.rfind(" ", 0, m.start()), text.rfind("\n", 0, m.start())) + 1
    in_link = re.match(r"(?:https?://|www\.)\S*$", text[word_start:m.start() + 1]) is not None
    return bool(in_link or _CORPORATE_RE.search(text[m.start(): m.end() + 25])
                or _MILLIONS_AFTER_RE.match(text, m.end())
                or _DEAL_RE.search(text[max(0, m.start() - 60): m.end() + 45]))


def _is_benefit(text: str, m) -> bool:
    """An amount among benefits, with no pay word in the phrase or just before it."""
    return (not _PAY_WORD_RE.search(text[max(0, m.start() - 30): m.end()])
            and bool(_BENEFIT_RE.search(text[max(0, m.start() - 50): m.end() + 40])))


_GROSS_RE = re.compile(r"lord[oaie]|\bgross\b|\bRAL\b|brutto|\bbruts?\b|\bbrute?s?\b|\bbruto\b", re.I)
_NET_RE = re.compile(r"nett[oaie]|\bnet\b|\bnets?\b|\bnette?s?\b", re.I)
_CURRENCIES = [("EUR", r"€|\bEUR\b|\beuro"), ("USD", r"\$|\bUSD\b"),
               ("GBP", r"£|\bGBP\b"), ("CHF", r"\bCHF\b")]


def find_salary(text: str) -> list[str]:
    """The first 3 distinct phrases that contain a pay figure."""
    text = normalize_currency(text)
    found = (m.group(0).strip() for m in SALARY_RE.finditer(text)
             if not _is_corporate(text, m)     # no company revenue/orders
             and not _is_benefit(text, m))
    return list(dict.fromkeys(found))[:3]


def is_vague(text: str) -> bool:
    return bool(VAGUE_SALARY_RE.search(text or ""))


def to_number(raw: str) -> float:
    """'35.000' -> 35000, '1.800,50' -> 1800.5, '40,5' -> 40.5, '35,000' -> 35000, '45 000' -> 45000."""
    raw = re.sub(r"['\s\u00a0\u202f]", "", raw)
    if "." in raw and "," in raw:
        # the separator that appears last is the decimal one
        decimal = "," if raw.rfind(",") > raw.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        return float(raw.replace(thousands, "").replace(decimal, "."))
    for sep in (".", ","):
        if sep in raw:
            groups = raw.split(sep)
            if all(len(g) == 3 for g in groups[1:]):   # 35.000 / 1,200,000 -> thousands
                return float(raw.replace(sep, ""))
            return float(raw.replace(sep, "."))       # 40,5 -> decimal
    return float(raw)


def normalize_currency(text: str) -> str:
    """'€. 65.000' -> '€ 65.000': the dot after the symbol stopped the figure from being found."""
    return re.sub(r"(€|\bEUR)\.(?=\s*\d)", r"\1", text or "")


def _detect_period(context: str, first_value: float):
    # common sense before keywords: no monthly, daily or hourly pay reaches €10,000
    if first_value >= YEARLY_FROM:
        return "year"
    if _HOUR_RE.search(context):
        return "hour"
    if _DAY_RE.search(context):
        return "day"
    if _YEAR_RE.search(context):
        return "year"
    if _MONTH_RE.search(context):
        return "month"
    # no keyword: the order of magnitude decides
    if first_value >= 8000:
        return "year"
    if first_value >= 400:
        return "month"
    return None


def _detect_currency(context: str):
    for code, pattern in _CURRENCIES:
        if re.search(pattern, context, re.IGNORECASE):
            return code
    return "EUR" if re.search(r"\bRAL\b", context) else None


# multi-country pay tables ("Greece: 51,920.00 EUR - 77,880.00 EUR | Italy: 61,600.00 EUR - 92,400.00 EUR"):
# the band of the ad's own country is taken
COUNTRY_LABELS = {"IT": r"italy|italia", "DE": r"germany|deutschland", "NL": r"netherlands|nederland",
                  "FR": r"france"}
_OTHER_CURRENCY_RE = re.compile(r"\b(?:CZK|PLN|HUF|SEK|NOK|DKK|RON|BGN|GBP|USD|CHF|INR)\b")


def _country_band(text: str, country: str):
    """The pay band given for `country` in a multi-country table, or None."""
    words = COUNTRY_LABELS.get(country or "")
    if not words:
        return None
    # "Germany: 97,600.00 EUR - 146,400.00 EUR" or "Base salary range (Germany) : €112,500 - €168,750"
    m = re.search(rf"\b(?:{words})\)?\s*:\s*(?:(?P<cur>€|EUR)\s*)?(?P<lo>{_NUM})\s*(?:€|EUR)?\s*[-–]\s*(?:€|EUR)?\s*"
                  rf"(?P<hi>{_NUM})(?(cur)|\s*(?:€|EUR))", text, re.IGNORECASE)
    if not m:
        return None
    lo, hi = to_number(m.group("lo")), to_number(m.group("hi"))
    return {"min": lo, "max": hi, "currency": "EUR", "period": "year" if lo >= YEARLY_FROM else "month",
            "gross_net": None, "match": m.group(0).strip()}


def parse_salary(text: str, country: str = None):
    """First plausible pay figure in the text, or None. In a multi-country pay table, the band of
    `country` (ISO code of the ad's country) when the table has one.

    Returns {min, max, currency, period, gross_net, match}.
    period: year / month / hour; gross_net: gross / net / None.
    """
    text = normalize_currency(text)
    band = _country_band(text, country)
    if band:
        return band
    for m in SALARY_RE.finditer(text):
        # numbers: from the keyword to a little after it (to catch the upper end of a range)
        amounts_zone = text[m.start(): m.end() + 40]
        # wider context for period, currency and gross/net
        context = text[max(0, m.start() - 60): m.end() + 80]
        # company amounts (revenue, orders, investments) in the company intro: not salaries
        if _is_corporate(text, m) or _is_benefit(text, m):
            continue

        raw = []   # (value, had k/mila, original text)
        raw_is_percent = []
        for a in _AMOUNT_RE.finditer(amounts_zone):
            value = to_number(a.group("num"))
            has_mult = bool(a.group("mult"))
            if has_mult and value < 1000:   # "35k" -> 35,000; "35.000k" (redundant k) stays 35,000
                value *= 1000
            raw.append([value, has_mult, a.group("num")])
            raw_is_percent.append(amounts_zone[a.end():a.end() + 2].lstrip().startswith("%"))

        # "€k 50-60", "k€ 50": the k comes BEFORE the numbers
        if re.search(r"(?:€\s?k|\bk\s?€|\bek)\s*\d", amounts_zone, re.IGNORECASE):
            for item in raw:
                if not item[1] and item[0] < 1000:
                    item[0] *= 1000
                    item[1] = True

        # "30-35k": the first number inherits the k of the second
        for i in range(len(raw) - 1):
            if not raw[i][1] and raw[i + 1][1] and raw[i][0] < 1000:
                raw[i][0] *= 1000
                raw[i][1] = True

        # a figure in the millions right after the keyword: another currency of a multi-country pay table
        # ("Czechia: 1,224,000.00 ... Italy: 51,920.00 EUR"): move on to the next phrase
        if raw and raw[0][0] > 1_000_000 and _OTHER_CURRENCY_RE.search(amounts_zone):
            continue

        period_hint = "hour" if _HOUR_RE.search(context) else "day" if _DAY_RE.search(context) else None
        values = []
        for i, (value, has_mult, original) in enumerate(raw):
            looks_like_year = (1990 <= value <= 2100 and not has_mult
                               and original.isdigit())
            if looks_like_year:
                continue
            if raw_is_percent[i]:
                continue          # "al raggiungimento del 100%" (bonus target)
            if value < 100 and period_hint not in ("hour", "day"):
                continue          # "3 anni di esperienza", "14 mensilità", "livello 2" (years, payments, grade)
            if (period_hint == "day" and value < 20) or (period_hint == "hour" and value < 5):
                continue          # "Level 4°" is not a daily rate
            if value > 1_000_000:
                continue
            values.append(value)
        if not values:
            continue

        low = values[0]
        high = low
        if len(values) > 1 and low <= values[1] <= low * 3:
            high = values[1]
        # an "annual" figure below €5,000 is not a salary: move on to the next phrase
        if _detect_period(context, low) == "year" and high < 5000:
            continue

        gross_net = None
        g, n = _GROSS_RE.search(context), _NET_RE.search(context)
        if g and (not n or g.start() < n.start()):
            gross_net = "gross"
        elif n:
            gross_net = "net"

        return {
            "min": low,
            "max": high,
            "currency": _detect_currency(context),
            "period": _detect_period(context, low),
            "gross_net": gross_net,
            "match": m.group(0).strip(),
        }
    return None


MONTHS_PER_YEAR = 14   # in Italy the annual salary usually covers 14 monthly payments
# Monthly payments per year by country. Germany and France: 12 (the 13th month, where it exists, depends on
# the company and is not added). Netherlands: 12; the 8% holiday allowance is paid on top and not added,
# so that monthly figures are not inflated by an assumption.
MONTHS_BY_COUNTRY = {"IT": 14, "DE": 12, "FR": 12, "NL": 12}
RAL_MIN_PLAUSIBLE = 5_000      # below: not an annual salary (e.g. "indennità di 250 €", an allowance)
RAL_MAX_PLAUSIBLE = 500_000    # above: almost certainly revenue or something else


def annualize(value, period, currency, gross_net, months: int = MONTHS_PER_YEAR):
    """Converts a figure to gross annual salary in euro. None if it cannot be converted sensibly.

    - annual: unchanged
    - monthly: x 14 (x 12 for internships: an allowance without the 13th and 14th payments)
    - hourly, net or in another currency: None (not comparable with an annual salary)
    """
    if value is None or currency not in ("EUR", None) or gross_net == "net":
        return None
    if period == "year":
        annual = value
    elif period == "month":
        annual = value * months
    else:
        return None
    # an annual salary outside this range is almost certainly another amount (allowance, bonus, revenue)
    return annual if RAL_MIN_PLAUSIBLE <= annual <= RAL_MAX_PLAUSIBLE else None
