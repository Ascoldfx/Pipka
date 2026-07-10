from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from app.sources.base import RawJob, SearchParams

logger = logging.getLogger(__name__)

SITE_MAP = {
    # Google Jobs is excluded: blocked on VPS IPs by Google (returns 0 results silently)
    # Glassdoor: best-effort (Cloudflare CAPTCHAs may block, handled gracefully)
    "de": ["indeed", "linkedin"],
    "ch": ["indeed", "linkedin"],
    "at": ["indeed", "linkedin"],
    "nl": ["indeed", "linkedin"],
    "be": ["indeed", "linkedin"],
    "si": ["indeed", "linkedin"],
    "sk": ["indeed", "linkedin"],
    "ro": ["indeed", "linkedin"],
    "hu": ["indeed", "linkedin"],
}

# JobSpy uses full country names, not ISO codes
COUNTRY_NAME = {
    "de": "germany",
    "ch": "switzerland",
    "at": "austria",
    "nl": "netherlands",
    "be": "belgium",
    "si": "slovenia",
    "sk": "slovakia",
    "ro": "romania",
    "hu": "hungary",
    "cz": "czech republic",
    "pl": "poland",
    "it": "italy",
    "es": "spain",
    "pt": "portugal",
    "fr": "france",
    "uk": "united kingdom",
    "ie": "ireland",
    "dk": "denmark",
    "se": "sweden",
    "no": "norway",
    "fi": "finland",
    # Gulf / Oceania / SEA (opt-in via profile countries; Indeed operates in all)
    "ae": "united arab emirates",
    "sa": "saudi arabia",
    "qa": "qatar",
    "au": "australia",
    "nz": "new zealand",
    "id": "indonesia",
    "sg": "singapore",
}


JOBSPY_MAX_QUERIES = 8    # cap to avoid timeout; top-ranked queries are most valuable
JOBSPY_MAX_COUNTRIES = 3  # countries per scan; rotated by 3h slot like queries —
                          # 6+ countries sequentially would blow the 240s source
                          # timeout and lose the WHOLE jobspy result set

class JobSpySource:
    @property
    def source_name(self) -> str:
        return "jobspy"

    async def search(self, params: SearchParams) -> list[RawJob]:
        results: list[RawJob] = []
        seen: set[str] = set()

        # Cap queries — LinkedIn/Indeed scraping is slow; too many sequential calls → timeout.
        # Rotate the window by 3h scan slot so EVERY profile title gets scraped over a
        # day (8 scans × 8 queries = 64 slots ≥ any realistic title list) instead of
        # only ever the same first 8.
        n = len(params.queries)
        slot = datetime.now().hour // 3  # 0..7, stable within one scan interval
        if n > JOBSPY_MAX_QUERIES:
            start = (slot * JOBSPY_MAX_QUERIES) % n
            queries = [params.queries[(start + i) % n] for i in range(JOBSPY_MAX_QUERIES)]
        else:
            queries = params.queries

        # Same rotation for countries: with Gulf/APAC opted in there are 6+, and
        # scraping them all sequentially would exceed the source timeout.
        nc = len(params.countries)
        if nc > JOBSPY_MAX_COUNTRIES:
            c_start = (slot * JOBSPY_MAX_COUNTRIES) % nc
            countries = [params.countries[(c_start + i) % nc] for i in range(JOBSPY_MAX_COUNTRIES)]
        else:
            countries = params.countries

        # Which sub-sites are allowed this run (env-configurable). LinkedIn is
        # off by default — it ignores the country filter and floods US jobs.
        from app.config import settings  # noqa: PLC0415
        allowed = {s.strip().lower() for s in settings.jobspy_sites.split(",") if s.strip()}

        for country in countries:
            sites = SITE_MAP.get(country, ["indeed", "linkedin", "google"])
            sites = [s for s in sites if s in allowed]
            if not sites:
                continue  # nothing enabled for this country
            location = ", ".join(params.locations) if params.locations else None
            limit = min(params.results_per_query, 25)

            # Run all queries for this country concurrently (each in its own thread)
            tasks = [self._scrape(q, sites, location, country, limit) for q in queries]
            batch = await asyncio.gather(*tasks, return_exceptions=True)

            for item in batch:
                if isinstance(item, Exception):
                    logger.warning("JobSpy scrape failed: %s", item)
                    continue
                for job in item:
                    if job.external_id not in seen:
                        seen.add(job.external_id)
                        results.append(job)
        return results

    async def _scrape(
        self, query: str, sites: list[str], location: str | None, country: str, limit: int
    ) -> list[RawJob]:
        try:
            from jobspy import scrape_jobs
        except ImportError:
            logger.error("python-jobspy not installed")
            return []

        def _run():
            kwargs = {
                "site_name": sites,
                "search_term": query,
                "results_wanted": min(limit, 50),
                "hours_old": 24 * 60,  # 60 days
                "country_indeed": COUNTRY_NAME.get(country, country),
            }
            if location:
                kwargs["location"] = location
            try:
                return scrape_jobs(**kwargs)
            except Exception as e:
                logger.error("JobSpy scrape failed for '%s': %s", query, e)
                return None

        df = await asyncio.to_thread(_run)
        if df is None or df.empty:
            return []

        jobs: list[RawJob] = []
        for _, row in df.iterrows():
            try:
                site = str(row.get("site", "unknown"))
                ext_id = f"{site}_{country}_{row.get('id', hash(row.get('job_url', '')))}"

                posted = None
                if row.get("date_posted"):
                    try:
                        posted = datetime.fromisoformat(str(row["date_posted"]))
                    except (ValueError, TypeError):
                        pass

                salary_min = row.get("min_amount") if row.get("min_amount") and row.get("min_amount") == row.get("min_amount") else None
                salary_max = row.get("max_amount") if row.get("max_amount") and row.get("max_amount") == row.get("max_amount") else None

                is_remote = None
                if row.get("is_remote") is not None:
                    is_remote = bool(row["is_remote"])

                jobs.append(
                    RawJob(
                        external_id=ext_id,
                        source=site,
                        title=str(row.get("title", "")),
                        company_name=str(row.get("company", "")) or None,
                        location=str(row.get("location", "")) or None,
                        # LinkedIn ignores country_indeed param and returns global results.
                        # Set country=None for LinkedIn so the text-based location filter runs.
                        # Indeed/Glassdoor respect the country filter, so keep it for them.
                        country=None if site == "linkedin" else country.upper(),
                        description=str(row.get("description", "")) or None,
                        salary_min=float(salary_min) if salary_min else None,
                        salary_max=float(salary_max) if salary_max else None,
                        salary_currency=str(row.get("currency", "EUR")) or "EUR",
                        url=str(row.get("job_url", "")),
                        is_remote=is_remote,
                        posted_at=posted,
                        raw_data={"site": site},
                    )
                )
            except Exception as e:
                logger.debug("JobSpy row parse error: %s", e)
        return jobs
