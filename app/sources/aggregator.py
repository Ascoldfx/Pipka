from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.job import Job
from app.sources.base import JobSource, RawJob, SearchParams, is_fuzzy_duplicate

logger = logging.getLogger(__name__)

NEGATIVE_KEYWORDS = [
    "ausbildung", "student", "praktikum", "azubi", "trainee",
    "werkstudent", "junior", "intern", "duales studium",
    "specialist", "analyst", "coordinator", "assistant", "clerk",
    "sachbearbeiter", "referent", "mitarbeiter", "fachkraft",
    "dispatcher", "planner",
    "merchandiser", "account executive", "founding",
]

# Foreign language requirements (not EN/DE) → reject
FOREIGN_LANG_REQUIRED = [
    "langue requise", "français courant", "francais courant",
    "courant français", "courant francais",
    "maîtrise du français", "french: native", "french required",
    "français - courant", "francais - courant",
    "español requerido", "spanish required",
    "italiano richiesto", "italian required",
    "polski wymagany", "polish required",
]

# Phrases in title or description that indicate restricted positions
EXCLUSION_PHRASES = [
    "schwerbehindert",
    "schwerbehinderung",
    "ausschließlich für schwerbehinderte",
    "ausschliesslich für schwerbehinderte",
    "exclusively for severely disabled",
    "тяжелыми формами инвалидности",
    "gleichgestellte",
    "nur für schwerbehinderte",
]

# German C1+ / native required — auto-reject (candidate has B1)
GERMAN_C1_REQUIRED = [
    # German level markers
    "deutsch c1", "deutsch c2", "german c1", "german c2",
    "deutschkenntnisse c1", "deutschkenntnisse c2",
    # Verhandlungssicher / fluent
    "verhandlungssicheres deutsch", "verhandlungssicher deutsch",
    "verhandlungssichere deutschkenntnisse",
    # Native
    "deutsch als muttersprache", "deutsch muttersprachlich",
    "muttersprachliche deutschkenntnisse", "muttersprachliches deutsch",
    "deutsch auf muttersprachniveau",
    # Fließend
    "fließende deutschkenntnisse", "fliessende deutschkenntnisse",
    "fließend deutsch", "fliessend deutsch",
    "fließendes deutsch", "fließend in deutsch",
    # Perfekt / sehr gut
    "perfekte deutschkenntnisse", "perfektes deutsch",
    "sehr gute deutschkenntnisse", "sehr guten deutschkenntnissen",
    "exzellente deutschkenntnisse",
    # English versions
    "german native", "native german", "native-level german",
    "fluent german", "fluent in german", "german fluency",
    "fluency in german", "proficient in german",
    "german & english fluency", "german and english fluency",
    "fluent in both german and english",
    "business fluent german", "business-fluent german",
    # Combined patterns
    "german (native", "german (fluent", "german (c1", "german (c2",
    "deutsch (verhandlungssicher", "deutsch (fließend",
    "deutsch (muttersprachlich", "deutsch (c1", "deutsch (c2",
    # Proficiency patterns
    "proficiency in written and spoken german",
    "proficiency in german and english",
    "proficient in german and english",
    "written and spoken german",
    "german and english required",
    "german language skills required",
    "strong german language",
    "excellent german",
    # German compound patterns (Deutsch- und Englischkenntnisse)
    "verhandlungssichere deutsch- und englischkenntnisse",
    "verhandlungssichere deutsch und englischkenntnisse",
    "deutsch- und englischkenntnisse in wort und schrift",
    "deutschkenntnisse in wort und schrift",
    "deutsch in wort und schrift",
    "mindestens c1 deutsch",
    # Additional patterns that slip through
    "sehr gute deutsch- und englischkenntnisse",
    "sehr gute deutsch und englischkenntnisse",
    "gute deutschkenntnisse",
    "gute deutsch- und englischkenntnisse",
    "sichere deutschkenntnisse",
    "german: business fluency",
    "german business fluency",
    "german language: fluent",
    "german language: business",
    "german language proficiency",
    "advanced german",
    "german is highly preferred",
    "german: business fluency is highly preferred",
]


