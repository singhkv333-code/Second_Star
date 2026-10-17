"""Earnings-outcome verifier.

``verify_earnings_outcome`` answers one question: *for symbol X, did the
just-announced quarter's ``metric`` (EPS or revenue) ``beat`` / ``miss``
/ ``meet`` consensus estimate?* This is the safety-critical gate between
"the calendar says a release window is open" and "fire a real action".

There is no LLM tier here: yfinance returns the *reported* and *estimate*
numbers DIRECTLY, so the comparison is plain arithmetic. Confidence is
therefore 1.0 whenever both numbers are concrete; absent data fails
closed to :py:meth:`EarningsOutcome.unknown`.

Fail-safe philosophy:

  * No estimate or reported number for the latest quarter → ``unknown``.
  * Revenue estimate / reported coverage is thin in yfinance's
    ``get_earnings_dates`` payload — we surface ``unknown`` rather than
    guess. (Roadmap: wire :class:`yfinance.Ticker.earnings_estimate` /
    ``revenue_estimate`` when we need revenue parity.)
  * Anything else suspicious → ``unknown``.

The scheduler fires only when ``outcome.matched is True``.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from backend.earnings_events.outcomes import EarningsOutcome

logger = logging.getLogger(__name__)


# Tolerance band (in % surprise) for the "meet" verdict — yfinance's
# surprise(%) field is the canonical way to ask "was this in-line?",
# but releases that hit the estimate to the cent often print 0.0%
# surprise, so we accept a small band rather than an exact-equality test.
_MEET_BAND_PCT: float = 1.0


def _latest_reported_row(rows: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Pick the row with the most recent ``report_date`` whose
    ``reported_eps`` is a concrete number — that is the just-announced
    quarter. Future quarters carry ``reported_eps = None`` (yfinance
    leaves them blank until the company actually files), so they are
    correctly skipped.

    Returns ``None`` when no row in the window has been reported yet.
    """
    candidates = [
        r for r in (rows or [])
        if r.get("reported_eps") is not None
    ]
    if not candidates:
        return None
    # rows are sorted ascending by report_date (calendar contract);
    # most-recent reported = the last one.
    candidates.sort(key=lambda r: r["report_date"])
    return candidates[-1]


def _decide_eps(
    *,
    reported: float,
    estimate: float,
    surprise_pct: Optional[float],
    surprise_threshold_pct: Optional[float],
) -> tuple[str, float]:
    """Arithmetic decision: beat / miss / meet, plus the surprise %
    actually used. ``surprise_threshold_pct`` (when given) gates the
    "beat" verdict: a 0.2 % beat doesn't count if the user requires 5 %.
    """
    if surprise_pct is None:
        if estimate == 0:
            # Avoid div-by-zero; if the consensus is exactly zero we
            # cannot compute a meaningful surprise %.
            surprise_pct = 0.0 if reported == 0 else float("inf") * (
                1.0 if reported > 0 else -1.0
            )
        else:
            surprise_pct = ((reported - estimate) / abs(estimate)) * 100.0

    # "meet" first — a 0.1 % beat against a 0.0 % threshold is still
    # effectively in-line.
    if abs(surprise_pct) <= _MEET_BAND_PCT or reported == estimate:
        return "meet", surprise_pct
    if reported > estimate:
        if (
            surprise_threshold_pct is not None
            and surprise_pct < surprise_threshold_pct
        ):
            # Beat the consensus, but not by the magnitude the user
            # required. Treat as "meet" (not enough oomph to call a beat,
            # but not a miss).
            return "meet", surprise_pct
        return "beat", surprise_pct
    return "miss", surprise_pct


