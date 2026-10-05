"""
Test minimi sulle funzioni di parsing. Lancia con:  python -m pytest -q
"""

from lib.ats_fetchers import parse_workday_url, workday_italy_count
from lib.ats_fingerprints import find_fingerprints
from lib.locations import is_italy
from lib.salary import annualize, find_salary, is_vague, parse_salary, to_number
from lib.slugs import candidate_slugs, company_id_from_name, domain_root, name_match


# ---------- URL Workday ----------
def test_workday_url_con_locale():
    host, tenant, site = parse_workday_url("https://tenant.wd3.myworkdayjobs.com/en-US/CompanyCareers")
    assert (host, tenant, site) == ("tenant.wd3.myworkdayjobs.com", "tenant", "CompanyCareers")


def test_workday_url_senza_sito():
    assert parse_workday_url("tenantg.wd3.myworkdayjobs.com") == ("tenantg.wd3.myworkdayjobs.com", "tenantg", None)


def test_workday_url_cxs_e_pagina_annuncio():
    assert parse_workday_url("https://x.wd3.myworkdayjobs.com/wday/cxs/x/External/jobs")[2] == "External"
    assert parse_workday_url("https://x.wd3.myworkdayjobs.com/en-US/Ext/job/Milan/Analyst_R123")[2] == "Ext"


def test_workday_facets_italia():
    facets = [{"facetParameter": "locationMainGroup", "values": [
        {"facetParameter": "locationCountry", "values": [
            {"descriptor": "France", "count": 7}, {"descriptor": "Italy", "count": 12}]}]}]
    assert workday_italy_count(facets) == 12
    assert workday_italy_count([]) is None


# ---------- slug ----------
def test_company_id():
    assert company_id_from_name("examplegroup") == "examplegroup"
    assert company_id_from_name("acme S.p.A.") == "acme-spa"


def test_slug_candidati():
    slugs = candidate_slugs("examplepay", "https://www.examplepay.com")
    assert slugs[0] == "examplepay"
    assert "examplepay" in slugs
    assert "examplepay-italia" in slugs
    slugs = candidate_slugs("examplebank S.p.A.", "https://www.examplebank.it")
    assert slugs[:2] == ["examplebank", "examplebank"]


def test_domain_root():
    assert domain_root("https://group.examplegroup.com/it") == "examplegroup"
    assert domain_root("gruppo.examplebpm.it") == "examplebpm"


def test_name_match():
    assert name_match("examplepay", "https://www.examplepay.com", ["At examplepay we build..."]) == "strong"
    assert name_match("examplebank", None, ["example cerca un analista"]) == "weak"
    assert name_match("companyb", None, ["We are a payments company"]) is None


# ---------- impronte ATS ----------
def test_fingerprint_greenhouse_eu_embed():
    html = '<script src="https://job-boards.eu.greenhouse.io/embed/job_board/js?for=examplepay"></script>'
    best = find_fingerprints(html)[0]
    assert (best["ats"], best["slug_or_url"], best["instance"]) == ("greenhouse", "examplepay", "eu")


def test_fingerprint_lever_e_workday():
    found = find_fingerprints('href="https://jobs.eu.lever.co/prima/abc-123"')
    assert (found[0]["ats"], found[0]["slug_or_url"], found[0]["instance"]) == ("lever", "prima", "eu")
    found = find_fingerprints('"https://tenant.wd3.myworkdayjobs.com/en-US/CompanyCareers/job/x"')
    assert found[0]["slug_or_url"] == "https://tenant.wd3.myworkdayjobs.com/CompanyCareers"


def test_fingerprint_non_supportato():
    found = find_fingerprints('<iframe src="https://inrecruiting.intervieweb.it/acme/"></iframe>'
                              '<a href="https://acme.jobs.personio.de">')
    by_ats = {f["ats"]: f for f in found}
    assert not by_ats["inrecruiting"]["supported"]
    assert by_ats["personio"]["supported"] and by_ats["personio"]["slug_or_url"] == "acme"
    assert find_fingerprints('href="https://apply.workable.com/j/ABC123"') == []


