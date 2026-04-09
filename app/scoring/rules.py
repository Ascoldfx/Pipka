from __future__ import annotations

from app.models.job import Job
from app.models.user import UserProfile

# Director+ level only — no plain "Manager"
DIRECTOR_KEYWORDS = [
    "director", "head of", "vp ", "vice president", "chief",
    "coo", "cfo", "cpo", "cso",  # C-suite
    "senior director", "global director",
    "principal", "partner",
    # German equivalents
    "direktor", "leiter", "abteilungsleiter", "bereichsleiter",
    "geschäftsführer", "geschaeftsfuehrer",
]

# These in title = too junior or wrong function, auto-reject
REJECT_TITLE_KEYWORDS = [
    # Junior / operational
    "specialist", "analyst", "coordinator", "assistant", "clerk",
    "sachbearbeiter", "referent", "mitarbeiter", "fachkraft",
    "junior", "trainee", "werkstudent", "praktikant", "azubi",
    "intern", "student",
    "buyer",  # operational buyer, not strategic
    "dispatcher", "planner",  # too operational
    "merchandiser",  # retail/marketing
    # Wrong function — NOT supply chain / procurement / operations
    "marketing", "sales director", "account executive", "account manager",
    "hr director", "hr manager", "human resources", "people operations",
    "people lead", "talent", "recruiting", "recruitment",
    "engineering manager", "software", "developer", "data scientist",
    "product manager", "product director", "product lead",
    "finance director", "financial controller", "accounting",
    "legal", "compliance director", "regulatory",
    "creative director", "design director", "art director",
    "editorial", "content director", "communications director",
    "customer success", "customer service", "support manager",
    "research director", "r&d director", "scientific",
    "medical director", "clinical",
    "real estate", "property",
    "founding", "co-founder",
]

DOMAIN_KEYWORDS = [
    "supply chain", "procurement", "einkauf", "beschaffung", "logistics",
    "logistik", "operations", "s2p", "source to pay", "sourcing",
    "purchasing", "lieferkette", "warehouse", "lager",
    "demand planning", "inventory", "distribution", "fulfillment",
    "supplier", "vendor management", "category management",
    "strategic sourcing", "indirect procurement", "direct procurement",
]

ENGLISH_FRIENDLY_SIGNALS = [
    "english", "international", "global", "multinational",
    "working language: english", "english-speaking",
    "startup", "remote",
]

# Non-English/non-German language requirements → reject
FOREIGN_LANGUAGE_REQUIRED = [
    # French
    "langue requise", "français", "francais", "french required",
    "french: native", "french: fluent", "french fluency",
    "courant français", "courant francais",
    "maîtrise du français", "maitrise du francais",
    # Spanish
    "español requerido", "spanish required", "spanish: native",
    # Italian
    "italiano richiesto", "italian required",
    # Dutch (for NL jobs requiring native Dutch)
    "nederlands vereist", "dutch: native", "native dutch required",
    "vloeiend nederlands",
    # Polish, Czech etc.
    "polski wymagany", "polish required",
    "čeština", "czech required",
]


def pre_filter(job: Job, profile: UserProfile | None) -> tuple[bool, str]:
    """Fast rule-based pre-filter. Returns (pass, bucket) where bucket is low/medium/high."""
    title_lower = job.title.lower()
    desc_lower = (job.description or "").lower()
    text = f"{title_lower} {desc_lower}"

    # Hard reject: too junior or wrong function
    if any(kw in title_lower for kw in REJECT_TITLE_KEYWORDS):
        return False, "low"

    # Hard reject: non-English/German language required
    if any(kw in desc_lower for kw in FOREIGN_LANGUAGE_REQUIRED):
        return False, "low"

    # Domain check — must be in supply chain / procurement / operations
    domain_match = any(kw in title_lower or kw in desc_lower for kw in DOMAIN_KEYWORDS)
    if not domain_match:
        return False, "low"

    # Salary floor check (strict: 100k+)
    if profile and profile.min_salary and job.salary_min:
        if job.salary_min < profile.min_salary * 0.7:  # 70k floor for 100k target
            return False, "low"

    # Country check
    if profile and profile.preferred_countries and job.country:
        if job.country.lower() not in [c.lower() for c in profile.preferred_countries]:
            return False, "low"

    # Director-level seniority
    is_director = any(kw in title_lower for kw in DIRECTOR_KEYWORDS)

    # "Senior Manager" is borderline — allow but lower bucket
    is_senior_manager = "senior manager" in title_lower or "lead" in title_lower

    # Plain "Manager" without Director/Head/VP → reject
    is_plain_manager = (
        "manager" in title_lower
        and not is_director
        and not is_senior_manager
    )
    if is_plain_manager:
        return False, "low"

    # English-friendly signal
    english_friendly = any(signal in text for signal in ENGLISH_FRIENDLY_SIGNALS)

    # Scoring buckets
    if is_director and english_friendly:
        return True, "high"
    if is_director:
        return True, "high"
    if is_senior_manager and english_friendly:
        return True, "medium"
    if is_senior_manager:
        return True, "medium"

    # Domain match but no seniority signal — low priority
    return True, "medium"
