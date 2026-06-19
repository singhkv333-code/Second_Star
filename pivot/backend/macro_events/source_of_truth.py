"""Per-category source-of-truth table for macro-event verification.

This is Requirement #4 of the upgrade: every accepted macro event
declares the *canonical* source we verify its outcome against, plus the
keyword filter that isolates the relevant release in that feed and the
prediction-market query used for the Tier-3 fallback.

The ``primary_source_id`` values are foreign keys into the
``news_events`` source registry (``backend/news_events/config.py``) so
the existing ``get_source()`` + ``RSSAdapter`` machinery is reused
verbatim — no new fetch code, just new rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


DecisionKind = Literal["rate", "print"]


@dataclass(frozen=True)
class SourceOfTruth:
    kind: str
    label: str
    # FK into news_events.config._REGISTRY — the official feed we read.
    primary_source_id: str
    # Lower-cased substrings; an RSS item must contain at least one to be
    # considered the relevant release (keeps us off unrelated headlines
    # in the same feed).
    match_keywords: tuple[str, ...]
    # 'rate' → verifier extracts a cut/hold/hike decision.
    # 'print' → verifier extracts a numeric figure and compares it to the
    #            user's threshold to yield met/not_met.
    decision_kind: DecisionKind
    # Natural-language query for the Tier-3 prediction-market fallback
    # (Polymarket/Kalshi search). None disables the fallback for this kind.
    pm_fallback_query: Optional[str]
    notes: str = ""


# The conservative-beta allow-list. Keep these four in lock-step with
# ``_ALLOWED_MACRO_KINDS`` in backend/workflows/propose.py.
_TABLE: dict[str, SourceOfTruth] = {
    "rbi_mpc": SourceOfTruth(
        kind="rbi_mpc",
        label="RBI MPC repo-rate decision",
        primary_source_id="rbi_press_releases",
        match_keywords=(
            "repo rate", "monetary policy", "mpc", "policy rate",
            "reverse repo", "rate decision",
        ),
        decision_kind="rate",
        pm_fallback_query="RBI repo rate cut",
        notes="Official: RBI Press Releases RSS. The MPC resolution "
        "headline names the repo-rate action verbatim.",
    ),
    "us_fomc": SourceOfTruth(
        kind="us_fomc",
        label="US FOMC rate decision",
        primary_source_id="fed_press_monetary",
        match_keywords=(
            "fomc", "federal funds", "federal open market",
            "target range", "monetary policy",
        ),
        decision_kind="rate",
        pm_fallback_query="Fed interest rate decision cut",
        notes="Official: Federal Reserve monetary press-release RSS. The "
        "statement states the target-range action.",
    ),
    "india_cpi": SourceOfTruth(
        kind="india_cpi",
        label="India CPI inflation print",
        primary_source_id="google_news_india_cpi",
        match_keywords=(
            "cpi", "consumer price", "retail inflation", "inflation",
        ),
        decision_kind="print",
        # No PM fallback for CPI: a generic prediction-market query can't
        # confirm the user's specific numeric threshold (met/not_met), so
        # falling back would risk firing on the wrong comparison. Print
        # kinds verify ONLY off the official figure.
        pm_fallback_query=None,
        notes="Official figure is MOSPI; we read a Google-News CPI RSS "
        "query (MOSPI has no machine feed). Numeric figure compared to "
        "the user's threshold.",
    ),
    "us_cpi": SourceOfTruth(
        kind="us_cpi",
        label="US CPI inflation print",
        primary_source_id="google_news_us_cpi",
        match_keywords=(
            "cpi", "consumer price", "inflation",
        ),
        decision_kind="print",
        # See india_cpi: no PM fallback for numeric-threshold print kinds.
        pm_fallback_query=None,
        notes="Official figure is BLS; we read a Google-News CPI RSS "
        "query (no clean BLS feed). Numeric figure compared to the "
        "user's threshold.",
    ),
}


def get_source_of_truth(kind: str) -> Optional[SourceOfTruth]:
    """Lookup by macro-event kind. None for unknown kinds."""
    return _TABLE.get(kind)


def all_kinds() -> tuple[str, ...]:
    return tuple(_TABLE.keys())
