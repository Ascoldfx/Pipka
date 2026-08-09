from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str

    # Adzuna
    adzuna_app_id: str
    adzuna_app_key: str

    # Google Gemini (optional).
    # Gemini 3.6 Flash is reserved for batch scoring. Its free-tier RPD is
    # small, so detailed on-demand analysis is disabled by default rather than
    # competing with fresh vacancy scoring.
    gemini_api_key: str = ""
    gemini_scoring_model: str = "gemini-3.6-flash"
    gemini_analysis_model: str = "gemini-3.6-flash"
    gemini_scoring_max_output_tokens: int = 12000
    gemini_analysis_max_output_tokens: int = 4096
    gemini_batch_delay: float = 4.0
    gemini_daily_request_limit: int = 20
    gemini_detailed_analysis_enabled: bool = False

    # NVIDIA Build is the automatic fallback when Gemini is unavailable. The
    # optional idle rescorer is a separate, lower-priority maintenance job.
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

    # Search / semantic indexing. Nemotron is deliberately separate from the
    # Gemini scorer quota. Its 2048-dimensional vectors need migration 0010.
    embedding_enabled: bool = True
    embedding_provider: str = "nvidia"
    embedding_model: str = "nvidia/nemotron-3-embed-1b"
    embedding_dimension: int = 2048
    embedding_batch_delay: float = 0.8
    embedding_jobs_per_run: int = 70
    embedding_profiles_per_run: int = 20
    embedding_index_interval_hours: int = 2
    # Drain a material NVIDIA backlog faster, without polling an almost-empty
    # queue or using the Gemini embedding quota.
    embedding_burst_interval_minutes: int = 30
    embedding_burst_queue_threshold: int = 100
    # Do not spend embedding quota on the historical archive. Semantic search
    # serves the active German feed, so only recent, open, already-promising
    # vacancies are indexed.
    embedding_index_max_age_days: int = 31
    embedding_index_country: str = "de"
    embedding_index_min_score: int = 60
    semantic_search_limit: int = 500
    nvidia_embedding_timeout_seconds: float = 30.0

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
    # A profile edit must never resurrect the whole historical archive. The
    # re-score queue is restricted to previously strong matches in the active
    # target market. New jobs are scored by the real-time scan separately.
    backfill_max_age_days: int = 31
    backfill_ai_jobs_per_run: int = 30
    backfill_country: str = "de"
    backfill_min_previous_score: int = 60
    score_cache_hours: int = 168  # 7 days

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

    # Registration policy (public Google/Telegram registration enabled).
    allow_public_registration: bool = True
    allowed_user_emails: str = ""
    allowed_telegram_ids: str = ""

    # Per-user Telegram budgets. Telegram updates do not pass through the
    # FastAPI rate-limit middleware, so expensive bot actions need their own
    # caps to protect scraper and AI-provider quotas.
    telegram_search_limit_per_hour: int = 6
    telegram_analysis_limit_per_hour: int = 10

    # Billing & Monetization (Cryptomus / NOWPayments)
    cryptomus_merchant_id: str = ""
    cryptomus_payment_key: str = ""
    billing_starter_price_usd: float = 5.0
    billing_starter_credits: int = 200
    billing_pro_price_usd: float = 10.0
    billing_pro_credits: int = 1000
    default_user_trial_credits: int = 50
    # Development-only switch. It enables the sandbox checkout URL and test
    # fulfilment endpoint; production must leave this false.
    billing_test_mode: bool = False

    # Search
    default_results_limit: int = 50
    job_max_age_days: int = 45

    # Logging
    log_level: str = "INFO"
    api_docs_enabled: bool = False

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
