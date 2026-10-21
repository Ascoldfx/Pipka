from datetime import datetime, timedelta, timezone

from app.sources.adzuna import parse_created


def test_created_is_local_time_mislabelled_as_utc():
    # Production: Voith "created" 17:29:40Z was scraped at 15:48 UTC.
    assert parse_created("2026-10-20T17:29:40Z", "de") == datetime(2026, 10, 20, 15, 29, 40)
    # Winter time and the southern hemisphere use their own offsets.
    assert parse_created("2026-01-15T10:00:00Z", "de") == datetime(2026, 1, 15, 9, 0)
    assert parse_created("2026-10-20T20:00:00Z", "nz") == datetime(2026, 10, 20, 7, 0)


def test_created_never_lies_in_the_future():
    ahead = datetime.now(timezone.utc) + timedelta(days=1)
    parsed = parse_created(ahead.strftime("%Y-%m-%dT%H:%M:%SZ"), "de")
    assert parsed <= datetime.now(timezone.utc).replace(tzinfo=None)


def test_missing_or_broken_created():
    assert parse_created(None, "de") is None
    assert parse_created("not a date", "de") is None
