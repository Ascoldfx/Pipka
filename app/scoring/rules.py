from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models.job import Job
from app.models.user import UserProfile
from app.sources.country_queries import expand_queries_for_country

# Seniority is read from the TITLE only. German compounds (Standortleiter,
# Betriebsdirektor, Fachbereichsleitung) are matched inside the word.
DIRECTOR_TITLE_PATTERN = re.compile(
    r"\b(?:director|head|[es]?vp|vice president|chief|c[oe]o|cpo|cfo|cso|cro|md|"
    r"interim|krisenmanager|turnaround|restructuring|"
    r"diretor|diretora|head de|vice-presidente|superintendente|"
    r"gerente executiv[oa]|gerente nacional)\b"
    r"|direktor|leiter|leitung|gesch[aä]ftsf[uü]hr"
)

# Mid-senior markers. "Principal"/"Partner"/"Lead" alone describe engineers,
# consultants and sales roles too, so they only count with the target
# function in the title.
SENIOR_TITLE_PATTERN = re.compile(
    r"\b(?:senior manager|lead|leader|teamlead|principal|partner|gerente s[eê]nior)\b"
)

# These in title = too junior or wrong function, auto-reject
REJECT_TITLE_KEYWORDS = [
    # Junior / operational
    "specialist", "analyst", "coordinator", "assistant", "clerk",
    "sachbearbeiter", "referent", "mitarbeiter", "fachkraft",
    "junior", "trainee", "werkstudent", "praktikant", "azubi",
    "student",
    "buyer",  # operational buyer, not strategic
    "dispatcher", "planner",  # too operational
    "merchandiser",  # retail/marketing
    # Brazilian Portuguese junior / operational
    "analista", "coordenador", "coordenadora", "assistente",
    "estagiário", "estagiario", "estágio", "estagio", "aprendiz",
    "auxiliar", "operador", "operadora", "recepcionista",
    "técnico", "tecnico", "supervisor", "júnior", " jr", "jr ",
    "comprador", "compradora", "planejador", "planejadora",
    # Wrong function — NOT supply chain / procurement / operations
    "marketing", "sales director", "business development",
    "account executive", "account manager",
    "diretor comercial", "diretora comercial", "diretor de vendas",
    "diretora de vendas", "desenvolvimento de negócios",
    "desenvolvimento de negocios", "executivo de negócios",
    "executivo de negocios", "gerente comercial", "gerente de vendas",
    "key account",
    "hr director", "hr manager", "human resources", "people operations",
    "recursos humanos",
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

INTERN_TITLE_PATTERN = re.compile(r"\bintern(?:ship)?s?\b", re.IGNORECASE)

DOMAIN_KEYWORDS = [
    "supply chain", "procurement", "einkauf", "beschaffung", "logistics",
    "logistik", "operations", "s2p", "source to pay", "sourcing",
    "purchasing", "lieferkette", "warehouse", "lager",
    "demand planning", "inventory", "distribution", "fulfillment",
    "supplier", "vendor management", "category management",
    "strategic sourcing", "indirect procurement", "direct procurement",
    # Brazilian Portuguese equivalents
    "cadeia de suprimentos", "suprimentos", "compras", "compras estratégicas",
    "compras estrategicas", "logística", "logistica", "operações", "operacoes",
    "abastecimento", "planejamento de demanda", "gestão de fornecedores",
    "gestao de fornecedores", "estoque", "distribuição", "distribuicao",
    "excelência operacional", "excelencia operacional",
    # Crisis / Turnaround / Transformation — closely related to operations
    "crisis management", "turnaround", "transformation",
    "restructuring", "interim management", "business continuity",
    "operational excellence", "continuous improvement",
    "growth",  # growth roles often overlap with operations leadership
]

# "growth" is a useful description signal but too generic in a title
# ("Growth Marketer", "Growth Engineer"); growth targets from the profile are
# matched exactly by matches_explicit_target_title instead.
TITLE_DOMAIN_KEYWORDS = [kw for kw in DOMAIN_KEYWORDS if kw != "growth"]

# High-frequency function words provide a deterministic, dependency-free
# language classifier for long vacancy descriptions.  We only hard-reject a
# confident non-English result; ambiguous/short text is sent to AI instead of
# risking a false negative.
LANGUAGE_STOPWORDS = {
    "en": {
        "the", "and", "of", "to", "in", "for", "with", "that", "this",
        "will", "you", "your", "our", "we", "are", "as", "on", "from",
        "an", "be", "is", "at", "by", "have", "has", "experience",
        "role", "team", "skills", "responsibilities", "requirements",
    },
    "de": {
        "der", "die", "das", "und", "zu", "in", "für", "mit", "von",
        "den", "dem", "des", "ein", "eine", "einer", "einem", "einen",
        "auf", "als", "wir", "sie", "ihre", "ist", "sind", "werden",
        "du", "deine", "unser", "bei", "oder", "durch", "erfahrung",
        "aufgaben", "anforderungen",
    },
    "fr": {
        "le", "la", "les", "de", "des", "du", "et", "à", "en", "pour",
        "avec", "dans", "un", "une", "vous", "votre", "nous", "notre",
        "est", "sont", "sur", "par", "ce", "cette", "expérience",
        "missions", "profil",
    },
    "nl": {
        "de", "het", "een", "en", "van", "voor", "met", "in", "op",
        "als", "je", "jouw", "wij", "ons", "is", "zijn", "aan", "door",
        "bij", "naar", "dit", "dat", "ervaring", "functie", "taken",
    },
    "es": {
        "el", "la", "los", "las", "de", "del", "y", "en", "para",
        "con", "un", "una", "su", "sus", "nuestro", "como", "por",
        "es", "son", "se", "que", "experiencia", "responsabilidades",
        "requisitos",
    },
    "it": {
        "il", "lo", "la", "i", "gli", "le", "di", "del", "e", "in",
        "per", "con", "un", "una", "come", "che", "si", "è", "sono",
        "nostro", "vostro", "esperienza", "responsabilità", "requisiti",
    },
    "pt": {
        "o", "a", "os", "as", "de", "do", "da", "dos", "das", "e", "em",
        "para", "com", "um", "uma", "que", "por", "como", "se", "no", "na",
        "nos", "nas", "sua", "suas", "seu", "seus", "nossa", "nosso",
        "você", "voce", "ser", "ter", "é", "são", "esta", "está",
        "experiência", "experiencia", "responsabilidades", "requisitos",
        "atividades", "empresa", "equipe", "vaga",
    },
}

INVALID_EXCLUSION_VALUES = {"", "nan", "none", "null", "n/a", "unknown"}

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
    re.compile(
        r"\b(?:comercial|comerciais|varejo|vendas|receita|lojas?)\s+"
        r"(?:e\s+)?opera(?:ç|c)(?:ão|ao|ões|oes)\b"
    ),
    re.compile(
        r"\b(?:diretor(?:a)?|head|chief)\s+(?:de\s+|da\s+|do\s+)?"
        r"(?:comercial|varejo|vendas|receita|lojas?)\b"
    ),
    re.compile(r"\bopera(?:ç|c)(?:ão|ao|ões|oes)\s+(?:comerciais|de\s+varejo|de\s+vendas)\b"),
]

