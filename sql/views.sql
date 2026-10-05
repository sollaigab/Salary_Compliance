-- Viste. Vengono ricreate a ogni connessione, così le modifiche a questo file si applicano subito.
-- SQL standard: funziona su SQLite e (salvo nomi tabella) su BigQuery.

-- Copertura della scoperta ATS, divisa tra tech e non tech, più il totale
DROP VIEW IF EXISTS v_coverage;
CREATE VIEW v_coverage AS
WITH base AS (
    SELECT
        CASE WHEN is_tech = 1 THEN 'tech' ELSE 'non tech' END AS gruppo,
        ats, supported, confidence
    FROM ats_registry
),
grouped AS (
    SELECT gruppo, ats, supported, confidence FROM base
    UNION ALL
    SELECT 'TOTALE', ats, supported, confidence FROM base
)
SELECT
    gruppo,
    COUNT(*)                                                              AS aziende,
    SUM(CASE WHEN ats <> 'none' THEN 1 ELSE 0 END)                        AS con_ats,
    SUM(CASE WHEN supported = 1 AND confidence IN ('high', 'medium') THEN 1 ELSE 0 END) AS supportati_pronti,
    SUM(CASE WHEN supported = 1 AND confidence = 'low' THEN 1 ELSE 0 END) AS supportati_da_rivedere,
    SUM(CASE WHEN ats <> 'none' AND supported = 0 THEN 1 ELSE 0 END)     AS non_supportati,
    SUM(CASE WHEN ats = 'none' THEN 1 ELSE 0 END)                         AS nessun_ats
FROM grouped
GROUP BY gruppo;

-- Storico delle ricerche: ultimo lancio, primo lancio e variazione
DROP VIEW IF EXISTS v_search_summary;
CREATE VIEW v_search_summary AS
WITH ranked AS (
    SELECT
        s.*,
        COALESCE(location, '') AS location_key,
        ROW_NUMBER() OVER (PARTITION BY search_term, COALESCE(location, '') ORDER BY run_at DESC) AS rn_last,
        ROW_NUMBER() OVER (PARTITION BY search_term, COALESCE(location, '') ORDER BY run_at ASC)  AS rn_first,
        COUNT(*)     OVER (PARTITION BY search_term, COALESCE(location, ''))                      AS n_runs
    FROM searches s
)
SELECT
    l.search_term,
    l.location,
    l.n_runs,
    f.run_at                                                         AS first_run,
    l.run_at                                                         AS last_run,
    l.n_jobs,
    l.n_companies,
    l.n_jobs_with_salary,
    ROUND(100.0 * l.n_jobs_with_salary / NULLIF(l.n_jobs, 0), 1)     AS pct_with_salary,
    l.n_jobs_vague,
    ROUND(100.0 * l.n_jobs_vague / NULLIF(l.n_jobs, 0), 1)           AS pct_vague,
    l.ral_min_median,
    l.ral_max_median,
    l.n_jobs - f.n_jobs                                              AS delta_n_jobs,
    l.n_jobs_with_salary - f.n_jobs_with_salary                      AS delta_with_salary
FROM ranked l
JOIN ranked f
  ON f.search_term = l.search_term AND f.location_key = l.location_key AND f.rn_first = 1
WHERE l.rn_last = 1;

-- Annunci arricchiti con azienda e luogo: la tabella "piatta" per la web app filtrabile
DROP VIEW IF EXISTS v_jobs_enriched;
CREATE VIEW v_jobs_enriched AS
SELECT
    j.job_key, j.title, j.url, j.ats,
    j.job_function, j.seniority, j.contract_type, j.work_schedule, j.workplace_type, j.is_remote,
    j.department, j.description_lang,
    j.city, j.country, l.province, l.province_code, l.region, l.macro_area,
    l.city_size_band, l.population, l.lat, l.lon,
    c.company_id, c.name AS company, c.sector, c.is_tech, c.company_type, c.size_band AS company_size,
    c.hq_city AS company_hq_city,
    j.salary_transparency, j.salary_vague, j.salary_source,
    j.salary_min, j.salary_max, j.salary_currency, j.salary_period, j.salary_gross_net,
    j.ral_min_annual, j.ral_max_annual,
    j.posted_at, j.posted_date, j.posting_period,
    CASE WHEN j.posting_period = 'post_legge' THEN 1 ELSE 0 END AS posted_after_law,
    j.first_seen, j.last_seen, j.is_active
FROM jobs j
LEFT JOIN companies c ON c.company_id = j.company_id
LEFT JOIN locations l ON l.location_id = j.location_id;

-- Versione PUBBLICABILE degli annunci: niente testo integrale, niente frasi estratte, niente JSON grezzo.
-- (il testo degli annunci è protetto dal diritto d'autore: resta solo nel DB privato)
DROP VIEW IF EXISTS v_jobs_public;
CREATE VIEW v_jobs_public AS
SELECT
    j.job_key, j.title, j.url, j.ats,
    j.job_function, j.seniority, j.contract_type, j.work_schedule, j.workplace_type, j.is_remote,
    j.description_lang,
    j.location_id, j.city, l.province, l.province_code, l.region, l.macro_area, l.city_size_band,
    c.company_id, c.name AS company, c.sector, c.is_tech, c.company_type, c.size_band AS company_size,
    j.salary_transparency, j.salary_source, j.salary_period, j.salary_gross_net,
    j.ral_min_annual, j.ral_max_annual,
    j.posted_at, j.posted_date, j.posting_period,
    CASE WHEN j.posting_period = 'post_legge' THEN 1 ELSE 0 END AS posted_after_law,
    j.first_seen, j.last_seen, j.is_active
FROM jobs j
LEFT JOIN companies c ON c.company_id = j.company_id
LEFT JOIN locations l ON l.location_id = j.location_id;

-- Trasparenza retributiva per azienda: annunci attivi pubblicati dal 7/6/2026 (soggetti al D.Lgs. 96/2026)
DROP VIEW IF EXISTS v_transparency_company;
CREATE VIEW v_transparency_company AS
SELECT
    c.name AS company, c.sector, c.is_tech,
    COUNT(*)                                                                         AS n_jobs,
    SUM(CASE WHEN j.salary_transparency = 'cifra' THEN 1 ELSE 0 END)                AS n_with_salary,
    SUM(CASE WHEN j.salary_transparency = 'vaga' THEN 1 ELSE 0 END)                 AS n_vague,
    ROUND(100.0 * SUM(CASE WHEN j.salary_transparency = 'cifra' THEN 1 ELSE 0 END) / COUNT(*), 1) AS pct_with_salary,
    ROUND(AVG(j.ral_min_annual), 0)                                                  AS ral_min_avg,
    ROUND(AVG(j.ral_max_annual), 0)                                                  AS ral_max_avg
FROM jobs j
JOIN companies c ON c.company_id = j.company_id
WHERE j.is_active = 1 AND j.posting_period = 'post_legge'
GROUP BY c.name, c.sector, c.is_tech;
