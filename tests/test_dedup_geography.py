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
