from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
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
        normalized = _normalize(f"{self.title}|{self.company_name or ''}")
        return hashlib.sha256(normalized.encode()).hexdigest()


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


def is_fuzzy_duplicate(a: "RawJob", b: "RawJob") -> bool:
    """True if two raw jobs are likely the same posting (different source/company spelling).

    Criteria: normalised title must match exactly AND company names must be
    compatible (one is a refinement/legal-expansion of the other).
    """
    if _normalize(a.title) != _normalize(b.title):
        return False
    return _are_same_company(a.company_name, b.company_name)


@dataclass
class SearchParams:
    queries: list[str]
    countries: list[str] = field(default_factory=lambda: ["de"])
    locations: list[str] = field(default_factory=list)
    results_per_query: int = 50
    max_age_days: int = 60


class JobSource(Protocol):
    @property
    def source_name(self) -> str: ...

    async def search(self, params: SearchParams) -> list[RawJob]: ...