# ---------- regex RAL ----------
def test_to_number():
    assert to_number("35.000") == 35000
    assert to_number("1.800,50") == 1800.5
    assert to_number("40,5") == 40.5
    assert to_number("35,000") == 35000


def test_find_salary():
    assert find_salary("Offriamo RAL 35.000 euro più benefit") == ["RAL 35.000"]
    assert find_salary("Esperienza generale 2 anni") == []      # 'generale' non è RAL


def test_parse_fascia_annua():
    s = parse_salary("Si offre RAL 35.000 - 40.000 € in base all'esperienza")
    assert (s["min"], s["max"], s["period"], s["currency"], s["gross_net"]) == (35000, 40000, "year", "EUR", "gross")


def test_parse_k():
    s = parse_salary("Retribuzione: RAL 30-35K")
    assert (s["min"], s["max"], s["period"]) == (30000, 35000, "year")


def test_parse_mensile_netto():
    s = parse_salary("Stipendio di 1.800 € netti al mese")
    assert (s["min"], s["period"], s["gross_net"]) == (1800, "month", "net")


def test_parse_ignora_anni_e_mensilita():
    assert parse_salary("Retribuzione secondo CCNL 2024, 14 mensilità") is None


def test_vaghe():
    assert is_vague("Retribuzione commisurata all'esperienza")
    assert is_vague("Inquadramento secondo CCNL Commercio")
    assert is_vague("We offer a competitive salary")
    assert not is_vague("RAL 35.000 €")


def test_annualize_14_mensilita():
    assert annualize(2000, "month", "EUR", "gross") == 28000
    assert annualize(35000, "year", "EUR", None) == 35000
    assert annualize(1800, "month", "EUR", "net") is None
    assert annualize(15, "hour", "EUR", "gross") is None


# ---------- sedi ----------
def test_is_italy():
    assert is_italy("Milan, Lombardy")
    assert is_italy("Remote", "IT")
    assert is_italy("Forlì")
    assert not is_italy("Berlin, Germany")


def test_fingerprint_avature_dominio_proprio_e_jobs2web():
    found = find_fingerprints("https://jobs.companyc.com/en_US/careers/JobOpenings")
    assert (found[0]["ats"], found[0]["slug_or_url"]) == ("avature", "jobs.companyc.com")
    found = find_fingerprints('src="https://rmk-map-12.jobs2web.com/x.js"')
    assert (found[0]["ats"], found[0]["slug_or_url"]) == ("successfactors", None)


def test_workday_myworkdaysite():
    url = "https://wd3.myworkdaysite.com/en-US/recruiting/tenanth/GroupExternalCareerSite/job/x"
    assert parse_workday_url(url) == ("wd3.myworkdaysite.com", "tenanth", "GroupExternalCareerSite")
    found = find_fingerprints(f'href="{url}"')
    assert found[0]["ats"] == "workday"
    assert found[0]["slug_or_url"] == "https://wd3.myworkdaysite.com/recruiting/tenanth/GroupExternalCareerSite"


# ---------- nuovi ATS: Oracle, Teamtailor, SuccessFactors; probe Workday ----------
from lib.ats_fetchers import parse_oracle_url, parse_sf_job_page, teamtailor_root
from lib.slugs import workday_tenants


def test_oracle_url():
    url = "https://hdeh.fa.em3.oraclecloud.com/hcmUI/CandidateExperience/it/sites/CX_1/job/12"
    assert parse_oracle_url(url) == ("hdeh.fa.em3.oraclecloud.com", "CX_1")
    assert parse_oracle_url("hdeh.fa.em3.oraclecloud.com") == ("hdeh.fa.em3.oraclecloud.com", None)


def test_teamtailor_root():
    assert teamtailor_root("companyu") == "https://companyu.teamtailor.com"
    assert teamtailor_root("https://jobs.companyk.it/jobs/123") == "https://jobs.companyk.it"


