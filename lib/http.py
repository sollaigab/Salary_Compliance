"""
Sessione HTTP condivisa da tutti gli script.

Fa queste cose:
- rate limiting gentile: pausa minima tra due richieste allo stesso dominio;
- retry con backoff esponenziale su errori di rete, 429 e 5xx;
- stop su richiesta: se un servizio chiede una pausa lunga (Retry-After), non lo contattiamo più;
- cache su disco, solo se attivata con init(use_cache=True) (utile in sviluppo);
- con check_robots=True (usato per TUTTE le fonti): rispetto di robots.txt e della riserva
  sul text and data mining (TDMRep: /.well-known/tdmrep.json e header "tdm-reservation"),
  il meccanismo di opposizione previsto dall'art. 70-quater L. 633/1941.
"""

import fnmatch
import json
import threading
import time
from datetime import timedelta
from pathlib import Path
from urllib import robotparser
from urllib.parse import urlparse

import requests
import requests_cache

ROOT = Path(__file__).resolve().parent.parent
CACHE_PATH = ROOT / "data" / "cache" / "http_cache"
CACHE_DAYS = 7

# Ci presentiamo in modo riconoscibile: browser reale + nome del progetto
BOT_NAME = "osservatorio-ral"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 " + BOT_NAME + "/0.1"
)
TIMEOUT = 20

# Pausa minima (secondi) tra due richieste allo stesso dominio.
# Le API degli ATS sono fatte per essere interrogate: lì bastano ~3 richieste al secondo.
# I siti aziendali li trattiamo con più riguardo: 1 richiesta al secondo.
DEFAULT_INTERVAL = 1.0
ATS_API_INTERVAL = 0.35
ATS_API_SUFFIXES = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com",
)

RETRY_STATUS = {429, 500, 502, 503, 504}

# Se un servizio chiede di aspettare più di così (Retry-After), non insistiamo:
# l'host viene bloccato fino alla scadenza, anche nei run successivi (file su disco).
MAX_RETRY_WAIT = 60
BLOCKED_HOSTS_FILE = ROOT / "data" / "cache" / "blocked_hosts.json"
# Domini più delicati: pausa più lunga tra le richieste
SLOW_DOMAINS = {"apply.workable.com": 3.0}


class RobotsDisallowed(Exception):
    """La pagina è vietata da robots.txt: non la scarichiamo."""


class TDMReserved(RobotsDisallowed):
    """Il titolare si oppone al text and data mining (TDMRep): non usiamo il contenuto."""


class HostBlocked(requests.RequestException):
    """Il servizio ci ha chiesto di fermarci (429 con Retry-After lungo): non lo contattiamo."""


_session = None
_last_call = {}      # dominio -> istante dell'ultima richiesta vera (non dalla cache)
_robots = {}         # "https://dominio" -> RobotFileParser
_tdm_rules = {}      # "https://dominio" -> regole di tdmrep.json (lista, vuota se assente)

# Piattaforme con molti sottodomini sugli stessi server: un unico rate limit per tutta la piattaforma
RATE_GROUPS = ("myworkdayjobs.com", "myworkdaysite.com")


def init(use_cache: bool = False):
    """Crea la sessione. Con use_cache=True le risposte restano su disco per CACHE_DAYS giorni."""
    global _session
    if use_cache:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _session = requests_cache.CachedSession(
            str(CACHE_PATH),
            backend="sqlite",
            expire_after=timedelta(days=CACHE_DAYS),
            allowable_methods=("GET", "HEAD", "POST"),   # POST serve per Workday
            allowable_codes=(200, 404),                   # anche i 404: utili nel probing
        )
    else:
        _session = requests.Session()
    _session.headers.update({"User-Agent": USER_AGENT})


def _get_session():
    if _session is None:
        init()
    return _session


def interval_for(host: str) -> float:
    if host in SLOW_DOMAINS:
        return SLOW_DOMAINS[host]
    return ATS_API_INTERVAL if host.endswith(ATS_API_SUFFIXES) else DEFAULT_INTERVAL


