from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str

    # Adzuna
    adzuna_app_id: str
    adzuna_app_key: str

    # Claude AI
    anthropic_api_key: str

    # Google Gemini (optional).
    # Scoring and detailed analysis have different latency/quality profiles,
    # so they intentionally use separate production-stable models.
    gemini_api_key: str = ""
    gemini_scoring_model: str = "gemini-3.5-flash-lite"
    gemini_analysis_model: str = "gemini-3.6-flash"
    gemini_scoring_max_output_tokens: int = 12000
    gemini_analysis_max_output_tokens: int = 4096
    gemini_batch_delay: float = 4.0

    # NVIDIA Build (optional fallback when the Gemini circuit breaker is open).
    # Get key at https://build.nvidia.com → set NVIDIA_API_KEY in .env to enable.
    # The periodic idle rescorer is disabled by default: NVIDIA Build can be
    # unstable under bulk load, while fallback scoring remains available.
    nvidia_idle_rescore_enabled: bool = False
    nvidia_api_key: str = ""
    # google/gemma-4-31b-it was decommissioned from NVIDIA Build (404 / hangs).
    # llama-3.3-70b-instruct is live, free, non-reasoning, fast (~30s/8 jobs) and
    # returns clean JSON. (Avoid nemotron reasoning models here: they blow past
    # the 120s timeout on the strict multi-job scoring prompt.)
    nvidia_model: str = "meta/llama-3.3-70b-instruct"
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

    # Semantic priority for backfill scoring. Legacy env names are retained,
    # but similarity now orders the AI queue only: it never writes score=0 or
    # permanently rejects a vacancy. Exact target roles always go first.
    semantic_skip_enabled: bool = True
    semantic_skip_threshold: float = 0.5     # 1.0 = identical, 0.0 = orthogonal, <0 = opposite

    # Source toggles (comma-separated, env-overridable).
    # disabled_sources — JobAggregator skips any source whose source_name is
    #   listed here. ``arbeitsagentur`` off by default: returns only
    #   German-language listings, which don't fit the English-first audience.
    # jobspy_sites — which JobSpy sub-sites to scrape. ``linkedin`` dropped by
    #   default because it ignores the country filter and floods US jobs.
    #   Re-enable with JOBSPY_SITES="indeed,linkedin".
    disabled_sources: str = "arbeitsagentur"
    jobspy_sites: str = "indeed"

    # Search / semantic indexing
    embedding_enabled: bool = True
    embedding_model: str = "models/gemini-embedding-001"
    embedding_dimension: int = 768
    embedding_batch_delay: float = 0.8       # Gemini Embedding free tier is RPM-bound
    embedding_jobs_per_run: int = 70         # ~840/day when run every 2h, under 1K RPD
    embedding_profiles_per_run: int = 20
    embedding_index_interval_hours: int = 2
    semantic_search_limit: int = 500

    # Database
    database_url: str = "sqlite+aiosqlite:///./pipka.db"

    # Arbeitsagentur
    arbeitsagentur_api_key: str = "jobboerse-jobsuche"

    # Jooble meta-aggregator (covers Stepstone, Monster, regional boards)
    jooble_api_key: str = ""

    # Gupy official job-board partner feed. Both stay empty until Gupy approves
    # the integration and issues a feed URL/token; no candidate-portal scraping.
    gupy_feed_url: str = ""
    gupy_feed_token: str = ""

    # Scoring
    max_jobs_per_scoring_batch: int = 15
    max_scored_per_search: int = 30
    score_cache_hours: int = 168  # 7 days
    claude_timeout_seconds: float = 60.0
    claude_max_retries: int = 2

    # Claude model/token knobs (overridable via .env without redeploy)
    claude_model: str = "claude-sonnet-4-20250514"
    claude_scoring_max_tokens: int = 8000     # batch scoring response budget (sized for batch=15)
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
