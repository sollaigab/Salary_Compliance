"""
Downloading job ads from the public ATS endpoints (Greenhouse, Lever, Ashby, Workday, ...).

One download function per ATS. For each function:
- every job ad has a `job_id` (used for deduplication) and some fields useful for the filters
  (department, contract type, workplace type, country, publication date);
- every call goes through lib/http.py (rate limit, retries, cache);
- the function returns (jobs, meta): meta says which instance (us/eu) hosts the board.
"""

import html
import re
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse

import requests

from lib import http
from lib.locations import in_scope, is_italy, scope_country
from lib.text import clean_html

# Keys present in every normalized job ad (None if the ATS does not provide them)
JOB_FIELDS = [
    "ats", "company", "job_id", "title", "location", "locations_all", "country",
    "department", "employment_type", "workplace_type", "posted_at", "url",
    "description", "salary_structured",
]


def _job(**fields) -> dict:
    job = dict.fromkeys(JOB_FIELDS)
    job.update(fields)
    return job


def _first_instance(urls_by_instance: dict):
    """Tries several instances (US, then EU) and returns (instance, json) of the first that answers."""
    last_err = None
    for instance, url in urls_by_instance.items():
        try:
            return instance, http.get_json(url)
        except requests.HTTPError as e:
            last_err = e
    raise last_err


# ---------- Greenhouse ----------
# Note: the main API also answers for many EU boards; boards.eu.greenhouse.io is the fallback
# (boards-api.eu.greenhouse.io does not exist: DNS error)
GREENHOUSE_HOSTS = {"us": "https://boards-api.greenhouse.io",
                    "eu": "https://boards.eu.greenhouse.io"}


def fetch_greenhouse(slug: str, instance: str = None):
    # Many European companies are on Greenhouse's EU instance (job-boards.eu.greenhouse.io)
    hosts = {instance: GREENHOUSE_HOSTS[instance]} if instance in GREENHOUSE_HOSTS else GREENHOUSE_HOSTS
    # 1) board data (light): whether it exists, on which instance, and the company name
    inst, board = _first_instance({i: f"{h}/v1/boards/{slug}" for i, h in hosts.items()})
    # 2) job ads with the full description
    data = http.get_json(f"{GREENHOUSE_HOSTS[inst]}/v1/boards/{slug}/jobs?content=true")

    jobs = []
    for j in data.get("jobs", []):
        offices = [o.get("name") for o in j.get("offices") or [] if o.get("name")]
        departments = [d.get("name") for d in j.get("departments") or [] if d.get("name")]
        jobs.append(_job(
            ats="greenhouse",
            company=slug,
            job_id=str(j.get("id")),
            title=j.get("title"),
            location=(j.get("location") or {}).get("name"),
            locations_all=offices or None,
            department=", ".join(departments) or None,
            posted_at=j.get("first_published") or j.get("updated_at"),
            url=j.get("absolute_url"),
            description=clean_html(j.get("content", "")),
            salary_structured=None,  # Greenhouse does not expose it on the public job board
        ))
    return jobs, {"instance": inst, "board_name": board.get("name")}


# ---------- Lever ----------
LEVER_HOSTS = {"us": "https://api.lever.co", "eu": "https://api.eu.lever.co"}


def _ms_to_iso(ms):
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_lever(slug: str, instance: str = None):
    # European companies are often on Lever's EU instance (jobs.eu.lever.co)
    hosts = {instance: LEVER_HOSTS[instance]} if instance in LEVER_HOSTS else LEVER_HOSTS
    inst, data = _first_instance({i: f"{h}/v0/postings/{slug}?mode=json" for i, h in hosts.items()})
    if not isinstance(data, list):
        raise ValueError(f"unexpected Lever response for {slug}")

    jobs = []
    for j in data:
        parts = [j.get("descriptionPlain", "")]
        for lst in j.get("lists", []):
            parts.append(lst.get("text", ""))
            parts.append(clean_html(lst.get("content", "")))
        parts.append(j.get("additionalPlain", ""))
        cat = j.get("categories") or {}
        jobs.append(_job(
            ats="lever",
            company=slug,
            job_id=j.get("id"),
            title=j.get("text"),
            location=cat.get("location"),
            locations_all=cat.get("allLocations"),
            country=j.get("country"),
            department=cat.get("team") or cat.get("department"),
            employment_type=cat.get("commitment"),
            workplace_type=j.get("workplaceType"),
            posted_at=_ms_to_iso(j.get("createdAt")),
            url=j.get("hostedUrl"),
            description=re.sub(r"\s+", " ", " ".join(p for p in parts if p)).strip(),
            salary_structured=j.get("salaryRange"),  # {min, max, currency, interval} if present
        ))
    return jobs, {"instance": inst, "board_name": None}


