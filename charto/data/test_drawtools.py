"""The parity guard between drawtools.py and preview/js/tools.js.

Two files declare the same ratios: the frontend one DRAWS them, the backend
one QUOTES them. Nothing in either language can notice that they have stopped
agreeing, and the failure is the worst-shaped kind — a reply that names 61.8%
beside a line the chart put at 60%, with both halves confident.

So the test reads the JavaScript and compares. Parsing another language's
source is normally a bad idea; here it is the only thing that actually checks
the invariant, and the arrays are plain literals on one line each.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import drawtools as D

JS = Path(__file__).resolve().parent.parent / "preview" / "js" / "tools.js"


def _array(name: str):
    """A `const NAME = [...];` literal out of tools.js, as Python."""
    src = JS.read_text(encoding="utf-8")
    m = re.search(rf"^\s*const {name} = (\[[^;]*?\]);\s*$", src, re.M | re.S)
    assert m, f"{name} is no longer a plain const array in tools.js"
    return json.loads(m.group(1))


def _const(name: str):
    src = JS.read_text(encoding="utf-8")
    m = re.search(rf"^\s*const {name} = (\d+);\s*$", src, re.M)
    assert m, f"{name} is no longer a plain const number in tools.js"
    return int(m.group(1))


@pytest.mark.parametrize("py, js", [
    (D.RETRACEMENT, "FIB"),
    (D.EXTENSION, "FIB_EXT"),
    (D.FAN, "FIB_FAN"),
    (D.ARC, "FIB_ARC"),
    (D.TIME_ZONE, "FIB_TIME"),
    (D.TIME_RATIO, "FIB_TIME_R"),
    (D.GANN, "GANN"),
    (D.GANN_EIGHTHS, "GANN_EIGHTHS"),
    (D.GANN_FAN, "GANN_FAN"),
])
def test_ratios_match_the_chart(py, js):
    assert py == _array(js), f"{js} has drifted from drawtools.py"


def test_square_width_matches():
    assert D.GANN_SQUARE_BARS == _const("GANN_SQUARE_BARS")


def test_every_tool_exists_on_the_chart_with_the_same_anchor_count():
    """A tool the backend offers and the chart cannot build draws nothing and
    reports success — the one failure mode this catalogue can produce."""
    src = JS.read_text(encoding="utf-8")
    for name, spec in D.TOOLS.items():
        m = re.search(rf"^\s*{name}: \{{ label: \"([^\"]+)\", anchors: (\d+)",
                      src, re.M)
        assert m, f"{name} is in drawtools.py but not in tools.js SPECS"
        assert m.group(1) == spec["label"], f"{name}: label drifted"
        assert int(m.group(2)) == spec["anchors"], f"{name}: anchor count drifted"


def test_retracement_ladder_matches_the_chart_convention():
    """r=0 at the leg's END, r=1 at its START — the app-wide orientation."""
    got = D._price_ladder(100.0, 200.0, [0, 0.5, 1])
    assert [x["price"] for x in got] == [200.0, 150.0, 100.0]


def test_extension_projects_from_the_third_anchor():
    pts = [{"t": 0, "v": 100.0}, {"t": 1, "v": 200.0}, {"t": 2, "v": 150.0}]
    got = D.levels("fibExtension", pts)["levels"]
    by = {x["ratio"]: x["price"] for x in got}
    assert by[0] == 150.0            # the pullback low itself
    assert by[1] == 250.0            # one whole leg on top of it
    assert by[1.618] == pytest.approx(311.8, abs=0.01)


def test_a_slope_tool_reports_no_levels():
    pts = [{"t": 0, "v": 100.0}, {"t": 10, "v": 200.0}]
    assert D.levels("gannFan", pts) is None
    note = D.report("gannFan", pts)["_note"]
    assert "no level table" in note


def test_fib_channel_rails_are_ranges_not_prices():
    pts = [{"t": 0, "v": 100.0}, {"t": 10, "v": 200.0}, {"t": 0, "v": 120.0}]
    rails = D.levels("fibChannel", pts)["rails"]
    one = next(r for r in rails if r["ratio"] == 1)
    assert (one["from"], one["to"]) == (120.0, 220.0)


