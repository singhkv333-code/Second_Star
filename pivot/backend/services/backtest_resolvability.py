"""Pre-flight: walk a draft's steps and report which Mustache refs the
backtester cannot resolve.

Why this exists (R4a): the live engine has a fail-closed ref resolver
(`refs.resolve_refs`), but the backtester only ships a partial
best-effort resolver (`workflow_backtester._resolve_ref`). When a draft
carries a ref the backtester does not understand — `{{ context.1.total_value_inr }}`
is the canonical example, surfaced from a portfolio-percentage sizing
prompt — the backtester previously crashed with
`could not convert string to float: '{{...}}'` after the ref leaked
into a numeric field.

This module ports the backtester's whitelist into a deterministic walk
that runs at *draft validation time*. The result drives:

  - `workflow_draft_card.backtestable` (bool) — FE hides the Backtest
    button when False.
  - `workflow_draft_card.backtest_blockers` (list[str]) — short
    human-readable reasons that the chat layer can surface.

The whitelist MUST match `workflow_backtester._resolve_ref` step-for-
step. When you add a new resolvable shape there, update
`_classify_ref` here.
"""
from __future__ import annotations

import re
from typing import Any

_REF_RE = re.compile(r"\{\{\s*([^}]+)\s*\}\}")

# Step types whose canonical output field is `value`. Mirrors the
# `parts[2] in {"value", ...}` branch in workflow_backtester._resolve_ref
# (which then dispatches per step_type).
_FETCH_VALUE_TYPES = frozenset({
    "fetch.indicator",
    "fetch.day_open",
    "fetch.prior_close",
    "fetch.rolling_high",
    "fetch.rolling_low",
    "fetch.relative_threshold",
    "fetch.spread_z_score",
    "fetch.fundamental",
})
_QUOTE_FIELDS = frozenset({"ltp", "open", "high", "low", "close", "volume"})
_DAY_PRIOR_FIELDS = frozenset({
    "day_open", "prior_close", "prior_high", "prior_low",
})


def _index_steps(steps: list[dict]) -> dict[int, str]:
    """Returns ``{step_index: step_type}``. Falls back to positional
    index when an explicit `step_index` is missing or malformed."""
    out: dict[int, str] = {}
    for pos, st in enumerate(steps or []):
        if not isinstance(st, dict):
            continue
        raw_idx = st.get("step_index", pos)
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            idx = pos
        out[idx] = str(st.get("step_type") or "")
    return out


def _classify_ref(inner: str, by_idx: dict[int, str]) -> str | None:
    """Return None when the backtester can resolve this ref, else a
    short human-readable reason explaining why it can't."""
    parts = inner.split(".")
    if not parts or parts[0] != "context" or len(parts) < 3:
        return f"non-context ref `{{{{ {inner} }}}}`"
    try:
        idx = int(parts[1])
    except ValueError:
        return f"non-integer step index in `{{{{ {inner} }}}}`"
    field = parts[2]

    # Always-resolvable (no step-type dependency).
    if field == "buying_power":
        return None
    if field == "holdings" and len(parts) >= 5 and parts[4] == "quantity":
        return None

    src_type = by_idx.get(idx)
    if src_type is None:
        return (
            f"step index {idx} referenced by `{{{{ {inner} }}}}` "
            "is not present in the draft"
        )

    # `{{context.<idx>.quantity}}` where step idx is an order — resolves to
    # the entry order's filled position size (see _resolve_ref). This is the
    # canonical "sell the quantity I bought" exit pattern.
    if field == "quantity" and src_type == "action.place_order":
        return None

    if field == "value" and src_type in _FETCH_VALUE_TYPES:
        return None
    if field in _QUOTE_FIELDS and src_type == "fetch.quote":
        return None
    if field in _DAY_PRIOR_FIELDS and src_type in {
        "fetch.day_open", "fetch.prior_close",
    }:
        return None

    return (
        f"backtester cannot resolve `{{{{ {inner} }}}}` "
        f"(step {idx} is `{src_type or '?'}`). "
        "Supported refs: `buying_power`, `holdings.<SYM>.quantity`, "
        "and `fetch.*` step outputs (`value`, `ltp`, OHLCV, "
        "`day_open`/`prior_close`/`prior_high`/`prior_low`)."
    )