def test_successfactors_microdata():
    page = ('<div itemscope itemtype="http://schema.org/JobPosting"><span itemprop="jobLocation">'
            '<span itemprop="address"><meta itemprop="addressLocality" content="Parma">'
            '<meta itemprop="addressCountry" content="IT"></span></span>'
            '<meta itemprop="datePosted" content="Thu Sep 17 00:00:00 UTC 2026">'
            '<meta itemprop="hiringOrganization" content="examplefoods Group">'
            '<span itemprop="title">Data Analyst</span></div>')
    p = parse_sf_job_page(page)
    assert (p["title"], p["location"], p["country"], p["posted_at"], p["company"]) == \
        ("Data Analyst", "Parma, IT", "IT", "2026-09-17", "examplefoods Group")


def test_successfactors_sede_dall_url():
    p = parse_sf_job_page('<span itemprop="title">Addetto vendita</span>',
                          "https://esselungajob.it/job/Lombardia-Addetto-Mantova/1170132601/")
    assert p["location"] == "Lombardia Addetto Mantova"
    assert is_italy(p["location"])


def test_workday_tenants_varianti():
    assert "examplecorp" in workday_tenants("examplecorp", "https://www.examplecorp.com")
    assert "examplebank" in workday_tenants("examplebank", "https://www.examplebank.it")
    assert "examplecorp" in workday_tenants("examplecorp", "https://www.examplecorp.com")


# ---------- Fase 2: arricchimento e sedi ----------
from lib import enrich
from lib.locations import LocationIndex


def _fake_index():
    base = {"province": None, "province_code": None, "region": "Lombardia", "region_code": "03",
            "macro_area": "Nord-ovest", "is_capoluogo": 1}
    comuni = [
        {**base, "location_id": "IT-015146", "city": "Milano", "population": 1_300_000},
        {**base, "location_id": "IT-015209", "city": "Sesto San Giovanni", "population": 80_000},
        {**base, "location_id": "IT-058091", "city": "Roma", "population": 2_700_000, "region": "Lazio",
         "region_code": "12", "macro_area": "Centro"},
    ]
    return LocationIndex(comuni)


def test_normalizza_sedi():
    idx = _fake_index()
    assert idx.normalize("Milan, Lombardy, Italy")["location_id"] == "IT-015146"
    assert idx.normalize("Sesto San Giovanni (MI)")["city"] == "Sesto San Giovanni"
    assert idx.normalize("Remote - Italy")["location_id"] == "IT-REMOTE"
    assert idx.normalize("Remote, EMEA")["location_id"] == "EU-REMOTE"
    assert idx.normalize("London, United Kingdom", "GB")["location_id"] is None
    assert idx.normalize("Lombardy")["location_id"] == "IT-REG-03"


def test_seniority_funzione_contratto():
    assert enrich.seniority("Senior Data Analyst") == "senior"
    assert enrich.seniority("Stage - Marketing") == "intern"
    assert enrich.job_function("Addetto/a alla Vendita Part Time") == "retail"
    assert enrich.job_function("Backend Developer (Python)") == "engineering"
    assert enrich.contract_type("Full time permanent", "", "") == "indeterminato"
    assert enrich.work_schedule(None, "Cassiere part time", "") == "part_time"


def test_privacy_email_telefono():
    out = enrich.scrub_personal_data("Scrivi a mario.rossi@azienda.it o chiama +39 02 1234 5678")
    assert "@" not in out and "1234" not in out


def test_retribuzione_strutturata_e_testo():
    s = enrich.salary_fields({"min": 30000, "max": 40000, "currency": "EUR", "interval": "per-year-salary"}, "")
    assert (s["salary_source"], s["ral_min_annual"], s["salary_transparency"]) == ("structured", 30000, "cifra")
    s = enrich.salary_fields(None, "Stipendio 1.800 € lordi al mese")
    assert (s["salary_period"], s["ral_min_annual"]) == ("month", 1800 * 14)
    assert enrich.salary_fields(None, "Retribuzione commisurata all'esperienza")["salary_transparency"] == "vaga"
    assert enrich.salary_fields(None, "Nessuna informazione")["salary_transparency"] == "assente"