CORE_FUNCTION_TITLE_PATTERN = re.compile(
    r"\b(?:supply chain|procurement|sourcing|purchasing|logistics|"
    r"einkauf|beschaffung|logistik|lieferkette|cadeia de suprimentos|"
    r"suprimentos|compras|logística|abastecimento)\b"
)

# VP roles are opt-in: the candidate considers them unrealistic in Germany
# and removed VP from the target list. Matched on a normalised title, where
# hyphens are already spaces ("vice-presidente" -> "vice presidente").
VP_ROLE_PATTERN = re.compile(
    r"\b(?:[aes]?vp|vice\s+president(?:e|a)?|vizepr[aä]sident(?:in)?)\b"
)

# The candidate's strongest experience; drives queue order only, never
# filtering. "einkauf" is matched inside compounds (Einkaufsleiter).
PROCUREMENT_TITLE_PATTERN = re.compile(
    r"\b(?:procurement|purchasing|sourcing|cpo|buying|category management|"
    r"source to pay|s2p|beschaffung|compras|suprimentos)\b|einkauf"
)

COO_ROLE_PATTERN = re.compile(
    r"^(?:(?:group|regional|global|interim|acting|deputy|country|division|"
    r"apac|emea|mena)\s+)*(?:coo|chief\s+(?:operating|operations)\s+officer)(?:\b|$)"
)

TARGET_TITLE_FILLER_TOKENS = {
    "of",
    "de",
    "da",
    "do",
    # Gender suffixes used in European job titles.
    "m",
    "w",
    "d",
    "f",
    "x",
    "gn",
}

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
    # Brazilian Portuguese — do not reject a Portuguese ad by itself; only
    # an explicit advanced/native/mandatory language requirement.
    "português fluente", "portugues fluente",
    "fluência em português", "fluencia em portugues",
    "português nativo", "portugues nativo",
    "português obrigatório", "portugues obrigatorio",
    "português avançado", "portugues avancado",
    "portuguese required", "native portuguese",
]


