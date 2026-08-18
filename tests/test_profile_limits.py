from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.profile import MAX_EXCLUDED_COMPANIES, _parse_exclusion_list


def _companies(count: int) -> str:
    return ",".join(f"Company {index}" for index in range(count))


def test_long_lived_company_blocklist_fits_profile_limit() -> None:
    values = _parse_exclusion_list(
        _companies(58),
        "excluded_companies",
        max_items=MAX_EXCLUDED_COMPANIES,
    )

    assert len(values) == 58


def test_company_blocklist_still_has_a_hard_upper_bound() -> None:
    with pytest.raises(HTTPException) as exc_info:
        _parse_exclusion_list(
            _companies(MAX_EXCLUDED_COMPANIES + 1),
            "excluded_companies",
            max_items=MAX_EXCLUDED_COMPANIES,
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == f"excluded_companies: max {MAX_EXCLUDED_COMPANIES} entries"


def test_profile_save_displays_the_server_validation_error() -> None:
    dashboard = (Path(__file__).resolve().parents[1] / "app/static/dashboard.html").read_text()

    assert "d.detail||d.error||'Error saving'" in dashboard
    assert "esc(d.detail||d.error||'Error saving')" in dashboard
