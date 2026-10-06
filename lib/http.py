"""
HTTP session shared by every script.

It handles:
- polite rate limiting: a minimum pause between two requests to the same domain;
- retries with exponential backoff on network errors, 429 and 5xx;
- stop on request: if a service asks for a long pause (Retry-After), it is not contacted again;
- on-disk cache, only when enabled with init(use_cache=True) (useful during development);
- with check_robots=True (used for ALL sources): robots.txt and the text and data mining
  reservation (TDMRep: /.well-known/tdmrep.json and the "tdm-reservation" header), the opt-out
  mechanism of art. 70-quater of Italian Law 633/1941.
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

# Identify ourselves clearly: real browser string + project name
BOT_NAME = "salary-observatory"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 " + BOT_NAME + "/0.1"
)
TIMEOUT = 20

# Minimum pause (seconds) between two requests to the same domain.
# ATS APIs are built to be queried: ~3 requests per second is fine there.
# Company websites get more care: 1 request per second.
DEFAULT_INTERVAL = 1.0
ATS_API_INTERVAL = 0.35
ATS_API_SUFFIXES = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com",
)

RETRY_STATUS = {429, 500, 502, 503, 504}

# If a service asks to wait longer than this (Retry-After), we stop:
# the host is blocked until then, across runs too (file on disk).
MAX_RETRY_WAIT = 60
BLOCKED_HOSTS_FILE = ROOT / "data" / "cache" / "blocked_hosts.json"
# More sensitive domains: longer pause between requests
SLOW_DOMAINS = {"apply.workable.com": 3.0}


class RobotsDisallowed(Exception):
    """robots.txt disallows the page: it is not downloaded."""


class TDMReserved(RobotsDisallowed):
    """The rights holder opts out of text and data mining (TDMRep): the content is not used."""


class HostBlocked(requests.RequestException):
    """The service asked us to stop (429 with a long Retry-After): it is not contacted."""


_session = None
_last_call = {}      # domain -> time of the last real request (not from cache)
_robots = {}         # "https://domain" -> RobotFileParser
_tdm_rules = {}      # "https://domain" -> tdmrep.json rules (list, empty if missing)

# Platforms with many subdomains on the same servers: one rate limit for the whole platform
RATE_GROUPS = ("myworkdayjobs.com", "myworkdaysite.com")


def init(use_cache: bool = False):
    """Creates the session. With use_cache=True responses stay on disk for CACHE_DAYS days."""
    global _session
    if use_cache:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _session = requests_cache.CachedSession(
            str(CACHE_PATH),
            backend="sqlite",
            expire_after=timedelta(days=CACHE_DAYS),
            allowable_methods=("GET", "HEAD", "POST"),   # POST is needed for Workday
            allowable_codes=(200, 404),                   # 404s too: useful when probing
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
    """Marks the host as blocked for `seconds` seconds (saved to disk)."""
    blocked = _load_blocked()
    blocked[host] = time.time() + seconds
    BLOCKED_HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    BLOCKED_HOSTS_FILE.write_text(json.dumps(blocked, indent=2), encoding="utf-8")


def blocked_until(host: str) -> float:
    """Time until which the host is blocked (0 if it is not)."""
    until = _load_blocked().get(host, 0)
    return until if until > time.time() else 0


def rate_key(host: str) -> str:
    """Rate limit key: the domain, or the whole platform (e.g. every Workday tenant)."""
    for group in RATE_GROUPS:
        if host.endswith(group):
            return group
    return host


_rate_lock = threading.Lock()   # the Workday probe uses several threads: the pace stays shared


def wait_turn(host: str):
    """Waits, if needed, before hitting the same domain (or platform) again.

    Each call books its own slot, so the minimum pause holds even with several threads.
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
    """Uses the Retry-After header if present (max 60s), otherwise 2, 4, 8... seconds."""
    value = response.headers.get("Retry-After", "")
    if value.isdigit():
        return min(int(value), 60)
    return 2 ** (attempt + 1)


def request(method: str, url: str, check_robots: bool = False, retries: int = 3, **kwargs):
    """HTTP request with rate limit and retries. Returns the Response (4xx included).

    Raises requests.RequestException if the network still fails after the retries,
    RobotsDisallowed if check_robots=True and robots.txt disallows the URL.
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
        raise HostBlocked(f"{host} asked us to stop: blocked until "
                          f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(blocked_until(host)))}")

    for attempt in range(retries + 1):
        wait_turn(host)
        try:
            response = session.request(method, url, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as e:
            mark_call(host)
            # domain does not exist (DNS): retrying is useless, it happens often when probing
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
                raise HostBlocked(f"{host}: 429 with Retry-After {asked}s, host blocked")
        if response.status_code in RETRY_STATUS and attempt < retries:
            time.sleep(_retry_delay(response, attempt))
            continue
        if check_robots and response.headers.get("tdm-reservation", "").strip() == "1":
            raise TDMReserved(url)      # reservation declared in the response: the content is not used
        return response


def get_json(url: str, **kwargs):
    """GET a JSON document. By default checks robots.txt and the TDM reservation (for every source)."""
    kwargs.setdefault("headers", {"Accept": "application/json"})
    kwargs.setdefault("check_robots", True)
    r = request("GET", url, **kwargs)
    r.raise_for_status()
    return r.json()


def post_json(url: str, payload: dict, **kwargs):
    """POST with a JSON response (Workday). Same checks as get_json."""
    kwargs.setdefault("headers", {"Accept": "application/json"})
    kwargs.setdefault("check_robots", True)
    r = request("POST", url, json=payload, **kwargs)
    r.raise_for_status()
    return r.json()


def allowed_by_robots(url: str) -> bool:
    """True if the domain's robots.txt allows downloading the URL.

    Rules (same as Google): missing robots.txt or 4xx = everything allowed;
    5xx or network error = everything disallowed, to be safe.
    """
    p = urlparse(url)
    root = f"{p.scheme}://{p.netloc}"
    if blocked_until(p.netloc.lower()):
        raise HostBlocked(f"{p.netloc} blocked: robots.txt cannot be checked now")
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
    """True if the site declares in /.well-known/tdmrep.json a TDM reservation covering the URL.

    TDMRep format (W3C Community Group): [{"location": "/path*", "tdm-reservation": 1}, ...]
    Missing or invalid file = no reservation declared this way.
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