# ---------- Ashby ----------
def fetch_ashby(slug: str, instance: str = None):
    data = http.get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    )
    if "jobs" not in data:
        raise ValueError(f"Ashby board {slug} not found")

    jobs = []
    for j in data.get("jobs", []):
        comp = j.get("compensation") or {}
        address = ((j.get("address") or {}).get("postalAddress") or {})
        secondary = [s.get("location") for s in j.get("secondaryLocations") or [] if s.get("location")]
        workplace = j.get("workplaceType") or ("Remote" if j.get("isRemote") else None)
        jobs.append(_job(
            ats="ashby",
            company=slug,
            job_id=j.get("id"),
            title=j.get("title"),
            location=j.get("location"),
            locations_all=[j.get("location")] + secondary if secondary else None,
            country=address.get("addressCountry"),
            department=j.get("department") or j.get("team"),
            employment_type=j.get("employmentType"),
            workplace_type=workplace,
            posted_at=j.get("publishedAt"),
            url=j.get("jobUrl"),
            description=j.get("descriptionPlain") or clean_html(j.get("descriptionHtml", "")),
            salary_structured=comp.get("compensationTierSummary") or comp.get("summaryComponents"),
        ))
    return jobs, {"instance": None, "board_name": None}


# ---------- Workday ----------
LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
NOT_WORKDAY_SITES = {"wday", "job", "jobs", "details", "login", "apply"}


def parse_workday_url(url: str) -> tuple[str, str, str | None]:
    """Gets (host, tenant, site) from a Workday URL. Site can be None.

    https://acme.wd103.myworkdayjobs.com/it-IT/AcmeCareers            -> (host, 'acme', 'AcmeCareers')
    https://x.wd3.myworkdayjobs.com/wday/cxs/x/Ext/jobs               -> (host, 'x', 'Ext')
    https://wd3.myworkdaysite.com/en-US/recruiting/acme/GroupSite     -> (host, 'acme', 'GroupSite')
    """
    if not url.startswith("http"):
        url = "https://" + url
    p = urlparse(url)
    host = p.netloc                      # e.g. acme.wd3.myworkdayjobs.com
    tenant = host.split(".")[0]          # e.g. acme
    segments = [s for s in p.path.split("/") if s]
    if len(segments) >= 4 and segments[:2] == ["wday", "cxs"]:
        return host, segments[2], segments[3]
    segments = [s for s in segments if not LOCALE_RE.match(s)]   # drops "it-IT", "en-US"...
    # myworkdaysite.com variant: the tenant is in the path, after "recruiting"
    if host.endswith("myworkdaysite.com"):
        if len(segments) >= 2 and segments[0] == "recruiting":
            return host, segments[1], segments[2] if len(segments) >= 3 else None
        return host, None, None
    site = segments[0] if segments else None
    if site in NOT_WORKDAY_SITES:
        site = None
    return host, tenant, site


def workday_board_url(host: str, tenant: str, site: str) -> str:
    """Public career page URL (canonical form stored in the registry)."""
    if host.endswith("myworkdaysite.com"):
        return f"https://{host}/recruiting/{tenant}/{site}"
    return f"https://{host}/{site}"


def workday_sites_from_robots(host: str, retries: int = 3) -> list[str]:
    """The tenant's career site names, read from the Sitemap lines of Workday's robots.txt.

    Existing tenant: 200 with "Sitemap: https://{host}/{Site}/siteMap.xml".
    Missing tenant: 422. It costs a single request, so it is also the fastest way to probe.
    """
    r = http.request("GET", f"https://{host}/robots.txt", retries=retries)
    if r.status_code != 200:
        return []
    sites = re.findall(r"Sitemap:\s*https?://[^/\s]+/([\w-]+)/siteMap\.xml", r.text, re.IGNORECASE)
    return list(dict.fromkeys(sites))


def best_workday_site(host: str, sites: list[str]) -> str:
    """Among several sites of the same tenant, picks the one with the most ads in the observed countries
    (then the most ads).

    E.g. the first site listed is for UK temporary workers, the right one is the one for
    experienced professionals. It costs one light request per site.
    """
    tenant = host.split(".")[0]
    best, best_score = sites[0], (-1, -1)
    for site in sites:
        try:
            data = http.post_json(f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
                                  {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""})
        except (requests.RequestException, ValueError):
            continue
        scope = workday_scope_count(data.get("facets"))
        if scope is None:   # no country filter: count the locations on the first page
            scope = sum(in_scope(jp.get("locationsText")) for jp in data.get("jobPostings", []))
        score = (scope, data.get("total", 0))
        if score > best_score:
            best, best_score = site, score
    return best


