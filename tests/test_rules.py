import pytest
from app.models.job import Job
from app.models.user import UserProfile
from app.scoring.rules import (
    detect_description_language,
    is_clearly_german_title,
    is_commercial_function_title,
    is_non_target_coo,
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


def test_business_development_is_rejected_as_commercial_function():
    job = Job(
        title="Business Development Manager – Freight Forwarding & Logistics",
        description="Lead international logistics growth and commercial sales.",
    )
    assert pre_filter(job, UserProfile()) == (False, "low")


@pytest.mark.parametrize(
    "title",
    [
        "Chief Operating Officer (COO)",
        "Chief Operations Officer",
        "COO - Middle East",
    ],
)
def test_coo_is_rejected_when_not_an_explicit_target(title):
    profile = UserProfile(
        target_titles=["Director Operations", "Chief Restructuring Officer"],
    )
    assert is_non_target_coo(title, profile) is True
    assert pre_filter(
        Job(title=title, description="Lead global supply chain operations."),
        profile,
    ) == (False, "low")


def test_coo_can_be_explicitly_enabled_in_target_roles():
    profile = UserProfile(target_titles=["Chief Operating Officer"])
    job = Job(
        title="COO - Middle East",
        description="Lead global supply chain operations.",
    )
    assert is_non_target_coo(job.title, profile) is False
    assert pre_filter(job, profile)[0] is True


@pytest.mark.parametrize(
    "title",
    [
        "Head of COO Supply Chain Solutions",
        "Director, COO Transformation Office",
        "Head of Operations — COO Organisation",
    ],
)
def test_coo_mention_is_not_mistaken_for_the_advertised_role(title):
    profile = UserProfile(target_titles=["Director Operations"])
    assert is_non_target_coo(title, profile) is False
    assert pre_filter(
        Job(title=title, description="Lead international supply chain transformation."),
        profile,
    )[0] is True


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


@pytest.mark.parametrize(
    "title",
    [
        "International Supply Chain Director",
        "International Head of Procurement",
        "Head of Internal Operations",
    ],
)
def test_international_and_internal_are_not_mistaken_for_internships(title):
    assert pre_filter(
        Job(title=title, description="Lead global supply chain operations."),
        UserProfile(),
    )[0] is True


@pytest.mark.parametrize("title", ["Supply Chain Intern", "Procurement Internship"])
def test_actual_internships_are_still_rejected(title):
    assert pre_filter(
        Job(title=title, description="Support global supply chain operations."),
        UserProfile(),
    ) == (False, "low")


# Strict title match (23.09.2026). Real titles from the production audit: the
# description-only keyword hit let ~90% non-target roles into the priority
# queue. The function or the seniority must now be visible in the title.
_GENERIC_DESC = "You will work with operations and supply chain teams to drive growth."


@pytest.mark.parametrize("title", [
    "Senior Graphic Designer",
    "Full-stack Engineer (f/m/d)",
    "Principal Hardware System Test Engineer",
    "Growth Partner (w/m/d)",
    "Website Growth Engineer",
    "Production Assembler",
])
def test_description_only_domain_hit_never_reaches_priority_queue(title):
    passed, bucket = pre_filter(Job(title=title, description=_GENERIC_DESC), None)
    assert not (passed and bucket in ("high", "medium"))


@pytest.mark.parametrize("title, expected", [
    ("Head of Procurement", "high"),
    ("Director Global Transportation Management (m/f/d)", "medium"),
    ("Principal Supply Chain Transformation (alle Geschlechter)", "medium"),
    ("Standortleiter Produktion (m/w/d)", "medium"),
    ("Fachbereichsleitung strategischer Einkauf (m/w/d)", "high"),
    ("MD Germany - Express and Freight Forwarder Handling", "medium"),
    ("Director / VP Procurement", "high"),
])
def test_senior_target_roles_stay_in_priority_queue(title, expected):
    passed, bucket = pre_filter(Job(title=title, description=_GENERIC_DESC), None)
    assert (passed, bucket) == (True, expected)


def test_function_without_seniority_goes_to_second_queue():
    job = Job(title="Supply Chain Manager North Island", description=_GENERIC_DESC)
    assert pre_filter(job, None) == (False, "manager_tier2")


# VP roles were removed from the candidate's targets (not realistic in DE).
_PROFILE = UserProfile(target_titles=["Head of Procurement", "Director Supply Chain"])


@pytest.mark.parametrize("title", [
    "VP, Strategic Operations (UK & EU)",
    "Vice President Procurement",
    "SVP Global Supply Chain",
])
def test_vp_only_titles_rejected_unless_targeted(title):
    assert pre_filter(Job(title=title, description=_GENERIC_DESC), _PROFILE) == (False, "low")
    vp_profile = UserProfile(target_titles=["VP Supply Chain"])
    assert pre_filter(Job(title=title, description=_GENERIC_DESC), vp_profile)[0] is True


def test_vp_with_director_marker_is_kept():
    job = Job(title="Director / VP Procurement", description=_GENERIC_DESC)
    assert pre_filter(job, _PROFILE) == (True, "high")


def test_focus_rank_freshness_beats_function_procurement_leads_within_day():
    from datetime import datetime, timedelta

    from app.scoring.rules import focus_rank

    now = datetime(2026, 9, 23, 12, 0)
    today_ops = Job(title="Director Logistics", posted_at=now - timedelta(hours=3))
    today_proc = Job(title="Leiter Einkauf (m/w/d)", posted_at=now - timedelta(hours=5))
    old_proc = Job(title="Head of Procurement", posted_at=now - timedelta(days=2))
    stale = Job(title="Head of Procurement", posted_at=now - timedelta(days=10))

    ordered = sorted([stale, old_proc, today_ops, today_proc], key=lambda j: focus_rank(j, _PROFILE, now))
    assert ordered == [today_proc, today_ops, old_proc, stale]

    # Without procurement in the profile the function gives no boost.
    no_proc = UserProfile(target_titles=["Director Supply Chain"])
    assert focus_rank(today_proc, no_proc, now) == focus_rank(today_ops, no_proc, now)


@pytest.mark.parametrize("title", [
    "Account Director, EMEA",
    "Chief Financial Officer (all genders)",
    "Head of Engineering (m/w/d)",
    "Niederlassungsleiter (m/w/d) Zeitarbeit in Mainz",
])
def test_director_level_outside_function_goes_to_second_queue(title):
    assert pre_filter(Job(title=title, description=_GENERIC_DESC), _PROFILE) == (False, "manager_tier2")


@pytest.mark.parametrize("title", [
    "Head of Production (w/m/d)",
    "Site Director",
    "Betriebsdirektor:in",
    "Managing Director (m/f/d)",
    "Senior Director, Lean Excellence (EU & AMEA)",
])
def test_operations_adjacent_leadership_stays_in_priority_queue(title):
    assert pre_filter(Job(title=title, description=_GENERIC_DESC), _PROFILE) == (True, "medium")
