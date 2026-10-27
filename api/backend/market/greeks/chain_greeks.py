"""Full-chain IV + Greeks decoration — one vectorized pass per side.

Input rows are the option_chain service's per-strike dicts:

    {"strike": 23500.0,
     "ce": {"ltp":…, "bid":…, "ask":…, "oi":…, "volume":…},
     "pe": {…}}

Output: the same rows with ``iv, iv_status, delta, gamma, theta, vega``
attached to each quoted side. Mid (not LTP) feeds the solve; sides with
``iv_status != ok/wide_spread`` carry ``iv/greeks = None`` — downstream
screeners must treat absence as absence, not zero.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from backend.market.greeks.black76 import black76_greeks
from backend.market.greeks.iv import IV_OK, IV_WIDE_SPREAD, implied_vol

_GREEK_KEYS = ("delta", "gamma", "theta", "vega")


def _mid(q: dict[str, Any]) -> float:
    bid = float(q.get("bid") or 0.0)
    ask = float(q.get("ask") or 0.0)
    if bid > 0.0 and ask >= bid:
        return (bid + ask) / 2.0
    return float(q.get("ltp") or 0.0)  # depth missing → LTP is all we have


def _decorate_side(
    rows: list[dict[str, Any]], side: str, F: float, T: float, r: float,
) -> None:
    quoted = [(i, r_[side]) for i, r_ in enumerate(rows) if r_.get(side)]
    if not quoted or F <= 0.0:
        return
    idx = [i for i, _ in quoted]
    quotes = [q for _, q in quoted]
    K = np.array([float(rows[i]["strike"]) for i in idx])
    mid = np.array([_mid(q) for q in quotes])
    bid = np.array([float(q.get("bid") or 0.0) for q in quotes])
    ask = np.array([float(q.get("ask") or 0.0) for q in quotes])
    flag = 1.0 if side == "ce" else -1.0

    iv, status = implied_vol(mid, F, K, T, flag, r=r, bid=bid, ask=ask)
    usable = np.array([s in (IV_OK, IV_WIDE_SPREAD) for s in status])
    greeks = black76_greeks(F, K, np.where(usable, iv, 0.2), np.full_like(K, T), r=r, flag=flag)

    for j, q in enumerate(quotes):
        q["mid"] = round(float(mid[j]), 4)
        q["iv_status"] = str(status[j])
        if usable[j]:
            q["iv"] = round(float(iv[j]), 6)
            for key in _GREEK_KEYS:
                q[key] = round(float(greeks[key][j]), 6)
        else:
            q["iv"] = None
            for key in _GREEK_KEYS:
                q[key] = None


def compute_chain_greeks(
    rows: list[dict[str, Any]],
    forward: float,
    T: float,
    *,
    r: float = 0.065,
) -> list[dict[str, Any]]:
    """Decorate ``rows`` in place (and return them) with IV + Greeks for
    both sides, priced off ``forward`` with year-fraction ``T``."""
    _decorate_side(rows, "ce", forward, T, r)
    _decorate_side(rows, "pe", forward, T, r)
    return rows
