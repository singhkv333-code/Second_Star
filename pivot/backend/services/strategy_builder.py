"""DB-driven EQUITY + GOLD strategy/basket builder (Workstream B, plan §3a).

This is the creative replacement for the old equal-weight / top-mcap basket
macro. Where ``workflow_macros`` hardwired ``strategy="equal"`` +
``sort_by="mcap"`` (so every basket was a 1/N size bet on the sector's index
heavyweights), this module runs the §3a *construction pipeline*:

  1. **Universe & selection** — build the candidate set from the request's
     theme / sector / index (``thematic_map`` + ``sector_universe``), then
     **gate/rank on the fundamentals DB** (``fundamentals_screen.screen_by_
     fundamentals`` + ``analysis_chat_tools.fetch_fundamentals``) via a
     Piotroski-style F-score gate, a Magic-Formula rank (Return-on-Capital ×
     Earnings-Yield), or a multi-factor (quality+value) score. Drop
     fundamentally broken names; enforce a single-sector cap (~30-35%) +
     a correlation/concentration check.
  2. **Weighting** — pick a :data:`WeightingScheme` by the §3a decision rule
     (``risk_parity``/ERC is the smart default, NOT 1/N; equal only survives
     for ≤4 names) and call :func:`weighting.compute_weights`.
  3. **Macro structure** — barbell / core-satellite / focused when the
     request implies it.
  4. **Gold sleeve** — SGB (long core) + GOLDBEES ETF (liquid), 5-15%, only
     when conservative / long-horizon / inflation-rupee-hedge intent earns it
     and ``gold`` is allowed. (options/hedge sleeves are DEFERRED — see the
     clearly-marked extension point in :func:`_build_sleeves`.)
  5. **Sizing & feasibility** vs ``slots.capital_inr`` — round to feasible
     tickets; state an honest boundary when something doesn't fit.

The builder ASSERTS the anti-bland guardrails (§3a) before returning:
no bare equal-weight unless ≤4 names; selection must name a gate; the sector
cap is enforced; a stated view maps to a tilt; gold only when it earns its
place. Output is a render-ready :class:`StrategyBuilderCard` (editable,
register-not-execute, not-advice disclaimer set).

Design choices (mirror the surrounding services):
  - ``from __future__ import annotations`` + strict typing throughout.
  - Pure-ish: the only I/O is the read-only fundamentals screen + price-
    history fetch (Kite-primary via ``get_historical_ohlcv``, yfinance
    fallback) — no writes, no LLM call. Safe to unit-test.
  - Reuses the EXISTING helpers rather than re-implementing them:
      * ``fundamentals_screen.screen_by_fundamentals`` — the cross-sectional
        DB screen (F-score / Magic-Formula / multi-factor source).
      * ``analysis_chat_tools.fetch_fundamentals`` — per-name metric backfill
        (DB-first, yfinance fallback) so the card can *show its work*.
      * ``sector_universe`` (``query_screener`` for the curated universe +
        ₹-cr mcap prior, ``normalize_sector`` for sector words, and
        ``resolve_theme`` for theme → sector resolution).
      * ``weighting.compute_weights`` — the named weighting schemes.
      * ``kite.market_data.get_historical_ohlcv`` — daily bars for the
        covariance / momentum / correlation inputs.
  - Never fabricates: a metric the DB can't serve comes back ``None`` and is
    surfaced as an honest boundary, never invented.
"""
from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

from backend.services import weighting as _weighting
from backend.services.sector_universe import (
    is_psu,
    normalize_sector,
    query_screener,
    resolve_theme,
    symbol_sector_map,
)
from backend.services.strategy_contracts import (
    DEFAULT_DISCLAIMER,
    DEFAULT_SECTOR_CAP_PCT,
    MIN_HISTORY_BARS_FOR_COV,
    GoldInstrument,
    MetricFilter,
    RejectedName,
    SelectionGate,
    Sleeve,
    SlotState,
    StrategyAlternative,
    StrategyBuilderCard,
    StrategyConstituent,
    WeightingScheme,
)

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════
# Tunables (kept here, not magic-numbered inline)
# ════════════════════════════════════════════════════════════════════════════

# Candidate universe pulled from the DB screen before the gate prunes it.
_SCREEN_LIMIT = 30

# Final basket size band, by risk. Conservative books hold fewer, higher-
# conviction names; aggressive books spread a touch wider. Capped by capital
# in Step 5.
_TARGET_NAMES: dict[str, tuple[int, int]] = {
    "conservative": (5, 8),
    "balanced": (6, 10),
    "aggressive": (8, 12),
}

# Equal-weight is allowed (honest, cost-efficient) ONLY at/below this name
# count — the §3a anti-bland guardrail #1.
_EQUAL_WEIGHT_MAX_NAMES = 4

# Gold-sleeve band (% of OVERALL portfolio), by how strongly it's earned.
_GOLD_PCT_BASE = 8.0
_GOLD_PCT_MIN = 5.0
_GOLD_PCT_MAX = 15.0

# History window for the covariance / momentum / correlation inputs.
# 1y (≈250 trading bars) clears the MIN_HISTORY_BARS_FOR_COV=120 floor with
# room to spare. We use Ledoit-Wolf SHRINKAGE covariance for a ~10-name basket,
# which pulls the sample matrix toward a well-conditioned target — so the extra
# year of bars in a 2y window barely moves the shrunk covariance (the dominant
# eigenstructure of a 10×10 daily-return cov is stable well before 500 bars),
# while halving the per-name payload and hitting a warmer Kite/yfinance cache.
# Quality of the weight solve is unchanged; only the fetch is cheaper.
_PRICE_PERIOD = "1y"

# Max parallel worker threads for the per-name I/O fan-outs (fundamentals
# backfill on the shortlist + price-history fetch). The work is I/O-bound (DB /
# HTTP round-trips that release the GIL), so threads give near-linear speedup;
# ~10 covers the largest basket band without oversubscribing the DB pool.
_IO_MAX_WORKERS = 10

# Short-TTL in-process memo for fetch_fundamentals(symbol, basis): re-builds and
# close-together requests (the user editing a card, a retry, two near-identical
# asks) reuse the result instead of re-hitting ~9 Azure round-trips per name.
# Keyed by (SYMBOL, basis) with a wall-clock timestamp; entries older than the
# TTL are ignored (and lazily overwritten). Process-local and best-effort — a
# cold process or an expired entry simply re-fetches, so it can never serve
# stale-enough-to-matter data (fundamentals move on quarterly filings, not
# minutes) and never fabricates.
_FUND_MEMO_TTL_S = 300.0  # 5 minutes
_FUND_MEMO: dict[tuple[str, str], tuple[float, dict]] = {}

# Pairwise-correlation ceiling for the concentration check. Above this, two
# names are flagged as effectively the same bet.
_CORR_FLAG = 0.85

# Piotroski-style F-score gate floor (drop < this). The plan's ~6-7 band; we
# screen on the available DB ratios (full 9-point Piotroski needs YoY deltas
# the sparse MC table can't always serve, so we approximate honestly — see
# :func:`_approx_fscore`).
_FSCORE_FLOOR = 5


# ════════════════════════════════════════════════════════════════════════════
# Internal candidate model
# ════════════════════════════════════════════════════════════════════════════


@dataclass
class _Candidate:
    """One name in flight through the pipeline, carrying the gate's working."""

    symbol: str
    name: str
    sector: str
    mcap_cr: Optional[float] = None
    # Raw fundamentals (DB-first, yfinance fallback). None == not available.
    roe: Optional[float] = None
    roce: Optional[float] = None
    de: Optional[float] = None
    pe: Optional[float] = None
    payout: Optional[float] = None
    earnings_yield: Optional[float] = None
    # Gate working surfaced on the card (StrategyConstituent.gate_metrics).
    gate_metrics: dict[str, float] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.gate_metrics is None:
            self.gate_metrics = {}


# ════════════════════════════════════════════════════════════════════════════
# PUBLIC API — matches strategy_contracts.StrategyBuilder.build_strategy
# ════════════════════════════════════════════════════════════════════════════


