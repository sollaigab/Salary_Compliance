"""
Job ad locations: recognizing the observed countries and normalizing to the `locations` table.

Observed countries: Italy, plus Germany, the Netherlands and France.

Sources (open data, downloaded once and saved in data/reference/):
- ISTAT, "Elenco comuni italiani" (CC BY 4.0): municipality, province, region, macro-area, provincial capital
- comuni-json by Matteo Contrini (MIT, ISTAT data): population, for the size band
- Eurostat, "Local Administrative Units (LAU) 2023" and "NUTS 2021" (Eurostat reuse policy, with attribution):
  municipalities with population and region for Germany, the Netherlands and France

location_id:
- 'IT-<ISTAT code>' for a municipality     e.g. IT-015146 (Milano)
- 'IT-REG-<region code>' region only        e.g. IT-REG-03 (Lombardia)
- 'IT' Italy without a city, 'IT-REMOTE' remote in Italy, 'EU-REMOTE' remote in Europe/anywhere
- 'DE-<LAU code>', 'DE-REG-<NUTS code>', 'DE', 'DE-REMOTE' (the same for NL and FR)

Place names stay in the national language, as in the ISTAT and Eurostat lists.
Regions: Italian regions, German Länder (NUTS 1), Dutch provinces (NUTS 2), French régions (NUTS 1).
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
LAU_URL = "https://ec.europa.eu/eurostat/documents/345175/501971/EU-27-LAU-2023-NUTS-2021.xlsx"
NUTS_URL = "https://ec.europa.eu/eurostat/documents/345175/629341/NUTS2021.xlsx"
LAU_FILE = REF_DIR / "eurostat_lau_2023.xlsx"
NUTS_FILE = REF_DIR / "eurostat_nuts_2021.xlsx"

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


# The other observed countries: ISO code -> English name; the codes/names an ATS may send as country,
# and the words that point to the country in a free-text location (country names, main cities, regions)
COUNTRY_NAMES = {"IT": "Italy", "DE": "Germany", "NL": "Netherlands", "FR": "France"}
FOREIGN_SCOPE = {
    "DE": ({"de", "deu", "germany", "deutschland"},
           r"germany|deutschland|allemagne|duitsland|berlin|hamburg|munich|muenchen|munchen"
           r"|cologne|koln|koeln|frankfurt|stuttgart|dusseldorf|duesseldorf|leipzig|dortmund|essen|bremen|dresden"
           r"|hannover|hanover|nuremberg|nurnberg|nuernberg|bonn|mannheim|karlsruhe|wiesbaden|mainz|walldorf"
           r"|wolfsburg|ingolstadt|erlangen|darmstadt|heidelberg|bavaria|bayern|hesse|hessen"),
    "NL": ({"nl", "nld", "netherlands", "the netherlands", "nederland", "holland"},
           r"netherlands|nederland|pays bas|niederlande|holland|amsterdam|rotterdam|the hague|den haag|utrecht"
           r"|eindhoven|groningen|hoofddorp|schiphol|amstelveen|leiden|delft|haarlem|nijmegen|tilburg|breda"
           r"|arnhem|maastricht|zwolle|almere|veldhoven|enschede|noord holland|zuid holland|noord brabant"),
    "FR": ({"fr", "fra", "france"},
           r"france|frankreich|frankrijk|paris|lyon|marseille|toulouse|nantes|strasbourg|montpellier"
           r"|bordeaux|lille|rennes|la defense|grenoble|sophia antipolis|boulogne billancourt|issy les moulineaux"
           r"|courbevoie|puteaux|neuilly sur seine|levallois perret|saint denis|nanterre|ile de france"
           r"|auvergne rhone alpes|provence alpes cote d azur|occitanie|nouvelle aquitaine|hauts de france"),
}
FOREIGN_TEXT_RE = {cc: re.compile(rf"\b(?:{words})\b") for cc, (_, words) in FOREIGN_SCOPE.items()}


def _norm(text) -> str:
    """normalize_text, keeping the German sharp s as 'ss' (Gießen -> giessen)."""
    return normalize_text(str(text or "").replace("ß", "ss"))


def scope_country(location, country=None) -> str | None:
    """ISO code of the observed country the ad points to ('IT', 'DE', 'NL', 'FR'), or None."""
    code = str(country or "").strip().lower()
    if code in ITALY_COUNTRY_CODES:
        return "IT"
    for cc, (codes, _) in FOREIGN_SCOPE.items():
        if code in codes:
            return cc
    if is_italy(location):
        return "IT"
    text = _norm(location)
    for cc, pattern in FOREIGN_TEXT_RE.items():
        if text and pattern.search(text):
            return cc
    return None


def in_scope(location, country=None) -> bool:
    """True if the location (free text) or the country code point to one of the observed countries."""
    return scope_country(location, country) is not None


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
    r"|singapore|china|japan|brazil|mexico|canada|australia|turkey|istanbul"
    r"|qu[eé]bec|montreal|toronto|vancouver|california|los angeles|san francisco|seattle)\b", re.I)


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
    """Downloads (once) the ISTAT, population and Eurostat files into data/reference/."""
    REF_DIR.mkdir(parents=True, exist_ok=True)
    for path, url in ((LAU_FILE, LAU_URL), (NUTS_FILE, NUTS_URL)):
        if not path.exists():
            r = http.request("GET", url, check_robots=True)
            r.raise_for_status()
            path.write_bytes(r.content)
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


# region level for each country: German Länder and French régions are NUTS 1, Dutch provinces NUTS 2
REGION_NUTS_LEVEL = {"DE": 1, "NL": 2, "FR": 1}
# English or short names used by the ATSs -> the national name in the Eurostat lists (normalized)
FOREIGN_ALIASES = {
    "munich": "munchen", "muenchen": "munchen", "cologne": "koln", "koeln": "koln", "nuremberg": "nurnberg",
    "nuernberg": "nurnberg", "frankfurt": "frankfurt am main", "hanover": "hannover", "duesseldorf": "dusseldorf",
    "the hague": "s gravenhage", "den haag": "s gravenhage", "hoofddorp": "haarlemmermeer",
    "schiphol": "haarlemmermeer", "la defense": "courbevoie",
}
FOREIGN_REGION_ALIASES = {
    "bavaria": "bayern", "hesse": "hessen", "lower saxony": "niedersachsen", "saxony": "sachsen",
    "north rhine westphalia": "nordrhein westfalen", "rhineland palatinate": "rheinland pfalz",
    "north holland": "noord holland", "south holland": "zuid holland", "north brabant": "noord brabant",
}


def load_foreign() -> tuple[list[dict], list[dict]]:
    """Municipalities (rows for the `locations` table) of the other observed countries, plus their
    special rows (country, remote in the country, one row per region), from Eurostat LAU and NUTS."""
    import openpyxl

    nuts_names = {}
    wb = openpyxl.load_workbook(NUTS_FILE, read_only=True)
    for row in wb["NUTS & SR 2021"].iter_rows(min_row=2, values_only=True):
        if row[0]:
            nuts_names[row[0]] = next((v for v in row[1:5] if v), None)
    wb = openpyxl.load_workbook(LAU_FILE, read_only=True)
    places, regions = [], {}
    for cc, level in REGION_NUTS_LEVEL.items():
        for row in wb[cc].iter_rows(min_row=2, values_only=True):
            nuts3, lau_code, name, pop = row[0], row[1], row[4] or row[3], row[6]
            if not nuts3 or not lau_code or not name:
                continue
            region_code = nuts3[:2 + level]
            pop = int(pop) if isinstance(pop, (int, float)) else None
            places.append({
                "location_id": f"{cc}-{lau_code}",
                "city": str(name).split(",")[0].strip(),      # "Stuttgart, Landeshauptstadt" -> "Stuttgart"
                "istat_code": None, "province": None, "province_code": None,
                "region": nuts_names.get(region_code), "region_code": region_code, "macro_area": None,
                "country": COUNTRY_NAMES[cc], "country_code": cc,
                "population": pop, "city_size_band": size_band(pop), "is_capoluogo": 0,
                "lat": None, "lon": None, "is_remote": 0,
            })
            regions[region_code] = (cc, nuts_names.get(region_code))
    base = dict.fromkeys(["city", "istat_code", "province", "province_code", "macro_area", "population",
                          "city_size_band", "lat", "lon"])
    special = []
    for cc in REGION_NUTS_LEVEL:
        for location_id, remote in ((cc, 0), (f"{cc}-REMOTE", 1)):
            special.append({**base, "location_id": location_id, "region": None, "region_code": None,
                            "country": COUNTRY_NAMES[cc], "country_code": cc, "is_capoluogo": 0, "is_remote": remote})
    for code, (cc, name) in regions.items():
        special.append({**base, "location_id": f"{cc}-REG-{code}", "region": name, "region_code": code,
                        "country": COUNTRY_NAMES[cc], "country_code": cc, "is_capoluogo": 0, "is_remote": 0})
    return places, special


class ForeignIndex:
    """Municipalities and regions of one country (Eurostat LAU), found inside a free-text location."""

    MIN_POP = 2000     # smaller municipalities only when they are the whole location: fewer homonyms

    def __init__(self, places: list[dict]):
        self.by_name, self.regions = {}, {}
        for c in places:
            for key in {_norm(c["city"]), _norm(re.sub(r"\s*\(.*\)", "", c["city"]))}:   # "Halle (Saale)"
                old = self.by_name.get(key)
                if not old or (c["population"] or 0) > (old["population"] or 0):
                    self.by_name[key] = c
            if c["region"]:
                self.regions[_norm(c["region"])] = c
        for alias, name in FOREIGN_REGION_ALIASES.items():
            if name in self.regions:
                self.regions[alias] = self.regions[name]

    def find_city(self, text: str):
        """The most populous municipality named in the text, longest names first (up to 5 words)."""
        words = _norm(text).split()
        for size in range(min(5, len(words)), 0, -1):
            best = None
            for i in range(len(words) - size + 1):
                phrase = " ".join(words[i:i + size])
                phrase = FOREIGN_ALIASES.get(phrase, phrase)
                c = self.by_name.get(phrase)
                if c and ((c["population"] or 0) >= self.MIN_POP or phrase == " ".join(words)):
                    if not best or (c["population"] or 0) > (best["population"] or 0):
                        best = c
            if best:
                return best
        return None

    def find_region(self, text: str):
        norm = _norm(text)
        for name in sorted(self.regions, key=len, reverse=True):
            if re.search(rf"\b{re.escape(name)}\b", norm):
                return self.regions[name]
        return None


class LocationIndex:
    """Finds the municipality (or region) inside a free-text location."""

    def __init__(self, comuni: list[dict], foreign: list[dict] = ()):
        self.foreign = {cc: ForeignIndex([p for p in foreign if p["country_code"] == cc])
                        for cc in {p["country_code"] for p in foreign}}
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

    def normalize_foreign(self, raw: str, cc: str, remote: bool) -> dict:
        """A location in Germany, the Netherlands or France: municipality, else region, else the country."""
        name = COUNTRY_NAMES[cc]
        index = self.foreign.get(cc)
        city = index.find_city(raw) if index and raw else None
        if city:
            return {"location_id": city["location_id"], "city": city["city"], "country": name, "is_remote": int(remote)}
        region = index.find_region(raw) if index and raw else None
        if region:
            return {"location_id": f"{cc}-REG-{region['region_code']}", "city": None, "country": name,
                    "is_remote": int(remote)}
        return {"location_id": f"{cc}-REMOTE" if remote else cc, "city": None, "country": name,
                "is_remote": int(remote)}

    def normalize(self, raw: str, country: str = None, workplace: str = None) -> dict:
        """From 'Milan, Lombardy, Italy' to {location_id, city, country, is_remote}."""
        raw = raw or ""
        remote = bool(REMOTE_RE.search(raw)) or (workplace or "").lower() in ("remote", "fully_remote")
        # the ATS country code wins: an ad coded as German is never matched against Italian municipalities
        coded = scope_country(None, country)
        if coded and coded != "IT":
            return self.normalize_foreign(raw, coded, remote)
        country_it = coded == "IT"
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
        foreign = scope_country(raw)
        if foreign and foreign != "IT":
            return self.normalize_foreign(raw, foreign, remote)
        if remote and EUROPE_RE.search(raw) and not FOREIGN_RE.search(raw):
            return {"location_id": "EU-REMOTE", "city": None, "country": None, "is_remote": 1}
        return {"location_id": None, "city": None, "country": country, "is_remote": int(remote)}
