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
    # Comma-separated pivot user ids allowed on /admin* surfaces (ticker
    # start/stop, chat-trace inspection, event simulation, F&O refresh).
    # FAIL-CLOSED: empty ⇒ nobody is admin, every admin endpoint 403s. Set
    # ADMIN_USER_IDS in .env (e.g. "2") — never default anyone in.
    admin_user_ids: str = ""
    # Optional READ-REPLICA DSN for the financials DB. When set, the app's
    # entire read path (FinancialsSessionLocal — the app never writes to
    # mc.*) binds here instead of `financials_dsn`, so app traffic can move
    # to an Azure read replica while the primary stays reserved for the
    # scraper/dev writes. Provision the replica in Azure (Flexible Server →
    # Replication → Add replica), then set FINANCIALS_READ_DSN in .env —
    # no code change needed. Empty string = use the primary DSN.
    financials_read_dsn: str = ""
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

    # --- Unattended Kite auto-login (opt-in; default OFF) ----------------------
    # When ON, the daily 07:30 IST refresh mints a fresh Kite token from THESE
    # env creds (the current, post-reset creds) instead of the encrypted DB
    # session — so no human logs in each morning. Guarded by kite/auth.py's
    # circuit-breaker + clock-skew: a bad attempt fails at most twice and can
    # never lock the account. OFF by default, so it runs ONLY where you set
    # KITE_UNATTENDED_AUTOLOGIN=1 (never during local dev edits). Requires
    # KITE_USER_ID, KITE_PASSWORD and PERMANENT_TOKEN (the base32 TOTP seed).
    kite_unattended_autologin: bool = False
    kite_user_id: str = ""       # Zerodha user id, e.g. "AB1234"
    kite_password: str = ""      # KITE_PASSWORD
    permanent_token: str = ""    # PERMANENT_TOKEN — the Kite TOTP base32 seed

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
    # MASTER register-not-execute kill-switch for the USER-CONFIRMED path
    # (chat /orders, View /place). While False (default), these never send a
    # real order to the broker — they REGISTER it and the user confirms in
    # their own broker app. Flip to True (LIVE_EXECUTION_ENABLED=true) only
    # when live execution is deliberately enabled. Independent of
    # auto_execute_enabled, which gates the unattended automation path.
    live_execution_enabled: bool = False

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
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173"
    frontend_url: str = "http://localhost:5173"

    # --- Google Sign-In ---------------------------------------------------------
    # OAuth 2.0 **Web** client id from Google Cloud Console. The SAME value is
    # exposed to the browser as NEXT_PUBLIC_GOOGLE_CLIENT_ID (it's not a secret
    # — the audience check below is what makes it safe). Empty ⇒ the
    # `/auth/google` endpoint 503s and the FE button falls back to a
    # "coming soon" toast, so nothing breaks until it's configured.
    google_client_id: str = ""

    # --- PostHog analytics ------------------------------------------------------
    posthog_project_token: str = ""
    posthog_host: str = "https://us.i.posthog.com"

    # --- Observability ----------------------------------------------------------
    log_format: str = "console"   # "json" | "console"
    log_level: str = "INFO"

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
    # Alpaca — US-equity/ETF market DATA only (register-not-execute: we never
    # place live US orders; US positions fill into the simulated paper book).
    # Paper keys are fine for the data API. Base is the DATA host, not trading.
    alpaca_api_key: str = ""
    alpaca_api_secret: str = ""
    alpaca_data_base_url: str = "https://data.alpaca.markets/v2"
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

    # --- Web search (provider-hosted) -----------------------------------------
    # When on, the main chat hop offers the LLM the Responses-API HOSTED
    # `web_search` tool. The provider runs the search server-side and returns
    # the answer with url citations in one call — no retrieval code our side.
    # Verified 2026-07-12 against deploymentpivot111/gpt-5.4-mini (all tool-type
    # variants 200 + real citations). Default OFF (feature-flag convention);
    # scoped/guided by system_core.md's web-search clause — prices/fundamentals
    # still come from Kite tools, web search is for LATEST qualitative context.
    # Reactivated 2026-07-19 with a SCOPED surface: the tool is attached
    # per-turn only for news / qualitative-company / earnings-date asks
    # (chat_service._web_search_scope) and runs with
    # search_context_size="low" for latency.
    web_search_enabled: bool = True

    # --- LLM-owned interpretation (A/B experiment, 2026-07-17) -----------------
    # When True, chat_service skips the regex "interpretation" layers — intent-
    # based tool-surface surgery, reply-class budget pinning, GAN guard
    # scope-forcing, thematic scenario routing — and instead injects prose
    # directions so the model interprets the ask itself. SAFETY and
    # CORRECTNESS gates (alert boundary, no-trade markers, schema validation,
    # post-LLM verification retries) are NOT affected by this flag.
    # Default OFF: flipping it back restores the deterministic behavior.
    llm_owned_interpretation: bool = False

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

    # When True (default), paper fills respect NSE market hours: a MARKET order
    # placed while the market is CLOSED rests ("queued for open") and fills at
    # the next market-hours evaluator tick against the then-live price, instead
    # of filling immediately at a stale close. Resting LIMIT/SL/GTT + queued
    # MARKET orders are only filled by the scheduler tick during 09:15-15:30
    # IST, Mon-Fri (is_market_open()). This is the CORRECT default (owner call
    # 2026-07-10 eve): paper mode must simulate the real world exactly — an
    # order placed after hours does NOT fill until the market reopens. (An
    # earlier same-day flip to immediate off-hours fills was reversed: the
    # user wants true market-timing simulation, not instant fills.)
    # NOTE: is_market_open() covers weekends + hours but NOT NSE holidays yet
    # (needs a trading_holidays calendar — tracked follow-up).
    paper_respect_market_hours: bool = True

    # Starting cash for a NEW paper account (env PAPER_SEED_CAPITAL). Raised to
    # 500000 for the beta test via .env; defaults to 150000 (matches the
    # money.SEED_CAPITAL fallback + the test suite's expectations).
    paper_seed_capital: float = 150000.0

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