def discover_workday_site(host: str, retries: int = 3) -> str | None:
    """Finds the Workday site name: first from robots.txt, then from the home page redirect.

    (Since October 2026 the home page answers 406 to non-browser clients, so the redirect often fails.)
    """
    sites = workday_sites_from_robots(host, retries)
    if len(sites) == 1:
        return sites[0]
    if sites:
        return best_workday_site(host, sites[:8])
    r = http.request("GET", f"https://{host}/", retries=retries, allow_redirects=True,
                     check_robots=True)
    if r.status_code >= 400 or urlparse(r.url).netloc != host:
        return None
    _, _, site = parse_workday_url(r.url)
    return site


def _workday_base(career_url: str):
    host, tenant, site = parse_workday_url(career_url)
    if not tenant:
        raise ValueError(f"unrecognized Workday URL: {career_url}")
    if not site:
        site = discover_workday_site(host)
        if not site:
            raise ValueError(
                "Workday site name not found: copy the full career page URL "
                f"from the browser (e.g. https://{host}/it-IT/<SiteName>)"
            )
    return host, tenant, site, f"https://{host}/wday/cxs/{tenant}/{site}"


def workday_country_counts(facets: list) -> dict:
    """Counts of the observed countries in Workday's filters (facets): {'IT': 12, 'DE': 30, ...}.
    A country can appear in several filters (country, location): the largest count is kept."""
    counts = {}

    def walk(nodes):
        for node in nodes or []:
            cc = scope_country(None, str(node.get("descriptor", "")).strip())
            if cc and "count" in node:
                counts[cc] = max(counts.get(cc, 0), node["count"])
            walk(node.get("values"))

    walk(facets)
    return counts


def workday_italy_count(facets: list):
    """Count of 'Italy'/'Italia' in Workday's filters (facets), None if the site has no such filter."""
    return workday_country_counts(facets).get("IT")


def workday_scope_count(facets: list):
    """Ads in the observed countries according to Workday's filters, None if there is no country filter."""
    counts = workday_country_counts(facets)
    return sum(counts.values()) if counts else None


def workday_scope_facet(facets: list):
    """Workday filter for the observed countries, to pass as appliedFacets: {"locationCountry": ["<id>", ...]}.

    None if the site has no country filter (then the location text is filtered instead).
    """
    found = {}

    def walk(nodes, parent_param):
        for node in nodes or []:
            param = node.get("facetParameter") or parent_param
            cc = scope_country(None, str(node.get("descriptor", "")).strip())
            if cc and node.get("id") and param:
                found.setdefault(param, []).append((node.get("count", 0), node["id"]))
            walk(node.get("values"), param)

    walk(facets, None)
    if not found:
        return None
    # the filter that covers the most ads (usually the country filter)
    param, values = max(found.items(), key=lambda kv: sum(c for c, _ in kv[1]))
    return {param: [value_id for _, value_id in values]}


def workday_summary(career_url: str, n_details: int = 2) -> dict:
    """First page of the Workday board: total ads, ads in Italy and in all observed countries, a few texts.

    Used during discovery: it costs 1 + n_details requests instead of downloading everything.
    """
    host, tenant, site, base = _workday_base(career_url)
    data = http.post_json(f"{base}/jobs",
                          {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""})
    postings = data.get("jobPostings", [])
    texts = [jp.get("title", "") for jp in postings]
    for jp in postings[:n_details]:
        try:
            info = http.get_json(f"{base}{jp['externalPath']}").get("jobPostingInfo", {})
            texts.append(clean_html(info.get("jobDescription", ""))[:3000])
        except (requests.RequestException, KeyError, ValueError):
            pass
    total = data.get("total", 0)
    italy = workday_italy_count(data.get("facets"))
    scope = workday_scope_count(data.get("facets"))
    if scope is not None and italy is None:
        italy = 0     # the country filter exists and Italy is not in it
    italy_estimated = False
    if scope is None and total:
        # No "country" filter: count the Italian locations in the list,
        # at most 10 pages (200 ads). Beyond that, estimate proportionally.
        locations = [jp.get("locationsText") for jp in postings]
        offset = 20
        while offset < min(total, 200):
            page = http.post_json(f"{base}/jobs", {"appliedFacets": {}, "limit": 20,
                                                   "offset": offset, "searchText": ""})
            if not page.get("jobPostings"):
                break
            locations += [jp.get("locationsText") for jp in page["jobPostings"]]
            offset += 20
        n_italy = sum(is_italy(loc) for loc in locations)
        n_scope = sum(in_scope(loc) for loc in locations)
        if len(locations) >= total:
            italy, scope = n_italy, n_scope
        elif locations:
            italy = round(n_italy / len(locations) * total)
            scope = round(n_scope / len(locations) * total)
            italy_estimated = True
    return {
        "url": workday_board_url(host, tenant, site),
        "total": total,
        "italy": italy,
        "scope": scope,
        "italy_estimated": italy_estimated,
        "texts": texts,
    }


