"""View Markets — Phase 3 timing → workflow trigger SPEC mapper.

Pre-position / Confirmation / Hybrid (spec §2 timing axis, §4.4 deployment) map
to the SHAPE of the workflow that would deploy an expression — but this module
only BUILDS THE MAPPING. It does NOT create workflows or place orders; deploy is
exercised with mocks in tests, and Phase 4 owns the real
``createWorkflow``/``activate`` call. register-not-execute is preserved: the spec
describes an *armed* workflow the user confirms.

Mapping (reuses the EXISTING trigger configs in ``backend.workflows.schemas``):

  * **Pre-position** → arm NOW. ``trigger.schedule`` with a one-time ``run_at``
    (or immediate), single larger first tranche. Belief-led.
  * **Confirmation** → trend-confirmation gated via ``trigger.indicator`` (e.g.
    theme ETF > 200-DMA), 0% until the trigger fires, then tranche in. Safer
    retail default.
  * **Hybrid** → split TRANCHE LADDER (canonical 50/30/20): a small starter
    ``trigger.schedule`` now + an armed ladder of ``trigger.indicator`` /
    ``trigger.price`` adds (MA cross / breakout-with-volume / higher-high), each
    add smaller than the last, with a trailing aggregate stop.

News / macro / prediction-market triggers are no longer supported — this
mapper only ever emits ``trigger.schedule`` / ``trigger.indicator`` /
``trigger.price``.

The output is a plain dict SPEC (validated against the real trigger configs in
INTEGRATE, not here) so dispatch can stash it on ``config.timing`` and Phase 4
can hand it to the workflow create path unchanged.

Functions raise ``NotImplementedError`` in the skeleton; the spec shape is frozen.
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from backend.models import MarketView

TimingMode = Literal["pre_position", "confirmation", "hybrid"]

# Canonical decreasing-size tranche ladder for Hybrid staged entry (spec §4.4).
TRANCHE_LADDER: tuple[int, ...] = (50, 30, 20)

# India market timezone — every emitted schedule trigger is wall-clock IST.
_IST = "Asia/Kolkata"

# Closing note stamped on every spec — register-not-execute, in user's words.
_ARMED_NOTE = (
    "Armed, not executed — Pivot registers the trigger; you confirm and place "
    "each order in your own broker app."
)

def _view_text(view: "MarketView") -> str:
    """Lower-cased title+thesis+category — the matcher input."""
    return " ".join(
        str(p) for p in (view.title, view.thesis, view.category) if p
    ).lower()


def _now_ist_iso() -> str:
    """Current wall-clock IST as an ISO-8601 string (valid run_at)."""
    try:
        from zoneinfo import ZoneInfo

        now = datetime.now(ZoneInfo(_IST))
    except Exception:  # pragma: no cover - zoneinfo always present on 3.9+
        now = datetime.now()
    return now.strftime("%Y-%m-%dT%H:%M:%S")


def _theme_symbol(view: "MarketView") -> str:
    """Resolve a tradeable trend-gate symbol for a theme/relative view.

    Prefers the theme's listed ETF proxy (``screens.THEME_ETF_PROXY``); falls
    back to ``NIFTY`` (a real, liquid broad-market regime gate) rather than
    inventing an instrument when no proxy matches.
    """
    from backend.view_markets.expressions.screens import THEME_ETF_PROXY

    text = _view_text(view)
    for theme_key, sym in THEME_ETF_PROXY.items():
        if theme_key in text:
            return sym
    return "NIFTY"


def _schedule_now_trigger() -> dict[str, Any]:
    """``trigger.schedule`` one-time fire = arm NOW (Pre-position / starter)."""
    return {
        "step_type": "trigger.schedule",
        "config": {"run_at": _now_ist_iso(), "timezone": _IST},
    }


def _indicator_trend_trigger(view: "MarketView", *, value: float) -> dict[str, Any]:
    """``trigger.indicator`` trend-confirmation gate on the theme/regime symbol.

    Expresses "the theme has confirmed its trend" with an RSI regime cross —
    a schema-valid confirmation that fabricates NO price level (the literal
    "ETF > 200-DMA" of the spec needs an absolute level we can't pin here).
    """
    return {
        "step_type": "trigger.indicator",
        "config": {
            "symbol": _theme_symbol(view),
            "indicator": "rsi",
            "period": 14,
            "operator": "crosses_above",
            "value": value,
            "timeframe": "1d",
        },
    }


def _confirmation_trigger(view: "MarketView") -> dict[str, Any]:
    """The gate Confirmation waits on. News / macro / prediction-market
    triggers are no longer supported, so every view type (EVENT / THEME /
    RELATIVE) degrades to the trend-regime ``indicator`` cross on the
    theme ETF proxy (or the NIFTY regime).
    """
    return _indicator_trend_trigger(view, value=50.0)


def timing_to_trigger(view: "MarketView", mode: TimingMode) -> dict[str, Any]:
    """Map a view + timing mode to a deploy SPEC (trigger shape, NOT a workflow).

    Returns a dict of the form::

        {
          "mode": "confirmation",
          "tranches": [{"pct": 100, "trigger": {"step_type": "trigger.indicator",
                                                 "config": {...}}}],
          "rebalance": None,                     # echoed from tier knobs by dispatch
          "invalidation": None,                  # thesis-break exit
          "note": "armed, not executed — you confirm each order in your broker app"
        }

    * **pre_position** → one tranche armed NOW via ``trigger.schedule`` (one-time
      ``run_at``); belief-led, full first tranche.
    * **confirmation** → one tranche, 0% until the trend-regime
      ``trigger.indicator`` fires.
    * **hybrid** → the :data:`TRANCHE_LADDER` (50/30/20): a starter armed now, then
      the confirmation gate, then a stronger follow-through ``indicator`` add — each
      add smaller than the last.

    No DB writes, no workflow creation — the SPEC is validated downstream against
    the real ``backend.workflows.schemas`` trigger configs.
    """
    if mode == "pre_position":
        tranches = [{"pct": 100, "trigger": _schedule_now_trigger()}]
        note = (
            "Pre-position: full first tranche armed now (belief-led, before the "
            "print). " + _ARMED_NOTE
        )
    elif mode == "confirmation":
        tranches = [{"pct": 100, "trigger": _confirmation_trigger(view)}]
        note = (
            "Confirmation: 0% until the gate fires, then enter on the digested "
            "move (safer retail default). " + _ARMED_NOTE
        )
    elif mode == "hybrid":
        starter, add_2, add_3 = TRANCHE_LADDER
        tranches = [
            {"pct": starter, "trigger": _schedule_now_trigger()},
            {"pct": add_2, "trigger": _confirmation_trigger(view)},
            {"pct": add_3, "trigger": _indicator_trend_trigger(view, value=60.0)},
        ]
        note = (
            "Hybrid: a small starter now + an armed 50/30/20 ladder, each add "
            "smaller than the last. " + _ARMED_NOTE
        )
    else:  # pragma: no cover - guarded by the TimingMode literal
        raise ValueError(
            f"unknown timing mode {mode!r}; expected pre_position|confirmation|hybrid"
        )

    return {
        "mode": mode,
        "tranches": tranches,
        # dispatch overwrites this with the tier's rebalance knob; kept here so
        # the envelope shape is complete even if dispatch is bypassed.
        "rebalance": None,
        "invalidation": invalidation_spec(view),
        "note": note,
    }


def invalidation_spec(view: "MarketView") -> dict[str, Any] | None:  # noqa: ARG001
    """Thesis-break (invalidation) exit SPEC.

    News / macro / prediction-market gates are no longer supported, so no
    invalidation trigger is emitted here — the caller falls back to the
    tier's own price / drawdown stop. Returns ``None``.
    """
    return None


__all__ = [
    "TimingMode",
    "TRANCHE_LADDER",
    "timing_to_trigger",
    "invalidation_spec",
]
