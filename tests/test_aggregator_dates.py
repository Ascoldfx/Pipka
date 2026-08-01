from datetime import datetime, timedelta, timezone

from app.sources.aggregator import _normalise_posted_at
from app.sources.base import normalise_posted_at


def test_aware_publication_date_is_converted_to_utc_naive():
    source_time = datetime(2026, 7, 31, 15, 30, tzinfo=timezone(timedelta(hours=3)))

    assert _normalise_posted_at(source_time) == datetime(2026, 7, 31, 12, 30)
    assert _normalise_posted_at(source_time).tzinfo is None


def test_naive_publication_date_is_preserved():
    source_time = datetime(2026, 7, 31, 12, 30)

    assert _normalise_posted_at(source_time) is source_time


def test_shared_source_normalizer_preserves_the_actual_instant():
    source_time = datetime(2026, 8, 1, 1, 15, tzinfo=timezone(timedelta(hours=-4)))

    assert normalise_posted_at(source_time) == datetime(2026, 8, 1, 5, 15)
