"""View Markets — Phase 4 deployment: ``deploy_expression`` (register-not-execute).

Turn a built :class:`~backend.models.ViewExpression` + its timing spec into an
**ARMED workflow draft**: the ``{step_type, label, config}`` step list the
workflow engine already speaks (trigger.* first, then the kind's action step),
persist it as a ``Workflow`` (status ``draft``), link
``ViewExpression.workflow_id``, and — only when ``activate`` — flip it ``active``
via the real activate path. Pivot **NEVER places an order**: every order/option/
basket action step carries ``requires_approval=True`` and live option strategies
deploy with ``book='live'`` (the user confirms + places in their own broker app).

Trigger steps come straight from ``config.timing`` (``timing.timing_to_trigger``,
already stamped by dispatch): each ``tranches[*].trigger`` is a ``{step_type,
config}`` pair (``trigger.schedule`` one-time / ``trigger.scheduled_macro`` /
``trigger.event`` / ``trigger.indicator`` / ``trigger.price``). Prediction-market
triggers are NEVER emitted — ``timing.py`` already guarantees this (PROGA-hidden).
The ``invalidation`` spec (thesis-break) becomes an exit branch (its trigger +
``action.squareoff_all`` / a notify) when present.

Action step per kind (:data:`ACTION_STEP_BY_KIND`):

  * ``option_strategy`` / ``hedge`` → ``action.place_option_strategy``
    (``underlying`` / ``template`` / ``expiry_rule`` / ``qty_lots`` from
    ``config.structure``; ``book='live'``, ``requires_approval=True``). MCX
    commodity underlyings route here too (now tradeable) WITH the leverage note.
  * ``basket`` → ``action.allocate_basket`` (legs = ``config.structure.weights``
    → ``[{symbol, weight, side}]``, ``total_inr`` from the capital tier knob,
    ``requires_approval=True``). AVOID names are dropped (never shorted).
  * ``multi_asset`` → ``action.allocate_basket`` across the sleeves (equity legs +
    the gold/silver ETF leg), ``requires_approval=True``.
  * ``pair`` → two ``action.place_order`` legs: long ``a`` (buy) + the HONEST
    short of ``b`` (``config.structure.short_leg`` — a tradeable SSF/MCX-future
    short, a defined-risk put via ``action.place_option_strategy``, or — when the
    short is ``AVOID`` — the long leg + a ``notify.message`` explaining the
    one-sided expression). ``requires_approval=True``. A COMMODITY leg routes to
    the MCX future / defined-risk MCX put and carries the leverage note.

Each tranche's ``pct`` scales that branch's size (``qty_lots`` / ``total_inr`` /
``notional_inr``) — sizing is NEVER auto-computed for commodities (the user
confirms lots). Approval gating is preserved end-to-end.

register-not-execute: build + persist the draft, optionally ``activate`` the
workflow (which only ARMS the trigger — no order ever fires from here). Does NOT
commit unless the underlying activate path commits; documents its txn behaviour.

Returned shape (FROZEN — see :func:`deploy_expression`)::

    {
      "workflow_id": "...",
      "name": "...",
      "status": "draft" | "active",
      "activated": false,
      "expression_id": "...",
      "timing_mode": "confirmation",
      "steps": [{"step_type": "...", "label": "...", "config": {...}}, ...],
      "requires_approval": true,            # every order/action step gated
      "register_not_execute": true,
      "leverage_note": "<commodity note>" | None,
      "note": "Armed, not executed — you confirm and place each order …"
    }

register-not-execute is enforced end-to-end: every order/option/basket step
carries ``requires_approval=True`` and live option strategies deploy with
``book='live'`` — Pivot arms the trigger and NEVER places an order.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from backend.view_markets.expressions import commodities as _commodities
from backend.view_markets.expressions import config_schema as _cfg

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import MarketView, ViewExpression
    from backend.view_markets.expressions.timing import TimingMode


# The action step type each expression kind deploys to (the workflow-engine
# action that expresses the structure). Order legs / option strategies / baskets
# all carry ``requires_approval=True`` — register-not-execute.
ACTION_STEP_BY_KIND: dict[str, str] = {
    "basket": "action.allocate_basket",
    "multi_asset": "action.allocate_basket",
    "pair": "action.place_order",            # two legs (long + honest short)
    "option_strategy": "action.place_option_strategy",
    "hedge": "action.place_option_strategy",
}

# Every emitted order/action step sets this — Pivot arms, the user executes.
REQUIRES_APPROVAL: bool = True

# Sizing is a USER decision at approval time — register-not-execute never
# fabricates a rupee notional, and commodities are NEVER auto-sized. The basket
# / order notional defaults to this Mustache placeholder the user fills in their
# broker app (or via an upstream fetch.buying_power ref). A concrete number is
# used only when the builder already computed one from a user-supplied capital.
_CAPITAL_REF: str = "{{ inputs.capital_inr }}"

# ``action.allocate_basket`` legs only carry an equity exchange — a leveraged MCX
# commodity leg can't ride in an equity basket and is surfaced honestly instead.
_EQUITY_EXCHANGES: frozenset[str] = frozenset({"NSE", "BSE"})

# honest_short modes that deploy to a defined-risk option (vs a future short).
_OPTION_SHORT_TEMPLATE: dict[str, str] = {
    "put": "long_put",
    "index_put": "long_put",
    "commodity_put": "long_put",
    "put_spread": "bear_put_spread",
}
_FUTURE_SHORT_MODES: frozenset[str] = frozenset(
    {"ssf_future", "index_future", "commodity_future"}
)

# Closing note stamped on every armed draft (register-not-execute, plain words).
_ARMED_NOTE: str = (
    "Armed, not executed — Pivot registers the trigger; you confirm and place "
    "each order in your own broker app. Nothing is auto-executed."
)


# ── helpers ───────────────────────────────────────────────────────────────


def _kind(expression: "ViewExpression") -> str:
    """The expression kind as a plain string (handles the ORM enum)."""
    k = expression.expression_kind
    return str(getattr(k, "value", k))


def _load_view(db: "Session", expression: "ViewExpression") -> Optional["MarketView"]:
    """Best-effort load of the parent view (for timing recompute). Never raises."""
    view = getattr(expression, "view", None)
    if view is not None:
        return view
    view_id = getattr(expression, "view_id", None)
    if db is not None and view_id:
        from backend.models import MarketView

        return db.get(MarketView, view_id)
    return None


def _instrument_meta(config: dict) -> dict[str, dict]:
    """Map ``config.instruments`` by symbol → its India-typed metadata.

    Lets the basket/pair deploy read each leg's ``role`` (long/short),
    ``exchange``, ``segment``, ``tradeable`` from the single source the builders
    already populated — never re-deriving or fabricating a side."""
    meta: dict[str, dict] = {}
    for inst in config.get("instruments") or []:
        if not isinstance(inst, dict):
            continue
        sym = inst.get("symbol")
        if isinstance(sym, str) and sym:
            meta.setdefault(sym, inst)
    return meta


def _has_commodity_leg(config: dict) -> bool:
    """True when any instrument is an MCX commodity (leveraged) leg."""
    for inst in config.get("instruments") or []:
        if not isinstance(inst, dict):
            continue
        if _cfg.is_commodity_segment(inst.get("segment")):
            return True
        if inst.get("instrument_type") in _cfg.COMMODITY_INSTRUMENT_TYPES:
            return True
    return False


def _leverage_note(config: dict, structure: dict) -> Optional[str]:
    """The commodity leverage-risk note when (and only when) a commodity leg is
    present — the convention every commodity expression must carry. Prefers the
    note the builder already stamped on ``structure``; never fabricated."""
    note = structure.get("leverage_note")
    if isinstance(note, str) and note:
        return note
    if _has_commodity_leg(config):
        return _commodities.LEVERAGE_NOTE
    return None


def _timing_spec(
    db: "Session",
    expression: "ViewExpression",
    config: dict,
    timing_mode: Optional["TimingMode"],
) -> dict:
    """Resolve the trigger SPEC (``timing.timing_to_trigger`` shape).

    Uses the stored ``config.timing`` (stamped by dispatch) unless ``timing_mode``
    overrides it, in which case the mapper is re-run against the parent view. A
    missing/empty timing block degrades to a single immediate ``pre_position``
    tranche so the draft is always armable."""
    from backend.view_markets.expressions import timing as _timing

    stored = config.get("timing") if isinstance(config.get("timing"), dict) else None
    if timing_mode is None and stored and stored.get("tranches"):
        return stored

    view = _load_view(db, expression)
    mode = timing_mode or (stored or {}).get("mode") or "pre_position"
    if view is not None:
        return _timing.timing_to_trigger(view, mode)  # type: ignore[arg-type]
    # No view to recompute against and no usable stored spec: arm now.
    if stored and stored.get("tranches"):
        return stored
    return {
        "mode": "pre_position",
        "tranches": [{"pct": 100, "trigger": _timing._schedule_now_trigger()}],
        "invalidation": None,
        "note": _ARMED_NOTE,
    }


def _underlying_from_instruments(config: dict) -> Optional[str]:
    """The underlying symbol for an option/hedge, read from ``instruments`` —
    prefers an explicit ``role == 'underlying'`` leg, never fabricated."""
    for inst in config.get("instruments") or []:
        if isinstance(inst, dict) and inst.get("role") == "underlying":
            sym = inst.get("symbol")
            if isinstance(sym, str) and sym:
                return sym
    return None


# ── per-kind action-step synthesis ────────────────────────────────────────


def _basket_action(
    config: dict, structure: dict, *, label: str
) -> tuple[dict, list[str]]:
    """One ``action.allocate_basket`` step from ``structure.weights`` (AVOID /
    untradeable / leveraged-MCX names dropped, never shorted-by-accident)."""
    meta = _instrument_meta(config)
    legs: list[dict] = []
    dropped: list[str] = []
    for sym, weight in (structure.get("weights") or {}).items():
        try:
            w = float(weight)
        except (TypeError, ValueError):
            continue
        if w <= 0:
            continue
        info = meta.get(sym, {})
        if info.get("tradeable") is False:
            dropped.append(sym)
            continue
        if _cfg.is_commodity_segment(info.get("segment")):
            # A leveraged MCX leg can't ride an equity basket — surface it.
            dropped.append(sym)
            continue
        exch = info.get("exchange")
        if exch not in _EQUITY_EXCHANGES:
            exch = "NSE"
        side = "short" if info.get("role") == "short" else "long"
        legs.append(
            {"symbol": sym, "weight": round(min(w, 1.0), 6), "side": side,
             "exchange": exch}
        )
    if not legs:
        raise ValueError(
            "basket expression has no tradeable equity legs to arm"
        )
    cfg = {
        "legs": legs,
        "total_inr": _CAPITAL_REF,
        "order_type": "market",
        "requires_approval": REQUIRES_APPROVAL,
    }
    return {"step_type": "action.allocate_basket", "label": label, "config": cfg}, dropped


def _multi_asset_action(
    config: dict, structure: dict, *, label: str
) -> tuple[dict, list[str]]:
    """One ``action.allocate_basket`` across the equity + gold/silver-ETF sleeves.
    The leveraged MCX commodity sleeve + the premium-financed hedge overlay can't
    be equity-basket legs — they are surfaced as ``skipped`` (armed separately),
    never silently dropped."""
    legs: list[dict] = []
    skipped: list[str] = []
    for sleeve in structure.get("sleeves") or []:
        if not isinstance(sleeve, dict):
            continue
        kind = sleeve.get("kind")
        try:
            sw = float(sleeve.get("weight") or 0.0)
        except (TypeError, ValueError):
            sw = 0.0
        detail = sleeve.get("detail") or {}
        if kind == "equity_basket":
            inner = (detail.get("structure") or {}).get("weights") or {}
            for sym, weight in inner.items():
                try:
                    ww = round(float(weight) * sw, 6)
                except (TypeError, ValueError):
                    continue
                if ww <= 0:
                    continue
                legs.append(
                    {"symbol": sym, "weight": min(ww, 1.0), "side": "long",
                     "exchange": "NSE"}
                )
        elif kind == "gold_etf":
            sym = detail.get("symbol")
            if isinstance(sym, str) and sym and sw > 0:
                legs.append(
                    {"symbol": sym, "weight": round(min(sw, 1.0), 6),
                     "side": "long", "exchange": "NSE"}
                )
        else:
            # commodity_future / direct MCX sleeve, hedge overlay, etc.
            sym = detail.get("symbol") or kind
            if sym:
                skipped.append(str(sym))
    if not legs:
        raise ValueError(
            "multi-asset expression has no equity/ETF sleeve legs to arm"
        )
    cfg = {
        "legs": legs,
        "total_inr": _CAPITAL_REF,
        "order_type": "market",
        "requires_approval": REQUIRES_APPROVAL,
    }
    return {"step_type": "action.allocate_basket", "label": label, "config": cfg}, skipped


def _option_action(config: dict, structure: dict, *, label: str) -> dict:
    """One ``action.place_option_strategy`` (``book='live'`` register-not-execute)
    for an option_strategy / hedge. Underlying + template come from the structure
    (MCX commodity underlyings route here too, now tradeable)."""
    template = structure.get("template") or structure.get("hedge_template")
    underlying = (
        structure.get("underlying")
        or structure.get("underlying_index")
        or _underlying_from_instruments(config)
    )
    if not template or not underlying:
        raise ValueError(
            "option/hedge expression is missing a deployable underlying or "
            "template in config.structure"
        )
    expiry_rule = structure.get("expiry_rule")
    if expiry_rule not in ("nearest", "next", "monthly"):
        expiry_rule = "nearest"
    try:
        qty_lots = max(1, int(structure.get("qty_lots") or 1))
    except (TypeError, ValueError):
        qty_lots = 1
    cfg: dict[str, Any] = {
        "underlying": str(underlying),
        "template": str(template),
        "expiry_rule": expiry_rule,
        "qty_lots": qty_lots,
        "book": "live",
        "requires_approval": REQUIRES_APPROVAL,
    }
    strikes = structure.get("strikes")
    if isinstance(strikes, list) and strikes:
        cfg["strikes"] = [float(s) for s in strikes]
    return {"step_type": "action.place_option_strategy", "label": label, "config": cfg}


def _pair_actions(config: dict, structure: dict, *, label: str) -> list[dict]:
    """The long ``a`` leg + the HONEST short of ``b`` (``structure.short_leg``):
    a future short → ``action.place_order(side='short')``; a defined-risk option
    short → ``action.place_option_strategy``; an AVOID short → the long leg + a
    ``notify.message`` that explains the one-sided expression (never a fabricated
    short). Commodity legs carry approval + are never auto-sized."""
    a = structure.get("a")
    b = structure.get("b")
    if not a:
        raise ValueError("pair expression is missing leg 'a' in config.structure")
    leg_a = structure.get("leg_a") or {}
    notional = leg_a.get("notional")
    long_notional: Any = (
        float(notional)
        if isinstance(notional, (int, float)) and notional > 0
        else _CAPITAL_REF
    )
    steps: list[dict] = [
        {
            "step_type": "action.place_order",
            "label": f"{label} — long {a}",
            "config": {
                "symbol": str(a),
                "side": "buy",
                "notional_inr": long_notional,
                "order_type": "market",
                "product": "CNC",
                "requires_approval": REQUIRES_APPROVAL,
            },
        }
    ]

    short = structure.get("short_leg") or {}
    mode = short.get("mode")
    instrument = short.get("instrument") or b
    if mode == "avoid" or not mode or not instrument:
        # No tradeable short exists → arm the long leg only + say so honestly.
        steps.append(
            {
                "step_type": "notify.message",
                "label": f"{label} — short {b or 'leg'} unavailable",
                "config": {
                    "channel": "push",
                    "template": (
                        str(short.get("note"))
                        if short.get("note")
                        else (
                            f"No tradeable short for {b or 'the second leg'} — "
                            "this is a one-sided (long-only) expression."
                        )
                    ),
                },
            }
        )
    elif mode in _OPTION_SHORT_TEMPLATE:
        steps.append(
            {
                "step_type": "action.place_option_strategy",
                "label": f"{label} — short {b} (defined-risk {mode})",
                "config": {
                    "underlying": str(b or instrument),
                    "template": _OPTION_SHORT_TEMPLATE[mode],
                    "expiry_rule": "nearest",
                    "qty_lots": 1,
                    "book": "live",
                    "requires_approval": REQUIRES_APPROVAL,
                },
            }
        )
    else:  # a future short (ssf / index / commodity)
        steps.append(
            {
                "step_type": "action.place_order",
                "label": f"{label} — short {instrument}",
                "config": {
                    "symbol": str(instrument),
                    "side": "short",
                    # Never auto-size — the user sets the lots/qty at approval.
                    "notional_inr": _CAPITAL_REF,
                    "order_type": "market",
                    "product": "CNC",
                    "requires_approval": REQUIRES_APPROVAL,
                },
            }
        )
    return steps


def _action_steps(
    kind: str, config: dict, structure: dict, *, label: str
) -> tuple[list[dict], list[str]]:
    """Synthesize the kind's action step(s) + any honestly-skipped leg names."""
    if kind == "basket":
        step, dropped = _basket_action(config, structure, label=label)
        return [step], dropped
    if kind == "multi_asset":
        step, skipped = _multi_asset_action(config, structure, label=label)
        return [step], skipped
    if kind in ("option_strategy", "hedge"):
        return [_option_action(config, structure, label=label)], []
    if kind == "pair":
        return _pair_actions(config, structure, label=label), []
    raise ValueError(f"cannot deploy unknown expression kind {kind!r}")


