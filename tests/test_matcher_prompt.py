from app.models.user import UserProfile
from app.scoring.matcher import SCORING_PROMPT, build_profile_text


def test_scoring_prompt_uses_profile_target_roles_as_source_of_truth():
    assert "Target roles in the Candidate Profile are the source of truth" in SCORING_PROMPT
    assert "looking EXCLUSIVELY" not in SCORING_PROMPT
    assert "NOT in Supply Chain/Procurement/Operations/Logistics" not in SCORING_PROMPT


def test_profile_preserves_restructuring_and_ai_target_directions():
    profile = UserProfile(
        target_titles=[
            "Chief Restructuring Officer",
            "Director of AI Strategy",
        ]
    )

    profile_text = build_profile_text(profile)
    assert (
        "Target roles: Chief Restructuring Officer, Director of AI Strategy"
        in profile_text
    )

    rendered = SCORING_PROMPT.format(profile_text=profile_text, jobs_text="No jobs")
    assert "Chief Restructuring Officer" in rendered
    assert "Director of AI Strategy" in rendered
