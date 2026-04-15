from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Telegram
    telegram_bot_token: str

    # Adzuna
    adzuna_app_id: str
    adzuna_app_key: str

    # Claude AI
    anthropic_api_key: str

    # Database
    database_url: str = "sqlite+aiosqlite:///./jobhunt.db"

    # Arbeitsagentur
    arbeitsagentur_api_key: str = "jobboerse-jobsuche"

    # Scoring
    max_jobs_per_scoring_batch: int = 8
    max_scored_per_search: int = 30
    score_cache_hours: int = 168  # 7 days

    # Dashboard Authentication
    dashboard_username: str = "ascoldfx"
    dashboard_password: str = "REDACTED"
    guest_username: str = "guest"
    guest_password: str = ""

    # Search
    default_results_limit: int = 50
    job_max_age_days: int = 60

    # Logging
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
