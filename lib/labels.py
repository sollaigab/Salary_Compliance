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
# Micro-sectors: finer than SECTOR, each tied to a NACE Rev. 2 division (the EU classification of economic
# activities) so the data stays comparable across countries. Assigned per company in the private seed list.
# code: (label, NACE division)
MICRO_SECTOR = {
    # finance
    "retail_banking": ("Retail and commercial banking", "64"),
    "digital_banking": ("Digital banks", "64"),
    "investment_banking": ("Investment banking and brokerage", "66"),
    "wealth_management": ("Wealth and asset management", "66"),
    "consumer_credit": ("Consumer credit", "64"),
    "credit_servicing": ("Credit servicing and collection", "82"),
    "credit_information": ("Credit information and risk data", "63"),
    "public_finance": ("Public finance institutions", "64"),
    "insurance": ("Insurance", "65"),
    "payments": ("Payments and fintech platforms", "66"),
    "digital_investing": ("Digital investing", "66"),
    "postal_services": ("Postal and financial services", "53"),
    # consulting and professional services
    "strategy_consulting": ("Strategy consulting", "70"),
    "management_consulting": ("Management and risk consulting", "70"),
    "audit_advisory": ("Audit, tax and advisory", "69"),
    "it_services": ("IT services and systems integration", "62"),
    "engineering_services": ("Engineering and R&D services", "71"),
    "advertising": ("Advertising and marketing services", "73"),
    # technology and media
    "global_tech": ("Global tech and cloud", "62"),
    "business_software": ("Business software", "58"),
    "consumer_apps": ("Consumer apps and AI", "58"),
    "hosting": ("Hosting and data centres", "63"),
    "public_digital": ("Public digital services", "62"),
    "digital_health": ("Digital health", "86"),
    "online_marketplaces": ("Online marketplaces and comparison", "63"),
    "online_travel": ("Online travel", "79"),
    "online_delivery": ("Online grocery and delivery", "47"),
    "fashion_ecommerce": ("Fashion e-commerce", "47"),
    "education": ("Education and innovation hubs", "85"),
    "broadcasting": ("TV, streaming and broadcasting", "60"),
    "publishing": ("Publishing and news", "58"),
    "gaming_betting": ("Gaming and betting", "92"),
    # energy, telco, infrastructure, transport
    "power": ("Power generation and supply", "35"),
    "multi_utilities": ("Multi-utilities", "35"),
    "oil_gas": ("Oil, gas and energy services", "06"),
    "energy_grids": ("Energy grids", "35"),
    "telecom_operators": ("Telecom operators", "61"),
    "telecom_infrastructure": ("Telecom infrastructure", "61"),
    "telecom_equipment": ("Telecom equipment", "26"),
    "rail": ("Rail transport", "49"),
    "local_transport": ("Local public transport", "49"),
    "aviation": ("Airlines and airports", "51"),
    "motorways": ("Motorways", "52"),
    "logistics": ("Parcels and logistics", "53"),
    "shipping": ("Shipping", "50"),
    # industry and pharma
    "aerospace_defence": ("Aerospace, defence and shipbuilding", "30"),
    "automotive_oem": ("Vehicle manufacturers", "29"),
    "auto_components": ("Automotive components", "29"),
    "machinery_automation": ("Machinery and automation", "28"),
    "electrical_electronics": ("Electrical equipment and semiconductors", "27"),
    "steel_materials": ("Steel, building materials and chemicals", "24"),
    "construction": ("Construction", "42"),
    "consumer_durables": ("Appliances and consumer durables", "27"),
    "fmcg": ("Household and personal care", "20"),
    "tobacco": ("Tobacco", "12"),
    "food": ("Food", "10"),
    "beverages": ("Beverages", "11"),
    "pharmaceuticals": ("Pharmaceuticals", "21"),
    "medtech": ("Medical devices and diagnostics", "32"),
    "healthcare_services": ("Healthcare retail and services", "47"),
    # fashion and retail
    "luxury_fashion": ("Luxury fashion", "14"),
    "jewellery_eyewear": ("Jewellery and eyewear", "32"),
    "fashion_retail": ("Fashion retail", "47"),
    "grocery_retail": ("Grocery retail", "47"),
    "electronics_retail": ("Electronics retail", "47"),
    "home_sports_retail": ("Home and sports retail", "47"),
    "beauty_retail": ("Beauty retail", "47"),
    "food_service": ("Food service and travel retail", "56"),
}

REGION_EXTRA = {"Remote": "Remote", "Unspecified": "Location not stated"}
COUNTRY = {"IT": "Italy", "DE": "Germany", "NL": "Netherlands", "FR": "France", "not_stated": "Remote in Europe"}

ALL = {
    "country": COUNTRY,
    "macro_sector": MACRO_SECTOR,
    "sector": SECTOR,
    "micro_sector": {code: label for code, (label, _) in MICRO_SECTOR.items()},
    "job_function": JOB_FUNCTION,
    "seniority": SENIORITY,
    "contract_type": CONTRACT_TYPE,
    "workplace_type": WORKPLACE_TYPE,
    "salary_transparency": SALARY_TRANSPARENCY,
    "posting_period": POSTING_PERIOD,
}
