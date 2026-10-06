"""
Job ad locations: recognizing Italy and normalizing to the `locations` table.

Sources (open data, downloaded once and saved in data/reference/):
- ISTAT, "Elenco comuni italiani" (CC BY 4.0): municipality, province, region, macro-area, provincial capital
- comuni-json by Matteo Contrini (MIT, ISTAT data): population, for the size band

location_id:
- 'IT-<ISTAT code>' for a municipality     e.g. IT-015146 (Milano)
- 'IT-REG-<region code>' region only        e.g. IT-REG-03 (Lombardia)
- 'IT' Italy without a city, 'IT-REMOTE' remote in Italy, 'EU-REMOTE' remote in Europe/anywhere

Italian place names stay in Italian, as in the ISTAT list.
"""

import csv
import io
import json
import re
from pathlib import Path

from lib.text import normalize_text, strip_accents

ROOT = Path(__file__).resolve().parent.parent
REF_DIR = ROOT / "data" / "reference"
ISTAT_URL = "https://www.istat.it/storage/codici-unita-amministrative/Elenco-comuni-italiani.csv"
POP_URL = "https://raw.githubusercontent.com/matteocontrini/comuni-json/master/comuni.json"
ISTAT_FILE = REF_DIR / "comuni_istat.csv"
POP_FILE = REF_DIR / "comuni_popolazione.json"

# ---------------------------------------------------------------- quick check (Phase 1)

# Country + main cities/municipalities with offices + regions
ITALY_RE = re.compile(
    r"\b(?:italy|italia|italien|italie"
    r"|milan|milano|rome|roma|turin|torino|napoli|naples|bologna|firenze|florence"
    r"|genova|genoa|venezia|venice|mestre|padova|padua|verona|bari|palermo|catania"
    r"|trieste|bergamo|brescia|parma|modena|reggio emilia|pisa|cagliari|ancona"
    r"|perugia|pescara|trento|bolzano|udine|treviso|vicenza|monza|lecce|salerno"
    r"|varese|novara|piacenza|ferrara|ravenna|rimini|livorno|lucca|siena|arezzo"
    r"|ivrea|segrate|assago|basiglio|rozzano|sesto san giovanni|san donato milanese"
    r"|cologno monzese|fiumicino|maranello|cesena|forli|empoli|pesaro|lodi|agrate brianza"
    r"|mantova|cremona|pavia|lecco|sondrio|biella|cuneo|asti|alessandria|la spezia|savona|imperia"
    r"|rovigo|belluno|pordenone|gorizia|massa|carrara|pistoia|prato|grosseto|viterbo|latina"
    r"|frosinone|caserta|avellino|benevento|foggia|taranto|brindisi|matera|potenza|cosenza"
    r"|reggio calabria|catanzaro|messina|siracusa|ragusa|trapani|agrigento|sassari|olbia|terni"
    r"|l.aquila|teramo|chieti|campobasso|aosta"
    # regions
    r"|lombardia|lombardy|piemonte|piedmont|veneto|emilia romagna|emilia|toscana|tuscany|lazio"
    r"|campania|puglia|apulia|sicilia|sicily|sardegna|sardinia|liguria|marche|abruzzo|umbria"
    r"|friuli venezia giulia|friuli|trentino|alto adige|calabria|basilicata|molise|valle d.aosta)\b",
    re.IGNORECASE,
)

ITALY_COUNTRY_CODES = {"it", "ita", "italy", "italia"}


def is_italy(location, country=None) -> bool:
    """True if the location (free text) or the country code point to Italy."""
    if country and str(country).strip().lower() in ITALY_COUNTRY_CODES:
        return True
    return bool(location and ITALY_RE.search(strip_accents(str(location))))


# ---------------------------------------------------------------- geography (Phase 2)

# Foreign names of Italian cities (as international ATSs write them)
EXONYMS = {
    "milan": "milano", "rome": "roma", "turin": "torino", "naples": "napoli",
    "florence": "firenze", "genoa": "genova", "venice": "venezia", "padua": "padova",
    "mantua": "mantova", "syracuse": "siracusa", "leghorn": "livorno", "bozen": "bolzano",
    "sienna": "siena", "trent": "trento", "milano area": "milano", "greater milan": "milano",
}
REGION_ALIASES = {
    "lombardy": "lombardia", "piedmont": "piemonte", "tuscany": "toscana", "apulia": "puglia",
    "sicily": "sicilia", "sardinia": "sardegna", "emilia romagna": "emilia romagna",
    "aosta valley": "valle d aosta", "south tyrol": "trentino alto adige",
}
REMOTE_RE = re.compile(r"\b(?:remote|remoto|da remoto|full remote|telelavoro|work from home|anywhere)\b", re.I)
EUROPE_RE = re.compile(r"\b(?:europe|europa|emea|eu|worldwide|global|anywhere)\b", re.I)
FOREIGN_RE = re.compile(
    r"\b(?:united kingdom|uk|london|england|ireland|dublin|france|paris|germany|deutschland|berlin"
    r"|munich|spain|espana|madrid|barcelona|portugal|lisbon|netherlands|amsterdam|belgium|brussels"
    r"|switzerland|zurich|geneva|austria|vienna|poland|warsaw|usa|united states|new york|india"
    r"|singapore|china|japan|brazil|mexico|canada|australia|turkey|istanbul)\b", re.I)


def size_band(population) -> str | None:
    if not population:
        return None
    if population >= 1_000_000:
        return "metro"
    if population >= 250_000:
        return "large"
    if population >= 50_000:
        return "medium"
    return "small"


