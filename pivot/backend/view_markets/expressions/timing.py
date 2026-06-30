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
  * **Confirmation** → event/print-gated. ``trigger.scheduled_macro`` (RBI MPC /
    CPI, outcome-verified) or ``trigger.event`` / ``trigger.indicator`` (e.g.
    theme ETF > 200-DMA), 0% until the trigger fires, then tranche in. Safer
    retail default.
  * **Hybrid** → split TRANCHE LADDER (canonical 50/30/20): a small starter
    ``trigger.schedule`` now + an armed ladder of ``trigger.indicator`` /
    ``trigger.price`` adds (MA cross / breakout-with-volume / higher-high), each
    add smaller than the last, with a trailing aggregate stop.

PROGA / flag-gating: prediction-market triggers (``trigger.polymarket`` /
``trigger.kalshi``) stay flag-gated + PROGA-hidden — this mapper NEVER emits one;
the "what's priced in" read surfaces Pivot's OWN option-implied probability
(``implied_move``) instead. Confirmation gating uses ``scheduled_macro`` /
``event`` / ``indicator`` only.

The output is a plain dict SPEC (validated against the real trigger configs in
INTEGRATE, not here) so dispatch can stash it on ``config.timing`` and Phase 4
can hand it to the workflow create path unchanged.

Functions raise ``NotImplementedError`` in the skeleton; the spec shape is frozen.
"""
from __future__ import annotations

import re
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

# Verifier confidence floor reused across macro/event gates (mirrors the
# news-classifier + scheduled-macro defaults in workflows.schemas).
_MIN_CONFIDENCE = 0.85

# Rate-decision outcome keyword map (rbi_mpc / us_fomc). Order matters: the
# first family with a keyword hit wins, so an explicit "cut" beats a stray
# "hold the line" idiom.
_RATE_OUTCOME_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cut", ("cut", "ease", "easing", "lower", "dovish", "reduc")),
    ("hike", ("hike", "raise", "tighten", "hawkish", "increase")),
    ("hold", ("hold", "pause", "unchanged", "status quo", "steady", "on hold")),
)

# Thesis-break = the clearly-opposite rate decision. "hold" has no single
# opposite (a break could be either way) → no scheduled-macro invalidation.
_OPPOSITE_RATE_OUTCOME: dict[str, str] = {"cut": "hike", "hike": "cut"}

# Stopwords dropped when distilling a view title into news keywords.
_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "at", "in", "on", "of", "to", "for", "and", "or", "vs",
    "over", "into", "next", "with", "by", "from", "is", "are", "be", "will",
    "this", "that", "its", "as", "than", "beats", "outperforms",
})


def _view_type_value(view: "MarketView") -> str:
    """Return the view-type as a plain string (handles the ORM enum)."""
    return str(getattr(view.view_type, "value", view.view_type))


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


def _keywords(view: "MarketView") -> list[str]:
    """Distil the view title into NewsAPI keywords (always ≥1 non-empty)."""
    title = str(view.title or "").strip()
    tokens = [
        t for t in re.findall(r"[A-Za-z][A-Za-z&]+", title)
        if t.lower() not in _STOPWORDS and len(t) > 2
    ]
    kws = tokens[:6]
    if not kws:
        kws = [title] if title else ["market view"]
    return kws


def _detect_rate_macro(view: "MarketView") -> tuple[str, str] | None:
    """Resolve an EVENT view to a (kind, expected_outcome) rate-decision pair.

    Only the rate kinds (``rbi_mpc`` / ``us_fomc``) are mapped to
    ``trigger.scheduled_macro`` here: they judge the cut/hold/hike decision
    directly. CPI/print kinds need a numeric comparison+threshold we must NOT
    fabricate, so those degrade to ``trigger.event`` instead. Returns ``None``
    when neither a kind nor a directional outcome can be read confidently.
    """
    text = _view_text(view)
    if "fomc" in text or "fed" in text or "federal reserve" in text:
        kind = "us_fomc"
    elif "rbi" in text or "mpc" in text or "repo" in text:
        kind = "rbi_mpc"
    else:
        return None
    for outcome, kws in _RATE_OUTCOME_KEYWORDS:
        if any(k in text for k in kws):
            return kind, outcome
    return None


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


def _event_news_trigger(view: "MarketView") -> dict[str, Any]:
    """``trigger.event`` news/classifier gate for a non-macro EVENT view."""
    return {
        "step_type": "trigger.event",
        "config": {
            "keywords": _keywords(view),
            "event_description": str(view.thesis or view.title or "")[:480]
            or "the view's resolving event",
            "min_confidence": _MIN_CONFIDENCE,
        },
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
    """The gate Confirmation waits on, by view type (NEVER a prediction market).

    EVENT → ``scheduled_macro`` (rate decision) when resolvable, else a news
    ``event`` gate. THEME / RELATIVE → an ``indicator`` trend confirmation on
    the theme ETF proxy (or the NIFTY regime).
    """
    vt = _view_type_value(view)
    if vt == "event":
        macro = _detect_rate_macro(view)
        if macro is not None:
            kind, outcome = macro
            return {
                "step_type": "trigger.scheduled_macro",
                "config": {
                    "kind": kind,
                    "expected_outcome": outcome,
                    "min_confidence": _MIN_CONFIDENCE,
                },
            }
        return _event_news_trigger(view)
    # theme + relative: a trend-regime confirmation cross.
    return _indicator_trend_trigger(view, value=50.0)


def timing_to_trigger(view: "MarketView", mode: TimingMode) -> dict[str, Any]:
    """Map a view + timing mode to a deploy SPEC (trigger shape, NOT a workflow).

    Returns a dict of the form::

        {
          "mode": "confirmation",
          "tranches": [{"pct": 100, "trigger": {"step_type": "trigger.scheduled_macro",
                                                 "config": {...}}}],
          "rebalance": None,                     # echoed from tier knobs by dispatch
          "invalidation": {...},                 # thesis-break exit (event/scheduled_macro)
          "note": "armed, not executed — you confirm each order in your broker app"
        }

    * **pre_position** → one tranche armed NOW via ``trigger.schedule`` (one-time
      ``run_at``); belief-led, full first tranche.
    * **confirmation** → one tranche, 0% until :func:`_confirmation_trigger` fires
      (EVENT ``scheduled_macro``/``event``; THEME/RELATIVE ``indicator``).
    * **hybrid** → the :data:`TRANCHE_LADDER` (50/30/20): a starter armed now, then
      the confirmation gate, then a stronger follow-through ``indicator`` add — each
      add smaller than the last.

    NEVER emits ``trigger.polymarket`` / ``trigger.kalshi`` (PROGA-hidden). No DB
    writes, no workflow creation — the SPEC is validated downstream against the
    real ``backend.workflows.schemas`` trigger configs.
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


