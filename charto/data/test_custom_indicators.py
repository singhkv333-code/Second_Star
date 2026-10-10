"""Custom indicators: the sandbox holds, the validator catches what it claims
to catch, and the store never lets a failed build replace a passed one.

No model calls — the builder's LLM is out of scope here; everything it hands
over goes through the code tested below.

Run: ../../pivot/.venv/bin/python -m pytest test_custom_indicators.py -q
"""
import os
import tempfile

os.environ.setdefault("CHARTO_USERS_DB", os.path.join(tempfile.mkdtemp(), "users.db"))

import custom_indicators as ci  # noqa: E402
import indicator_sandbox as sb  # noqa: E402
import indicators as native  # noqa: E402

ROWS = ci.synthetic("walk", 800)
REAL = [("walk 5m", ROWS, "5m", 19800)]

SPEC = {
    "title": "RSI with signal", "short": "RSI+S", "formula": "Wilder RSI; EMA signal",
    "classification": "standard", "standard_name": "Relative Strength Index",
    "pane": "own", "bounds": [0, 100], "levels": [70, 30],
    "lines": [{"key": "rsi", "label": "RSI", "plot": "line"},
              {"key": "signal", "label": "Signal", "plot": "line"}],
    "inputs": [{"key": "length", "label": "Length", "type": "int", "default": 14, "min": 2, "max": 200},
               {"key": "source", "label": "Source", "type": "source", "default": "close"}],
    "reference": {"kind": "native", "name": "rsi", "params": {"period": 14},
                  "line_map": {"rsi": "rsi"}},
}
GOOD = '''
def compute(bars, params):
    r = ta.rsi(bars[params["source"]], params["length"])
    return {"rsi": r, "signal": ta.ema(r, 9)}
'''


def checks(rep):
    return {c["id"]: c["status"] for c in rep["checks"]}


def test_sandbox_matches_native_engine():
    lines, bad = sb.compute(GOOD, ROWS, {"length": 14, "source": "close"})
    ref = native.compute("rsi", ROWS, 14)["lines"]["rsi"]
    assert bad == 0
    assert max(abs(a - b) for a, b in zip(lines["rsi"], ref) if a is not None and b is not None) < 1e-9


def test_sandbox_refuses_escapes():
    for code in ("import os\ndef compute(bars, params):\n    return {}",
                 "def compute(bars, params):\n    return {'x': ().__class__}",
                 "def compute(bars, params):\n    s = '{0.__class__}'.format(1)\n    return {}",
                 "X=1\ndef compute(bars, params):\n    global X\n    return {}"):
        assert sb.check_source(code), code
    r = sb.run("def compute(bars, params):\n    open('/etc/hosts')\n    return {}",
               [{"id": "a", "bars": sb.columns(ROWS[:50]), "params": {}}], timeout=5)
    assert not r["results"][0]["ok"]


def test_sandbox_times_out_a_hang():
    try:
        sb.run("def compute(bars, params):\n    while True:\n        pass",
               [{"id": "a", "bars": sb.columns(ROWS[:50]), "params": {}}], timeout=3)
    except sb.SandboxError as exc:
        assert "timed out" in str(exc) or "CPU" in str(exc)
    else:
        raise AssertionError("a hang must not return")


def test_validator_passes_a_correct_study():
    rep = ci.validate(SPEC, GOOD, REAL)
    assert rep["passed"], rep["summary"]
    assert checks(rep)["reference"] == "pass"


def test_validator_catches_look_ahead():
    leak = GOOD.replace('return {"rsi": r, "signal": ta.ema(r, 9)}',
                        'return {"rsi": r, "signal": [None if i + 1 >= len(r) else r[i + 1] '
                        'for i in range(len(r))]}')
    rep = ci.validate(SPEC, leak, REAL)
    assert not rep["passed"] and checks(rep)["causal"] == "fail"


def test_validator_catches_flat_tape_division():
    fragile = '''
def compute(bars, params):
    h, l, c = bars["high"], bars["low"], bars["close"]
    k = [100 * (c[i] - l[i]) / (h[i] - l[i]) for i in range(len(c))]
    return {"rsi": k, "signal": ta.sma(k, 3)}
'''
    rep = ci.validate(SPEC, fragile, REAL)
    assert not rep["passed"] and checks(rep)["edge"] == "fail"


def test_validator_catches_wrong_formula_against_reference():
    wrong = GOOD.replace("ta.rsi(", "ta.roc(")
    rep = ci.validate({**SPEC, "bounds": None}, wrong, REAL)
    assert checks(rep)["reference"] == "fail"


def test_validator_catches_nan():
    nan = 'def compute(bars, params):\n    return {"rsi": [nan] * bars["n"], "signal": [0.0] * bars["n"]}'
    assert checks(ci.validate({**SPEC, "reference": None}, nan, REAL))["finite"] == "fail"


def test_spec_requires_declared_library_reference_or_reason():
    spec = {**SPEC, "reference": None, "library_equivalent": "rsi"}
    assert any("pandas-ta-classic `rsi`" in p for p in ci.check_spec(spec))
    assert not any("pandas-ta" in p for p in ci.check_spec(
        {**spec, "reference_waiver": "the library seeds differently"}))


