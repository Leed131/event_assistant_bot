from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    telegram_bot_token: str
    database_url: str
    app_env: str = "development"
    log_level: str = "INFO"
    default_city: str = "Copenhagen"
    default_timezone: str = "Europe/Copenhagen"
    scrape_interval_hours: int = 6

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
