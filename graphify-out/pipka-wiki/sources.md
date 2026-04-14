# Sources Domain

Abstraction wrapper around diverse job boards and API endpoints handling extraction to unified formats.
Dependencies: [[models.md]]
Dependent on by: [[services.md]], [[routes.md]]

## Base Extractor
Path: `app/sources/base.py`
Defines `BaseSource` and structured `SearchParams` payloads:
```python
class SearchParams(BaseModel):
    queries: list[str]
    locations: list[str]
    countries: list[str]

class RawJob(BaseModel):
    url: str
    title: str
    company_name: str
    location: str
    description: str
```

## Aggregator Component
Path: `app/sources/aggregator.py`
A massive manager initializing multiple `BaseSource` providers (such as JobSpy, Remotive). Runs geographic filters allowing cross-checking of blacklist logic across US State Names or arbitrary non-requested geographic hubs.

## Native Sources
Path: `app/sources/*.py`
1. Remotive (`remotive.py`) - Remote job aggregation.
2. Arbeitnow (`arbeitnow.py`) - European/German startups.
3. JobSpy (`jobspy_source.py`) - Powerful library mapping Google, LinkedIn, Adzuna schemas.
