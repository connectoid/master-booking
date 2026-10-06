from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    database_url: str = "postgresql+asyncpg://booking:booking@localhost:5432/booking"
    max_bot_token: str = ""
    telegram_bot_token: str = ""
    public_base_url: str = "http://localhost:8000"
    initdata_max_age_hours: int = 24

    @property
    def sync_database_url(self) -> str:
        """URL для Alembic (синхронный драйвер psycopg)."""
        return self.database_url.replace("+asyncpg", "+psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()