def _normalise_title(title: str) -> str:
    """Return title text suitable for deterministic language checks."""
    value = html.unescape(title or "").replace("\\-", "-")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[^\wäöüß]+", " ", value.casefold(), flags=re.UNICODE)
    return " ".join(value.split())


def _normalise_company(value: str | None) -> str:
    return " ".join(html.unescape(value or "").strip().casefold().split())


def detect_description_language(description: str | None) -> str:
    """Return en/de/fr/nl/es/it/pt for confident text, otherwise ``unknown``."""
    value = html.unescape(description or "")
    value = re.sub(r"<[^>]+>", " ", value).casefold()
    tokens = re.findall(r"[a-zà-öø-ÿß]+", value[:12_000], flags=re.UNICODE)
    if len(tokens) < 20:
        return "unknown"

    counts = {
        language: sum(token in stopwords for token in tokens)
        for language, stopwords in LANGUAGE_STOPWORDS.items()
    }
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    winner, winner_score = ranked[0]
    runner_up_score = ranked[1][1]
    if winner_score < 6:
        return "unknown"
    if winner_score < runner_up_score * 1.5 and winner_score - runner_up_score < 4:
        return "unknown"
    return winner


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


def is_non_target_coo(title: str, profile: UserProfile | None) -> bool:
    """Reject COO as the advertised role, not incidental mentions of a COO."""
    if not COO_ROLE_PATTERN.search(_normalise_title(title)):
        return False
    if profile is None or not profile.target_titles:
        return True
    return not any(
        COO_ROLE_PATTERN.search(_normalise_title(target))
        for target in profile.target_titles
        if target
    )


def is_non_target_vp(title: str, profile: UserProfile | None) -> bool:
    """Reject a VP-only title unless the profile targets VP roles.

    A combined title such as "Director / VP Supply Chain" keeps its other
    director-level marker and is not rejected here.
    """
    normalised = _normalise_title(title)
    if not VP_ROLE_PATTERN.search(normalised):
        return False
    if profile and any(
        VP_ROLE_PATTERN.search(_normalise_title(target))
        for target in (profile.target_titles or [])
        if target
    ):
        return False
    remainder = VP_ROLE_PATTERN.sub(" ", normalised)
    return not DIRECTOR_TITLE_PATTERN.search(remainder)


def targets_procurement(profile: UserProfile | None) -> bool:
    return bool(profile and any(
        PROCUREMENT_TITLE_PATTERN.search(_normalise_title(target))
        for target in (profile.target_titles or [])
        if target
    ))


def focus_rank(job: Job, profile: UserProfile | None, now: datetime | None = None) -> tuple[int, int]:
    """Scoring-queue rank: freshness bucket first, then the primary function.

    Recruiters shortlist from the first applicants, so a vacancy from today
    always outranks one from the day before. Within the same freshness bucket
    procurement roles lead when the profile targets procurement. Timestamps
    are UTC-naive (see ``normalise_posted_at``).
    """
    seen_at = job.posted_at or job.scraped_at
    if seen_at is None:
        bucket = 2
    else:
        if seen_at.tzinfo is not None:
            seen_at = seen_at.astimezone(timezone.utc).replace(tzinfo=None)
        now = now or datetime.now(timezone.utc).replace(tzinfo=None)
        age = now - seen_at
        if age <= timedelta(days=1):
            bucket = 0
        elif age <= timedelta(days=settings.fresh_search_days):
            bucket = 1
        else:
            bucket = 2
    primary = 0 if (
        targets_procurement(profile)
        and PROCUREMENT_TITLE_PATTERN.search(_normalise_title(job.title))
    ) else 1
    return bucket, primary


def _target_title_tokens(title: str) -> tuple[str, ...]:
    """Normalise only harmless fillers; retain all functional modifiers."""
    return tuple(
        token
        for token in _normalise_title(title).split()
        if token not in TARGET_TITLE_FILLER_TOKENS
    )


