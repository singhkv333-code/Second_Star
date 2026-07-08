"""Result type for the macro-event verifier.

Kept dependency-free so both the verifier and the scheduler poll loop
can import it without dragging in httpx / the LLM client.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# The decision vocabulary the verifier can return.
#   cut / hold / hike  — central-bank rate decisions (rbi_mpc, us_fomc)
#   met / not_met      — numeric prints judged against a user threshold
#                        (india_cpi, us_cpi)
#   unknown            — no confident answer; the caller MUST NOT fire
MacroDecision = Literal["cut", "hold", "hike", "met", "not_met", "unknown"]

MACRO_DECISIONS: frozenset[str] = frozenset(
    {"cut", "hold", "hike", "met", "not_met", "unknown"}
)

# Which tier produced the answer (for the audit trail / debugging).
VerifierTier = Literal["official", "llm", "prediction_market", "none"]


@dataclass(frozen=True)
class OutcomeResult:
    """One verification verdict.

    ``matched`` is the only field the scheduler keys on to decide
    whether to fire: it is True **iff** the verifier confidently
    determined that the observed outcome equals the user's
    ``expected_outcome``. Everything else is for the audit context that
    rides into ``fire_external_event(audit_context=...)``.
    """

    matched: bool
    decision: MacroDecision
    confidence: float
    tier: VerifierTier
    evidence: str | None = None
    audit: dict = field(default_factory=dict)

    @classmethod
    def unknown(cls, *, reason: str = "", tier: VerifierTier = "none") -> "OutcomeResult":
        """Fail-safe verdict: never fires. Used whenever the verifier
        cannot reach a confident conclusion (no source text, low
        confidence, hallucination guard tripped, network down)."""
        return cls(
            matched=False,
            decision="unknown",
            confidence=0.0,
            tier=tier,
            evidence=None,
            audit={"reason": reason} if reason else {},
        )
