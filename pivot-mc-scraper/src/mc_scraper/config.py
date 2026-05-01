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

    pivot_pg_dsn: str = "postgresql://pivot_user:pivot_password@localhost:5432/postgres"
    financials_db_name: str = "financials"

    user_agent_pool: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Safari/605.1.15|"
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
    rate_limit: float = 10.0
    http_timeout: float = 20.0

    respect_market_hours: bool = True

    @property
    def user_agents(self) -> list[str]:
        return [ua.strip() for ua in self.user_agent_pool.split("|") if ua.strip()]

    def financials_dsn(self) -> str:
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(self.pivot_pg_dsn)
        new_path = f"/{self.financials_db_name}"
        return urlunparse(parsed._replace(path=new_path))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
