import os
from pydantic_settings import BaseSettings
from functools import lru_cache

# Absolute path to pivot/.env (this file is pivot/backend/config.py), so the
# server loads its creds regardless of the working directory it's launched
# from. A relative ".env" silently fell back to mock mode when uvicorn was
# started outside pivot/ — Kite data creds then never loaded.
_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")


class Settings(BaseSettings):
    # Database
    database_url: str
    # Read-only DSN for the Moneycontrol-derived fundamentals DB
    # (mc.companies, mc.statement_lines, mc.daily_prices). Maintained by
    # pivot-mc-scraper. Backend only reads — never writes.
    financials_dsn: str = "postgresql://pivot_user:pivot_password@localhost:5432/financials"
    # Read-only DSN for the yfinance-enriched company profiles DB
    # (enrich.company_profile / enrich.v_company_enriched: profile, sector,
    # promoter-holding proxy, ticker). Separate DB `pivot_enrich`, built by
    # scripts/enrich_company_profiles.py. Backend only reads. Join to
    # mc.companies by sc_id or ticker. Empty string disables the read path.
    enrich_dsn: str = ""

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
    # Used for ALL broker secrets at rest (kite/dhan/fyers access+refresh+api
    # secret+totp). Named kite_* for env back-compat; broker_token_enc_key is an
    # accepted alias below.
    kite_token_enc_key: str = ""

    # --- Multi-broker onboarding (brokers/) -----------------------------------
    # Dhan is the "clean unattended" broker: a 12-month app api key+secret lets
    # the backend silently mint a fresh daily access token (with the user's TOTP)
    # — no daily human re-login. Leave blank to keep the Dhan connector in mock
    # mode. partner_* are for the Dhan OAuth "Login with Dhan" partner flow.
    dhan_api_key: str = ""
    dhan_api_secret: str = ""
    dhan_partner_id: str = ""
    dhan_partner_secret: str = ""
    # Fyers is the OAuth + 15-day refresh-token broker: a one-time hosted login
    # mints an access + refresh token, and the refresh token silently re-mints
    # the daily access token for ~2 weeks (no daily re-login). These are the
    # app-level credentials from the Fyers API dashboard (myapi.fyers.in). The
    # appIdHash sent to the auth endpoints is sha256(f"{fyers_app_id}:{fyers_secret_id}").
    # Leave blank to keep the Fyers connector in mock mode.
    fyers_app_id: str = ""
    fyers_secret_id: str = ""
    # Alias accepted from env; falls back to kite_token_enc_key when unset.
    broker_token_enc_key: str = ""

    # Server-side auto-execution master flag. When False, workflow-triggered
    # orders are PREPARED (register-not-execute) but not fired by the server.
    # Going live for OTHER users requires NSE/BSE algo-provider empanelment;
    # the account owner's own account is a legitimate self-developed algo and
    # is gated by broker_auto_exec_user_ids (comma-separated user ids).
    auto_execute_enabled: bool = False
    broker_auto_exec_user_ids: str = ""

    # AI
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
    # `llm_provider`: "openai" | "azure" (default).
    # `llm_model`:    overrides the per-provider default (gpt-5-mini /
    #                 gpt-5.4-mini). For azure this is the *deployment
    #                 name* from the Azure portal, not the underlying
    #                 model id.
    llm_provider: str = "azure"
    llm_model: str = ""

    # App
    app_env: str = "development"
    app_version: str = "0.1.0"
    # SECURITY: when true, unauthenticated /chat requests fall back to the
    # default dev user (id 1). MUST stay false for beta/production — it
    # disables auth on the chat surface. Opt in only for local dev.
    dev_auth_bypass: bool = False
    # Deferred-send email: when false, send_email() logs the link instead of
    # sending. Flip to true once an email provider is configured.
    email_enabled: bool = False
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

    # --- Kalshi prediction-market trigger (trigger.kalshi) --------------------
    # Sub-flag: boots a REST poll worker (asyncio task, NOT an APScheduler
    # job) that drives the SAME venue-agnostic prediction-market evaluator
    # the Polymarket path uses, firing active trigger.kalshi workflow steps
    # via fire_external_event. Kalshi public market-data reads need no auth;
    # the WS channel needs RSA-signed auth, so REST polling is the beta path.
    # Master news_events flag must also be on. Default off.
    kalshi_rest_enabled: bool = False
    # How often the worker reconciles registrations + polls watched market
    # prices. Kalshi unauth reads are generous (~20 req/s) and we batch by
    # ticker, so 30s is brisk and well under any rate cap.
    kalshi_rest_reconcile_interval_s: int = 30
    # Public market-data base URL. The `.elections.` host is current
    # canonical; api.kalshi.com is an alias.
    kalshi_api_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"

    # --- Scheduled macro-event triggers (trigger.scheduled_macro) -------------
    # Gates registration of the macro watcher poll loop
    # (_poll_scheduled_macro_triggers). Independent of news_events_enabled:
    # the verifier only needs the RSS adapter + (optionally) the
    # prediction-market client, both of which import fine with the master
    # news flag off. Default off so dev/tests don't arm the loop.
    macro_events_enabled: bool = False
    # Minimum verifier confidence to fire (overridable per-step via the
    # trigger config's own min_confidence; this is the global floor).
    macro_verifier_min_confidence: float = 0.85

    # --- Global-price triggers (trigger.global_price) -------------------------
    # Master flag for the trigger.global_price watcher poll loop
    # (_poll_global_price_triggers in backend/workflows/scheduler.py). This
    # trigger fires when a CRYPTO / FOREX / global USD-denominated COMMODITY
    # price crosses a level — assets that Kite does NOT serve. INR-denominated
    # NSE/MCX symbols (e.g. CRUDEOIL/GOLD/SILVER in INR) are still reachable
    # through the existing trigger.price -> Kite path; this is the non-Kite
    # complement. Default off so dev/tests don't arm the loop.
    global_price_triggers_enabled: bool = False
    # Twelve Data API key — primary forex/commodity provider. Leave blank to
    # skip Twelve Data and use the free fallbacks (Frankfurter ECB for forex,
    # yfinance futures for commodity). Crypto needs no key (Kraken public +
    # CoinGecko public).
    twelvedata_api_key: str = ""
    # Provider base URLs (overridable for staging/test mirrors). None of these
    # require auth except Twelve Data (which uses twelvedata_api_key above).
    kraken_api_base_url: str = "https://api.kraken.com/0/public"
    coingecko_api_base_url: str = "https://api.coingecko.com/api/v3"
    twelvedata_api_base_url: str = "https://api.twelvedata.com"
    # api.frankfurter.app now 301-redirects to a non-JSON page; the live
    # host is api.frankfurter.dev/v1 (accepts the same from/to params).
    frankfurter_api_base_url: str = "https://api.frankfurter.dev/v1"
    # When True, backend.market.global_quotes.get_global_quote() returns a
    # deterministic synthetic price derived from a stable hash of the symbol
    # (no randomness, no wall-clock-dependent value) so dev + tests are
    # reproducible without network. Honoured alongside the GLOBAL_QUOTES_MOCK
    # env var.
    global_quotes_mock: bool = False
    # Poll interval (seconds) for _poll_global_price_triggers. Crypto markets
    # are 24/7 so the loop is NOT gated on NSE market hours. 60s is brisk
    # enough for retail alerts and well under any free-tier rate cap.
    global_price_poll_seconds: int = 60

    # --- Earnings-event triggers (trigger.earnings) ---------------------------
    # Master flag for the trigger.earnings watcher poll loop
    # (_poll_earnings_triggers in backend/workflows/scheduler.py). The trigger
    # fires after a company's results are announced when reported EPS beats /
    # misses / meets the consensus estimate. Source: yfinance earnings dates
    # (Redis-cached ~12h). Default off so dev/tests don't arm the loop.
    earnings_events_enabled: bool = False
    # Minimum verifier confidence to fire (overridable per-step via the
    # trigger config's own min_confidence; this is the global floor). Earnings
    # numbers are REPORTED data (not an LLM guess) so confidence is ~1.0 when
    # both reported + estimate are present — this floor mainly guards against
    # half-populated rows.
    earnings_verifier_min_confidence: float = 0.85

    # --- Company logos (logo.dev) ---------------------------------------------
    # Publishable token (pk_…) for img.logo.dev — safe to expose in the
    # frontend, and the same token mc.companies.logo_url already embeds.
    # Free tier REQUIRES the "Logos provided by Logo.dev" attribution link
    # on any page that displays logos (rendered in the FE footer). Swap for
    # a paid token (no attribution) or empty to disable derived logos.
    logodev_publishable_token: str = "pk_X3WtLGU0RTuTq-o9GTLEsg"

    # --- Paper trading (simulated broker) -------------------------------------
    # When True, orders from chat (/orders/confirm, /orders/gtt) and from
    # workflow action.* steps route through the PaperBroker (backend/paper/)
    # for any account in mode='paper' (the default), filling against live
    # prices and accruing a structured portfolio. When False, orders take the
    # legacy Kite path (mock in dev). The per-account `mode` column is the
    # finer switch: mode='live' always uses Kite even with this flag on.
    paper_trading_enabled: bool = True

    # --- View Markets (V2: belief -> expression -> deployment) -----------------
    # Master flag for the View Markets layer (backend/view_markets/, the /api/
    # views router, the FE "Views" tab). With it FALSE (the default), the router
    # is not mounted, no curated-view generation/lifecycle jobs are registered,
    # and the chat View-Markets tool subset stays inert — the 0023 migration may
    # still run so the tables exist, but they stay empty. V1 is CURATED-ONLY
    # (backend-generated + human-reviewed views; no user-authored beliefs) and
    # register-not-execute; we READ Polymarket/Kalshi for "what's priced in" and
    # never become a prediction exchange. Flip on for internal -> beta -> GA.
    view_markets_enabled: bool = True  # V2 beta: Views tab live

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",")]

    @property
    def token_enc_key(self) -> str:
        """The Fernet key for at-rest broker secrets. Prefers the broker_*
        alias, falls back to the legacy kite_* env name."""
        return (self.broker_token_enc_key or self.kite_token_enc_key or "").strip()

    @property
    def auto_exec_user_ids(self) -> set[int]:
        """User ids allowed to run server-side auto-execution (own-account
        pilot) before NSE/BSE empanelment broadens it."""
        out: set[int] = set()
        for part in (self.broker_auto_exec_user_ids or "").split(","):
            part = part.strip()
            if part.isdigit():
                out.add(int(part))
        return out

    class Config:
        env_file = _ENV_FILE
        case_sensitive = False
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
