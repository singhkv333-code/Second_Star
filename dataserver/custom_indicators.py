"""Custom indicators: the spec, the validator, and the per-user store.

A custom indicator is DATA, not a code path: a spec (title, pane, lines,
inputs, bounds, classification, sources) plus a `compute(bars, params)`
function in a restricted Python dialect. Nothing here is specific to any one
indicator — the same machinery carries a Squeeze Momentum, a z-score of
volume or a rule the user invented this morning.

The life of one, and the module that owns each step:

    generation   indicator_builder.py   brief → research → code (an LLM)
    validation   validate() below        ~40 sandboxed runs, a report
    execution    indicator_sandbox.py   a child process, every single time
    rendering    the native chart path   /indicators + /indicator, unchanged

The chart never learns that a study was AI-written beyond a flag for its
menu section: the catalogue entry has the same shape as a native one, so the
legend, the settings dialog, panes, colours and tooltips are the native ones.

**Nothing reaches a chart unvalidated.** `register()` refuses a report that
did not pass, `compute_for()` refuses a study whose stored status is not
`validated`, and a failed build is kept (so its source and report can be
read and edited) but is never listed in the catalogue.
"""
from __future__ import annotations

import json
import math
import random
import re
import secrets
import sqlite3
import string
import threading
import time
from os import environ
from pathlib import Path

import indicators as native
import indicator_sandbox as sandbox

DB_PATH = Path(environ.get("CHARTO_USERS_DB") or Path(__file__).parent / "charto_users.db")
_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row
_db.execute("PRAGMA journal_mode=WAL")
_db.execute("PRAGMA busy_timeout=10000")
_lock = threading.Lock()
_db.executescript("""
CREATE TABLE IF NOT EXISTS custom_indicators (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL CHECK(status IN ('validated','failed','deleted')),
  prompt TEXT NOT NULL DEFAULT '',
  spec TEXT NOT NULL DEFAULT '{}',
  code TEXT NOT NULL DEFAULT '',
  report TEXT NOT NULL DEFAULT '{}',
  history TEXT NOT NULL DEFAULT '[]',
  created INTEGER NOT NULL,
  updated INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS custom_indicators_user ON custom_indicators(user_id, status, updated DESC);
""")
_db.commit()

PREFIX = "cx_"
_ID_RE = re.compile(r"^cx_[a-z]{8}$")
PLOTS = ("line", "stepline", "area", "columns", "circles", "state")
MAX_BASKET = 40
INPUT_TYPES = ("int", "float", "bool", "enum", "source")
CLASSES = ("standard", "variant", "custom")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,23}$")


def is_custom(name: str) -> bool:
    return bool(name) and name.startswith(PREFIX)


def new_id() -> str:
    # letters only: the chart parses a trailing number on an id as a period
    return PREFIX + "".join(secrets.choice(string.ascii_lowercase) for _ in range(8))


# ══════════════════════════════════════════════════════════════════
# THE SPEC
# ══════════════════════════════════════════════════════════════════

def check_spec(spec: dict) -> list[str]:
    """Every problem with a spec, phrased for the model that has to fix it."""
    p: list[str] = []
    if not isinstance(spec, dict):
        return ["spec must be an object"]
    for k in ("title", "short", "formula"):
        if not str(spec.get(k) or "").strip():
            p.append(f"spec.{k} is empty")
    if len(str(spec.get("short") or "")) > 24:
        p.append("spec.short must be at most 24 characters (it is the legend label)")
    if spec.get("classification") not in CLASSES:
        p.append(f"spec.classification must be one of {CLASSES}")
    if spec.get("pane") not in ("overlay", "own"):
        p.append("spec.pane must be 'overlay' (on price) or 'own' (its own pane)")
    lines = spec.get("lines")
    if not isinstance(lines, list) or not 1 <= len(lines) <= 8:
        p.append("spec.lines must list 1-8 lines")
        lines = []
    seen = set()
    for ln in lines:
        k = str((ln or {}).get("key") or "")
        if not _KEY_RE.match(k):
            p.append(f"line key '{k}' must be lowercase snake_case, max 24 chars")
        if k in seen:
            p.append(f"line key '{k}' is duplicated")
        seen.add(k)
        if (ln or {}).get("plot", "line") not in PLOTS:
            p.append(f"line '{k}' plot must be one of {PLOTS}")
    keys = set()
    for f in spec.get("inputs") or []:
        k = str((f or {}).get("key") or "")
        t = (f or {}).get("type")
        if not _KEY_RE.match(k) or k == "period":
            p.append(f"input key '{k}' must be snake_case and not 'period' (use 'length')")
        if k in keys:
            p.append(f"input key '{k}' is duplicated")
        keys.add(k)
        if t not in INPUT_TYPES:
            p.append(f"input '{k}' type must be one of {INPUT_TYPES}")
            continue
        d = f.get("default")
        if t in ("int", "float"):
            lo, hi = f.get("min"), f.get("max")
            if not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                       for x in (d, lo, hi)):
                p.append(f"input '{k}' needs numeric default, min and max")
            elif not lo <= d <= hi:
                p.append(f"input '{k}' default {d} is outside [{lo}, {hi}]")
            elif t == "int" and any(int(x) != x for x in (d, lo, hi)):
                p.append(f"input '{k}' is int but has fractional bounds")
        elif t == "bool" and not isinstance(d, bool):
            p.append(f"input '{k}' is bool; default must be true/false")
        elif t == "enum":
            opts = f.get("options") or []
            if not opts or d not in opts:
                p.append(f"input '{k}' enum needs options containing the default")
        elif t == "source" and d not in native.SOURCES:
            p.append(f"input '{k}' source default must be one of {native.SOURCES}")
    b = spec.get("bounds")
    if b is not None and not (isinstance(b, list) and len(b) == 2
                              and all(isinstance(x, (int, float)) for x in b) and b[0] < b[1]):
        p.append("spec.bounds must be [low, high] or null")
    ref = spec.get("reference")
    if ref:
        if ref.get("kind") == "native" and ref.get("name") not in native.SPECS:
            p.append(f"reference native name must be one of {sorted(native.SPECS)}")
        if ref.get("kind") not in ("native", "pandas_ta"):
            p.append("reference.kind must be 'native' or 'pandas_ta'")
        if not isinstance(ref.get("line_map"), dict) or not ref.get("line_map"):
            p.append("reference.line_map must map at least one of your lines to the reference's output")
        elif any(k not in seen for k in ref["line_map"]):
            p.append("reference.line_map keys must be your own line keys")
    bk = spec.get("basket")
    if bk is not None:
        syms = (bk or {}).get("symbols") if isinstance(bk, dict) else None
        if not isinstance(syms, list) or not syms or not all(isinstance(x, str) and x for x in syms):
            p.append("spec.basket must be null or {label, symbols:[...]} with at least one symbol")
        elif len(syms) > MAX_BASKET:
            p.append(f"spec.basket has {len(syms)} symbols; the limit is {MAX_BASKET}")
    if spec.get("classification") == "standard" and not spec.get("standard_name"):
        p.append("a standard indicator must name itself in spec.standard_name")
    lib = str(spec.get("library_equivalent") or "")
    if lib and not ref and not str(spec.get("reference_waiver") or "").strip():
        p.append(f"your research named pandas-ta-classic `{lib}` as an implementation of this "
                 f"indicator — declare it as spec.reference, or say in reference waiver which "
                 f"convention differs (the user is shown that reason)")
    return p


