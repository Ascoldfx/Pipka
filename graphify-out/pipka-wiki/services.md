# Services Domain

Business logic controlling aggregation triggers, match scoring, and application pipelines.
Dependencies: [[db.md]], [[models.md]], [[sources.md]]

## Fast Rejection Rules
Path: `app/scoring/rules.py`
High-performance rule-based filtering (`pre_filter`). Automatically rejects jobs missing domain criteria (Logistics/Supply Chain) or meeting user negative rules (`excluded_keywords`). Saves API costs by dropping invalid roles before AI analysis.

## Job Score Matching
Path: `app/scoring/matcher.py`
Executes Claude 3 calls (`anthropic`) to deeply read Job Descriptions and grade them linearly against the UserProfile. Integrates `CRITICAL EXCLUSIONS` from the user's negative keywords list into the prompt to strictly enforce AI-driven rejection. Returns the internal generic `JobScore` model dict.

## Schedulers
Path: `app/services/scheduler_service.py`
Initializes `APScheduler` tracking intervals for automatic continuous searches. Iterates across users and active `UserProfile` variables (countries and targets), building a `SearchParams` payload for `JobAggregator` defined in [[sources.md]].

Triggers notifications by directly pushing to the `bot` object via Telegram core functionality if scores cross 80.

## User Tracker Services
Path: `app/services/tracker_service.py`
State machines mapping the application process, toggling variables inside the `Application` schema mapping rows within PostgreSQL. Also provides lookup functions for missing `JobScore` instances against unreviewed applications.