async def verify_earnings_outcome(
    symbol: str,
    metric: str,
    condition: str,
    *,
    surprise_threshold_pct: Optional[float] = None,
    min_confidence: float = 0.85,
    fetch: Optional[Callable[[str], list[dict[str, Any]]]] = None,
) -> EarningsOutcome:
    """Verify the latest quarter's outcome for ``symbol`` against the
    user's ``condition``.

    Parameters
    ----------
    symbol:
        Ticker the user is watching (e.g. ``"INFY"``). Routed through
        :func:`backend.market.yfinance_service.resolve_symbol` by the
        default fetcher.
    metric:
        ``"eps"`` (supported) or ``"revenue"`` (currently surfaces
        ``unknown`` — see module docstring).
    condition:
        What the user is waiting for: ``"beat"`` / ``"miss"`` / ``"meet"``.
    surprise_threshold_pct:
        Optional magnitude gate on "beat". A 0.5 % surprise vs. a 5 %
        threshold is downgraded to "meet".
    min_confidence:
        Passed through for API symmetry with the macro verifier; this
        path produces confidence 1.0 when concrete numbers exist and 0.0
        otherwise, so this is effectively a guardrail (caller can require
        >= 0.85 and the verifier will honour it).
    fetch:
        Injection seam — callable ``symbol -> list[row]`` (same row shape
        as :func:`backend.earnings_events.calendar.fetch_earnings_rows`).
        Defaults to the live yfinance-backed fetcher.

    Returns
    -------
    :class:`EarningsOutcome`. The scheduler fires only when
    ``outcome.matched`` is True.
    """
    metric_norm = (metric or "").strip().lower()
    condition_norm = (condition or "").strip().lower()
    if condition_norm not in {"beat", "miss", "meet"}:
        return EarningsOutcome.unknown(
            f"unsupported condition {condition!r}",
            metric=metric_norm or "eps",
        )
    if metric_norm not in {"eps", "revenue"}:
        return EarningsOutcome.unknown(
            f"unsupported metric {metric!r}",
            metric=metric_norm or "eps",
        )

    # Revenue coverage in yfinance's get_earnings_dates is thin — fail
    # closed rather than guess. Roadmap: wire revenue_estimate /
    # quarterly_financials when we need parity.
    if metric_norm == "revenue":
        return EarningsOutcome.unknown(
            "revenue metric not yet supported by earnings verifier",
            metric="revenue",
        )

    # Resolve rows via injected fetcher when present, else the live one.
    if fetch is None:
        from backend.earnings_events.calendar import fetch_earnings_rows
        fetch = fetch_earnings_rows
    try:
        rows = fetch(symbol) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[earnings_verifier] fetch failed sym=%s err=%s", symbol, exc,
        )
        return EarningsOutcome.unknown(
            f"earnings fetch failed: {exc}", metric=metric_norm,
        )

    # Calendar contract is "ascending by report_date"; the live fetcher
    # may return newest-first. Sort defensively here so _latest_reported_row
    # works on either ordering.
    try:
        rows = sorted(rows, key=lambda r: r["report_date"])
    except Exception:  # noqa: BLE001
        rows = list(rows or [])

    latest = _latest_reported_row(rows)
    if latest is None:
        return EarningsOutcome.unknown(
            "no reported quarter in the calendar window",
            metric=metric_norm,
        )

    reported = latest.get("reported_eps")
    estimate = latest.get("eps_estimate")
    surprise_pct = latest.get("surprise_pct")

    if reported is None or estimate is None:
        return EarningsOutcome.unknown(
            "reported or estimate value missing for latest quarter",
            metric=metric_norm,
            reported=reported,
            estimate=estimate,
            surprise_pct=surprise_pct,
        )

    decision, used_surprise = _decide_eps(
        reported=float(reported),
        estimate=float(estimate),
        surprise_pct=(float(surprise_pct) if surprise_pct is not None else None),
        surprise_threshold_pct=surprise_threshold_pct,
    )

    # Confidence is 1.0 — these are REPORTED numbers, not an LLM guess.
    # We still honour ``min_confidence`` so the caller can dial up a
    # higher floor (e.g. > 1.0 to force always-unknown for dry-runs).
    confidence = 1.0
    if confidence < min_confidence:
        return EarningsOutcome.unknown(
            f"confidence {confidence:.2f} below floor {min_confidence:.2f}",
            metric=metric_norm,
            reported=float(reported),
            estimate=float(estimate),
            surprise_pct=used_surprise,
        )

    matched = (decision == condition_norm)
    report_date = latest.get("report_date")
    report_date_iso = (
        report_date.isoformat() if hasattr(report_date, "isoformat") else None
    )
    audit: dict[str, Any] = {
        "symbol": symbol.strip().upper(),
        "metric": metric_norm,
        "condition": condition_norm,
        "decision": decision,
        "reported": float(reported),
        "estimate": float(estimate),
        "surprise_pct": used_surprise,
        "surprise_threshold_pct": surprise_threshold_pct,
        "report_date": report_date_iso,
    }
    evidence = (
        f"{symbol.strip().upper()} Q reported {metric_norm.upper()}="
        f"{float(reported):g} vs estimate {float(estimate):g} "
        f"(surprise {used_surprise:+.2f}%)"
    )
    return EarningsOutcome(
        matched=matched,
        decision=decision,  # type: ignore[arg-type]
        metric=metric_norm,
        reported=float(reported),
        estimate=float(estimate),
        surprise_pct=used_surprise,
        confidence=confidence,
        evidence=evidence,
        audit=audit,
    )
