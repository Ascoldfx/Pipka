from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


@dataclass
class RawJob:
    external_id: str
    source: str
    title: str
    company_name: str | None = None
    location: str | None = None
    country: str | None = None
    description: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    url: str | None = None
    is_remote: bool | None = None
    posted_at: datetime | None = None
    raw_data: dict = field(default_factory=dict)

    @property
    def dedup_hash(self) -> str:
        return build_dedup_hash(
            self.title,
            self.company_name,
            self.country,
            self.location,
        )


def normalise_posted_at(value: datetime | None) -> datetime | None:
    """Store source timestamps as UTC-naive without discarding an offset."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def build_dedup_hash(
    title: str,
    company_name: str | None,
    country: str | None,
    location: str | None,
) -> str:
    """Stable v2 identity that preserves same-company roles by geography."""
    components = (
        _normalize(title),
        _normalize(company_name or ""),
        _normalize(country or ""),
        _normalize(location or ""),
    )
    return hashlib.sha256(("v2|" + "|".join(components)).encode()).hexdigest()


def _normalize(text: str) -> str:
    # Strip accents: é→e, ü→u, ä→a etc.
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower().strip()
    # Remove common suffixes from company names
    for suffix in ("gmbh", " ag", " ltd", " inc", " se", " co.", "& co", " mbh",
                   " kg", " e.v.", " ohg", " ug", " sarl", " bv", " nv"):
        text = text.replace(suffix, "")
    # Remove job board noise from titles (Adzuna appends categories)
    text = re.sub(r"\s*-\s*(system engineering|admin|ingenieur|it|engineering).*$", "", text)
    # Remove (m/w/d), (m/f/d), (all genders), (f/m/x) and similar
    text = re.sub(r"\s*\([mwfd/]+\)\s*", " ", text)
    text = re.sub(r"\s*\(all genders?\)\s*", " ", text)
    text = re.sub(r"\s*\(m/f/x\)\s*", " ", text)
    # Remove "senior" for dedup — "Senior X" and "X" at same company = likely same role
    text = text.replace("senior ", "")
    # Remove location from title (e.g. "Director | Berlin", "COO - Munich")
    text = re.sub(r"\s*[|–—-]\s*(berlin|münchen|munich|hamburg|frankfurt|düsseldorf|köln|cologne|stuttgart|leipzig|dresden|hannover|nürnberg|dortmund|essen|bremen|bonn)\b.*$", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    # Remove location specifics (postal codes)
    text = re.sub(r"\d{5}", "", text)
    return text.strip()


def fuzzy_title_key(title: str) -> str:
    """Return the exact title key used by fuzzy duplicate comparison."""
    return _normalize(title)


def _company_tokens(company: str) -> frozenset[str]:
    """Extract significant tokens from a company name for fuzzy comparison."""
    text = _normalize(company)
    text = re.sub(r'[^\w\s]', '', text)   # strip remaining punctuation artifacts
    return frozenset(w for w in text.split() if len(w) >= 4)


def _are_same_company(a: str | None, b: str | None) -> bool:
    """True if two company names refer to the same organisation.

    Main algorithm: token-subset check.
      "Heraeus" (tokens: {heraeus})
      "Heraeus Quarzglas GmbH & Co. KG HRdirekt" (tokens: {heraeus, quarzglas, hrdirekt})
      → {heraeus} ⊆ {heraeus, quarzglas, hrdirekt} → same company ✓

    Fallback for short names (BMW, VW, SAP etc. where all tokens < 4 chars):
      word-list prefix comparison.
    """
    if not a and not b:
        return True
    if not a or not b:
        return False
    ta = _company_tokens(a)
    tb = _company_tokens(b)
    if ta and tb:
        return ta <= tb or tb <= ta
    # Fallback — at least one name has only short tokens (BMW, SAP, VW …)
    wa = [w for w in re.sub(r"[^\w]", " ", _normalize(a)).split() if w]
    wb = [w for w in re.sub(r"[^\w]", " ", _normalize(b)).split() if w]
    if not wa or not wb:
        return False
    n = min(len(wa), len(wb))
    return wa[:n] == wb[:n]


def _location_root(location: str) -> str:
    """Return the first significant word of a location for loose comparison.

    "Kleinostheim, BY, DE (DE)"  → "kleinostheim"
    "Frankfurt am Main"          → "frankfurt"
    "Munich, Bavaria"            → "munich"
    "Germany"                    → "germany"
    """
    text = unicodedata.normalize("NFD", location)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.lower()
    # Drop postal codes, country codes in parens like "(DE)"
    text = re.sub(r"\([a-z]{2,3}\)", "", text)
    text = re.sub(r"\b[a-z]{2}\b", "", text)   # short country/state codes
    text = re.sub(r"\d+", "", text)             # postal codes
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Return the first word that is >= 4 chars (city name)
    words = [w for w in text.split() if len(w) >= 4]
    return words[0] if words else text[:8]


def _locations_conflict(a: str | None, b: str | None) -> bool:
    """True if locations conflict — meaning jobs should NOT be merged.

    Rules (conservative — prefer keeping jobs separate on any doubt):
      • Both unknown          → False  (no info either way, allow merge)
      • One known, one not    → True   (asymmetry = uncertainty = keep both)
      • Both known, same city → False  (clearly same place, allow merge)
      • Both known, diff city → True   (different places, keep both)
    """
    a_has = bool(a and a.strip())
    b_has = bool(b and b.strip())

    # Both missing — no information to conflict on
    if not a_has and not b_has:
        return False

    # Asymmetry: one has location, other doesn't — too uncertain to merge
    if a_has != b_has:
        return True

    # Both present — compare city roots
    ra = _location_root(a)  # type: ignore[arg-type]
    rb = _location_root(b)  # type: ignore[arg-type]
    if not ra or not rb:
        return False
    return ra != rb


def is_fuzzy_duplicate(a: "RawJob", b: "RawJob") -> bool:
    """True if two raw jobs are likely the same posting (different source/company spelling).

    All three conditions must hold:
      1. Normalised title matches exactly.
      2. Company names are compatible (one is a refinement of the other).
      3. Countries and locations do NOT clearly conflict.

    The location guard prevents merging e.g.:
      "Head of Procurement" @ Siemens Energy, Frankfurt
      "Head of Procurement" @ Siemens Healthineers, Erlangen
    """
    if fuzzy_title_key(a.title) != fuzzy_title_key(b.title):
        return False
    if not _are_same_company(a.company_name, b.company_name):
        return False
    if a.country and b.country and _normalize(a.country) != _normalize(b.country):
        return False
    if _locations_conflict(a.location, b.location):
        return False
    return True


@dataclass
class SearchParams:
    queries: list[str]
    countries: list[str] = field(default_factory=lambda: ["de"])
    locations: list[str] = field(default_factory=list)
    country_queries: dict[str, list[str]] = field(default_factory=dict)
    results_per_query: int = 50
    max_age_days: int = 60

    def queries_for_country(self, country: str) -> list[str]:
        """Use country-pack aliases when present, otherwise global queries."""
        return self.country_queries.get(country.casefold(), self.queries)


class JobSource(Protocol):
    @property
    def source_name(self) -> str: ...

    async def search(self, params: SearchParams) -> list[RawJob]: ...
