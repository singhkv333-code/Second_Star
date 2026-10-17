from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    financials_dsn: str = "postgresql://pivot_user:pivot_password@localhost:5432/financials"
    pivot_pg_dsn: str = "postgresql://pivot_user:pivot_password@localhost:5432/postgres"
    mc_scraper_path: str = "../pivot-mc-scraper"
    risk_free_rate: float = 0.065


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
