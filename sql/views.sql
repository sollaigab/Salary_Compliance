-- Views. They are recreated on every connection, so changes to this file apply right away.
-- Standard SQL: works on SQLite and (table names aside) on BigQuery.

-- ATS discovery coverage, split between tech and non-tech, plus the total
DROP VIEW IF EXISTS v_coverage;
CREATE VIEW v_coverage AS
WITH base AS (
    SELECT
        CASE WHEN is_tech = 1 THEN 'tech' ELSE 'non tech' END AS group_name,
        ats, supported, confidence
    FROM ats_registry
),
grouped AS (
    SELECT group_name, ats, supported, confidence FROM base
    UNION ALL
    SELECT 'TOTAL', ats, supported, confidence FROM base
)
SELECT
    group_name,
    COUNT(*)                                                              AS companies,
    SUM(CASE WHEN ats <> 'none' THEN 1 ELSE 0 END)                        AS with_ats,
    SUM(CASE WHEN supported = 1 AND confidence IN ('high', 'medium') THEN 1 ELSE 0 END) AS supported_ready,
    SUM(CASE WHEN supported = 1 AND confidence = 'low' THEN 1 ELSE 0 END) AS supported_to_review,
    SUM(CASE WHEN ats <> 'none' AND supported = 0 THEN 1 ELSE 0 END)     AS not_supported,
    SUM(CASE WHEN ats = 'none' THEN 1 ELSE 0 END)                         AS no_ats
FROM grouped
GROUP BY group_name;

-- Search history: latest run, first run and the change between them
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

DROP VIEW IF EXISTS v_jobs_enriched;   -- removed: unused

-- Job ads without the full text, extracted phrases or raw JSON (the ad text is copyrighted).
-- It still names the companies, so it is used only for the PRIVATE DuckDB export and for the
-- counts in meta.json. The public data is built by export.py from aggregates only.
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
    CASE WHEN j.posting_period = 'post_law' THEN 1 ELSE 0 END AS posted_after_law,
    j.first_seen, j.last_seen, j.is_active
FROM jobs j
LEFT JOIN companies c ON c.company_id = j.company_id
LEFT JOIN locations l ON l.location_id = j.location_id;

DROP VIEW IF EXISTS v_transparency_company;   -- removed: unused
