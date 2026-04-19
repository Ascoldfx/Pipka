from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str

    # Adzuna
    adzuna_app_id: str
    adzuna_app_key: str

    # Claude AI
    anthropic_api_key: str

    # Google Gemini (optional — free tier backfill scorer)
    # Set GEMINI_API_KEY in .env to enable; leave empty to use Claude for backfill too
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash-lite"  # 30 RPM / 1500 RPD free tier
    gemini_batch_delay: float = 4.0  # seconds between batches (30 RPM → 1 req/2s, use 4s to be safe)

    # Database
    database_url: str = "sqlite+aiosqlite:///./pipka.db"

    # Arbeitsagentur
    arbeitsagentur_api_key: str = "jobboerse-jobsuche"

    # Jooble meta-aggregator (covers Stepstone, Monster, regional boards)
    jooble_api_key: str = ""

    # Scoring
    max_jobs_per_scoring_batch: int = 8
    max_scored_per_search: int = 30
    score_cache_hours: int = 168  # 7 days
    claude_timeout_seconds: float = 60.0
    claude_max_retries: int = 2

    # Claude model/token knobs (overridable via .env without redeploy)
    claude_model: str = "claude-sonnet-4-20250514"
    claude_scoring_max_tokens: int = 5000     # batch scoring response budget
    claude_analysis_max_tokens: int = 1500    # single-job detailed analysis budget

    # Dashboard Authentication (legacy Basic Auth — kept for backward compat)
    dashboard_username: str = ""
    dashboard_password: str = ""
    guest_username: str = ""
    guest_password: str = ""

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    session_secret: str  # signs session cookies — REQUIRED, must be set in .env

    # Admin emails (comma-separated) — these Google accounts get admin role
    admin_emails: str = ""

    # Search
    default_results_limit: int = 50
    job_max_age_days: int = 45

    # Logging
    log_level: str = "INFO"

    # `extra="ignore"` lets us share .env with docker-compose interpolation vars
    # (e.g. POSTGRES_PASSWORD) without breaking Settings validation.
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