def fetch_workday(career_url: str, max_jobs: int = None, location_regex: str = None,
                  search: str = "", scope_only: bool = False):
    """scope_only=True: uses Workday's country filter (the observed countries) if present, otherwise keeps
    locations in those countries and multiple ones ("3 Locations"), to be checked in the detail."""
    host, tenant, site, base = _workday_base(career_url)
    loc_re = re.compile(location_regex, re.IGNORECASE) if location_regex else None
    facets = {}
    if scope_only:
        first = http.post_json(f"{base}/jobs", {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": search})
        facets = workday_scope_facet(first.get("facets")) or {}
        if not facets:
            multi = re.compile(r"^\d+ (?:locations|sedi|località|standorte|lieux|locaties)$", re.IGNORECASE)
            keep = lambda text: in_scope(text) or bool(multi.match(text or ""))

    # 1) Job list, paginated (Workday returns at most 20 results per call).
    # With the country filter the cap applies to each country separately: on a global site the
    # ads of one country (e.g. 1,200 in France) must not crowd out those of another (Italy).
    totals = []    # ads on the site for each filter value (in the returned metadata)

    def list_postings(applied: dict) -> list:
        postings, offset, total = [], 0, None
        while max_jobs is None or len(postings) < max_jobs:
            data = http.post_json(f"{base}/jobs", {"appliedFacets": applied, "limit": 20,
                                                   "offset": offset, "searchText": search})
            if total is None:
                total = data.get("total", 0)
                totals.append(total)
            page = data.get("jobPostings", [])
            if not page:
                break
            for jp in page:
                text = jp.get("locationsText") or ""
                if loc_re and not loc_re.search(text):
                    continue
                if scope_only and not facets and not keep(text):
                    continue
                postings.append(jp)
            offset += 20
            if offset >= total:
                break
        return postings if max_jobs is None else postings[:max_jobs]

    if facets:
        (param, values), = facets.items()
        postings = {}
        for value in values:
            for jp in list_postings({param: [value]}):
                postings.setdefault(jp.get("externalPath"), jp)   # an ad in two countries: once
        postings = list(postings.values())
    else:
        postings = list_postings({})

    # 2) Detail of each ad (one call is needed to get the full description)
    jobs = []
    for jp in postings:
        path = jp.get("externalPath")
        if not path:
            continue
        try:
            info = http.get_json(f"{base}{path}").get("jobPostingInfo", {})
        except requests.RequestException:
            continue
        additional = info.get("additionalLocations") or []
        jobs.append(_job(
            ats="workday",
            company=tenant,
            job_id=info.get("jobReqId") or info.get("id") or path,
            title=info.get("title") or jp.get("title"),
            location=info.get("location") or jp.get("locationsText"),
            locations_all=[info.get("location")] + additional if additional else None,
            country=(info.get("country") or {}).get("descriptor"),
            employment_type=info.get("timeType"),
            workplace_type=info.get("remoteType"),
            posted_at=info.get("startDate"),
            url=info.get("externalUrl") or workday_board_url(host, tenant, site) + path,
            description=clean_html(info.get("jobDescription", "")),
            salary_structured=None,   # Workday exposes no standard structured salary
        ))
    return jobs, {"instance": None, "board_name": None, "total": sum(totals),
                  "url": workday_board_url(host, tenant, site)}


# ---------- Workable ----------
# Public "widget" API documented by Workable; the robots.txt of apply.workable.com allows everything.
def fetch_workable(slug: str, instance: str = None):
    data = http.get_json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true",
                         check_robots=True)
    jobs = []
    for j in data.get("jobs", []):
        places = [", ".join(p for p in (l.get("city"), l.get("region"), l.get("country")) if p)
                  for l in j.get("locations") or []]
        city_country = ", ".join(p for p in (j.get("city"), j.get("state"), j.get("country")) if p)
        jobs.append(_job(
            ats="workable",
            company=slug,
            job_id=j.get("shortcode"),
            title=j.get("title"),
            location=city_country or None,
            locations_all=places or None,
            country=j.get("country"),
            department=j.get("department") or None,
            employment_type=j.get("employment_type") or None,
            workplace_type="remote" if j.get("telecommuting") else None,
            posted_at=j.get("published_on") or j.get("created_at"),
            url=j.get("url") or j.get("shortlink"),
            description=clean_html(j.get("description", "")),
            salary_structured=None,   # the public widget does not expose pay
        ))
    return jobs, {"instance": None, "board_name": data.get("name")}


# ---------- Personio ----------
# Public XML feed documented by Personio for job integrations; robots.txt: Allow /
PERSONIO_DOMAINS = ["jobs.personio.de", "jobs.personio.com"]


def fetch_personio(slug: str, instance: str = None):
    import xml.etree.ElementTree as ET

    last_err = None
    for domain in PERSONIO_DOMAINS:
        base = f"https://{slug}.{domain}"
        # no redirects: a missing slug redirects to the personio.com marketing site
        r = http.request("GET", f"{base}/xml", check_robots=True, allow_redirects=False)
        if r.status_code == 200 and "<workzag-jobs" in r.text:
            break
        last_err = requests.HTTPError(f"Personio {slug}: HTTP {r.status_code}", response=r)
    else:
        raise last_err

    jobs = []
    root = ET.fromstring(r.content)
    for p in root.findall("position"):
        text = lambda tag: (p.findtext(tag) or "").strip() or None
        offices = [o.text.strip() for o in p.findall("additionalOffices/office") if o.text]
        parts = []
        for d in p.findall("jobDescriptions/jobDescription"):
            parts += [d.findtext("name") or "", clean_html(d.findtext("value") or "")]
        jobs.append(_job(
            ats="personio",
            company=slug,
            job_id=text("id"),
            title=text("name"),
            location=text("office"),
            locations_all=[text("office")] + offices if offices else None,
            department=text("department"),
            employment_type=" ".join(x for x in (text("employmentType"), text("schedule")) if x) or None,
            posted_at=text("createdAt"),
            url=f"{base}/job/{text('id')}",
            description=re.sub(r"\s+", " ", " ".join(parts)).strip(),
            salary_structured=None,
        ))
    board_name = jobs and root.findtext("position/subcompany")
    return jobs, {"instance": None, "board_name": board_name or None}


# ---------- Recruitee ----------
# Public job API (Careers Site API, documented by Recruitee); robots.txt: Allow /
def fetch_recruitee(slug: str, instance: str = None):
    data = http.get_json(f"https://{slug}.recruitee.com/api/offers/", check_robots=True)
    jobs = []
    for j in data.get("offers", []):
        salary = j.get("salary") or {}
        workplace = "remote" if j.get("remote") else "hybrid" if j.get("hybrid") else \
            "onsite" if j.get("on_site") else None
        places = [l.get("name") or ", ".join(p for p in (l.get("city"), l.get("country")) if p)
                  for l in j.get("locations") or []]
        jobs.append(_job(
            ats="recruitee",
            company=slug,
            job_id=str(j.get("id")),
            title=j.get("title"),
            location=j.get("location"),
            locations_all=[p for p in places if p] or None,
            country=j.get("country_code"),
            department=j.get("department"),
            employment_type=j.get("employment_type_code"),
            workplace_type=workplace,
            posted_at=j.get("published_at"),
            url=j.get("careers_url"),
            description=clean_html(f"{j.get('description', '')} {j.get('requirements', '')}"),
            salary_structured=salary if salary.get("min") or salary.get("max") else None,
        ))
    company_name = data["offers"][0].get("company_name") if data.get("offers") else None
    return jobs, {"instance": None, "board_name": company_name}


# ---------- Oracle Recruiting Cloud ----------
# Public JSON endpoint used by the "CandidateExperience" career page (no login),
# similar to the Workday endpoint. robots.txt and the TDM reservation are checked by http.get_json.
ORACLE_SITE_RE = re.compile(r"/sites/([\w-]+)")
ORACLE_DEFAULT_SITES = ["CX_1", "CX", "CX_2"]
ORACLE_PAGE = 25


def parse_oracle_url(url: str) -> tuple[str, str | None]:
    """'https://x.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/it/sites/CX_1/job/5' -> (host, 'CX_1')."""
    if not url.startswith("http"):
        url = "https://" + url
    p = urlparse(url)
    m = ORACLE_SITE_RE.search(p.path)
    return p.netloc, m.group(1) if m else None


def oracle_board_url(host: str, site: str) -> str:
    return f"https://{host}/hcmUI/CandidateExperience/it/sites/{site}"


def _oracle_page(host: str, site: str, offset: int) -> dict:
    url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
           f"?onlyData=true&expand=requisitionList.secondaryLocations"
           f"&finder=findReqs;siteNumber={site},limit={ORACLE_PAGE},offset={offset},sortBy=POSTING_DATES_DESC")
    items = http.get_json(url).get("items") or [{}]
    return items[0]


