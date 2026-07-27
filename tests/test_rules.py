import pytest
from app.models.job import Job
from app.models.user import UserProfile
from app.scoring.rules import (
    detect_description_language,
    is_clearly_german_title,
    is_commercial_function_title,
    matches_explicit_target_title,
    pre_filter,
)

def test_hard_reject_junior():
    job = Job(title="Junior Procurement Analyst", description="Some text")
    passed, bucket = pre_filter(job, None)
    assert passed is False
    assert bucket == "low"

def test_hard_reject_foreign_language():
    job = Job(title="Director of Supply Chain", description="fluent french required for this role")
    passed, bucket = pre_filter(job, None)
    assert passed is False
    assert bucket == "low"

def test_user_exclusions():
    profile = UserProfile(excluded_keywords=["apple", "amazon"])
    job = Job(title="Head of Procurement", description="Join Amazon team")
    passed, bucket = pre_filter(job, profile)
    assert passed is False
    assert bucket == "low"


def test_excluded_company_matches_company_only_not_description():
    profile = UserProfile(excluded_companies=["Amazon", "SAP"])
    unrelated = Job(
        title="Head of Supply Chain",
        company_name="Hyprwork",
        description="Experience with Amazon fulfilment and SAP is useful.",
    )
    blocked = Job(
        title="Head of Supply Chain",
        company_name="  AMAZON ",
        description="Global supply chain leadership role.",
    )

    assert pre_filter(unrelated, profile)[0] is True
    assert pre_filter(blocked, profile) == (False, "low")


def test_legacy_nan_keyword_is_ignored_defensively():
    profile = UserProfile(excluded_keywords=["nan"])
    job = Job(
        title="Director Procurement",
        description="Own financial planning and vendor management.",
    )
    assert pre_filter(job, profile)[0] is True


def test_english_only_filter_fail():
    profile = UserProfile(english_only=True)
    job = Job(title="Leiter Logistik", description="Wir suchen einen Leiter.")
    passed, bucket = pre_filter(job, profile)
    assert passed is False
    assert bucket == "low"

def test_english_only_filter_pass():
    profile = UserProfile(english_only=True)
    job = Job(title="Director Supply Chain", description="International team, english working language.")
    passed, bucket = pre_filter(job, profile)
    assert passed is True


def test_english_only_accepts_english_description_without_marker_words():
    profile = UserProfile(english_only=True)
    job = Job(
        title="Director - Contracts & Procurement",
        description=(
            "The successful candidate will lead sourcing and contract lifecycle "
            "management for the business. You will work with internal stakeholders "
            "and suppliers to ensure that procurement requirements are delivered on "
            "time. Responsibilities include vendor governance, planning, reporting "
            "and continuous improvement across the organisation."
        ),
    )
    assert detect_description_language(job.description) == "en"
    assert pre_filter(job, profile)[0] is True


def test_english_only_rejects_confidently_german_description_with_english_title():
    profile = UserProfile(english_only=True)
    job = Job(
        title="Director Supply Chain",
        description=(
            "Wir suchen eine erfahrene Führungskraft für die Leitung unserer "
            "Lieferkette. Sie sind verantwortlich für die Planung und arbeiten "
            "mit den Teams in der Produktion zusammen. Ihre Aufgaben umfassen "
            "die Steuerung von Lieferanten und die kontinuierliche Verbesserung "
            "der Prozesse in unserem Unternehmen."
        ),
    )
    assert detect_description_language(job.description) == "de"
    assert pre_filter(job, profile) == (False, "low")


def test_english_only_allows_ambiguous_short_text_for_ai_review():
    profile = UserProfile(english_only=True)
    job = Job(title="Head of Procurement", description="Procurement leadership role.")
    assert detect_description_language(job.description) == "unknown"
    assert pre_filter(job, profile)[0] is True


@pytest.mark.parametrize(
    "title",
    [
        "Geschäftsführer Operational Excellence (m/w/d)",
        "Gesch&auml;ftsf&uuml;hrer Logistik",
        "Einkaufsleiter (m/w/d)",
        "Leiter Logistik",
        "Bereichsleiter Supply Chain",
        "Produktmanager (m/w/d)",
        "Krisenmanager Restrukturierung",
    ],
)
def test_english_only_rejects_clearly_german_title_despite_english_markers(title):
    profile = UserProfile(english_only=True)
    job = Job(
        title=title,
        description="International global company. English working language. Remote role.",
    )

    assert pre_filter(job, profile) == (False, "low")


@pytest.mark.parametrize(
    "title",
    [
        "Director Operations (m/w/d)",
        "Head of Procurement – Deutschland",
        "Chief Restructuring Officer",
        "Global Supply Chain Director",
    ],
)
def test_german_title_detector_allows_english_titles(title):
    assert is_clearly_german_title(title) is False


