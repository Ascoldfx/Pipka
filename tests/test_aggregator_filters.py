import pytest

from app.sources.aggregator import _is_negative
from app.sources.base import RawJob


def _job(title: str) -> RawJob:
    return RawJob(
        external_id="test",
        source="test",
        title=title,
        description="Global supply chain leadership role.",
    )


@pytest.mark.parametrize(
    "title",
    [
        "International Supply Chain Director",
        "International Head of Procurement",
        "Head of Internal Operations",
    ],
)
def test_aggregator_keeps_words_that_only_contain_intern(title: str) -> None:
    assert _is_negative(_job(title)) is False


@pytest.mark.parametrize("title", ["Supply Chain Intern", "Procurement Internship"])
def test_aggregator_still_rejects_actual_internships(title: str) -> None:
    assert _is_negative(_job(title)) is True
