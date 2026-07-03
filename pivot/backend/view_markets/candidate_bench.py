"""Candidate bench — thesis-aligned substitutes with EVENT-CONDITIONED stats.

The affordability fix (beta readiness): when a strategy's own name is too
expensive to hold at a small ticket (one BRITANNIA share ≈ ₹5,100), the old
allocator just DROPPED it and stuffed the budget into ETF units. But the
research universe has full price history for hundreds of thesis-aligned
names — many affordable. This module builds, per curated view, a ranked
bench of such candidates so the allocator can SUBSTITUTE a real stock for a
dropped one instead of collapsing the basket into ETFs.

Honesty contract:

* The bench universe is THESIS-ALIGNED (the view's own industries /
  scenario winners / stated beneficiary list) — never a whole-market
  top-gainer mine.
* Every candidate's stats come from the SAME per-occurrence event windows
  the view's headline uses (weak-IT prints, IMD-normal monsoon sowing
  seasons, Brent 10d ≤ −8% de-escalation triggers). ``method`` states it:
  ``event_study`` (real occurrences) — candidates without enough history
  are labelled ``insufficient_history`` and rank below event-tested ones.
* Ranking = shrunk mean episode return × positive rate, n/(n+4) shrinkage —
  small samples get pulled toward zero rather than trusted outright.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Minimum occurrences for a candidate to count as event-tested.
MIN_EPISODES_EVENT_TESTED = 4

# Per-view thesis universes: NIFTY-500 industry tags (NSE taxonomy) that map
# to the view's transmission story. Members/scenario winners are added on top.
_VIEW_INDUSTRIES: dict[str, list[str]] = {
    # Rural / Kharif consumption: FMCG core, autos (tractors/2W), durables.
    "monsoon": [
        "Fast Moving Consumer Goods",
        "Automobile and Auto Components",
        "Consumer Durables",
    ],
    # Weak-IT rotation winners: domestic-facing power / infra / capex names.
    "it": [
        "Power",
        "Capital Goods",
        "Construction",
        "Construction Materials",
        "Realty",
    ],
    # Crude-DOWN (de-escalation) importers: paints/durables, autos/tyres,
    # FMCG (input + logistics costs), airlines (Services). OMCs come from the
    # explicit beneficiary resolver — the Oil&Gas industry tag also contains
    # upstream producers (ONGC/OIL), which are crude-DOWN LOSERS, so the tag
    # itself is deliberately NOT included.
    "crude": [
        "Consumer Durables",
        "Automobile and Auto Components",
        "Fast Moving Consumer Goods",
        "Services",
    ],
}

# The crude view's own thesis basket ("the only placeable expression") —
# stated in the deployed thesis text; used as members + bench seeds.
CRUDE_THESIS_BASKET = [
    "ASIANPAINT.NS", "BERGEPAINT.NS", "INDIGO.NS",
    "HINDPETRO.NS", "BPCL.NS", "IOC.NS",
]


@dataclass
class Candidate:
    symbol: str                    # matrix ticker (with .NS)
    price: Optional[float]         # last close from the engine matrix
    mean_episode_pct: Optional[float]
    median_episode_pct: Optional[float]
    positive_rate: Optional[float]  # 0..1
    n_episodes: int
    score: float                   # shrunk ranking score (see rank formula)
    method: str                    # "event_study" | "insufficient_history"
    source: str                    # "member" | "scenario" | "industry" | "resolver"


@dataclass
class Bench:
    view_key: str                  # "monsoon" | "it" | "crude"
    candidates: list[Candidate] = field(default_factory=list)
    universe_size: int = 0
    n_episodes: int = 0
    method_note: str = ""

    def ranked(self) -> list[Candidate]:
        """Event-tested candidates by score desc; insufficient-history last."""
        tested = [c for c in self.candidates if c.method == "event_study"]
        rest = [c for c in self.candidates if c.method != "event_study"]
        return (
            sorted(tested, key=lambda c: -c.score)
            + sorted(rest, key=lambda c: -(c.score or 0.0))
        )

    def expected_returns(self) -> dict[str, float]:
        """symbol → mean per-occurrence return % (event-tested names only)."""
        return {
            c.symbol: float(c.mean_episode_pct)
            for c in self.candidates
            if c.method == "event_study" and c.mean_episode_pct is not None
        }


def _view_key(view_id: str) -> Optional[str]:
    from backend.view_markets import plain_copy

    return {
        plain_copy.VIEW_MONSOON: "monsoon",
        plain_copy.VIEW_IT: "it",
        plain_copy.VIEW_CRUDE: "crude",
    }.get(view_id)


def _universe_for(view_key: str, members: list[str]) -> dict[str, str]:
    """ticker(.NS) → source, thesis-aligned. Members always included."""
    out: dict[str, str] = {m: "member" for m in members}

    try:
        from scripts.strategy_research.v3 import universe as _v3u

        ind_map = _v3u.industry_map()  # ticker(.NS) -> Industry
        wanted = set(_VIEW_INDUSTRIES.get(view_key, []))
        for tkr, ind in ind_map.items():
            if ind in wanted and tkr not in out:
                out[tkr] = "industry"
    except Exception as exc:  # noqa: BLE001 — bench degrades to members only
        logger.warning("candidate_bench: industry universe unavailable (%s)", exc)

    if view_key == "crude":
        for s in CRUDE_THESIS_BASKET:
            out.setdefault(s, "resolver")
        try:
            from backend.services import sector_universe

            for s in sector_universe.crude_down_beneficiaries():
                out.setdefault(f"{s}.NS", "resolver")
        except Exception:  # noqa: BLE001
            pass

    if view_key == "monsoon":
        try:
            from backend.services import thematic_map

            scen = thematic_map.detect_thematic_scenario(
                "position me for a good monsoon rural recovery"
            )
            if scen is not None:
                for tk, _why in scen.winners:
                    out.setdefault(f"{tk}.NS", "scenario")
        except Exception:  # noqa: BLE001
            pass

    return out


def crude_episodes(idx) -> tuple[list[tuple[int, int]], list[dict[str, str]]]:
    """(entry_pos, exit_pos) on the EQUITY matrix index for each crude-DOWN
    de-escalation trigger (Brent 10d move ≤ −8%, read at close; position
    next bar, ~20-bar hold, non-overlapping) — the same signal family the
    deployed crude thesis was backtested on (_crude_bt_common).
    """
    import pandas as pd

    from scripts.strategy_research.v3 import universe as _v3u

    SIG_WIN, HOLD, DOWN = 10, 20, -0.08
    try:
        brent = _v3u.driver_close("BRENT")
    except Exception as exc:  # noqa: BLE001
        logger.warning("candidate_bench: Brent driver unavailable (%s)", exc)
        return [], []
    sig = brent.pct_change(SIG_WIN)

    eps: list[tuple[int, int]] = []
    meta: list[dict[str, str]] = []
    _MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    i, n = SIG_WIN + 1, len(brent)
    last_exit_pos = -1
    while i < n - 1:
        s = sig.iloc[i]
        if pd.notna(s) and float(s) <= DOWN:
            t_sig = brent.index[i]
            lo = int(idx.searchsorted(t_sig, side="right"))  # next equity bar
            hi = min(lo + HOLD - 1, len(idx) - 1)
            if lo < len(idx) and lo > last_exit_pos and hi > lo:
                eps.append((lo, hi))
                meta.append({
                    "label": "Brent −8%/10d de-escalation",
                    "date": f"{_MONTHS[t_sig.month]} {t_sig.year}",
                })
                last_exit_pos = hi
            i += HOLD + 1  # non-overlap on the Brent clock too
        else:
            i += 1
    return eps, meta


def build_bench(
    engine: Any,
    view_id: str,
    members: list[str],
    episodes: list[tuple[int, int]],
) -> Optional[Bench]:
    """Per-candidate event-conditioned stats over the view's OWN episode
    windows, for every thesis-aligned name present in the returns matrix.

    ``engine`` is precompute's _Engine (rets/px matrices). Returns None when
    the view key is unknown. All stats real — a name with no coverage inside
    the windows is labelled, never guessed.
    """
    from scripts.strategy_research.v3 import exits as _v3e

    key = _view_key(view_id)
    if key is None:
        return None

    uni = _universe_for(key, members)
    in_matrix = {t: src for t, src in uni.items() if t in engine.rets.columns}

    bench = Bench(
        view_key=key,
        universe_size=len(in_matrix),
        n_episodes=len(episodes),
        method_note=(
            f"Event-conditioned backtest: per-name return measured inside each "
            f"of the view's {len(episodes)} historical occurrence windows "
            f"(same windows as the headline strategy), over a thesis-aligned "
            f"universe of {len(in_matrix)} names."
            if episodes
            else "No historical occurrence windows — candidates carry no "
                 "event-tested stats (forward/model view)."
        ),
    )

    for tkr, src in in_matrix.items():
        ser = engine.px[tkr].dropna()
        price = float(ser.iloc[-1]) if len(ser) else None

        per_ep: list[float] = []
        if episodes:
            for p in _v3e.episode_returns(episodes, engine.rets, tkr):
                if len(p) and p.notna().sum() >= max(3, len(p) // 2):
                    per_ep.append(float((1.0 + p.fillna(0.0)).prod() - 1.0) * 100.0)

        n = len(per_ep)
        if n >= MIN_EPISODES_EVENT_TESTED:
            mean = sum(per_ep) / n
            srt = sorted(per_ep)
            median = srt[n // 2] if n % 2 else (srt[n // 2 - 1] + srt[n // 2]) / 2
            pos = sum(1 for r in per_ep if r > 0) / n
            # Shrunk score: small samples pulled toward zero (n/(n+4)).
            score = (n / (n + 4.0)) * mean * pos
            method = "event_study"
        else:
            mean = median = None
            pos = None
            score = 0.0
            method = "insufficient_history"

        bench.candidates.append(Candidate(
            symbol=tkr, price=price,
            mean_episode_pct=None if mean is None else round(mean, 2),
            median_episode_pct=None if median is None else round(median, 2),
            positive_rate=None if pos is None else round(pos, 3),
            n_episodes=n, score=round(score, 4),
            method=method, source=src,
        ))
    return bench


__all__ = [
    "Bench",
    "Candidate",
    "CRUDE_THESIS_BASKET",
    "MIN_EPISODES_EVENT_TESTED",
    "build_bench",
    "crude_episodes",
]
