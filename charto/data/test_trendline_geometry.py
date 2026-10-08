"""Isolated fitter regressions; no market database or model required."""
import ast
from pathlib import Path


def fit(rows, role):
    tree = ast.parse(Path(__file__).with_name("dataserver.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_trendlines")
    namespace = {
        "_pivots": lambda *_: [(10, 100, role), (20, 100, role), (30, 100, role)],
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
