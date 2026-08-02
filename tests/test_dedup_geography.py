from app.sources.aggregator import _fuzzy_deduplicate
from app.sources.base import RawJob, is_fuzzy_duplicate


def _job(country: str, location: str) -> RawJob:
    return RawJob(
        external_id=f"{country}-{location}",
        source="test",
        title="Head of Procurement",
        company_name="Example Group GmbH",
        country=country,
        location=location,
    )


def test_exact_hash_keeps_same_role_in_different_countries() -> None:
    dubai = _job("ae", "Dubai")
    singapore = _job("sg", "Singapore")

    assert dubai.dedup_hash != singapore.dedup_hash
    assert is_fuzzy_duplicate(dubai, singapore) is False


def test_exact_hash_is_stable_across_case_and_company_suffix() -> None:
    first = _job("AE", "Dubai")
    second = RawJob(
        external_id="same",
        source="other",
        title="HEAD OF PROCUREMENT",
        company_name="Example Group",
        country="ae",
        location="dubai",
    )

    assert first.dedup_hash == second.dedup_hash
    assert is_fuzzy_duplicate(first, second) is True


def test_fuzzy_dedup_only_compares_jobs_with_same_normalized_title() -> None:
    jobs = [
        RawJob(
            external_id=str(index),
            source="test",
            title=f"Director Function {index}",
            company_name="Example Corp",
            country="de",
            location="Berlin",
        )
        for index in range(200)
    ]

    deduped, comparisons = _fuzzy_deduplicate(jobs)

    assert deduped == jobs
    assert comparisons == 0


def test_fuzzy_dedup_preserves_richer_description_and_sources() -> None:
    short = RawJob(
        external_id="short",
        source="linkedin",
        title="Senior Head of Procurement",
        company_name="Example GmbH",
        country="de",
        location="Berlin, DE",
        description="Short",
    )
    rich = RawJob(
        external_id="rich",
        source="indeed",
        title="Head of Procurement",
        company_name="Example GmbH & Co. KG",
        country="DE",
        location="Berlin",
        description="A substantially richer vacancy description",
    )

    deduped, comparisons = _fuzzy_deduplicate([short, rich])

    assert comparisons == 1
    assert deduped == [rich]
    assert rich.raw_data["merged_sources"] == ["linkedin", "indeed"]
