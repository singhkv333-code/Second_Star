"""Options pricing & Greeks engine (P0 of the F&O integration).

Model choice (see docs/plan): **Black-76 on the same-expiry future** for
every NSE/BSE/MCX option we touch. NSE index/stock options are European
and cash-settled on spot, but the no-arbitrage hedge and the market's
risk-neutral drift live in the synchronous future — pricing off the
future absorbs dividends / repo / basis automatically, so we never model
a dividend yield. MCX options are literally options-on-futures, so
Black-76 is mandatory there. When the same-expiry future is illiquid we
derive a synthetic forward from ATM put-call parity (`forward.py`).

Layout:
  black76.py      owned, vectorized numpy Black-76 price + Greeks — the
                  deterministic risk path (no third-party dep).
  iv.py           implied-vol solving. Prefers py_vollib_vectorized's
                  Jäckel rational solver when installed; falls back to a
                  vectorized Newton-Raphson + Brent solver. Emits a
                  per-strike ``iv_status`` — we NEVER fabricate an IV.
  forward.py      synthetic forward via put-call parity.
  chain_greeks.py full-chain vectorized IV + Greeks decoration.

Conventions (pinned here so every consumer agrees):
  * ``T`` is in YEARS, calendar-day basis (365), computed with an
    intraday clock — weeklies mis-state theta by a full day otherwise.
  * IV is solved on the bid/ask MID, never LTP (stale LTP poisons chains).
  * ``vega`` is per 1 percentage-point of vol; ``theta`` is per calendar
    DAY (both the retail-display conventions).
  * ``delta`` is w.r.t. the future. For short tenors ∂F/∂S ≈ 1, so we
    report it as-is; document, don't chain-rule.
"""
from backend.market.greeks.black76 import (  # noqa: F401
    black76_greeks,
    black76_price,
    year_fraction,
)
from backend.market.greeks.forward import synthetic_forward  # noqa: F401
from backend.market.greeks.iv import (  # noqa: F401
    IV_ILLIQUID,
    IV_NO_SOLUTION,
    IV_NO_ARB,
    IV_OK,
    IV_STALE,
    implied_vol,
)
from backend.market.greeks.chain_greeks import compute_chain_greeks  # noqa: F401
