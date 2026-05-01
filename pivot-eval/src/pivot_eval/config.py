from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = PROJECT_ROOT / "runs"
DATASET_DEFAULT = PROJECT_ROOT.parent / "Readme.md"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    pivot_base_url: str = "http://127.0.0.1:8000"
    pivot_bearer_token: str = ""
    pivot_login_email: str = "smoke@example.com"
    pivot_login_password: str = "smokepass1"
    sarvam_api_key: str = ""
    pivot_eval_concurrency: int = 4


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