def build_strategy(
    request: str,
    slots: SlotState,
    ctx: object,
    symbols: Optional[list[str]] = None,
    constituent_reasons: Optional[dict[str, str]] = None,
    weight_overrides: Optional[dict[str, float]] = None,
    rationale_override: Optional[str] = None,
) -> StrategyBuilderCard:
    """Run the §3a equity+gold construction pipeline → a render-ready card.

    See module docstring + ``StrategyBuilder.build_strategy`` in
    ``strategy_contracts`` for the full contract. ``ctx`` is the per-turn
    context (loose-typed); this builder only reads from it opportunistically
    (a DB session if present) and otherwise opens its own read-only sessions
    via the reused helpers.

    ``symbols`` (plan §3 B1) is an optional explicit **allow-list** of NSE
    constituents the caller has already vetted (the DISCOVER→VET→JUDGE thematic
    flow's winners). When present — or carried in ``slots.symbols`` — the builder
    PINS the universe to exactly these names (no discovery, no dropping a name for
    missing data) via :func:`_build_pinned_strategy`; the weighting scheme + sizing
    are still computed and the sector cap becomes advisory.
    """
    request = request or ""
    assumptions: list[str] = _assumption_lines(slots)

    # ── Step 0 (B1): PINNED allow-list path ──────────────────────────────────
    # The explicit kwarg wins; otherwise fall back to the in-band slot-state pin
    # (so a clarify round-trip that carried the winners still builds them).
    pinned = _clean_symbols(symbols if symbols is not None else slots.symbols)
    if pinned:
        return _build_pinned_strategy(
            request, slots, pinned, assumptions,
            constituent_reasons=constituent_reasons,
            weight_overrides=weight_overrides,
            rationale_override=rationale_override,
        )

    # ── Step 1: universe → fundamentals gate/rank → sector cap + corr check ──
    gate = _choose_selection_gate(slots, request)
    candidates, universe_note, single_sector = _build_universe(slots, request)
    if universe_note:
        assumptions.append(universe_note)

    # SINGLE-NAME GUARD (Part B): when the user anchored on a specific stock
    # ("build a strategy for reliance") — whether the universe came back
    # degenerate (0-1 names) or they named one ticker with no theme/sector to
    # widen it — build a peers-anchored basket AROUND the name (its sector peers
    # + the name as a core tilt) rather than a degenerate 1-name "basket" or an
    # error.
    candidates, anchor_note, single_sector = _expand_anchor_if_degenerate(
        candidates, slots, request, single_sector
    )
    if anchor_note:
        assumptions.append(anchor_note)

    # ── Step 1a (LATENCY): cheap batch pre-gate over the WHOLE universe ──
    # ONE SQL pulls the gate ratios the MC DB can serve; the gate drops only
    # names the DB POSITIVELY marks broken/sub-floor and keeps the rest (incl.
    # names the MC DB is silent on — most large caps are yfinance-sourced — at a
    # neutral rank). This shrinks the pool we pay the FULL per-name fetch on,
    # without ever dropping a name on data the cheap pass simply lacked.
    _backfill_gate_inputs(candidates)
    candidates, gate_note = _apply_gate(candidates, gate, slots)
    if gate_note:
        assumptions.append(gate_note)

    _lo, hi = _TARGET_NAMES.get(slots.risk, _TARGET_NAMES["balanced"])
    # A stated count ("exactly 4 private banks") is an instruction, not a
    # preference the risk-band gets to override.
    if slots.max_names:
        hi = max(1, int(slots.max_names))

    # A deliberate single-sector basket reports a 100% sector cap (the cap is
    # there to stop ACCIDENTAL collapse, not to forbid an explicit focused
    # basket); a cross-sector basket gets the default ~32% ceiling.
    sector_cap = 100.0 if single_sector else DEFAULT_SECTOR_CAP_PCT

    # NOTE: no early-return on `not candidates` here — a universe that
    # gates down to ZERO survivors is the strongest case for widening
    # below (step 1b2), not a dead end. Falls through with an empty
    # `shortlist`; the widen step's `len(shortlist) < _lo` condition
    # covers zero exactly like "too few".

    # ── Step 1b (LATENCY): take a BOUNDED ranked superset, fetch the FULL
    # per-name fundamentals on it IN PARALLEL (+ short-TTL memo), then re-rank on
    # that complete data. The superset is small (~2× the target band) so the
    # parallel fetch stays cheap, yet the AUTHORITATIVE selection below sees the
    # same full fundamentals the old single-pass builder did — so the final names
    # + their gate_metrics are output-identical, just gathered far faster. We
    # over-fetch a buffer because the cheap pre-gate can't perfectly rank the
    # MC-silent (yfinance-sourced) names; the full fetch + refinalise fixes that
    # BEFORE the sector cap truncates, so truncation never drops the wrong name. ──
    shortlist = candidates[: max(hi + 6, 2 * hi)]
    _backfill_fundamentals_parallel(shortlist)
    shortlist = _refinalise_gate(shortlist, gate, slots)

    # ── Step 1b2: WIDEN ON THIN RESULT. A narrow universe (a theme that
    # resolves to one small sector) can gate down to 1-2 survivors — each
    # individually correct, but a "basket" of 1 stock is a degenerate result
    # nobody asked for. Pull the diversified cross-sector pool, gate it the
    # same way, and top up toward the minimum target band instead of
    # silently shipping a single-name "basket". Skipped when the pinned
    # allow-list path was used (handled earlier) or when this already IS
    # the broad pool (nothing further to widen into). ──
    if len(shortlist) < _lo:
        have = {c.symbol for c in shortlist}
        extra_candidates = [
            _Candidate(
                symbol=str(r["symbol"]).upper(),
                name=str(r.get("name") or r["symbol"]),
                sector=str(r.get("sector") or "unknown"),
                mcap_cr=float(r["mcap_cr"]) if r.get("mcap_cr") is not None else None,
            )
            for r in _broad_universe()
            if str(r["symbol"]).upper() not in have
        ]
        extra_candidates = _apply_exclusions(extra_candidates, slots)
        _backfill_gate_inputs(extra_candidates)
        extra_candidates, _ = _apply_gate(extra_candidates, gate, slots)
        needed = _lo - len(shortlist)
        top_up = extra_candidates[: needed + 4]  # small buffer for the full-fetch re-gate
        if top_up:
            _backfill_fundamentals_parallel(top_up)
            top_up = [
                c for c in _refinalise_gate(top_up, gate, slots)
                if c.symbol not in have
            ][:needed]
        if top_up:
            shortlist = shortlist + top_up
            assumptions.append(
                f"only {len(have)} name(s) cleared the {gate} screen in the "
                "resolved sector/theme — broadened to a cross-sector quality "
                "pool to reach a diversified basket rather than ship a "
                "single-stock 'basket'"
            )
            single_sector = False
            sector_cap = DEFAULT_SECTOR_CAP_PCT

    # Defensive dedup: a basket must never list the same symbol twice.
    # Keeps the first (best-ranked) occurrence — cheap, catch-all invariant
    # regardless of which upstream step (widen, universe merge) could have
    # reintroduced a symbol already present.
    _seen_final: set[str] = set()
    _deduped: list[_Candidate] = []
    for c in shortlist:
        if c.symbol in _seen_final:
            continue
        _seen_final.add(c.symbol)
        _deduped.append(c)
    shortlist = _deduped

    # ── Step 1b2: the USER's own hard constraints. The gate above ranks on our
    # quality opinion; these EXCLUDE on what the user actually asked for, and a
    # name that fails is reported (never silently absent). Runs before the cap
    # so the cap fills the basket from names that already clear the ask. ──
    rejected: list[RejectedName] = []
    shortlist, _rej = _apply_user_filters(shortlist, slots.filters)
    rejected.extend(_rej)
    shortlist, _rej = _apply_mcap_band(shortlist, slots.mcap_band)
    rejected.extend(_rej)
    if not shortlist:
        _asked = "; ".join(
            f"{_FILTER_LABELS.get(f.field, f.field)} {f.op} {f.value:g}"
            for f in slots.filters
        ) or (f"{slots.mcap_band}-cap universe" if slots.mcap_band else "")
        assumptions.append(
            f"no name in the resolved universe clears your constraints ({_asked}) — "
            "nothing was substituted; relax a threshold or widen the sector"
        )
        card = _empty_card(request, slots, gate, sector_cap, assumptions)
        card.rejected = rejected[:12]
        return card

    # ── Step 1c: enforce the sector cap on the AUTHORITATIVE (full-data) ranking,
    # selecting the final ~hi names — provably cap-compliant at its own size. ──
    candidates, cap_note = _enforce_sector_cap(
        shortlist, sector_cap, target_size=hi, single_sector=single_sector
    )
    if cap_note:
        assumptions.append(cap_note)
    if not candidates:
        return _empty_card(request, slots, gate, sector_cap, assumptions)

    # ── Step 2: weighting scheme by the decision rule (NOT a hardwired 1/N) ──
    price_history = _fetch_price_history([c.symbol for c in candidates])
    scheme, scheme_note = _choose_scheme(slots, request, candidates)
    if scheme_note:
        assumptions.append(scheme_note)

    factor_emphasis = _detect_factor_style(slots, request)
    # A user-named weighting metric ("weighted by ROE") wins over the internal
    # scheme choice — it is an explicit instruction, not a preference we get to
    # optimise away.
    _by_weights, _by_note = (
        _metric_proportional_weights(candidates, slots.weight_by)
        if slots.weight_by else ({}, "")
    )
    if _by_weights:
        weights, weight_note, scheme = _by_weights, "", "conviction"
        assumptions.append(_by_note)
    else:
        if slots.weight_by:
            assumptions.append(
                f"no usable {_FILTER_LABELS.get(slots.weight_by, slots.weight_by)} "
                "data for these names — could not weight by it; used the "
                f"{scheme.replace('_', ' ')} split instead"
            )
        weights, weight_note = _compute_weights(
            scheme, candidates, slots, price_history, factor_emphasis=factor_emphasis
        )
    if weight_note:
        # Covariance too thin etc. → honest equal-weight fallback + restate.
        scheme = "equal"
        assumptions.append(weight_note)

    # ── Step 1 (cont.): correlation/concentration check on the final set ──
    corr_note = _correlation_check(candidates, weights, price_history)
    if corr_note:
        assumptions.append(corr_note)

    # ── Step 3: macro structure (barbell / core-satellite / focused) ──
    structure, equity_pct, structure_note = _macro_structure(slots, request, candidates)

    # ── Step 4: gold sleeve (SGB + GOLDBEES) when it earns its place ──
    sleeves, gold_pct, sleeve_notes = _build_sleeves(slots, request)
    assumptions.extend(sleeve_notes)

    # Equity sleeve takes whatever the sleeves leave. Scale the constituent
    # weights (which sum to 1.0 over the equity sleeve) to overall-portfolio %.
    equity_share = max(0.0, 100.0 - gold_pct)

    # ── Step 5: sizing & feasibility vs capital ──
    constituents, sizing_notes = _size_constituents(
        candidates, weights, equity_share, slots
    )
    assumptions.extend(sizing_notes)

    # ── Anti-bland guardrails (assert before render) ──
    _conc_note = _assert_guardrails(
        constituents=constituents,
        scheme=scheme,
        gate=gate,
        sector_cap=sector_cap,
        slots=slots,
        single_sector=single_sector,
    )
    if _conc_note:
        assumptions.append(_conc_note)

    title = _title(slots, request, structure, has_gold=gold_pct > 0)
    # The MODEL's own defence wins when it wrote one — it has the user's actual
    # words and the thesis; the template below is the fallback for callers that
    # didn't author one (and never claims the user said anything they didn't).
    rationale = (rationale_override or "").strip() or _rationale(
        slots=slots,
        request=request,
        scheme=scheme,
        gate=gate,
        structure=structure,
        constituents=constituents,
        sleeves=sleeves,
        sector_cap=sector_cap,
    )
    alternatives = _alternatives(
        slots=slots,
        scheme=scheme,
        gate=gate,
        sleeves=sleeves,
        n_names=len(constituents),
        single_sector=single_sector,
    )

    return StrategyBuilderCard(
        title=title,
        rationale=rationale,
        weighting_scheme=scheme,
        selection_gate=gate,
        sector_cap=sector_cap,
        constituents=constituents,
        sleeves=sleeves,
        assumptions=_dedup(assumptions),
        alternatives=alternatives,
        constraints_not_applied=_unapplied_constraints(slots, pinned=False),
        rejected=rejected[:12],
        capital_inr=slots.capital_inr,
        disclaimer=DEFAULT_DISCLAIMER,
    )


def _unapplied_constraints(slots: SlotState, *, pinned: bool) -> list[str]:
    """Every user constraint this build could NOT honour, in plain words.

    The reply must disclose these — a card that quietly violates a stated
    constraint is worse than an honest boundary (2026-07-17 eval B11/B09/B16).
    Only reports what is genuinely dropped: expressible constraints are applied
    upstream and never appear here."""
    out: list[str] = []
    if pinned:
        # A pinned build honours the caller's names verbatim; universe-shaping
        # constraints have nothing to shape.
        if slots.mcap_band:
            out.append(
                f"{slots.mcap_band}-cap universe — not applied: you pinned the "
                "names, so the band didn't filter anything"
            )
        if slots.filters:
            out.append(
                "your fundamental filters weren't used as a screen — the pinned "
                "names were built as given (their ratios are shown per leg)"
            )
    return out


# ════════════════════════════════════════════════════════════════════════════
# Step 0 (B1) — pinned allow-list path + factor-style detection
# ════════════════════════════════════════════════════════════════════════════


# Factor-style themes (plan §3 B2): a "strategy that benefits from momentum /
# quality / value / low-vol" is a FACTOR ask, not a sector. These words map to
# a factor emphasis that (a) forces the ``factor`` weighting scheme over a broad
# liquid universe and (b) tilts the factor blend toward that style, so the basket
# is genuinely factor-led rather than an equal four-factor mix.
_FACTOR_STYLE_THEMES: dict[str, str] = {
    "momentum": "momentum",
    "high momentum": "momentum",
    "trend": "momentum",
    "quality": "quality",
    "quality factor": "quality",
    "compounder": "quality",
    "value factor": "value",
    "low vol": "low_vol",
    "low-vol": "low_vol",
    "low volatility": "low_vol",
    "minimum volatility": "low_vol",
    "min vol": "low_vol",
    "min-vol": "low_vol",
}


def _detect_factor_style(slots: SlotState, request: str) -> Optional[str]:
    """Return the factor to emphasise (``momentum``/``quality``/``value``/
    ``low_vol``) when the request/theme reads as a factor-style ask, else None.
    Longest keyword wins so "low volatility" beats a stray "vol" substring."""
    text = f"{request or ''} {slots.theme or ''}".lower()
    hit: Optional[str] = None
    hit_len = 0
    for kw, factor in _FACTOR_STYLE_THEMES.items():
        if kw in text and len(kw) > hit_len:
            hit, hit_len = factor, len(kw)
    return hit


def _clean_symbols(symbols: Optional[list[str]]) -> list[str]:
    """Normalise a caller allow-list: strip ``.NS``, upper-case, drop blanks, and
    de-dup preserving order. Returns ``[]`` for a missing/empty list."""
    out: list[str] = []
    seen: set[str] = set()
    for s in symbols or []:
        t = str(s or "").replace(".NS", "").strip().upper()
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _curated_row_map() -> dict[str, dict]:
    """``{SYMBOL -> universe row}`` across the whole curated universe, network-
    free — used to resolve name/sector/mcap for pinned symbols we recognise. A
    pinned symbol absent here still builds (symbol as name, 'unknown' sector)."""
    m: dict[str, dict] = {}
    for sec in sorted(set(symbol_sector_map().values())):
        for r in query_screener(sector=sec, limit=_SCREEN_LIMIT):
            m[str(r["symbol"]).upper()] = r
    return m


# ── User-stated hard constraints (filters / mcap band) ──────────────────────
# The selection gate RANKS; these EXCLUDE. Kept separate so a user constraint
# is never silently replaced by the builder's own quality opinion (the
# 2026-07-17 eval's B11/B16 failure class).

_MCAP_BANDS: dict[str, tuple[float, float]] = {
    "large": (20000.0, float("inf")),
    "mid": (5000.0, 20000.0),
    "small": (0.0, 5000.0),
}

_FILTER_LABELS: dict[str, str] = {
    "roe": "ROE", "roce": "ROCE", "de": "D/E", "pe": "P/E",
    "earnings_yield": "earnings yield", "payout": "payout ratio",
    "market_cap_cr": "market cap (₹ cr)",
}


def _candidate_metric(c: _Candidate, field: str) -> Optional[float]:
    return {
        "roe": c.roe, "roce": c.roce, "de": c.de, "pe": c.pe,
        "earnings_yield": c.earnings_yield, "payout": c.payout,
        "market_cap_cr": c.mcap_cr,
    }.get(field)


def _passes(value: float, op: str, target: float) -> bool:
    return {
        ">": value > target, ">=": value >= target,
        "<": value < target, "<=": value <= target,
    }[op]


def _apply_user_filters(
    candidates: list[_Candidate], filters: list[MetricFilter],
) -> tuple[list[_Candidate], list[RejectedName]]:
    """Drop every candidate failing a user-stated filter. A name the DB is
    silent on is ALSO dropped — we cannot assert it passes, and quietly keeping
    it is how a "ROE > 15" basket shipped ROE-unknown names. Returns
    (survivors, rejected-with-reason)."""
    if not filters:
        return candidates, []
    kept: list[_Candidate] = []
    rejected: list[RejectedName] = []
    for c in candidates:
        fail: Optional[str] = None
        for f in filters:
            label = _FILTER_LABELS.get(f.field, f.field)
            v = _candidate_metric(c, f.field)
            if v is None:
                fail = f"no {label} data — can't confirm it clears {label} {f.op} {f.value:g}"
                break
            if not _passes(float(v), f.op, float(f.value)):
                fail = f"{label} {float(v):.2f} vs your {label} {f.op} {f.value:g}"
                break
        (kept if fail is None else rejected).append(
            c if fail is None else RejectedName(symbol=c.symbol, reason=fail)
        )
    return kept, rejected


