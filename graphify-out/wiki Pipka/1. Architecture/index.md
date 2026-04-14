# Knowledge Graph Index

Welcome to the `jobhunt` Knowledge Graph. This AI-powered job search aggregator focuses on the European market, integrating a Telegram Bot and a FastApi Dashboard.

## Navigation Map
- [[config.md]] - Application Configuration and Environment Variables
- [[db.md]] - Database Connectivity (PostgreSQL/SQLite) and Alembic Migrations
- [[models.md]] - SQLAlchemy DOM models (User, Job, JobScore, Application)
- [[routes.md]] - FastAPI endpoints serving the dashboard and API
- [[services.md]] - Business Logic, Background Scanning (APScheduler), AI Scoring
- [[sources.md]] - Job scraping aggregators (JobSpy, Adzuna, Remotive, Arbeitnow)
- [[bot.md]] - Telegram Bot UI and interaction handlers
- [[Changelog_Exclusions.md]] - Feature Log: Exclusions & Negative Keywords

## Blast-Radius Table
| Component | Primary Files | Dependents | Risk Level |
| :--- | :--- | :--- | :--- |
| **Models** | `app.models.*` | DB, Routes, Bot, Services, Scrapers | High - Schema alters require migrations
| **Sources** | `app.sources.*` | Aggregator, Scheduler | Medium - Source changes only break that specific API import
| **Services** | `app.services.*`| Routes, Bot | Medium - Core logic affecting AI and schedules
| **Bot UI** | `app.bot.*` | Telegram Users | Low - Visual updates
| **Dashboard** | `app.api.*` | Web Users | Low - UI state logic
