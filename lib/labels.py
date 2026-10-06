"""
Readable labels for the coded values stored in the database (web app filters and charts).

The DB keeps stable codes (e.g. 'energy_utilities'); the app shows the labels.
export.py writes them to webapp/data/labels.json.
"""

SECTOR = {
    "banks": "Banks",
    "asset_management": "Asset management",
    "insurance": "Insurance",
    "financial_services": "Financial services",
    "fintech": "Fintech and insurtech",
    "big4": "Big Four",
    "consulting": "Consulting",
    "it_consulting": "IT consulting",
    "marketing_media": "Marketing and media agencies",
    "media_publishing": "Media and publishing",
    "energy_utilities": "Energy and utilities",
    "telco": "Telecommunications",
    "fashion_luxury": "Fashion and luxury",
    "retail": "Retail and grocery",
    "industrial": "Industrial",
    "automotive": "Automotive",
    "food_beverage": "Food and beverage",
    "consumer_goods": "Consumer goods",
    "pharma_health": "Pharma and healthcare",
    "transport_logistics": "Transport and logistics",
    "infrastructure": "Infrastructure",
    "big_tech": "Big tech",
    "software_saas": "Software",
    "ecommerce_marketplace": "E-commerce and marketplaces",
    "education": "Education",
}

JOB_FUNCTION = {
    "engineering": "Engineering and IT",
    "data": "Data and analytics",
    "product": "Product",
    "design": "Design",
    "project_management": "Project management",
    "sales": "Sales",
    "retail": "Stores",
    "marketing": "Marketing and communication",
    "finance": "Finance and accounting",
    "insurance": "Insurance (claims, actuarial)",
    "hr": "Human resources",
    "legal": "Legal, compliance and tenders",
    "strategy": "Strategy and planning",
    "admin": "Office support and back office",
    "customer_service": "Customer service",
    "operations": "Operations, production and technicians",
    "consulting": "Consulting",
    "healthcare": "Healthcare",
    "hospitality": "Food service and hospitality",
    "other": "Other",
}

SENIORITY = {
    "intern": "Internship",
    "junior": "Junior",
    "mid": "Mid-level",
    "senior": "Senior",
    "lead": "Lead / expert specialist",
    "manager": "Manager",
    "director": "Director",
    "executive": "Executive",
}

CONTRACT_TYPE = {
    "permanent": "Permanent",
    "fixed_term": "Fixed term",
    "internship": "Internship",
    "apprenticeship": "Apprenticeship",
    "freelance": "Freelance",
    "agency": "Agency work",
}

WORKPLACE_TYPE = {"onsite": "On site", "hybrid": "Hybrid", "remote": "Remote"}
SALARY_TRANSPARENCY = {
    "figure": "States the pay",
    "vague": "Vague wording only",
    "none": "No information",
}
POSTING_PERIOD = {
    "post_law": "From 7 June 2026",
    "pre_law": "Before the law",
    "old": "Online for over a year",
}

# Macro-sectors for the public version: each must include at least 3 companies with job ads
# (media and communication alone had 2, so it is merged with technology)
MACRO_OF_SECTOR = {
    "banks": "finance", "asset_management": "finance", "insurance": "finance",
    "financial_services": "finance", "fintech": "finance",
    "big4": "consulting", "consulting": "consulting", "it_consulting": "consulting",
    "software_saas": "tech_media", "big_tech": "tech_media",
    "ecommerce_marketplace": "tech_media", "education": "tech_media",
    "marketing_media": "tech_media", "media_publishing": "tech_media",
    "energy_utilities": "energy_infrastructure", "telco": "energy_infrastructure",
    "infrastructure": "energy_infrastructure", "transport_logistics": "energy_infrastructure",
    "industrial": "industry_pharma", "automotive": "industry_pharma",
    "pharma_health": "industry_pharma", "food_beverage": "industry_pharma",
    "consumer_goods": "industry_pharma",
    "fashion_luxury": "fashion_retail", "retail": "fashion_retail",
}
MACRO_SECTOR = {
    "finance": "Finance and insurance",
    "consulting": "Consulting",
    "tech_media": "Technology and media",
    "energy_infrastructure": "Energy, telco and infrastructure",
    "industry_pharma": "Industry and pharma",
    "fashion_retail": "Fashion and retail",
}
REGION_EXTRA = {"Remote": "Remote", "Unspecified": "Location not stated"}

ALL = {
    "macro_sector": MACRO_SECTOR,
    "sector": SECTOR,
    "job_function": JOB_FUNCTION,
    "seniority": SENIORITY,
    "contract_type": CONTRACT_TYPE,
    "workplace_type": WORKPLACE_TYPE,
    "salary_transparency": SALARY_TRANSPARENCY,
    "posting_period": POSTING_PERIOD,
}