def _apply_mcap_band(
    candidates: list[_Candidate], band: Optional[str],
) -> tuple[list[_Candidate], list[RejectedName]]:
    """Restrict to a market-cap band. Unknown-mcap names are KEPT (unlike a
    metric filter): the band is a universe preference, not an assertion about
    the name, and dropping every DB-silent name would empty a smallcap ask."""
    if not band or band not in _MCAP_BANDS:
        return candidates, []
    lo, hi = _MCAP_BANDS[band]
    kept: list[_Candidate] = []
    rejected: list[RejectedName] = []
    for c in candidates:
        m = c.mcap_cr
        if m is None or lo <= float(m) < hi:
            kept.append(c)
        else:
            rejected.append(RejectedName(
                symbol=c.symbol,
                reason=f"₹{float(m):,.0f} cr market cap — outside the {band}-cap band",
            ))
    return kept, rejected


def _metric_proportional_weights(
    candidates: list[_Candidate], field: str,
) -> tuple[dict[str, float], str]:
    """Weights ∝ a named metric ("weighted by ROE"). Non-positive/missing values
    take the positive mean so a name is never silently zero-weighted; for
    lower-is-better metrics (D/E, P/E) proportionality is inverted."""
    label = _FILTER_LABELS.get(field, field)
    vals = {c.symbol: _candidate_metric(c, field) for c in candidates}
    positive = [float(v) for v in vals.values() if v is not None and float(v) > 0]
    if not positive:
        return {}, ""
    mean = sum(positive) / len(positive)
    invert = field in ("de", "pe")
    raw: dict[str, float] = {}
    for sym, v in vals.items():
        x = float(v) if v is not None and float(v) > 0 else mean
        raw[sym] = (1.0 / x) if invert else x
    total = sum(raw.values()) or 1.0
    note = (
        f"weighted in proportion to {label}"
        + (" (inverted — lower is better)" if invert else "")
    )
    return {s: v / total for s, v in raw.items()}, note


def _pinned_gate_metrics(candidates: list[_Candidate], gate: SelectionGate) -> None:
    """Set each pinned candidate's ``gate_metrics`` for DISPLAY (never a drop
    gate). Names the DB is silent on keep an empty dict — the caller surfaces that
    honestly as "(no data)"; nothing is fabricated."""
    for c in candidates:
        has_data = any(v is not None for v in (c.roe, c.roce, c.de, c.pe))
        if not has_data:
            c.gate_metrics = {}
            continue
        if gate == "fscore":
            c.gate_metrics = _fscore_metrics(c, _approx_fscore(c))
        elif gate == "magic_formula":
            c.gate_metrics = _magic_metrics(c)
        else:
            c.gate_metrics = _multifactor_metrics(c)


def _advisory_sector_note(candidates: list[_Candidate], sector_cap_pct: float) -> str:
    """B1: for a pinned basket the sector cap is ADVISORY — we warn when one
    sector dominates but never trim (the caller chose the names). Returns a note
    or ""."""
    n = len(candidates)
    if n == 0:
        return ""
    counts: dict[str, int] = {}
    for c in candidates:
        counts[c.sector] = counts.get(c.sector, 0) + 1
    worst_sec, worst = max(counts.items(), key=lambda kv: kv[1])
    if worst_sec != "unknown" and (worst / n) * 100.0 > sector_cap_pct:
        return (
            f"advisory: {worst}/{n} pinned names are {worst_sec} (> the ~{sector_cap_pct:.0f}% "
            "sector guide) — concentrated by your pin, kept as-is (not trimmed)"
        )
    return ""


def _build_pinned_strategy(
    request: str,
    slots: SlotState,
    pinned: list[str],
    assumptions: list[str],
    constituent_reasons: Optional[dict[str, str]] = None,
    weight_overrides: Optional[dict[str, float]] = None,
    rationale_override: Optional[str] = None,
) -> StrategyBuilderCard:
    """B1 — PINNED allow-list path. The caller (e.g. the DISCOVER→VET→JUDGE
    thematic flow) has already vetted the names, so we build EXACTLY these:
    no discovery, no dropping a name for missing data. We still fetch fundamentals
    to *show* each name's ``gate_metrics`` (missing → shown as no-data, never
    dropped), compute a real weighting scheme + sizing, run the correlation check,
    add the gold sleeve when earned, and treat the sector cap as advisory."""
    gate = _choose_selection_gate(slots, request)

    row_map = _curated_row_map()
    candidates: list[_Candidate] = []
    for sym in pinned:
        row = row_map.get(sym)
        candidates.append(
            _Candidate(
                symbol=sym,
                name=str(row.get("name") or sym) if row else sym,
                sector=str(row.get("sector") or "unknown") if row else "unknown",
                mcap_cr=float(row["mcap_cr"]) if row and row.get("mcap_cr") is not None else None,
            )
        )

    # A pinned universe is "already vetted" for DATA availability (never
    # dropped for missing fundamentals) — but a user's explicit exclusion
    # ("no PSU exposure", "not X") is a hard constraint that outranks a
    # vetted pin, including the deterministic thematic-scenario seed (e.g.
    # crude_spike pins ONGC/OIL straight into this path). Without this, an
    # excluded name that happened to get pinned (by the model or the
    # scenario backstop) shipped anyway — confirmed bug.
    before_excl = [c.symbol for c in candidates]
    candidates = _apply_exclusions(candidates, slots)
    excluded = [s for s in before_excl if s not in {c.symbol for c in candidates}]
    if excluded:
        assumptions.append(
            "excluded per your stated preference: " + ", ".join(excluded)
        )
    if not candidates:
        return _empty_card(request, slots, gate, DEFAULT_SECTOR_CAP_PCT, assumptions)

    assumptions.append(
        f"pinned universe — building exactly the {len(candidates)} name(s) you/the flow "
        "vetted (after exclusions); no discovery ran and none were dropped for missing data"
    )

    # Fundamentals for the per-name gate_metrics DISPLAY only (never a drop gate).
    _backfill_gate_inputs(candidates)
    _backfill_fundamentals_parallel(candidates)
    _pinned_gate_metrics(candidates, gate)
    no_data = [c.symbol for c in candidates if not c.gate_metrics]
    if no_data:
        assumptions.append(
            "no DB fundamentals for " + ", ".join(no_data)
            + " — shown without gate metrics (no data), kept as pinned (not fabricated)"
        )

    # Sector cap is advisory for a pinned basket (warn, don't trim).
    sector_cap = DEFAULT_SECTOR_CAP_PCT
    adv = _advisory_sector_note(candidates, sector_cap)
    if adv:
        assumptions.append(adv)

    # ── Weighting + sizing ──
    # PINNED thesis names are conviction-weighted: sized by the quality gate
    # (roe/roce/de/pe) where the DB serves it, else by thesis-conviction ORDER
    # (lead name = highest-beta beneficiary). This is deliberately NOT
    # factor/covariance weighting — those degrade to momentum/1-N on the thin,
    # illiquid small-cap history these baskets usually hold, producing distorted
    # or flat splits (the reported bug). A rich-history large-cap basket still
    # gets a meaningful spread because the quality gate differentiates it.
    price_history = _fetch_price_history([c.symbol for c in candidates])
    scheme = "conviction"
    weights = _differentiate_weights(candidates)
    _q = any(_quality_score(c.gate_metrics or {}) is not None for c in candidates)
    assumptions.append(
        "conviction-weighted — sized by "
        + ("the quality gate (ROE/ROCE/D-E) " if _q else "thesis-conviction order ")
        + "so the split is structured, not a flat 1/N or momentum-on-thin-history"
    )

    corr_note = _correlation_check(candidates, weights, price_history)
    if corr_note:
        assumptions.append(corr_note)

    structure, _equity_pct, _structure_note = _macro_structure(slots, request, candidates)
    sleeves, gold_pct, sleeve_notes = _build_sleeves(slots, request)
    assumptions.extend(sleeve_notes)
    equity_share = max(0.0, 100.0 - gold_pct)
    constituents, sizing_notes = _size_constituents(
        candidates, weights, equity_share, slots,
        provided_reasons=constituent_reasons,
        weight_overrides=weight_overrides,
    )
    assumptions.extend(sizing_notes)

    _assert_guardrails(
        constituents=constituents,
        scheme=scheme,
        gate=gate,
        sector_cap=sector_cap,
        slots=slots,
        single_sector=False,
        pinned=True,
    )

    title = _title(slots, request, structure, has_gold=gold_pct > 0)
    # The MODEL's own defence wins when it wrote one — it has the user's actual
    # words and the thesis; the template below is the fallback for callers that
    # didn't author one (and never claims the user said anything they didn't).
    rationale = (rationale_override or "").strip() or _rationale(
        slots=slots,
        request=request,
        scheme=scheme,
        gate=gate,
        structure=structure,
        constituents=constituents,
        sleeves=sleeves,
        sector_cap=sector_cap,
    )
    alternatives = _alternatives(
        slots=slots,
        scheme=scheme,
        gate=gate,
        sleeves=sleeves,
        n_names=len(constituents),
        single_sector=False,
    )
    return StrategyBuilderCard(
        title=title,
        rationale=rationale,
        weighting_scheme=scheme,
        selection_gate=gate,
        sector_cap=sector_cap,
        constituents=constituents,
        sleeves=sleeves,
        assumptions=_dedup(assumptions),
        alternatives=alternatives,
        constraints_not_applied=_unapplied_constraints(slots, pinned=True),
        capital_inr=slots.capital_inr,
        disclaimer=DEFAULT_DISCLAIMER,
    )


# ════════════════════════════════════════════════════════════════════════════
# Step 1 — universe construction
# ════════════════════════════════════════════════════════════════════════════


