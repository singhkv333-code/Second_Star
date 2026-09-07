import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import chart_patterns_v2 as patterns_v2
import chart_patterns_v3 as patterns_v3
import load_chart_pattern_v2 as loader
import sweep_chart_patterns_v2 as sweep


def _rows(closes):
    return [(i * 60, c, c + 0.5, c - 0.5, c, 1000.0) for i, c in enumerate(closes)]


def test_prefix_invariance_no_future_leakage():
    closes = [100 + ((i % 20) - 10) * 0.2 + i * 0.03 for i in range(180)]
    rows = _rows(closes)
    pivots = [(i, rows[i][2], "resistance") if i % 20 == 5
              else (i, rows[i][3], "support") for i in range(5, 170, 10)]
    fmt = lambda ts: str(ts)
    prefix = patterns_v2.additional_chart_patterns(rows[:120],
                                                    [p for p in pivots if p[0] < 120],
                                                    0.5, fmt)
    full = patterns_v2.additional_chart_patterns(rows, pivots, 0.5, fmt)
    sig = lambda p: (p["pattern"], p["completion_i"], p["span_bars"])
    assert {sig(p) for p in prefix} == {sig(p) for p in full if p["completion_i"] < 120}


def test_outcomes_start_at_completion_bar():
    rows = _rows([100, 101, 102, 103, 104, 105, 106, 107])
    result = sweep._outcomes(rows, 2, "bullish")
    assert result["entry_close"] == 102
    assert result["fwd_ret_1"] == round((103 - 102) / 102 * 100, 4)
    assert result["fwd_ret_5"] == round((107 - 102) / 102 * 100, 4)


def _artifact(events):
    db = sqlite3.connect(":memory:")
    db.executescript(sweep.DDL)
    for pid, comp, ret in events:
        features = json.dumps({"completion_bar_index": comp})
        outcomes = json.dumps({"entry_close": 100, "measured_move_hit_40": None,
                               "retest_held": None})
        db.execute(sweep.INS, (pid, "TEST", "equity", "5m", "range_breakout_up",
                              "bullish", "up", comp, comp, comp, comp, "confirmed",
                              sweep.DETECTOR_VERSION, 2, features, outcomes,
                              ret, ret, ret, ret, -ret))
    for h in (5, 10, 20):
        db.execute("INSERT INTO chart_pattern_controls_v2 VALUES (?,?,?,?,?,?,?,?)",
                   ("equity", "TEST", "5m", h, 1000, 50, 50, 1))
    db.commit()
    return db


def test_aggregates_decluster_per_forward_horizon():
    db = _artifact([("a", 10, 1), ("b", 13, -1), ("c", 21, 2)])
    rows = {(r[3]): r for r in loader.aggregates(db)}
    # h=5 keeps bars 10 and 21; h=10 also keeps 10 and 21; h=20 only bar 10.
    assert rows[5][4] == 2
    assert rows[10][4] == 2
    assert rows[20][4] == 1
    assert rows[5][6] == 100.0
    db.close()


def test_v3_pivot_is_only_visible_after_right_window():
    closes = [10, 11, 12, 13, 14, 20, 14, 13, 12, 11, 10]
    rows = _rows(closes)
    assert patterns_v3._new_pivots(rows, 9) == []
    assert patterns_v3._new_pivots(rows, 10) == [(5, 20.5, "resistance")]


def test_v3_event_driven_prefix_invariance():
    closes = [100 + i * 0.1 + ((i % 17) - 8) * 0.3 for i in range(220)]
    rows = _rows(closes)
    kinds = {"ascending_triangle", "descending_triangle", "rising_wedge",
             "falling_wedge", "rectangle", "channel_up", "channel_down",
             "broadening", "symmetrical_triangle"}
    prefix = patterns_v3.event_driven_edge_patterns(rows[:170], "TEST", kinds)
    full = patterns_v3.event_driven_edge_patterns(rows, "TEST", kinds)
    sig = lambda e: (e["pattern"], e["formation_start_i"], e["completion_i"])
    assert {sig(e) for e in prefix} == {
        sig(e) for e in full if e["completion_i"] < 170}
