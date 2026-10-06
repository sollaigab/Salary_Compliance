# Starting brief for Claude Code

This is the brief I gave Claude Code at the start of the project, translated from Italian. Company names and slugs are replaced with placeholders. The project then changed along the way: the public output became sector aggregates only, and more ATSs were added.

---

# Context

I am building an observatory of pay in job ads in Italy. Since 7 June 2026 (Legislative Decree 96/2026) job ads must state the starting pay or the pay range. I want to measure how many ads actually state it, how many use vague wording ("commensurate with experience", "according to the national collective agreement") and what salaries are offered by role and city.

My profile: I am strong in SQL and BigQuery, less so in Python. Write readable code, commented in Italian, without needless abstractions.

## What already exists: `test_ats.py`, which you are looking at now. Create a new repo for the project

A Python script (`requests` only) that queries the public job board APIs of the ATSs and normalizes the ads into a single format (`ats, company, title, location, url, description, salary_structured, salary_in_text, salary_vague`):

- Greenhouse: `boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`, with a fallback to the EU instance (`boards-api.eu.greenhouse.io`). It does not expose structured pay.
- Lever: `api.lever.co/v0/postings/{slug}?mode=json`, with an EU fallback (`api.eu.lever.co`). Sometimes it has `salaryRange`.
- Ashby: `api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true`. It has structured pay.
- Workday: the career site's internal endpoint. `POST https://{tenant}.wdN.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` (paginated, 20 at a time), then `GET .../wday/cxs/{tenant}/{site}{externalPath}` for the full description. If the site name is missing, it gets it from the redirect of the domain's home page.
- Salary extraction with a regex (`SALARY_RE`) and detection of vague wording (`VAGUE_SALARY_RE`).

Status: tested and working, including on a large consulting firm (Workday). Slugs already verified: Company A → Lever EU `company-a`; Company B → Greenhouse EU `company-b-srl`; Company C → Workday `https://company-c.wd103.myworkdayjobs.com/it-IT/CompanyCCareers`.

Reuse these fetch functions instead of rewriting them: move them into a module and import them.

# Goal

Collect all the job ads of the main companies operating in Italy, both tech and non-tech (banks, insurance, consulting and Big Four, marketing and media agencies, energy, telco, fashion and luxury, retail, industry, fintech and startups), and build a database that can be queried.

The first problem to solve is that today I find the slugs by hand. The top priority is to automate finding which ATS each company uses and its slug or URL. The data coming from the ATSs is already good.

# Phase 1 (the priority): automatic discovery of ATS and slug

## 1a. Seed list of companies

Create `data/companies_seed.csv` with the columns `name, website, sector, is_tech, source`. Fill it with a first reasoned list of about 150 companies. Include large listed companies (FTSE MIB and Mid Cap), the largest banks and insurers, the Big Four and consulting firms, advertising networks and agencies, multinationals with major offices in Italy, and the best-known Italian scaleups and startups. The file must be easy to extend by hand. Mark as "to verify" the websites you are not sure about.

## 1b. ATS detection (`discover_ats.py`)

For each company try these methods in order and stop at the first valid result:

1. Fingerprint on the career page. Download the website, find the career page (a link with text or path such as `careers`, `carriere`, `lavora-con-noi`, `jobs`, `work-with-us`) and scan the HTML, iframes, scripts and links for ATS fingerprints:
   - `boards.greenhouse.io/{slug}`, `job-boards.greenhouse.io/{slug}`, `job-boards.eu.greenhouse.io/{slug}`, `greenhouse.io/embed/job_board?for={slug}`
   - `jobs.lever.co/{slug}`, `jobs.eu.lever.co/{slug}`
   - `jobs.ashbyhq.com/{slug}`
   - `{tenant}.wd{N}.myworkdayjobs.com/{locale?}/{site}`
   - Also recognize the ATSs that are not supported yet, to measure coverage: SmartRecruiters, Workable, Recruitee, Teamtailor, Personio, SAP SuccessFactors, Oracle/Taleo, iCIMS, Avature, inRecruiting/Intervieweb (very common in Italy). Save them with `supported = false`.
2. Slug probing. Generate candidate slugs from the name (lowercase, no spaces, with hyphens, with or without `spa`, `srl`, `italia`, `group`). Try the public endpoints of Greenhouse, Lever and Ashby, US and EU instances. For Workday try `{tenant}.wd{1,3,5,103}.myworkdayjobs.com` and find the site through the redirect.
3. Validation. A candidate is valid only if the endpoint answers with at least one job ad and the company name matches. Check the name in the descriptions or URLs, because a generic slug can belong to a company with the same name.

Output in `ats_registry` with the columns: `company, sector, is_tech, ats, slug_or_url, instance (us/eu), supported, detection_method (fingerprint/probe/manual), confidence (high/medium/low), n_jobs_total, n_jobs_italy, last_checked, notes`.

Rules:
- Low-confidence rows go to manual review, not to the crawler.
- The script must be idempotent: it can be run again and only updates what is needed. It must also accept a manual correction (`manual_overrides.csv`) that always wins.
- At the end print a coverage report: how many companies have a detected ATS, how many supported and unsupported, how many with no ATS found. Also split between tech and non-tech.

# Phase 2: job ad crawler

`crawl_jobs.py` reads `ats_registry` (only rows with `supported = true` and high or medium confidence), downloads the ads and saves them normalized. Requirements:
- Keep only ads in Italy, or remote with the option to work from Italy. Save the raw `location` field plus normalized `city` and `country`.
- Deduplicate by `(ats, slug, job_id)` and keep `first_seen` and `last_seen`, so you can see over time when an ad disappears.
- Save the full description and the pay fields: structured, figures extracted from the text (min, max, currency, period, gross or net if it can be inferred) and the `salary_vague` flag.

# Phase 3: database and searches

Use SQLite locally (`data/jobs.db`), with a schema compatible with BigQuery and an `export_bigquery.py` script to load it later.

Tables: `companies`, `ats_registry`, `jobs`, `searches`.

Searches are the core of how I will use it. Create a `search.py "data analyst" --location milano` command that:
- searches the term in the title and description of the ads in the DB;
- saves in `searches` the fields `search_term, location, run_at, n_jobs, n_companies, n_jobs_with_salary, n_jobs_vague, ral_min_median, ral_max_median`;
- prints the companies that have ads for that term and place, with the count per company and how many state the pay.

Add an SQL view `v_search_summary` to see the search history.

# Constraints

- Use only public endpoints and public pages: no login, no LinkedIn or Indeed, and respect `robots.txt` for company website pages.
- Apply gentle rate limiting (pauses between requests, at most a few requests per second per domain), with retries and backoff on errors.
- Cache HTTP responses during development, so as not to repeat hundreds of calls on every test.
- Everything from the command line, with a `README.md` that explains in a few lines how to run each phase.
- Add minimal tests for the parsing functions (Workday URLs, slug generation, salary regex).

# How to proceed

1. Read `test_ats.py` and propose the folder structure and the table schema before writing code.
2. Implement Phase 1 and try it on 20 mixed companies (10 tech and 10 non-tech). Show me `ats_registry` and the coverage report, then stop so I can review the results.
3. Only after my ok, move on to phases 2 and 3.
