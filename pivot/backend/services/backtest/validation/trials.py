"""Per-session backtest TRIAL tracking → the Deflated-Sharpe selection-bias guard.

The Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014) deflates a strategy's
Sharpe by the *expected maximum* Sharpe an unskilled researcher would stumble
onto after N independent trials. A lone backtest has N=1 → no deflation
(DSR == PSR(0)). The honest value appears when a user **tunes** — runs many
variants and keeps the best: each new *distinct* variant raises N, and unless the
kept strategy's edge is real its DSR correctly collapses toward insignificance.
This is the single thing no Indian retail backtester does, and it's exactly the
behaviour the chat surface invites ("try RSI<30… now RSI<25… now MACD…").

This registry tracks, per ``group`` (a research session), the distinct strategy
*fingerprints* tried and each one's observed (non-annualized) Sharpe, then
re-deflates a ``forward_stats`` block with the group's effective N and the
cross-trial Sharpe variance V[SR_n].

In-process + TTL-evicted (research bursts are short-lived; a single uvicorn
worker in dev). A multi-worker deployment should back this with Redis — the API
(``record_and_deflate`` / ``strategy_fingerprint``) is storage-agnostic so the
swap is local to this module.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any, Optional

from backend.services.forward_stats import deflated_sharpe_ratio

_TTL_SECONDS = 2 * 3600        # a trial older than 2h belongs to a different session
_MAX_GROUPS = 2000            # memory backstop

_lock = threading.Lock()
# group -> { fingerprint -> (observed_sharpe_or_None, last_ts) }
_registry: dict[str, dict[str, tuple[Optional[float], float]]] = {}


def strategy_fingerprint(*parts: Any) -> str:
    """Stable short hash identifying an exact strategy + window. Re-running the
    identical thing reuses the fingerprint (NOT counted as a new trial); any
    change to steps / symbol / window mints a new one."""
    blob = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:16]


def _evict(now: float) -> None:
    """Drop stale fingerprints, then emptied groups, then cap total groups."""
    for g in list(_registry.keys()):
        fps = _registry[g]
        for fp in list(fps.keys()):
            if now - fps[fp][1] > _TTL_SECONDS:
                del fps[fp]
        if not fps:
            del _registry[g]
    if len(_registry) > _MAX_GROUPS:
        ordered = sorted(
            _registry.items(),
            key=lambda kv: max((ts for _, ts in kv[1].values()), default=0.0),
        )
        for g, _ in ordered[: len(_registry) - _MAX_GROUPS]:
            _registry.pop(g, None)


def record_and_deflate(
    fs_block: dict, group: Optional[str], fingerprint: str,
    *, now: Optional[float] = None,
) -> dict:
    """Record this trial under ``group`` and return ``fs_block`` with its DSR
    re-deflated for the group's effective N (distinct fingerprints with a usable
    Sharpe) + the cross-trial Sharpe variance V[SR_n].

    ``group`` falsy → returned unchanged (num_trials stays 1, the lone-backtest
    case). Idempotent on fingerprint: re-running the same strategy refreshes its
    timestamp but does not inflate N."""
    if not group:
        return fs_block
    ts = time.time() if now is None else now
    sr_hat = fs_block.get("observed_sharpe")
    with _lock:
        _evict(ts)
        grp = _registry.setdefault(group, {})
        grp[fingerprint] = (sr_hat, ts)
        sharpes = [s for (s, _t) in grp.values() if s is not None]
    n_trials = max(1, len(sharpes))
    sr_variance: Optional[float] = None
    if len(sharpes) >= 2:
        mu = sum(sharpes) / len(sharpes)
        sr_variance = sum((s - mu) ** 2 for s in sharpes) / (len(sharpes) - 1)
    dsr = deflated_sharpe_ratio(
        fs_block.get("observed_sharpe"),
        int(fs_block.get("n_obs") or 0),
        fs_block.get("skew"),
        fs_block.get("kurtosis"),
        n_trials,
        sr_variance=sr_variance,
    )
    out = dict(fs_block)
    out["num_trials"] = n_trials
    out["deflated_sharpe"] = round(dsr, 4) if dsr is not None else None
    return out


def reset_group(group: str) -> None:
    """Forget a group's trials (test / 'start fresh' hook)."""
    with _lock:
        _registry.pop(group, None)


__all__ = ["strategy_fingerprint", "record_and_deflate", "reset_group"]