def _load_blocked() -> dict:
    try:
        return json.loads(BLOCKED_HOSTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def block_host(host: str, seconds: float):
    """Segna l'host come bloccato per `seconds` secondi (salvato su disco)."""
    blocked = _load_blocked()
    blocked[host] = time.time() + seconds
    BLOCKED_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    BLOCKED_HOSTS_FILE.write_text(json.dumps(blocked, indent=2), encoding="utf-8")


def blocked_until(host: str) -> float:
    """Istante fino a cui l'host è bloccato (0 se non lo è)."""
    until = _load_blocked().get(host, 0)
    return until if until > time.time() else 0


def rate_key(host: str) -> str:
    """Chiave del rate limit: il dominio, oppure la piattaforma intera (es. tutti i tenant Workday)."""
    for group in RATE_GROUPS:
        if host.endswith(group):
            return group
    return host


_rate_lock = threading.Lock()   # il probe Workday usa più thread: il ritmo resta comunque unico


def wait_turn(host: str):
    """Aspetta, se serve, prima di colpire di nuovo lo stesso dominio (o piattaforma).

    Ogni chiamata prenota il proprio turno: anche con più thread si rispetta la pausa minima.
    """
    key = rate_key(host)
    with _rate_lock:
        now = time.time()
        start = max(now, _last_call.get(key, 0) + interval_for(host))
        _last_call[key] = start
    if start > now:
        time.sleep(start - now)


def mark_call(host: str):
    key = rate_key(host)
    with _rate_lock:
        _last_call[key] = max(_last_call.get(key, 0), time.time())


def _retry_delay(response, attempt: int) -> float:
    """Usa l'header Retry-After se c'è (max 60s), altrimenti 2, 4, 8... secondi."""
    value = response.headers.get("Retry-After", "")
    if value.isdigit():
        return min(int(value), 60)
    return 2 ** (attempt + 1)


def request(method: str, url: str, check_robots: bool = False, retries: int = 3, **kwargs):
    """Richiesta HTTP con rate limit e retry. Restituisce la Response (anche se 4xx).

    Solleva requests.RequestException se la rete fallisce anche dopo i retry,
    RobotsDisallowed se check_robots=True e robots.txt vieta l'URL.
    """
    if check_robots:
        if not allowed_by_robots(url):
            raise RobotsDisallowed(url)
        if tdm_reserved(url):
            raise TDMReserved(url)

    session = _get_session()
    host = urlparse(url).netloc.lower()
    kwargs.setdefault("timeout", TIMEOUT)
    if blocked_until(host):
        raise HostBlocked(f"{host} ci ha chiesto di fermarci: bloccato fino a "
                          f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(blocked_until(host)))}")

    for attempt in range(retries + 1):
        wait_turn(host)
        try:
            response = session.request(method, url, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as e:
            mark_call(host)
            # dominio inesistente (DNS): riprovare non serve, succede spesso nel probing
            dns_error = "getaddrinfo" in str(e) or "NameResolution" in str(e)
            if attempt == retries or dns_error:
                raise
            time.sleep(2 ** attempt)
            continue

        if not getattr(response, "from_cache", False):
            mark_call(host)
        if response.status_code == 429:
            asked = response.headers.get("Retry-After", "")
            if asked.isdigit() and int(asked) > MAX_RETRY_WAIT:
                block_host(host, int(asked))
                raise HostBlocked(f"{host}: 429 con Retry-After {asked}s, host bloccato")
        if response.status_code in RETRY_STATUS and attempt < retries:
            time.sleep(_retry_delay(response, attempt))
            continue
        if check_robots and response.headers.get("tdm-reservation", "").strip() == "1":
            raise TDMReserved(url)      # riserva dichiarata nella risposta: il contenuto non si usa
        return response


def get_json(url: str, **kwargs):
    """GET di un JSON. Per default controlla robots.txt e riserva TDM (vale per tutte le fonti)."""
    kwargs.setdefault("headers", {"Accept": "application/json"})
    kwargs.setdefault("check_robots", True)
    r = request("GET", url, **kwargs)
    r.raise_for_status()
    return r.json()


def post_json(url: str, payload: dict, **kwargs):
    """POST con risposta JSON (Workday). Stessi controlli di get_json."""
    kwargs.setdefault("headers", {"Accept": "application/json"})
    kwargs.setdefault("check_robots", True)
    r = request("POST", url, json=payload, **kwargs)
    r.raise_for_status()
    return r.json()


def allowed_by_robots(url: str) -> bool:
    """True se robots.txt del dominio permette di scaricare l'URL.

    Regole (come fa Google): robots.txt assente o 4xx = tutto permesso;
    5xx o errore di rete = per prudenza tutto vietato.
    """
    p = urlparse(url)
    root = f"{p.scheme}://{p.netloc}"
    if blocked_until(p.netloc.lower()):
        raise HostBlocked(f"{p.netloc} bloccato: robots.txt non verificabile ora")
    if root not in _robots:
        parser = robotparser.RobotFileParser()
        try:
            r = request("GET", root + "/robots.txt", retries=1)
            if r.status_code >= 500:
                parser.disallow_all = True
            elif r.status_code >= 400:
                parser.allow_all = True
            else:
                parser.parse(r.text.splitlines())
        except HostBlocked:
            raise
        except requests.RequestException:
            parser.disallow_all = True
        _robots[root] = parser
    return _robots[root].can_fetch(BOT_NAME, url)


def tdm_reserved(url: str) -> bool:
    """True se il sito dichiara in /.well-known/tdmrep.json una riserva TDM che copre l'URL.

    Formato TDMRep (W3C Community Group): [{"location": "/percorso*", "tdm-reservation": 1}, ...]
    File assente o non valido = nessuna riserva dichiarata in questo modo.
    """
    p = urlparse(url)
    root = f"{p.scheme}://{p.netloc}"
    if root not in _tdm_rules:
        rules = []
        try:
            r = request("GET", root + "/.well-known/tdmrep.json", retries=1)
            if r.status_code == 200:
                data = r.json()
                rules = data if isinstance(data, list) else []
        except (requests.RequestException, ValueError):
            rules = []
        _tdm_rules[root] = rules
    path = p.path or "/"
    for rule in _tdm_rules[root]:
        if not isinstance(rule, dict):
            continue
        location = str(rule.get("location", "/*"))
        if str(rule.get("tdm-reservation", 0)) == "1" and fnmatch.fnmatch(path, location):
            return True
    return False