def test_german_title_detector_defers_bilingual_title_to_description_filter():
    title = "Einkaufsleiter / Head of Procurement"
    assert is_clearly_german_title(title) is False

    profile = UserProfile(english_only=True)
    job = Job(title=title, description="English is the working language in our global team.")
    assert pre_filter(job, profile)[0] is True


def test_german_title_is_allowed_when_english_only_is_disabled():
    profile = UserProfile(english_only=False)
    job = Job(
        title="Geschäftsführer Operational Excellence",
        description="International operations transformation.",
    )
    assert pre_filter(job, profile)[0] is True


@pytest.mark.parametrize(
    "title",
    [
        "Director of Retail and Commercial Operations",
        "Director of Commercial Operations",
        "Head of Retail Operations",
        "Sales Operations Director",
        "Revenue Operations Director",
        "Retail Director",
        "Chief Commercial Officer",
    ],
)
def test_commercial_operations_titles_are_hard_rejected(title):
    job = Job(
        title=title,
        description=(
            "International English-speaking company with distribution, "
            "inventory, suppliers and operational excellence."
        ),
    )
    assert is_commercial_function_title(title) is True
    assert pre_filter(job, UserProfile(english_only=True)) == (False, "low")


@pytest.mark.parametrize(
    "title",
    [
        "Director of Retail Supply Chain",
        "Commercial Procurement Director",
        "Head of Sourcing — Retail",
        "Director of Logistics, Retail Division",
    ],
)
def test_explicit_supply_chain_title_overrides_commercial_sector_word(title):
    job = Job(
        title=title,
        description="International team. English working language.",
    )
    assert is_commercial_function_title(title) is False
    assert pre_filter(job, UserProfile(english_only=True))[0] is True


def test_exact_target_title_is_protected_from_generic_domain_filter():
    profile = UserProfile(
        target_titles=["Director Operations", "AI Agent Orchestrator"],
        english_only=True,
    )
    job = Job(
        title="AI Agent Orchestrator (m/w/d)",
        description="English is the working language.",
    )

    assert matches_explicit_target_title(job.title, profile) is True
    assert pre_filter(job, profile) == (True, "high")


@pytest.mark.parametrize(
    "target_title",
    [
        "Director Supply Chain",
        "Head of Procurement",
        "Head of Supply Chain",
        "Director Operations",
        "Chief Procurement Officer",
        "Director Purchasing",
        "Head of Sourcing",
        "Director Logistics",
        "Head of Operations",
        "Global Supply Chain Director",
        "interim manager",
        "crisis manager",
        "crisis director",
        "turnaround",
        "Supply chain transformation",
        "E2E Supply Chain",
        "Chief Restructuring Officer",
        "Growth director",
        "Growth manager",
        "Head of Autonomous Operations",
        "AI Agent Orchestrator",
        "Supply Chain Transformation & AI",
        "Director of AI Strategy",
        "Chief of Staff AI",
    ],
)
def test_current_target_role_is_never_lost_to_generic_rules(target_title):
    profile = UserProfile(
        target_titles=[target_title],
        english_only=True,
    )
    job = Job(
        title=f"{target_title} (m/w/d)",
        description="International team. English is the working language.",
    )

    assert pre_filter(job, profile) == (True, "high")


def test_director_of_operations_matches_target_but_commercial_modifiers_do_not():
    profile = UserProfile(target_titles=["Director Operations"])

    assert matches_explicit_target_title("Director of Operations (m/w/d)", profile) is True
    assert matches_explicit_target_title(
        "Director of Retail and Commercial Operations",
        profile,
    ) is False


def test_explicit_target_does_not_override_personal_exclusion():
    profile = UserProfile(
        target_titles=["Director Operations"],
        excluded_keywords=["gambling"],
    )
    job = Job(
        title="Director of Operations",
        description="Lead operations for an online gambling platform.",
    )

    assert matches_explicit_target_title(job.title, profile) is True
    assert pre_filter(job, profile) == (False, "low")


def test_domain_check_fail():
    # Marketing director should fail domain check
    job = Job(title="Marketing Director", description="Responsible for campaigns")
    passed, bucket = pre_filter(job, None)
    assert passed is False
    assert bucket == "low"

def test_director_seniority_pass():
    job = Job(title="Director of Global Sourcing", description="Manage global sourcing strategy")
    passed, bucket = pre_filter(job, None)
    assert passed is True
    assert bucket == "high"

def test_plain_manager_fail():
    job = Job(title="Procurement Manager", description="Manage procurement tasks")
    passed, bucket = pre_filter(job, None)
    assert passed is False
    assert bucket == "manager_tier2"

@pytest.mark.parametrize("salary_min", [None, 50_000, 150_000])
def test_salary_is_ignored(salary_min):
    job = Job(
        title="Head of Logistics",
        description="Global logistics operations in English",
        salary_min=salary_min,
    )
    passed, bucket = pre_filter(job, UserProfile())
    assert passed is True
    assert bucket == "high"