def _oracle_site(url: str) -> tuple[str, str]:
    """Host and site number; if the URL does not say, tries the standard names (CX_1, CX, CX_2)."""
    host, site = parse_oracle_url(url)
    for candidate in [site] if site else ORACLE_DEFAULT_SITES:
        try:
            page = _oracle_page(host, candidate, 0)
        except (requests.RequestException, ValueError):
            continue
        if "requisitionList" in page:
            return host, candidate
    raise ValueError(f"Oracle site not found on {host}")


def _oracle_countries(req: dict) -> list:
    return [req.get("PrimaryLocationCountry")] + [s.get("CountryCode") for s in req.get("secondaryLocations") or []]


def _oracle_is_italy(req: dict) -> bool:
    return any(is_italy(None, c) for c in _oracle_countries(req)) or is_italy(req.get("PrimaryLocation"))


def _oracle_in_scope(req: dict) -> bool:
    return any(in_scope(None, c) for c in _oracle_countries(req)) or in_scope(req.get("PrimaryLocation"))


def oracle_summary(url: str, max_pages: int = 8) -> dict:
    """Total ads, ads in Italy and in the observed countries (exact up to max_pages*25 ads, estimated beyond)."""
    host, site = _oracle_site(url)
    first = _oracle_page(host, site, 0)
    total = first.get("TotalJobsCount") or 0
    reqs = list(first.get("requisitionList") or [])
    offset = ORACLE_PAGE
    while offset < min(total, max_pages * ORACLE_PAGE):
        reqs += _oracle_page(host, site, offset).get("requisitionList") or []
        offset += ORACLE_PAGE
    n_italy = sum(_oracle_is_italy(r) for r in reqs)
    n_scope = sum(_oracle_in_scope(r) for r in reqs)
    estimated = bool(reqs) and len(reqs) < total
    italy = round(n_italy / len(reqs) * total) if estimated else n_italy
    scope = round(n_scope / len(reqs) * total) if estimated else n_scope
    return {"url": oracle_board_url(host, site), "total": total, "italy": italy, "scope": scope,
            "italy_estimated": estimated, "texts": [r.get("Title", "") for r in reqs[:25]]}


