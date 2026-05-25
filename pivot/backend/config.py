from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str
    # Read-only DSN for the Moneycontrol-derived fundamentals DB
    # (mc.companies, mc.statement_lines, mc.daily_prices). Maintained by
    # pivot-mc-scraper. Backend only reads — never writes.
    financials_dsn: str = "postgresql://pivot_user:pivot_password@localhost:5432/financials"

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
    # Phase 0: token encryption at rest. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    kite_token_enc_key: str = ""

    # AI
    sarvam_api_key: str = ""
    openai_api_key: str = ""

    # Azure AI Foundry (chat base URL is the /openai/v1 path on the
    # services.ai.azure.com host — Foundry, not the empty *.openai.azure.com
    # resource). LLM_PROVIDER=azure selects this client.
    azure_openai_endpoint: str = ""
    azure_openai_legacy_endpoint: str = ""
    azure_project_endpoint: str = ""
    azure_key: str = ""

    # News (used by backend/triggers/* for news-driven event triggers).
    # Free-tier NewsAPI.org account — see news_client.py for fetch logic.
    # Leave blank to disable real polling (fetch_news returns [] + warns).
    newsapi_key: str = ""

    # LLM provider selection (read by backend.llm.factory).
    # `llm_provider`: "openai" | "sarvam" | "azure".
    # `llm_model`:    overrides the per-provider default (gpt-5-mini /
    #                 sarvam-m / gpt-5.4-mini). For azure this is the
    #                 *deployment name* from the Azure portal, not the
    #                 underlying model id.
    llm_provider: str = "openai"
    llm_model: str = ""

    # App
    app_env: str = "development"
    app_version: str = "0.1.0"
    # Phase 0: error reporting. Leave dsn blank to disable.
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.0
    allowed_origins: str = "http://localhost:3000,http://localhost:5173"
    frontend_url: str = "http://localhost:5173"

    # --- Observability ----------------------------------------------------------
    log_format: str = "console"   # "json" | "console"
    log_level: str = "INFO"

    # --- News & Event Trigger subsystem -----------------------------------------
    # Master flag for backend/news_events/. With it FALSE (the default),
    # the router is not included, no APScheduler jobs are registered, and
    # the integration seam is a no-op. The 0007 migration still runs so
    # the tables exist, but they stay empty.
    news_events_enabled: bool = False
    # Identifying User-Agent for all outbound source fetches. Some Indian
    # publisher feeds 403 a generic Python UA; this string is sent on
    # every request and is also what we surface in robots.txt requests.
    news_events_user_agent: str = (
        "PivotNewsBot/0.1 (+https://pivot.app/news-bot; "
        "automation for retail-investor event triggers)"
    )

    # --- Phase 7 Tier-A: Telegram MTProto channel reader -----------------------
    # Sub-flag: TELEGRAM_ENABLED gates the long-running Telethon
    # client. Master news_events flag must also be on. Both default
    # off so dev and tests don't try to connect.
    telegram_enabled: bool = False
    # Get these from https://my.telegram.org → API development tools.
    telegram_api_id: int = 0
    telegram_api_hash: str = ""
    # Path to the Telethon ``.session`` file. Created by the
    # one-time auth CLI (``scripts/auth_telegram.py``); reused on
    # every subsequent boot so no SMS step is needed.
    telegram_session_path: str = "/var/lib/pivot/telegram.session"

    # --- Phase 7 Tier-B: Miniflux webhook receiver ----------------------------
    # Shared HMAC secret. Configure the SAME value inside Miniflux's
    # ``WEBHOOK_SECRET`` env. Empty string disables the endpoint
    # entirely (POSTs return 401).
    miniflux_webhook_secret: str = ""

    # --- Polymarket WS prediction-market trigger -------------------------------
    # Sub-flag: opens a persistent CLOB market-data WS connection and
    # drives fire decisions for any active NewsEventSpec whose
    # resolution_criteria carry a polymarket_token_id. Master
    # news_events flag must also be on. Default off so dev and tests
    # don't open the connection.
    polymarket_ws_enabled: bool = False
    # How often the supervisor scans the DB to reconcile its in-memory
    # registration set against active specs. 30s is brisk enough that
    # newly-created specs go live within one tick, slow enough that the
    # query is negligible.
    polymarket_ws_reconcile_interval_s: int = 30

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