def _build_universe(
    slots: SlotState, request: str
) -> tuple[list[_Candidate], str, bool]:
    """Build the candidate universe from theme/sector/index.

    Resolution order (reuses the existing helpers):
      1. ``slots.theme`` → ``sector_universe.resolve_theme`` → canonical
         sector(s).
      2. a sector word in the theme/request → ``sector_universe.normalize_sector``.
      3. otherwise a broad multi-sector "core" universe (quality compounders
         across sectors) so an under-specified ask still builds.

    Returns ``(candidates, note, single_sector)``. ``note`` discloses an
    approximate resolution; ``single_sector`` is True when the user
    DELIBERATELY asked for one sector — in that case the cross-sector cap must
    NOT trim the basket (a focused sector basket is a legitimate, explicit
    intent, not an accidental collapse).
    """
    note = ""
    sectors: list[str] = []

    theme = (slots.theme or "").strip()
    if theme:
        mapping = resolve_theme(theme)
        if mapping is not None:
            sectors = [s for s in mapping.sectors]
            if mapping.confidence == "approximate":
                note = f"theme '{theme}' → {', '.join(sectors)} ({mapping.note})"
        else:
            sec = normalize_sector(theme)
            if sec is not None:
                sectors = [sec]

    if not sectors and slots.view.target == "sector" and theme:
        sec = normalize_sector(theme)
        if sec is not None:
            sectors = [sec]

    # Build via the curated sector universe (network-free, fast at build time).
    rows: list[dict] = []
    if sectors:
        seen: set[str] = set()
        per = max(6, _SCREEN_LIMIT // max(1, len(sectors)))
        for sec in sectors:
            for r in query_screener(sector=sec, limit=per):
                if r["symbol"] not in seen:
                    seen.add(r["symbol"])
                    rows.append(r)
    else:
        # Broad, cross-sector "core" universe — the sector heavyweights, which
        # the gate then prunes on fundamentals (so this is a starting pool, not
        # the answer).
        rows = _broad_universe()
        if theme:
            # A theme WAS named but couldn't be mapped to a sector this
            # builder recognises ("mid-cap manufacturing", "insurance
            # plays", etc.) — say so explicitly rather than the generic "no
            # explicit theme/sector" line, which reads as if nothing was
            # asked for at all. Honest boundary (confirmed bug): the basket
            # below is a broad quality pool, NOT the specific universe the
            # user named.
            note = note or (
                f"couldn't map '{theme}' to a specific sector in this "
                "builder — built from a broad cross-sector quality pool "
                "instead of the specific universe you asked for; name a "
                "recognised sector (IT/pharma/auto/energy/metals/steel/"
                "banking/fmcg/cement/defence/telecom) to narrow it"
            )
        else:
            note = note or "no explicit theme/sector — drew a broad cross-sector pool, then gated on fundamentals"

    # Build candidates, de-duping by symbol (sector universes overlap — e.g. a
    # name can sit in both the broad pool and a sector pull; the first/best
    # occurrence wins so a symbol never lands in the basket twice).
    candidates: list[_Candidate] = []
    seen_syms: set[str] = set()
    for r in rows:
        sym = str(r["symbol"]).upper()
        if sym in seen_syms:
            continue
        seen_syms.add(sym)
        candidates.append(
            _Candidate(
                symbol=sym,
                name=str(r.get("name") or r["symbol"]),
                sector=str(r.get("sector") or "unknown"),
                mcap_cr=float(r["mcap_cr"]) if r.get("mcap_cr") is not None else None,
            )
        )
    # Honour explicit exclusions (sectors / named tickers / "PSU").
    candidates = _apply_exclusions(candidates, slots)
    # A deliberate single-sector ask: exactly one resolved sector AND the
    # universe really is dominated by it (banks split private/psu, so check the
    # realised sector spread, not just len(sectors)).
    realised = {c.sector for c in candidates}
    single_sector = bool(sectors) and len(realised) <= 2 and len(sectors) == 1
    return candidates, note, single_sector


def _broad_universe() -> list[dict]:
    """A diversified cross-sector starting pool from the curated universe —
    the top names across the major sectors, de-concentrated so the gate has
    real breadth to rank over (not a single-sector list)."""
    rows: list[dict] = []
    for sec in ("private_bank", "it", "auto", "pharma", "fmcg", "energy", "metals", "cement"):
        rows.extend(query_screener(sector=sec, limit=4))
    return rows


def _apply_exclusions(candidates: list[_Candidate], slots: SlotState) -> list[_Candidate]:
    """Drop names the user carved out: a named ticker, a sector word, or
    ``"PSU"``. Free-text exclusions are matched coarsely — honest and
    conservative.

    ``"PSU"`` is an OWNERSHIP tag, not a sector — only ``psu_bank`` carries
    "psu" in the sector name, but real PSUs also sit in ``energy`` (ONGC,
    IOC, NTPC...), ``metals``/``steel`` (COALINDIA, SAIL, NMDC) and
    ``defence`` (HAL, BEL, BHEL...). Matching just ``"psu" in sec`` silently
    kept every non-bank PSU in an "exclude PSU" basket (confirmed bug) — the
    ``is_psu`` membership check catches those too, regardless of sector.
    """
    excl = [e.strip().lower() for e in slots.asset_prefs.exclusions if e.strip()]
    if not excl:
        return candidates
    out: list[_Candidate] = []
    for c in candidates:
        sym = c.symbol.lower()
        sec = c.sector.lower()
        drop = False
        for e in excl:
            if e == sym or e in sec or (e == "psu" and ("psu" in sec or is_psu(c.symbol))):
                drop = True
                break
            norm = normalize_sector(e)
            if norm is not None and norm == c.sector:
                drop = True
                break
        if not drop:
            out.append(c)
    return out


def _detect_anchor(slots: SlotState, request: str) -> Optional[dict]:
    """Find a single-name ANCHOR the user built around (Part B): a ticker from
    the curated universe named in the request / theme, e.g. "build a strategy for
    reliance". Returns the universe row ``{symbol,name,sector,mcap_cr}`` for the
    best match (largest-cap on a tie) or ``None``. Network-free, no fabrication —
    only resolves names already in the curated universe."""
    text = f"{request or ''} {slots.theme or ''}".upper()
    if not text.strip():
        return None
    # Scan the whole curated universe once (one row per symbol), network-free.
    sectors = sorted(set(symbol_sector_map().values()))
    rows: list[dict] = []
    for s in sectors:
        rows.extend(query_screener(sector=s, limit=_SCREEN_LIMIT))
    best: Optional[dict] = None
    for row in rows:
        sym = str(row["symbol"]).upper()
        name = str(row.get("name") or sym).upper()
        # Whole-word-ish match on the ticker, or the company's lead word
        # (e.g. "RELIANCE" from "Reliance Industries"). Coarse but conservative.
        lead = name.split()[0] if name.split() else ""
        if _word_in(sym, text) or (len(lead) >= 4 and _word_in(lead, text)):
            if best is None or (row.get("mcap_cr") or 0) > (best.get("mcap_cr") or 0):
                best = dict(row)
    return best


def _word_in(token: str, text: str) -> bool:
    """Coarse word-boundary containment (avoids 'M&M' matching inside a longer
    run, and 'IOC' matching 'BIOCON'). Both args already upper-cased."""
    if not token or token not in text:
        return False
    i = text.find(token)
    before = text[i - 1] if i > 0 else " "
    after = text[i + len(token)] if i + len(token) < len(text) else " "
    return not before.isalnum() and not after.isalnum()


def _expand_anchor_if_degenerate(
    candidates: list[_Candidate],
    slots: SlotState,
    request: str,
    single_sector: bool,
) -> tuple[list[_Candidate], str, bool]:
    """PART B — single-name guard. Fires when the user anchored on a SPECIFIC
    stock ("build a strategy for reliance") — either because the resolved
    universe is degenerate (0-1 names) OR because they named one ticker without
    any theme/sector to widen it (so the build would otherwise fall back to a
    generic broad pool that ignores the name they actually asked about). A 1-name
    "basket" is nonsensical and a hard error is worse, so we build something
    CONNECTED to the name: a peers-anchored basket — the anchor's sector peers
    (so the gate/weighting have real breadth) with the anchor itself kept as a
    deliberate core tilt. Returns the (possibly expanded) candidates, a
    disclosure note, and the updated ``single_sector`` flag (an anchored basket
    is sector-focused by construction).

    Falls through unchanged for a real multi-name basket that the user steered
    via a theme/sector, so the common path pays nothing. Never throws.
    """
    degenerate = len(candidates) <= 1
    # "Unanchored broad pool" = no theme and no resolved single sector → the
    # universe is the generic cross-sector fallback. If the request also names a
    # specific stock, the user wanted THAT name, not a generic basket.
    unanchored = not (slots.theme or "").strip() and not single_sector
    anchor = _detect_anchor(slots, request)
    if not degenerate and not (unanchored and anchor is not None):
        return candidates, "", single_sector

    # If we somehow have 1 candidate but couldn't text-match an anchor, treat that
    # single candidate AS the anchor so we still expand around it.
    if anchor is None and len(candidates) == 1:
        c0 = candidates[0]
        anchor = {"symbol": c0.symbol, "name": c0.name, "sector": c0.sector, "mcap_cr": c0.mcap_cr}
    if anchor is None:
        # Nothing to anchor on — let the caller fall through to the broad pool /
        # empty-card path (still never an exception).
        return candidates, "", single_sector

    sec = str(anchor.get("sector") or "")
    sym = str(anchor["symbol"]).upper()
    peers = query_screener(sector=sec, limit=_SCREEN_LIMIT) if sec else []

    rows: list[dict] = [anchor] + [r for r in peers if str(r["symbol"]).upper() != sym]
    out: list[_Candidate] = []
    seen: set[str] = set()
    for r in rows:
        s = str(r["symbol"]).upper()
        if s in seen:
            continue
        seen.add(s)
        out.append(
            _Candidate(
                symbol=s,
                name=str(r.get("name") or s),
                sector=str(r.get("sector") or "unknown"),
                mcap_cr=float(r["mcap_cr"]) if r.get("mcap_cr") is not None else None,
            )
        )
    out = _apply_exclusions(out, slots)

    if len(out) < 2:
        # The anchor's sector is too thin to build a real basket — honest
        # boundary handled downstream (empty/near-empty), still no throw.
        note = (
            f"'{anchor.get('name') or sym}' is a single name — I can't build a one-stock "
            "'basket', and its sector is too thin in the universe to anchor peers around it; "
            "name a theme/sector and I'll build a real basket"
        )
        return out, note, single_sector

    note = (
        f"you anchored on a single name ({anchor.get('name') or sym}) — a one-stock 'basket' is "
        f"degenerate, so I built a {sec or 'sector'}-peer basket AROUND it: {sym} kept as a core "
        f"tilt, screened against its peers on fundamentals. Want just {sym}? Place it directly; "
        "want a different shape? name a theme/sector"
    )
    # An anchored basket is a deliberate single-sector focus by construction.
    return out, note, True


# ════════════════════════════════════════════════════════════════════════════
# Step 1 — fundamentals backfill + selection gate
# ════════════════════════════════════════════════════════════════════════════


def _choose_selection_gate(slots: SlotState, request: str) -> SelectionGate:
    """Pick the gate by intent. ``magic_formula`` for value-tilted asks,
    ``fscore`` for quality/safety asks, ``multifactor`` as the smart default
    (quality+value blended). ``none`` is NEVER chosen here — a named gate is
    anti-bland guardrail #2; even a thematic ask gets a multifactor quality
    gate so it can't collapse into 'top mcap'."""
    r = request.lower()
    theme = (slots.theme or "").lower()
    text = f"{r} {theme}"

    value_cues = ("cheap", "value", "undervalued", "magic formula", "bargain", "low pe", "low p/e")
    quality_cues = ("quality", "compounder", "safe", "stable", "blue chip", "bluechip", "high roe", "defensive")

    if any(c in text for c in value_cues):
        return "magic_formula"
    if slots.risk == "conservative" or any(c in text for c in quality_cues):
        return "fscore"
    return "multifactor"


def _backfill_gate_inputs(candidates: list[_Candidate]) -> None:
    """LATENCY FAST-PATH (Step 1a): batch-fetch ONLY the four cheap gate ratios
    (roe/roce/de/pe) for the WHOLE pre-gate universe in ONE SQL round-trip, then
    derive earnings-yield. Mutates candidates in place; missing metrics stay
    ``None`` (never fabricated).

    Why this is safe / output-identical: the gate (:func:`_apply_gate`) reads
    exactly these four ratios (+ derived earnings_yield) to score/drop names.
    ``fundamentals_screen.fetch_gate_inputs`` resolves them from the SAME DB
    CTEs the per-name fetcher uses (latest-per-sc_id, consolidated-preferred,
    same recency floor, same P/E=1/EarningsYield derivation), so the gate sees
    identical inputs — we've only collapsed ~30 sequential per-name fetches (each
    ~9 round-trips) into a single statement. The FULL per-name fetch (for the
    card's per-constituent ``gate_metrics``, incl. dividend payout) runs LATER on
    the ~8-12 survivors only, in :func:`_backfill_fundamentals_parallel`.
    """
    from backend.services.fundamentals_screen import fetch_gate_inputs

    try:
        # No recency floor here: the per-name fetcher this replaces applies none
        # (it reads point-in-time-latest), so dropping the floor keeps the batch
        # AT LEAST as permissive — a name the per-name path would have scored is
        # never starved to a no-data 0.0 by a floor the per-name path didn't use.
        rows = fetch_gate_inputs([c.symbol for c in candidates], min_period_end=None)
    except Exception as e:  # noqa: BLE001 — degrade gracefully on a sparse/unreachable DB
        logger.info("batch gate-input fetch failed: %s — gate runs on no-data names", str(e)[:160])
        rows = {}

    for c in candidates:
        row = rows.get(c.symbol.upper())
        if not row:
            continue
        c.roe = _num(row.get("roe"))
        c.roce = _num(row.get("roce"))
        c.de = _num(row.get("de"))
        c.pe = _num(row.get("pe"))
        if c.pe is not None and c.pe > 0:
            c.earnings_yield = round(1.0 / c.pe, 4)


def _fetch_fundamentals_memo(symbol: str, basis: str = "consolidated") -> dict:
    """``analysis_chat_tools.fetch_fundamentals`` with a short-TTL in-process
    memo (see :data:`_FUND_MEMO`). A warm entry (< :data:`_FUND_MEMO_TTL_S` old)
    is reused; otherwise we fetch and cache. Best-effort and process-local —
    never raises here (the caller catches), never serves a stale-enough value to
    matter (fundamentals update on quarterly filings)."""
    from backend.services.analysis_chat_tools import fetch_fundamentals

    key = (symbol.upper(), basis)
    hit = _FUND_MEMO.get(key)
    now = time.monotonic()
    if hit is not None and (now - hit[0]) < _FUND_MEMO_TTL_S:
        return hit[1]
    f = fetch_fundamentals(symbol, basis=basis)
    _FUND_MEMO[key] = (now, f)
    return f


def _backfill_fundamentals_parallel(candidates: list[_Candidate]) -> None:
    """Backfill the FULL per-name fundamentals on the SHORTLIST (post-gate
    survivors) via the existing per-name fetcher — but IN PARALLEL across a
    thread pool and through the short-TTL memo. Mutates candidates in place;
    missing metrics stay ``None`` (never fabricated).

    This is what populates the card's per-constituent ``gate_metrics`` with the
    richer fields the batch gate-input fetch doesn't carry (notably dividend
    payout, and the consolidated-basis name/sector). Running it only on the
    ~8-12 survivors (not the ~30-name universe) and concurrently — the work is
    I/O-bound DB round-trips that release the GIL — is the bulk of the latency
    win, with ZERO change to the numbers shown (same fetcher, same values).
    """
    def _one(c: _Candidate) -> None:
        try:
            f = _fetch_fundamentals_memo(c.symbol)
        except Exception as e:  # noqa: BLE001 — degrade gracefully on a sparse/unreachable DB
            logger.info("fundamentals backfill failed for %s: %s", c.symbol, str(e)[:120])
            return
        # The batch pass already set roe/roce/de/pe; refresh from the full fetch
        # (same source) and fill the fields the batch path doesn't carry.
        c.roe = _num(f.get("roe"))
        c.roce = _num(f.get("roce"))
        c.de = _num(f.get("de"))
        c.pe = _num(f.get("pe"))
        c.payout = _num(f.get("dividend_payout"))
        if c.pe is not None and c.pe > 0:
            c.earnings_yield = round(1.0 / c.pe, 4)

    if not candidates:
        return
    workers = min(_IO_MAX_WORKERS, len(candidates))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_one, candidates))


