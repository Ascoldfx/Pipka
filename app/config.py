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
    gemini_model: str = "gemini-3.1-flash-lite-preview"  # 15 RPM / 500 RPD free tier — единственная живая модель на ascoldfx@gmail free tier (2.5/2.0/3-flash все выбраны)
    gemini_batch_delay: float = 4.0  # seconds between batches (30 RPM → 1 req/2s, use 4s to be safe)

    # NVIDIA Build (optional — idle rescorer, runs only when Gemini queue drained)
    # Get key at https://build.nvidia.com → set NVIDIA_API_KEY in .env to enable.
    nvidia_api_key: str = ""
    nvidia_model: str = "google/gemma-4-31b-it"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_batch_delay: float = 2.0      # seconds between batches (conservative)
    nvidia_max_per_run: int = 300        # hard cap per scheduler tick
    nvidia_country: str = "de"           # ISO country filter for idle rescore
    nvidia_rescore_stale_days: int = 7   # refresh successful scores older than N days

    # URL liveness checker (daily HEAD-ping to detect closed postings).
    url_check_enabled: bool = True
    url_check_per_run: int = 500             # how many jobs to check each tick
    url_check_concurrency: int = 10          # parallel HEAD requests in flight
    url_check_per_host_delay: float = 1.5    # min seconds between requests to the same host
    url_check_recheck_hours: int = 20        # don't recheck within this window (default ~1/day)
    url_check_timeout_seconds: float = 10.0  # per-request HTTP timeout
    url_check_max_failures: int = 3          # consecutive transient failures → mark unreachable

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

    # Sentry — error tracking. Empty DSN disables Sentry entirely (no SDK init).
    sentry_dsn: str = ""
    sentry_environment: str = "production"
    sentry_traces_sample_rate: float = 0.05   # 5% performance traces
    sentry_profiles_sample_rate: float = 0.05  # 5% profiling samples

    # Backblaze B2 backups (optional — local backup always runs when DB is PostgreSQL)
    # Set all three to enable cloud upload; leave empty to use local-only backups
    b2_key_id: str = ""
    b2_app_key: str = ""
    b2_bucket: str = ""
    b2_endpoint: str = "https://s3.us-west-004.backblazeb2.com"

    # `extra="ignore"` lets us share .env with docker-compose interpolation vars
    # (e.g. POSTGRES_PASSWORD) without breaking Settings validation.
    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
