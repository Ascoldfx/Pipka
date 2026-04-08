from __future__ import annotations

import hashlib
import re
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
        normalized = _normalize(f"{self.title}|{self.company_name or ''}|{self.location or ''}")
        return hashlib.sha256(normalized.encode()).hexdigest()


def _normalize(text: str) -> str:
    text = text.lower().strip()
    # Remove common suffixes from company names
    for suffix in ("gmbh", " ag", " ltd", " inc", " se", " co.", "& co", " mbh"):
        text = text.replace(suffix, "")
    # Remove job board noise from titles (Adzuna appends categories)
    text = re.sub(r"\s*-\s*(system engineering|admin|ingenieur|it|engineering).*$", "", text)
    # Remove (m/w/d) and similar
    text = re.sub(r"\s*\(m/w/d\)\s*", " ", text)
    text = re.sub(r"\s*\(w/m/d\)\s*", " ", text)
    text = re.sub(r"\s*\(all genders\)\s*", " ", text)
    # Remove "senior" for dedup — "Senior X" and "X" at same company = likely same role
    text = text.replace("senior ", "")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    # Remove location specifics (postal codes)
    text = re.sub(r"\d{5}", "", text)
    return text.strip()


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
