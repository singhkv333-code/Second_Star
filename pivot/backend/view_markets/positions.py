"""My Views — the per-user position ledger over deployed view expressions.

Register-not-execute, end to end: a ViewPosition records what the user has
ARMED and (by their own hand, in their broker app) expressed — Pivot never
places, resizes, or exits an order. Everything here is ledger arithmetic:

  * :func:`create_position` — snapshot the expression's tradeable legs at
    entry with REAL marks (Kite-primary / yfinance-fallback via
    ``paper.marks.get_mark_price``). A leg without a live mark keeps
    ``entry_price = None`` and is excluded from the return — never fabricated.
    Option/hedge expressions get NO priced legs (their strikes exist only at
    deploy in the broker) and carry an honest "Priced at deploy" note.
  * :func:`position_snapshot` — the live, up-to-date state: per-leg marks,
    the weighted return on the OPEN fraction, rupee values when (and only
    when) the user has declared ``capital_inr``, and whether the user's own
    take-profit / stop-loss levels are hit RIGHT NOW (computed at read time —
    there is no auto-exit watcher and we never claim one).
  * :func:`apply_exit` — record a partial/full exit of the OPEN fraction at
    current marks; accrues realized P&L when capital is known. The response
    reminds the user to place the actual exit orders themselves.

Short legs profit when price falls: r = entry/mark - 1 … no, careful —
r_short = (entry - mark) / entry. Weights are normalized over the PRICED legs
so a dropped/unpriceable leg shrinks coverage honestly instead of skewing it.

Tests monkeypatch :data:`get_mark_price` on THIS module (the seam paper-trading
tests already use for ``marks``)."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from backend.paper.marks import get_mark_price  # Kite-primary, yf fallback

# A position whose open_fraction falls below this is fully exited (float dust).
_EXIT_EPSILON = 1e-6

# The honest note stamped on option/hedge positions (no fabricated marks).
# Kept as the LIVE-mode wording (real broker fill) for backward compat with
# any code importing the bare constant; `create_position` below picks the
# paper-mode variant when the account is in paper mode, since "track the fill
# in your broker app" is factually wrong there — nothing was sent to a broker.
PRICED_AT_DEPLOY_NOTE = (
    "Options structure — strikes are set when you place it, so the ledger "
    "can't price it from here. Track the fill in your broker app."
)
PRICED_AT_DEPLOY_NOTE_PAPER = (
    "Options structure — strikes are set when it fires, so the ledger can't "
    "price it from here. Check the Agents tab for this workflow's armed "
    "status and its simulated fills."
)

# Every exit response carries this (register-not-execute).
EXIT_NOTE = (
    "Recorded on your ledger — place the actual exit order(s) in your own "
    "broker app. Pivot never places or exits orders for you."
)


def _mark(symbol: str) -> Optional[float]:
    """One live mark (float) or None — never a fabricated price."""
    try:
        m = get_mark_price(symbol)
    except Exception:  # noqa: BLE001 — a data hiccup must not 500 the ledger
        return None
    if m is None:
        return None
    try:
        f = float(m)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


# ── entry-leg snapshot ───────────────────────────────────────────────────────


def _structure(expression: Any) -> tuple[str, dict, dict]:
    kind = str(getattr(expression.expression_kind, "value",
                       expression.expression_kind))
    config = dict(expression.config or {})
    structure = config.get("structure") or {}
    if not isinstance(structure, dict):
        structure = {}
    return kind, config, structure


def position_legs(expression: Any) -> tuple[list[dict], Optional[str]]:
    """The ledger legs ``[{symbol, side, weight}]`` for an expression + an
    optional honesty note. Mirrors the deploy synthesis rules (AVOID /
    untradeable legs dropped, shorts only when a real short exists); option
    and hedge kinds return NO legs + the priced-at-deploy note."""
    kind, config, structure = _structure(expression)

    if kind in ("option_strategy", "hedge"):
        return [], PRICED_AT_DEPLOY_NOTE

    if kind == "pair":
        legs: list[dict] = []
        a = structure.get("a")
        if a:
            legs.append({"symbol": str(a), "side": "long", "weight": 1.0})
        short = structure.get("short_leg") or {}
        mode = short.get("mode")
        instrument = short.get("instrument") or structure.get("b")
        if mode and mode != "avoid" and instrument:
            legs.append(
                {"symbol": str(instrument), "side": "short", "weight": 1.0}
            )
            return legs, None
        note = (
            "One-sided expression — no tradeable short leg, so the ledger "
            "tracks the long leg only."
            if legs
            else None
        )
        return legs, note

    if kind == "multi_asset":
        legs = []
        for sleeve in structure.get("sleeves") or []:
            if not isinstance(sleeve, dict):
                continue
            try:
                sw = float(sleeve.get("weight") or 0.0)
            except (TypeError, ValueError):
                continue
            if sw <= 0:
                continue
            detail = sleeve.get("detail") or {}
            if sleeve.get("kind") == "equity_basket":
                inner = (detail.get("structure") or {}).get("weights") or {}
                for sym, w in inner.items():
                    try:
                        ww = float(w) * sw
                    except (TypeError, ValueError):
                        continue
                    if ww > 0:
                        legs.append(
                            {"symbol": str(sym), "side": "long",
                             "weight": round(ww, 6)}
                        )
            elif sleeve.get("kind") == "gold_etf":
                sym = detail.get("symbol")
                if isinstance(sym, str) and sym:
                    legs.append(
                        {"symbol": sym, "side": "long", "weight": round(sw, 6)}
                    )
            # commodity/hedge sleeves are armed separately — not ledger legs
        return legs, None

    # basket (the default kind)
    legs = []
    meta: dict[str, dict] = {}
    for inst in config.get("instruments") or []:
        if isinstance(inst, dict) and isinstance(inst.get("symbol"), str):
            meta.setdefault(inst["symbol"], inst)

    # Explicit weights when the builder emitted them; otherwise the v3
    # research structures carry an equal-weight ``members_long`` list.
    weights: dict[str, float] = {}
    for sym, w in (structure.get("weights") or {}).items():
        try:
            ww = float(w)
        except (TypeError, ValueError):
            continue
        if ww > 0:
            weights[str(sym)] = ww
    if not weights:
        members = [
            str(m) for m in (structure.get("members_long") or []) if m
        ]
        if members:
            ew = 1.0 / len(members)
            weights = {m: ew for m in members}

    for sym, ww in weights.items():
        info = meta.get(sym, {})
        if info.get("tradeable") is False:
            continue
        side = "short" if info.get("role") == "short" else "long"
        legs.append({"symbol": sym, "side": side, "weight": round(ww, 6)})
    return legs, None


def create_position(
    db: Any,
    view: Any,
    expression: Any,
    *,
    user_id: int,
    capital_inr: Optional[float] = None,
    workflow_id: Optional[str] = None,
) -> Any:
    """Create the ledger row for a fresh deploy. Flush-only (caller owns txn).

    Legs are snapshotted with live entry marks; a leg the market can't price
    right now keeps ``entry_price = None`` (excluded from returns, never
    guessed)."""
    from backend.models import ViewPosition, ViewPositionStatus

    legs, note = position_legs(expression)
    for leg in legs:
        leg["entry_price"] = _mark(leg["symbol"])

    if note == PRICED_AT_DEPLOY_NOTE:
        # "Track the fill in your broker app" is only true in LIVE mode — a
        # paper deploy never contacts a broker (see paper/routing.should_use_
        # paper). Swap to the paper-accurate wording so the ledger never
        # points the user somewhere nothing happened.
        try:
            from backend.paper.routing import should_use_paper
            if should_use_paper(db, int(user_id)):
                note = PRICED_AT_DEPLOY_NOTE_PAPER
        except Exception:  # noqa: BLE001 — the LIVE note is still accurate
            pass

    pos = ViewPosition(
        user_id=user_id,
        view_id=str(view.id),
        expression_id=str(expression.id),
        workflow_id=str(workflow_id) if workflow_id else None,
        status=ViewPositionStatus.open,
        capital_inr=float(capital_inr) if capital_inr else None,
        open_fraction=1.0,
        legs=legs,
        exits=[],
        note=note,
    )
    db.add(pos)
    db.flush()
    return pos


# ── live snapshot / return math ──────────────────────────────────────────────


def _leg_return_pct(leg: dict, mark: Optional[float]) -> Optional[float]:
    """One leg's live % return (short-aware) or None when unpriceable."""
    entry = leg.get("entry_price")
    if not entry or not mark:
        return None
    try:
        entry_f = float(entry)
    except (TypeError, ValueError):
        return None
    if entry_f <= 0:
        return None
    r = (mark - entry_f) / entry_f
    if leg.get("side") == "short":
        r = -r
    return r * 100.0


