"""PEG-ratio coverage for the fundamentals screen (services/fundamentals_screen.py).

Regression for the 2026-07-14 eval finding: a 3-constraint screen
("ROCE>25, revenue growth>20, PEG<1") silently ran with only 2 filters
applied, and the response header claimed "filters applied" with no mention
that PEG had been dropped. Root cause: PEG was not a screenable field at all
(not in FIELD_MAP, not in the screen's field table, not expressible via
custom_ratios since that mechanism only combines two RAW line items and
"growth" is a paired-period derivation, not a line item) — so the LLM never
even attempted to pass a PEG filter to the tool, and there was nothing left
downstream (note/applied_filters) to disclose the drop.

Fix: PEG is now a first-class derived field (kind="peg" in _FIELD_DEFS),
computed as trailing P/E ÷ trailing YoY EPS growth from the SAME two
building blocks (`pe_from_ey`, `growth`) the module already uses elsewhere —
not an approximation. These tests exercise the SQL-building and
result-shaping path with a stubbed DB session (the module's raw SQL uses
Postgres-only syntax — DISTINCT ON, unnest, ANY, array_position — that
sqlite can't run), so no live Postgres connection is required.
"""
from __future__ import annotations

from backend.services.fundamentals_screen import (
    _normalise_field,
    screen_by_fundamentals,
)


class _FakeResult:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self):
        return self

    def fetchall(self):
        return self._rows


class _FakeSession:
    """Stand-in for a SQLAlchemy Session — records the SQL/params it was
    asked to run and returns canned rows, so the test never touches a real
    (Postgres-only) database."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.last_sql: str = ""
        self.last_params: dict = {}

    def execute(self, sql, params=None):
        self.last_sql = str(sql)
        self.last_params = dict(params or {})
        return _FakeResult(self._rows)

    def close(self) -> None:
        pass


def _row(**val) -> dict:
    base = {
        "sc_id": "SC1", "company_name": "Test Co", "nse_symbol": "TESTCO",
        "ticker": "TESTCO", "industry_slug": "automobile",
    }
    base.update(val)
    return base


def test_peg_field_normalises_aliases():
    assert _normalise_field("peg") == "peg"
    assert _normalise_field("PEG") == "peg"
    assert _normalise_field("peg_ratio") == "peg"
    assert _normalise_field("peg ratio") == "peg"


def test_three_constraint_screen_applies_all_three_filters_including_peg():
    """The exact eval-repro shape: ROCE>25, revenue_growth>20, PEG<1 — all
    three must land in applied_filters, and the note must NOT contain an
    'unknown field' disclaimer for peg (since it's now genuinely supported)."""
    rows = [_row(val_roce=30.0, val_revenue_growth=25.0, val_peg=0.8)]
    sess = _FakeSession(rows)

    out = screen_by_fundamentals(
        filters=[
            {"field": "roce", "op": ">", "value": 25},
            {"field": "revenue_growth", "op": ">", "value": 20},
            {"field": "peg", "op": "<", "value": 1},
        ],
        sort_by={"field": "roce", "dir": "desc"},
        limit=15,
        session=sess,
    )

    applied_fields = {f["field"] for f in out["applied_filters"]}
    assert applied_fields == {"roce", "revenue_growth", "peg"}, (
        "all three stated constraints must be applied, not silently dropped"
    )
    assert "unknown field" not in out["note"]
    # The disclosure note explains what PEG *means* here (derived, historical)
    # rather than staying silent about a constraint the model asked to apply.
    assert "PEG" in out["note"]

    # Result row carries the peg value verbatim (rounded), never fabricated.
    assert out["results"][0]["peg"] == 0.8

    # The SQL actually built must reference the PEG composition (pe + growth
    # sub-CTEs joined together) — proves the constraint reached the query,
    # not just the validation layer.
    sql_lower = sess.last_sql.lower()
    assert "peg" in sql_lower
    assert "_pe" in sql_lower and "_gr" in sql_lower


def test_unknown_field_is_still_disclosed_not_silently_dropped():
    """Sanity check the general honesty mechanism still works for a genuinely
    unsupported field — the fix must not have broken this path."""
    rows = [_row(val_roce=30.0)]
    sess = _FakeSession(rows)

    out = screen_by_fundamentals(
        filters=[
            {"field": "roce", "op": ">", "value": 25},
            {"field": "not_a_real_metric_xyz", "op": "<", "value": 1},
        ],
        session=sess,
    )

    applied_fields = {f["field"] for f in out["applied_filters"]}
    assert applied_fields == {"roce"}
    assert "not_a_real_metric_xyz" in out["note"]
    assert "skipped" in out["note"]


def test_peg_only_filter_builds_and_shapes_results():
    rows = [_row(val_peg=0.6)]
    sess = _FakeSession(rows)

    out = screen_by_fundamentals(
        filters=[{"field": "peg_ratio", "op": "<", "value": 1}],
        session=sess,
    )

    assert out["applied_filters"] == [{"field": "peg", "op": "<", "value": 1.0}]
    assert out["results"][0]["peg"] == 0.6
    assert out["sorted_by"]["field"] == "peg"
