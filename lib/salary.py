"""
Estrazione della retribuzione dal testo degli annunci.

- find_salary(): le frasi che contengono una cifra (come in test_ats.py)
- VAGUE_SALARY_RE: formule che parlano di retribuzione senza dare una cifra
- parse_salary(): da testo libero a {min, max, valuta, periodo, lordo/netto}
- annualize(): porta tutto a RAL annua lorda in euro (mensile x 14)
"""

import re

# Cerca importi in euro / RAL nel testo: "RAL 35.000", "€40k", "30.000 - 40.000 EUR", ...
# Rispetto a test_ats.py: \b intorno a RAL, altrimenti "generale 2" contava come RAL.
SALARY_RE = re.compile(
    r"(?:\bRAL\b|retribu\w*|salary|compenso|compensation|stipendio|\bpay\b|lordo annuo|annuo lordo)"
    r"[^.\n]{0,60}?\d[\d.,]*\s?(?:k|K|mila)?"
    r"|(?:€|EUR)\s?\d[\d.,]*\s?(?:k|K)?"
    r"|\d[\d.,]*\s?(?:k|K)?\s?(?:€|EUR|euro)",
    re.IGNORECASE,
)

# Formule che "parlano" di retribuzione senza dare una cifra (indice di trasparenza)
VAGUE_SALARY_RE = re.compile(
    r"commisurat\w* (?:all.|al |alle )(?:esperienz|competenz|profil|capacit)"
    r"|(?:retribuzione|pacchetto retributivo|trattamento economico|compenso)"
    r" (?:competitiv|adeguat|interessant|in linea|commisurat|di sicuro interesse)"
    r"|(?:secondo|come da|previsto dal|in base al|da) (?:il |l.)?CCNL"
    r"|inquadramento[^.\n]{0,40}CCNL"
    r"|in accordance with the CCNL"
    r"|competitive (?:salary|compensation|pay|package)"
    r"|(?:salary|compensation) (?:commensurate|based on experience|DOE)"
    r"|commensurate with experience",
    re.IGNORECASE,
)

# Un numero: "35.000", "35,000", "1.800,50", "40,5", "35000"
_NUM = r"\d{1,3}(?:[.,']\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?"
_AMOUNT_RE = re.compile(rf"(?P<num>{_NUM})\s?(?P<mult>k\b|mila\b)?", re.IGNORECASE)

# "orario" da solo NON indica paga oraria ("Orario full time"): servono espressioni esplicite
_HOUR_RE = re.compile(r"/\s?h\b|/\s?ora\b|all.ora|(?:paga|retribuzione|tariffa|compenso) orari[ao]"
                      r"|per hour|hourly|an hour", re.I)
_DAY_RE = re.compile(r"per day|al giorno|/\s?giorno|giornalier\w*|\bdaily\b|a giornata", re.I)
# "annuo/annua/annui/annue", non "annu" da solo: comparirebbe anche in "annuncio"
_YEAR_RE = re.compile(r"\bRAL\b|\bannu[oaie]\b|all.anno|/\s?anno\b|per anno|\byear|annual|p\.\s?a\.|/\s?y\b", re.I)
# "14 mensilità" indica una RAL, non uno stipendio mensile: solo "mensile/mensili"
_MONTH_RE = re.compile(r"mensil[ei]\b|al mese|/\s?mese\b|per month|a month|monthly|/\s?month", re.I)
YEARLY_FROM = 10_000   # una cifra da 10.000 € in su è annua, qualunque parola ci sia intorno
_CORPORATE_RE = re.compile(
    r"\b(?:mld|mln|miliard\w*|milion\w*|billion|million|bn|ricavi|fatturato|revenue\w*|turnover|ordini"
    r"|investit\w*|capitalizzazione)\b", re.IGNORECASE)
_GROSS_RE = re.compile(r"lord[oaie]|\bgross\b|\bRAL\b", re.I)
_NET_RE = re.compile(r"nett[oaie]|\bnet\b", re.I)
_CURRENCIES = [("EUR", r"€|\bEUR\b|\beuro"), ("USD", r"\$|\bUSD\b"),
               ("GBP", r"£|\bGBP\b"), ("CHF", r"\bCHF\b")]


def find_salary(text: str) -> list[str]:
    """Le prime 3 frasi distinte che contengono una cifra di retribuzione."""
    text = normalize_currency(text)
    found = (m.group(0).strip() for m in SALARY_RE.finditer(text)
             if not _CORPORATE_RE.search(text[m.start(): m.end() + 25]))   # niente ricavi/ordini aziendali
    return list(dict.fromkeys(found))[:3]


def is_vague(text: str) -> bool:
    return bool(VAGUE_SALARY_RE.search(text or ""))