def default_params(spec: dict) -> dict:
    return {f["key"]: f["default"] for f in spec.get("inputs") or []}


def coerce_params(spec: dict, raw: dict) -> dict:
    """User/query values → typed params, every key the spec declares and
    nothing else. Out-of-range numbers are clamped, never passed through."""
    out = default_params(spec)
    for f in spec.get("inputs") or []:
        k, t = f["key"], f["type"]
        if k not in (raw or {}) or raw[k] in (None, ""):
            continue
        v = raw[k]
        try:
            if t == "bool":
                out[k] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            elif t == "enum":
                if v not in f["options"]:
                    raise ValueError(f"bad {k} '{v}'; allowed: {f['options']}")
                out[k] = v
            elif t == "source":
                if v not in native.SOURCES:
                    raise ValueError(f"bad {k} '{v}'")
                out[k] = v
            else:
                x = float(v) if t == "float" else int(float(v))
                out[k] = min(max(x, f["min"]), f["max"])
        except (TypeError, ValueError) as exc:
            raise ValueError(str(exc) if "bad" in str(exc) else f"bad {k} '{v}'") from exc
    return out


def catalog_entry(rec: dict) -> dict:
    """The /indicators shape, so the chart treats this exactly like a native
    study. `inputs` is in the dialog's own vocabulary."""
    spec = rec["spec"]
    inputs = []
    for f in spec.get("inputs") or []:
        e = {"key": f["key"], "label": f.get("label") or f["key"].replace("_", " ").title(),
             "type": f["type"], "default": f["default"]}
        if f["type"] in ("int", "float"):
            e.update(min=f["min"], max=f["max"],
                     step=f.get("step") or (1 if f["type"] == "int" else 0.01))
        elif f["type"] == "enum":
            e["options"] = [{"value": o, "label": str(o).replace("_", " ").title()}
                            for o in f["options"]]
        elif f["type"] == "source":
            e["options"] = list(native.SOURCES)
        inputs.append(e)
    out = {"name": rec["id"], "period": 0, "pane": spec["pane"],
           "group": "custom", "formula": spec["formula"],
           "lines": [ln["key"] for ln in spec["lines"]],
           "inputs": inputs, "custom": True, "version": rec["version"],
           "title": spec["title"], "short": spec["short"],
           "classification": spec["classification"],
           "line_labels": {ln["key"]: ln.get("label") or ln["key"] for ln in spec["lines"]},
           "plots": {ln["key"]: ln.get("plot", "line") for ln in spec["lines"]},
           "levels": [x for x in (spec.get("levels") or []) if isinstance(x, (int, float))][:6]}
    if spec.get("bounds"):
        out["bounds"] = list(spec["bounds"])
    if spec.get("basket"):
        out["basket"] = {"label": spec["basket"].get("label") or "",
                         "count": len(spec["basket"].get("symbols") or [])}
    return out


