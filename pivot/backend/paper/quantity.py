"""Asset-class-aware quantity quantization for the paper book.

The book now stores Numeric(18,8) quantities (migration 0025). How a quantity
is ROUNDED depends on the asset class:
  - Indian equities / ETFs / F&O : whole shares / lots — integer.
  - US equities / ETFs           : fractional shares — up to 6 dp.
  - Crypto                       : fractional units — up to 8 dp.

`quantize_qty(raw, symbol=…|asset_class=…)` returns a Decimal rounded the right
way. `is_fractional_asset(...)` says whether fractional is allowed. `qnum(x)`
coerces a stored Decimal/None to float for arithmetic/display.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Optional

_FRACTIONAL_CLASSES = {"us_equity", "us_etf", "crypto"}
_US_STEP = Decimal("0.000001")    # 6 dp — US fractional shares
_CRYPTO_STEP = Decimal("0.00000001")  # 8 dp — crypto units


def _asset_class(symbol: Optional[str], asset_class: Optional[str]) -> str:
    if asset_class:
        return asset_class
    if not symbol:
        return "in_equity"
    try:
        from backend.view_markets.security_meta import classify
        return str(classify(symbol).get("asset_class") or "in_equity")
    except Exception:  # noqa: BLE001
        return "in_equity"


def is_fractional_asset(symbol: Optional[str] = None, asset_class: Optional[str] = None) -> bool:
    return _asset_class(symbol, asset_class) in _FRACTIONAL_CLASSES


def quantize_qty(
    raw, *, symbol: Optional[str] = None, asset_class: Optional[str] = None,
) -> Decimal:
    """Round ``raw`` to the precision this asset class allows. Indian/options →
    whole units (truncated toward zero); US → 6 dp; crypto → 8 dp. Always
    truncates (ROUND_DOWN) so a fill never claims more than the cash bought."""
    try:
        d = Decimal(str(raw))
    except Exception:  # noqa: BLE001
        return Decimal(0)
    ac = _asset_class(symbol, asset_class)
    if ac == "crypto":
        return d.quantize(_CRYPTO_STEP, rounding=ROUND_DOWN)
    if ac in ("us_equity", "us_etf"):
        return d.quantize(_US_STEP, rounding=ROUND_DOWN)
    # Indian equity / ETF / F&O — whole units.
    return Decimal(int(d))


def qnum(x) -> float:
    """Stored Decimal/None → float for arithmetic and JSON. None → 0.0."""
    if x is None:
        return 0.0
    try:
        return float(x)
    except Exception:  # noqa: BLE001
        return 0.0


def qty_display(x):
    """Read-shape quantity: a plain int for a whole number (Indian shares /
    lots) and a float when fractional (US shares / crypto units). Keeps every
    existing integer consumer/UI unchanged while letting fractional through."""
    f = qnum(x)
    return int(f) if f == int(f) else f
