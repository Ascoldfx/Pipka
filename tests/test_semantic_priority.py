from app.models.job import Job
from app.models.user import UserProfile
from app.services.scheduler_service import _order_by_semantic_priority


def test_semantic_priority_never_removes_candidates() -> None:
    jobs = [
        Job(id=1, title="Director Supply Chain"),
        Job(id=2, title="Transformation PMO Manager"),
        Job(id=3, title="Head of Procurement"),
        Job(id=4, title="Interim Digital Transformation Manager"),
    ]
    profile = UserProfile(target_titles=["Head of Procurement"])
    similarities = {1: 0.82, 2: 0.12, 3: 0.08}

    ordered, low_count = _order_by_semantic_priority(
        jobs,
        similarities,
        threshold=0.6,
        profile=profile,
    )

    assert [job.id for job in ordered] == [3, 1, 4, 2]
    assert {job.id for job in ordered} == {1, 2, 3, 4}
    assert low_count == 1


def test_exact_target_leads_even_with_low_similarity() -> None:
    jobs = [
        Job(id=1, title="Director Supply Chain"),
        Job(id=2, title="AI Agent Orchestrator"),
    ]
    profile = UserProfile(target_titles=["AI Agent Orchestrator"])

    ordered, _ = _order_by_semantic_priority(
        jobs,
        {1: 0.95, 2: 0.01},
        threshold=0.6,
        profile=profile,
    )

    assert [job.id for job in ordered] == [2, 1]