def library_probe(fn: str, rows: list[tuple]) -> dict | None:
    """Verify a pandas-ta-classic function the model named, and describe it
    from the library itself: keyword defaults and the output columns with a
    sample value each. None when no such indicator exists — the model's
    claim is checked, never trusted, and the coder sees real column names
    instead of guessing them."""
    try:
        import inspect
        import pandas as pd
        import pandas_ta_classic as pta
    except Exception:  # noqa: BLE001
        return None
    if fn not in {f for fs in pta.Category.values() for f in fs}:
        return None
    f = getattr(pta, fn)
    sig = inspect.signature(f).parameters
    kwargs = {k: (None if v.default is inspect.Parameter.empty else v.default)
              for k, v in sig.items()
              if k not in ("open_", "high", "low", "close", "volume", "offset", "kwargs")}
    out = None
    if len(rows) >= 60:
        df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"])
        args = {nm: df[col].astype(float) for col, nm in
                (("open", "open_"), ("high", "high"), ("low", "low"), ("close", "close"),
                 ("volume", "volume")) if nm in sig}
        try:
            res = f(**args)
            if isinstance(res, pd.Series):
                out = {"series": None if pd.isna(res.iloc[-1]) else round(float(res.iloc[-1]), 4)}
            elif res is not None:
                out = {c: (None if pd.isna(res[c].iloc[-1]) else round(float(res[c].iloc[-1]), 4))
                       for c in res.columns}
        except Exception:  # noqa: BLE001 — the signature alone is still useful
            out = None
    # the library's own description of its maths and keywords — its defaults
    # live in the docstring, not the signature (which says None for all)
    doc = inspect.getdoc(f) or ""
    at = doc.find("Calculation:")
    doc = (doc[at:] if at >= 0 else doc)
    cut = doc.find("Returns:")
    doc = (doc[:cut] if cut > 0 else doc).strip()[:2200]
    return {"fn": fn, "keywords": sorted(kwargs), "definition": doc,
            "outputs_at_defaults": out}


# ══════════════════════════════════════════════════════════════════
# VALIDATION
# ══════════════════════════════════════════════════════════════════

def align_basket(rows: list[tuple], members: dict[str, list[tuple]], *, label: str = "",
                 max_fill: int = 5) -> dict:
    """Other instruments' bars on THIS chart's bar times, causally.

    For each chart bar, a member contributes its own bar with the same open
    time; where it has none (a halt, a later listing, a session it does not
    trade) its last close is carried forward for at most `max_fill` bars
    with zero volume, and after that — or before its first bar — it has no
    value (None). A member is never given a bar from after the chart bar it
    sits beside. Members are kept in the order given."""
    times = [r[0] for r in rows]
    out = {"label": label, "symbols": [], **{k: [] for k in ("open", "high", "low", "close", "volume")}}
    for sym, mrows in members.items():
        by_t = {r[0]: r for r in mrows}
        mts = sorted(by_t)
        o, h, l, c, v = [], [], [], [], []
        j, held, gap = 0, None, 0
        for t in times:
            while j < len(mts) and mts[j] <= t:
                j += 1
            r = by_t.get(t)
            if r is not None:
                o.append(r[1]); h.append(r[2]); l.append(r[3]); c.append(r[4]); v.append(r[5] or 0)
                held, gap = t, 0
                continue
            # the member's latest bar at or before t, if any; `gap` counts the
            # chart bars it has been carried across
            prev = by_t[mts[j - 1]] if j else None
            if prev is not None and prev[0] != held:
                held, gap = prev[0], 0
            gap += 1
            if prev is None or gap > max_fill:
                o.append(None); h.append(None); l.append(None); c.append(None); v.append(None)
            else:
                p = prev[4]
                o.append(p); h.append(p); l.append(p); c.append(p); v.append(0)
        out["symbols"].append(sym)
        for k, col in zip(("open", "high", "low", "close", "volume"), (o, h, l, c, v)):
            out[k].append(col)
    return out


def synthetic_basket(rows: list[tuple], k: int = 6, seed: int = 11) -> dict:
    """Members for a synthetic dataset: the series re-scaled with their own
    noise, one listed late, one with no data at all — the shapes a real
    basket throws at a study."""
    rng = random.Random(f"basket:{seed}:{len(rows)}")
    members = {}
    for m in range(k):
        scale = 0.4 + 0.5 * m
        drift = rng.gauss(0, 0.002)
        start = len(rows) * 2 // 5 if m == k - 2 else 0
        mr = []
        f = 1.0
        for i, r in enumerate(rows):
            f *= 1 + drift + rng.gauss(0, 0.003)
            if i < start:
                continue
            o, h, l, c = (round(x * scale * f, 4) for x in r[1:5])
            mr.append((r[0], o, max(o, h, c), min(o, l, c), c, (r[5] or 0) * (0.5 + m)))
        members[f"MEMBER{m + 1}"] = [] if m == k - 1 else mr
    return align_basket(rows, members, label="synthetic basket")


