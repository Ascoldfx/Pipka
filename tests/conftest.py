import os


# Settings are instantiated at import time. Tests never call external services,
# but the production settings model intentionally requires these values.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test")
os.environ.setdefault("ADZUNA_APP_ID", "test")
os.environ.setdefault("ADZUNA_APP_KEY", "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("SESSION_SECRET", "test")
