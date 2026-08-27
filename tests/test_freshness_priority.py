from datetime import datetime, timedelta

from app.models.job import Job
from app.services.job_service import _freshness_key


def test_freshness_key_prefers_publication_then_collection_time() -> None:
    now = datetime.now()
    newly_published = Job(title="New", posted_at=now, scraped_at=now - timedelta(hours=1))
    recently_collected = Job(title="Collected", posted_at=None, scraped_at=now - timedelta(minutes=5))
    older = Job(title="Old", posted_at=now - timedelta(days=1), scraped_at=now)

    ranked = sorted([older, recently_collected, newly_published], key=_freshness_key, reverse=True)

    assert ranked == [newly_published, recently_collected, older]
