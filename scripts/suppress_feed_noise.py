"""Hide reseller re-posts and bulk feed dumps scraped since a date.

Run inside the app container:

    python scripts/suppress_feed_noise.py --since 2026-10-21            # dry run
    python scripts/suppress_feed_noise.py --since 2026-10-21 --apply > rollback.json
    python scripts/suppress_feed_noise.py --rollback < rollback.json

Nothing is deleted. Rows get url_status='suppressed' (hidden from feed,
queues and URL checks) plus raw_data.suppressed with the reason. When a
re-post carried a direct link or an earlier date, the original row takes it
over; previous values are part of the rollback file.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timedelta

from sqlalchemy import cast, func, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB

from app.database import async_session
from app.models.job import URL_STATUS_SUPPRESSED, Job
from app.sources.aggregator import BULK_FEED_MIN_PER_MINUTE, REPOST_LOOKBACK_DAYS
from app.sources.base import RawJob, fuzzy_title_key, is_aggregator_clickout, is_fuzzy_duplicate

COLUMNS = (Job.id, Job.title, Job.company_name, Job.country, Job.location, Job.url,
           Job.posted_at, Job.scraped_at, Job.source, Job.external_id, Job.url_status)


def _raw(row) -> RawJob:
    return RawJob(external_id="", source=row.source, title=row.title, company_name=row.company_name,
                  country=row.country, location=row.location, url=row.url, posted_at=row.posted_at)


async def plan(since: datetime):
    async with async_session() as session:
        not_suppressed = or_(Job.url_status.is_(None), Job.url_status != URL_STATUS_SUPPRESSED)
        rows = (await session.execute(
            select(*COLUMNS).where(
                Job.scraped_at >= since - timedelta(days=REPOST_LOOKBACK_DAYS), not_suppressed
            ).order_by(Job.id)
        )).all()
        minute = func.date_trunc("minute", Job.posted_at)
        per_minute = dict((await session.execute(
            select(minute, func.count())
            .where(Job.source == "adzuna", Job.url.like("%/land/ad/%"), Job.scraped_at >= since)
            .group_by(minute)
        )).all())

    suppress: dict[int, dict] = {}
    for row in rows:
        if row.scraped_at < since or not is_aggregator_clickout(row.url) or not row.posted_at:
            continue
        if per_minute.get(row.posted_at.replace(second=0, microsecond=0), 0) >= BULK_FEED_MIN_PER_MINUTE:
            suppress[row.id] = {"reason": "bulk_feed"}

    upgrades: dict[int, dict] = {}
    known: dict[str, list] = {}
    for row in rows:
        key = fuzzy_title_key(row.title)
        if row.scraped_at >= since and row.id not in suppress:
            original = next(
                (k for k in known.get(key, [])
                 if k.id not in suppress and k.country == row.country and is_fuzzy_duplicate(_raw(row), _raw(k))),
                None,
            )
            if original is not None:
                suppress[row.id] = {"reason": "repost", "repost_of": original.id}
                change = upgrades.setdefault(original.id, {"before": {}, "after": {}})
                current_url = change["after"].get("url", original.url)
                if is_aggregator_clickout(current_url) and row.url and not is_aggregator_clickout(row.url):
                    for field in ("url", "source", "external_id"):
                        change["before"].setdefault(field, getattr(original, field))
                        change["after"][field] = getattr(row, field)
                current_date = change["after"].get("posted_at", original.posted_at)
                if row.posted_at and (current_date is None or row.posted_at < current_date):
                    change["before"].setdefault("posted_at", original.posted_at)
                    change["after"]["posted_at"] = row.posted_at
                continue
        if row.id not in suppress:
            known.setdefault(key, []).append(row)
    upgrades = {k: v for k, v in upgrades.items() if v["after"]}
    return rows, suppress, upgrades


async def apply(suppress: dict[int, dict], upgrades: dict[int, dict]) -> None:
    async with async_session() as session:
        for job_id, reason in suppress.items():
            await session.execute(
                update(Job).where(Job.id == job_id).values(
                    url_status=URL_STATUS_SUPPRESSED,
                    raw_data=func.coalesce(cast(Job.raw_data, JSONB), func.jsonb_build_object())
                    .op("||")(func.jsonb_build_object("suppressed", cast(json.dumps(reason), JSONB))),
                )
            )
        for job_id, change in upgrades.items():
            await session.execute(update(Job).where(Job.id == job_id).values(**change["after"]))
        await session.commit()


async def rollback(data: dict) -> None:
    async with async_session() as session:
        for job_id in data["suppressed"]:
            await session.execute(
                update(Job).where(Job.id == int(job_id)).values(
                    url_status=None, raw_data=cast(Job.raw_data, JSONB).op("-")("suppressed")
                )
            )
        for job_id, change in data["upgrades"].items():
            before = {k: (datetime.fromisoformat(v) if k == "posted_at" and v else v)
                      for k, v in change["before"].items()}
            await session.execute(update(Job).where(Job.id == int(job_id)).values(**before))
        await session.commit()


def _jsonable(value):
    return value.isoformat() if isinstance(value, datetime) else value


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=datetime.fromisoformat)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()

    if args.rollback:
        data = json.load(sys.stdin)
        await rollback(data)
        print(f"rolled back {len(data['suppressed'])} suppressed, {len(data['upgrades'])} upgraded",
              file=sys.stderr)
        return

    rows, suppress, upgrades = await plan(args.since)
    by_id = {row.id: row for row in rows}
    reasons = Counter(r["reason"] for r in suppress.values())
    print(f"since {args.since}: to suppress {dict(reasons)}; originals upgraded: {len(upgrades)}",
          file=sys.stderr)
    for job_id, reason in list(suppress.items())[:15]:
        row = by_id[job_id]
        print(f"  {reason['reason']:9} {job_id} {row.source:9} {row.company_name} | {row.title[:60]}",
              file=sys.stderr)
    if not args.apply:
        print("dry run: nothing written", file=sys.stderr)
        return
    await apply(suppress, upgrades)
    json.dump({
        "since": args.since.isoformat(),
        "suppressed": {str(k): v for k, v in suppress.items()},
        "upgrades": {str(k): {side: {f: _jsonable(v) for f, v in vals.items()} for side, vals in c.items()}
                     for k, c in upgrades.items()},
    }, sys.stdout, ensure_ascii=False, indent=1)
    print("applied", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