class JobAggregator:
    def __init__(self, sources: list[JobSource]):
        self.sources = sources
        self.last_stats: dict = {}

    SOURCE_TIMEOUT = 120   # default timeout per source
    # JobSpy scrapes LinkedIn + Indeed sequentially; needs more time for many queries
    SOURCE_TIMEOUT_OVERRIDES: dict[str, int] = {"jobspy": 240}

    async def _search_source(self, source: JobSource, params: SearchParams):
        """Run one source with a hard timeout so a hung source can't block the scan."""
        timeout = self.SOURCE_TIMEOUT_OVERRIDES.get(source.source_name, self.SOURCE_TIMEOUT)
        try:
            return await asyncio.wait_for(source.search(params), timeout=timeout)
        except asyncio.TimeoutError:
            raise asyncio.TimeoutError(
                f"{source.source_name} timed out after {timeout}s"
            )

    async def search(self, params: SearchParams, session: AsyncSession) -> list[Job]:
        tasks = [self._search_source(source, params) for source in self.sources]
        all_results = await asyncio.gather(*tasks, return_exceptions=True)

        raw_jobs: list[RawJob] = []
        source_stats: list[dict] = []
        for i, result in enumerate(all_results):
            source_name = self.sources[i].source_name
            if isinstance(result, Exception):
                logger.error("Source %s failed: %s", source_name, result)
                source_stats.append(
                    {
                        "source": source_name,
                        "status": "error",
                        "raw_count": 0,
                        "error": str(result)[:200],
                    }
                )
                continue
            stat: dict = {
                "source": source_name,
                "status": "ok",
                "raw_count": len(result),
            }
            # Include API request count if the source tracked it (e.g. Jooble budget)
            src_obj = self.sources[i]
            if hasattr(src_obj, "_last_request_count"):
                stat["api_requests"] = src_obj._last_request_count
            source_stats.append(stat)
            raw_jobs.extend(result)

        logger.info("Aggregated %d raw jobs from %d sources", len(raw_jobs), len(self.sources))

        # Pass 1 — exact dedup by SHA-256(title+company)
        seen_hashes: set[str] = set()
        unique: list[RawJob] = []
        for job in raw_jobs:
            if job.dedup_hash not in seen_hashes:
                seen_hashes.add(job.dedup_hash)
                unique.append(job)

        logger.info("After exact dedup: %d unique jobs", len(unique))

        # Pass 2 — fuzzy dedup: same normalised title + company-name subset match
        # Handles "Heraeus" vs "Heraeus Quarzglas GmbH & Co. KG HRdirekt" and
        # the same job posted on Indeed + LinkedIn + Arbeitsagentur simultaneously.
        # O(n²) but n < 500 per scan in practice — negligible.
        fuzzy_deduped: list[RawJob] = []
        for job in unique:
            merged = False
            for i, seen in enumerate(fuzzy_deduped):
                if is_fuzzy_duplicate(job, seen):
                    # Accumulate all sources seen for this job
                    all_sources: list[str] = seen.raw_data.get("merged_sources", [seen.source])
                    if job.source not in all_sources:
                        all_sources.append(job.source)

                    if len(job.description or "") > len(seen.description or ""):
                        # Switch to richer candidate, carry over the merged sources list
                        job.raw_data["merged_sources"] = all_sources
                        fuzzy_deduped[i] = job
                    else:
                        seen.raw_data["merged_sources"] = all_sources
                    merged = True
                    break
            if not merged:
                fuzzy_deduped.append(job)

        removed = len(unique) - len(fuzzy_deduped)
        if removed:
            logger.info("After fuzzy dedup: %d jobs (removed %d near-duplicates)", len(fuzzy_deduped), removed)
        unique = fuzzy_deduped

        # Filter with stats
        cutoff = datetime.now() - timedelta(days=params.max_age_days)
        filtered: list[RawJob] = []
        rejected_negative = 0
        rejected_old = 0
        rejected_location = 0
        for job in unique:
            if _is_negative(job):
                rejected_negative += 1
                continue
            if job.posted_at and job.posted_at < cutoff:
                rejected_old += 1
                continue
            if _is_wrong_location(job):
                rejected_location += 1
                continue
            filtered.append(job)
            # Log jobs that PASSED location filter for debugging
            if job.location and not any(m in (job.location or "").lower() for m in DACH_MARKERS):
                logger.debug("Location passed (no DACH marker): '%s' — %s @ %s", job.location, job.title, job.company_name)

        logger.info(
            "After filter: %d jobs (rejected: %d negative/german, %d old, %d wrong location)",
            len(filtered), rejected_negative, rejected_old, rejected_location,
        )
        self.last_stats = {
            "raw_count": len(raw_jobs),
            "unique_count": len(unique),
            "fuzzy_removed": removed,
            "filtered_count": len(filtered),
            "rejected_negative": rejected_negative,
            "rejected_old": rejected_old,
            "rejected_location": rejected_location,
            "sources": source_stats,
        }

        # Upsert into DB
        db_jobs: list[Job] = []
        for raw in filtered:
            existing = await session.execute(select(Job).where(Job.dedup_hash == raw.dedup_hash))
            job_row = existing.scalar_one_or_none()
            if job_row is None:
                job_row = Job(
                    external_id=raw.external_id,
                    source=raw.source,
                    title=raw.title,
                    company_name=raw.company_name,
                    location=raw.location,
                    country=raw.country,
                    description=raw.description,
                    salary_min=raw.salary_min,
                    salary_max=raw.salary_max,
                    salary_currency=raw.salary_currency,
                    url=raw.url,
                    is_remote=raw.is_remote,
                    posted_at=raw.posted_at,
                    raw_data=raw.raw_data,
                    dedup_hash=raw.dedup_hash,
                )
                session.add(job_row)
            db_jobs.append(job_row)

        await session.commit()
        # Sort: newest first
        db_jobs.sort(key=lambda j: j.posted_at or datetime.min, reverse=True)
        return db_jobs