def _walk_refs(value: Any) -> list[str]:
    """Return every Mustache inner ref found anywhere inside ``value``,
    recursing through dicts / lists. Strings are scanned for `{{...}}`
    matches; non-collection scalars are ignored."""
    found: list[str] = []
    if isinstance(value, str):
        for m in _REF_RE.finditer(value):
            found.append(m.group(1).strip())
    elif isinstance(value, dict):
        for v in value.values():
            found.extend(_walk_refs(v))
    elif isinstance(value, list):
        for v in value:
            found.extend(_walk_refs(v))
    return found


# Largest live fetch window (3y ≈ 756 NSE trading days) — matches the cap in
# market_data.period_for_indicator. An indicator whose min-history need
# (period + ~5 warm-up) exceeds this can never compute live even after the
# window-scaling fix, so the agent would silently never fire.
_LIVE_CAP_BARS = 756
_LIVE_WARMUP = 5


def _indicator_periods(value: Any) -> list[int]:
    """Recursively collect indicator look-back periods from a step config.
    Handles both the flat `trigger.indicator`/`fetch.indicator` shape
    ({"indicator": "ema", "period": N}) and nested DSL IndicatorNodes
    ({"type": "indicator", "period": N, ...}) inside compound trees."""
    out: list[int] = []
    if isinstance(value, dict):
        is_indicator = (
            value.get("type") == "indicator" or "indicator" in value
        )
        if is_indicator and isinstance(value.get("period"), (int, float)):
            out.append(int(value["period"]))
        for v in value.values():
            out.extend(_indicator_periods(v))
    elif isinstance(value, list):
        for v in value:
            out.extend(_indicator_periods(v))
    return out


def check_live_fireable(steps: list[dict]) -> list[str]:
    """Return WARNINGS (not blockers) for indicators whose required history
    exceeds the live fetch cap, so the live watcher could never compute them.
    Backtesting is unaffected (it uses multi-year windows); this only warns
    that the agent may not FIRE live. Returns [] when everything is fireable.
    """
    worst = 0
    for st in steps or []:
        if not isinstance(st, dict):
            continue
        for p in _indicator_periods(st.get("config") or {}):
            if p + _LIVE_WARMUP > _LIVE_CAP_BARS:
                worst = max(worst, p)
    if worst:
        return [
            f"A {worst}-period indicator needs more than ~3 years of daily "
            "history; live evaluation caps at 3 years, so this agent may not "
            "fire reliably. Consider a shorter period (≤ ~750)."
        ]
    return []


def check_draft(steps: list[dict]) -> tuple[bool, list[str]]:
    """Inspect every Mustache ref in a draft's step configs and report
    backtest resolvability.

    Returns ``(backtestable, blockers)`` where ``backtestable`` is False
    iff at least one ref appears that the backtester cannot resolve.
    ``blockers`` enumerates each *distinct* unresolvable ref with a
    short reason — safe to render verbatim to the user.

    Tolerant of malformed input: returns ``(True, [])`` for an empty
    ``steps`` list and ignores non-dict step entries.
    """
    by_idx = _index_steps(steps)
    seen: set[str] = set()
    blockers: list[str] = []
    for st in steps or []:
        if not isinstance(st, dict):
            continue
        cfg = st.get("config") or {}
        for inner in _walk_refs(cfg):
            reason = _classify_ref(inner, by_idx)
            if reason and reason not in seen:
                seen.add(reason)
                blockers.append(reason)
    return (len(blockers) == 0, blockers)