def test_retribuzione_casi_reali_mancati():
    # casi trovati nella validazione: "retributivo" e "k" prima dei numeri
    s = parse_salary("💰 Pacchetto retributivo: 35.000-40.000 | welfare")
    assert (s["min"], s["max"]) == (35000, 40000)
    s = parse_salary("Range retribuzione : €k 50-60")
    assert (s["min"], s["max"]) == (50000, 60000)
    assert parse_salary("We pay attention to 3 things") is None


def test_stage_su_12_mensilita():
    s = enrich.salary_fields(None, "Stage con rimborso spese di 1.000 € al mese", is_internship=True)
    assert s["ral_min_annual"] == 12000


# ---------- periodo rispetto alla legge e plausibilità RAL ----------
def test_periodo_legge():
    assert enrich.posted_date("2026-09-17T10:00:00Z") == "2026-09-17"
    assert enrich.posting_period("2026-06-07", "2026-10-04T08:00:00Z") == "post_legge"
    assert enrich.posting_period("2026-01-15", "2026-10-04T08:00:00Z") == "pre_legge"
    assert enrich.posting_period("2024-03-01", "2026-10-04T08:00:00Z") == "storico"
    assert enrich.posting_period(None, "2026-10-04") is None


def test_ral_implausibile():
    assert annualize(250, "year", "EUR", None) is None          # indennità, non RAL
    assert annualize(2_000_000, "year", "EUR", None) is None    # fatturato
    assert annualize(1500, "month", "EUR", "gross") == 21000


def test_ignora_importi_aziendali():
    # caso reale (examplecorp): presentazione con ricavi in miliardi prima della retribuzione vera
    text = ("Nel 2024 examplecorp ha registrato ricavi consolidati pari a € 17,8 mld, nuovi ordini per € 20,9 mld, "
            "e ha investito € 2,5 mld in attività di R&S. ... Retribuzione annua lorda compresa tra 32.000 e 38.000 €.")
    s = parse_salary(text)
    assert (s["min"], s["max"], s["period"]) == (32000, 38000, "year")
    assert parse_salary("ricavi pari a € 17,8 mld e investito € 2,5 mld") is None


# ---------- casi trovati nella validazione manuale ----------
def test_euro_con_punto_example():
    s = parse_salary("alle seguenti condizioni: Retribuzione Annua Lorda: da €. 65.000 a €. 80.000 + Premio")
    assert (s["min"], s["max"], s["period"]) == (65000, 80000, "year")
    s = parse_salary("Range RAL da €. 35.000 a €. 50.000 Buoni Pasto")
    assert (s["min"], s["max"]) == (35000, 50000)


def test_paga_giornaliera_otb():
    s = parse_salary("Compensation : CCNL Textile Industry, Level 4° - 5° Salary Renges: 70€ - 90€ per day")
    assert (s["min"], s["max"], s["period"]) == (70, 90, "day")
    assert annualize(70, "day", "EUR", None) is None     # non si trasforma in RAL


def test_percentuale_non_e_cifra_esempio():
    s = parse_salary("retribuzione totale prevista al raggiungimento del 100% degli obiettivi: €35,200 - €44,000")
    assert (s["min"], s["max"]) == (35200, 44000)


def test_periodo_casi_validazione_2():
    # "Orario full time" non è paga oraria; "smart/mese" e "14 mensilità" non rendono mensile una RAL
    s = parse_salary("Retribuzione Annua Lorda: da €. 21.000 a €. 24.000 Buoni Pasto Orario full time")
    assert s["period"] == "year"
    assert parse_salary("10 gg smart/mese. Retribuzione e benefit: 45k-55k + MBO")["period"] == "year"
    assert parse_salary("su 14 mensilità La corrispondente RAL è pari a 47.000 €")["period"] == "year"
    # "35.000k": la k è superflua
    assert parse_salary("Gross annual salary no less than: 35.000k")["min"] == 35000
    # "annuncio" non vuol dire "annuo": rimborso stage mensile
    s = parse_salary("con un rimborso spese di: 500€ + 140€ in buoni pasto. Il presente annuncio si rivolge a")
    assert (s["min"], s["period"]) == (500, "month")


# ---------- protezione dell'identità delle aziende nei dati pubblici ----------
from lib import disclosure


