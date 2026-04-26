"""Profile fingerprinting for score-cache invalidation.

We tag every ``JobScore`` row with two pieces of provenance:

* ``profile_hash`` — sha256 of the user's scoring-relevant profile fields,
  serialised in a stable order. Changes when the user edits anything that
  could change a score (resume, target titles, languages, etc.).
* ``model_version`` — the AI backend identifier (``gemini-3.1-flash-lite-preview``,
  ``claude-sonnet-4-20250514``, ``google/gemma-4-31b-it``). Changes when we
  swap models or rev a model version.

Application code reads these to decide whether a cached score is still
valid for a given (user, model) pair, and to selectively invalidate scores
on profile changes without a global re-score.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from app.config import settings
from app.models.user import UserProfile

# Backend identifiers — pulled from settings so an env-only model bump
# automatically invalidates downstream caches without code changes.
MODEL_GEMINI = lambda: f"gemini:{settings.gemini_model}"           # noqa: E731
MODEL_CLAUDE = lambda: f"claude:{settings.claude_model}"           # noqa: E731
MODEL_NVIDIA = lambda: f"nvidia:{settings.nvidia_model}"           # noqa: E731

# The set of profile attributes that influence scoring. Order is fixed so
# the resulting JSON serialisation is deterministic across runs.
_PROFILE_FIELDS: tuple[str, ...] = (
    "resume_text",
    "target_titles",
    "languages",
    "work_mode",
    "preferred_countries",
    "excluded_keywords",
    "english_only",
    "target_companies",
    "min_salary",
    "experience_years",
)


def compute_profile_hash(profile: UserProfile | None) -> str | None:
    """Return a 64-char hex sha256 of the scoring-relevant profile fields,
    or ``None`` if the profile is missing.

    Lists/dicts are serialised with sorted keys; whitespace is stripped from
    string fields so trivial edits ("Sales Manager " → "Sales Manager") don't
    spuriously invalidate cached scores.
    """
    if profile is None:
        return None

    payload: dict[str, Any] = {}
    for field in _PROFILE_FIELDS:
        value = getattr(profile, field, None)
        if isinstance(value, str):
            value = value.strip()
        elif isinstance(value, list):
            # Normalise: strip + drop empties, sort for stability.
            value = sorted(
                (v.strip() if isinstance(v, str) else v for v in value if v not in (None, "")),
                key=lambda x: (str(type(x).__name__), str(x)),
            )
        # dicts (e.g. ``languages={"en":"C1","de":"B1"}``) serialise stably
        # via ``sort_keys=True`` below.
        payload[field] = value

    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