def invalidation_spec(view: "MarketView") -> dict[str, Any] | None:
    """Build the thesis-break (invalidation) exit SPEC, distinct from a price stop.

    * **THEME** — wire ``thematic_map``'s per-scenario ``invalidate`` condition as
      a ``trigger.event`` news gate (policy reversal / order-book collapse). Only
      when the seed map recognises the scenario; else ``None``.
    * **EVENT** — the clearly-opposite rate decision as a ``trigger.scheduled_macro``
      (a "cut" thesis breaks on a "hike"). ``None`` for ``hold`` (no single
      opposite) or a non-macro event.

    Returns ``None`` when the view carries no checkable thesis-break.
    """
    vt = _view_type_value(view)
    if vt == "theme":
        from backend.services.thematic_map import detect_thematic_scenario

        scen = detect_thematic_scenario(
            " ".join(str(p) for p in (view.title, view.thesis) if p)
        )
        if scen is None:
            return None
        return {
            "step_type": "trigger.event",
            "config": {
                "keywords": _keywords(view),
                "event_description": str(scen.invalidate)[:480],
                "min_confidence": _MIN_CONFIDENCE,
            },
            "note": "thesis-break exit (scenario invalidation) — not a price stop",
        }
    if vt == "event":
        macro = _detect_rate_macro(view)
        if macro is None:
            return None
        kind, outcome = macro
        opposite = _OPPOSITE_RATE_OUTCOME.get(outcome)
        if opposite is None:
            return None
        return {
            "step_type": "trigger.scheduled_macro",
            "config": {
                "kind": kind,
                "expected_outcome": opposite,
                "min_confidence": _MIN_CONFIDENCE,
            },
            "note": "thesis-break exit: the opposite macro outcome — not a price stop",
        }
    return None


__all__ = [
    "TimingMode",
    "TRANCHE_LADDER",
    "timing_to_trigger",
    "invalidation_spec",
]
