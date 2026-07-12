"""Per-security display metadata for multi-asset strategy cards.

A strategy's constituents can be Indian equities/ETFs, US equities/ETFs, or
crypto. The card must show a real NAME + LOGO + asset class for each — the
opinion cards previously rendered bare tickers ("MSTR", "RIOT", "ETH-USD").

`resolve_security_meta(symbol)` returns:
    {symbol, base, name, logo_url, asset_class, currency}

asset_class ∈ {in_equity, in_etf, us_equity, us_etf, crypto}.
currency    ∈ {INR, USD}.

Sources:
  - Indian: mc.companies (name) + company_logos/logo.dev (logo) — mature path.
  - US:     curated ticker→{name, domain} map → logo.dev by domain.
  - Crypto: curated ticker→{name, coingecko_id} map → CoinCap icon CDN.

Everything is best-effort: a missing logo_url is fine (the FE renders a
monogram), and an unknown symbol degrades to {asset_class inferred, name=base}.
Batch via `resolve_many` to avoid per-symbol DB/Redis round-trips.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Crypto: ticker → (display name, coingecko id) ───────────────────────────
# Superset of market.global_quotes._CRYPTO_MAP with display names. Logos come
# from the CoinCap icon CDN (stable, keyed by lowercase ticker).
_CRYPTO_META: dict[str, tuple[str, str]] = {
    "BTC": ("Bitcoin", "bitcoin"),
    "XBT": ("Bitcoin", "bitcoin"),
    "ETH": ("Ethereum", "ethereum"),
    "SOL": ("Solana", "solana"),
    "ADA": ("Cardano", "cardano"),
    "XRP": ("XRP", "ripple"),
    "DOGE": ("Dogecoin", "dogecoin"),
    "MATIC": ("Polygon", "matic-network"),
    "DOT": ("Polkadot", "polkadot"),
    "LTC": ("Litecoin", "litecoin"),
    "AVAX": ("Avalanche", "avalanche-2"),
    "LINK": ("Chainlink", "chainlink"),
    "BCH": ("Bitcoin Cash", "bitcoin-cash"),
    "ATOM": ("Cosmos", "cosmos"),
    "BNB": ("BNB", "binancecoin"),
    "TRX": ("TRON", "tron"),
    "SHIB": ("Shiba Inu", "shiba-inu"),
    "UNI": ("Uniswap", "uniswap"),
    "NEAR": ("NEAR Protocol", "near"),
    "APT": ("Aptos", "aptos"),
    "ARB": ("Arbitrum", "arbitrum"),
    "OP": ("Optimism", "optimism"),
}


# ── US: ticker → (display name, logo domain) ────────────────────────────────
# Curated for the names that show up in opinion/view strategies (crypto-proxy
# equities, big tech, popular US ETFs). Extend freely — a miss just falls back
# to the bare ticker + monogram. Domains feed logo.dev (same as the Indian path).
_US_META: dict[str, tuple[str, str]] = {
    # crypto-adjacent equities (the RIOT/MSTR class in the screenshot)
    "MSTR": ("MicroStrategy", "microstrategy.com"),
    "RIOT": ("Riot Platforms", "riotplatforms.com"),
    "MARA": ("Marathon Digital", "mara.com"),
    "COIN": ("Coinbase", "coinbase.com"),
    "CLSK": ("CleanSpark", "cleanspark.com"),
    "HUT": ("Hut 8", "hut8.com"),
    "BITF": ("Bitfarms", "bitfarms.com"),
    "WULF": ("TeraWulf", "terawulf.com"),
    "CIFR": ("Cipher Mining", "ciphermining.com"),
    # big tech / mega caps
    "AAPL": ("Apple", "apple.com"),
    "MSFT": ("Microsoft", "microsoft.com"),
    "NVDA": ("NVIDIA", "nvidia.com"),
    "GOOGL": ("Alphabet", "abc.xyz"),
    "GOOG": ("Alphabet", "abc.xyz"),
    "AMZN": ("Amazon", "amazon.com"),
    "META": ("Meta Platforms", "meta.com"),
    "TSLA": ("Tesla", "tesla.com"),
    "AMD": ("AMD", "amd.com"),
    "NFLX": ("Netflix", "netflix.com"),
    "AVGO": ("Broadcom", "broadcom.com"),
    "PLTR": ("Palantir", "palantir.com"),
    "SMCI": ("Super Micro", "supermicro.com"),
    "INTC": ("Intel", "intel.com"),
    "MU": ("Micron", "micron.com"),
    "CRM": ("Salesforce", "salesforce.com"),
    "ORCL": ("Oracle", "oracle.com"),
    "UBER": ("Uber", "uber.com"),
    "JPM": ("JPMorgan", "jpmorganchase.com"),
    "V": ("Visa", "visa.com"),
    "MA": ("Mastercard", "mastercard.com"),
    "DIS": ("Disney", "disney.com"),
    "BA": ("Boeing", "boeing.com"),
    "XOM": ("ExxonMobil", "exxonmobil.com"),
    "WMT": ("Walmart", "walmart.com"),
    # popular US ETFs
    "SPY": ("S&P 500 ETF", "ssga.com"),
    "QQQ": ("Nasdaq-100 ETF", "invesco.com"),
    "VOO": ("Vanguard S&P 500", "vanguard.com"),
    "IWM": ("Russell 2000 ETF", "ishares.com"),
    "GLD": ("Gold ETF", "spdrgoldshares.com"),
    "ARKK": ("ARK Innovation", "ark-funds.com"),
    "IBIT": ("iShares Bitcoin", "ishares.com"),
}
_US_ETF_TICKERS = {"SPY", "QQQ", "VOO", "IWM", "GLD", "ARKK", "IBIT"}

# CoinCap icon CDN — stable, guessable by lowercase ticker, no API call.
_CRYPTO_ICON = "https://assets.coincap.io/assets/icons/{sym}@2x.png"

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _base_symbol(symbol: str) -> str:
    """Strip exchange suffixes and crypto quote suffixes: RELIANCE.NS→RELIANCE,
    ETH-USD→ETH, BTCUSD→BTC (when the base is a known coin)."""
    s = str(symbol or "").upper().strip()
    s = s.split("(")[0].strip()
    for suf in (".NS", ".BO", ".NSE", ".BSE"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    # crypto quote suffixes
    for suf in ("-USD", "-USDT", "/USD", "USD-", ):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    if s.endswith("USDT") and s[:-4] in _CRYPTO_META:
        s = s[:-4]
    elif s.endswith("USD") and s[:-3] in _CRYPTO_META:
        s = s[:-3]
    return s.strip("-/ ")


def _looks_like_crypto(raw: str, base: str) -> bool:
    r = str(raw or "").upper()
    return (
        base in _CRYPTO_META
        or r.endswith("-USD") or r.endswith("-USDT") or r.endswith("/USD")
    )


def classify(symbol: str, *, session: Any = None) -> dict:
    """Return {base, asset_class, currency} for a symbol WITHOUT resolving a
    logo (cheap; used where only class/currency is needed). asset_class ∈
    {in_equity, in_etf, us_equity, us_etf, crypto}."""
    raw = str(symbol or "").upper().strip()
    base = _base_symbol(raw)
    if _looks_like_crypto(raw, base):
        return {"base": base, "asset_class": "crypto", "currency": "USD"}
    # Explicit US map wins (covers ADRs/tickers that also look Indian-ish).
    if base in _US_META:
        cls = "us_etf" if base in _US_ETF_TICKERS else "us_equity"
        return {"base": base, "asset_class": cls, "currency": "USD"}
    # Indian ETF (NIFTYBEES/GOLDBEES/…BEES, …ETF, gold/silver funds) — these
    # are NSE-listed but often absent from mc.companies, so check BEFORE the
    # US fallback or they'd be mislabeled US.
    if _is_indian_etf(base):
        return {"base": base, "asset_class": "in_etf", "currency": "INR"}
    # Indian if it resolves in the moneycontrol DB (or carries an NSE suffix).
    if raw.endswith(".NS") or raw.endswith(".BO") or _is_indian(base, session):
        return {"base": base, "asset_class": "in_equity", "currency": "INR"}
    # Unknown all-caps ticker with no Indian match → treat as US equity
    # (best-effort; logo may be absent → monogram).
    if _TICKER_RE.match(base):
        return {"base": base, "asset_class": "us_equity", "currency": "USD"}
    return {"base": base, "asset_class": "in_equity", "currency": "INR"}


def _is_indian(base: str, session: Any = None) -> bool:
    try:
        from backend.market.financials_db import resolve_symbol
        return resolve_symbol(base, session=session) is not None
    except Exception:  # noqa: BLE001
        return False


# Per-symbol metadata is static (name/logo/class don't change intraday), so we
# memoize it — resolve_security_meta does a DB lookup + logo network probe that
# was ~300ms/call, which added up when sizing a multi-leg basket preview.
_META_CACHE: dict[str, dict] = {}


def resolve_security_meta(symbol: str, *, session: Any = None) -> dict:
    """Full display metadata for one symbol:
    {symbol, base, name, logo_url, asset_class, currency}."""
    raw = str(symbol or "").upper().strip()
    cached = _META_CACHE.get(raw)
    if cached is not None:
        return dict(cached)
    c = classify(raw, session=session)
    base, asset_class, currency = c["base"], c["asset_class"], c["currency"]
    name: Optional[str] = None
    logo_url: Optional[str] = None

    if asset_class == "crypto":
        meta = _CRYPTO_META.get(base)
        name = meta[0] if meta else base
        logo_url = _CRYPTO_ICON.format(sym=base.lower())
    elif asset_class in ("us_equity", "us_etf"):
        meta = _US_META.get(base)
        if meta:
            name = meta[0]
            from backend.market.company_logos import logo_url_for_domain
            logo_url = logo_url_for_domain(meta[1])
        else:
            name = base  # unknown US ticker — bare symbol, monogram logo
    else:  # Indian equity / ETF
        try:
            from backend.market.financials_db import get_company
            comp = get_company(base, session=session)
            if comp is not None:
                name = comp.name or base
        except Exception:  # noqa: BLE001
            pass
        try:
            from backend.market.company_logos import get_logo_url
            logo_url = get_logo_url(base)
        except Exception:  # noqa: BLE001
            logo_url = None
        # Refine ETF vs equity for Indian names.
        if _is_indian_etf(base):
            asset_class = "in_etf"
        name = name or _india_name_fallback(base) or base

    result = {
        "symbol": raw,
        "base": base,
        "name": name or base,
        "logo_url": logo_url,
        "asset_class": asset_class,
        "currency": currency,
    }
    _META_CACHE[raw] = dict(result)
    return result


def _india_name_fallback(base: str) -> Optional[str]:
    try:
        from backend.view_markets.plain_copy import stock_name
        n = stock_name(base)
        return n if n and n != base else None
    except Exception:  # noqa: BLE001
        return None


_IN_ETF_HINT = re.compile(r"BEES$|ETF$|IETF$|GOLD|SILVER|LIQUID", re.IGNORECASE)

# All NSE ETF tickers from the catalog (MON100, BANKBEES, …) — loaded once so
# INR ETFs that don't match the suffix regex (e.g. MON100) still classify as
# Indian, not US.
_IN_ETF_SYMBOLS: set[str] = set()


def _load_in_etf_symbols() -> set[str]:
    global _IN_ETF_SYMBOLS
    if _IN_ETF_SYMBOLS:
        return _IN_ETF_SYMBOLS
    try:
        from backend.view_markets.etf_catalog import load_catalog
        syms: set[str] = set()

        def _walk(o: Any) -> None:
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("symbol", "etf", "ticker") and isinstance(v, str):
                        syms.add(v.upper())
                    _walk(v)
            elif isinstance(o, list):
                for x in o:
                    _walk(x)

        _walk(load_catalog())
        _IN_ETF_SYMBOLS = syms
    except Exception:  # noqa: BLE001
        _IN_ETF_SYMBOLS = set()
    return _IN_ETF_SYMBOLS


def _is_indian_etf(base: str) -> bool:
    if base in _load_in_etf_symbols():
        return True
    return bool(_IN_ETF_HINT.search(base))


def is_us_or_crypto_fast(symbol: str) -> bool:
    """CHEAP US/crypto test — NO DB hit (only the crypto suffix/map + the US
    map). Used by the mark-on-read gate: when the NSE is closed we still want
    to mark US/crypto (they trade on their own clock) but must NOT trigger a
    per-symbol DB resolve for the Indian book. Unknown tickers → False (treated
    as Indian, frozen at the NSE close), which is the safe/fast default."""
    raw = str(symbol or "").upper().strip()
    base = _base_symbol(raw)
    if _looks_like_crypto(raw, base):
        return True
    return base in _US_META


def resolve_many(symbols: list[str], *, session: Any = None) -> dict[str, dict]:
    """Batch resolver: {original_symbol: meta}. Dedups work per symbol."""
    out: dict[str, dict] = {}
    for s in symbols or []:
        if s in out:
            continue
        try:
            out[s] = resolve_security_meta(s, session=session)
        except Exception:  # noqa: BLE001 — never let one bad symbol break a card
            base = _base_symbol(s)
            out[s] = {
                "symbol": str(s).upper(), "base": base, "name": base,
                "logo_url": None, "asset_class": "in_equity", "currency": "INR",
            }
    return out
