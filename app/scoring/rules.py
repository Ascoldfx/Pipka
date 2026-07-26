from __future__ import annotations

import html
import re

from app.models.job import Job
from app.models.user import UserProfile

# Director+ level only — no plain "Manager"
DIRECTOR_KEYWORDS = [
    "director", "head of", "vp ", "vice president", "chief",
    "coo", "cfo", "cpo", "cso", "cro",  # C-suite
    "senior director", "global director",
    "principal", "partner",
    # Interim / Crisis / Turnaround — senior by nature
    "interim manager", "interim director", "interim head",
    "crisis manager", "crisis director", "krisenmanager",
    "turnaround manager", "turnaround director",
    "restructuring",
    "growth director",
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
    # Consulting
    "consultant", "consulting", "berater", "beratung",
    "advisory", "advisor",
]

DOMAIN_KEYWORDS = [
    "supply chain", "procurement", "einkauf", "beschaffung", "logistics",
    "logistik", "operations", "s2p", "source to pay", "sourcing",
    "purchasing", "lieferkette", "warehouse", "lager",
    "demand planning", "inventory", "distribution", "fulfillment",
    "supplier", "vendor management", "category management",
    "strategic sourcing", "indirect procurement", "direct procurement",
    # Crisis / Turnaround / Transformation — closely related to operations
    "crisis management", "turnaround", "transformation",
    "restructuring", "interim management", "business continuity",
    "operational excellence", "continuous improvement",
    "growth",  # growth roles often overlap with operations leadership
]

ENGLISH_FRIENDLY_SIGNALS = [
    "english", "international", "global", "multinational",
    "working language: english", "english-speaking",
    "startup", "remote",
]

# Strong German role words in a job title.  Short titles are a poor fit for
# statistical language detection, so keep this deliberately deterministic.
# German locations, company names and the common "(m/w/d)" suffix are not
# signals by themselves.
GERMAN_TITLE_PATTERNS = [
    re.compile(r"\bgeschäftsführ\w*\b"),
    re.compile(r"\bgeschaeftsfuehr\w*\b"),
    re.compile(
        r"\b(?:einkaufs|bereichs|abteilungs|standort|werks|betriebs|"
        r"niederlassungs|produktions|logistik)leiter(?:in)?\b"
    ),
    re.compile(r"\b(?:kaufmännische\w*|kaufmaennische\w*|technische\w*)\s+leiter(?:in)?\b"),
    re.compile(r"\bleiter(?:in)?\b"),
    re.compile(r"\bleitung\b"),
    re.compile(r"\bdirektor(?:in)?\b"),
    re.compile(r"\b(?:produkt|projekt|krisen)manager(?:in)?\b"),
    re.compile(r"\bvorstand\b"),
]

# A bilingual title such as "Einkaufsleiter / Head of Procurement" is not
# rejected from the title alone.  Its description must still satisfy the
# existing English-working-language gate.
ENGLISH_ROLE_PATTERN = re.compile(
    r"\b(?:director|head|chief|officer|manager|lead|president)\b|"
    r"\bvice president\b|\bvp\b"
)

# "Operations" alone is too broad: search engines routinely return sales,
# retail and revenue leadership for a "Director Operations" query.  Reject
# those commercial functions unless the title itself explicitly names one of
# the user's operational domains.
COMMERCIAL_FUNCTION_PATTERNS = [
    re.compile(r"\b(?:commercial|retail|sales|revenue|store)\s+operations\b"),
    re.compile(
        r"\b(?:commercial|retail|sales|revenue|store)\s+"
        r"(?:operations\s+)?(?:director|manager|lead|head)\b"
    ),
    re.compile(
        r"\b(?:director|head|chief)\s+(?:of\s+)?"
        r"(?:commercial|retail|sales|revenue|store)\b"
    ),
    re.compile(r"\bchief commercial officer\b"),
]

CORE_FUNCTION_TITLE_PATTERN = re.compile(
    r"\b(?:supply chain|procurement|sourcing|purchasing|logistics|"
    r"einkauf|beschaffung|logistik|lieferkette)\b"
)

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