def fetch_oracle(url: str, instance: str = None, details: bool = True, scope_only: bool = False):
    host, site = _oracle_site(url)
    reqs, offset, total = [], 0, None
    while total is None or offset < total:
        page = _oracle_page(host, site, offset)
        total = page.get("TotalJobsCount") or 0
        batch = page.get("requisitionList") or []
        if not batch:
            break
        reqs += batch
        offset += ORACLE_PAGE
    if scope_only:   # the detail costs one request: only for ads in the observed countries
        reqs = [r for r in reqs if _oracle_in_scope(r)]

    jobs = []
    for r in reqs:
        description = ""
        if details:   # one request per ad: only needed by the crawler (Phase 2)
            try:
                d = http.get_json(
                    f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
                    f'?expand=all&onlyData=true&finder=ById;Id="{r["Id"]}",siteNumber={site}')
                item = (d.get("items") or [{}])[0]
                description = clean_html(" ".join(item.get(k) or "" for k in (
                    "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")))
            except (requests.RequestException, ValueError):
                pass
        secondary = [s.get("Name") for s in r.get("secondaryLocations") or [] if s.get("Name")]
        jobs.append(_job(
            ats="oracle",
            company=host,
            job_id=str(r.get("Id")),
            title=r.get("Title"),
            location=r.get("PrimaryLocation"),
            locations_all=[r.get("PrimaryLocation")] + secondary if secondary else None,
            country=r.get("PrimaryLocationCountry"),
            department=r.get("JobFamily") or r.get("JobFunction"),
            employment_type=" ".join(x for x in (r.get("ContractType"), r.get("JobSchedule")) if x) or None,
            workplace_type=r.get("WorkplaceTypeCode"),
            posted_at=r.get("PostedDate"),
            url=f"{oracle_board_url(host, site)}/job/{r.get('Id')}",
            description=description,
            salary_structured=None,
        ))
    return jobs, {"instance": None, "board_name": None, "total": total,
                  "url": oracle_board_url(host, site)}


# ---------- Teamtailor ----------
# Public RSS feed of the career site (/jobs.rss), also on the company's own domain.
def teamtailor_root(slug_or_url: str) -> str:
    """'acme' -> https://acme.teamtailor.com ; 'https://jobs.acme.it/x' -> https://jobs.acme.it"""
    if "." in slug_or_url or slug_or_url.startswith("http"):
        url = slug_or_url if slug_or_url.startswith("http") else "https://" + slug_or_url
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    return f"https://{slug_or_url}.teamtailor.com"