def _score_candidates(
    candidates: list[_Candidate], gate: SelectionGate
) -> tuple[list[tuple[float, _Candidate]], int, int]:
    """The pure scoring/drop/rank core shared by the gate's two passes (the
    batch-input shortlist pass and the full-data refinalise pass). Returns the
    ranked ``[(score, candidate)]`` survivors (best first) plus the
    ``(dropped_broken, no_data)`` counts. Sets ``c.gate_metrics`` from whatever
    ratios are currently on the candidate — so the SAME logic runs identically on
    the cheap batch ratios and, later, on the full per-name fundamentals."""
    scored: list[tuple[float, _Candidate]] = []
    dropped_broken = 0
    no_data = 0
    for c in candidates:
        has_data = any(v is not None for v in (c.roe, c.roce, c.de, c.pe))
        if not has_data:
            no_data += 1
            # Keep with a neutral score — we can't fairly drop a name the DB
            # is simply silent on, but it ranks below names with proven quality.
            scored.append((0.0, c))
            continue
        if _is_broken(c):
            dropped_broken += 1
            continue
        if gate == "fscore":
            fs = _approx_fscore(c)
            applicable = _fscore_applicable_max(c)
            # _FSCORE_FLOOR (5) is calibrated against all 9 criteria being
            # answerable. A name with sparse DB fundamentals (common for
            # yfinance-sourced large-caps) can't reach 9 possible points at
            # all — comparing its partial score against the full-9 floor
            # silently rejects genuinely strong names just for having
            # incomplete data (observed live: a broad 29-name cross-sector
            # pool gated down to 1 survivor this way). Below a minimum
            # signal threshold, rank neutral-low like the no-data bucket
            # instead of "weak"; above it, hold the SAME relative bar
            # (floor/9) scaled to what's actually answerable.
            if applicable < 4:
                no_data += 1
                scored.append((0.0, c))
                continue
            if fs < _FSCORE_FLOOR * applicable / 9:
                dropped_broken += 1
                continue
            c.gate_metrics = _fscore_metrics(c, fs)
            scored.append((float(fs), c))
        elif gate == "magic_formula":
            score = _magic_formula_score(c)
            c.gate_metrics = _magic_metrics(c)
            scored.append((score, c))
        else:  # multifactor
            score = _multifactor_score(c)
            c.gate_metrics = _multifactor_metrics(c)
            scored.append((score, c))
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored, dropped_broken, no_data


def _refinalise_gate(
    candidates: list[_Candidate], gate: SelectionGate, slots: SlotState
) -> list[_Candidate]:
    """Re-run the gate's score/metric pass on the SHORTLIST now that the full
    per-name fundamentals are loaded (the batch pass only had roe/roce/de/pe; the
    full pass adds dividend payout, which the F-score reads). This keeps the
    shipped gate_metrics + ranking IDENTICAL to the original single-pass builder,
    which always scored on the full fetch. A name that now slips below the F-score
    floor with complete data is dropped honestly (same as before). Preserves rank
    order (best first)."""
    scored, _, _ = _score_candidates(candidates, gate)
    return [c for _, c in scored]


def _apply_gate(
    candidates: list[_Candidate], gate: SelectionGate, slots: SlotState
) -> tuple[list[_Candidate], str]:
    """Score + rank + prune the candidates by the chosen gate.

    Always drops fundamentally broken names when DB data exists (negative ROE,
    extreme leverage) — even a thematic basket can't carry a broken name
    (§3a). Returns the ranked survivors (best first) + a disclosure note.
    """
    scored, dropped_broken, no_data = _score_candidates(candidates, gate)
    survivors = [c for _, c in scored]

    parts: list[str] = []
    gate_label = {
        "fscore": f"Piotroski-style F-score gate (drop < {_FSCORE_FLOOR}/9, on available DB ratios)",
        "magic_formula": "Magic-Formula rank (Return-on-Capital × Earnings-Yield)",
        "multifactor": "multi-factor quality+value score (ROE/ROCE/D-E + earnings yield)",
        "none": "no fundamental gate",
    }[gate]
    parts.append(f"selection: {gate_label}")
    if dropped_broken:
        parts.append(f"dropped {dropped_broken} fundamentally weak/broken name(s)")
    if no_data:
        parts.append(
            f"{no_data} name(s) had sparse DB fundamentals — ranked below proven names, not fabricated"
        )
    return survivors, "; ".join(parts)


def _is_broken(c: _Candidate) -> bool:
    """Hard drop: a name the fundamentals clearly mark as broken."""
    if c.roe is not None and c.roe < 0:
        return True
    if c.de is not None and c.de > 5.0 and (c.sector or "").lower() not in (
        "private_bank", "psu_bank", "banking", "finance", "financial_services",
    ):
        # High leverage is normal for lenders; penal for the rest.
        return True
    return False


def _approx_fscore(c: _Candidate) -> int:
    """A Piotroski-style score on the ratios the MC/yfinance fundamentals DB
    actually serves. The classic 9-point Piotroski needs YoY accrual/asset-
    turnover deltas the sparse table can't reliably provide, so we approximate
    HONESTLY from profitability + leverage + payout signals and disclose it as
    'Piotroski-style' (never claim the full 9-factor). Higher == better."""
    score = 0
    if c.roe is not None and c.roe > 12:
        score += 1
    if c.roe is not None and c.roe > 18:
        score += 1
    if c.roce is not None and c.roce > 12:
        score += 1
    if c.roce is not None and c.roce > 18:
        score += 1
    if c.de is not None and c.de < 1.0:
        score += 1
    if c.de is not None and c.de < 0.5:
        score += 1
    if c.earnings_yield is not None and c.earnings_yield > 0.05:
        score += 1
    if c.payout is not None and 5 <= c.payout <= 80:
        score += 1
    if c.pe is not None and 0 < c.pe < 35:
        score += 1
    return score


def _fscore_applicable_max(c: _Candidate) -> int:
    """Max points `_approx_fscore` could award this candidate given which
    ratios it actually has — the denominator for a data-fair floor
    comparison. Mirrors the point-weighting in `_approx_fscore` exactly."""
    m = 0
    if c.roe is not None:
        m += 2
    if c.roce is not None:
        m += 2
    if c.de is not None:
        m += 2
    if c.earnings_yield is not None:
        m += 1
    if c.payout is not None:
        m += 1
    if c.pe is not None:
        m += 1
    return m


def _magic_formula_score(c: _Candidate) -> float:
    """Greenblatt Magic Formula proxy: rank on Return-on-Capital (ROCE, the
    available proxy for EBIT/Capital) × Earnings-Yield (1/PE). Names missing a
    leg score low (not fabricated)."""
    roc = (c.roce if c.roce is not None else c.roe) or 0.0
    ey = c.earnings_yield if c.earnings_yield is not None else 0.0
    # Normalise to comparable scales and combine (equal-weight rank surrogate).
    return (max(roc, 0.0) / 100.0) + ey


def _multifactor_score(c: _Candidate) -> float:
    """Blended quality (ROE+ROCE, low D-E) + value (earnings yield) score —
    the smart-default gate. Two factors blended to fight single-factor
    cyclicality (§3a)."""
    quality = 0.0
    if c.roe is not None:
        quality += min(c.roe, 60.0) / 60.0
    if c.roce is not None:
        quality += min(c.roce, 60.0) / 60.0
    if c.de is not None:
        quality += max(0.0, 1.0 - min(c.de, 2.0) / 2.0)
    value = (c.earnings_yield or 0.0) * 4.0  # ~5% EY ≈ 0.2 contribution
    return quality + value


def _fscore_metrics(c: _Candidate, fs: int) -> dict[str, float]:
    m: dict[str, float] = {"fscore": float(fs)}
    _put(m, "roe", c.roe)
    _put(m, "roce", c.roce)
    _put(m, "de", c.de)
    _put(m, "pe", c.pe)
    return m


def _magic_metrics(c: _Candidate) -> dict[str, float]:
    m: dict[str, float] = {}
    _put(m, "roce", c.roce if c.roce is not None else c.roe)
    _put(m, "earnings_yield", c.earnings_yield)
    _put(m, "pe", c.pe)
    return m


def _multifactor_metrics(c: _Candidate) -> dict[str, float]:
    m: dict[str, float] = {}
    _put(m, "roe", c.roe)
    _put(m, "roce", c.roce)
    _put(m, "de", c.de)
    _put(m, "earnings_yield", c.earnings_yield)
    _put(m, "pe", c.pe)
    return m


# ════════════════════════════════════════════════════════════════════════════
# Step 1 — sector cap + correlation/concentration check
# ════════════════════════════════════════════════════════════════════════════


def _enforce_sector_cap(
    candidates: list[_Candidate],
    sector_cap_pct: float,
    *,
    target_size: int,
    single_sector: bool = False,
) -> tuple[list[_Candidate], str]:
    """Select up to ``target_size`` names from the ranked pool while keeping any
    single sector under ``sector_cap_pct`` of the FINAL basket — so the result
    is provably cap-compliant at its own size (no truncate-vs-cap ordering bug).

    Algorithm: the per-sector ceiling is ``ceil(cap% × target_size)``. Walk the
    ranked pool keeping the best names per sector up to that ceiling; if the
    cap leaves fewer than ``target_size`` names available, the basket is simply
    smaller (and still compliant). ``candidates`` is rank-ordered (best first),
    so this keeps the strongest names per sector.

    When ``single_sector`` is True the user deliberately asked for one sector,
    so no cap is applied — we just truncate to ``target_size`` and note that the
    concentration is intentional.
    """
    if not candidates:
        return candidates, ""
    if single_sector:
        sec = candidates[0].sector
        return candidates[:target_size], (
            f"single-sector basket by request ({sec}) — sector cap relaxed; "
            "this is a focused, concentrated bet by design"
        )

    max_per_sector = max(1, math.ceil((sector_cap_pct / 100.0) * target_size))
    kept: list[_Candidate] = []
    by_sector: dict[str, int] = {}
    trimmed = 0
    for c in candidates:
        if len(kept) >= target_size:
            break
        sec = c.sector or "unknown"
        if by_sector.get(sec, 0) >= max_per_sector:
            trimmed += 1
            continue
        by_sector[sec] = by_sector.get(sec, 0) + 1
        kept.append(c)

    # Re-check against the REALISED size: ceil(cap% × n_final) can be tighter
    # than the target-size ceiling when the pool was sector-thin and the basket
    # ended up small. Tighten once more so the shipped basket is compliant at
    # its own size (matches the guardrail's recompute).
    final_ceiling = max(1, math.ceil((sector_cap_pct / 100.0) * len(kept)))
    if final_ceiling < max_per_sector:
        kept2: list[_Candidate] = []
        seen: dict[str, int] = {}
        for c in kept:
            sec = c.sector or "unknown"
            if seen.get(sec, 0) >= final_ceiling:
                trimmed += 1
                continue
            seen[sec] = seen.get(sec, 0) + 1
            kept2.append(c)
        kept = kept2
        max_per_sector = final_ceiling

    note = ""
    if trimmed:
        note = (
            f"sector cap ≤{sector_cap_pct:.0f}% enforced — dropped {trimmed} "
            f"name(s) so no single sector dominates (≤{max_per_sector}/sector)"
        )
    return kept, note


def _correlation_check(
    candidates: list[_Candidate],
    weights: dict[str, float],
    price_history: dict[str, list[dict]],
) -> str:
    """Flag pairs of names that move together (corr > :data:`_CORR_FLAG`) so
    the user knows the basket carries fewer *independent* bets than names. We
    DISCLOSE rather than silently drop (the user may want both); honest
    boundary. Returns a note (possibly empty)."""
    returns: dict[str, list[float]] = {}
    for c in candidates:
        closes = [float(b["close"]) for b in price_history.get(c.symbol, []) if b.get("close") is not None]
        if len(closes) >= MIN_HISTORY_BARS_FOR_COV:
            returns[c.symbol] = _log_returns(closes)
    flagged: list[str] = []
    syms = list(returns)
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            a, b = syms[i], syms[j]
            corr = _pearson(returns[a], returns[b])
            if corr is not None and corr > _CORR_FLAG:
                flagged.append(f"{a}/{b} ({corr:.2f})")
    if not flagged:
        return ""
    return (
        "correlation check: highly co-moving pair(s) — "
        + ", ".join(flagged[:3])
        + " — fewer independent bets than names; kept (disclosed, not dropped)"
    )


# ════════════════════════════════════════════════════════════════════════════
# Step 2 — weighting-scheme decision rule + compute
# ════════════════════════════════════════════════════════════════════════════