def to_number(raw: str) -> float:
    """'35.000' -> 35000, '1.800,50' -> 1800.5, '40,5' -> 40.5, '35,000' -> 35000."""
    raw = raw.replace("'", "")
    if "." in raw and "," in raw:
        # il separatore che compare per ultimo è quello dei decimali
        decimal = "," if raw.rfind(",") > raw.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        return float(raw.replace(thousands, "").replace(decimal, "."))
    for sep in (".", ","):
        if sep in raw:
            groups = raw.split(sep)
            if all(len(g) == 3 for g in groups[1:]):   # 35.000 / 1,200,000 -> migliaia
                return float(raw.replace(sep, ""))
            return float(raw.replace(sep, "."))       # 40,5 -> decimale
    return float(raw)


def normalize_currency(text: str) -> str:
    """'€. 65.000' (example) -> '€ 65.000': il punto dopo il simbolo interrompeva la ricerca della cifra."""
    return re.sub(r"(€|\bEUR)\.(?=\s*\d)", r"\1", text or "")


def _detect_period(context: str, first_value: float):
    # buon senso prima delle parole: nessuno stipendio mensile, giornaliero o orario arriva a 10.000 €
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
    # nessuna parola chiave: decide l'ordine di grandezza
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


def parse_salary(text: str):
    """Prima cifra di retribuzione plausibile nel testo, oppure None.

    Restituisce {min, max, currency, period, gross_net, match}.
    period: year / month / hour; gross_net: gross / net / None.
    """
    text = normalize_currency(text)
    for m in SALARY_RE.finditer(text):
        # numeri: dalla parola chiave fino a poco dopo (per prendere il secondo estremo della fascia)
        amounts_zone = text[m.start(): m.end() + 40]
        # contesto più largo per periodo, valuta e lordo/netto
        context = text[max(0, m.start() - 60): m.end() + 80]
        # importi aziendali (ricavi, ordini, investimenti) nella presentazione dell'azienda: non sono stipendi
        if _CORPORATE_RE.search(text[m.start(): m.end() + 25]):
            continue

        raw = []   # (valore, aveva k/mila, testo originale)
        raw_is_percent = []
        for a in _AMOUNT_RE.finditer(amounts_zone):
            value = to_number(a.group("num"))
            has_mult = bool(a.group("mult"))
            if has_mult and value < 1000:   # "35k" -> 35.000; "35.000k" (k superflua) resta 35.000
                value *= 1000
            raw.append([value, has_mult, a.group("num")])
            raw_is_percent.append(amounts_zone[a.end():a.end() + 2].lstrip().startswith("%"))

        # "€k 50-60", "k€ 50": la k sta PRIMA dei numeri
        if re.search(r"(?:€\s?k|\bk\s?€|\bek)\s*\d", amounts_zone, re.IGNORECASE):
            for item in raw:
                if not item[1] and item[0] < 1000:
                    item[0] *= 1000
                    item[1] = True

        # "30-35k": il primo numero eredita la k del secondo
        for i in range(len(raw) - 1):
            if not raw[i][1] and raw[i + 1][1] and raw[i][0] < 1000:
                raw[i][0] *= 1000
                raw[i][1] = True

        period_hint = "hour" if _HOUR_RE.search(context) else "day" if _DAY_RE.search(context) else None
        values = []
        for i, (value, has_mult, original) in enumerate(raw):
            looks_like_year = (1990 <= value <= 2100 and not has_mult
                               and original.isdigit())
            if looks_like_year:
                continue
            if raw_is_percent[i]:
                continue          # "al raggiungimento del 100%"
            if value < 100 and period_hint not in ("hour", "day"):
                continue          # "3 anni di esperienza", "14 mensilità", "livello 2"
            if (period_hint == "day" and value < 20) or (period_hint == "hour" and value < 5):
                continue          # "Level 4°" non è una paga giornaliera
            if value > 1_000_000:
                continue
            values.append(value)
        if not values:
            continue

        low = values[0]
        high = low
        if len(values) > 1 and low <= values[1] <= low * 3:
            high = values[1]
        # una cifra "annua" sotto 5.000 € non è una retribuzione: si cerca la frase successiva
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


MONTHS_PER_YEAR = 14   # in Italia la RAL si calcola di solito su 14 mensilità
RAL_MIN_PLAUSIBLE = 5_000      # sotto: non è una RAL (es. "indennità di 250 €")
RAL_MAX_PLAUSIBLE = 500_000    # sopra: quasi certamente fatturato o altro


def annualize(value, period, currency, gross_net, months: int = MONTHS_PER_YEAR):
    """Porta una cifra a RAL annua lorda in euro. None se non è convertibile in modo sensato.

    - annuale: resta così
    - mensile: x 14 (x 12 per stage e tirocini: rimborso spese senza tredicesima e quattordicesima)
    - oraria, netta o in altra valuta: None (non si confronta con una RAL)
    """
    if value is None or currency not in ("EUR", None) or gross_net == "net":
        return None
    if period == "year":
        annual = value
    elif period == "month":
        annual = value * months
    else:
        return None
    # una RAL fuori da questo intervallo è quasi certamente un altro importo (indennità, bonus, fatturato)
    return annual if RAL_MIN_PLAUSIBLE <= annual <= RAL_MAX_PLAUSIBLE else None