US_STATE_ABBREVS = [
    ", al", ", ak", ", az", ", ar", ", ca", ", co", ", ct", ", de", ", fl",
    ", ga", ", hi", ", id", ", il", ", in", ", ia", ", ks", ", ky", ", la",
    ", me", ", md", ", ma", ", mi", ", mn", ", ms", ", mo", ", mt", ", ne",
    ", nv", ", nh", ", nj", ", nm", ", ny", ", nc", ", nd", ", oh", ", ok",
    ", or", ", pa", ", ri", ", sc", ", sd", ", tn", ", tx", ", ut", ", vt",
    ", va", ", wa", ", wv", ", wi", ", wy",
]

US_STATE_NAMES = [
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york", "north carolina",
    "north dakota", "ohio", "oklahoma", "oregon", "pennsylvania",
    "rhode island", "south carolina", "south dakota", "tennessee", "texas",
    "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming",
]

US_KEYWORDS = [
    "united states", "usa", ", us",
    "greater chicago", "chicago metropolitan", "chicago area",
    "greater new york", "greater los angeles", "greater boston",
    "greater seattle", "greater denver", "san francisco bay",
    "silicon valley", "wall street", "bay area",
    "greater philadelphia", "greater atlanta", "greater dallas", "greater washington",
    "greater miami", "greater phoenix", "greater minneapolis", "greater st. louis",
    "greater detroit", "greater pittsburgh", "greater cleveland", "greater baltimore",
    "greater salt lake", "greater memphis", "greater richmond", "greater louisville",
    # Russian — LinkedIn returns locations in Russian
    "соединенные штаты", "соединённые штаты", "сша",
    "агломерация", # "Агломерация Нью-Йорка", "Агломерация Чикаго" etc.
]

# Russian city/country names from LinkedIn
NON_DACH_RUSSIAN = [
    # US cities in Russian
    "нью-йорк", "чикаго", "лос-анджелес", "сан-франциско", "бостон",
    "сиэтл", "денвер", "остин", "майами", "хьюстон", "даллас",
    "атланта", "финикс", "портленд", "филадельфия", "детройт",
    "миннеаполис", "сан-диего", "сан-хосе", "шарлотт", "нэшвилл",
    "колумбус", "индианаполис", "питтсбург", "цинциннати",
    "канзас-сити", "тампа", "орландо", "балтимор", "сакраменто",
    "кливленд", "новый орлеан",
    # Other global hubs (Asia/Americas/Oceania)
    "сингапур", "токио", "шанхай", "пекин", "сеул",
    "сидней", "мельбурн", "торонто", "ванкувер", "монреаль",
    "дубай",
]

