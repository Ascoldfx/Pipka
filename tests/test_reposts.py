"""Reseller re-posts must not look like fresh vacancies (21.10.2026).

Production case: Voith "Head of Procurement Digitalization & AI" arrived
three times via Adzuna click-outs within a day, as "(m|w|d)" / "(m w d)" and
with location "Heidenheim (Kreis)" / "Deutschland"; each became a new job.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.sql.dml import Update

from app.sources.aggregator import _fuzzy_deduplicate, _suppress_reposts
from app.sources.base import (
    RawJob,
    _locations_conflict,
    fuzzy_title_key,
    is_aggregator_clickout,
    is_fuzzy_duplicate,
)

CLICKOUT = "https://www.adzuna.de/land/ad/5936209015?se=x&utm_medium=api"


def _job(title, location, url, posted, source="adzuna"):
    return RawJob(
        external_id=url, source=source, title=title, company_name="Voith SE & Co. KG",
        country="DE", location=location, url=url, posted_at=posted,
    )


@pytest.mark.parametrize("variant", [
    "Head of Procurement (m|w|d) Digitalization & AI",
    "Head of Procurement (m w d) Digitalization & AI",
    "Head of Procurement (w/m/d) Digitalization & AI",
    "Head of Procurement (gn) Digitalization & AI",
    "Head of Procurement m/w/d Digitalization & AI",
])
def test_gender_marker_spellings_share_one_title_key(variant):
    assert fuzzy_title_key(variant) == fuzzy_title_key("Head of Procurement Digitalization & AI")


def test_country_wide_location_does_not_conflict_with_a_city():
    assert not _locations_conflict("Deutschland", "Heidenheim (Kreis), Baden-Württemberg")
    assert _locations_conflict("Berlin", "Heidenheim (Kreis), Baden-Württemberg")


def test_clickout_detection():
    assert is_aggregator_clickout(CLICKOUT)
    assert not is_aggregator_clickout("https://www.adzuna.de/details/5934252968")
    assert not is_aggregator_clickout("https://www.xing.com/jobs/x-158662994")


def test_in_scan_merge_prefers_direct_link_and_keeps_earliest_date():
    clickout = _job("Head of Procurement (m|w|d)", "Deutschland", CLICKOUT, datetime(2026, 10, 21, 6))
    direct = _job("Head of Procurement (m/w/d)", "Heidenheim", "https://www.xing.com/jobs/1",
                  datetime(2026, 10, 9), source="xing")
    clickout.description = "a much longer description " * 10
    assert is_fuzzy_duplicate(clickout, direct)

    merged, _ = _fuzzy_deduplicate([clickout, direct])

    assert len(merged) == 1
    assert merged[0].url == "https://www.xing.com/jobs/1"
    assert merged[0].posted_at == datetime(2026, 10, 9)


class _Session:
    def __init__(self, rows):
        self.rows, self.updates, self.commits = rows, [], 0

    async def execute(self, stmt):
        if isinstance(stmt, Update):
            self.updates.append(stmt.compile().params)
            return None
        return SimpleNamespace(all=lambda: self.rows)

    async def commit(self):
        self.commits += 1


def _stored(**kw):
    base = dict(id=75192, title="Head of Procurement (m|w|d) Digitalization & AI",
                company_name="Voith SE & Co. KG", country="de",
                location="Heidenheim (Kreis), Baden-Württemberg", url=CLICKOUT,
                posted_at=datetime(2026, 10, 20, 17), source="adzuna")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_repost_of_stored_vacancy_is_skipped_without_changes():
    session = _Session([_stored()])
    repost = _job("Head of Procurement (m w d) Digitalization & AI", "Deutschland",
                  "https://www.adzuna.de/land/ad/5936176856", datetime(2026, 10, 21, 5))

    skipped = await _suppress_reposts(session, [repost])

    assert skipped == {repost.dedup_hash}
    assert session.updates == []


@pytest.mark.asyncio
async def test_direct_source_upgrades_stored_clickout_and_its_date():
    session = _Session([_stored()])
    direct = _job("Head of Procurement (m/w/d) Digitalization & AI", "Heidenheim",
                  "https://www.xing.com/jobs/heidenheim-1", datetime(2026, 10, 8), source="xing")

    skipped = await _suppress_reposts(session, [direct])

    assert skipped == {direct.dedup_hash}
    (params,) = session.updates
    assert params["url"] == "https://www.xing.com/jobs/heidenheim-1"
    assert params["source"] == "xing"
    assert params["posted_at"] == datetime(2026, 10, 8)
    assert session.commits == 1


@pytest.mark.asyncio
async def test_different_company_is_not_a_repost():
    session = _Session([_stored()])
    other = RawJob(external_id="o", source="adzuna", title="Head of Procurement (m/w/d) Digitalization & AI",
                   company_name="Diehl Aviation", country="DE", location="Deutschland", url=CLICKOUT)

    assert await _suppress_reposts(session, [other]) == set()
