"""
Etichette leggibili per i valori codificati nel database (filtri e grafici della web app).

Il DB resta con codici stabili (es. 'energia_utility'); l'app mostra le etichette.
export.py le scrive in data/export/public/labels.json.
"""

SECTOR = {
    "banche": "Banche",
    "asset_management": "Gestione del risparmio",
    "assicurazioni": "Assicurazioni",
    "servizi_finanziari": "Servizi finanziari",
    "fintech": "Fintech e insurtech",
    "big4": "Big Four",
    "consulenza": "Consulenza",
    "consulenza_it": "Consulenza IT",
    "marketing_media": "Agenzie marketing e media",
    "media_editoria": "Media ed editoria",
    "energia_utility": "Energia e utility",
    "telco": "Telecomunicazioni",
    "moda_lusso": "Moda e lusso",
    "gdo_retail": "Grande distribuzione e retail",
    "industria": "Industria",
    "automotive": "Automotive",
    "alimentare_bevande": "Alimentare e bevande",
    "beni_consumo": "Beni di consumo",
    "farmaceutico_salute": "Farmaceutica e salute",
    "trasporti_logistica": "Trasporti e logistica",
    "infrastrutture": "Infrastrutture",
    "big_tech": "Big tech",
    "software_saas": "Software",
    "ecommerce_marketplace": "E-commerce e marketplace",
    "formazione": "Formazione",
}

JOB_FUNCTION = {
    "engineering": "Ingegneria e IT",
    "data": "Dati e analytics",
    "product": "Prodotto",
    "design": "Design",
    "project_management": "Project management",
    "sales": "Vendite",
    "retail": "Negozi e punti vendita",
    "marketing": "Marketing e comunicazione",
    "finance": "Finanza e amministrazione",
    "insurance": "Assicurativo (sinistri, attuariale)",
    "hr": "Risorse umane",
    "legal": "Legale, compliance e gare",
    "strategy": "Strategia e pianificazione",
    "admin": "Segreteria e back office",
    "customer_service": "Assistenza clienti",
    "operations": "Operations, produzione e tecnici",
    "consulting": "Consulenza",
    "healthcare": "Sanità",
    "hospitality": "Ristorazione e ospitalità",
    "other": "Altro",
}

SENIORITY = {
    "intern": "Stage e tirocinio",
    "junior": "Junior",
    "mid": "Intermedio",
    "senior": "Senior",
    "lead": "Lead / specialista esperto",
    "manager": "Manager",
    "director": "Direttore",
    "executive": "Dirigente apicale",
}

CONTRACT_TYPE = {
    "indeterminato": "Tempo indeterminato",
    "determinato": "Tempo determinato",
    "stage": "Stage",
    "apprendistato": "Apprendistato",
    "freelance": "Libera professione",
    "somministrazione": "Somministrazione",
}

WORK_SCHEDULE = {"full_time": "Tempo pieno", "part_time": "Part time"}
WORKPLACE_TYPE = {"onsite": "In sede", "hybrid": "Ibrido", "remote": "Da remoto"}
SALARY_TRANSPARENCY = {
    "cifra": "Indica la retribuzione",
    "vaga": "Solo formula vaga",
    "assente": "Nessuna indicazione",
}
POSTING_PERIOD = {
    "post_legge": "Dal 7 giugno 2026 (D.Lgs. 96/2026)",
    "pre_legge": "Prima della legge (ultimi 12 mesi)",
    "storico": "Online da oltre 12 mesi",
}
COMPANY_TYPE = {
    "quotata_ftse_mib": "Quotata FTSE MIB",
    "quotata_altro": "Quotata (altri indici)",
    "multinazionale_estera": "Multinazionale estera",
    "privata_italiana": "Privata italiana",
    "scaleup_startup": "Scaleup e startup",
    "pubblica": "A controllo pubblico",
}

# Macro-settori per la versione pubblica: ognuno deve contenere almeno 3 aziende con annunci
# (Media e comunicazione da sola ne aveva 2: è unita a tecnologia)
MACRO_OF_SECTOR = {
    "banche": "finanza", "asset_management": "finanza", "assicurazioni": "finanza",
    "servizi_finanziari": "finanza", "fintech": "finanza",
    "big4": "consulenza", "consulenza": "consulenza", "consulenza_it": "consulenza",
    "software_saas": "tecnologia_media", "big_tech": "tecnologia_media",
    "ecommerce_marketplace": "tecnologia_media", "formazione": "tecnologia_media",
    "marketing_media": "tecnologia_media", "media_editoria": "tecnologia_media",
    "energia_utility": "energia_infrastrutture", "telco": "energia_infrastrutture",
    "infrastrutture": "energia_infrastrutture", "trasporti_logistica": "energia_infrastrutture",
    "industria": "industria_salute", "automotive": "industria_salute",
    "farmaceutico_salute": "industria_salute", "alimentare_bevande": "industria_salute",
    "beni_consumo": "industria_salute",
    "moda_lusso": "moda_retail", "gdo_retail": "moda_retail",
}
MACRO_SECTOR = {
    "finanza": "Finanza e assicurazioni",
    "consulenza": "Consulenza",
    "tecnologia_media": "Tecnologia e media",
    "energia_infrastrutture": "Energia, telco e infrastrutture",
    "industria_salute": "Industria e farmaceutica",
    "moda_retail": "Moda e distribuzione",
}
REGION_EXTRA = {"Remoto": "Da remoto", "Sede non specificata": "Sede non specificata"}

ALL = {
    "macro_sector": MACRO_SECTOR,
    "sector": SECTOR,
    "job_function": JOB_FUNCTION,
    "seniority": SENIORITY,
    "contract_type": CONTRACT_TYPE,
    "work_schedule": WORK_SCHEDULE,
    "workplace_type": WORKPLACE_TYPE,
    "salary_transparency": SALARY_TRANSPARENCY,
    "posting_period": POSTING_PERIOD,
    "company_type": COMPANY_TYPE,
}