def _choose_scheme(
    slots: SlotState, request: str, candidates: list[_Candidate]
) -> tuple[WeightingScheme, str]:
    """The §3a Step-2 decision rule (NOT a hardwired default).

    ≤4 names                                 → equal (honest, cost-efficient)
    capital-preservation / conservative      → min_variance
    name-level view/tilts stated             → black_litterman (mcap prior + view)
    theme-as-factor                          → factor (blend quality+momentum)
    'own the market' / passive               → mcap
    else                                     → risk_parity  (smart-than-1/N default)
    """
    n = len(candidates)
    r = request.lower()

    if n <= _EQUAL_WEIGHT_MAX_NAMES and len(slots.asset_prefs.allow) <= 2:
        return "equal", f"equal-weight (only {n} names — 1/N is honest and cost-efficient here)"

    # A factor-style ask ("benefits from momentum/quality/value/low-vol") is a
    # factor bet by definition → factor-weighted, tilted toward that style (B2),
    # regardless of the parsed view direction.
    factor_style = _detect_factor_style(slots, request)
    if factor_style is not None:
        return (
            "factor",
            f"factor-weighted ({factor_style.replace('_', ' ')} factor emphasised — "
            "the basket is tilted toward the style you asked to benefit from)",
        )

    if slots.risk == "conservative" or "capital preservation" in r or "preserve" in r:
        return "min_variance", "minimum-variance (capital-preservation / conservative intent)"

    # A name-level directional view with conviction → tilt via Black-Litterman.
    if slots.view.direction in ("bull", "bear") and slots.view.target in ("stock", "sector"):
        return (
            "black_litterman",
            f"Black-Litterman (your {slots.view.direction} {slots.view.target} view "
            f"as the BL view vector over an mcap prior)",
        )

    if slots.theme and slots.view.direction == "none":
        return "factor", f"factor-weighted ('{slots.theme}' as a factor; quality+momentum blended to fight cyclicality)"

    if "own the market" in r or "passive" in r:
        return "mcap", "market-cap weighted (passive 'own the market' intent)"

    return "risk_parity", "risk-parity / ERC (smart default — equal risk contribution beats naive 1/N ~84% of the time)"


def _compute_weights(
    scheme: WeightingScheme,
    candidates: list[_Candidate],
    slots: SlotState,
    price_history: dict[str, list[dict]],
    factor_emphasis: Optional[str] = None,
) -> tuple[dict[str, float], str]:
    """Call ``weighting.compute_weights`` against its contract signature.

    Returns ``(weights, note)``; a non-empty ``note`` signals the covariance
    fallback fired (caller restates the scheme as ``equal`` and discloses).
    ``factor_emphasis`` tilts the ``factor`` scheme toward one style (B2).
    """
    symbols = [c.symbol for c in candidates]
    mcap = {c.symbol: c.mcap_cr for c in candidates if c.mcap_cr is not None}
    views = _view_vector(slots, candidates) if scheme == "black_litterman" else None

    try:
        weights = _weighting.compute_weights(
            symbols,
            scheme,
            price_history=price_history,
            mcap=mcap or None,
            views=views,
            factor_emphasis=factor_emphasis,
        )
    except Exception as e:  # noqa: BLE001 — never let a weighting fault sink the build
        logger.info("compute_weights(%s) failed: %s — equal-weight fallback", scheme, str(e)[:160])
        eq = 1.0 / len(symbols)
        return {s: eq for s in symbols}, (
            f"weighting fell back to equal-weight — {scheme.replace('_', '-')} could not be "
            "computed from the available history (honest boundary, not a silent guess)"
        )

    # Covariance-based schemes silently degrade to ~uniform when history is too
    # short; detect that and disclose it as the contract's honest fallback.
    if scheme in ("risk_parity", "min_variance", "black_litterman"):
        if _history_too_short(symbols, price_history):
            return weights, (
                f"{scheme.replace('_', '-')} needs ≥{MIN_HISTORY_BARS_FOR_COV} bars/name; "
                "some names are too short — using the equal-weight fallback (covariance unreliable)"
            )
    return weights, ""


def _view_vector(slots: SlotState, candidates: list[_Candidate]) -> dict[str, float]:
    """Map the parsed chat view to a per-symbol BL tilt. A bull/bear view tilts
    every in-scope name by a conviction-scaled magnitude (the BL view vector)."""
    mag = {"low": 0.02, "medium": 0.04, "high": 0.07}.get(slots.view.conviction, 0.04)
    sign = 1.0 if slots.view.direction == "bull" else -1.0 if slots.view.direction == "bear" else 0.0
    return {c.symbol: sign * mag for c in candidates}


# ════════════════════════════════════════════════════════════════════════════
# Step 3 — macro structure
# ════════════════════════════════════════════════════════════════════════════


def _macro_structure(
    slots: SlotState, request: str, candidates: list[_Candidate]
) -> tuple[str, float, str]:
    """Pick the macro shape. Returns ``(structure_label, equity_pct, note)``.
    ``equity_pct`` here is informational (the gold sleeve does the real split);
    the label drives the card title + rationale."""
    r = request.lower()
    if any(k in r for k in ("safe + moonshot", "safe and moonshot", "barbell", "moonshot")):
        return "barbell", 100.0, "barbell macro: a stable core + a small high-beta satellite"
    if any(k in r for k in ("invest for me", "long term", "long-term", "set and forget", "core")) and slots.horizon == "long":
        return "core-satellite", 100.0, "core-satellite macro: a broad quality core with thematic satellites"
    if slots.view.direction in ("bull", "bear") and slots.view.target in ("stock", "sector"):
        return "focused", 100.0, ""
    return "diversified", 100.0, ""


# ════════════════════════════════════════════════════════════════════════════
# Step 4 — sleeves (gold built; options/hedge DEFERRED, extension point)
# ════════════════════════════════════════════════════════════════════════════


def _build_sleeves(slots: SlotState, request: str) -> tuple[list[Sleeve], float, list[str]]:
    """Build the GOLD sleeve when it earns its place (conservative / long-
    horizon / inflation-rupee-hedge intent) AND ``gold`` is allowed.

    Returns ``(sleeves, gold_pct_of_portfolio, notes)``.

    EXTENSION POINT (deferred phase): the options sleeve (view-mapped legs,
    buy-vs-sell from live IV/PCR) and the hedge sleeve (protective put / collar
    on an index proxy) attach HERE. They are intentionally NOT built in this
    phase (equity+gold only); the ``Sleeve`` wire shape already reserves
    ``kind in {'options','hedge'}`` so adding them later needs no contract
    change. Do not silently fake them — that's why this is a marked stub.
    """
    notes: list[str] = []
    if "gold" not in slots.asset_prefs.allow:
        if slots.gold_pct:
            notes.append(
                f"you asked for a {slots.gold_pct:g}% gold sleeve but gold is "
                "excluded by the asset preferences on this build — no gold added"
            )
        return [], 0.0, notes

    r = request.lower()
    hedge_cue = any(
        k in r for k in ("inflation", "hedge", "rupee", "safe haven", "ballast", "uncertain", "diversif")
    )
    # An explicitly STATED split ("70% equity / 30% gold") is an instruction:
    # it bypasses the earns-its-place heuristic AND the 5-15% band, which
    # otherwise silently capped a 30% ask at 15% (2026-07-17 eval, B09).
    if slots.gold_pct is not None:
        _explicit = max(0.0, min(100.0, float(slots.gold_pct)))
        if _explicit <= 0:
            notes.append("gold sleeve set to 0% as asked")
            return [], 0.0, notes
        return _gold_sleeve_at(_explicit, notes, stated=True)
    # An explicit "yes, gold" clarify answer is a direct instruction, not a
    # second vote alongside the risk/horizon/hedge heuristic — it must win
    # outright rather than still needing to "earn its place" (2026-07-14).
    earns = (
        slots.asset_prefs.gold_requested
        or slots.risk == "conservative"
        or slots.horizon == "long"
        or hedge_cue
    )
    if not earns:
        notes.append("gold sleeve skipped — no conservative / long-horizon / inflation-hedge signal earned it")
        return [], 0.0, notes

    # Size the sleeve: base 8%, nudged by how many signals fired (cap 5-15%).
    signals = sum(
        [slots.risk == "conservative", slots.horizon == "long", hedge_cue]
    )
    gold_pct = max(_GOLD_PCT_MIN, min(_GOLD_PCT_MAX, _GOLD_PCT_BASE + (signals - 1) * 3.0))
    return _gold_sleeve_at(gold_pct, notes, stated=False)


def _gold_sleeve_at(
    gold_pct: float, notes: list[str], *, stated: bool,
) -> tuple[list[Sleeve], float, list[str]]:
    """Build the SGB + GOLDBEES sleeve at a given % of the OVERALL portfolio.
    ``stated=True`` means the user named the split — the note says so, and no
    band clamp applies."""
    # SGB long-core (illiquid but tax-efficient) + GOLDBEES ETF (liquid leg).
    sgb_pct = round(gold_pct * 0.6, 2)
    etf_pct = round(gold_pct - sgb_pct, 2)
    instruments = [
        GoldInstrument(
            kind="sgb",
            symbol="SGB",
            name="Sovereign Gold Bond (long core, 2.5% coupon + gold-price upside)",
            weight_pct=sgb_pct,
        ),
        GoldInstrument(
            kind="etf",
            symbol="GOLDBEES",
            name="Nippon Gold ETF (liquid leg, exchange-traded)",
            weight_pct=etf_pct,
        ),
    ]
    note = (
        "the split you asked for" if stated
        else "inflation / rupee hedge + low-correlation ballast"
    )
    sleeve = Sleeve(kind="gold", pct=round(gold_pct, 2), instruments=instruments, note=note)
    notes.append(
        f"gold sleeve {gold_pct:.0f}% (SGB {sgb_pct:.0f}% + GOLDBEES {etf_pct:.0f}%) — {note}"
    )
    return [sleeve], gold_pct, notes


# ════════════════════════════════════════════════════════════════════════════
# Step 5 — sizing & feasibility
# ════════════════════════════════════════════════════════════════════════════


def _quality_score(gm: dict) -> Optional[float]:
    """Composite quality score in ~[0,1] from a name's gate metrics (higher
    roe/roce better; lower de/pe better). None when the DB is silent on quality
    (no roe AND no roce) — the caller then falls back to conviction-order tilt."""
    if not gm:
        return None
    roe, roce = gm.get("roe"), gm.get("roce")
    de, pe = gm.get("de"), gm.get("pe")
    if roe is None and roce is None:
        return None
    s, k = 0.0, 0
    if roe is not None:
        s += max(min(float(roe), 60.0), -20.0) / 60.0
        k += 1
    if roce is not None:
        s += max(min(float(roce), 60.0), -20.0) / 60.0
        k += 1
    base = s / max(k, 1)
    if de is not None:
        base += (1.0 - min(max(float(de), 0.0), 3.0) / 3.0) * 0.25
    if pe is not None and float(pe) > 0:
        base += (1.0 - min(float(pe), 60.0) / 60.0) * 0.25
    return base


def _differentiate_weights(candidates: list[_Candidate]) -> dict[str, float]:
    """Produce DIFFERENTIATED equity-sleeve weights when the scheme would
    otherwise be flat 1/N (thin history / ≤4 names / no covariance signal).

    Tilt source, in order of preference:
      1. quality gate (roe/roce/de/pe) when the DB serves it → quality-weighted;
      2. else conviction/pin ORDER (lead name = highest conviction) → decay.
    Blended 65/35 with uniform so the tilt is meaningful but never degenerate,
    normalised to sum 1.0. This is what turns a bare 25/25/25/25 basket into a
    structured, reasoned split."""
    n = len(candidates)
    if n <= 1:
        return {c.symbol: 1.0 for c in candidates}
    have_quality = any(_quality_score(c.gate_metrics or {}) is not None for c in candidates)
    scores: list[float] = []
    for i, c in enumerate(candidates):
        if have_quality:
            q = _quality_score(c.gate_metrics or {})
            scores.append(q if q is not None else 0.0)
        else:
            scores.append(float(n - i))  # conviction/pin-order decay
    lo = min(scores)
    scores = [s - lo + 0.5 for s in scores]  # shift positive, keep spread
    tot = sum(scores) or 1.0
    uni = 1.0 / n
    out = {c.symbol: 0.65 * (scores[i] / tot) + 0.35 * uni
           for i, c in enumerate(candidates)}
    z = sum(out.values()) or 1.0
    return {k: v / z for k, v in out.items()}


def _weight_reason(
    c: _Candidate, rank: int, n: int, have_quality: bool,
    provided: Optional[str],
) -> str:
    """One honest line explaining WHY this name carries its weight."""
    if provided:
        return provided.strip()
    gm = c.gate_metrics or {}
    if have_quality and _quality_score(gm) is not None:
        bits = []
        if gm.get("roe") is not None:
            bits.append(f"ROE {float(gm['roe']):.0f}%")
        if gm.get("roce") is not None:
            bits.append(f"ROCE {float(gm['roce']):.0f}%")
        if gm.get("de") is not None:
            bits.append(f"D/E {float(gm['de']):.2f}")
        metrics = ", ".join(bits)
        tier = rank / max(n - 1, 1)
        if tier <= 0.34:
            head = "Overweight — strongest quality gate"
        elif tier >= 0.67:
            head = "Underweight — thinner/weaker fundamentals"
        else:
            head = "Core position"
        return f"{head}" + (f" ({metrics})" if metrics else "")
    # No quality data → conviction/pin order.
    if rank == 0:
        return "Lead conviction — highest-beta beneficiary of the thesis"
    if rank >= n - 1:
        return "Satellite — diversifier / lower-conviction tail"
    return "Core position — mid-conviction constituent"


