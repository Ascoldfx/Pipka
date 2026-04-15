# Routes Domain

FastAPI domain serving UI dashboards, system actions, and webhooks.
Dependencies: [[models.md]], [[db.md]], [[services.md]]

## Main Gateway
Path: `app/main.py`
Exposes the main FastAPI application setup, and routes webhook Telegram endpoints.

## Dashboard Backend
Path: `app/api/dashboard.py`
Provides backend JSON APIs utilized by the frontend site `dashboard.html`.

Provides views over the aggregated Jobs data, handles search filters, and reads User Profiles:
```python
@router.get("/api/jobs")
async def get_jobs(...): ...

@router.get("/api/stats")
async def get_stats(): ...

@router.post("/api/scan")
async def trigger_scan(): ...
```

## System Dependencies
Routes are deeply attached to the PostgreSQL context initialized via [[db.md]] and depend on background scanner schedules exposed in [[services.md]].