def test_time_zone_counts_in_fibonacci_numbers():
    """Gapless bars: bar-index and wall-clock agree, so the numbers are plain."""
    bars = [i * 60 for i in range(200)]
    pts = [{"t": 0, "v": 1.0}, {"t": 60, "v": 1.0}]
    got = D.levels("fibTimeZone", pts, bars=bars)
    assert got["unit_bars"] == 1
    assert [d["at"] for d in got["dates"][:5]] == [0, 60, 120, 180, 300]


# ── the time axis is a queue of BARS ────────────────────────────────────
# The defect these cover: every DATE a ratio tool reports used to be a
# fraction of WALL CLOCK. On an intraday series that is not the axis — six of
# a time zone's ten verticals walked into the same overnight gap and stacked
# on one column, and four of a Gann box's seven time divisions collapsed onto
# one. The chart drew them there and the reply quoted the same wrong dates.

def _gapped():
    """12 sessions of 75 five-minute bars, with a 16-hour hole between each."""
    out, t = [], 1750000000
    for _ in range(12):
        for _ in range(75):
            out.append(t)
            t += 300
        t += 16 * 3600
    return out


def test_time_zone_counts_bars_not_seconds():
    bars = _gapped()
    pts = [{"t": bars[100], "v": 1.0}, {"t": bars[110], "v": 1.0}]
    got = D.levels("fibTimeZone", pts, bars=bars)
    assert got["unit_bars"] == 10
    at = {d["n"]: d["at"] for d in got["dates"]}
    # every fibonacci number lands on its own bar, n×10 forward
    for n in (1, 2, 3, 5, 8, 13, 21, 34, 55):
        assert at[n] == bars[100 + 10 * n], f"n={n} left the bar clock"
    # …and they are all DISTINCT, which is the whole failure
    assert len(set(at.values())) == len(at)


def test_gann_time_divisions_do_not_collapse_into_a_gap():
    bars = _gapped()
    pts = [{"t": bars[600], "v": 100.0}, {"t": bars[700], "v": 200.0}]
    got = D.levels("gannBox", pts, bars=bars)
    at = [d["at"] for d in got["time_levels"]]
    assert len(set(at)) == len(at), "time divisions stacked on one column"
    half = next(d for d in got["time_levels"] if d["ratio"] == 0.5)
    assert half["at"] == bars[650]


def test_time_extension_counts_bars():
    bars = _gapped()
    pts = [{"t": bars[100], "v": 1.0}, {"t": bars[140], "v": 2.0},
           {"t": bars[200], "v": 1.5}]
    got = D.levels("fibTimeExtension", pts, bars=bars)
    assert got["unit_bars"] == 40
    at = {d["ratio"]: d["at"] for d in got["dates"]}
    assert at[1] == bars[240]
    # 40 × 1.618 = 64.72 bars, which is a FRACTIONAL index — it should land
    # between two bars rather than snap to one. A vertical is allowed to sit
    # between candles; rounding it would be inventing a precision the ratio
    # does not have.
    assert bars[264] < at[1.618] < bars[265]


def test_channel_zero_span_falls_back_the_way_the_chart_does():
    """Geo.valueAt returns the FAR anchor's value when the span is zero.
    Falling back to the near one flipped the offset's sign, so the reply
    quoted the mirror image of the rails on screen."""
    pts = [{"t": 0, "v": 100.0}, {"t": 0, "v": 200.0}, {"t": 0, "v": 150.0}]
    rails = D.levels("fibChannel", pts)["rails"]
    one = next(r for r in rails if r["ratio"] == 1)
    assert (one["from"], one["to"]) == (50.0, 150.0)


def test_gann_grid_cuts_both_axes_by_the_same_fractions():
    bars = list(range(0, 200))
    pts = [{"t": 0, "v": 100.0}, {"t": 100, "v": 200.0}]
    got = D.levels("gannBox", pts, bars=bars)
    assert {x["ratio"] for x in got["price_levels"]} == set(D.GANN)
    assert {x["ratio"] for x in got["time_levels"]} == set(D.GANN)
    half_p = next(x for x in got["price_levels"] if x["ratio"] == 0.5)
    half_t = next(x for x in got["time_levels"] if x["ratio"] == 0.5)
    assert (half_p["price"], half_t["at"]) == (150.0, 50)


def test_unknown_tool_is_named_not_swallowed():
    out = D.report("fibonacciDeluxe", [{"t": 0, "v": 1.0}])
    assert "error" in out and "fib" in " ".join(out["available"])
