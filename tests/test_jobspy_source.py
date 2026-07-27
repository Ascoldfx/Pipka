from app.sources.jobspy_source import _clean_optional_text


def test_jobspy_does_not_persist_missing_company_placeholders():
    assert _clean_optional_text(None) is None
    assert _clean_optional_text(float("nan")) is None
    assert _clean_optional_text(" NaN ") is None
    assert _clean_optional_text("<NA>") is None


def test_jobspy_keeps_real_company_name():
    assert _clean_optional_text("  Hyprwork  ") == "Hyprwork"