def synthetic(kind: str, n: int = 600, seed: int = 7) -> list[tuple]:
    """Deterministic bars for one market condition. Times are 5-minute steps;
    the regimes are what matter — a trend, a reversal, a range, a crash, a
    dead-flat tape, a short listing, a series with no volume."""
    rng = random.Random(f"{kind}:{seed}")
    t0, p, rows = 1_750_000_000, 100.0, []
    for i in range(n):
        o = p
        if kind == "trend_up":
            p *= 1 + 0.0015 + rng.gauss(0, 0.004)
        elif kind == "trend_down":
            p *= 1 - 0.0015 + rng.gauss(0, 0.004)
        elif kind == "range":
            p = 100 + 4 * math.sin(i / 15) + rng.gauss(0, 0.6)
        elif kind == "shock":
            p *= (0.75 if i == n // 2 else 1) * (1 + rng.gauss(0, 0.006))
        elif kind == "flat":
            p = 100.0
        else:
            p *= 1 + rng.gauss(0, 0.006)
        if kind == "flat":
            h = l = o = p
        else:
            h = max(o, p) * (1 + abs(rng.gauss(0, 0.002)))
            l = min(o, p) * (1 - abs(rng.gauss(0, 0.002)))
        v = 0.0 if kind in ("flat_novol", "zero_volume") else float(rng.randint(500, 5000))
        if kind == "flat":
            v = 1000.0
        rows.append((t0 + 300 * i, round(o, 4), round(h, 4), round(l, 4), round(p, 4), v))
    return rows


def _ok_val(x) -> bool:
    return x is not None and not isinstance(x, str)


def _compare(ours: list, ref: list, warm: int) -> dict:
    """Max relative error over the points where both have values, after
    `warm` such points (recursive studies converge from different seeds)."""
    pairs = [(i, a, b) for i, (a, b) in enumerate(zip(ours, ref))
             if _ok_val(a) and b is not None and not (isinstance(b, float) and math.isnan(b))]
    pairs = pairs[warm:]
    if len(pairs) < 20:
        return {"compared": len(pairs), "error": "too few overlapping values to compare"}
    # Scaled by the reference's RANGE (or typical size, if larger). A median
    # is 0 for an oscillator pinned at 0 and 100, which made a single
    # disagreement read as a 1e14 relative error.
    vals = sorted(b for _, _, b in pairs)
    scale = max(vals[-1] - vals[0], abs(vals[len(vals) // 2]), 1e-12)
    worst = max(pairs, key=lambda x: abs(x[1] - x[2]))
    rel = abs(worst[1] - worst[2]) / max(scale, 1e-12)
    close = sum(1 for _, a, b in pairs if abs(a - b) <= 1e-3 * scale)
    return {"compared": len(pairs), "max_rel_error": rel,
            "agree_pct": round(100.0 * close / len(pairs), 1),
            "worst": {"index": worst[0], "ours": round(worst[1], 6), "reference": round(worst[2], 6)}}


def _reference_lines(ref: dict, rows: list[tuple], params: dict) -> tuple[dict, str]:
    """({our_line: reference series}, description) from a TRUSTED library."""
    lm = ref.get("line_map") or {}
    if ref.get("kind") == "native":
        rp = dict(ref.get("params") or {})
        period = int(rp.pop("period", 0) or 0)
        source = str(rp.pop("source", "") or "")
        res = native.compute(ref["name"], rows, period, source, **rp)
        got = {}
        for ours, theirs in lm.items():
            if theirs not in res["lines"]:
                raise ValueError(f"native {ref['name']} has no line '{theirs}'; "
                                 f"it has {sorted(res['lines'])}")
            got[ours] = res["lines"][theirs]
        return got, f"Charto native {ref['name']}"
    import inspect
    import pandas as pd
    import pandas_ta_classic as pta
    fn_name = str(ref.get("fn") or "")
    allowed = {f for fs in pta.Category.values() for f in fs}
    if fn_name not in allowed:
        raise ValueError(f"pandas_ta function '{fn_name}' is not an indicator in pandas-ta-classic")
    fn = getattr(pta, fn_name)
    sig = inspect.signature(fn).parameters
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"])
    df["hl2"] = (df.high + df.low) / 2
    df["hlc3"] = (df.high + df.low + df.close) / 3
    df["ohlc4"] = (df.open + df.high + df.low + df.close) / 4
    args = {}
    for col, names in (("open", ("open_", "open")), ("high", ("high",)), ("low", ("low",)),
                       ("close", ("close",)), ("volume", ("volume",))):
        for nm in names:
            if nm in sig:
                args[nm] = df[col].astype(float)
    # a single-series function (zscore, rsi, ema…) reads `close`; input= feeds
    # it the column the study actually runs on, e.g. a z-score of volume
    feed = str((ref.get("kwargs") or {}).get("input") or "")
    if feed in df.columns and "close" in sig:
        args["close"] = df[feed].astype(float)
    takes_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.values())
    for k, v in (ref.get("kwargs") or {}).items():
        if k == "input":
            continue
        # pandas-ta reads switches such as lazybear= and tr= from **kwargs
        if (k in sig or takes_any) and isinstance(v, (int, float, str, bool)) and k not in args:
            args[k] = v
    out = fn(**args)
    if out is None:
        raise ValueError(f"pandas_ta.{fn_name} returned nothing for these arguments")
    got = {}
    for ours, col in lm.items():
        if isinstance(out, pd.Series):
            series = out
        elif col in out.columns:
            series = out[col]
        else:
            raise ValueError(f"pandas_ta.{fn_name} has no column '{col}'; "
                             f"its columns are {list(out.columns)}")
        got[ours] = [None if pd.isna(x) else float(x) for x in series.tolist()]
    return got, f"pandas-ta-classic {fn_name}"


def validate(spec: dict, code: str, real: list[tuple[str, list[tuple], str, int]],
             *, progress=None) -> dict:
    """Run every check. `real` is [(label, rows, interval, tz_offset)] of real
    market data; synthetic regimes are added here. Returns a report:
    {passed, checks:[{id,label,status,detail,blocking}], datasets, summary}.

    `progress(detail)` is called as checks complete, for the build stream.
    """
    say = progress or (lambda *_: None)
    checks: list[dict] = []

    def add(cid, label, status, detail="", blocking=True):
        checks.append({"id": cid, "label": label, "status": status,
                       "detail": detail, "blocking": blocking})

    problems = sandbox.check_source(code)
    add("policy", "Code compiles and stays inside the sandbox policy",
        "fail" if problems else "pass", "; ".join(problems[:6]))
    sp = check_spec(spec)
    add("spec", "Spec declares lines, pane, inputs and classification",
        "fail" if sp else "pass", "; ".join(sp[:6]))
    if problems or sp:
        return _report(checks, [])

    basketed = bool(spec.get("basket"))
    reads_basket = "basket" in code
    if basketed != reads_basket:
        add("basket", "Reads the basket it declares", "fail",
            "spec.basket is set but compute() never reads bars['basket']" if basketed else
            "compute() reads bars['basket'] but spec.basket is null — declare the members")
        return _report(checks, [])

    declared = [ln["key"] for ln in spec["lines"]]
    sparse = {ln["key"] for ln in spec["lines"] if ln.get("sparse")}
    states = {ln["key"] for ln in spec["lines"] if ln.get("plot") == "state"}
    defaults = default_params(spec)
    needs_volume = bool(spec.get("needs_volume"))

    datasets: dict[str, dict] = {}
    meta: list[dict] = []
    real_keys = []
    for item in real:
        label, rows, interval, tz = item[:4]
        bk = item[4] if len(item) > 4 else None
        if basketed and bk is None:
            meta.append({"label": label, "bars": len(rows), "used": False,
                         "why": "no basket bars for this series"})
            continue
        if len(rows) < 30:
            meta.append({"label": label, "bars": len(rows), "used": False,
                         "why": "not enough stored bars"})
            continue
        if needs_volume and not any(r[5] for r in rows):
            meta.append({"label": label, "bars": len(rows), "used": False,
                         "why": "prints no volume; a volume study is refused there, as natively"})
            continue
        key = f"real{len(real_keys)}"
        datasets[key] = sandbox.columns(rows, interval=interval, tz_offset=tz,
                                        symbol=label.split(" ")[0],
                                        basket=bk if basketed else None)
        real_keys.append((key, label, rows))
        meta.append({"label": label, "bars": len(rows), "used": True,
                     "from": rows[0][0], "to": rows[-1][0]})
    synth = ["trend_up", "trend_down", "range", "shock", "flat"] + (
        [] if needs_volume else ["zero_volume"])
    for s in synth:
        rows_s = synthetic(s)
        datasets[s] = sandbox.columns(rows_s, interval="5m", symbol="SYNTH",
                                      basket=synthetic_basket(rows_s) if basketed else None)
        meta.append({"label": f"synthetic {s.replace('_', ' ')}", "bars": 600, "used": True})
    rows_s = synthetic("walk", 8)
    datasets["short"] = sandbox.columns(rows_s, interval="5m", symbol="SYNTH",
                                        basket=synthetic_basket(rows_s) if basketed else None)
    meta.append({"label": "synthetic 8-bar listing", "bars": 8, "used": True})

    jobs: list[dict] = []
    for k in datasets:
        jobs.append({"id": f"full:{k}", "data": k, "params": defaults, "repeat": 2})
    # look-ahead: re-run on every prefix cut and require the past to be
    # unchanged. This is Pine's own definition of repainting — a value on a
    # historical bar that differs from what that bar showed in real time.
    causal_on = [k for k, _, _ in real_keys[:2]] + ["range", "shock"]
    cuts: dict[str, list[int]] = {}
    for k in causal_on:
        n = len(datasets[k]["close"])
        cuts[k] = sorted({max(10, int(n * f)) for f in (0.35, 0.5, 0.62, 0.75, 0.9)} | {n - 1})
        for c in cuts[k]:
            jobs.append({"id": f"cut:{k}:{c}", "data": k, "params": defaults, "truncate": c})
    # every input across its range, one at a time, on real data if any
    pk = real_keys[0][0] if real_keys else "range"
    variants: list[tuple[str, dict]] = []
    for f in spec.get("inputs") or []:
        vals = []
        if f["type"] in ("int", "float"):
            vals = [f["min"], f["max"]]
        elif f["type"] == "enum":
            vals = [o for o in f["options"] if o != f["default"]]
        elif f["type"] == "bool":
            vals = [not f["default"]]
        elif f["type"] == "source":
            vals = [s for s in native.SOURCES if s not in ("volume", f["default"])][:3]
        for v in vals:
            variants.append((f"{f['key']}={v}", {**defaults, f["key"]: v}))
    for i, (lab, prm) in enumerate(variants[:24]):
        jobs.append({"id": f"param:{i}", "data": pk, "params": prm, "label": lab})

    say(f"Running {len(jobs)} sandboxed runs over {len(datasets)} datasets")
    t0 = time.perf_counter()
    try:
        reply = sandbox.run(code, jobs, datasets=datasets, timeout=60.0)
    except sandbox.SandboxError as exc:
        add("sandbox", "Runs inside the sandbox", "fail", str(exc))
        return _report(checks, meta)
    if not reply.get("ok"):
        add("load", "Code loads in the sandbox", "fail", str(reply.get("error")))
        return _report(checks, meta)
    res = {r["id"]: r for r in reply["results"]}
    wall = time.perf_counter() - t0

    # contract — every dataset, the declared lines and nothing else
    errs = []
    for k in datasets:
        r = res.get(f"full:{k}") or {}
        if not r.get("ok"):
            errs.append(f"{k}: {r.get('error')}" + (f" (line {r['line']})" if r.get("line") else ""))
            continue
        got = set(r["lines"])
        if got != set(declared):
            errs.append(f"{k}: returned lines {sorted(got)} but the spec declares {declared}")
    add("contract", "Returns every declared line, one value per bar, on every dataset",
        "fail" if errs else "pass", "; ".join(errs[:5]))
    full = {k: res[f"full:{k}"]["lines"] for k in datasets
            if res.get(f"full:{k}", {}).get("ok") and set(res[f"full:{k}"]["lines"]) == set(declared)}

    # finite
    bad = []
    for k, lines in full.items():
        for ln, v in lines.items():
            nb = sum(1 for x in v if isinstance(x, str))
            if nb:
                first = next(i for i, x in enumerate(v) if isinstance(x, str))
                bad.append(f"{ln} on {k}: {nb} NaN/inf, first at bar {first}")
    add("finite", "No NaN or infinite values (incl. flat prices and zero volume)",
        "fail" if bad else ("pass" if full else "skip"), "; ".join(bad[:5]))

    # a state line carries a state, not a magnitude: +1 / 0 / -1
    if states:
        odd = []
        for k, lines in full.items():
            for ln in states:
                xs = {x for x in lines.get(ln, []) if _ok_val(x)} - {1.0, 0.0, -1.0}
                if xs:
                    odd.append(f"'{ln}' on {k} has {sorted(xs)[:3]}")
        add("state", "State lines hold only +1, 0 or -1",
            "fail" if odd else "pass", "; ".join(odd[:3]))

    # determinism
    nd = [k for k in datasets if res.get(f"full:{k}", {}).get("deterministic") is False]
    add("deterministic", "Same bars in, same values out",
        "fail" if nd else "pass", f"differs between runs on {', '.join(nd)}" if nd else "")

    # causal
    leaks = []
    for k, cs in cuts.items():
        if k not in full:
            continue
        for c in cs:
            r = res.get(f"cut:{k}:{c}") or {}
            if not r.get("ok"):
                leaks.append(f"{k} cut at {c}: {r.get('error')}")
                continue
            for ln in declared:
                a, b = r["lines"].get(ln) or [], full[k][ln][:c]
                for i, (x, y) in enumerate(zip(a, b)):
                    same = (x == y) or (_ok_val(x) and _ok_val(y)
                                        and abs(x - y) <= 1e-9 * max(1.0, abs(y)))
                    if not same:
                        leaks.append(f"'{ln}' at bar {i} reads {x} with {c} bars loaded but "
                                     f"{y} once later bars exist ({c - i} bars from the edge)")
                        break
                else:
                    continue
                break
    add("causal", "No look-ahead: a bar's value never changes when later bars arrive",
        "fail" if leaks else ("pass" if cuts else "skip"), "; ".join(leaks[:4]))

    # coverage after warm-up, on real data
    empty = []
    for k, label, rows in real_keys:
        lines = full.get(k)
        if not lines:
            continue
        half = len(rows) // 2
        for ln in declared:
            if ln in sparse:
                continue
            if not any(_ok_val(x) for x in lines[ln][half:]):
                empty.append(f"'{ln}' has no values in the second half of {label}")
    add("coverage", "Produces values after its warm-up on real bars",
        "fail" if empty else ("pass" if real_keys else "skip"),
        "; ".join(empty[:4]) if empty else ("" if real_keys else "no real bars were available"))

    # params
    perr = []
    for i, (lab, _) in enumerate(variants[:24]):
        r = res.get(f"param:{i}") or {}
        if not r.get("ok"):
            perr.append(f"{lab}: {r.get('error')}")
        elif any(isinstance(x, str) for v in r["lines"].values() for x in v):
            perr.append(f"{lab}: produced NaN/inf")
    add("params", f"Every input across its range ({len(variants[:24])} variants)",
        "fail" if perr else ("pass" if variants else "skip"), "; ".join(perr[:4]))

    # edge
    eerr = [f"{k}: {res[f'full:{k}'].get('error')}" for k in ("short", "flat", "shock", "zero_volume")
            if k in datasets and not res.get(f"full:{k}", {}).get("ok")]
    add("edge", "Survives a short listing, a flat tape, a 25% gap"
        + ("" if needs_volume else " and zero volume"),
        "fail" if eerr else "pass", "; ".join(eerr))

    # bounds
    if spec.get("bounds"):
        lo, hi = spec["bounds"]
        tol = 1e-6 * max(1.0, abs(hi - lo))
        out_b = []
        for k, lines in full.items():
            for ln, v in lines.items():
                if ln in states:
                    continue        # a ribbon on its own scale, not on this axis
                vals = [x for x in v if _ok_val(x)]
                if vals and (min(vals) < lo - tol or max(vals) > hi + tol):
                    out_b.append(f"{ln} on {k} spans {min(vals):.4g}..{max(vals):.4g}")
        add("bounds", f"Stays inside its declared range [{lo}, {hi}]",
            "fail" if out_b else "pass", "; ".join(out_b[:4]))

    # overlay scale — an overlay drawn far off the candles is a pane study
    if spec["pane"] == "overlay" and real_keys:
        k, label, rows = real_keys[0]
        lo_p, hi_p = min(r[3] for r in rows), max(r[2] for r in rows)
        off = []
        for ln, v in (full.get(k) or {}).items():
            if ln in states:
                continue
            vals = [x for x in v if _ok_val(x)]
            if vals and (max(vals) > hi_p * 3 or min(vals) < lo_p / 3):
                off.append(f"{ln} spans {min(vals):.4g}..{max(vals):.4g} vs price "
                           f"{lo_p:.4g}..{hi_p:.4g}")
        add("scale", "Overlay lines sit on the price scale",
            "fail" if off else "pass",
            "; ".join(off) + (" — use pane 'own' for an oscillator" if off else ""))

    # one pane is one scale: a price line beside a volume line is drawn as a
    # flat line on an axis it does not belong to
    if spec["pane"] == "own" and real_keys and len(declared) > 1:
        k = real_keys[0][0]
        mags = {}
        for ln, v in (full.get(k) or {}).items():
            vals = sorted(abs(x) for x in v if _ok_val(x) and x != 0)
            if vals and ln not in sparse and ln not in states:
                mags[ln] = vals[len(vals) // 2]
        if len(mags) > 1 and max(mags.values()) / max(min(mags.values()), 1e-12) > 50:
            big = max(mags, key=mags.get)
            small = min(mags, key=mags.get)
            add("one_scale", "Lines in one pane share a scale", "fail",
                f"'{big}' is typically {mags[big]:.4g} while '{small}' is {mags[small]:.4g} — "
                f"in one pane the smaller line is drawn flat. Drop the line that repeats "
                f"what the chart already shows (price is the candles), or normalise it — "
                f"and a +1/0/-1 condition is plot 'state' (a coloured ribbon on its own scale)")
        else:
            add("one_scale", "Lines in one pane share a scale", "pass")

    # reference implementation
    ref = spec.get("reference")
    if ref and real_keys:
        k, label, rows = real_keys[0]
        try:
            ref_lines, what = _reference_lines(ref, rows, defaults)
            rep = []
            worst = 0.0
            for ours, theirs in ref_lines.items():
                cmp = _compare(full.get(k, {}).get(ours, []), theirs,
                               warm=max(50, len(rows) // 5))
                if "error" in cmp:
                    rep.append(f"{ours}: {cmp['error']}")
                    worst = max(worst, 1.0)
                    continue
                worst = max(worst, cmp["max_rel_error"])
                rep.append(f"{ours}: agrees on {cmp['agree_pct']}% of {cmp['compared']} bars, max "
                           f"error {cmp['max_rel_error']:.2e} of range (worst bar {cmp['worst']['index']}: "
                           f"ours {cmp['worst']['ours']} vs {cmp['worst']['reference']})")
            status = "pass" if worst <= 1e-3 else "fail"
            grade = "identical" if worst <= 1e-6 else "agrees" if worst <= 1e-3 else "disagrees"
            add("reference", f"Matches {what} on {label} ({grade})", status, "; ".join(rep))
        except Exception as exc:  # noqa: BLE001 — a reference that cannot run is a finding
            add("reference", "Matches a reference implementation", "fail",
                f"the reference could not be computed: {exc}")
    elif str(spec.get("reference_waiver") or "").strip():
        add("reference", "Not compared with a reference implementation", "warn",
            f"waived: {spec['reference_waiver']}", blocking=False)
    elif spec.get("classification") == "standard":
        add("reference", "Matches a reference implementation", "warn",
            "no reference implementation available here; the formula was checked "
            "against the cited sources only", blocking=False)

    # speed
    ms = max((r.get("ms") or 0) for r in res.values() if r.get("ok")) if res else 0
    add("speed", "Fast enough to redraw on every bar",
        "fail" if ms > 3000 else "warn" if ms > 800 else "pass",
        f"slowest run {ms:.0f} ms; all {len(jobs)} runs in {wall:.1f}s",
        blocking=ms > 3000)
    return _report(checks, meta)


def _report(checks: list[dict], meta: list[dict]) -> dict:
    blocking_fail = [c for c in checks if c["status"] == "fail" and c["blocking"]]
    passed = not blocking_fail
    n_pass = sum(1 for c in checks if c["status"] == "pass")
    return {"passed": passed, "checks": checks, "datasets": meta,
            "summary": (f"{n_pass} of {len(checks)} checks passed"
                        + ("" if passed else
                           f"; failing: {', '.join(c['id'] for c in blocking_fail)}"))}


# ══════════════════════════════════════════════════════════════════
# THE STORE
# ══════════════════════════════════════════════════════════════════

def _row(r) -> dict:
    return {"id": r["id"], "user_id": r["user_id"], "version": r["version"],
            "status": r["status"], "prompt": r["prompt"],
            "spec": json.loads(r["spec"]), "code": r["code"],
            "report": json.loads(r["report"]), "history": json.loads(r["history"]),
            "created": r["created"], "updated": r["updated"]}


def get(cid: str, uid: int | None = None) -> dict | None:
    if not _ID_RE.match(cid or ""):
        return None
    with _lock:
        r = _db.execute("SELECT * FROM custom_indicators WHERE id=?", (cid,)).fetchone()
    if not r or r["status"] == "deleted":
        return None
    if uid is not None and r["user_id"] != uid:
        return None
    return _row(r)


def list_for(uid: int, include_failed: bool = False) -> list[dict]:
    q = ("SELECT * FROM custom_indicators WHERE user_id=? AND status IN (%s) "
         "ORDER BY updated DESC" % ("'validated','failed'" if include_failed else "'validated'"))
    with _lock:
        return [_row(r) for r in _db.execute(q, (uid,)).fetchall()]


def save(uid: int, *, spec: dict, code: str, report: dict, prompt: str,
         cid: str | None = None) -> dict:
    """Store a build. A NEW id for a first build; a new VERSION of `cid` for an
    edit — the chart keeps its id, so a study on screen updates in place.
    A failed edit never replaces a validated version: it is kept beside it as
    the latest attempt, and the chart keeps drawing the version that passed."""
    now = int(time.time())
    status = "validated" if report.get("passed") else "failed"
    with _lock:
        if cid:
            r = _db.execute("SELECT * FROM custom_indicators WHERE id=? AND user_id=?",
                            (cid, uid)).fetchone()
            if not r:
                raise KeyError(cid)
            hist = json.loads(r["history"])
            attempt = {"version": r["version"] + (1 if status == "validated" else 0),
                       "status": status, "prompt": prompt, "spec": spec, "code": code,
                       "summary": report.get("summary"), "at": now}
            if status != "validated" and r["status"] == "validated":
                hist.append({**attempt, "report": report, "kept_previous": True})
                _db.execute("UPDATE custom_indicators SET history=?, updated=? WHERE id=?",
                            (json.dumps(hist[-20:]), now, cid))
                _db.commit()
                return {**_row(r), "history": hist[-20:], "last_attempt_failed": True,
                        "attempt_report": report}
            hist.append({"version": r["version"], "status": r["status"],
                         "prompt": r["prompt"], "spec": json.loads(r["spec"]),
                         "code": r["code"], "summary": json.loads(r["report"]).get("summary"),
                         "at": r["updated"]})
            _db.execute("UPDATE custom_indicators SET version=?, status=?, prompt=?, spec=?, "
                        "code=?, report=?, history=?, updated=? WHERE id=?",
                        (r["version"] + 1, status, prompt, json.dumps(spec), code,
                         json.dumps(report), json.dumps(hist[-20:]), now, cid))
        else:
            cid = new_id()
            _db.execute("INSERT INTO custom_indicators (id, user_id, version, status, prompt, "
                        "spec, code, report, history, created, updated) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (cid, uid, 1, status, prompt, json.dumps(spec), code,
                         json.dumps(report), "[]", now, now))
        _db.commit()
        r = _db.execute("SELECT * FROM custom_indicators WHERE id=?", (cid,)).fetchone()
    return _row(r)


def delete(uid: int, cid: str) -> bool:
    with _lock:
        cur = _db.execute("UPDATE custom_indicators SET status='deleted', updated=? "
                          "WHERE id=? AND user_id=? AND status!='deleted'",
                          (int(time.time()), cid, uid))
        _db.commit()
    return cur.rowcount > 0


def compute_for(rec: dict, rows: list[tuple], raw_params: dict, *,
                interval: str = "", tz_offset: int = 0, symbol: str = "",
                basket: dict | None = None) -> dict:
    """The /indicator path for a custom study: the same {lines, last, spec}
    shape indicators.compute() returns, so every caller treats them alike."""
    if rec["status"] != "validated":
        raise ValueError(f"{rec['spec'].get('title', rec['id'])} has not passed validation "
                         f"and cannot be drawn")
    spec = rec["spec"]
    params = coerce_params(spec, raw_params or {})
    if spec.get("needs_volume") and not any(r[5] for r in rows):
        raise ValueError(f"{spec['title']} needs traded volume and this instrument prints "
                         f"none — every bar has v=0, as indices are quoted.")
    if spec.get("basket") and basket is None:
        raise ValueError(f"{spec['title']} reads a basket of instruments and none was loaded")
    lines, bad = sandbox.compute(rec["code"], rows, params, interval=interval,
                                 tz_offset=tz_offset, symbol=symbol,
                                 basket=basket if spec.get("basket") else None)
    lines = {ln["key"]: lines.get(ln["key"], [None] * len(rows)) for ln in spec["lines"]}

    def last(v):
        x = next((y for y in reversed(v) if y is not None), None)
        return None if x is None else round(x, 4)
    out = {"lines": lines, "last": {k: last(v) for k, v in lines.items()},
           "spec": {"name": rec["id"], "period": 0, "pane": spec["pane"], "group": "custom",
                    "formula": spec["formula"], "title": spec["title"],
                    "classification": spec["classification"], "version": rec["version"],
                    **({"bounds": list(spec["bounds"])} if spec.get("bounds") else {}),
                    **params}}
    if bad:
        out["nonfinite"] = bad
    return out