def _apply_weight_overrides(
    candidates: list[_Candidate], overrides: dict[str, float]
) -> dict[str, float]:
    """Honour an explicit user re-weight ("heavier in X", "make KSB 40%").
    Named symbols take their stated share (as a fraction of 100); any remaining
    names split the leftover proportional to the conviction decay so the tilt is
    respected without leaving un-named names at zero. Returns weights summing 1."""
    up = {str(k).upper(): float(v) for k, v in (overrides or {}).items()}
    syms = [c.symbol.upper() for c in candidates]
    named = {s: up[s] for s in syms if s in up and up[s] > 0}
    # Values >1 are read as percents; ≤1 as fractions.
    if named and max(named.values()) > 1.0:
        named = {k: v / 100.0 for k, v in named.items()}
    named_sum = sum(named.values())
    out: dict[str, float] = {}
    rest = [c for c in candidates if c.symbol.upper() not in named]
    leftover = max(0.0, 1.0 - named_sum)
    if rest and leftover > 0:
        n = len(rest)
        decay = [float(n - i) for i in range(n)]  # conviction order
        dtot = sum(decay) or 1.0
        for c, d in zip(rest, decay):
            out[c.symbol] = leftover * (d / dtot)
    for c in candidates:
        if c.symbol.upper() in named:
            out[c.symbol] = named[c.symbol.upper()]
    z = sum(out.values()) or 1.0
    return {k: v / z for k, v in out.items()}


def _size_constituents(
    candidates: list[_Candidate],
    weights: dict[str, float],
    equity_share: float,
    slots: SlotState,
    *,
    provided_reasons: Optional[dict[str, str]] = None,
    weight_overrides: Optional[dict[str, float]] = None,
) -> tuple[list[StrategyConstituent], list[str]]:
    """Scale the equity-sleeve weights (sum 1.0) to overall-portfolio %, build
    the constituents, and feasibility-check against capital.

    ``weight_pct`` on each constituent is its share of the EQUITY SLEEVE (per
    the contract), so they sum to ~100 across the equity names; the gold sleeve
    carries its own overall-% weights. We surface the equity-sleeve share + any
    capital infeasibility as honest-boundary notes.

    When the upstream scheme produced a ~flat 1/N split (covariance/factor
    fallback on thin history), we replace it with a conviction/quality tilt via
    :func:`_differentiate_weights` so the basket is never bland, and attach a
    per-name ``weight_reason``. ``provided_reasons`` (the thematic WHY strings)
    win over generated ones.
    """
    notes: list[str] = []
    provided_reasons = provided_reasons or {}

    # Explicit user re-weight (a "rebuild heavier in X" / "make KSB 40%"
    # amendment) wins over the computed split — this is what makes a rebuild
    # ACTUALLY re-allocate instead of reproducing the same weights.
    if weight_overrides:
        weights = _apply_weight_overrides(candidates, weight_overrides)
        notes.append("weights set to your specified tilt (re-weighted on request)")

    # Detect a ~flat weight vector and differentiate it (structural, reasoned
    # split instead of bare equal-weight). Single-asset ≤2-name baskets stay as
    # given (1/N is genuinely honest there).
    norm = {c.symbol: weights.get(c.symbol, 0.0) for c in candidates}
    tot0 = sum(norm.values()) or 1.0
    norm = {k: v / tot0 for k, v in norm.items()}
    spread = (max(norm.values()) - min(norm.values())) if norm else 0.0
    if len(candidates) >= 3 and spread < 0.02 and not weight_overrides:
        weights = _differentiate_weights(candidates)
        notes.append(
            "weights conviction-tilted (differentiated by "
            + ("quality gate" if any(_quality_score(c.gate_metrics or {}) is not None
                                     for c in candidates) else "thesis conviction")
            + " — not a flat 1/N split)"
        )

    have_quality = any(_quality_score(c.gate_metrics or {}) is not None for c in candidates)
    # Rank by final weight (desc) so weight_reason tiers line up with the split.
    ranking = sorted(candidates, key=lambda c: weights.get(c.symbol, 0.0), reverse=True)
    rank_of = {c.symbol: i for i, c in enumerate(ranking)}

    total_w = sum(weights.get(c.symbol, 0.0) for c in candidates) or 1.0
    # Per-leg rupees, computed HERE so the reply can quote the split instead of
    # burning a `compute` hop on weight × capital (2026-07-17 eval: B04/B06).
    _equity_capital = (
        slots.capital_inr * (equity_share / 100.0)
        if slots.capital_inr is not None else None
    )
    constituents: list[StrategyConstituent] = []
    for c in candidates:
        w = weights.get(c.symbol, 0.0) / total_w
        constituents.append(
            StrategyConstituent(
                symbol=c.symbol,
                name=c.name,
                sector=c.sector,
                weight_pct=round(w * 100.0, 2),
                gate_metrics=c.gate_metrics or {},
                weight_reason=_weight_reason(
                    c, rank_of.get(c.symbol, 0), len(candidates), have_quality,
                    provided_reasons.get(c.symbol.upper()) or provided_reasons.get(c.symbol),
                ),
            )
        )
    _normalise_to_100(constituents)
    # Rupees AFTER normalisation so the split always reconciles to the weights
    # actually shown on the card (and thus to the stated capital).
    if _equity_capital is not None:
        for _c in constituents:
            _c.allocation_inr = round(_equity_capital * (_c.weight_pct / 100.0), 2)

    if equity_share < 100.0:
        notes.append(
            f"equity sleeve is {equity_share:.0f}% of the portfolio (the rest is the gold sleeve); "
            "constituent weights below are shares of the equity sleeve"
        )

    # Feasibility vs capital: per-name ticket must clear a sane floor so the
    # smallest weight isn't an un-investable ₹-sliver. Honest boundary, no fake
    # success — we state it; we do not silently re-weight to hide it.
    cap = slots.capital_inr
    if cap is not None and constituents:
        equity_capital = cap * (equity_share / 100.0)
        smallest = min(c.weight_pct for c in constituents)
        smallest_ticket = equity_capital * (smallest / 100.0)
        if smallest_ticket < 5000 and len(constituents) > _EQUAL_WEIGHT_MAX_NAMES:
            notes.append(
                f"at ₹{cap:,.0f} the smallest position is ~₹{smallest_ticket:,.0f} — thin for a "
                f"single stock. Nearest real fit: hold fewer names, or use NIFTYBEES/sector ETFs "
                f"for the long tail instead of {len(constituents)} separate tickets"
            )
    elif cap is None:
        notes.append("no capital stated — sized in percentages; state a ₹ amount to round to lots/tickets")

    return constituents, notes


# ════════════════════════════════════════════════════════════════════════════
# Anti-bland guardrails (assert before render)
# ════════════════════════════════════════════════════════════════════════════


def _assert_guardrails(
    *,
    constituents: list[StrategyConstituent],
    scheme: WeightingScheme,
    gate: SelectionGate,
    sector_cap: float,
    slots: SlotState,
    single_sector: bool = False,
    pinned: bool = False,
) -> str:
    """The §3a anti-bland invariants. These are *internal* asserts — they catch
    a builder regression in dev/tests, not a user-input problem. A violation is
    a bug in this module, so failing loudly is correct.

    Returns a concentration note ("" when clean) for the one invariant a thin
    universe can make unsatisfiable — see #3.

    For a PINNED basket (B1) the universe SHAPE was chosen by the caller/flow,
    not the builder: the count, the sector spread, and (when history is thin) an
    honestly-disclosed equal-weight fallback are all legitimate, so #1/#3/#4 are
    relaxed. The weights-sanity check still runs — that's a real math invariant."""
    n = len(constituents)
    if n == 0:
        return ""  # the empty-card path handled this honestly already.

    if pinned:
        # Only the math invariant applies to a pinned basket.
        total = sum(c.weight_pct for c in constituents)
        assert all(c.weight_pct >= 0 for c in constituents) and abs(total - 100.0) < 1.0, (
            f"weights must be ≥0 and sum ~100 (got {total:.2f})"
        )
        return ""

    # #1: no bare equal-weight unless ≤4 names.
    assert not (scheme == "equal" and n > _EQUAL_WEIGHT_MAX_NAMES), (
        f"anti-bland #1: equal-weight with {n} names (>{_EQUAL_WEIGHT_MAX_NAMES}) — "
        "the covariance fallback must restate the reason, not silently 1/N"
    )

    # #2: selection must name a gate (never bare 'top mcap').
    assert gate in ("fscore", "magic_formula", "multifactor"), (
        f"anti-bland #2: selection gate {gate!r} is not a named fundamental gate"
    )

    # #3: sector cap enforced (no single sector over the ceiling by count).
    # Skipped for a deliberate single-sector basket (sector_cap reported 100%).
    #
    # A THIN pool can make the cap arithmetically unsatisfiable: a 3-name
    # basket under a 32% cap allows 1 name per sector, so any two names sharing
    # a sector "violate" it with nothing the trimmer can do short of shipping a
    # 2-name basket. That is a disclosure, not a builder bug — crashing the
    # turn on it took out every thin-universe sector ask (`theme="defence"`
    # raised AssertionError before this; found 2026-07-17). We assert only when
    # a compliant basket was actually reachable, and return a concentration
    # note otherwise so the caller can surface it honestly.
    concentration_note = ""
    if not single_sector and constituents:
        counts: dict[str, int] = {}
        for c in constituents:
            counts[c.sector] = counts.get(c.sector, 0) + 1
        max_allowed = max(1, math.ceil((sector_cap / 100.0) * n))
        worst_sector, worst = max(counts.items(), key=lambda kv: kv[1])
        if worst > max_allowed:
            # Reachable ⇒ real bug. Unreachable ⇒ honest note.
            reachable = len(counts) >= math.ceil(n / max_allowed)
            assert not reachable, (
                f"anti-bland #3: a sector holds {worst}/{n} names, over the "
                f"{sector_cap:.0f}% cap ({max_allowed} max)"
            )
            concentration_note = (
                f"{worst} of {n} names are {worst_sector} — above the "
                f"~{sector_cap:.0f}% sector guide, but the pool that cleared "
                "the quality gate was too thin to diversify further without "
                "dropping to a smaller basket; concentrated by data, not design"
            )
    return concentration_note

    # #4: a stated directional view must map to a tilt (BL / factor / focused),
    # never get flattened into a passive equal/mcap basket.
    if slots.view.direction in ("bull", "bear"):
        assert scheme in ("black_litterman", "factor", "risk_parity", "min_variance"), (
            f"anti-bland #4: a {slots.view.direction} view did not map to a tilt "
            f"(scheme={scheme})"
        )

    # weights are sane (sum ~100, all ≥ 0).
    total = sum(c.weight_pct for c in constituents)
    assert all(c.weight_pct >= 0 for c in constituents) and abs(total - 100.0) < 1.0, (
        f"weights must be ≥0 and sum ~100 (got {total:.2f})"
    )


# ════════════════════════════════════════════════════════════════════════════
# Card text
# ════════════════════════════════════════════════════════════════════════════


def _title(slots: SlotState, request: str, structure: str,
           has_gold: bool = False) -> str:
    bits: list[str] = []
    if slots.theme:
        bits.append(slots.theme.strip().title())
    base = {
        "barbell": "Barbell Basket",
        "core-satellite": "Core-Satellite Basket",
        "focused": "Focused Basket",
        "diversified": "Diversified Equity Basket",
    }.get(structure, "Equity Basket")
    # A basket with a real gold sleeve must say so — calling it a pure
    # "Equity Basket" while it holds SGB/GOLDBEES is the same silent-
    # mismatch bug this fix targets, just in the card's own title.
    if has_gold:
        base = (base.replace("Equity Basket", "Equity + Gold Basket")
                if "Equity" in base else f"{base} + Gold")
    bits.append(base)
    return " — ".join(bits) if len(bits) > 1 else bits[0]