def test_store_keeps_validated_version_when_an_edit_fails():
    ok = ci.validate(SPEC, GOOD, REAL)
    rec = ci.save(1, spec=SPEC, code=GOOD, report=ok, prompt="rsi")
    assert rec["status"] == "validated" and rec["version"] == 1
    bad = {"passed": False, "checks": [], "summary": "0 of 1 checks passed"}
    after = ci.save(1, spec=SPEC, code="broken", report=bad, prompt="break it", cid=rec["id"])
    assert after.get("last_attempt_failed")
    now = ci.get(rec["id"], 1)
    assert now["status"] == "validated" and now["code"] == GOOD and now["version"] == 1
    assert ci.get(rec["id"], 2) is None            # another account cannot read it
    v2 = ci.save(1, spec=SPEC, code=GOOD + "\n", report=ok, prompt="v2", cid=rec["id"])
    assert v2["version"] == 2
    assert ci.catalog_entry(v2)["custom"] and ci.catalog_entry(v2)["levels"] == [70, 30]
    assert ci.delete(1, rec["id"]) and ci.get(rec["id"], 1) is None


def test_compute_for_refuses_unvalidated_and_clamps_inputs():
    rec = {"id": "cx_abcdefgh", "version": 1, "status": "failed", "spec": SPEC, "code": GOOD}
    try:
        ci.compute_for(rec, ROWS, {})
    except ValueError:
        pass
    else:
        raise AssertionError("a failed build must never compute")
    rec["status"] = "validated"
    out = ci.compute_for(rec, ROWS, {"length": "9999"})
    assert out["spec"]["length"] == 200


# ── baskets: studies that read other instruments ─────────────────────────
BASKET_SPEC = {
    "title": "Basket breadth", "short": "Breadth", "formula": "% of members above their SMA",
    "classification": "custom", "pane": "own", "bounds": [0, 100], "levels": [50],
    "lines": [{"key": "breadth", "label": "% above", "plot": "line"},
              {"key": "regime", "label": "Regime", "plot": "state"}],
    "inputs": [{"key": "length", "label": "Length", "type": "int", "default": 20, "min": 2, "max": 200}],
    "basket": {"label": "test", "symbols": ["A", "B"]}, "reference": None,
    "reference_waiver": "custom method",
}
BREADTH = '''
def compute(bars, params):
    conds = []
    for c in bars["basket"]["close"]:
        s = ta.sma(c, params["length"])
        conds.append([None if s[i] is None or c[i] is None else c[i] > s[i] for i in range(bars["n"])])
    br = ta.xpct(conds)
    return {"breadth": br,
            "regime": [None if x is None else (1 if x > 60 else -1 if x < 40 else 0) for x in br]}
'''


def _real_with_basket():
    members = {"A": ci.synthetic("trend_up", 800), "B": ci.synthetic("range", 800)}
    return [("walk 5m", ROWS, "5m", 19800, ci.align_basket(ROWS, members, label="test"))]


def test_align_basket_is_causal_and_carries_gaps_briefly():
    m = [r for i, r in enumerate(ROWS[:40]) if i not in (5, 20, 21, 22, 23, 24, 25, 26)]
    b = ci.align_basket(ROWS[:40], {"X": m}, max_fill=5)
    c, v = b["close"][0], b["volume"][0]
    assert c[5] == ROWS[4][4] and v[5] == 0          # one missing bar: carried, no volume
    assert c[24] == ROWS[19][4] and c[25] is None     # carried 5 bars, then no value
    late = ci.align_basket(ROWS[:40], {"L": ROWS[10:40]})
    assert late["close"][0][:10] == [None] * 10       # never a bar from the future


def test_basket_study_validates_including_look_ahead_on_members():
    rep = ci.validate(BASKET_SPEC, BREADTH, _real_with_basket())
    st = checks(rep)
    assert rep["passed"], rep["summary"]
    assert st["causal"] == "pass" and st["state"] == "pass"
    assert "bounds" in st                             # the state ribbon is exempt from [0, 100]


def test_basket_must_be_declared_and_read():
    rep = ci.validate({**BASKET_SPEC, "basket": None}, BREADTH, REAL)
    assert checks(rep).get("basket") == "fail"


def test_state_lines_hold_only_three_values():
    bad = BREADTH.replace("(1 if x > 60 else -1 if x < 40 else 0)", "x / 50")
    rep = ci.validate(BASKET_SPEC, bad, _real_with_basket())
    assert checks(rep)["state"] == "fail"


def test_compute_for_passes_the_basket_through():
    rec = {"id": "cx_basketaa", "status": "validated", "version": 1,
           "spec": BASKET_SPEC, "code": BREADTH}
    bk = _real_with_basket()[0][4]
    out = ci.compute_for(rec, ROWS, {}, interval="5m", basket=bk)
    assert out["last"]["breadth"] is not None
    assert out["last"]["regime"] in (1.0, 0.0, -1.0)
