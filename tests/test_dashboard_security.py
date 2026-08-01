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