def deploy_expression(
    db: "Session",
    expression: "ViewExpression",
    *,
    timing_mode: Optional["TimingMode"] = None,
    activate: bool = False,
    user_id: Optional[int] = None,
) -> dict:
    """Build (and optionally activate) the armed workflow draft for an expression.

    Reads the trigger SPEC from ``expression.config["timing"]`` (or recomputes via
    ``timing.timing_to_trigger(expression.view, timing_mode)`` when ``timing_mode``
    overrides the stored mode), synthesizes the ``{step_type, label, config}`` step
    list (trigger branch(es) per tranche + the kind's action step via
    :data:`ACTION_STEP_BY_KIND` + the optional invalidation exit branch), validates
    it through the workflow registry (``routers.workflows._validate_steps`` /
    ``backend.workflows.schemas``), and persists a ``Workflow`` (status ``draft``,
    ``user_id``) with its ``WorkflowStep`` rows. Links
    ``expression.workflow_id = workflow.id`` (soft ref).

    When ``activate`` is True, transitions the workflow to ``active`` via the real
    activate path (re-validate steps + ``upsert_workflow_schedule``) — this only
    ARMS the trigger; **no order is ever placed**. Every order/option/basket step
    carries ``requires_approval=True``; live option strategies use ``book='live'``.
    Commodity actions route to the MCX underlying (now tradeable) and surface the
    leverage note; commodity sizing is NEVER auto-computed.

    Returns the deploy dict above. Raises ``ValueError`` (surfaced as a 422 by the
    caller) when the expression cannot be deployed (e.g. an un-tradeable/AVOID-only
    structure with no armable action). Txn: persistence flushes; ``activate``
    follows the activate route's commit semantics (documented at call site).
    """
    # Heavy / cyclic imports kept local (routers.workflows pulls in FastAPI).
    from backend.models import Workflow, WorkflowStatus, WorkflowStep
    from backend.routers.workflows import _validate_steps
    from backend.workflows.scheduler import (
        InvalidCronError,
        upsert_workflow_schedule,
    )

    kind = _kind(expression)
    config = dict(expression.config or {})
    structure = config.get("structure") or {}
    if not isinstance(structure, dict):
        structure = {}

    label = config.get("label") or f"{kind} expression"
    leverage_note = _leverage_note(config, structure)

    spec = _timing_spec(db, expression, config, timing_mode)
    tranches = spec.get("tranches") or []
    if not tranches:
        from backend.view_markets.expressions import timing as _timing

        tranches = [{"pct": 100, "trigger": _timing._schedule_now_trigger()}]

    # ── synthesize the {step_type, label, config} list ──────────────────────
    # Per tranche: the trigger branch + the kind's approval-gated action step(s).
    # Sizing is NOT scaled per tranche (never auto-size — esp. commodities); the
    # tranche % rides in the label so the user sizes each leg at approval.
    steps: list[dict] = []
    deferred: list[str] = []
    for tranche in tranches:
        trig = tranche.get("trigger") or {}
        if not trig.get("step_type"):
            continue
        try:
            pct = int(tranche.get("pct") or 100)
        except (TypeError, ValueError):
            pct = 100
        steps.append(
            {
                "step_type": trig["step_type"],
                "label": f"{label} — trigger ({pct}%)",
                "config": dict(trig.get("config") or {}),
            }
        )
        actions, skipped = _action_steps(
            kind, config, structure, label=f"{label} ({pct}%)"
        )
        steps.extend(actions)
        deferred = skipped  # identical across tranches; keep the last

    # ── invalidation (thesis-break) exit branch — notify-only, never an
    # auto-square-off order (register-not-execute) ──────────────────────────
    invalidation = spec.get("invalidation")
    if isinstance(invalidation, dict) and invalidation.get("step_type"):
        steps.append(
            {
                "step_type": invalidation["step_type"],
                "label": f"{label} — thesis-break exit",
                "config": dict(invalidation.get("config") or {}),
            }
        )
        steps.append(
            {
                "step_type": "notify.message",
                "label": f"{label} — thesis broke",
                "config": {
                    "channel": "push",
                    "template": (
                        str(invalidation.get("note"))
                        if invalidation.get("note")
                        else "Thesis-break trigger fired — square off in your "
                        "broker app (Pivot does not auto-exit)."
                    ),
                },
            }
        )

    # Validate every step against the registry (same path the REST create uses).
    _validate_steps(steps)

    # ── persist the draft Workflow + steps (flush only; caller owns txn) ────
    resolved_user_id = (
        user_id
        if user_id is not None
        else getattr(_load_view(db, expression), "user_id", None)
    )
    if resolved_user_id is None:
        raise ValueError(
            "deploy_expression requires a user_id to own the armed workflow"
        )

    note = _ARMED_NOTE
    if leverage_note:
        note = f"{note} {leverage_note}"
    if deferred:
        note = (
            f"{note} Leveraged/overlay leg(s) not armed in the basket "
            f"({', '.join(sorted(set(deferred)))}) — arm separately."
        )

    wf = Workflow(
        user_id=resolved_user_id,
        name=str(label)[:255],
        description=note,
        status=WorkflowStatus.draft,
        single_instance=True,
    )
    db.add(wf)
    db.flush()
    for idx, s in enumerate(steps):
        db.add(
            WorkflowStep(
                workflow_id=wf.id,
                step_index=idx,
                step_type=s["step_type"],
                config=s.get("config") or {},
                label=s.get("label"),
            )
        )
    db.flush()

    # Link the soft workflow_id back onto the expression row.
    expression.workflow_id = str(wf.id)  # type: ignore[assignment]
    db.flush()

    activated = False
    if activate:
        # Arms the trigger ONLY — no order ever fires from here.
        _validate_steps(steps)
        wf.status = WorkflowStatus.active  # type: ignore[assignment]
        wf.activated_at = datetime.now(timezone.utc)  # type: ignore[assignment]
        try:
            upsert_workflow_schedule(db, wf)
        except InvalidCronError as exc:
            raise ValueError(f"cannot arm schedule: {exc}") from exc
        db.flush()
        activated = True

    return {
        "workflow_id": str(wf.id),
        "name": wf.name,
        "status": wf.status.value,
        "activated": activated,
        "expression_id": str(getattr(expression, "id", "") or ""),
        "timing_mode": spec.get("mode"),
        "steps": steps,
        "requires_approval": REQUIRES_APPROVAL,
        "register_not_execute": True,
        "leverage_note": leverage_note,
        "deferred_legs": deferred,
        "note": note,
    }


__all__ = [
    "ACTION_STEP_BY_KIND",
    "REQUIRES_APPROVAL",
    "deploy_expression",
]
