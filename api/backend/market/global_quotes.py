"""
Global (non-Kite) spot-quote layer.

The existing :mod:`backend.market.yfinance_service` and the Kite Connect path
together cover NSE / BSE equities, NSE indices, NFO options, and
INR-denominated MCX commodities (CRUDEOIL / GOLD / SILVER on MCX). This
module fills the gap that those paths *don't* cover:

* **Crypto** spot — BTC, ETH, SOL, ... in USD (and best-effort INR via cross).
* **Forex** spot — EURUSD, USDINR, GBPUSD, ...
* **Global commodity** spot — USD-denominated WTI / Brent / spot gold
  (XAUUSD) / spot silver (XAGUSD). Note: INR-denominated MCX commodities
  (e.g. ``CRUDEOIL``, ``GOLD``, ``SILVER``) are already reachable via the
  existing ``trigger.price`` -> Kite path; ``trigger.global_price`` is for
  the assets Kite does NOT serve.

The provider chain per asset class (chosen for "no key needed by default,
free for retail volumes, sub-second public REST"):

* **crypto**     — primary: Kraken public REST (`/Ticker?pair=...`),
                   fallback: CoinGecko (`/simple/price?ids=...`).
* **forex**      — primary: Twelve Data (requires ``twelvedata_api_key``),
                   fallback: Frankfurter ECB (`/latest?from=...&to=...`, no key).
* **commodity**  — primary: Twelve Data,
                   fallback: yfinance futures tickers (CL=F / BZ=F / GC=F / SI=F).

A *mock mode* short-circuit (``settings.global_quotes_mock`` or env
``GLOBAL_QUOTES_MOCK``) returns a deterministic synthetic price derived
from a stable SHA-1 hash of the symbol — no randomness, no wall-clock
dependence — so dev and tests are reproducible.

All HTTP I/O goes through an injectable ``http_get`` seam so tests never
touch the network. The seam is ``callable(url: str, params: dict | None = None) -> dict``;
the default implementation uses :mod:`httpx` (sync client, ~6s timeout)
and returns ``resp.json()``.

Resolved quotes are cached in Redis for ~30s (keyed on
``(asset_class, symbol, quote_currency)``) so the polling watcher in
``backend.workflows.scheduler`` can scan many workflows without hammering
the public providers.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Callable, Optional

from backend.cache import redis_client
from backend.config import settings

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 30
HTTP_TIMEOUT_SECONDS = 6.0

ASSET_CLASSES: tuple[str, ...] = ("crypto", "forex", "commodity")


@dataclass(frozen=True)
class GlobalQuote:
    """A resolved spot quote for a non-Kite asset.

    ``asset_class`` is one of :data:`ASSET_CLASSES`. ``symbol`` is the
    canonical, upper-cased symbol as the caller requested it (e.g. ``"BTC"``,
    ``"EURUSD"``, ``"WTI"``) — *not* a provider-specific code. ``source``
    identifies which provider in the chain actually returned the price
    (``"kraken" | "coingecko" | "twelvedata" | "frankfurter" | "yfinance" | "mock"``).
    ``as_of`` is an ISO8601 UTC timestamp captured when this module
    resolved the price; it is NOT the upstream exchange timestamp (which
    most free endpoints don't expose consistently).
    """

    asset_class: str
    symbol: str
    price: float
    quote_currency: str
    source: str
    as_of: str


# ---------------------------------------------------------------------------
# Symbol normalisation
# ---------------------------------------------------------------------------

# Crypto: canonical 3-5 letter ticker -> (kraken pair vs USD, coingecko id).
# Kraken uses "XBT" for bitcoin in some endpoints — the Ticker endpoint
# accepts "XBTUSD". CoinGecko uses lowercase slugs.
_CRYPTO_MAP: dict[str, tuple[str, str]] = {
    "BTC": ("XBTUSD", "bitcoin"),
    "XBT": ("XBTUSD", "bitcoin"),
    "ETH": ("ETHUSD", "ethereum"),
    "SOL": ("SOLUSD", "solana"),
    "ADA": ("ADAUSD", "cardano"),
    "XRP": ("XRPUSD", "ripple"),
    "DOGE": ("DOGEUSD", "dogecoin"),
    "MATIC": ("MATICUSD", "matic-network"),
    "DOT": ("DOTUSD", "polkadot"),
    "LTC": ("LTCUSD", "litecoin"),
    "AVAX": ("AVAXUSD", "avalanche-2"),
    "LINK": ("LINKUSD", "chainlink"),
    "BCH": ("BCHUSD", "bitcoin-cash"),
    "ATOM": ("ATOMUSD", "cosmos"),
}

# Commodity: canonical name -> (twelvedata symbol, yfinance futures ticker).
_COMMODITY_MAP: dict[str, tuple[str, str]] = {
    "WTI": ("WTI/USD", "CL=F"),       # West Texas Intermediate crude
    "CRUDE": ("WTI/USD", "CL=F"),
    "CRUDEOIL": ("WTI/USD", "CL=F"),  # USD-denominated crude (vs MCX CRUDEOIL in INR)
    "BRENT": ("BRENT/USD", "BZ=F"),
    "XAUUSD": ("XAU/USD", "GC=F"),    # Spot gold
    "GOLD": ("XAU/USD", "GC=F"),
    "XAGUSD": ("XAG/USD", "SI=F"),    # Spot silver
    "SILVER": ("XAG/USD", "SI=F"),
    "NATGAS": ("NG/USD", "NG=F"),
    "COPPER": ("COPPER/USD", "HG=F"),
}


def _norm_asset_class(asset_class: str) -> Optional[str]:
    """Lower-case + validate against the supported asset classes."""
    if not asset_class:
        return None
    key = asset_class.strip().lower()
    if key not in ASSET_CLASSES:
        return None
    return key


def _norm_symbol(symbol: str) -> str:
    """Canonicalise a user-supplied symbol.

    Strip whitespace, upper-case, and remove separators commonly used in
    forex pair notation (``EUR/USD`` -> ``EURUSD``, ``USD-INR`` -> ``USDINR``).
    Crypto and commodity symbols are unaffected — they don't normally carry
    these separators.
    """
    if not symbol:
        return ""
    return symbol.strip().upper().replace(" ", "").replace("/", "").replace("-", "")


def _split_forex(symbol: str) -> Optional[tuple[str, str]]:
    """Split a forex symbol like 'EURUSD' or 'EUR/USD' into ('EUR', 'USD')."""
    s = symbol.replace("/", "").replace("-", "").upper()
    if len(s) != 6 or not s.isalpha():
        return None
    return s[:3], s[3:]


def supported_examples() -> dict[str, list[str]]:
    """Per-asset-class hint set the chat planner can suggest to users."""
    return {
        "crypto": ["BTC", "ETH", "SOL"],
        "forex": ["EURUSD", "USDINR", "GBPUSD"],
        "commodity": ["WTI", "BRENT", "XAUUSD", "XAGUSD"],
    }


# ---------------------------------------------------------------------------
# Default HTTP shim — injectable via ``http_get`` for tests.
# ---------------------------------------------------------------------------

def _default_http_get(url: str, params: Optional[dict] = None) -> dict:
    """Sync HTTP GET returning parsed JSON. Raises on any non-200."""
    import httpx  # local import so the module loads without httpx in pure-unit paths

    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        resp = client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Mock-mode deterministic synthetic prices
# ---------------------------------------------------------------------------

def _mock_enabled() -> bool:
    """Honour the explicit env var override and the settings flag."""
    if os.environ.get("GLOBAL_QUOTES_MOCK", "").lower() in ("1", "true", "yes"):
        return True
    return bool(getattr(settings, "global_quotes_mock", False))


def _mock_price(asset_class: str, symbol: str) -> float:
    """Deterministic synthetic price seeded only by (asset_class, symbol).

    Uses SHA-1 of the canonical key (NOT random, NOT wall-clock-dependent)
    so the same inputs always produce the same output — repeatable across
    runs, processes, and test sessions.

    Ranges (broadly plausible without claiming live accuracy):
      * crypto    — $20 .. $120,000
      * forex     — 0.50 .. 200.00
      * commodity — 1.0 .. 5,000.00
    """
    seed = f"{asset_class}:{symbol}".encode()
    digest = hashlib.sha1(seed).digest()
    # Two 32-bit slices give an integer + a fractional offset.
    head = int.from_bytes(digest[:4], "big")
    tail = int.from_bytes(digest[4:8], "big")
    frac = tail / 0xFFFFFFFF  # 0.0 .. 1.0
    if asset_class == "crypto":
        base = 20.0 + (head % 120_000)
        return round(base + frac, 2)
    if asset_class == "forex":
        base = 0.5 + (head % 200)
        return round(base + frac, 4)
    # commodity
    base = 1.0 + (head % 5_000)
    return round(base + frac, 4)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(asset_class: str, symbol: str, quote_currency: Optional[str]) -> str:
    qc = (quote_currency or "").upper() or "DEFAULT"
    return f"global_quote:{asset_class}:{symbol}:{qc}"


def _cache_read(key: str) -> Optional[GlobalQuote]:
    try:
        raw = redis_client.get(key)
    except Exception as e:  # noqa: BLE001
        logger.debug("global_quote cache read failed for %s: %s", key, e)
        return None
    if not raw:
        return None
    try:
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode()
        payload = json.loads(raw)
        return GlobalQuote(**payload)
    except Exception as e:  # noqa: BLE001
        logger.debug("global_quote cache decode failed for %s: %s", key, e)
        return None


def _cache_write(key: str, quote: GlobalQuote) -> None:
    try:
        redis_client.set(key, json.dumps(asdict(quote)), ex=CACHE_TTL_SECONDS)
    except Exception as e:  # noqa: BLE001
        logger.debug("global_quote cache write failed for %s: %s", key, e)


# ---------------------------------------------------------------------------
# Per-provider fetchers — each returns Optional[float] (None on any failure)
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_float(value: object) -> Optional[float]:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _fetch_kraken(symbol: str, http_get: Callable[..., dict]) -> Optional[float]:
    """Kraken: GET /0/public/Ticker?pair=XBTUSD -> result.<pair>.c[0]."""
    pair = _CRYPTO_MAP.get(symbol, (f"{symbol}USD", symbol.lower()))[0]
    url = f"{settings.kraken_api_base_url.rstrip('/')}/Ticker"
    try:
        data = http_get(url, {"pair": pair})
    except Exception as e:  # noqa: BLE001
        logger.debug("kraken fetch failed for %s: %s", pair, e)
        return None
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    if not isinstance(result, dict) or not result:
        return None
    # Kraken returns its own internal pair code as the key (e.g. "XXBTZUSD"
    # for a request of "XBTUSD"). Take the first value — it's the only one.
    for entry in result.values():
        if not isinstance(entry, dict):
            continue
        closes = entry.get("c")
        if isinstance(closes, list) and closes:
            return _safe_float(closes[0])
    return None


def _fetch_coingecko(symbol: str, quote_currency: str, http_get: Callable[..., dict]) -> Optional[float]:
    """CoinGecko: GET /simple/price?ids=bitcoin&vs_currencies=usd."""
    cg_id = _CRYPTO_MAP.get(symbol, (None, symbol.lower()))[1]
    if not cg_id:
        return None
    url = f"{settings.coingecko_api_base_url.rstrip('/')}/simple/price"
    vs = (quote_currency or "USD").lower()
    try:
        data = http_get(url, {"ids": cg_id, "vs_currencies": vs})
    except Exception as e:  # noqa: BLE001
        logger.debug("coingecko fetch failed for %s: %s", cg_id, e)
        return None
    if not isinstance(data, dict):
        return None
    entry = data.get(cg_id)
    if not isinstance(entry, dict):
        return None
    return _safe_float(entry.get(vs))


def _fetch_twelvedata(td_symbol: str, http_get: Callable[..., dict]) -> Optional[float]:
    """Twelve Data: GET /price?symbol=EUR/USD&apikey=KEY."""
    key = (settings.twelvedata_api_key or "").strip()
    if not key:
        return None
    url = f"{settings.twelvedata_api_base_url.rstrip('/')}/price"
    try:
        data = http_get(url, {"symbol": td_symbol, "apikey": key})
    except Exception as e:  # noqa: BLE001
        logger.debug("twelvedata fetch failed for %s: %s", td_symbol, e)
        return None
    if not isinstance(data, dict):
        return None
    # Twelve Data error shape: {"code": 401, "message": "...", "status": "error"}.
    if data.get("status") == "error":
        return None
    return _safe_float(data.get("price"))


def _fetch_frankfurter(base: str, quote: str, http_get: Callable[..., dict]) -> Optional[float]:
    """Frankfurter (ECB): GET /latest?from=EUR&to=USD -> rates.USD."""
    url = f"{settings.frankfurter_api_base_url.rstrip('/')}/latest"
    try:
        data = http_get(url, {"from": base, "to": quote})
    except Exception as e:  # noqa: BLE001
        logger.debug("frankfurter fetch failed for %s/%s: %s", base, quote, e)
        return None
    if not isinstance(data, dict):
        return None
    rates = data.get("rates")
    if not isinstance(rates, dict):
        return None
    return _safe_float(rates.get(quote))


def _fetch_yfinance_futures(yf_ticker: str) -> Optional[float]:
    """yfinance fallback for global commodities (CL=F, BZ=F, GC=F, SI=F)."""
    try:
        import yfinance as yf
    except Exception as e:  # noqa: BLE001
        logger.debug("yfinance import failed: %s", e)
        return None
    try:
        df = yf.Ticker(yf_ticker).history(period="5d", interval="1d", auto_adjust=False)
    except Exception as e:  # noqa: BLE001
        logger.debug("yfinance futures fetch failed for %s: %s", yf_ticker, e)
        return None
    if df is None or getattr(df, "empty", True):
        return None
    try:
        return _safe_float(df["Close"].iloc[-1])
    except Exception as e:  # noqa: BLE001
        logger.debug("yfinance futures close-parse failed for %s: %s", yf_ticker, e)
        return None


# ---------------------------------------------------------------------------
# Per-asset-class provider chains
# ---------------------------------------------------------------------------

def _resolve_crypto(symbol: str, quote_currency: str, http_get: Callable[..., dict]) -> Optional[tuple[float, str]]:
    # Kraken's free Ticker returns USD by default. If the caller wants a
    # non-USD quote we fall through to CoinGecko, which natively supports
    # `vs_currencies=inr/eur/...`.
    if quote_currency == "USD":
        price = _fetch_kraken(symbol, http_get)
        if price is not None and price > 0:
            return price, "kraken"
    price = _fetch_coingecko(symbol, quote_currency, http_get)
    if price is not None and price > 0:
        return price, "coingecko"
    # Final fallback: even for non-USD callers, try Kraken USD so we at least
    # return *something* useful (operator can interpret).
    if quote_currency != "USD":
        price = _fetch_kraken(symbol, http_get)
        if price is not None and price > 0:
            return price, "kraken"
    return None


def _resolve_forex(symbol: str, http_get: Callable[..., dict]) -> Optional[tuple[float, str, str]]:
    pair = _split_forex(symbol)
    if not pair:
        return None
    base, quote = pair
    td_sym = f"{base}/{quote}"
    price = _fetch_twelvedata(td_sym, http_get)
    if price is not None and price > 0:
        return price, quote, "twelvedata"
    price = _fetch_frankfurter(base, quote, http_get)
    if price is not None and price > 0:
        return price, quote, "frankfurter"
    return None


def _resolve_commodity(symbol: str, http_get: Callable[..., dict]) -> Optional[tuple[float, str]]:
    td_sym, yf_ticker = _COMMODITY_MAP.get(symbol, (f"{symbol}/USD", ""))
    price = _fetch_twelvedata(td_sym, http_get)
    if price is not None and price > 0:
        return price, "twelvedata"
    if yf_ticker:
        price = _fetch_yfinance_futures(yf_ticker)
        if price is not None and price > 0:
            return price, "yfinance"
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def get_global_quote(
    asset_class: str,
    symbol: str,
    *,
    quote_currency: Optional[str] = None,
    http_get: Optional[Callable[..., dict]] = None,
) -> Optional[GlobalQuote]:
    """Resolve a spot quote for a non-Kite asset.

    Returns ``None`` only when *every* provider in the chain fails (or the
    inputs are invalid). The provider chain order is documented at the top
    of this module.

    Mock mode (``settings.global_quotes_mock=True`` or env
    ``GLOBAL_QUOTES_MOCK=1``) short-circuits the providers and returns a
    deterministic synthetic price with ``source="mock"`` — useful for
    development, tests, and demo paths.

    Parameters
    ----------
    asset_class
        One of :data:`ASSET_CLASSES`: ``"crypto" | "forex" | "commodity"``.
    symbol
        Canonical user-supplied symbol (``"BTC"``, ``"EURUSD"``, ``"WTI"``,
        ``"XAUUSD"``, ...). Case- and space-insensitive.
    quote_currency
        Optional override for the quote leg. Crypto defaults to ``USD``;
        forex is intrinsic to the pair (``USDINR`` -> quote = ``INR``);
        commodity defaults to ``USD``.
    http_get
        Injection seam for tests. Default uses :mod:`httpx` (sync, ~6s
        timeout) and returns ``resp.json()``. Signature:
        ``callable(url: str, params: dict | None = None) -> dict``.
    """
    ac = _norm_asset_class(asset_class)
    sym = _norm_symbol(symbol)
    if not ac or not sym:
        return None

    cache_key = _cache_key(ac, sym, quote_currency)
    cached = _cache_read(cache_key)
    if cached is not None:
        return cached

    if _mock_enabled():
        # Deterministic synthetic price. We still respect the requested
        # quote_currency so callers receive a coherent shape; falls back
        # to the asset-class default when None.
        if ac == "forex":
            pair = _split_forex(sym)
            qc = pair[1] if pair else (quote_currency or "USD").upper()
        else:
            qc = (quote_currency or "USD").upper()
        quote = GlobalQuote(
            asset_class=ac,
            symbol=sym,
            price=_mock_price(ac, sym),
            quote_currency=qc,
            source="mock",
            as_of=_now_iso(),
        )
        _cache_write(cache_key, quote)
        return quote

    getter = http_get or _default_http_get

    try:
        if ac == "crypto":
            qc = (quote_currency or "USD").upper()
            resolved = _resolve_crypto(sym, qc, getter)
            if resolved is None:
                return None
            price, source = resolved
            quote = GlobalQuote(ac, sym, price, qc, source, _now_iso())
        elif ac == "forex":
            resolved_fx = _resolve_forex(sym, getter)
            if resolved_fx is None:
                return None
            price, qc, source = resolved_fx
            quote = GlobalQuote(ac, sym, price, qc, source, _now_iso())
        else:  # commodity
            qc = (quote_currency or "USD").upper()
            resolved_c = _resolve_commodity(sym, getter)
            if resolved_c is None:
                return None
            price, source = resolved_c
            quote = GlobalQuote(ac, sym, price, qc, source, _now_iso())
    except Exception as e:  # noqa: BLE001 — never raise out of public API
        logger.warning("get_global_quote unexpected error for %s/%s: %s", ac, sym, e)
        return None

    _cache_write(cache_key, quote)
    return quote


__all__ = [
    "ASSET_CLASSES",
    "GlobalQuote",
    "get_global_quote",
    "supported_examples",
]
