"""Isolated fitter regressions; no market database or model required."""
import ast
from pathlib import Path


def fit(rows, role, at=(10, 20, 30)):
    tree = ast.parse(Path(__file__).with_name("dataserver.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_trendlines")
    namespace = {
        "_pivots": lambda *_: [(i, 100, role) for i in at],
        "_tolerance": lambda _: .1,
        "_ist": lambda timestamp, _: str(timestamp),
    }
    exec(compile(ast.Module(body=[function], type_ignores=[]), "fitter", "exec"), namespace)
    return namespace["_trendlines"](rows)


def test_post_anchor_breach_stays_broken_after_price_returns():
    for role, normal, breach in [("resistance", 99, 104), ("support", 101, 96)]:
        rows = [(i * 900, normal, 104, 96, normal, 1) for i in range(50)]
        rows[35] = (35 * 900, breach, 104, 96, breach, 1)
        lines = fit(rows, role)
        assert lines and all(line["status"] == "broken" for line in lines)


def test_unbreached_line_remains_intact_and_keeps_real_anchors():
    rows = [(i * 900, 99, 100, 98, 99, 1) for i in range(50)]
    lines = fit(rows, "resistance")
    assert lines and all(line["status"] == "intact" for line in lines)
    for line in lines:
        assert line["_t1"] == rows[line["i1"]][0]
        assert line["_t2"] == rows[line["i2"]][0]
        assert line["projects_to"] == 100


def test_a_session_spent_beyond_the_line_between_anchors_rejects_it():
    # Anchors 30 bars apart (span 60, so the 15% pierce budget is 9 bars) and
    # a swing window of 5. Six straight closes below the support fit inside
    # that budget, but six bars is longer than a swing: price lived on the
    # other side, and a line through it is a re-fit.
    rows = [(i * 900, 101, 104, 96, 101, 1) for i in range(100)]
    for k in range(41, 47):
        rows[k] = (k * 900, 99, 104, 96, 99, 1)
    assert fit(rows, "support", at=(10, 40, 70)) == []


def test_a_brief_excursion_shorter_than_a_swing_is_forgiven():
    rows = [(i * 900, 101, 104, 96, 101, 1) for i in range(100)]
    for k in range(41, 45):
        rows[k] = (k * 900, 99, 104, 96, 99, 1)
    lines = fit(rows, "support", at=(10, 40, 70))
    assert lines and all(line["status"] == "intact" for line in lines)
