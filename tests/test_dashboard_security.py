import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_job_rows_do_not_embed_untrusted_titles_in_inline_javascript() -> None:
    dashboard = (ROOT / "app/static/dashboard.html").read_text()

    assert 'onclick="showAnalysis(${j.id}' not in dashboard
    assert 'data-job-title="${attr(j.title)}"' in dashboard
    assert "data-job-analysis" in dashboard


def test_external_job_links_use_scheme_validation_and_attribute_escaping() -> None:
    dashboard = (ROOT / "app/static/dashboard.html").read_text()

    assert '<a class="job-title" href="${safeUrl(j.url)}"' in dashboard
    assert '<td><a href="${safeUrl(j.url)}"' in dashboard
    assert "return attr(p.href)" in dashboard
    assert "p.protocol==='https:'||p.protocol==='http:'" in dashboard


def test_source_tooltip_is_attribute_escaped() -> None:
    dashboard = (ROOT / "app/static/dashboard.html").read_text()

    assert 'title="${attr(tip)}"' in dashboard


def test_dashboard_loads_csrf_fetch_wrapper_before_inline_application_code() -> None:
    dashboard = (ROOT / "app/static/dashboard.html").read_text()
    security_js = (ROOT / "app/static/js/security.js").read_text()

    assert '/static/js/security.js' in dashboard
    assert dashboard.index('/static/js/security.js') < dashboard.index('<script>')
    assert "X-CSRF-Token" in security_js
    assert "window.fetch" in security_js


def test_dashboard_uses_csp_safe_event_delegation() -> None:
    dashboard = (ROOT / "app/static/dashboard.html").read_text()
    events_js = (ROOT / "app/static/js/events.js").read_text()

    assert not re.search(r"\son[a-z]+\s*=", dashboard, re.IGNORECASE)
    assert '/static/js/events.js' in dashboard
    assert "data-action" in dashboard
    assert "closest('[data-action]')" in events_js
    assert not (ROOT / "app/static/js/app.js").exists()


def test_feedback_control_has_direct_binding_and_explicit_global_exports() -> None:
    dashboard = (ROOT / "app/static/dashboard.html").read_text()

    assert 'id="feedback-trigger"' in dashboard
    assert "feedbackTrigger.addEventListener('click'" in dashboard
    assert "[data-action=\"close-feedback\"]" in dashboard
    assert "window.openFeedbackModal = openFeedbackModal" in dashboard
    assert "window.submitFeedback = submitFeedback" in dashboard
    assert '/static/js/events.js?v=2.0.3' in dashboard


def test_ops_scoring_alert_accepts_multi_country_payload() -> None:
    dashboard = (ROOT / "app/static/dashboard.html").read_text()

    assert "scoringQueue.country.toUpperCase()" not in dashboard
    assert "const scoringCountryLabel = scoringCountries.join(', ').toUpperCase()" in dashboard


def test_admin_profile_ui_uses_only_server_truncated_resume_preview() -> None:
    dashboard = (ROOT / "app/static/dashboard.html").read_text()

    assert "p.resume_preview || 'No resume text'" in dashboard
    assert "p.resume_text && p.resume_text.length" not in dashboard
