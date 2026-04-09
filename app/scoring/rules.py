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

# These in title = too junior, auto-reject
REJECT_TITLE_KEYWORDS = [
    "specialist", "analyst", "coordinator", "assistant", "clerk",
    "sachbearbeiter", "referent", "mitarbeiter", "fachkraft",
    "junior", "trainee", "werkstudent", "praktikant", "azubi",
    "intern", "student",
    "buyer",  # operational buyer, not strategic
    "dispatcher", "planner",  # too operational
]

DOMAIN_KEYWORDS = [
    "supply chain", "procurement", "einkauf", "beschaffung", "logistics",
    "logistik", "operations", "s2p", "source to pay", "sourcing",
    "purchasing", "lieferkette", "warehouse", "lager",
]

ENGLISH_FRIENDLY_SIGNALS = [
    "english", "international", "global", "multinational",
    "working language: english", "english-speaking",
    "startup", "remote",
]


def pre_filter(job: Job, profile: UserProfile | None) -> tuple[bool, str]:
    """Fast rule-based pre-filter. Returns (pass, bucket) where bucket is low/medium/high."""
    title_lower = job.title.lower()
    desc_lower = (job.description or "").lower()
    text = f"{title_lower} {desc_lower}"

    # Hard reject: too junior titles
    if any(kw in title_lower for kw in REJECT_TITLE_KEYWORDS):
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
