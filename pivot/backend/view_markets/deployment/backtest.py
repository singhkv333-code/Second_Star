"""View Markets — Phase 4 backtest wiring: ``backtest_expression``.

Route a built :class:`~backend.models.ViewExpression` to the REAL backtest engine
for its kind, run the FULL **Trust Battery** (forward-stats PSR/MinTRL/DSR +
Monte-Carlo block-bootstrap + sub-period concentration + trial-deflated verdict),
and ATTACH the result onto the row:

  * ``ViewExpression.backtest_run_id`` (soft uuid ref — the run's identity),
  * ``ViewExpression.config["scores"]["trust"]`` (the :data:`TRUST_BLOCK_KEYS`
    envelope below — verdict + headline metrics + the gated expression dial),
  * and, when the parent view is present, the **Phase-2 expression confidence
    dial** (``confidence.score_expression_dial`` → ``persist_confidence``),
    which the Trust verdict CAPS and ``insufficient_data`` SUPPRESSES.

The battery is **reused, never reinvented**: every engine already computes
``metrics.{forward_stats, monte_carlo, sub_periods, trust_verdict}`` on its own
equity curve via the identical shared primitives, so this module's job is
*routing + attachment*, not re-deriving statistics.

Routing (by ``ViewExpression.expression_kind`` → :data:`ENGINE_BY_KIND`):

  * ``basket`` / ``multi_asset`` → the **portfolio backtest** service,
    ``backend.services.backtest.portfolio.run_portfolio_backtest`` (symbols from
    ``config.structure.weights`` / the sleeve weights; ``num_trials`` deflated via
    ``trial_group``). The faithful fixed-weight alternative — backtesting the
    deploy-synthesized ``action.allocate_basket`` workflow through
    ``backtest_workflow`` — shares the identical battery, so the Trust block shape
    is engine-agnostic.
  * ``pair`` → ``backend.services.backtest.pairs.engine.run_pairs_backtest``
    (``a`` / ``b`` + ``lookback`` / ``z_entry`` / ``z_exit`` / ``z_stop`` from
    ``config.structure``). When ``config.structure.backtest_available is False``
    (a direct-MCX bullion CM4 pair built construct-only, or thin data), the engine
    is **NEVER invoked** — return an honest ``insufficient_data`` block.
  * ``option_strategy`` / ``hedge`` → the **dsl-tree / workflow backtester**,
    ``backend.services.workflow_backtester.backtest_workflow`` over the
    deploy-synthesized steps (trial-deflated via ``trial_group``). The equity
    simulator replays equity / basket legs but does NOT price option legs, so a
    pure-option expression degrades honestly (directional-underlying proxy or
    ``insufficient_data``) — it never fabricates an option equity curve.

COMMODITY HONESTY (hard rule): when MCX commodity price history is unavailable,
return an ``insufficient_data`` verdict with ``degraded=True`` + a ``data_note``
— NEVER fabricate an equity curve, a spread, or a price.

register-not-execute: this module only *evaluates* — it places no order and arms
no workflow (that is ``deploy.py``). Does NOT commit (caller owns the txn).

Skeleton: the public functions raise ``NotImplementedError``; the contract
(signatures + :data:`TRUST_BLOCK_KEYS` + :data:`ENGINE_BY_KIND` +
:data:`VERDICT_RANK`) is FROZEN here for the BUILD agents.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import ViewExpression


# Which real engine each expression kind routes to (mirrors ExpressionKind).
ENGINE_BY_KIND: dict[str, str] = {
    "basket": "portfolio",        # run_portfolio_backtest
    "multi_asset": "portfolio",   # run_portfolio_backtest (over the equity sleeve)
    "pair": "pairs",              # run_pairs_backtest
    "option_strategy": "workflow",  # backtest_workflow (dsl-tree / workflow)
    "hedge": "workflow",          # backtest_workflow
}

# Trust-verdict ordering for ranking (compare_tiers); higher == more credible.
# Mirrors confidence.VERDICT_CEILING's ladder.
VERDICT_RANK: dict[str, int] = {
    "promising": 3,
    "unproven": 2,
    "no_edge": 1,
    "insufficient_data": 0,
}

# The FROZEN shape of ``config["scores"]["trust"]`` that backtest_expression
# writes (and compare/deploy read). Every key is always present; values are
# ``None`` where honestly undefined — no key is ever omitted, no number faked.
TRUST_BLOCK_KEYS: tuple[str, ...] = (
    "verdict",          # insufficient_data | no_edge | unproven | promising
    "label",            # human verdict label (trust_verdict.label)
    "confidence",       # 0..100 P(edge is real) (trust_verdict.confidence)
    "rationale",        # plain-English why (trust_verdict.rationale)
    "flags",            # list[str] risk flags (selection_bias / drawdown_risk / …)
    "engine",           # ENGINE_BY_KIND value actually run, or "none"
    "backtest_run_id",  # soft uuid ref (mirrors the ViewExpression column)
    "metrics",          # headline numbers + the three raw battery sub-blocks
    "alignment",        # the Phase-2 expression dial (gated/suppressed by Trust)
    "degraded",         # True when data was missing → honest insufficient_data
    "data_note",        # honest reason string when degraded, else None
    "as_of",            # ISO-8601 timestamp the battery ran
)

# The headline-metrics sub-block inside trust["metrics"] (no fabrication: each is
# None when the engine couldn't produce it).
TRUST_METRICS_KEYS: tuple[str, ...] = (
    "total_return_pct",
    "max_drawdown_pct",
    "n_trades",
    "benchmark_return_pct",   # workflow / portfolio only; None for pairs
    "forward_stats",          # forward_stats_block(): psr/min_trl/deflated_sharpe/n_obs/…
    "monte_carlo",            # monte_carlo_robustness(): dd_p95 / prob_loss / … or None
    "sub_periods",            # sub_period_robustness(): concentration / … or None
)


# Honest reason strings (no fabricated numbers ever reach a card).
_OPTION_PROXY_NOTE = (
    "The historical equity simulator replays equity/basket legs but does NOT "
    "price option legs, so a Trust verdict on this option/hedge structure's own "
    "P&L isn't available — a directional-underlying proxy is informational only "
    "and is never reported here as the option payoff."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _kind_value(expression: "ViewExpression") -> str:
    """The ExpressionKind value as a plain string (enum or str-tolerant)."""
    kind = expression.expression_kind
    return str(getattr(kind, "value", kind))


def _config(expression: "ViewExpression") -> dict:
    cfg = expression.config
    return cfg if isinstance(cfg, dict) else {}


def _is_commodity_expression(cfg: dict) -> bool:
    """True when any leg is an MCX commodity (segment MCX* or a commodity type).
    These are LEVERAGED and carry the leverage note + honest-degrade when MCX
    price history is missing — we NEVER fabricate a commodity curve."""
    from backend.view_markets.expressions.config_schema import (
        COMMODITY_INSTRUMENT_TYPES,
        is_commodity_segment,
    )

    for inst in cfg.get("instruments") or []:
        if not isinstance(inst, dict):
            continue
        if is_commodity_segment(inst.get("segment")):
            return True
        if inst.get("instrument_type") in COMMODITY_INSTRUMENT_TYPES:
            return True
    return False


def _leverage_note() -> str:
    from backend.view_markets.expressions.commodities import LEVERAGE_NOTE

    return LEVERAGE_NOTE


def _portfolio_symbols(kind: str, structure: dict) -> list[str]:
    """Symbols for the portfolio engine: basket ``weights`` keys, or the
    ``equity_basket`` sleeve weights for a multi-asset expression."""
    if kind == "multi_asset":
        syms: list[str] = []
        for sleeve in structure.get("sleeves") or []:
            if not isinstance(sleeve, dict) or sleeve.get("kind") != "equity_basket":
                continue
            detail = sleeve.get("detail") or {}
            weights = detail.get("weights") or {}
            syms.extend(str(s) for s in weights.keys())
        return list(dict.fromkeys(syms))
    weights = structure.get("weights") or {}
    return [str(s) for s in weights.keys()]


def _payoff_pop(cfg: dict, kind: str) -> Optional[float]:
    """Option POP (0..1) for the expression dial, when the structure carries one.
    Percent-scaled values (>1) are normalised; nothing is invented."""
    if kind not in ("option_strategy", "hedge"):
        return None
    structure = cfg.get("structure") or {}
    pop = structure.get("pop")
    if not isinstance(pop, (int, float)):
        return None
    val = float(pop)
    if val > 1.0:
        val = val / 100.0
    return max(0.0, min(1.0, val))


def _empty_metrics() -> dict:
    return {k: None for k in TRUST_METRICS_KEYS}


def _insufficient_block(
    *, data_note: str, run_id: Optional[str], engine: str = "none",
) -> dict:
    """An honest ``insufficient_data`` Trust block — every key present, every
    number ``None`` (no fabricated curve), the dial SUPPRESSED."""
    return {
        "verdict": "insufficient_data",
        "label": "Insufficient data",
        "confidence": None,
        "rationale": data_note,
        "flags": [],
        "engine": engine,
        "backtest_run_id": run_id,
        "metrics": _empty_metrics(),
        "alignment": None,
        "degraded": True,
        "data_note": data_note,
        "as_of": _now_iso(),
    }


def _normalize_metrics(engine: str, result: Any) -> dict:
    """Pull the headline numbers + the three raw battery sub-blocks + the verdict
    out of an engine's payload into the :data:`TRUST_METRICS_KEYS` shape. The
    engines already ran the identical battery — this only reshapes, never
    re-derives."""
    metrics = result.metrics if engine == "workflow" else result["metrics"]
    n_trades = metrics.get("n_trades")
    if n_trades is None:  # the portfolio engine reports rebalances, not trades
        n_trades = metrics.get("n_rebalances")
    return {
        "total_return_pct": metrics.get("total_return_pct"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "n_trades": n_trades,
        "benchmark_return_pct": metrics.get("benchmark_return_pct"),
        "forward_stats": metrics.get("forward_stats"),
        "monte_carlo": metrics.get("monte_carlo"),
        "sub_periods": metrics.get("sub_periods"),
        "trust_verdict": metrics.get("trust_verdict"),
    }


def _apply_trial_deflation(
    norm: dict, *, trial_group: Optional[str], fingerprint_parts: tuple,
) -> dict:
    """Thread the per-session DSR selection-bias deflation through ``trial_group``
    for the engines that don't take a group natively (portfolio / pairs): record
    this variant, re-deflate the forward-stats for the group's effective N, then
    RE-RUN the (same) ``trust_verdict`` primitive so the verdict reflects the
    deflated edge. A no-op when ``trial_group`` is falsy."""
    if not trial_group:
        return norm
    fs = norm.get("forward_stats")
    if not fs:
        return norm
    from backend.services.backtest.validation.trials import (
        record_and_deflate,
        strategy_fingerprint,
    )
    from backend.services.backtest.validation.verdict import trust_verdict

    fingerprint = strategy_fingerprint(*fingerprint_parts)
    fs_deflated = record_and_deflate(fs, trial_group, fingerprint)
    norm["forward_stats"] = fs_deflated
    norm["trust_verdict"] = trust_verdict(
        forward_stats=fs_deflated,
        monte_carlo=norm.get("monte_carlo"),
        sub_periods=norm.get("sub_periods"),
        total_return_pct=norm.get("total_return_pct") or 0.0,
        n_trades=norm.get("n_trades") or 0,
    )
    return norm


def _alignment_dial(cfg: dict, kind: str, norm: dict):
    """The Phase-2 EXPRESSION dial (DIAL 2) for this backtest: the Trust verdict
    CAPS it and ``insufficient_data`` SUPPRESSES it (statistics only cap, never
    inflate). ``cost_survival`` is the engine's PSR — the engines simulate net of
    the real Indian cost model (``trading_costs``), so PSR = P(the cost-net Sharpe
    > 0) IS the net-of-cost survivability signal. ``payoff_pop`` rides along only
    for option/hedge structures. CAAR/BHAR alignment + significance are NOT
    produced at this seam — they come from the event study in the Phase-2
    ``two_dial_score`` merge; here the backtest contributes the verdict cap +
    survivability."""
    from backend.view_markets.confidence import score_expression_dial

    verdict_block = norm.get("trust_verdict") or {}
    fs = norm.get("forward_stats") or {}
    return score_expression_dial(
        verdict=verdict_block.get("verdict"),
        deflated_sharpe=fs.get("deflated_sharpe"),
        n_obs=fs.get("n_obs"),
        min_trl=fs.get("min_trl"),
        cost_survival=fs.get("psr"),
        payoff_pop=_payoff_pop(cfg, kind),
    )


def _trust_block(
    *, cfg: dict, kind: str, engine: str, run_id: str, norm: dict, commodity: bool,
) -> tuple[dict, Any]:
    """Assemble the FROZEN :data:`TRUST_BLOCK_KEYS` envelope from a real engine
    run + the gated expression dial. Returns ``(block, dial)`` so the caller can
    persist the dial as a ``view_confidence`` row."""
    verdict_block = norm.get("trust_verdict") or {}
    dial = _alignment_dial(cfg, kind, norm)
    alignment = {
        "score": dial.score,
        "letter": dial.letter,
        "suppressed": dial.suppressed,
        "verdict": dial.verdict,
        "rationale": dial.rationale,
    }
    block = {
        "verdict": verdict_block.get("verdict"),
        "label": verdict_block.get("label"),
        "confidence": verdict_block.get("confidence"),
        "rationale": verdict_block.get("rationale"),
        "flags": list(verdict_block.get("flags") or []),
        "engine": engine,
        "backtest_run_id": run_id,
        "metrics": {k: norm.get(k) for k in TRUST_METRICS_KEYS},
        "alignment": alignment,
        "degraded": False,
        "data_note": _leverage_note() if commodity else None,
        "as_of": _now_iso(),
    }
    return block, dial


def _persist(
    db: "Session", expression: "ViewExpression", run_id: str, block: dict, dial: Any,
) -> None:
    """Attach the run onto the row: ``backtest_run_id`` (soft uuid),
    ``config.scores.trust`` (re-assigned so SQLAlchemy tracks the JSON mutation),
    and the EXPRESSION ``view_confidence`` dial. Only the ``expression`` dimension
    is upserted — the ``outcome`` dial (Phase-2, derived from the analog event
    study, not from this backtest) is left untouched. Does NOT commit."""
    from backend.models import ConfidenceDimension, ViewConfidence

    expression.backtest_run_id = run_id  # type: ignore[assignment]
    cfg = dict(_config(expression))
    scores = dict(cfg.get("scores") or {})
    scores["trust"] = block
    cfg["scores"] = scores
    expression.config = cfg  # type: ignore[assignment]

    view_id = expression.view_id
    if not view_id:
        return
    score = None if dial is None or dial.score is None else dial.score
    score_frac = None if score is None else score / 100.0
    evidence = (
        dial.rationale if dial is not None else (block.get("data_note") or "")
    )
    existing = (
        db.query(ViewConfidence)
        .filter(
            ViewConfidence.view_id == view_id,
            ViewConfidence.dimension == ConfidenceDimension.expression,
        )
        .one_or_none()
    )
    if existing is None:
        db.add(
            ViewConfidence(
                view_id=view_id,
                dimension=ConfidenceDimension.expression,
                score=score_frac,
                evidence=evidence,
            )
        )
    elif not (score_frac is None and existing.score is not None):
        # Don't let a survivability-only backtest dial (suppressed → None) clobber
        # a richer existing expression dial (e.g. the Phase-2 event-study merge).
        existing.score = score_frac  # type: ignore[assignment]
        existing.evidence = evidence  # type: ignore[assignment]
    db.flush()


def backtest_expression(
    db: "Session",
    expression: "ViewExpression",
    *,
    trial_group: Optional[str] = None,
    period: Optional[str] = None,
    persist: bool = True,
) -> dict:
    """Backtest one expression through its real engine, run the Trust Battery, and
    attach the verdict + the Phase-2 expression dial onto the row.

    Routes by ``expression.expression_kind`` (see :data:`ENGINE_BY_KIND`), reusing
    each engine's already-computed ``metrics.{forward_stats, monte_carlo,
    sub_periods, trust_verdict}`` — never recomputing the battery. ``trial_group``
    threads the per-session DSR selection-bias deflation (each tier of a view is a
    distinct *variant*; ``compare_tiers`` shares one group across the three so an
    inflated in-sample Sharpe collapses). ``period`` overrides the engine default
    (else the kind's natural window).

    When ``persist`` (default), writes onto ``expression``:
      * ``backtest_run_id`` — a fresh uuid identifying this run,
      * ``config["scores"]["trust"]`` — the :data:`TRUST_BLOCK_KEYS` envelope,
      * the **expression confidence dial** via
        ``confidence.score_expression_dial(...)`` upserted as the ``expression``
        ``view_confidence`` row. Statistics only CAP, never inflate; an
        ``insufficient_data`` verdict SUPPRESSES the dial (``score=None``).
    Does NOT commit (caller owns the txn).

    Returns the trust block (also the value stored at ``config.scores.trust``).
    For a COMMODITY expression whose MCX price history is unavailable (the engine
    raises / has no spread series), returns an ``insufficient_data`` block with
    ``degraded=True`` + a ``data_note`` carrying the leverage note — and NEVER
    fabricates an equity curve. register-not-execute: this only EVALUATES; it
    places no order and arms no workflow (that is ``deploy.py``).
    """
    kind = _kind_value(expression)
    cfg = _config(expression)
    structure = cfg.get("structure") or {}
    engine = ENGINE_BY_KIND.get(kind, "none")
    commodity = _is_commodity_expression(cfg)
    run_id = str(uuid.uuid4())
    lev = _leverage_note() + " " if commodity else ""

    block: dict
    dial: Any = None

    if engine == "pairs":
        block, dial = _run_pairs(
            structure, period=period, trial_group=trial_group,
            cfg=cfg, kind=kind, run_id=run_id, commodity=commodity, lev=lev,
        )
    elif engine == "portfolio":
        block, dial = _run_portfolio(
            structure, kind=kind, period=period, trial_group=trial_group,
            cfg=cfg, run_id=run_id, commodity=commodity, lev=lev,
        )
    elif engine == "workflow":
        # option_strategy / hedge: the equity simulator can't price option legs,
        # so we degrade HONESTLY rather than fabricate an option equity curve.
        block = _insufficient_block(
            data_note=lev + _OPTION_PROXY_NOTE, run_id=run_id, engine="none",
        )
    else:
        block = _insufficient_block(
            data_note=f"No backtest engine is wired for expression kind {kind!r}.",
            run_id=run_id,
        )

    if persist:
        _persist(db, expression, run_id, block, dial)
    return block


def _run_pairs(
    structure: dict,
    *,
    period: Optional[str],
    trial_group: Optional[str],
    cfg: dict,
    kind: str,
    run_id: str,
    commodity: bool,
    lev: str,
) -> tuple[dict, Any]:
    """Route a ``pair`` to ``run_pairs_backtest`` — but NEVER when the builder
    flagged ``backtest_available is False`` (a direct-MCX construct / thin data:
    there is no spread series to simulate)."""
    if structure.get("backtest_available") is False:
        note = (
            lev
            + "Pairs backtest unavailable for this construct (direct-MCX or thin "
            "data) — built construct-only; there is no spread series to simulate."
        )
        return _insufficient_block(data_note=note, run_id=run_id), None

    a, b = structure.get("a"), structure.get("b")
    if not a or not b:
        return (
            _insufficient_block(
                data_note=lev + "Pair legs missing from the expression structure.",
                run_id=run_id,
            ),
            None,
        )

    from backend.services.backtest.pairs.engine import run_pairs_backtest

    kwargs: dict[str, Any] = {
        "lookback": int(structure.get("lookback", 60) or 60),
        "entry_z": float(structure.get("z_entry", 2.0) or 2.0),
        "exit_z": float(structure.get("z_exit", 0.5) or 0.5),
        "stop_z": float(structure.get("z_stop", 4.0) or 4.0),
    }
    if period:
        kwargs["period"] = period
    try:
        result = run_pairs_backtest(str(a), str(b), **kwargs)
    except Exception as exc:  # honest degrade — missing/thin MCX history etc.
        note = lev + f"Pairs engine could not backtest {a}/{b}: {exc}"
        return _insufficient_block(data_note=note, run_id=run_id), None

    norm = _normalize_metrics("pairs", result)
    norm = _apply_trial_deflation(
        norm,
        trial_group=trial_group,
        fingerprint_parts=("pairs", str(a), str(b), period, kwargs["lookback"]),
    )
    return _trust_block(
        cfg=cfg, kind=kind, engine="pairs", run_id=run_id, norm=norm,
        commodity=commodity,
    )


def _run_portfolio(
    structure: dict,
    *,
    kind: str,
    period: Optional[str],
    trial_group: Optional[str],
    cfg: dict,
    run_id: str,
    commodity: bool,
    lev: str,
) -> tuple[dict, Any]:
    """Route a ``basket`` / ``multi_asset`` (equity sleeve) to
    ``run_portfolio_backtest``."""
    symbols = _portfolio_symbols(kind, structure)
    if len(symbols) < 2:
        return (
            _insufficient_block(
                data_note=lev
                + "Need at least two symbols to backtest the basket; the "
                "expression structure carries fewer.",
                run_id=run_id,
            ),
            None,
        )

    from backend.services.backtest.portfolio.engine import run_portfolio_backtest

    kwargs: dict[str, Any] = {"top_n": min(5, len(symbols))}
    if period:
        kwargs["period"] = period
    try:
        result = run_portfolio_backtest(symbols, **kwargs)
    except Exception as exc:  # honest degrade — missing/thin history (e.g. MCX)
        note = lev + f"Portfolio engine could not backtest the basket: {exc}"
        return _insufficient_block(data_note=note, run_id=run_id), None

    norm = _normalize_metrics("portfolio", result)
    norm = _apply_trial_deflation(
        norm,
        trial_group=trial_group,
        fingerprint_parts=("portfolio", sorted(symbols), period),
    )
    return _trust_block(
        cfg=cfg, kind=kind, engine="portfolio", run_id=run_id, norm=norm,
        commodity=commodity,
    )


__all__ = [
    "ENGINE_BY_KIND",
    "VERDICT_RANK",
    "TRUST_BLOCK_KEYS",
    "TRUST_METRICS_KEYS",
    "backtest_expression",
]