def matches_explicit_target_title(title: str, profile: UserProfile | None) -> bool:
    """Exact role match against the user's target list.

    Removing ``of`` makes "Director Operations" and "Director of Operations"
    equivalent. Functional modifiers are deliberately retained, so
    "Director of Retail and Commercial Operations" does not match.
    """
    if profile is None or not profile.target_titles:
        return False
    title_tokens = _target_title_tokens(title)
    if not title_tokens:
        return False
    for target in profile.target_titles:
        if not target:
            continue
        # Brazil aliases are retrieval equivalents of the same profile role,
        # not new directions. Protect their exact matches from generic rules.
        variants = expand_queries_for_country([target], "br")
        if any(title_tokens == _target_title_tokens(variant) for variant in variants):
            return True
    return False


def pre_filter(job: Job, profile: UserProfile | None) -> tuple[bool, str]:
    """Fast rule-based pre-filter. Returns (pass, bucket) where bucket is low/medium/high."""
    title_lower = job.title.lower()
    desc_lower = (job.description or "").lower()
    text = f"{title_lower} {desc_lower}"
    explicit_target_match = matches_explicit_target_title(job.title, profile)

    # Match the complete word so "International" and "Internal" are not
    # mistaken for internships. An actual internship remains a hard reject.
    if INTERN_TITLE_PATTERN.search(title_lower):
        return False, "low"

    # COO was deliberately removed from this candidate's target directions.
    # Keep the rule profile-driven so a future user can opt in explicitly.
    if is_non_target_coo(job.title, profile):
        return False, "low"

    if is_non_target_vp(job.title, profile):
        return False, "low"

    # An exact user target is authoritative over generic title/category rules.
    # Explicit exclusions and language/location preferences below still apply.
    if not explicit_target_match and any(kw in title_lower for kw in REJECT_TITLE_KEYWORDS):
        return False, "low"

    # "Operations" can mean retail/sales/revenue rather than the user's
    # supply-chain and transformation background.
    if not explicit_target_match and is_commercial_function_title(job.title):
        return False, "low"

    # Hard reject: non-English/German language required
    if any(kw in desc_lower for kw in FOREIGN_LANGUAGE_REQUIRED):
        return False, "low"

    # User-defined content exclusions. Invalid source placeholders such as
    # "nan" are ignored defensively even on a legacy/unmigrated profile.
    if profile and profile.excluded_keywords:
        for kw in profile.excluded_keywords:
            normalised_kw = str(kw or "").strip().casefold()
            if (
                normalised_kw not in INVALID_EXCLUSION_VALUES
                and normalised_kw in text
            ):
                return False, "low"

    # Company exclusions are exact company-name matches, never free-text
    # searches across requirements. Blocking SAP must not reject another
    # employer merely because its description mentions SAP.
    if profile and getattr(profile, "excluded_companies", None):
        company = _normalise_company(job.company_name)
        excluded_companies = {
            _normalise_company(str(value))
            for value in profile.excluded_companies
            if _normalise_company(str(value)) not in INVALID_EXCLUSION_VALUES
        }
        if company and company in excluded_companies:
            return False, "low"

    # English-only filter: reject only confident non-English language. Short
    # or ambiguous text is allowed through to AI rather than false-rejected.
    if profile and getattr(profile, "english_only", False):
        if is_clearly_german_title(job.title):
            return False, "low"
        description_language = detect_description_language(job.description)
        if description_language not in {"en", "unknown"}:
            return False, "low"

    # Domain check — must be in supply chain / procurement / operations
    domain_match = explicit_target_match or any(
        kw in title_lower or kw in desc_lower for kw in DOMAIN_KEYWORDS
    )
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

    # Exact targets do not need to rediscover seniority through generic
    # Director/Head/Manager keywords.
    if explicit_target_match:
        return True, "high"

    # Strict match: the target function or the seniority must be visible in
    # the TITLE. Words like "operations" or "growth" appear in almost every
    # description, so a description-only hit is not a match (it previously
    # sent ~90% non-target roles to the priority AI queue).
    title_domain = any(kw in title_lower for kw in TITLE_DOMAIN_KEYWORDS)
    is_director = bool(DIRECTOR_TITLE_PATTERN.search(title_lower))
    is_senior = bool(SENIOR_TITLE_PATTERN.search(title_lower))

    if is_director:
        return True, "high" if title_domain else "medium"
    if is_senior and title_domain:
        return True, "medium"

    # Right function or plain manager, seniority unclear: scored only after
    # the priority queue is empty.
    is_plain_manager = "manager" in title_lower or "gerente" in title_lower
    if title_domain or is_senior or is_plain_manager:
        return False, "manager_tier2"

    # Neither the function nor any seniority in the title.
    return False, "low"
