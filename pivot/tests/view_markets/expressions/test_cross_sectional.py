"""Unit tests for the Phase-3 cross-sectional rank engine + factor→ETF map.

``cross_sectional`` is pure helper code (numpy/stdlib + one price fetch), so
these tests assert:

  * ``FACTOR_ETF_MAP`` is the real, complete factor→smart-beta-ETF catalog and
    ``factor_etf`` resolves it (no fabricated/missing factors).
  * ``rank_scores`` is a tie-aware percentile in ``(0, 1)`` ordered by score.
  * ``decile_split`` puts the top names in bucket 1, the worst in the last
    bucket, and always exposes every bucket.
  * ``composite_factor_scores`` blends only the factors that carry a signal —
    momentum from (mocked) Kite closes, value/quality from caller fundamentals —
    and never fabricates a tilt when there is no data (neutral 0.0).

External data (the Kite/yfinance price fetch) is monkeypatched so the test is
self-contained and never hits the network.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backend.view_markets.expressions import cross_sectional as cs


# ── FACTOR_ETF_MAP (real DATA) ───────────────────────────────────────────────


def test_factor_etf_map_covers_all_factors() -> None:
    assert set(cs.FACTOR_ETF_MAP) == {
        "momentum",
        "quality",
        "value",
        "low_vol",
        "multi",
    }
    for factor, etf in cs.FACTOR_ETF_MAP.items():
        assert isinstance(etf, cs.FactorETF)
        assert etf.factor == factor
        # Real NSE factor index named, with a human label + note.
        assert etf.index and etf.label and etf.note
        assert "NIFTY" in etf.index.upper() or "NIFTY" in etf.label.upper()


def test_factor_etf_lookup() -> None:
    assert cs.factor_etf("momentum") is cs.FACTOR_ETF_MAP["momentum"]
    assert cs.factor_etf("momentum").index == "NIFTY200 Momentum 30"
    assert cs.factor_etf("not_a_factor") is None


def test_factor_etf_is_frozen() -> None:
    etf = cs.factor_etf("quality")
    with pytest.raises(Exception):
        etf.index = "TAMPERED"  # type: ignore[misc]


# ── rank_scores ──────────────────────────────────────────────────────────────


def test_rank_scores_orders_by_score() -> None:
    ranks = cs.rank_scores({"A": 3.0, "B": 1.0, "C": 2.0})
    assert ranks["A"] > ranks["C"] > ranks["B"]
    # Hazen plotting position → strictly inside (0, 1).
    assert all(0.0 < v < 1.0 for v in ranks.values())


def test_rank_scores_ties_share_average() -> None:
    ranks = cs.rank_scores({"A": 5.0, "B": 5.0, "C": 1.0})
    assert ranks["A"] == ranks["B"]
    assert ranks["A"] > ranks["C"]


def test_rank_scores_single_name_is_neutral() -> None:
    assert cs.rank_scores({"A": 7.0}) == {"A": 0.5}


def test_rank_scores_drops_non_finite_and_empty() -> None:
    ranks = cs.rank_scores({"A": 1.0, "B": float("nan"), "C": None})  # type: ignore[dict-item]
    assert set(ranks) == {"A"}
    assert cs.rank_scores({}) == {}


# ── decile_split ─────────────────────────────────────────────────────────────


def test_decile_split_top_and_bottom() -> None:
    scores = {f"S{i}": float(i) for i in range(20)}  # S19 best, S0 worst
    buckets = cs.decile_split(scores, n_buckets=10)
    assert set(buckets) == set(range(1, 11))
    assert "S19" in buckets[1]          # top decile holds the best name
    assert "S0" in buckets[10]          # bottom decile holds the worst name
    # Every name lands in exactly one bucket.
    placed = [s for names in buckets.values() for s in names]
    assert sorted(placed) == sorted(scores)


def test_decile_split_sparse_keeps_all_buckets() -> None:
    buckets = cs.decile_split({"A": 2.0, "B": 1.0}, n_buckets=10)
    assert set(buckets) == set(range(1, 11))
    assert buckets[1] == ["A"]          # best in the top bucket
    # The other name is somewhere; the top/bottom keys still exist.
    placed = [s for names in buckets.values() for s in names]
    assert sorted(placed) == ["A", "B"]


def test_decile_split_rejects_zero_buckets() -> None:
    with pytest.raises(ValueError):
        cs.decile_split({"A": 1.0}, n_buckets=0)


# ── composite_factor_scores ──────────────────────────────────────────────────


def _series(values: list[float]) -> pd.Series:
    idx = pd.date_range("2024-01-01", periods=len(values), freq="B")
    return pd.Series(values, index=idx, dtype=float)


def _patch_closes(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, pd.Series]) -> None:
    from backend.core.data import historical

    monkeypatch.setattr(
        historical, "get_close_dict", lambda symbols, period="2y": dict(mapping)
    )


def test_composite_momentum_orders_by_trailing_return(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # WINNER ramps up; LOSER ramps down; FLAT is unchanged.
    up = _series([100.0 + i for i in range(60)])
    down = _series([200.0 - i for i in range(60)])
    flat = _series([150.0 for _ in range(60)])
    _patch_closes(monkeypatch, {"UP": up, "DOWN": down, "FLAT": flat})

    out = cs.composite_factor_scores(
        object(), ["UP", "DOWN", "FLAT"], factors=["momentum"]
    )
    assert set(out) == {"UP", "DOWN", "FLAT"}
    assert out["UP"] > out["FLAT"] > out["DOWN"]
    # z-scored → roughly mean-zero across the cross-section.
    assert abs(sum(out.values())) < 1e-9


def test_composite_fundamentals_only_no_price_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # If a price factor is NOT requested, the price fetch must never be called.
    from backend.core.data import historical

    def _boom(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("price fetch should not be called for value/quality")

    monkeypatch.setattr(historical, "get_close_dict", _boom)

    out = cs.composite_factor_scores(
        object(),
        ["A", "B", "C"],
        factors=["value", "quality"],
        fundamentals={"A": 2.0, "B": 0.0, "C": -2.0},
    )
    assert out["A"] > out["B"] > out["C"]


def test_composite_per_factor_fundamentals_dict() -> None:
    out = cs.composite_factor_scores(
        object(),
        ["A", "B"],
        factors=["value"],
        fundamentals={"A": {"value": 5.0}, "B": {"value": 1.0}},
    )
    assert out["A"] > out["B"]


def test_composite_no_signal_is_neutral(monkeypatch: pytest.MonkeyPatch) -> None:
    # value requested but no fundamentals supplied, no price factor → all neutral.
    out = cs.composite_factor_scores(object(), ["A", "B"], factors=["value"])
    assert out == {"A": 0.0, "B": 0.0}


def test_composite_multi_expands_and_blends(monkeypatch: pytest.MonkeyPatch) -> None:
    up = _series([100.0 + i for i in range(60)])
    down = _series([200.0 - i for i in range(60)])
    _patch_closes(monkeypatch, {"UP": up, "DOWN": down})

    out = cs.composite_factor_scores(
        object(),
        ["UP", "DOWN"],
        factors=["multi"],
        fundamentals={"UP": 1.0, "DOWN": -1.0},
    )
    # momentum + fundamentals both favour UP → UP wins the blend.
    assert out["UP"] > out["DOWN"]


def test_composite_empty_symbols() -> None:
    assert cs.composite_factor_scores(object(), [], factors=["momentum"]) == {}


def test_composite_price_fetch_failure_degrades_honestly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.core.data import historical

    def _boom(*a, **k):
        raise RuntimeError("kite down")

    monkeypatch.setattr(historical, "get_close_dict", _boom)
    # Momentum has no data → neutral, no fabricated tilt, no exception.
    out = cs.composite_factor_scores(object(), ["A", "B"], factors=["momentum"])
    assert out == {"A": 0.0, "B": 0.0}


def test_composite_feeds_rank_and_decile(monkeypatch: pytest.MonkeyPatch) -> None:
    series = {
        sym: _series([100.0 + mult * i for i in range(60)])
        for sym, mult in {"A": 3.0, "B": 1.0, "C": -1.0, "D": -3.0}.items()
    }
    _patch_closes(monkeypatch, series)
    scores = cs.composite_factor_scores(
        object(), list(series), factors=["momentum"]
    )
    ranks = cs.rank_scores(scores)
    assert ranks["A"] == max(ranks.values())
    buckets = cs.decile_split(scores, n_buckets=2)
    assert "A" in buckets[1]
    assert "D" in buckets[2]
    assert all(np.isfinite(v) for v in scores.values())