def fetch_teamtailor(slug_or_url: str, instance: str = None):
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    root_url = teamtailor_root(slug_or_url)
    r = http.request("GET", f"{root_url}/jobs.rss", check_robots=True)
    r.raise_for_status()
    if "<rss" not in r.text[:500]:
        raise ValueError(f"Teamtailor feed not found on {root_url}")

    local = lambda tag: tag.rsplit("}", 1)[-1]   # drops the namespace (tt:city -> city)
    jobs = []
    for item in ET.fromstring(r.content).iter("item"):
        fields, places, countries = {}, [], []
        for child in item.iter():
            name = local(child.tag)
            if name == "location":
                city = next((c.text for c in child if local(c.tag) == "city" and c.text), None)
                country = next((c.text for c in child if local(c.tag) == "country" and c.text), None)
                places.append(", ".join(x for x in (city, country) if x))
                countries.append(country)
            elif child.text and name not in fields:
                fields[name] = child.text.strip()
        posted = None
        if fields.get("pubDate"):
            try:
                posted = parsedate_to_datetime(fields["pubDate"]).strftime("%Y-%m-%dT%H:%M:%S%z")
            except (TypeError, ValueError):
                pass
        link = fields.get("link", "")
        jobs.append(_job(
            ats="teamtailor",
            company=root_url,
            job_id=re.search(r"/jobs/(\d+)", link).group(1) if re.search(r"/jobs/(\d+)", link) else fields.get("guid"),
            title=fields.get("title"),
            location=places[0] if places else None,
            locations_all=places or None,
            country=next((c for c in countries if c), None),
            department=fields.get("department"),
            workplace_type=fields.get("remoteStatus"),
            posted_at=posted,
            url=link,
            description=clean_html(fields.get("description", "")),
            salary_structured=None,
        ))
    return jobs, {"instance": None, "board_name": None, "url": root_url}


# ---------- SuccessFactors ("Recruiting Marketing" career sites) ----------
# No public API, but the career site publishes a sitemap.xml with every ad and, in each ad,
# schema.org/JobPosting structured data (meant for search engines).
# Only pages allowed by robots.txt and without a TDM reservation are read, one request at a time.
SF_JOB_URL_RE = re.compile(r"<loc>\s*([^<\s]*/job/[^<\s]*)\s*</loc>")
SF_SUBSITEMAP_RE = re.compile(r"<sitemap>\s*<loc>\s*([^<\s]+)\s*</loc>")
SF_SKIP_RE = re.compile(r"talent-?community|candidatura-spontanea|spontaneous", re.IGNORECASE)


def sf_root(url: str) -> str:
    p = urlparse(url if url.startswith("http") else "https://" + url)
    return f"{p.scheme}://{p.netloc}"


def sf_job_urls(site_url: str) -> list[str]:
    """URLs of every ad from the career site sitemap (follows nested sitemaps)."""
    root = sf_root(site_url)
    queue, seen, urls = [f"{root}/sitemap.xml"], set(), []
    while queue and len(seen) < 10:
        sitemap = queue.pop(0)
        if sitemap in seen:
            continue
        seen.add(sitemap)
        r = http.request("GET", sitemap, check_robots=True)
        if r.status_code != 200:
            continue
        urls += SF_JOB_URL_RE.findall(r.text)
        queue += [u for u in SF_SUBSITEMAP_RE.findall(r.text) if urlparse(u).netloc == urlparse(root).netloc]
    urls = [html.unescape(u) for u in dict.fromkeys(urls)]
    return [u for u in urls if not SF_SKIP_RE.search(u)]


