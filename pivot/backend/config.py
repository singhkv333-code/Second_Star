from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str

    # Redis
    redis_url: str

    # JWT
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    # Default 12 hours for demo / development convenience. Production
    # should override via ACCESS_TOKEN_EXPIRE_MINUTES in env to 15-30.
    access_token_expire_minutes: int = 720
    refresh_token_expire_days: int = 7

    # Kite
    kite_api_key: str = ""
    kite_api_secret: str = ""

    # AI
    sarvam_api_key: str = ""
    openai_api_key: str = ""

    # App
    app_env: str = "development"
    app_version: str = "0.1.0"
    allowed_origins: str = "http://localhost:3000,http://localhost:5173"
    frontend_url: str = "http://localhost:5173"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",")]

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