def open_return_pct(legs: list[dict],
                    marks: Optional[dict[str, Optional[float]]] = None,
                    ) -> Optional[float]:
    """Weighted live return (%) across the PRICED legs, or None when nothing
    is priceable. Weights are normalized over priced legs only."""
    total_w = 0.0
    acc = 0.0
    for leg in legs or []:
        mark = (marks or {}).get(leg.get("symbol")) if marks is not None \
            else _mark(leg.get("symbol") or "")
        r = _leg_return_pct(leg, mark)
        if r is None:
            continue
        try:
            w = abs(float(leg.get("weight") or 0.0))
        except (TypeError, ValueError):
            continue
        if w <= 0:
            continue
        acc += r * w
        total_w += w
    if total_w <= 0:
        return None
    return acc / total_w


def position_snapshot(position: Any) -> dict[str, Any]:
    """The live, up-to-date read of one ledger row (pure computation +
    price I/O; no DB writes). Marks are fetched once per distinct symbol."""
    legs: list[dict] = list(position.legs or [])
    marks: dict[str, Optional[float]] = {}
    for leg in legs:
        sym = leg.get("symbol")
        if isinstance(sym, str) and sym and sym not in marks:
            marks[sym] = _mark(sym)

    legs_out: list[dict[str, Any]] = []
    for leg in legs:
        mark = marks.get(leg.get("symbol"))
        legs_out.append(
            {
                "symbol": leg.get("symbol"),
                "side": leg.get("side") or "long",
                "weight": leg.get("weight"),
                "entry_price": leg.get("entry_price"),
                "last_price": mark,
                "return_pct": _leg_return_pct(leg, mark),
            }
        )

    ret = open_return_pct(legs, marks)
    capital = position.capital_inr
    frac = float(position.open_fraction or 0.0)
    realized = position.realized_pnl_inr

    unrealized_inr = None
    open_value_inr = None
    if capital and ret is not None:
        unrealized_inr = capital * frac * (ret / 100.0)
        open_value_inr = capital * frac * (1.0 + ret / 100.0)
    elif capital:
        open_value_inr = capital * frac  # honest: no live mark, principal only

    tp = position.take_profit_pct
    sl = position.stop_loss_pct
    return {
        "return_pct": ret,
        "legs": legs_out,
        "unrealized_pnl_inr": unrealized_inr,
        "open_value_inr": open_value_inr,
        "realized_pnl_inr": realized,
        # The user's own exit plan, compared at READ time — never auto-acted.
        "take_profit_hit": bool(tp is not None and ret is not None
                                and ret >= float(tp)),
        "stop_loss_hit": bool(sl is not None and ret is not None
                              and ret <= -abs(float(sl))),
    }


