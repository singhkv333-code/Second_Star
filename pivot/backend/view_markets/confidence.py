"""View Markets — two-dial confidence scorer + Alignment Score.

The single most important honesty decision (testing doc §2): OUTCOME confidence
and EXPRESSION confidence are DIFFERENT questions, scored on SEPARATE dials and
**never averaged into one number**.

  * DIAL 1 — OUTCOME (the thesis): analog hit-rate / base rate, base-rate vs
    market-priced odds (edge-vs-priced, using the HIDDEN prediction-market
    prior or option-implied probability), relationship strength (RELATIVE
    only), and sample sufficiency vs MinTRL.
  * DIAL 2 — EXPRESSION (the structure): event-study CAAR/BHAR alignment of the
    chosen instruments, statistical significance (BMP + non-parametric),
    the Trust Battery verdict on the expression's backtest (DSR / walk-forward),
    cost-survivability, and option payoff geometry (POP / expected-move cover).

Each dial: a weighted blend of its soft dimensions, **clamped (capped) by a
ceiling derived from the Trust verdict + N** — statistics can only CAP, never
inflate. Output 0..100 + a letter band (A..E) reusing Pivot's Trust ladder.

CRUCIAL GATE: when the Trust verdict is ``insufficient_data`` (N below MinTRL),
the dial is **SUPPRESSED** — ``score=None`` / ``letter=None`` — and the
rationale reads "Too few analog events (N=k) to score honestly." This single
rule prevents the worst failure mode (a confident-looking 72 on 3 events).

Bands (testing doc §2.3): 80-100 A (promising) / 60-79 B (promising-unproven) /
40-59 C (unproven) / 20-39 D (no_edge) / 0-19 E (no_edge) / suppressed (—,
insufficient_data).

Verdict -> ceiling: ``insufficient_data`` -> suppressed; ``no_edge`` -> 39;
``unproven`` -> 79; ``promising`` -> 100.

Reuses (real interfaces, pinned 2026-06-29):
  * ``backend.services.backtest.validation.verdict.trust_verdict`` output dict
    (``.verdict`` ∈ insufficient_data/no_edge/unproven/promising, ``.flags``)
    — typically carried on ``EventStudyResult.verdict``.
  * ``backend.services.forward_stats`` block fields (``n_obs`` / ``min_trl`` /
    ``deflated_sharpe``) for the N-vs-MinTRL gate.
  * ``backend.view_markets.event_study.EventStudyResult`` and
    ``backend.view_markets.expectations.SurpriseFraming`` as high-level inputs.
  * ``backend.models.ViewConfidence`` (writes; UNIQUE(view_id, dimension);
    ``score`` is Float 0..1 — divide the 0..100 dial by 100; ``dimension`` ∈
    {outcome, expression}).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from backend.models import ViewConfidence
    from backend.view_markets.event_study import EventStudyResult
    from backend.view_markets.expectations import SurpriseFraming


# Verdict -> hard ceiling on the 0..100 dial (None == suppress entirely).
VERDICT_CEILING: dict[str, Optional[int]] = {
    "insufficient_data": None,
    "no_edge": 39,
    "unproven": 79,
    "promising": 100,
}


@dataclass(frozen=True)
class DialScore:
    """One confidence dial. ``score`` / ``letter`` are ``None`` when suppressed
    (Trust verdict ``insufficient_data``). ``components`` records the soft
    inputs + the applied ceiling for auditability."""

    dimension: str                 # "outcome" | "expression"
    score: Optional[int]           # 0..100, None when suppressed
    letter: Optional[str]          # "A".."E", None when suppressed
    suppressed: bool
    verdict: Optional[str]
    components: dict
    rationale: str


@dataclass(frozen=True)
class TwoDialScore:
    """The Alignment Score: the two dials kept SEPARATE, plus shared flags.
    There is intentionally NO combined/averaged scalar."""

    outcome: DialScore
    expression: DialScore
    flags: tuple[str, ...] = field(default_factory=tuple)


# ── Soft-dimension weights (renormalised over the dimensions actually present;
#    a missing input never penalises, it just drops out of the blend) ─────────
_OUTCOME_WEIGHTS = {
    "hit_rate": 0.40,            # analog base rate in the thesis direction
    "edge_vs_priced": 0.30,     # own prior vs market-priced odds
    "relationship_strength": 0.15,  # RELATIVE views only
    "sample": 0.15,             # N vs MinTRL sufficiency
}
_EXPRESSION_WEIGHTS = {
    "alignment": 0.35,          # CAAR / BHAR historical alignment
    "significance": 0.25,       # BMP + non-parametric agreement
    "cost_survival": 0.25,      # net-of-Indian-cost survivability
    "payoff": 0.15,             # option POP / expected-move coverage
}
# Saturating analog count when no explicit MinTRL is supplied (≈ "enough").
_SAMPLE_SATURATION = 12.0
# Significance p at/above which the dimension contributes nothing.
_SIG_P_FLOOR = 0.10
# CAAR magnitude that saturates the alignment sub-score (±2.5% over the window).
_CAAR_SCALE = 20.0

# ── per-EXPRESSION historical-alignment blend ────────────────────────────────
# The detail-page "Historical alignment" dial must vary by EXPRESSION (basket vs
# option vs pair) and by tier, not echo the view-level construction score for
# every strategy. We therefore blend the belief-design fit with this
# expression's OWN realised backtest evidence — all real numbers already on the
# config, never fabricated — and cap the result at the Trust-verdict ceiling so
# it can never over-claim.
_ALIGNMENT_BLEND_WEIGHTS = {
    "design": 0.40,        # construction_alignment — expression⇄belief fit
    "consistency": 0.25,   # % of episodes this expression beat the benchmark
    "edge": 0.20,          # deflated-Sharpe edge surviving selection-bias deflation
    "reward_risk": 0.15,   # realised reward per unit of drawdown
}
# Deflated Sharpe at which the edge sub-score saturates (event-gated studies).
_DSR_FULL_CREDIT = 0.9
# Realised reward:risk (total return ÷ |max drawdown|) that saturates its term.
_RR_FULL_CREDIT = 8.0


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _weighted_blend(
    parts: list[tuple[float, float]],
) -> Optional[float]:
    """Weighted mean of ``(value_0_1, weight)`` pairs, renormalised over the
    present weights. ``None`` when no dimension is present."""
    den = sum(w for _, w in parts)
    if den <= 0:
        return None
    return sum(v * w for v, w in parts) / den


def _sample_sufficiency(
    sample_n: Optional[int], min_trl: Optional[float]
) -> Optional[float]:
    """N-vs-MinTRL sufficiency in 0..1 (saturates at MinTRL, or ~12 analogs)."""
    if sample_n is None:
        return None
    if min_trl is not None and min_trl > 0:
        return _clamp(sample_n / min_trl)
    return _clamp(sample_n / _SAMPLE_SATURATION)


def _significance_norm(p_value: float) -> float:
    """p-value -> 0..1 confidence (p≤0 → 1, p≥0.10 → 0, linear between)."""
    return _clamp(1.0 - (p_value / _SIG_P_FLOOR))


def _ceiling_for(verdict: Optional[str]) -> Optional[int]:
    """Trust-verdict ceiling on the 0..100 dial. Unknown/absent verdict means no
    statistical cap (100) — the blend stands on its own soft inputs."""
    if verdict is None:
        return 100
    return VERDICT_CEILING.get(verdict, 100)


def _suppressed(
    dimension: str, verdict: Optional[str], sample_n: Optional[int],
    components: dict, *, reason: str,
) -> DialScore:
    return DialScore(
        dimension=dimension,
        score=None,
        letter=None,
        suppressed=True,
        verdict=verdict,
        components={**components, "ceiling": None},
        rationale=reason,
    )


def letter_band(score: Optional[int]) -> Optional[str]:
    """Map a 0..100 dial score to its letter band (A..E), or ``None`` when the
    score is suppressed. Bands per testing doc §2.3."""
    if score is None:
        return None
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    if score >= 20:
        return "D"
    return "E"


def score_outcome_dial(
    *,
    hit_rate: Optional[float] = None,
    edge_vs_priced: Optional[float] = None,
    relationship_strength: Optional[float] = None,
    sample_n: Optional[int] = None,
    min_trl: Optional[float] = None,
    verdict: Optional[str] = None,
) -> DialScore:
    """Score DIAL 1 (outcome). Weighted blend of analog hit-rate, edge-vs-priced
    (own prior vs market-priced odds), relationship strength (RELATIVE), and
    sample sufficiency, then clamp to ``VERDICT_CEILING[verdict]``. Suppressed
    when ``verdict == 'insufficient_data'`` or ``sample_n`` is below ``min_trl``.

    ``edge_vs_priced`` is a *signed* probability delta (own prior − market-priced
    probability, typically −1..1): a +edge lifts the dial above neutral, a −edge
    (market already agrees / contradicts) pulls it below. ``hit_rate`` /
    ``relationship_strength`` are 0..1.
    """
    components = {
        "hit_rate": hit_rate,
        "edge_vs_priced": edge_vs_priced,
        "relationship_strength": relationship_strength,
        "sample_n": sample_n,
        "min_trl": min_trl,
        "verdict": verdict,
    }
    below_trl = (
        sample_n is not None and min_trl is not None and sample_n < min_trl
    )
    if verdict == "insufficient_data" or below_trl:
        n_txt = sample_n if sample_n is not None else "?"
        return _suppressed(
            "outcome", verdict, sample_n, components,
            reason=f"Too few analog events (N={n_txt}) to score the outcome "
                   "dial honestly.",
        )

    parts: list[tuple[float, float]] = []
    if hit_rate is not None:
        parts.append((_clamp(hit_rate), _OUTCOME_WEIGHTS["hit_rate"]))
    if edge_vs_priced is not None:
        parts.append(
            (_clamp(0.5 + edge_vs_priced), _OUTCOME_WEIGHTS["edge_vs_priced"])
        )
    if relationship_strength is not None:
        parts.append(
            (_clamp(relationship_strength),
             _OUTCOME_WEIGHTS["relationship_strength"])
        )
    sample_norm = _sample_sufficiency(sample_n, min_trl)
    if sample_norm is not None:
        parts.append((sample_norm, _OUTCOME_WEIGHTS["sample"]))

    blend = _weighted_blend(parts)
    if blend is None:
        return _suppressed(
            "outcome", verdict, sample_n, components,
            reason="No scorable outcome inputs supplied.",
        )

    raw = round(blend * 100)
    ceiling = _ceiling_for(verdict)
    if ceiling is None:  # defensive — insufficient_data handled above
        return _suppressed(
            "outcome", verdict, sample_n, components,
            reason="Trust verdict suppresses the outcome dial.",
        )
    score = min(raw, ceiling)
    letter = letter_band(score)

    bits: list[str] = []
    if hit_rate is not None:
        bits.append(f"analog hit-rate {hit_rate:.0%}")
    if edge_vs_priced is not None:
        bits.append(f"edge vs priced-in {edge_vs_priced:+.0%}")
    if relationship_strength is not None:
        bits.append(f"relationship strength {relationship_strength:.0%}")
    if sample_n is not None:
        trl_txt = f" (MinTRL {min_trl:.0f})" if min_trl is not None else ""
        bits.append(f"N={sample_n}{trl_txt}")
    cap_txt = ""
    if raw > ceiling:
        cap_txt = f" — capped at {ceiling} by Trust verdict '{verdict}'"
    rationale = (
        f"Outcome {letter} ({score}/100): "
        + "; ".join(bits)
        + cap_txt
        + "."
    )
    return DialScore(
        dimension="outcome",
        score=score,
        letter=letter,
        suppressed=False,
        verdict=verdict,
        components={**components, "raw": raw, "ceiling": ceiling},
        rationale=rationale,
    )


def score_expression_dial(
    *,
    caar_bhar_alignment: Optional[float] = None,
    significance_p: Optional[float] = None,
    cost_survival: Optional[float] = None,
    payoff_pop: Optional[float] = None,
    verdict: Optional[str] = None,
    deflated_sharpe: Optional[float] = None,
    n_obs: Optional[int] = None,
    min_trl: Optional[float] = None,
) -> DialScore:
    """Score DIAL 2 (expression). Weighted blend of CAAR/BHAR alignment,
    statistical significance, cost-survivability, and option payoff geometry,
    clamped by a ceiling derived from the Trust verdict + DSR + N. Suppressed on
    ``insufficient_data`` / N below MinTRL.

    ``caar_bhar_alignment`` / ``cost_survival`` / ``payoff_pop`` are 0..1;
    ``significance_p`` is a p-value (lower is better). A non-positive
    ``deflated_sharpe`` (edge gone after selection-bias deflation) hard-caps the
    dial at the ``no_edge`` ceiling.
    """
    components = {
        "caar_bhar_alignment": caar_bhar_alignment,
        "significance_p": significance_p,
        "cost_survival": cost_survival,
        "payoff_pop": payoff_pop,
        "verdict": verdict,
        "deflated_sharpe": deflated_sharpe,
        "n_obs": n_obs,
        "min_trl": min_trl,
    }
    below_trl = n_obs is not None and min_trl is not None and n_obs < min_trl
    if verdict == "insufficient_data" or below_trl:
        n_txt = n_obs if n_obs is not None else "?"
        return _suppressed(
            "expression", verdict, n_obs, components,
            reason=f"Too few observations (N={n_txt}) to score the expression "
                   "dial honestly.",
        )

    parts: list[tuple[float, float]] = []
    if caar_bhar_alignment is not None:
        parts.append(
            (_clamp(caar_bhar_alignment), _EXPRESSION_WEIGHTS["alignment"])
        )
    if significance_p is not None:
        parts.append(
            (_significance_norm(significance_p),
             _EXPRESSION_WEIGHTS["significance"])
        )
    if cost_survival is not None:
        parts.append(
            (_clamp(cost_survival), _EXPRESSION_WEIGHTS["cost_survival"])
        )
    if payoff_pop is not None:
        parts.append((_clamp(payoff_pop), _EXPRESSION_WEIGHTS["payoff"]))

    blend = _weighted_blend(parts)
    if blend is None:
        return _suppressed(
            "expression", verdict, n_obs, components,
            reason="No scorable expression inputs supplied.",
        )

    raw = round(blend * 100)
    ceiling = _ceiling_for(verdict)
    if ceiling is None:  # defensive — insufficient_data handled above
        return _suppressed(
            "expression", verdict, n_obs, components,
            reason="Trust verdict suppresses the expression dial.",
        )
    dsr_capped = deflated_sharpe is not None and deflated_sharpe <= 0
    if dsr_capped:
        # Selection-bias deflation killed the edge — statistics can only cap.
        ceiling = min(ceiling, VERDICT_CEILING["no_edge"])
    score = min(raw, ceiling)
    letter = letter_band(score)

    bits: list[str] = []
    if caar_bhar_alignment is not None:
        bits.append(f"CAAR/BHAR alignment {caar_bhar_alignment:.0%}")
    if significance_p is not None:
        bits.append(f"significance p={significance_p:.3f}")
    if cost_survival is not None:
        bits.append(f"net-of-cost survival {cost_survival:.0%}")
    if payoff_pop is not None:
        bits.append(f"POP {payoff_pop:.0%}")
    cap_txt = ""
    if raw > score:
        if dsr_capped:
            cap_txt = (
                f" — capped at {ceiling} (deflated Sharpe "
                f"{deflated_sharpe:.2f} ≤ 0, selection bias)"
            )
        else:
            cap_txt = f" — capped at {ceiling} by Trust verdict '{verdict}'"
    rationale = (
        f"Expression {letter} ({score}/100): "
        + "; ".join(bits)
        + cap_txt
        + "."
    )
    return DialScore(
        dimension="expression",
        score=score,
        letter=letter,
        suppressed=False,
        verdict=verdict,
        components={**components, "raw": raw, "ceiling": ceiling},
        rationale=rationale,
    )


def score_historical_alignment(
    *,
    construction_alignment: Optional[float],
    pct_episodes_beat: Optional[float] = None,
    deflated_sharpe: Optional[float] = None,
    total_return_pct: Optional[float] = None,
    max_dd_pct: Optional[float] = None,
    verdict: Optional[str] = None,
    n_obs: Optional[int] = None,
    min_trl: Optional[float] = None,
    expression_dial: Optional[str] = None,
) -> Optional[dict]:
    """Per-EXPRESSION "historical alignment" dial → ``{score, letter}`` or ``None``.

    The detail page asks *"how well did THIS strategy line up before?"* — it must
    differ across the basket / option / pair of one view and move with the tier.
    The seeded ``expression_score`` answered with the view-level
    ``construction_alignment`` (identical for every expression), so we recompute
    here from this expression's OWN realised backtest evidence:

      • design       — ``construction_alignment`` (expression⇄belief fit)
      • consistency  — ``pct_episodes_beat`` (share of episodes it beat the bench)
      • edge         — ``deflated_sharpe`` that survived selection-bias deflation
      • reward:risk  — ``total_return_pct`` ÷ ``|max_dd_pct|``

    Every input is a real per-expression number from ``config.scores`` — nothing
    is fabricated. The blend is capped at ``VERDICT_CEILING[verdict]`` so an
    ``unproven`` strategy can never present as a proven one.

    Suppressed (``None``) — identical gate to the seeded dial — when the dial was
    marked SUPPRESSED, the verdict is ``insufficient_data``, N is below MinTRL, or
    no design score exists. The FE then shows 'not enough track record'.
    """
    if expression_dial is not None and str(expression_dial).strip().upper() == "SUPPRESSED":
        return None
    if verdict == "insufficient_data":
        return None
    if n_obs is not None and min_trl is not None and n_obs < min_trl:
        return None
    if construction_alignment is None or isinstance(construction_alignment, bool):
        return None
    if not isinstance(construction_alignment, (int, float)):
        return None

    parts: list[tuple[float, float]] = [
        (_clamp(construction_alignment / 100.0), _ALIGNMENT_BLEND_WEIGHTS["design"]),
    ]
    if isinstance(pct_episodes_beat, (int, float)) and not isinstance(pct_episodes_beat, bool):
        parts.append(
            (_clamp(pct_episodes_beat / 100.0), _ALIGNMENT_BLEND_WEIGHTS["consistency"])
        )
    if isinstance(deflated_sharpe, (int, float)) and not isinstance(deflated_sharpe, bool):
        parts.append(
            (_clamp(deflated_sharpe / _DSR_FULL_CREDIT), _ALIGNMENT_BLEND_WEIGHTS["edge"])
        )
    if (
        isinstance(total_return_pct, (int, float)) and not isinstance(total_return_pct, bool)
        and isinstance(max_dd_pct, (int, float)) and not isinstance(max_dd_pct, bool)
        and abs(max_dd_pct) > 1e-9
    ):
        rr = total_return_pct / abs(max_dd_pct)
        parts.append(
            (_clamp(rr / _RR_FULL_CREDIT), _ALIGNMENT_BLEND_WEIGHTS["reward_risk"])
        )

    blend = _weighted_blend(parts)
    if blend is None:
        return None
    raw = round(blend * 100)
    ceiling = _ceiling_for(verdict)
    score = raw if ceiling is None else min(raw, ceiling)
    score = int(max(0, min(100, score)))
    letter = letter_band(score)
    if letter is None:
        return None
    return {"score": score, "letter": letter}


# Allowed soft-input keys per dial (curator overrides are filtered to these so a
# stray key never explodes the keyword call).
_OUTCOME_KEYS = frozenset(
    {"hit_rate", "edge_vs_priced", "relationship_strength", "sample_n",
     "min_trl", "verdict"}
)
_EXPRESSION_KEYS = frozenset(
    {"caar_bhar_alignment", "significance_p", "cost_survival", "payoff_pop",
     "verdict", "deflated_sharpe", "n_obs", "min_trl"}
)


def _hit_rate_from_events(event_study: "EventStudyResult") -> Optional[float]:
    """Fraction of analog events whose CAR resolved in the thesis (+) direction.
    ``None`` when the sample is empty (caller then suppresses)."""
    series = event_study.car_by_event or ()
    if not series:
        return None
    positive = sum(1 for s in series if s.car is not None and s.car > 0)
    return positive / len(series)


def _alignment_from_events(event_study: "EventStudyResult") -> Optional[float]:
    """CAAR (+ BHAR sign agreement) -> 0..1 historical-alignment sub-score."""
    caar = event_study.caar
    if caar is None:
        return None
    align = _clamp(0.5 + caar * _CAAR_SCALE)
    mean_bhar = event_study.mean_bhar
    if mean_bhar is not None and ((mean_bhar >= 0) != (caar >= 0)):
        align *= 0.7  # CAR and BHAR disagree on sign — dampen
    return _clamp(align)


def _significance_p_from_events(
    event_study: "EventStudyResult",
) -> Optional[float]:
    """Pick the p-value: the agreeing (min) one when both tests reject, else the
    conservative (max) one — a single disagreeing test must not look reliable."""
    sig = event_study.significance
    if sig is None:
        return None
    ps = [p for p in (sig.bmp_p, sig.nonparam_p) if p is not None]
    if not ps:
        return None
    return min(ps) if getattr(sig, "both_agree", False) else max(ps)


def two_dial_score(
    *,
    event_study: Optional["EventStudyResult"] = None,
    surprise: Optional["SurpriseFraming"] = None,
    outcome_overrides: Optional[dict] = None,
    expression_overrides: Optional[dict] = None,
) -> TwoDialScore:
    """High-level Alignment Score: derive each dial's inputs from an
    ``EventStudyResult`` (verdict / significance / forward-stats) + a
    ``SurpriseFraming`` (hit-rate, edge-vs-priced from the hidden prior), score
    both dials SEPARATELY via :func:`score_outcome_dial` /
    :func:`score_expression_dial`, and bundle the shared Trust flags. Explicit
    ``*_overrides`` let the curator supply RELATIVE/THEME inputs the event study
    doesn't produce. Never averages the two dials.
    """
    flags: list[str] = []
    o_in: dict = {}
    e_in: dict = {}

    if event_study is not None:
        verdict_block = event_study.verdict or {}
        verdict = verdict_block.get("verdict")
        flags.extend(verdict_block.get("flags") or [])
        fs = event_study.forward_stats or {}

        # Outcome dial — derived from the analog sample. NOTE: ``min_trl`` is
        # deliberately NOT carried over from forward_stats — that MinTRL is in
        # RETURN-OBSERVATION units (the expression dial's axis), whereas the
        # outcome dial counts analog EVENTS (N). Mixing them would gate a 12-
        # analog sample against a 60-return-obs floor. Suppression of a thin
        # analog sample is already driven by the Trust ``verdict``
        # (``insufficient_data``); sample sufficiency saturates at ~12 analogs.
        o_in["verdict"] = verdict
        o_in["sample_n"] = event_study.n_events
        o_in["hit_rate"] = _hit_rate_from_events(event_study)

        # Expression dial — derived from the event study + Trust battery.
        e_in["verdict"] = verdict
        e_in["deflated_sharpe"] = fs.get("deflated_sharpe")
        e_in["n_obs"] = fs.get("n_obs")
        e_in["min_trl"] = fs.get("min_trl")
        e_in["caar_bhar_alignment"] = _alignment_from_events(event_study)
        e_in["significance_p"] = _significance_p_from_events(event_study)

    if surprise is not None:
        # Edge-vs-priced = own prior (analog hit-rate) − market-priced
        # probability. Prefer Pivot's option-implied probability (user-facing);
        # fall back to the HIDDEN prediction-market prior when option-implied is
        # unavailable. Never surfaces the PM odds — only the delta feeds a dial.
        priced = surprise.implied_probability
        if priced is None:
            priced = surprise.hidden_prior
        hr = o_in.get("hit_rate")
        if priced is not None and hr is not None:
            o_in["edge_vs_priced"] = hr - priced

    if outcome_overrides:
        o_in.update(
            {k: v for k, v in outcome_overrides.items()
             if k in _OUTCOME_KEYS and v is not None}
        )
    if expression_overrides:
        e_in.update(
            {k: v for k, v in expression_overrides.items()
             if k in _EXPRESSION_KEYS and v is not None}
        )

    outcome = score_outcome_dial(**o_in)
    expression = score_expression_dial(**e_in)
    # De-duplicate flags, preserving order.
    return TwoDialScore(
        outcome=outcome,
        expression=expression,
        flags=tuple(dict.fromkeys(flags)),
    )


def persist_confidence(
    db: "Session",
    view_id: str,
    two_dial: TwoDialScore,
) -> list["ViewConfidence"]:
    """Upsert the two ``view_confidence`` rows (one per dimension).

    ``ViewConfidence.score`` is Float 0..1, so store ``dial.score / 100``
    (``None`` when suppressed). ``evidence`` carries the dial rationale. Honours
    the UNIQUE(view_id, dimension) constraint (upsert, not duplicate). Does NOT
    commit (caller owns the txn). Returns the two ORM rows.

    Reads/writes: ``view_confidence`` (write); ``market_views`` (FK target).
    """
    from backend.models import ConfidenceDimension, ViewConfidence

    rows: list["ViewConfidence"] = []
    for dial in (two_dial.outcome, two_dial.expression):
        dimension = ConfidenceDimension(dial.dimension)
        score_frac = None if dial.score is None else dial.score / 100.0
        existing = (
            db.query(ViewConfidence)
            .filter(
                ViewConfidence.view_id == view_id,
                ViewConfidence.dimension == dimension,
            )
            .one_or_none()
        )
        if existing is None:
            row = ViewConfidence(
                view_id=view_id,
                dimension=dimension,
                score=score_frac,
                evidence=dial.rationale,
            )
            db.add(row)
        else:
            existing.score = score_frac
            existing.evidence = dial.rationale
            row = existing
        rows.append(row)

    # Flush (not commit) so PKs/defaults populate while the caller keeps the
    # transaction open (curation-service unit-of-work).
    db.flush()
    return rows


__all__ = [
    "VERDICT_CEILING",
    "DialScore",
    "TwoDialScore",
    "letter_band",
    "score_outcome_dial",
    "score_expression_dial",
    "score_historical_alignment",
    "two_dial_score",
    "persist_confidence",
]