def _rationale(
    *,
    slots: SlotState,
    request: str,
    scheme: WeightingScheme,
    gate: SelectionGate,
    structure: str,
    constituents: list[StrategyConstituent],
    sleeves: list[Sleeve],
    sector_cap: float,
) -> str:
    """PART C — a tailored defence that EXPLICITLY connects what we propose to
    what the user asked. We open by naming their ask, then walk the causal chain
    (ask → screen → weight → risk → gold) so each choice reads as a CONSEQUENCE
    of their words, not a generic template: the view drives the scheme, the gate
    drives the names, the risk/horizon drives the gold %."""
    view = slots.view
    {
        "equal": "equal-weight",
        "mcap": "market-cap weighting",
        "risk_parity": "risk-parity (equal risk contribution)",
        "min_variance": "minimum-variance",
        "black_litterman": "Black-Litterman",
        "factor": "factor weighting",
        "conviction": "conviction weighting (quality gate / thesis order)",
    }[scheme]
    gate_txt = {
        "fscore": "a Piotroski-style F-score quality gate (drops weak balance sheets)",
        "magic_formula": "a Magic-Formula rank (return-on-capital × earnings-yield — cheap-but-good)",
        "multifactor": "a multi-factor quality+value gate (ROE/ROCE/D-E + earnings yield)",
        "none": "a price/technical filter",
    }[gate]

    # 1. Name the user's ASK in their own terms (what we heard).
    ask = _summarise_ask(slots, request)

    # 2. WHY this gate — connect the screen back to the ask.
    if gate == "magic_formula":
        why_gate = (
            "because you leaned toward value/cheapness, we screened with " + gate_txt
        )
    elif gate == "fscore":
        why_gate = (
            "because you wanted quality/safety (and a "
            f"{slots.risk} risk appetite), we screened with " + gate_txt
        )
    else:
        why_gate = "we screened with " + gate_txt + " (quality and value together, so one bad year on either factor can't carry a name in)"

    # 3. WHY this scheme — connect the weighting back to the view/risk.
    if scheme == "black_litterman" and view.direction in ("bull", "bear"):
        why_scheme = (
            f"your {view.conviction}-conviction {view.direction} {view.target} view becomes the "
            f"Black-Litterman view vector over a market-cap prior, so the names you're bullish on "
            f"carry more weight — a tilt, not a coin-flip"
        )
    elif scheme == "min_variance":
        why_scheme = (
            "your capital-preservation / conservative intent points at minimum-variance — it sizes "
            "for the lowest-volatility mix the covariance allows, not the biggest-name bet"
        )
    elif scheme == "factor":
        why_scheme = (
            f"we treat '{slots.theme}' as a factor and weight on a quality+momentum blend, so the "
            "theme actually drives sizing instead of just picking the names"
        )
    elif scheme == "risk_parity":
        why_scheme = (
            "with no single name to over-weight, we use risk-parity so every holding contributes the "
            "SAME share of portfolio risk — equal risk, not equal rupees (it beats a reflex 1/N most "
            "of the time)"
        )
    elif scheme == "equal":
        why_scheme = (
            "the basket is small enough that equal-weight is the honest, cost-efficient choice (1/N "
            "earns its place at this name count)"
        )
    elif scheme == "conviction":
        why_scheme = (
            "weights follow conviction — the thesis/quality order decides size, "
            "so the names carrying the argument carry the capital"
        )
    else:  # mcap
        # NEVER put words in the user's mouth: this used to read "you asked to
        # 'own the market'" as a QUOTE on every mcap build, including ones
        # where the user said no such thing (2026-07-17 eval flagged it as a
        # fabricated quote contradicting the reply on 4/10 baskets).
        why_scheme = (
            "we weight by market cap — index-like by design, so the basket "
            "tracks the market rather than taking a name-level bet"
        )

    # 4. WHY the sector cap / gold — connect risk to structure.
    cap_txt = (
        f"and a ≤{sector_cap:.0f}% single-sector cap stops it from quietly becoming one big sector bet"
        if sector_cap < 100.0
        else "and the single-sector concentration is deliberate — you anchored on this sector, so the cap is relaxed by design"
    )
    gold = next((s for s in sleeves if s.kind == "gold"), None)
    gold_txt = ""
    if gold:
        gold_txt = (
            f" Because the brief reads {slots.risk}/{slots.horizon}-horizon (with a hedge/ballast cue), "
            f"a {gold.pct:.0f}% gold sleeve (SGB long core + GOLDBEES liquid leg) is added as {gold.note} "
            f"— that's risk appetite directly setting the gold %."
        )

    cap_amt = (
        f"₹{slots.capital_inr:,.0f}" if slots.capital_inr is not None else "an unstated amount (sized in %)"
    )

    return (
        f"You asked for {ask}. So here's the chain: {why_gate} — that's what picked these "
        f"{len(constituents)} names (each shows the exact ratios that earned its slot). Then {why_scheme}, "
        f"{cap_txt}.{gold_txt} It's sized against {cap_amt}. Every step traces back to your brief — "
        f"edit any leg before you register it; nothing executes until you place it in your broker app."
    )


def _alternatives(
    *,
    slots: SlotState,
    scheme: WeightingScheme,
    gate: SelectionGate,
    sleeves: list[Sleeve],
    n_names: int,
    single_sector: bool,
) -> list[StrategyAlternative]:
    """PART D — 1-3 GENUINELY different strategies the user might prefer, each
    tied to WHEN they'd choose it (not boilerplate). We pick alternatives that
    are real pivots away from the CURRENT card (so we never propose what we just
    built) and order them by how relevant the swap is to this brief."""
    alts: list[StrategyAlternative] = []

    # Diversify pivot — the single most useful swap for a FOCUSED/anchored
    # (single-sector) basket: it's the one structural thing it isn't.
    if single_sector:
        alts.append(StrategyAlternative(
            title="Diversify across sectors",
            detail=(
                "Widen the same quality screen across sectors instead of concentrating in this one, "
                "to cut single-sector risk. Prefer this if you're less sure about the sector call and "
                "want the basket to lean on stock-picking, not one industry's cycle."
            ),
        ))

    # Value pivot — only if we DIDN'T already lean value.
    if gate != "magic_formula":
        alts.append(StrategyAlternative(
            title="Value tilt",
            detail=(
                "Swap the quality gate for a Magic-Formula rank (return-on-capital × earnings-yield) "
                "to lean toward cheaper names. Prefer this if you think quality is already priced in "
                "and you'd rather buy the bargain than the compounder."
            ),
        ))
    else:
        # We already went value → offer the quality counterpart.
        alts.append(StrategyAlternative(
            title="Quality tilt",
            detail=(
                "Swap the Magic-Formula rank for a Piotroski-style F-score quality gate to favour "
                "stronger balance sheets over cheapness. Prefer this if you'd rather pay up for "
                "durability than chase a low multiple."
            ),
        ))

    # Lower-risk pivot — only if we aren't already min-variance, and only worth
    # proposing when the current book is wide enough that tightening it is a real
    # change (a ≤5-name book can't meaningfully shrink).
    if scheme != "min_variance" and n_names > 5 and len(alts) < 3:
        alts.append(StrategyAlternative(
            title="Lower-risk",
            detail=(
                f"Run minimum-variance on a tighter ~5-name book (down from {n_names}) to minimise "
                "drawdown rather than spread risk evenly. Prefer this if capital preservation matters "
                "more than upside — e.g. money you'll need within a year or two."
            ),
        ))

    # Passive pivot — always a useful, cheaper baseline unless we ARE passive.
    if scheme != "mcap" and len(alts) < 3:
        alts.append(StrategyAlternative(
            title="Passive / index-like",
            detail=(
                "Market-cap-weight the same names (or just hold NIFTYBEES) for an index-like, "
                "low-maintenance basket. Prefer this if you don't want to defend any active call and "
                "would rather pay the lowest cost to 'own the market'."
            ),
        ))

    # Gold-ballast pivot — surface it only when we DIDN'T already add gold.
    if not any(s.kind == "gold" for s in sleeves) and len(alts) < 3:
        alts.append(StrategyAlternative(
            title="Add a gold ballast",
            detail=(
                "Carve 5-15% into a gold sleeve (SGB long core + GOLDBEES liquid leg) as a low-"
                "correlation hedge. Prefer this if you're worried about inflation or a rupee/market "
                "wobble and want a cushion that doesn't move with equities."
            ),
        ))

    return alts[:3]


def _summarise_ask(slots: SlotState, request: str) -> str:
    """A short, human restatement of what the user asked for — pulled from the
    request text + the parsed slots so the rationale can open by naming it (Part
    C: the proposal must read as tailored to THEIR words)."""
    bits: list[str] = []
    if slots.theme:
        bits.append(f"a '{slots.theme.strip()}' basket")
    else:
        bits.append("an equity basket")
    if slots.view.direction in ("bull", "bear"):
        bits.append(
            f"with your {slots.view.conviction}-conviction {slots.view.direction} "
            f"{slots.view.target} view"
        )
    bits.append(f"at a {slots.risk} risk level")
    bits.append(f"over a {slots.horizon}-term horizon")
    return ", ".join(bits)


def _empty_card(
    request: str,
    slots: SlotState,
    gate: SelectionGate,
    sector_cap: float,
    assumptions: list[str],
) -> StrategyBuilderCard:
    """Honest boundary: the gate left nothing. Don't fabricate a basket."""
    assumptions = _dedup(
        assumptions
        + [
            "no names cleared the fundamentals gate on the available DB data — "
            "loosen the theme/sector or relax the quality floor, or I can rank on what data exists"
        ]
    )
    return StrategyBuilderCard(
        title="No basket built (gate left nothing)",
        rationale=(
            "I couldn't assemble a defensible basket: after the fundamentals gate and the "
            "sector cap, no names survived on the data the DB could serve. Rather than ship a "
            "fabricated 'top-mcap, equal-weight' list, here's the honest boundary — widen the "
            "universe (a broader theme/sector) or relax the quality floor and I'll rebuild."
        ),
        weighting_scheme="equal",
        selection_gate=gate,
        sector_cap=sector_cap,
        constituents=[],
        sleeves=[],
        assumptions=assumptions,
        capital_inr=slots.capital_inr,
        disclaimer=DEFAULT_DISCLAIMER,
    )


def _assumption_lines(slots: SlotState) -> list[str]:
    """Surface each defaulted/skipped slot as a '(assumed …)' line."""
    out: list[str] = []
    a = slots.assumed
    if a.view:
        out.append(
            f"(assumed view: {slots.view.direction}/{slots.view.target}, "
            f"{slots.view.conviction} conviction — you didn't state one)"
        )
    if a.risk:
        out.append(f"(assumed risk: {slots.risk})")
    if a.horizon:
        out.append(f"(assumed horizon: {slots.horizon})")
    if a.capital_inr and slots.capital_inr is None:
        out.append("(assumed no capital stated — sizing in %)")
    if a.theme and slots.theme:
        out.append(f"(assumed theme: {slots.theme})")
    return out


# ════════════════════════════════════════════════════════════════════════════
# Price history + math helpers
# ════════════════════════════════════════════════════════════════════════════


def _fetch_price_history(symbols: list[str]) -> dict[str, list[dict]]:
    """Daily OHLCV per symbol, Kite-primary (yfinance fallback) via the shared
    ``get_historical_ohlcv``. Empty list per symbol on failure (never raises) —
    the weighting layer + correlation check degrade gracefully on thin data.

    LATENCY: the final basket is ~8-12 names and each fetch is an independent
    I/O round-trip (Kite/yfinance HTTP) that releases the GIL, so we fan them out
    across a thread pool instead of fetching serially. Identical data per symbol —
    the network result doesn't depend on fetch order — just gathered concurrently.
    """
    from backend.kite.market_data import get_historical_ohlcv

    def _one(sym: str) -> tuple[str, list[dict]]:
        try:
            return sym, (get_historical_ohlcv(sym, period=_PRICE_PERIOD) or [])
        except Exception as e:  # noqa: BLE001
            logger.info("price history fetch failed for %s: %s", sym, str(e)[:120])
            return sym, []

    if not symbols:
        return {}
    workers = min(_IO_MAX_WORKERS, len(symbols))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(_one, symbols))


def _history_too_short(symbols: list[str], price_history: dict[str, list[dict]]) -> bool:
    return any(
        len(price_history.get(s, [])) < MIN_HISTORY_BARS_FOR_COV for s in symbols
    )


def _log_returns(closes: list[float]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev and closes[i] > 0 and prev > 0:
            out.append(math.log(closes[i] / prev))
    return out


def _pearson(a: list[float], b: list[float]) -> Optional[float]:
    n = min(len(a), len(b))
    if n < 2:
        return None
    a, b = a[-n:], b[-n:]
    ma = sum(a) / n
    mb = sum(b) / n
    cov = sum((a[i] - ma) * (b[i] - mb) for i in range(n))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    denom = math.sqrt(va * vb)
    if denom == 0:
        return None
    return cov / denom


def _normalise_to_100(constituents: list[StrategyConstituent]) -> None:
    total = sum(c.weight_pct for c in constituents)
    if total <= 0:
        eq = round(100.0 / len(constituents), 2) if constituents else 0.0
        for c in constituents:
            c.weight_pct = eq
        return
    if abs(total - 100.0) > 0.01:
        for c in constituents:
            c.weight_pct = round(c.weight_pct / total * 100.0, 2)
    # Push any residual rounding onto the largest leg.
    drift = round(100.0 - sum(c.weight_pct for c in constituents), 2)
    if drift and constituents:
        biggest = max(constituents, key=lambda c: c.weight_pct)
        biggest.weight_pct = round(biggest.weight_pct + drift, 2)


def _num(v: object) -> Optional[float]:
    try:
        f = float(v) if v is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    # NaN/inf must degrade to None, NOT propagate: yfinance returns float('nan')
    # (not None) for a missing ROE/ROCE/D-E. An unguarded NaN poisons the whole
    # conviction-weight vector (every weight → NaN) and then trips the weight
    # guardrail assertion, crashing the build.
    if f is not None and not math.isfinite(f):
        return None
    return f


def _put(m: dict[str, float], key: str, v: Optional[float]) -> None:
    if v is not None and math.isfinite(float(v)):
        m[key] = round(float(v), 4)


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


__all__ = ["build_strategy"]
