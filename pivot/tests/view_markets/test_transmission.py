"""Focused unit tests for ``backend.view_markets.transmission``.

Covers the four authoring/DAG paths (seed, manual author, DAG fold, persist/
load) without touching any sibling module — ``thematic_map`` is the only real
dependency and is exercised directly (it is pure / DB-free).
"""
from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from backend.models import MarketView, ViewTransmission, ViewType
from backend.schemas import ViewTransmissionInput
from backend.view_markets import transmission as tx


# ── seed_transmission_from_scenario ───────────────────────────────────


def test_seed_winners_only() -> None:
    edges = tx.seed_transmission_from_scenario(
        "inr_depreciation", include_losers=False
    )
    # 4 winners for inr_depreciation, no losers.
    assert len(edges) == 4
    assert all(isinstance(e, ViewTransmissionInput) for e in edges)
    # Cause node is the scenario label; effects are the winner tickers.
    assert {e.from_node for e in edges} == {"a falling rupee (INR depreciation)"}
    assert {e.to_node for e in edges} == {"INFY", "TCS", "SUNPHARMA", "CIPLA"}
    # seq is sequential from base_seq=0; edge_label carries the per-name "why".
    assert [e.seq for e in edges] == [0, 1, 2, 3]
    assert edges[0].edge_label and "FX" in edges[0].edge_label
    # Evidence grounds in the thesis; strength is never fabricated.
    assert edges[0].evidence and "margin" in edges[0].evidence
    assert all(e.strength is None for e in edges)


def test_seed_with_losers_and_base_seq() -> None:
    edges = tx.seed_transmission_from_scenario(
        "inr_depreciation", include_losers=True, base_seq=10
    )
    # 4 winners + 4 losers.
    assert len(edges) == 8
    assert [e.seq for e in edges] == list(range(10, 18))
    losers = [e for e in edges if e.to_node in {"IOC", "BPCL", "INDIGO", "NESTLEIND"}]
    assert len(losers) == 4
    # Loser edges are AVOID legs (shorting not wired -> named, not shorted).
    assert all(e.edge_label and e.edge_label.startswith("avoid:") for e in losers)
    assert all(e.evidence and "Invalidate:" in e.evidence for e in losers)


def test_seed_unknown_scenario_returns_empty() -> None:
    assert tx.seed_transmission_from_scenario("not_a_real_scenario") == []


# ── author_transmission_edges ─────────────────────────────────────────


def test_author_accepts_dicts_and_resequences() -> None:
    raw = [
        {"seq": 99, "from_node": "RBI cuts rates", "to_node": "NIFTYBEES",
         "edge_label": "rate-sensitive rally"},
        ViewTransmissionInput(seq=5, from_node="RBI cuts rates", to_node="BANKBEES"),
    ]
    out = tx.author_transmission_edges(raw, base_seq=3)
    assert [e.seq for e in out] == [3, 4]  # caller seq ignored, re-sequenced
    assert out[0].from_node == "RBI cuts rates"
    assert isinstance(out[0], ViewTransmissionInput)


def test_author_rejects_dangling_node() -> None:
    raw = [{"from_node": "A", "to_node": "B"}]
    with pytest.raises(ValueError):
        tx.author_transmission_edges(raw, nodes=["A", "C"])


def test_author_enforces_declared_nodes_ok() -> None:
    raw = [{"from_node": "A", "to_node": "B"}]
    out = tx.author_transmission_edges(raw, nodes=["A", "B"])
    assert len(out) == 1


# ── build_dag ─────────────────────────────────────────────────────────


def test_build_dag_roots_leaves_acyclic() -> None:
    edges = [
        ViewTransmissionInput(seq=1, from_node="cause", to_node="mid"),
        ViewTransmissionInput(seq=0, from_node="mid", to_node="effect"),
    ]
    dag = tx.build_dag(edges)
    assert dag.nodes == ("mid", "effect", "cause")  # sorted by seq, first-seen
    assert dag.roots == ("cause",)
    assert dag.leaves == ("effect",)
    assert dag.is_acyclic is True


def test_build_dag_detects_cycle() -> None:
    edges = [
        ViewTransmissionInput(from_node="A", to_node="B"),
        ViewTransmissionInput(from_node="B", to_node="A"),
    ]
    assert tx.build_dag(edges).is_acyclic is False


def test_build_dag_detects_self_loop() -> None:
    edges = [ViewTransmissionInput(from_node="A", to_node="A")]
    assert tx.build_dag(edges).is_acyclic is False


# ── persist / load ────────────────────────────────────────────────────


def _make_view(db: Session) -> MarketView:
    view = MarketView(view_type=ViewType.theme, title="INR depreciation theme")
    db.add(view)
    db.flush()
    return view


def test_persist_and_load_roundtrip(view_db: Session) -> None:
    view = _make_view(view_db)
    edges = tx.seed_transmission_from_scenario("inr_depreciation", include_losers=False)
    rows = tx.persist_transmission(view_db, view.id, edges)
    assert len(rows) == 4
    assert all(isinstance(r, ViewTransmission) for r in rows)
    assert all(r.view_id == view.id for r in rows)

    loaded = tx.load_transmission(view_db, view.id)
    assert [r.seq for r in loaded] == [0, 1, 2, 3]
    assert {r.to_node for r in loaded} == {"INFY", "TCS", "SUNPHARMA", "CIPLA"}


def test_persist_replace_is_idempotent(view_db: Session) -> None:
    view = _make_view(view_db)
    edges = tx.seed_transmission_from_scenario("inr_depreciation", include_losers=False)
    tx.persist_transmission(view_db, view.id, edges)
    # Re-run with replace=True (default) -> no duplicate rows.
    tx.persist_transmission(view_db, view.id, edges)
    assert len(tx.load_transmission(view_db, view.id)) == 4


def test_persist_no_replace_appends(view_db: Session) -> None:
    view = _make_view(view_db)
    edges = tx.seed_transmission_from_scenario("inr_depreciation", include_losers=False)
    tx.persist_transmission(view_db, view.id, edges)
    tx.persist_transmission(view_db, view.id, edges, replace=False)
    assert len(tx.load_transmission(view_db, view.id)) == 8


def test_build_dag_from_orm_rows(view_db: Session) -> None:
    view = _make_view(view_db)
    edges = tx.seed_transmission_from_scenario("inr_depreciation", include_losers=False)
    rows = tx.persist_transmission(view_db, view.id, edges)
    dag = tx.build_dag(rows)
    assert dag.roots == ("a falling rupee (INR depreciation)",)
    assert set(dag.leaves) == {"INFY", "TCS", "SUNPHARMA", "CIPLA"}
    assert dag.is_acyclic is True
