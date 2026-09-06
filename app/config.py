from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str | None = None
    database_url: str
    openai_api_key: str | None = None
    brave_search_api_key: str | None = None
    openai_discovery_model: str = "gpt-5.6-luna"
    app_env: str = "development"
    log_level: str = "INFO"
    default_city: str = "Copenhagen"
    default_timezone: str = "Europe/Copenhagen"
    scrape_interval_hours: int = 6
    ai_discovery_interval_days: int = 7
    public_base_url: str = "https://event-assistant-calendar.onrender.com"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def sqlalchemy_database_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg://", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url


settings = Settings()
