-- Schema del database locale (SQLite), pensato per essere caricato su BigQuery.
-- Corrispondenza tipi: TEXT -> STRING, INTEGER -> INT64, REAL -> FLOAT64,
-- date/ore in TEXT ISO-8601 UTC -> TIMESTAMP, flag 0/1 in INTEGER -> BOOL, JSON in TEXT -> JSON/STRING.

-- Aziende osservate (dal file data/companies_seed.csv)
CREATE TABLE IF NOT EXISTS companies (
    company_id        TEXT PRIMARY KEY,   -- slug del nome, es. 'examplegroup'
    name              TEXT NOT NULL,
    website           TEXT,
    website_verified  INTEGER,            -- 0 = sito "da verificare"
    sector            TEXT,               -- banche, assicurazioni, energia_utility, software_saas, ...
    is_tech           INTEGER,
    company_type      TEXT,               -- quotata_ftse_mib, quotata_altro, multinazionale_estera, privata_italiana, scaleup_startup, pubblica
    hq_city           TEXT,               -- sede principale in Italia
    size_band         TEXT,               -- dipendenti nel mondo: S <250, M 250-1k, L 1k-10k, XL >10k
    source            TEXT,               -- lista da cui proviene (ftse_mib, big4_consulenza, ...)
    updated_at        TEXT
);

-- Quale ATS usa ogni azienda (Fase 1). Una riga per azienda.
CREATE TABLE IF NOT EXISTS ats_registry (
    company_id        TEXT PRIMARY KEY REFERENCES companies(company_id),
    company           TEXT,               -- ripetuti per leggere comodo in revisione
    sector            TEXT,
    is_tech           INTEGER,
    ats               TEXT,               -- greenhouse, lever, ashby, workday, smartrecruiters, ..., none
    slug_or_url       TEXT,
    instance          TEXT,               -- us / eu (solo Greenhouse e Lever)
    supported         INTEGER,            -- 1 = il crawler lo sa scaricare
    detection_method  TEXT,               -- fingerprint / probe / manual
    confidence        TEXT,               -- high / medium / low (low = revisione manuale)
    n_jobs_total      INTEGER,
    n_jobs_italy      INTEGER,
    careers_url       TEXT,               -- pagina carriere trovata sul sito
    evidence          TEXT,               -- testo che ha fatto scattare il riconoscimento
    last_checked      TEXT,
    notes             TEXT
);

-- Dimensione geografica: una riga per comune ISTAT + righe speciali (regioni, Italia, remoto)
CREATE TABLE IF NOT EXISTS locations (
    location_id       TEXT PRIMARY KEY,   -- es. 'IT-015146' (codice ISTAT), 'IT-REMOTE', 'EU-REMOTE'
    city              TEXT,
    istat_code        TEXT,
    province          TEXT,
    province_code     TEXT,               -- sigla: MI, RM, ...
    region            TEXT,
    region_code       TEXT,               -- codice ISTAT della regione (01 Piemonte ... 20 Sardegna)
    macro_area        TEXT,               -- Nord-ovest, Nord-est, Centro, Sud, Isole
    country           TEXT,
    country_code      TEXT,
    population        INTEGER,
    city_size_band    TEXT,               -- metropoli >1M, grande 250k-1M, media 50k-250k, piccola <50k
    is_capoluogo      INTEGER,
    lat               REAL,               -- vuote per ora (nessuna fonte aperta scaricata)
    lon               REAL,
    is_remote         INTEGER
);

-- Annunci (Fase 2). Chiave = ats|slug|job_id.
CREATE TABLE IF NOT EXISTS jobs (
    job_key                TEXT PRIMARY KEY,
    ats                    TEXT NOT NULL,
    slug_or_url            TEXT NOT NULL,
    job_id                 TEXT NOT NULL,
    company_id             TEXT REFERENCES companies(company_id),
    title                  TEXT,
    department             TEXT,          -- come lo scrive l'ATS
    job_function           TEXT,          -- normalizzata: data, engineering, sales, marketing, finance, hr, legal, operations, ...
    seniority              TEXT,          -- intern, junior, mid, senior, lead, manager, director, executive
    seniority_source       TEXT,          -- titolo / esperienza (dagli anni richiesti) / non_indicata
    experience_years       INTEGER,       -- anni minimi di esperienza richiesti, se indicati
    contract_type          TEXT,          -- indeterminato, determinato, stage, apprendistato, freelance
    work_schedule          TEXT,          -- full_time, part_time
    workplace_type         TEXT,          -- onsite, hybrid, remote
    location_raw           TEXT,
    location_id            TEXT REFERENCES locations(location_id),   -- sede principale
    city                   TEXT,
    country                TEXT,
    is_remote              INTEGER,
    n_locations            INTEGER,
    url                    TEXT,
    description            TEXT,
    description_lang       TEXT,          -- it / en
    description_length     INTEGER,
    posted_at              TEXT,          -- come arriva dall'ATS (formati diversi)
    posted_date            TEXT,          -- AAAA-MM-GG normalizzata
    posting_period         TEXT,          -- post_legge (dal 7/6/2026) / pre_legge / storico (oltre 12 mesi)
    -- retribuzione
    salary_structured_json TEXT,          -- come arriva dall'ATS
    salary_source          TEXT,          -- structured / text / none
    salary_min             REAL,
    salary_max             REAL,
    salary_currency        TEXT,
    salary_period          TEXT,          -- year / month / hour
    salary_gross_net       TEXT,          -- gross / net / NULL
    ral_min_annual         REAL,          -- RAL annua lorda EUR (mensile x 14)
    ral_max_annual         REAL,
    salary_text_matches    TEXT,          -- JSON: frasi trovate nel testo
    salary_vague           INTEGER,       -- formula vaga ("commisurata all'esperienza", "secondo CCNL")
    salary_transparency    TEXT,          -- cifra / vaga / assente (comodo per i filtri)
    -- storico
    first_seen             TEXT,
    last_seen              TEXT,
    is_active              INTEGER,       -- 0 se non compare più nell'ultimo crawl della sua board
    content_hash           TEXT           -- per accorgersi se l'annuncio cambia
);

-- Annunci con più sedi: una riga per sede (per filtrare per città senza perdere nulla)
CREATE TABLE IF NOT EXISTS job_locations (
    job_key      TEXT REFERENCES jobs(job_key),
    location_id  TEXT REFERENCES locations(location_id),
    location_raw TEXT,
    PRIMARY KEY (job_key, location_raw)
);

-- Ogni passaggio del crawler su una board (serve per is_active e per il monitoraggio)
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

-- Storico delle ricerche lanciate con search.py (Fase 3)
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