def download_reference(http):
    """Downloads (once) the ISTAT and population files into data/reference/."""
    REF_DIR.mkdir(parents=True, exist_ok=True)
    if not ISTAT_FILE.exists():
        r = http.request("GET", ISTAT_URL, check_robots=True)
        r.raise_for_status()
        ISTAT_FILE.write_text(r.content.decode("latin-1"), encoding="utf-8")
    if not POP_FILE.exists():
        r = http.request("GET", POP_URL, check_robots=True)
        r.raise_for_status()
        POP_FILE.write_text(r.text, encoding="utf-8")


def load_comuni() -> list[dict]:
    """ISTAT municipalities with population. One row = one dict ready for the `locations` table."""
    population = {c["codice"]: c.get("popolazione") for c in json.loads(POP_FILE.read_text(encoding="utf-8"))}
    rows = list(csv.reader(io.StringIO(ISTAT_FILE.read_text(encoding="utf-8")), delimiter=";"))
    comuni = []
    for r in rows[1:]:
        if len(r) < 15 or not r[4].strip():
            continue
        code = r[4].strip()
        pop = population.get(code)
        comuni.append({
            "location_id": f"IT-{code}",
            "city": r[6].strip(),
            "istat_code": code,
            "province": r[11].strip(),
            "province_code": r[14].strip(),
            "region": r[10].strip(),
            "region_code": r[0].strip(),
            "macro_area": r[9].strip(),
            "country": "Italy",
            "country_code": "IT",
            "population": pop,
            "city_size_band": size_band(pop),
            "is_capoluogo": int(r[13].strip() == "1"),
            "lat": None,
            "lon": None,
            "is_remote": 0,
        })
    return comuni


def special_locations(comuni: list[dict]) -> list[dict]:
    """Rows for 'Italy', 'remote in Italy', 'remote in Europe' and one row per region."""
    base = dict.fromkeys(["city", "istat_code", "province", "province_code", "region", "region_code",
                          "macro_area", "population", "city_size_band", "lat", "lon"])
    rows = [
        {**base, "location_id": "IT", "country": "Italy", "country_code": "IT", "is_capoluogo": 0, "is_remote": 0},
        {**base, "location_id": "IT-REMOTE", "country": "Italy", "country_code": "IT", "is_capoluogo": 0, "is_remote": 1},
        {**base, "location_id": "EU-REMOTE", "country": "Europe", "country_code": None, "is_capoluogo": 0, "is_remote": 1},
    ]
    regions = {}
    for c in comuni:
        regions.setdefault(c["region_code"], c)
    for code, c in regions.items():
        rows.append({**base, "location_id": f"IT-REG-{code}", "region": c["region"], "region_code": code,
                     "macro_area": c["macro_area"], "country": "Italy", "country_code": "IT",
                     "is_capoluogo": 0, "is_remote": 0})
    return rows


class LocationIndex:
    """Finds the municipality (or region) inside a free-text location."""

    def __init__(self, comuni: list[dict]):
        self.by_name = {}
        for c in comuni:
            key = normalize_text(c["city"])
            # same name in different regions: keep the most populous (e.g. "San Giorgio")
            old = self.by_name.get(key)
            if not old or (c["population"] or 0) > (old["population"] or 0):
                self.by_name[key] = c
        self.regions = {}
        for c in comuni:
            self.regions[normalize_text(c["region"])] = c
        for alias, name in REGION_ALIASES.items():
            if normalize_text(name) in self.regions:
                self.regions[alias] = self.regions[normalize_text(name)]

    def find_city(self, text: str):
        """Looks for the longest municipality name in the text (up to 5 words)."""
        words = normalize_text(text).split()
        best = None
        for size in range(min(5, len(words)), 0, -1):
            for i in range(len(words) - size + 1):
                phrase = " ".join(words[i:i + size])
                phrase = EXONYMS.get(phrase, phrase)
                c = self.by_name.get(phrase)
                # very short names (e.g. "Re", "Ne", "Vo") only if they are the whole location
                if c and (len(phrase) >= 4 or phrase == " ".join(words)):
                    if not best or (c["population"] or 0) > (best["population"] or 0):
                        best = c
            if best:
                return best
        return None

    def find_region(self, text: str):
        norm = normalize_text(text)
        for name in sorted(self.regions, key=len, reverse=True):
            if re.search(rf"\b{re.escape(name)}\b", norm):
                return self.regions[name]
        return None

    def normalize(self, raw: str, country: str = None, workplace: str = None) -> dict:
        """From 'Milan, Lombardy, Italy' to {location_id, city, country, is_remote}."""
        raw = raw or ""
        remote = bool(REMOTE_RE.search(raw)) or (workplace or "").lower() in ("remote", "fully_remote")
        country_it = country and str(country).strip().lower() in ITALY_COUNTRY_CODES
        city = self.find_city(raw) if raw else None
        if city and FOREIGN_RE.search(raw) and not (country_it or re.search(r"\bital", raw, re.I)):
            city = None     # e.g. "Paris, France": "Paris" is not a municipality, but avoid homonyms
        if city:
            return {"location_id": city["location_id"], "city": city["city"], "country": "Italy",
                    "is_remote": int(remote)}
        region = self.find_region(raw)
        if region and not FOREIGN_RE.search(raw):
            return {"location_id": f"IT-REG-{region['region_code']}", "city": None, "country": "Italy",
                    "is_remote": int(remote)}
        if country_it or re.search(r"\bital(?:y|ia)\b", raw, re.I):
            return {"location_id": "IT-REMOTE" if remote else "IT", "city": None, "country": "Italy",
                    "is_remote": int(remote)}
        if remote and EUROPE_RE.search(raw) and not FOREIGN_RE.search(raw):
            return {"location_id": "EU-REMOTE", "city": None, "country": None, "is_remote": 1}
        return {"location_id": None, "city": None, "country": country, "is_remote": int(remote)}
