-- Statistiche aggregate pubblicabili, calcolate in DuckDB (ha MEDIAN e QUANTILE).
-- Base: annunci attivi PUBBLICATI DAL 7/6/2026 (soggetti al D.Lgs. 96/2026), salvo transparency_by_period.
-- Le RAL sono annue lorde in euro (mensili x 14, stage x 12).
-- Ogni query diventa un file Parquet in data/export/public/<nome>.parquet

-- name: transparency_by_company
SELECT
    company, sector, is_tech,
    COUNT(*)                                                         AS n_jobs,
    COUNT(*) FILTER (WHERE salary_transparency = 'cifra')            AS n_with_salary,
    COUNT(*) FILTER (WHERE salary_transparency = 'vaga')             AS n_vague,
    ROUND(100.0 * COUNT(*) FILTER (WHERE salary_transparency = 'cifra') / COUNT(*), 1) AS pct_with_salary,
    MEDIAN(ral_min_annual)                                           AS ral_min_median,
    MEDIAN(ral_max_annual)                                           AS ral_max_median
FROM jobs_public
WHERE is_active = 1 AND posting_period = 'post_legge'
GROUP BY ALL
ORDER BY n_jobs DESC;

-- name: transparency_by_sector
SELECT
    sector,
    COUNT(DISTINCT company)                                          AS n_companies,
    COUNT(*)                                                         AS n_jobs,
    ROUND(100.0 * COUNT(*) FILTER (WHERE salary_transparency = 'cifra') / COUNT(*), 1) AS pct_with_salary,
    ROUND(100.0 * COUNT(*) FILTER (WHERE salary_transparency = 'vaga') / COUNT(*), 1)  AS pct_vague,
    MEDIAN(ral_min_annual)                                           AS ral_min_median,
    MEDIAN(ral_max_annual)                                           AS ral_max_median
FROM jobs_public
WHERE is_active = 1 AND posting_period = 'post_legge'
GROUP BY ALL
ORDER BY n_jobs DESC;

-- name: salary_by_function_seniority
SELECT
    job_function, seniority,
    COUNT(*)                                                         AS n_jobs,
    COUNT(ral_min_annual)                                            AS n_with_ral,
    QUANTILE_CONT(ral_min_annual, 0.25)                              AS ral_min_p25,
    MEDIAN(ral_min_annual)                                           AS ral_min_median,
    MEDIAN(ral_max_annual)                                           AS ral_max_median,
    QUANTILE_CONT(ral_max_annual, 0.75)                              AS ral_max_p75
FROM jobs_public
WHERE is_active = 1 AND posting_period = 'post_legge'
GROUP BY ALL
HAVING COUNT(ral_min_annual) >= 5          -- niente mediane su meno di 5 annunci
ORDER BY job_function, seniority;

-- name: salary_by_region
SELECT
    COALESCE(region, CASE WHEN is_remote = 1 THEN 'Remoto' ELSE 'Italia (sede non specificata)' END) AS region,
    macro_area,
    COUNT(*)                                                         AS n_jobs,
    ROUND(100.0 * COUNT(*) FILTER (WHERE salary_transparency = 'cifra') / COUNT(*), 1) AS pct_with_salary,
    MEDIAN(ral_min_annual)                                           AS ral_min_median,
    MEDIAN(ral_max_annual)                                           AS ral_max_median
FROM jobs_public
WHERE is_active = 1 AND posting_period = 'post_legge'
GROUP BY ALL
ORDER BY n_jobs DESC;

-- name: transparency_by_period
-- Confronto prima/dopo la legge (tutti gli annunci attivi). ATTENZIONE: gli annunci pre-legge ancora online
-- non sono un campione rappresentativo del mercato prima della legge (bias di sopravvivenza).
SELECT
    posting_period,
    COUNT(*)                                                         AS n_jobs,
    ROUND(100.0 * COUNT(*) FILTER (WHERE salary_transparency = 'cifra') / COUNT(*), 1) AS pct_with_salary,
    ROUND(100.0 * COUNT(*) FILTER (WHERE salary_transparency = 'vaga') / COUNT(*), 1)  AS pct_vague,
    MIN(posted_date)                                                 AS first_posted,
    MAX(posted_date)                                                 AS last_posted
FROM jobs_public
WHERE is_active = 1
GROUP BY ALL
ORDER BY first_posted;
