from __future__ import annotations

from app.models.job import Job
from app.models.user import UserProfile

SENIOR_KEYWORDS = [
    "director", "head of", "vp ", "vice president", "chief", "coo", "cfo",
    "senior manager", "lead", "principal", "head", "leiter", "direktor",
    "manager", "teamleiter", "abteilungsleiter",
]

DOMAIN_KEYWORDS = [
    "supply chain", "procurement", "einkauf", "beschaffung", "logistics",
    "logistik", "operations", "s2p", "source to pay", "sourcing",
    "purchasing", "lieferkette", "warehouse", "lager",
]

ENGLISH_FRIENDLY_SIGNALS = [
    "english", "international", "global", "multinational",
    "working language: english", "english-speaking",
    "startup", "remote", "hybrid",
]


def pre_filter(job: Job, profile: UserProfile | None) -> tuple[bool, str]:
    """Fast rule-based pre-filter. Returns (pass, bucket) where bucket is low/medium/high."""
    title_lower = job.title.lower()
    desc_lower = (job.description or "").lower()
    text = f"{title_lower} {desc_lower}"

    # Seniority check
    seniority_match = any(kw in title_lower for kw in SENIOR_KEYWORDS)

    # Domain check
    domain_match = any(kw in title_lower or kw in desc_lower for kw in DOMAIN_KEYWORDS)

    if not domain_match:
        return False, "low"

    # Salary floor check
    if profile and profile.min_salary and job.salary_min:
        if job.salary_min < profile.min_salary * 0.7:
            return False, "low"

    # Location check
    if profile and profile.preferred_countries and job.country:
        if job.country.lower() not in [c.lower() for c in profile.preferred_countries]:
            return False, "low"

    # Work mode check
    if profile and profile.work_mode == "remote" and job.is_remote is False:
        return False, "low"

    # English-friendly bonus
    english_friendly = any(signal in text for signal in ENGLISH_FRIENDLY_SIGNALS)

    # Scoring: high = seniority + domain + english, medium = domain only
    if seniority_match and domain_match and english_friendly:
        return True, "high"
    if seniority_match and domain_match:
        return True, "high"
    if domain_match and english_friendly:
        return True, "high"
    if domain_match:
        return True, "medium"
    return True, "low"