def parse_sf_job_page(page: str, url: str = "") -> dict:
    """Reads the schema.org/JobPosting data (microdata) of a SuccessFactors job page."""
    def meta(prop):
        m = re.search(rf'<meta\s+itemprop="{prop}"\s+content="([^"]*)"', page)
        return html.unescape(m.group(1)).strip() or None if m else None
    title = re.search(r'itemprop="title"[^>]*>(.*?)</span>', page, re.S)
    if not title:   # some sites leave the title out of the microdata: the page's own og:title has it
        title = re.search(r'<meta\s+property="og:title"\s+content="([^"]*)"', page)
    desc = element_inner_html(page, 'itemprop="description"')
    posted = meta("datePosted")
    if posted:
        try:
            posted = datetime.strptime(posted, "%a %b %d %H:%M:%S %Z %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    city, region, country = meta("addressLocality"), meta("addressRegion"), meta("addressCountry")
    location = ", ".join(x for x in (city, region, country) if x) or None
    if not location and "/job/" in url:
        # some sites fill the location via JavaScript: take it from the URL (/job/Lombardia-...-Mantova/123/)
        slug = unquote(url.split("/job/", 1)[1].split("/")[0])
        location = re.sub(r"[-_]+", " ", slug)
    return {
        "title": clean_html(title.group(1)) if title else None,
        "location": location,
        "country": country,
        "posted_at": posted,
        "employment_type": meta("employmentType"),
        "company": meta("hiringOrganization"),
        "description": clean_html(desc)[:20000] if desc else "",
    }


def element_inner_html(page: str, attribute: str) -> str | None:
    """Inner HTML of the first element carrying `attribute`, up to ITS closing tag (nested tags counted).

    A regex up to a guessed end marker ran on into the rest of the page (buttons, JSON, scripts)
    on the sites where that marker is missing.
    """
    start = page.find(attribute)
    if start < 0:
        return None
    open_at = page.rfind("<", 0, start)
    tag = re.match(r"<(\w+)", page[open_at:]).group(1).lower()
    body_start = page.find(">", start) + 1
    depth = 1
    tag_re = re.compile(rf"<(/?){tag}\b[^>]*?(/?)>", re.IGNORECASE)
    for m in tag_re.finditer(page, body_start):
        if m.group(2):          # self-closing <tag/>
            continue
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return page[body_start:m.start()]
    return page[body_start:]    # never closed: keep the rest, as before


def successfactors_summary(site_url: str, sample: int = 8) -> dict:
    """Total ads (from the sitemap) and an estimate of those in Italy and in the observed countries,
    from a sample of pages."""
    urls = sf_job_urls(site_url)
    if not urls:
        raise ValueError(f"no job ads in the sitemap of {sf_root(site_url)}")
    picked = urls[:: max(1, len(urls) // sample)][:sample]   # sample spread across the whole list
    pages = []
    for u in picked:
        try:
            r = http.request("GET", u, check_robots=True)
            if r.status_code == 200:
                pages.append(parse_sf_job_page(r.text, u))
        except (requests.RequestException, http.RobotsDisallowed):
            continue
    n_italy = sum(is_italy(p["location"], p["country"]) for p in pages)
    n_scope = sum(in_scope(p["location"], p["country"]) for p in pages)
    estimated = len(pages) < len(urls)
    italy = round(n_italy / len(pages) * len(urls)) if pages and estimated else n_italy
    scope = round(n_scope / len(pages) * len(urls)) if pages and estimated else n_scope
    return {"url": sf_root(site_url), "total": len(urls), "italy": italy if pages else None,
            "scope": scope if pages else None,
            "italy_estimated": estimated,
            "texts": [f"{p['company'] or ''} {p['title'] or ''} {p['description'][:2000]}" for p in pages]}


def fetch_successfactors(site_url: str, instance: str = None, max_jobs: int = None,
                         scope_only: bool = False):
    urls = sf_job_urls(site_url)
    if scope_only:
        # the location is almost always in the URL (/job/Milano-Data-Analyst/123/): download only the ads in
        # the observed countries. If no URL has a recognizable location, download all (up to max_jobs) and filter later.
        kept = [u for u in urls if in_scope(unquote(u.split("/job/", 1)[-1]).replace("-", " "))]
        if kept and max_jobs:
            # the cap applies to each country separately (see fetch_workday)
            by_country = {}
            for u in kept:
                cc = scope_country(unquote(u.split("/job/", 1)[-1]).replace("-", " "))
                by_country.setdefault(cc, []).append(u)
            kept = [u for country_urls in by_country.values() for u in country_urls[:max_jobs]]
            max_jobs = None
        urls = kept or urls
    jobs = []
    for u in urls[:max_jobs]:
        try:
            r = http.request("GET", u, check_robots=True)
        except (requests.RequestException, http.RobotsDisallowed):
            continue
        if r.status_code != 200:
            continue
        p = parse_sf_job_page(r.text, u)
        m = re.search(r"/(\d+)/?$", u)
        jobs.append(_job(ats="successfactors", company=sf_root(site_url),
                         job_id=m.group(1) if m else u, title=p["title"], location=p["location"],
                         country=p["country"], employment_type=p["employment_type"],
                         posted_at=p["posted_at"], url=u, description=p["description"]))
    return jobs, {"instance": None, "board_name": None, "url": sf_root(site_url)}


FETCHERS = {
    "oracle": fetch_oracle,
    "teamtailor": fetch_teamtailor,
    "successfactors": fetch_successfactors,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "workday": fetch_workday,
    "workable": fetch_workable,
    "personio": fetch_personio,
    "recruitee": fetch_recruitee,
}

# ATSs detected but NOT downloadable without breaking terms or robots.txt (do not add them to the crawler):
# - smartrecruiters: api.smartrecruiters.com/robots.txt -> "User-agent: * Disallow: /"
# - avature, taleo, icims, inrecruiting, altamira, phenom, eightfold, cornerstone: no public feed;
#   it would mean scraping the career pages
# - teamtailor: the official API needs a company key, so only the public RSS feed is used


def count_italy(jobs: list) -> int:
    """Job ads with at least one location in Italy."""
    n = 0
    for j in jobs:
        places = [j["location"]] + list(j["locations_all"] or [])
        if is_italy(None, j["country"]) or any(is_italy(p) for p in places):
            n += 1
    return n


def count_scope(jobs: list) -> int:
    """Job ads with at least one location in the observed countries."""
    n = 0
    for j in jobs:
        places = [j["location"]] + list(j["locations_all"] or [])
        if in_scope(None, j["country"]) or any(in_scope(p) for p in places):
            n += 1
    return n
