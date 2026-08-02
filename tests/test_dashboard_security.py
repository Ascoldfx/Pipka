from pathlib import Path
import re

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

    assert '<script src="/static/js/security.js"></script>' in dashboard
    assert dashboard.index('/static/js/security.js') < dashboard.index('<script>')
    assert "X-CSRF-Token" in security_js
    assert "window.fetch" in security_js


def test_dashboard_uses_csp_safe_event_delegation() -> None:
    dashboard = (ROOT / "app/static/dashboard.html").read_text()
    events_js = (ROOT / "app/static/js/events.js").read_text()

    assert not re.search(r"\son[a-z]+\s*=", dashboard, re.IGNORECASE)
    assert '<script src="/static/js/events.js"></script>' in dashboard
    assert "data-action" in dashboard
    assert "closest('[data-action]')" in events_js
    assert not (ROOT / "app/static/js/app.js").exists()