NON_DACH_CITIES = [
    # US cities (extended)
    "new york", "san francisco", "los angeles", "chicago", "boston",
    "seattle", "denver", "austin", "miami", "houston", "dallas",
    "atlanta", "phoenix", "portland", "philadelphia", "detroit",
    "minneapolis", "san diego", "san jose", "charlotte", "nashville",
    "columbus", "indianapolis", "jacksonville", "raleigh", "pittsburgh",
    "cincinnati", "kansas city", "salt lake", "tampa", "orlando",
    "st. louis", "st louis", "baltimore", "sacramento", "milwaukee",
    "oklahoma", "richmond", "memphis", "louisville", "hartford",
    "tucson", "fresno", "mesa", "omaha", "tulsa", "arlington",
    "new orleans", "cleveland", "honolulu", "anchorage", "boise",
    "des moines", "little rock", "birmingham", "spokane", "rochester",
    "grand rapids", "knoxville", "chattanooga", "greensboro",
    # Canada cities & provinces
    "toronto", "vancouver", "montreal", "calgary", "edmonton",
    "ottawa", "winnipeg", "quebec city", "halifax", "victoria",
    "ontario", "british columbia", "alberta", "manitoba", "quebec",
    "nova scotia", "new brunswick", "saskatchewan",
    # UK — removed (now in ALLOWED_COUNTRIES + DACH_MARKERS)
    # "london", "manchester", etc. → user has UK in target countries
    # Asia
    "singapore", "tokyo", "shanghai", "beijing", "seoul",
    "mumbai", "bangalore", "dubai", "hong kong", "kuala lumpur", "taipei", "jakarta", "bangkok",
    "manila", "hanoi", "ho chi minh",
    # Other
    "sydney", "melbourne", "são paulo", "abu dhabi", "riyadh", "doha",
    "mexico city", "buenos aires", "bogota", "lima", "santiago",
    "cape town", "johannesburg", "nairobi", "lagos",
]

# European locations — if these appear in job text, job is considered valid
DACH_MARKERS = [
    # DACH
    "germany", "deutschland", "austria", "österreich", "oesterreich",
    "netherlands", "nederland", "schweiz", "switzerland",
    "berlin", "munich", "münchen", "muenchen",
    "hamburg", "frankfurt", "düsseldorf", "duesseldorf", "cologne", "köln", "koeln",
    "stuttgart", "leipzig", "dresden", "hannover", "nürnberg", "nuernberg",
    "dortmund", "essen", "bremen", "bonn", "mannheim", "karlsruhe",
    "augsburg", "wiesbaden", "braunschweig", "freiburg", "erfurt",
    "rostock", "kassel", "halle", "magdeburg", "chemnitz", "potsdam",
    "vienna", "wien", "graz", "salzburg", "linz", "innsbruck",
    "amsterdam", "rotterdam", "den haag", "utrecht", "eindhoven",
    # Belgium
    "belgium", "belgien", "bruxelles", "brussels", "brüssel", "antwerp",
    "antwerpen", "gent", "ghent", "leuven", "liège", "liege",
    # CEE (Slovenia, Slovakia, Romania, Hungary)
    "slovenia", "slowenien", "ljubljana", "maribor",
    "slovakia", "slowakei", "bratislava", "kosice", "košice",
    "romania", "rumänien", "bucharest", "cluj", "timisoara", "iasi", "constanta",
    "hungary", "ungarn", "budapest", "debrecen", "miskolc", "pecs",
    # Poland
    "poland", "polen", "warszawa", "warsaw", "krakow", "kraków", "wroclaw", "wrocław",
    "gdansk", "gdańsk", "poznan", "poznań", "lodz", "łódź", "katowice",
    "szczecin", "bydgoszcz", "lublin", "białystok", "gdynia", "silesia",
    # Czech Republic
    "czech", "tschechien", "czechia", "prague", "praha", "brno", "ostrava",
    "plzen", "plzeň", "olomouc", "liberec", "hradec kralove",
    # Western Europe (for user-expanded target countries)
    "france", "frankreich", "paris", "lyon", "marseille", "toulouse",
    "lille", "bordeaux", "nantes", "strasbourg", "nice",
    "spain", "spanien", "madrid", "barcelona", "valencia", "seville", "bilbao",
    "italy", "italien", "milan", "milano", "rome", "roma", "turin", "torino",
    "florence", "firenze", "naples", "napoli", "bologna", "genova",
    "portugal", "lissabon", "lisbon", "porto", "braga", "faro",
    "ireland", "irland", "dublin", "cork", "galway",
    "denmark", "dänemark", "copenhagen", "københavn", "aarhus",
    "sweden", "schweden", "stockholm", "gothenburg", "göteborg", "malmö",
    "norway", "norwegen", "oslo", "bergen", "trondheim",
    "finland", "finnland", "helsinki", "tampere", "turku",
    "uk", "united kingdom", "england", "scotland", "wales",
    "london", "manchester", "edinburgh", "glasgow", "leeds", "bristol",
    "liverpool", "cambridge", "oxford",
    "luxembourg", "luxemburg",
    # Russian DACH names (LinkedIn)
    "германия", "берлин", "мюнхен", "гамбург", "франкфурт",
    "штутгарт", "дюссельдорф", "кёльн", "лейпциг", "дрезден",
    "ганновер", "нюрнберг", "бремен", "бонн", "дортмунд",
    "австрия", "вена", "грац", "зальцбург",
    "нидерланды", "амстердам", "роттердам", "гаага", "утрехт",
    "словения", "любляна", "словакия", "братислава",
    "румыния", "бухарест", "венгрия", "будапешт",
    "польша", "варшава", "краков", "вроцлав", "гданьск",
    "чехия", "прага", "брно",
    "франция", "париж", "испания", "мадрид", "барселона",
    "италия", "милан", "рим", "португалия", "лиссабон",
    "швеция", "стокгольм", "норвегия", "осло", "дания", "копенгаген",
    "финляндия", "хельсинки", "ирландия", "дублин",
    "великобритания", "лондон", "манчестер",
]

