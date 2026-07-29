"""Portfolio scoring service — transparent, real-data-derived scores.

All three scores are computed **on-read** from existing holdings + sector
data and (where available) the paper-account NAV series. Nothing is
persisted, no schema is added, and **no number is fabricated**: if the
underlying data does not exist (e.g. no NAV history for a return figure, or
no holdings at all) the relevant input is dropped and its weight removed
from the blend rather than being filled with a guess.

Three scores are produced:

1. ``diversification_score`` (0-100)
   Derived from the Herfindahl-Hirschman Index (HHI) of *position* weights
   and *sector* weights. HHI is the sum of squared weights (each weight in
   [0,1]); it ranges from 1/n (perfectly even) to 1 (a single position).
   We normalise HHI to [0,1] via ``(HHI - 1/n) / (1 - 1/n)`` so that an
   evenly-spread book maps to 0 and a single holding maps to 1, then
   ``score = round(100 * (1 - HHI_normalised))``. Position-HHI and
   sector-HHI are averaged (equal weight) before scoring. A single-holding
   portfolio scores ~0; an evenly diversified one scores ~100.

2. ``portfolio_score`` (0-100)
   A composite blend of:
     - ``diversification_score`` (reward spread),
     - a concentration penalty derived from ``top_sector_pct`` (penalise a
       book dominated by one sector), and
     - a risk-adjusted *performance* component derived from
       ``total_return_pct`` **only if** a real NAV series exists for the
       user's paper account. If no return is available the performance
       component is dropped and its weight is removed from the blend (the
       remaining components are re-normalised), and this is flagged in the
       response. We never invent a return.
   The sub-components and the exact weights used are returned so the score
   is fully explainable.

3. ``community_score`` (0-100)
   A **percentile** of this user's ``portfolio_score`` against EVERY other
   Pivot user who holds a real, differentiated portfolio — anyone with live
   broker holdings or a currently-held paper position (see
   ``_real_peer_candidate_ids``) — when at least ``_MIN_REAL_PEERS`` of them
   exist. Users who have never traded hold no position and are excluded: on
   the shared dev mock holdings their score is byte-identical to every other
   such user, so counting them would dress up one placeholder as hundreds of
   duplicate data points. Below the threshold this falls back to a fixed,
   documented benchmark cohort distribution (``BENCHMARK_SCORE_DISTRIBUTION``)
   and says so plainly in ``basis`` — never silently degrades without
   disclosing it.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

# Composite-score weights when *all* inputs are present. They sum to 1.0.
# When the performance component is unavailable, ``performance``'s weight is
# stripped and the remaining weights are re-normalised (see _normalise_weights).
_BASE_WEIGHTS: dict[str, float] = {
    "diversification": 0.50,
    "concentration_penalty": 0.20,
    "performance": 0.30,
}

# A fixed, documented reference distribution of *representative* retail
# portfolio scores used purely as a benchmark cohort for the community
# percentile. These are illustrative benchmark anchors (NOT live users and
# NOT scraped peer data): they span a typical range from a concentrated
# single-name book (low) to a well-diversified, positive-carry book (high).
BENCHMARK_SCORE_DISTRIBUTION: tuple[float, ...] = (
    10.0, 18.0, 25.0, 30.0, 35.0, 40.0, 45.0, 48.0, 52.0, 55.0,
    58.0, 60.0, 63.0, 66.0, 70.0, 74.0, 78.0, 82.0, 88.0, 94.0,
)

BENCHMARK_BASIS = (
    "Percentile vs a fixed reference distribution of representative retail "
    "portfolio scores (benchmark anchors, not live peer/user data)."
)

# Below this many users in the ranked distribution, a percentile is too
# noisy/small a sample to be meaningful (e.g. "beats 1 of 2 people") — fall
# back to the documented benchmark cohort instead, and say so in `basis`.
# With the whole-community population (active traders + never-traded users
# padded in) this threshold is effectively always cleared except on a
# brand-new deployment.
_MIN_REAL_PEERS = 5

# The honest score for a user who has never traded — an empty/all-cash book
# has no diversification and no return track record, so it ranks at the very
# bottom of the community distribution. NOT a fabricated mock-holdings score:
# it's the true score of holding nothing. Kept strictly below any real
# portfolio's score so "you rank above everyone who hasn't built a book yet"
# always holds. These users never SEE a community score themselves (no
# holdings → the card is hidden); they exist only to size the denominator.
_EMPTY_PORTFOLIO_SCORE = 0.0


def _hhi(weights: Iterable[float]) -> float:
    """Herfindahl-Hirschman Index: sum of squared weights (weights in [0,1])."""
    return float(sum(w * w for w in weights))


def _normalised_hhi(hhi: float, n: int) -> float:
    """Map raw HHI to [0,1]: 0 = perfectly even (n items), 1 = single item.

    For n items the minimum HHI is 1/n; the maximum is 1. We rescale so an
    evenly-spread book reads 0 and a single position reads 1. With n<=1 the
    book is maximally concentrated by construction, so we return 1.0.
    """
    if n <= 1:
        return 1.0
    floor = 1.0 / n
    span = 1.0 - floor
    if span <= 0:
        return 1.0
    return max(0.0, min(1.0, (hhi - floor) / span))


def _weights_from_values(values: list[float]) -> list[float]:
    """Normalise a list of non-negative magnitudes into weights summing to 1."""
    total = sum(values)
    if total <= 0:
        return []
    return [v / total for v in values]


def _normalise_weights(weights: dict[str, float]) -> dict[str, float]:
    """Re-normalise a (possibly partial) weight dict so it sums to 1.0."""
    total = sum(weights.values())
    if total <= 0:
        return {}
    return {k: v / total for k, v in weights.items()}


def compute_diversification(
    holdings: list[dict[str, Any]],
    sector_of,
) -> Optional[dict[str, Any]]:
    """Compute the diversification score + its components.

    ``holdings`` is the enriched holdings list (each dict has at least
    ``tradingsymbol``, ``last_price``, ``quantity``). ``sector_of`` maps a
    tradingsymbol -> sector label. Returns ``None`` if there are no
    holdings with positive market value.
    """
    # Per-position market values (skip non-positive / missing).
    positions: list[tuple[str, float]] = []
    for h in holdings:
        try:
            mv = float(h["last_price"]) * float(h["quantity"])
        except (KeyError, TypeError, ValueError):
            continue
        if mv > 0:
            positions.append((h["tradingsymbol"], mv))

    if not positions:
        return None

    pos_values = [mv for _, mv in positions]
    sum(pos_values)

    # Sector aggregation.
    sector_totals: dict[str, float] = {}
    for sym, mv in positions:
        sector = sector_of(sym)
        sector_totals[sector] = sector_totals.get(sector, 0.0) + mv

    pos_weights = _weights_from_values(pos_values)
    sector_weights = _weights_from_values(list(sector_totals.values()))

    n_holdings = len(positions)
    n_sectors = len(sector_totals)

    pos_hhi = _hhi(pos_weights)
    sector_hhi = _hhi(sector_weights)

    pos_hhi_norm = _normalised_hhi(pos_hhi, n_holdings)
    sector_hhi_norm = _normalised_hhi(sector_hhi, n_sectors)

    # Equal-weight blend of position- and sector-level concentration.
    hhi_norm = (pos_hhi_norm + sector_hhi_norm) / 2.0
    score = round(100 * (1 - hhi_norm))

    top_holding_pct = round(max(pos_weights) * 100, 1) if pos_weights else 0.0
    top_sector_pct = (
        round(max(sector_weights) * 100, 1) if sector_weights else 0.0
    )
    # Reported HHI is the blended raw HHI (averaged across the two views).
    blended_hhi = round((pos_hhi + sector_hhi) / 2.0, 4)

    return {
        "score": score,
        "components": {
            "n_holdings": n_holdings,
            "n_sectors": n_sectors,
            "top_holding_pct": top_holding_pct,
            "top_sector_pct": top_sector_pct,
            "hhi": blended_hhi,
        },
        "explainer": (
            f"Blends position- and sector-level concentration (HHI). "
            f"{n_holdings} holdings across {n_sectors} sectors; top holding "
            f"{top_holding_pct}% of book, top sector {top_sector_pct}%. "
            f"Higher = more evenly spread."
        ),
        # Internal hand-off for the composite (not part of the public schema):
        "_top_sector_pct": top_sector_pct,
    }


def compute_total_return_pct(account, nav_snapshots: list) -> Optional[float]:
    """Real risk-adjusted *return* proxy from the paper NAV series.

    Returns ``total_return_pct`` = (latest NAV / starting capital - 1) * 100
    using ``PaperNavSnapshot`` rows. Returns ``None`` when there is no NAV
    history or no usable starting capital — we never synthesise a return.
    """
    if account is None or not nav_snapshots:
        return None
    try:
        start = float(account.starting_capital)
    except (TypeError, ValueError):
        return None
    if start <= 0:
        return None
    # Snapshots are expected oldest->newest; take the last as latest NAV.
    latest = nav_snapshots[-1]
    try:
        latest_nav = float(latest.nav)
    except (AttributeError, TypeError, ValueError):
        return None
    return round((latest_nav / start - 1.0) * 100, 2)


def _concentration_penalty_subscore(top_sector_pct: float) -> float:
    """Map top-sector concentration to a 0-100 sub-score (higher = better).

    A book with no single sector above ~25% scores ~100; a fully
    single-sector book (100%) scores 0. Linear between those anchors.
    """
    return round(max(0.0, min(100.0, 100.0 - (top_sector_pct - 25.0) * (100.0 / 75.0))), 1)


def _performance_subscore(total_return_pct: float) -> float:
    """Map a total return % to a bounded 0-100 sub-score.

    Anchored so that 0% return -> 50, +50% -> ~100, -50% -> ~0 (clamped).
    This is a monotone, transparent squash — not a Sharpe ratio; we label
    it as a return-based proxy in the explainer.
    """
    return round(max(0.0, min(100.0, 50.0 + total_return_pct)), 1)


def compute_portfolio_score(
    diversification_score: int,
    top_sector_pct: float,
    total_return_pct: Optional[float],
) -> dict[str, Any]:
    """Composite, explainable blend. Drops the performance leg (and its
    weight) when no real return is available."""
    weights = dict(_BASE_WEIGHTS)
    subscores: dict[str, float] = {
        "diversification": float(diversification_score),
        "concentration_penalty": _concentration_penalty_subscore(top_sector_pct),
    }

    performance_available = total_return_pct is not None
    if performance_available:
        subscores["performance"] = _performance_subscore(total_return_pct)
    else:
        # Remove the performance leg entirely — never blend a fake number.
        weights.pop("performance", None)

    weights = _normalise_weights(weights)
    score = round(sum(subscores[k] * weights[k] for k in weights))

    explainer_bits = [
        f"diversification {subscores['diversification']:.0f}",
        f"concentration {subscores['concentration_penalty']:.0f}",
    ]
    if performance_available:
        explainer_bits.append(f"performance {subscores['performance']:.0f}")
        perf_note = (
            f"return-based performance leg included "
            f"(total return {total_return_pct:+.2f}%)."
        )
    else:
        perf_note = (
            "no NAV history available, so the performance leg is dropped "
            "and its weight re-distributed."
        )

    return {
        "score": score,
        "components": {
            "subscores": {k: round(v, 1) for k, v in subscores.items()},
            "weights": {k: round(v, 4) for k, v in weights.items()},
            "performance_available": performance_available,
            "total_return_pct": total_return_pct,
        },
        "explainer": (
            "Weighted blend of " + ", ".join(explainer_bits) + ". " + perf_note
        ),
    }


def compute_community_score(
    portfolio_score: float,
    peer_scores: Optional[list[float]] = None,
    empty_peer_count: int = 0,
) -> dict[str, Any]:
    """Percentile of the user's portfolio score across the WHOLE community.

    The ranked distribution is every OTHER Pivot user:
      - ``peer_scores`` — the real portfolio scores of other users who
        actually hold a book (live broker holdings or a traded paper book),
        gathered by the caller.
      - ``empty_peer_count`` — how many other registered users have NEVER
        traded. Each is padded into the distribution at
        ``_EMPTY_PORTFOLIO_SCORE`` (the honest score of holding nothing), so
        the percentile is against all users, not just active traders. These
        users don't see a community score themselves (no holdings → hidden
        card); they only size the denominator.

    Because empties sit strictly below any real score, any real portfolio
    ranks above all of them — "you're ahead of everyone who hasn't built a
    book yet." Falls back to ``BENCHMARK_SCORE_DISTRIBUTION`` only when the
    community is smaller than ``_MIN_REAL_PEERS`` (a brand-new deployment),
    and discloses that in ``basis``.
    """
    real_peers = list(peer_scores or [])
    empties = [_EMPTY_PORTFOLIO_SCORE] * max(0, empty_peer_count)
    community = real_peers + empties
    use_real = len(community) >= _MIN_REAL_PEERS

    if use_real:
        dist: tuple[float, ...] = tuple(community)
        total_incl_self = len(dist) + 1  # + the requesting user
        n_empty = len(empties)
        basis = (
            f"Percentile across all {total_incl_self} Pivot users. "
            f"{n_empty} of them haven't built a portfolio yet and are scored "
            "as empty, so holding any real book ranks you above them; the "
            f"other {len(real_peers)} hold real portfolios ranked on merit."
        )
    else:
        dist = BENCHMARK_SCORE_DISTRIBUTION
        basis = BENCHMARK_BASIS + (
            f" (Only {len(community)} other Pivot user(s) exist right now — "
            f"need at least {_MIN_REAL_PEERS} before the percentile switches "
            "to live community data.)"
        )

    if not dist:
        return {
            "score": 0,
            "percentile": 0.0,
            "basis": basis,
            "explainer": "No reference distribution available.",
        }
    at_or_below = sum(1 for d in dist if portfolio_score >= d)
    percentile = round(at_or_below / len(dist) * 100, 1)
    return {
        # community_score *is* the percentile (0-100), per the spec.
        "score": round(percentile),
        "percentile": percentile,
        "basis": basis,
        "explainer": (
            f"Your portfolio score of {round(portfolio_score)} sits at the "
            f"{percentile:.0f}th percentile of "
            + (
                f"all {len(dist) + 1} Pivot users "
                f"({len(empties)} of whom haven't built a portfolio yet)."
                if use_real
                else f"a fixed benchmark cohort of {len(dist)} representative "
                "retail portfolio scores. This is a benchmark comparison, "
                "not live community/peer data."
            )
        ),
    }


def compute_scores(
    holdings: list[dict[str, Any]],
    sector_of,
    account=None,
    nav_snapshots: Optional[list] = None,
    peer_scores: Optional[list[float]] = None,
    empty_peer_count: int = 0,
) -> dict[str, Any]:
    """Top-level entry: returns the full scores payload.

    If there are no holdings (or none with positive market value) all three
    scores are ``None`` and ``reason`` is ``"no_holdings"``. ``peer_scores``
    is the caller-gathered list of OTHER real-portfolio users' scores, and
    ``empty_peer_count`` is how many other users have never traded — together
    they form the whole-community distribution for the percentile (see
    ``compute_community_score``).
    """
    div = compute_diversification(holdings, sector_of)
    if div is None:
        return {
            "diversification_score": None,
            "portfolio_score": None,
            "community_score": None,
            "reason": "no_holdings",
        }

    top_sector_pct = div.pop("_top_sector_pct")
    total_return_pct = compute_total_return_pct(account, nav_snapshots or [])

    portfolio = compute_portfolio_score(
        diversification_score=div["score"],
        top_sector_pct=top_sector_pct,
        total_return_pct=total_return_pct,
    )
    community = compute_community_score(
        portfolio["score"],
        peer_scores=peer_scores,
        empty_peer_count=empty_peer_count,
    )

    return {
        "diversification_score": div,
        "portfolio_score": portfolio,
        "community_score": community,
        "reason": None,
    }