def _normalise_title(title: str) -> str:
    """Return title text suitable for deterministic language checks."""
    value = html.unescape(title or "").replace("\\-", "-")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[^\wäöüß]+", " ", value.casefold(), flags=re.UNICODE)
    return " ".join(value.split())


def is_clearly_german_title(title: str) -> bool:
    """Detect an explicitly German role title without guessing from location."""
    normalised = _normalise_title(title)
    has_german_role = any(pattern.search(normalised) for pattern in GERMAN_TITLE_PATTERNS)
    if not has_german_role:
        return False

    # Let the description decide genuinely bilingual titles.
    return ENGLISH_ROLE_PATTERN.search(normalised) is None


def is_commercial_function_title(title: str) -> bool:
    """Reject commercial/retail operations while preserving explicit SC roles."""
    normalised = _normalise_title(title)
    if CORE_FUNCTION_TITLE_PATTERN.search(normalised):
        return False
    return any(pattern.search(normalised) for pattern in COMMERCIAL_FUNCTION_PATTERNS)


def pre_filter(job: Job, profile: UserProfile | None) -> tuple[bool, str]:
    """Fast rule-based pre-filter. Returns (pass, bucket) where bucket is low/medium/high."""
    title_lower = job.title.lower()
    desc_lower = (job.description or "").lower()
    text = f"{title_lower} {desc_lower}"

    # Hard reject: too junior or wrong function
    if any(kw in title_lower for kw in REJECT_TITLE_KEYWORDS):
        return False, "low"

    # "Operations" can mean retail/sales/revenue rather than the user's
    # supply-chain and transformation background.
    if is_commercial_function_title(job.title):
        return False, "low"

    # Hard reject: non-English/German language required
    if any(kw in desc_lower for kw in FOREIGN_LANGUAGE_REQUIRED):
        return False, "low"

    # User-defined Exclusions
    if profile and profile.excluded_keywords:
        for kw in profile.excluded_keywords:
            if kw and kw.lower() in text:
                return False, "low"

    # English-only filter: a clearly German role title is a hard reject even
    # when the description contains misleading markers such as "remote" or
    # an English-language job-board footer.
    if profile and getattr(profile, "english_only", False):
        if is_clearly_german_title(job.title):
            return False, "low"

        english_friendly = any(signal in text for signal in ENGLISH_FRIENDLY_SIGNALS)
        if not english_friendly:
            return False, "low"

    # Domain check — must be in supply chain / procurement / operations
    domain_match = any(kw in title_lower or kw in desc_lower for kw in DOMAIN_KEYWORDS)
    if not domain_match:
        return False, "low"

    # Salary is deliberately ignored: most source listings do not provide it,
    # so it cannot be a reliable filter or scoring signal.

    # Work mode filter
    if profile and profile.work_mode and profile.work_mode != "any":
        if profile.work_mode == "remote":
            # Reject explicitly onsite jobs
            if job.is_remote is False:
                return False, "low"
            # If is_remote is unknown, require "remote" keyword in text
            if job.is_remote is None and "remote" not in text:
                return False, "low"
        elif profile.work_mode == "onsite":
            # Reject explicitly remote jobs
            if job.is_remote is True:
                return False, "low"
        elif profile.work_mode == "hybrid":
            # Reject explicitly remote-only (no hybrid mention) jobs
            if job.is_remote is True and "hybrid" not in text:
                return False, "low"

    # Country check
    if profile and profile.preferred_countries and job.country:
        if job.country.lower() not in [c.lower() for c in profile.preferred_countries]:
            return False, "low"

    # Director-level seniority
    is_director = any(kw in title_lower for kw in DIRECTOR_KEYWORDS)

    # "Senior Manager" is borderline — allow but lower bucket
    is_senior_manager = "senior manager" in title_lower or "lead" in title_lower

    # Plain "Manager" without Director/Head/VP → tier2 queue (scored after tier1 is clear)
    is_plain_manager = (
        "manager" in title_lower
        and not is_director
        and not is_senior_manager
    )
    if is_plain_manager:
        return False, "manager_tier2"

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
