-- Local database schema (SQLite), designed to be loadable into BigQuery.
-- Type mapping: TEXT -> STRING, INTEGER -> INT64, REAL -> FLOAT64,
-- ISO-8601 UTC dates/times in TEXT -> TIMESTAMP, 0/1 flags in INTEGER -> BOOL, JSON in TEXT -> JSON/STRING.

-- Observed companies (from data/companies_seed.csv)
CREATE TABLE IF NOT EXISTS companies (
    company_id        TEXT PRIMARY KEY,   -- name slug, e.g. 'example-group'
    name              TEXT NOT NULL,
    website           TEXT,
    website_verified  INTEGER,            -- 0 = website "to be verified"
    sector            TEXT,               -- banks, insurance, energy_utilities, software_saas, ...
    is_tech           INTEGER,
    company_type      TEXT,               -- listing/ownership type (FTSE MIB listed, other listed, foreign multinational, private, scaleup, public)
    hq_city           TEXT,               -- main office in Italy
    size_band         TEXT,               -- employees worldwide: S <250, M 250-1k, L 1k-10k, XL >10k
    source            TEXT,               -- list it comes from (FTSE MIB, Mid Cap, ...)
    updated_at        TEXT
);

-- Which ATS each company uses (Phase 1). One row per company.
CREATE TABLE IF NOT EXISTS ats_registry (
    company_id        TEXT PRIMARY KEY REFERENCES companies(company_id),
    company           TEXT,               -- repeated so the table is easy to review
    sector            TEXT,
    is_tech           INTEGER,
    ats               TEXT,               -- greenhouse, lever, ashby, workday, smartrecruiters, ..., none
    slug_or_url       TEXT,
    instance          TEXT,               -- us / eu (Greenhouse and Lever only)
    supported         INTEGER,            -- 1 = the crawler can download it
    detection_method  TEXT,               -- fingerprint / probe / manual
    confidence        TEXT,               -- high / medium / low (low = manual review)
    n_jobs_total      INTEGER,
    n_jobs_italy      INTEGER,
    careers_url       TEXT,               -- career page found on the website
    evidence          TEXT,               -- text that triggered the detection
    last_checked      TEXT,
    notes             TEXT
);

-- Geography: one row per ISTAT municipality + special rows (regions, Italy, remote)
CREATE TABLE IF NOT EXISTS locations (
    location_id       TEXT PRIMARY KEY,   -- e.g. 'IT-015146' (ISTAT code), 'IT-REMOTE', 'EU-REMOTE'
    city              TEXT,
    istat_code        TEXT,
    province          TEXT,
    province_code     TEXT,               -- abbreviation: MI, RM, ...
    region            TEXT,
    region_code       TEXT,               -- ISTAT region code (01 Piemonte ... 20 Sardegna)
    macro_area        TEXT,               -- Nord-ovest, Nord-est, Centro, Sud, Isole
    country           TEXT,
    country_code      TEXT,
    population        INTEGER,
    city_size_band    TEXT,               -- metro >1M, large 250k-1M, medium 50k-250k, small <50k
    is_capoluogo      INTEGER,
    lat               REAL,               -- empty for now (no open source downloaded)
    lon               REAL,
    is_remote         INTEGER
);

-- Job ads (Phase 2). Key = ats|slug|job_id.
CREATE TABLE IF NOT EXISTS jobs (
    job_key                TEXT PRIMARY KEY,
    ats                    TEXT NOT NULL,
    slug_or_url            TEXT NOT NULL,
    job_id                 TEXT NOT NULL,
    company_id             TEXT REFERENCES companies(company_id),
    title                  TEXT,
    department             TEXT,          -- as the ATS writes it
    job_function           TEXT,          -- normalized: data, engineering, sales, marketing, finance, hr, legal, operations, ...
    seniority              TEXT,          -- intern, junior, mid, senior, lead, manager, director, executive
    seniority_source       TEXT,          -- title / experience (from the years required) / not_stated
    experience_years       INTEGER,       -- minimum years of experience required, if stated
    contract_type          TEXT,          -- permanent, fixed_term, internship, apprenticeship, freelance, agency
    work_schedule          TEXT,          -- full_time, part_time
    workplace_type         TEXT,          -- onsite, hybrid, remote
    location_raw           TEXT,
    location_id            TEXT REFERENCES locations(location_id),   -- main location
    city                   TEXT,
    country                TEXT,
    is_remote              INTEGER,
    n_locations            INTEGER,
    url                    TEXT,
    description            TEXT,
    description_lang       TEXT,          -- it / en
    description_length     INTEGER,
    posted_at              TEXT,          -- as received from the ATS (various formats)
    posted_date            TEXT,          -- normalized YYYY-MM-DD
    posting_period         TEXT,          -- post_law (from 7/6/2026) / pre_law / old (over 12 months)
    -- pay
    salary_structured_json TEXT,          -- as received from the ATS
    salary_source          TEXT,          -- structured / text / none
    salary_min             REAL,
    salary_max             REAL,
    salary_currency        TEXT,
    salary_period          TEXT,          -- year / month / hour
    salary_gross_net       TEXT,          -- gross / net / NULL
    ral_min_annual         REAL,          -- gross annual salary in EUR (monthly x 14)
    ral_max_annual         REAL,
    salary_text_matches    TEXT,          -- JSON: phrases found in the text
    salary_vague           INTEGER,       -- vague wording ("commisurata all'esperienza", "secondo CCNL")
    salary_transparency    TEXT,          -- figure / vague / none (handy for filters)
    -- history
    first_seen             TEXT,
    last_seen              TEXT,
    is_active              INTEGER,       -- 0 if missing from the latest crawl of its board
    content_hash           TEXT           -- to notice when the ad changes
);

-- Ads with several locations: one row per location (to filter by city without losing any)
CREATE TABLE IF NOT EXISTS job_locations (
    job_key      TEXT REFERENCES jobs(job_key),
    location_id  TEXT REFERENCES locations(location_id),
    location_raw TEXT,
    PRIMARY KEY (job_key, location_raw)
);

-- Every crawler pass on a board (used for is_active and monitoring)
CREATE TABLE IF NOT EXISTS crawl_runs (
    run_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id   TEXT,
    ats          TEXT,
    slug_or_url  TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    n_jobs_seen  INTEGER,
    n_jobs_new   INTEGER,
    status       TEXT,                    -- ok / error
    error        TEXT
);

-- History of searches run with search.py (Phase 3)
CREATE TABLE IF NOT EXISTS searches (
    search_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    search_term         TEXT NOT NULL,
    location            TEXT,
    run_at              TEXT NOT NULL,
    n_jobs              INTEGER,
    n_companies         INTEGER,
    n_jobs_with_salary  INTEGER,
    n_jobs_vague        INTEGER,
    ral_min_median      REAL,
    ral_max_median      REAL
);

CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_jobs_location ON jobs(location_id);
CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs(is_active);
