import os

# Settings are validated at import time; provide harmless defaults for tests.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DATABASE_URL", "sqlite://")
