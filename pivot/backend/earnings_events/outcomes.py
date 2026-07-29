"""Result type for the earnings-outcome verifier.

Kept dependency-free so both the verifier and the scheduler poll loop
can import it without dragging in yfinance / pandas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional


# The decision vocabulary the verifier can return.
#   beat / miss / meet  — the just-announced quarter judged against the
#                         consensus estimate (reported > / < / ≈ estimate)
#   unknown             — no confident answer; the caller MUST NOT fire
EarningsDecision = Literal["beat", "miss", "meet", "unknown"]

EARNINGS_DECISIONS: frozenset[str] = frozenset({"beat", "miss", "meet", "unknown"})


@dataclass(frozen=True)
class EarningsOutcome:
    """One earnings-verification verdict.

    ``matched`` is the only field the scheduler keys on to decide whether
    to fire: it is True **iff** the just-announced quarter's reported
    metric, compared to consensus estimate, equals the user's
    ``condition`` ("beat" | "miss" | "meet"). Everything else is for the
    audit context that rides into ``fire_external_event(audit_context=...)``.

    yfinance returns reported + estimated numbers DIRECTLY, so
    verification is arithmetic, not LLM parsing. Confidence is therefore
    1.0 whenever both numbers are concrete; absent data fails closed to
    :py:meth:`unknown`.
    """

    matched: bool
    decision: EarningsDecision
    metric: str                       # "eps" | "revenue"
    reported: Optional[float]
    estimate: Optional[float]
    surprise_pct: Optional[float]
    confidence: float
    evidence: Optional[str] = None
    audit: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def unknown(
        cls,
        reason: str,
        *,
        metric: str = "eps",
        reported: Optional[float] = None,
        estimate: Optional[float] = None,
        surprise_pct: Optional[float] = None,
        evidence: Optional[str] = None,
    ) -> "EarningsOutcome":
        """Fail-safe verdict: never fires. Used whenever the verifier
        cannot reach a confident conclusion (quarter not yet reported,
        unsupported metric coverage, fetch failure, missing estimate)."""
        return cls(
            matched=False,
            decision="unknown",
            metric=metric,
            reported=reported,
            estimate=estimate,
            surprise_pct=surprise_pct,
            confidence=0.0,
            evidence=evidence,
            audit={"reason": reason} if reason else {},
        )