# All European country codes we're willing to index jobs for
ALLOWED_COUNTRIES = {
    "de", "at", "nl", "ch", "be",           # DACH + NL + BE
    "si", "sk", "ro", "hu",                  # CEE (original)
    "pl", "cz",                              # Poland, Czech Republic
    "fr", "es", "it", "pt",                  # Southern/Western Europe
    "gb", "uk", "ie",                        # British Isles
    "dk", "se", "no", "fi",                  # Nordics
    "lu",                                    # Luxembourg
}


def _is_wrong_location(job: RawJob) -> bool:
    """Filter out jobs that are clearly outside European target region.

    Key insight: LinkedIn via JobSpy often returns location=None and country="DE"
    (search param, not actual country). So we MUST check description text and
    REQUIRE a European marker if location is empty.
    """
    # If the source tagged the job with a known European country code → allow immediately
    if job.country and job.country.lower() in ALLOWED_COUNTRIES:
        return False

    # Hard block: if the source explicitly tagged the job as US or CA country,
    # reject immediately — no location text analysis needed.
    if job.country and job.country.lower() in {"us", "ca"}:
        return True

    location_lower = (job.location or "").lower()
    title_lower = job.title.lower()
    desc_lower = (job.description or "").lower()
    url_lower = (job.url or "").lower()
    all_text = f"{location_lower} {title_lower} {desc_lower} {url_lower}"

    has_dach = any(m in all_text for m in DACH_MARKERS)

    # If location explicitly mentions DACH → allow
    if location_lower and any(m in location_lower for m in DACH_MARKERS):
        return False

    # --- Blacklist checks (location field) ---
    if location_lower:
        if any(us in location_lower for us in US_STATE_ABBREVS):
            return True
        if any(state in location_lower for state in US_STATE_NAMES):
            return True
        if any(kw in location_lower for kw in US_KEYWORDS):
            return True
        if any(city in location_lower for city in NON_DACH_CITIES):
            return True
        if any(city in location_lower for city in NON_DACH_RUSSIAN):
            return True

    # --- Blacklist checks (description + title) ---
    if any(kw in all_text for kw in US_KEYWORDS):
        if not has_dach:
            return True
    if any(state in desc_lower for state in US_STATE_NAMES):
        if not has_dach:
            return True
    if any(city in desc_lower for city in NON_DACH_CITIES):
        if not has_dach:
            return True
    if any(city in all_text for city in NON_DACH_RUSSIAN):
        if not has_dach:
            return True

    # Relaxed location check: if no location provided, we pass it to AI
    # US_KEYWORDS blacklist will catch the most obvious US spam.

    return False


def _is_negative(job: RawJob) -> bool:
    title_lower = job.title.lower()
    desc_lower = (job.description or "").lower()
    text = f"{title_lower} {desc_lower}"
    if any(kw in title_lower for kw in NEGATIVE_KEYWORDS):
        return True
    if any(phrase in text for phrase in EXCLUSION_PHRASES):
        return True
    if any(phrase in text for phrase in GERMAN_C1_REQUIRED):
        return True
    # Foreign language required (not EN/DE)
    if any(phrase in desc_lower for phrase in FOREIGN_LANG_REQUIRED):
        return True
    return False
