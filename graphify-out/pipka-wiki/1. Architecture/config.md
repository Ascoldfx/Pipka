# Config Domain

Centralizes environment variables and logic variables via Pydantic.
Dependencies: None

## Pydantic Settings
Path: `app/config.py`
```python
class Settings(BaseSettings):
    telegram_token: str
    admin_telegram_ids: list[int] = []
    database_url: str = "sqlite+aiosqlite:///example.db"
    telegram_secret_token: str = ""
    webhook_url: str = ""
    anthropic_api_key: str = ""
```

## Environment File
Path: `.env`
Provides configuration defaults and secrets (Not checked into VCS!).

Used by [[db.md]] for the generic `database_url`, and by [[services.md]] and [[bot.md]] for API keys.