# ── exits (partial / full) ───────────────────────────────────────────────────


def apply_exit(db: Any, position: Any, *, pct_of_open: float) -> dict[str, Any]:
    """Record exiting ``pct_of_open``% of the OPEN fraction at current marks.

    Flush-only (caller owns txn). Returns ``{exited_pct, return_pct,
    realized_pnl_inr, open_fraction, status, note}``. Raises ``ValueError``
    on a non-positive/oversized pct or an already-exited position."""
    from backend.models import ViewPositionStatus

    if str(getattr(position.status, "value", position.status)) == "exited":
        raise ValueError("position is already fully exited")
    try:
        pct = float(pct_of_open)
    except (TypeError, ValueError) as exc:
        raise ValueError("exit pct must be a number") from exc
    if not 0.0 < pct <= 100.0:
        raise ValueError("exit pct must be between 0 and 100")

    frac = float(position.open_fraction or 0.0)
    if frac <= _EXIT_EPSILON:
        raise ValueError("position has no open fraction left to exit")

    ret = open_return_pct(list(position.legs or []))
    slice_frac = frac * (pct / 100.0)

    realized_slice = None
    if position.capital_inr and ret is not None:
        realized_slice = float(position.capital_inr) * slice_frac * (ret / 100.0)
        position.realized_pnl_inr = (
            float(position.realized_pnl_inr or 0.0) + realized_slice
        )

    new_frac = frac - slice_frac
    position.open_fraction = new_frac
    exits = list(position.exits or [])
    exits.append(
        {
            "at": datetime.now(timezone.utc).isoformat(),
            "pct_of_open": pct,
            "return_pct": ret,
            "realized_pnl_inr": realized_slice,
        }
    )
    position.exits = exits

    if new_frac <= _EXIT_EPSILON or pct >= 100.0:
        position.open_fraction = 0.0
        position.status = ViewPositionStatus.exited
        position.exited_at = datetime.now(timezone.utc)

    db.flush()
    return {
        "exited_pct": pct,
        "return_pct": ret,
        "realized_pnl_inr": realized_slice,
        "open_fraction": float(position.open_fraction),
        "status": str(getattr(position.status, "value", position.status)),
        "note": EXIT_NOTE,
    }


__all__ = [
    "EXIT_NOTE",
    "PRICED_AT_DEPLOY_NOTE",
    "apply_exit",
    "create_position",
    "open_return_pct",
    "position_legs",
    "position_snapshot",
]
