import pytest
from app.models.job import Job
from app.models.user import UserProfile
from app.scoring.rules import (
    is_clearly_german_title,
    is_commercial_function_title,
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