def _cell(value, n_jobs, n_companies, top_share, dim="region"):
    return {"period": "post_legge", "macro": "tutti", "dim": dim, "value": value, "n_jobs": n_jobs,
            "n_companies": n_companies, "top_share": top_share, "n_ral": n_jobs,
            "n_ral_companies": n_companies, "ral_top_share": top_share,
            "ral_min_median": 30000, "ral_max_median": 40000}


def test_soglie_e_dominanza():
    out = disclosure.apply_rules([
        _cell("Lombardia", 100, 10, 0.3),
        _cell("Lazio", 50, 6, 0.4),
        _cell("Piemonte", 40, 5, 0.5),
        _cell("Molise", 4, 3, 0.5),          # troppo pochi annunci
        _cell("Umbria", 20, 2, 0.5),         # troppo poche aziende
        _cell("Marche", 30, 4, 0.9),         # un'azienda domina
    ])
    assert {c["value"] for c in out} == {"Lombardia", "Lazio", "Piemonte"}
    assert "top_share" not in out[0]         # i dati di controllo non escono


def test_soppressione_secondaria():
    # una sola cella nascosta nel gruppo: si nasconde anche la più piccola delle visibili
    out = disclosure.apply_rules([
        _cell("Lombardia", 100, 10, 0.3),
        _cell("Lazio", 50, 6, 0.4),
        _cell("Molise", 4, 3, 0.5),
    ])
    assert {c["value"] for c in out} == {"Lombardia"}


def test_mediane_ral_protette():
    c = _cell("Lombardia", 100, 10, 0.3)
    c["n_ral_companies"] = 2                 # cifre da sole 2 aziende: niente mediane
    out = disclosure.apply_rules([c, _cell("Lazio", 50, 6, 0.4)])
    lomb = next(x for x in out if x["value"] == "Lombardia")
    assert lomb["ral_min_median"] is None and lomb["n_jobs"] == 100


def test_seniority_practice_e_grado():
    # "CFO Services" è una practice di consulenza, non un ruolo da CFO
    assert enrich.seniority("CFO Services - Consulente in ambito Finance Transformation - Senior Consultant") == "senior"
    assert enrich.seniority("CFO Services - Consulente in ambito Enterprise Performance Management - Consultant") == "mid"
    assert enrich.seniority("CFO Services – Project Manager Finance Transformation - Consultant") == "mid"
    assert enrich.seniority("Logistic Chief Engineer Fighters & Uav") == "lead"
    assert enrich.seniority("Deputy Store Director BOH") == "manager"
    # i ruoli apicali veri restano tali
    assert enrich.seniority("Group Chief Financial Officer") == "executive"
    assert enrich.seniority("Chief Information Security Officer") == "executive"
    assert enrich.seniority("CFO") == "executive"
    assert enrich.seniority("Head of Brand") == "director"


def test_anni_di_esperienza():
    assert enrich.experience_years("Requisiti: Da 3 a 6 anni di esperienza nel ruolo") == 3
    assert enrich.experience_years("Up to 3-4 years of experience as a business developer") == 3
    assert enrich.experience_years("Esperienza di almeno 5 anni in contesti industriali") == 5
    assert enrich.experience_years("At least 3 years of experience as a Backend Engineer") == 3
    assert enrich.experience_years("Esperienza di 4+ anni in ruoli commerciali") == 4
    assert enrich.experience_years("Expert-Senior level with [4-10 years] of professional experience") == 4
    # storia dell'azienda, non requisito
    assert enrich.experience_years("examplecorp, con quasi 150 anni di esperienza") is None
    assert enrich.experience_years("With 85 years of experience in construction") is None


def test_seniority_con_fonte():
    assert enrich.seniority_with_source("Data Analyst", "Cerchiamo 1-2 anni di esperienza") == ("junior", "esperienza", 1)
    assert enrich.seniority_with_source("Data Analyst", "almeno 6 anni di esperienza") == ("senior", "esperienza", 6)
    assert enrich.seniority_with_source("Senior Data Analyst", "1 anno di esperienza")[:2] == ("senior", "titolo")
    assert enrich.seniority_with_source("Data Analyst", "Ottimo team") == ("mid", "non_indicata", None)
