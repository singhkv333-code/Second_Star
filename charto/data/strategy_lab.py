"""The strategy lab: what the Strategy widget runs and reads.

Three ways in, one engine underneath. A backtest is asked for as

  - a TEMPLATE (moving-average cross, RSI reversion, breakout, Supertrend,
    MACD, Bollinger reversion) with its parameters,
  - a SAVED strategy of the user's, by id, run on the draft it was armed from,
  - or a raw draft's `steps[]`, the shape the chat's draft card holds,

and every one of them becomes the same `steps[]` and goes through the same
`execution_bridge` call the chat's Backtest button makes, so the widget, the
card and the model can never show two numbers for one rule.

`report()` then derives what a strategy tester shows beside the engine's own
metrics: profit factor, expectancy, payoff, streaks, holding time, the
drawdown series and a month-by-year return grid. All of it is arithmetic on
the engine's trades and equity curve, computed here and nowhere else: the
widget arranges numbers, it does not make them (model reads, code computes).
"""
from __future__ import annotations

import datetime as _dt
import json
import math
from typing import Any, Optional

# ── the templates ───────────────────────────────────────────────────────────
#
# Each is a pair of DSL trees (entry, exit) over one symbol and timeframe.
# Parameters are bounded here, not trusted from the client: a period of 0 or
# a fast average slower than the slow one is refused with the reason.

PARAM = dict  # {key, label, def, min, max, step}

TEMPLATES: list[dict] = [
    {"id": "ma_cross", "name": "Moving-average cross", "family": "Trend",
     "blurb": "Long when the fast average crosses above the slow one; out when it crosses back.",
     "params": [
         {"key": "fast", "label": "Fast", "def": 20, "min": 2, "max": 200, "step": 1},
         {"key": "slow", "label": "Slow", "def": 50, "min": 5, "max": 400, "step": 1},
         {"key": "kind", "label": "Average", "def": "ema", "options": ["ema", "sma"]},
     ]},
    {"id": "rsi_revert", "name": "RSI reversion", "family": "Mean reversion",
     "blurb": "Long when RSI falls below the floor; out when it rises above the ceiling.",
     "params": [
         {"key": "period", "label": "RSI length", "def": 14, "min": 2, "max": 100, "step": 1},
         {"key": "low", "label": "Buy below", "def": 30, "min": 5, "max": 50, "step": 1},
         {"key": "high", "label": "Sell above", "def": 70, "min": 50, "max": 95, "step": 1},
     ]},
    {"id": "breakout", "name": "Channel breakout", "family": "Breakout",
     "blurb": "Long on a close above the prior N-bar high; out on a close below the prior M-bar low.",
     "params": [
         {"key": "entry", "label": "High of", "def": 20, "min": 5, "max": 250, "step": 1},
         {"key": "exit", "label": "Low of", "def": 10, "min": 3, "max": 250, "step": 1},
     ]},
    {"id": "supertrend", "name": "Supertrend", "family": "Trend",
     "blurb": "Long when price closes above the Supertrend line; out when it closes below.",
     "params": [
         {"key": "period", "label": "ATR length", "def": 10, "min": 3, "max": 100, "step": 1},
         {"key": "mult", "label": "Multiplier", "def": 3, "min": 0.5, "max": 10, "step": 0.5},
     ]},
    {"id": "macd", "name": "MACD cross", "family": "Momentum",
     "blurb": "Long when the MACD line crosses above its signal; out when it crosses below.",
     "params": [
         {"key": "fast", "label": "Fast", "def": 12, "min": 2, "max": 100, "step": 1},
         {"key": "slow", "label": "Slow", "def": 26, "min": 5, "max": 200, "step": 1},
         {"key": "signal", "label": "Signal", "def": 9, "min": 2, "max": 50, "step": 1},
     ]},
    {"id": "bb_revert", "name": "Bollinger reversion", "family": "Mean reversion",
     "blurb": "Long on a close below the lower band; out on a close back above the middle.",
     "params": [
         {"key": "period", "label": "Length", "def": 20, "min": 5, "max": 200, "step": 1},
         {"key": "std", "label": "Deviations", "def": 2, "min": 0.5, "max": 4, "step": 0.25},
     ]},
]

_BY_ID = {t["id"]: t for t in TEMPLATES}

# What the widget offers. Intraday windows are short because the minute store
# is; the engine itself says so (interval_notes) when a window is clipped.
INTERVALS = ["1d", "1h", "15m"]
PERIODS = {"1d": ["1y", "3y", "5y", "10y"], "1h": ["3m", "6m", "1y"], "15m": ["1m", "2m"]}
_PERIOD_DAYS = {"1m": 31, "2m": 61, "3m": 92, "6m": 183, "1y": 365, "3y": 1096, "5y": 1826, "10y": 3652}


class LabError(ValueError):
    """A request the lab will not run, with the reason in words."""


def catalog() -> dict:
    return {"templates": TEMPLATES, "intervals": INTERVALS, "periods": PERIODS}


def _params(tpl: dict, given: dict) -> dict:
    out = {}
    for p in tpl["params"]:
        v = (given or {}).get(p["key"], p["def"])
        if "options" in p:
            v = str(v)
            if v not in p["options"]:
                raise LabError(f"{p['label']} must be one of {', '.join(p['options'])}.")
        else:
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise LabError(f"{p['label']} must be a number.")
            if not (p["min"] <= v <= p["max"]):
                raise LabError(f"{p['label']} must be between {p['min']} and {p['max']}.")
            if float(p["step"]).is_integer():
                v = int(round(v))
        out[p["key"]] = v
    return out


def _ind(sym, tf, name, period, component=None, settings=None, offset=0):
    n = {"type": "indicator", "indicator": name, "symbol": sym, "period": int(period), "timeframe": tf}
    if component:
        n["component"] = component
    if settings:
        n["settings"] = settings
    if offset:
        n["offset"] = int(offset)
    return n


def _px(sym, tf, basis="close", offset=0):
    n = {"type": "price", "symbol": sym, "basis": basis, "timeframe": tf}
    if offset:
        n["offset"] = int(offset)
    return n


def _cmp(op, left, right):
    if isinstance(right, (int, float)):
        right = {"type": "constant", "value": right}
    return {"type": "comparison", "op": op, "left": left, "right": right}


def trees(template: str, symbol: str, interval: str, params: dict) -> tuple[dict, dict, dict]:
    """(entry, exit, resolved params) for one template."""
    tpl = _BY_ID.get(template)
    if not tpl:
        raise LabError(f"No template called '{template}'.")
    p = _params(tpl, params)
    s, tf = symbol, interval
    if template == "ma_cross":
        if p["fast"] >= p["slow"]:
            raise LabError("The fast average has to be shorter than the slow one.")
        f, sl = _ind(s, tf, p["kind"], p["fast"]), _ind(s, tf, p["kind"], p["slow"])
        return _cmp("crosses_above", f, sl), _cmp("crosses_below", f, sl), p
    if template == "rsi_revert":
        if p["low"] >= p["high"]:
            raise LabError("The buy level has to be below the sell level.")
        r = _ind(s, tf, "rsi", p["period"])
        return _cmp("<", r, p["low"]), _cmp(">", r, p["high"]), p
    if template == "breakout":
        hi = {"type": "aggregate", "op": "highest", "bars": p["entry"], "source": _px(s, tf, "high", 1)}
        lo = {"type": "aggregate", "op": "lowest", "bars": p["exit"], "source": _px(s, tf, "low", 1)}
        return _cmp("crosses_above", _px(s, tf), hi), _cmp("crosses_below", _px(s, tf), lo), p
    if template == "supertrend":
        # The engine's supertrend is the trend DIRECTION (+1 up, -1 down), not
        # the line, so "closes above the line" is the direction turning up.
        st = _ind(s, tf, "supertrend", p["period"], settings={"multiplier": float(p["mult"])})
        return _cmp("crosses_above", st, 0), _cmp("crosses_below", st, 0), p
    if template == "macd":
        if p["fast"] >= p["slow"]:
            raise LabError("The fast length has to be shorter than the slow one.")
        cfg = {"fast": p["fast"], "slow": p["slow"], "signal": p["signal"]}
        line = _ind(s, tf, "macd", p["slow"], component="macd", settings=cfg)
        sig = _ind(s, tf, "macd", p["slow"], component="signal", settings=cfg)
        return _cmp("crosses_above", line, sig), _cmp("crosses_below", line, sig), p
    if template == "bb_revert":
        cfg = {"std": float(p["std"])}
        lower = _ind(s, tf, "bb", p["period"], component="lower", settings=cfg)
        mid = _ind(s, tf, "bb", p["period"], component="middle", settings=cfg)
        return _cmp("<", _px(s, tf), lower), _cmp(">", _px(s, tf), mid), p
    raise LabError(f"No template called '{template}'.")


def steps(symbol: str, entry: dict, exit_tree: Optional[dict], quantity: int = 1) -> list[dict]:
    """A draft's steps[], exactly as the chat's draft card holds one."""
    out = [{"step_type": "trigger.compound", "config": {"symbol": symbol, "entry": entry}}]
    if exit_tree:
        out.append({"step_type": "trigger.exit_compound", "config": {"target_symbol": symbol, "entry": exit_tree}})
    out.append({"step_type": "action.place_order",
                "config": {"symbol": symbol, "side": "buy", "quantity": max(1, int(quantity))}})
    return out


def _start_date(period: str, today: Optional[_dt.date] = None) -> str:
    days = _PERIOD_DAYS.get(str(period or ""))
    if not days:
        raise LabError(f"Period must be one of {', '.join(_PERIOD_DAYS)}.")
    return ((today or _dt.date.today()) - _dt.timedelta(days=days)).isoformat()


def resolve(body: dict, *, saved_draft=None) -> dict:
    """The backtest's arguments, and the draft that was tested.

    `saved_draft(id) -> dict` looks a saved strategy up for the signed-in
    user; it is passed in so this module never touches the user database.
    """
    body = body or {}
    interval = str(body.get("interval") or "1d")
    if interval not in INTERVALS:
        raise LabError(f"Interval must be one of {', '.join(INTERVALS)}.")
    period = str(body.get("period") or PERIODS[interval][0])
    if period not in PERIODS[interval]:
        raise LabError(f"A {interval} test runs over {', '.join(PERIODS[interval])}.")
    capital = body.get("capital", 100000)
    try:
        capital = float(capital)
    except (TypeError, ValueError):
        raise LabError("Capital must be a number.")
    if not (1000 <= capital <= 1e10):
        raise LabError("Capital must be between ₹1,000 and ₹1,000 crore.")

    source: dict[str, Any]
    if body.get("template"):
        symbol = str(body.get("symbol") or "").strip().upper()
        if not symbol:
            raise LabError("Pick a symbol to test on.")
        entry, exit_tree, p = trees(str(body["template"]), symbol, interval, body.get("params") or {})
        tpl = _BY_ID[str(body["template"])]
        name = f"{tpl['name']} · {symbol}"
        draft = {"name": name, "steps": steps(symbol, entry, exit_tree, int(body.get("quantity") or 1))}
        source = {"kind": "template", "template": tpl["id"], "params": p}
    elif body.get("strategy_id") is not None:
        if saved_draft is None:
            raise LabError("Sign in to test your saved strategies.")
        draft = saved_draft(int(body["strategy_id"]))
        if not draft or not draft.get("steps"):
            raise LabError("That strategy has no draft to test.")
        name = str(draft.get("name") or "Saved strategy")
        source = {"kind": "saved", "strategy_id": int(body["strategy_id"])}
    elif isinstance(body.get("steps"), list) and body["steps"]:
        draft = {"name": str(body.get("name") or "Draft"), "steps": body["steps"]}
        name = draft["name"]
        source = {"kind": "draft"}
    else:
        raise LabError("Say what to test: a template, a saved strategy or a draft.")

    args = {"steps": draft["steps"], "name": name, "interval": interval,
            "start_date": _start_date(period), "starting_capital": capital}
    return {"args": args, "draft": draft, "source": source, "period": period}


# ── the report ──────────────────────────────────────────────────────────────

def _f(x) -> Optional[float]:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _day(s) -> Optional[_dt.datetime]:
    if not s:
        return None
    try:
        return _dt.datetime.fromisoformat(str(s).replace("Z", "")[:19])
    except ValueError:
        return None


def _thin(n: int, cap: int) -> list[int]:
    """Indices that keep at most `cap` points, always the first and last."""
    if n <= cap:
        return list(range(n))
    step = (n - 1) / (cap - 1)
    idx = sorted({int(round(i * step)) for i in range(cap)})
    if idx[-1] != n - 1:
        idx.append(n - 1)
    return idx



# NSE's session is 09:15-15:30: 7 hourly bars (the last one short), 25 of 15m.
_BARS_PER_YEAR = {"1d": 252, "1h": 252 * 7, "15m": 252 * 25}
# rolling-volatility window: about a month of days, a week of hours, a day of 15m
_VOL_WINDOW = {"1d": 20, "1h": 35, "15m": 25}


def _std(xs: list[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _rolling_vol(vals: list[float], window: int, per_year: int) -> list[Optional[float]]:
    """Annualised %, of bar-to-bar returns over the trailing window; None until full."""
    rets = [None] + [(b / a - 1) if a and a > 0 else None for a, b in zip(vals, vals[1:])]
    out: list[Optional[float]] = []
    for i in range(len(vals)):
        win = [r for r in rets[max(1, i - window + 1): i + 1] if r is not None]
        sd = _std(win) if i >= window else None
        out.append(None if sd is None else sd * math.sqrt(per_year) * 100)
    return out


def _trend(vals: list[float], per_year: int) -> Optional[dict]:
    """Least-squares line through log equity: its fitted path, annual pace and R^2."""
    pts = [(i, math.log(v)) for i, v in enumerate(vals) if v and v > 0]
    if len(pts) < 3:
        return None
    n = len(pts)
    mx = sum(i for i, _ in pts) / n
    my = sum(y for _, y in pts) / n
    sxx = sum((i - mx) ** 2 for i, _ in pts)
    if sxx <= 0:
        return None
    b = sum((i - mx) * (y - my) for i, y in pts) / sxx
    a = my - b * mx
    ss_tot = sum((y - my) ** 2 for _, y in pts)
    ss_res = sum((y - (a + b * i)) ** 2 for i, y in pts)
    return {
        "fit": [math.exp(a + b * i) for i in range(len(vals))],
        "pace_pct": (math.exp(b * per_year) - 1) * 100,
        "r2": 1 - ss_res / ss_tot if ss_tot > 0 else None,
    }


def _histogram(xs: list[float], most: int = 12) -> Optional[dict]:
    """Trade returns in equal, round-width bins (in %)."""
    if not xs:
        return None
    lo, hi = min(xs), max(xs)
    span = max(hi - lo, 1e-9)
    raw = span / min(most, max(4, len(xs)))
    mag = 10 ** math.floor(math.log10(raw))
    width = next(k * mag for k in (1, 2, 2.5, 5, 10) if k * mag >= raw)
    start = math.floor(lo / width) * width
    nb = max(1, int(math.ceil((hi - start) / width + 1e-9)))
    if start + nb * width <= hi:
        nb += 1
    counts = [0] * nb
    for x in xs:
        counts[min(nb - 1, int((x - start) // width))] += 1
    return {"width": round(width, 6),
            "bins": [{"lo": round(start + k * width, 4), "hi": round(start + (k + 1) * width, 4), "n": c}
                     for k, c in enumerate(counts)]}


def _key(t) -> str:
    s = str(t).replace(" ", "T")
    return s[:10] if len(s) <= 10 or s[10:19] == "T00:00:00" else s[:16]


def hold_curve(res: dict, closes: list[tuple[Any, float]]) -> Optional[list]:
    """Buy & hold on the same bars: capital x close / first close, on the equity's clock.

    The DSL engine's `price_curve` is a copy of its equity curve (it fills a
    thumbnail slot), so it is not a benchmark. This one is built from the bars
    the engine ran on, and it is kept only when its end agrees with the
    engine's own buy & hold return; a line that disagrees with the number
    printed beside it is worse than no line.
    """
    eq = [p for p in (res.get("equity_curve") or []) if isinstance(p, dict) and p.get("t")]
    cl = [(t, float(c)) for t, c in closes if c is not None and c > 0]
    cap0 = _f(eq[0].get("v")) if eq else None
    if not eq or not cl or not cap0:
        return None
    stamps = [str(p["t"]) for p in eq]
    if len(set(stamps)) == len(stamps):
        # one stamp per bar: match on it
        px = {_key(t): c for t, c in cl}
        hit = [px.get(_key(t)) for t in stamps]
    else:
        # the engine stamps intraday equity by DATE only, so several bars share
        # one; align by position instead, on the tail the curve covers, and
        # only when both ends fall on the same days
        if len(cl) < len(eq):
            return None
        tail = cl[-len(eq):]
        if str(tail[0][0])[:10] != stamps[0][:10] or str(tail[-1][0])[:10] != stamps[-1][:10]:
            return None
        hit = [c for _, c in tail]
    first = next((c for c in hit if c), None)
    if not first or sum(1 for c in hit if c) < len(eq) * 0.9:
        return None
    out = [None if not c else cap0 * c / first for c in hit]
    bench = _f((res.get("metrics") or {}).get("benchmark_return_pct"))
    last = out[-1]
    end_pct = (last / cap0 - 1) * 100 if last else None
    if bench is not None and (end_pct is None or abs(end_pct - bench) > max(1.5, abs(bench) * 0.05)):
        return None
    return out


def closes(res: dict) -> list[tuple[Any, float]]:
    """The closes of the bars the engine just ran on, from its memo; [] if gone."""
    import execution_bridge
    if not res.get("symbol"):
        return []
    df = execution_bridge.cached_bars(str(res["symbol"]), str(res.get("interval") or "1d"))
    if df is None or "close" not in getattr(df, "columns", []):
        return []
    return [(ts.isoformat(), float(c)) for ts, c in zip(df.index, df["close"])]


def report(res: dict, *, cap: int = 600, hold: Optional[list] = None) -> dict:
    """What a tester shows beside the engine's metrics, from its own series.

    `hold` is hold_curve()'s output (one value per equity point); without it
    there is no buy & hold line.
    """
    trades = [t for t in (res.get("trades") or []) if isinstance(t, dict)]
    pnl = [_f(t.get("net_pnl")) for t in trades]
    pnl = [x for x in pnl if x is not None]
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x <= 0]
    gross_w, gross_l = sum(wins), -sum(losses)
    rets = [_f(t.get("return_pct")) for t in trades]
    rets = [x * 100 for x in rets if x is not None]

    streak_w = streak_l = cur_w = cur_l = 0
    for x in pnl:
        if x > 0:
            cur_w += 1; cur_l = 0
        else:
            cur_l += 1; cur_w = 0
        streak_w, streak_l = max(streak_w, cur_w), max(streak_l, cur_l)

    held = []
    for t in trades:
        a, b = _day(t.get("entry_date")), _day(t.get("exit_date"))
        if a and b and b >= a:
            held.append((b - a).total_seconds() / 86400)

    out: dict[str, Any] = {
        "trades": len(trades),
        "wins": len(wins), "losses": len(losses),
        "gross_profit": round(gross_w, 2), "gross_loss": round(gross_l, 2),
        # no losing trade: the factor is undefined, not infinite
        "profit_factor": round(gross_w / gross_l, 2) if gross_l > 0 else None,
        "expectancy": round(sum(pnl) / len(pnl), 2) if pnl else None,
        "avg_win": round(gross_w / len(wins), 2) if wins else None,
        "avg_loss": round(-gross_l / len(losses), 2) if losses else None,
        "payoff": round((gross_w / len(wins)) / (gross_l / len(losses)), 2) if wins and losses and gross_l > 0 else None,
        "best_trade_pct": round(max(rets), 2) if rets else None,
        "worst_trade_pct": round(min(rets), 2) if rets else None,
        "max_win_streak": streak_w, "max_loss_streak": streak_l,
        "avg_hold_days": round(sum(held) / len(held), 1) if held else None,
        "open_at_end": sum(1 for t in trades if t.get("exit_reason") == "force_close"),
        # trade by trade, in order (the last 300), and how they spread
        "trade_returns": [round(x, 3) for x in rets[-300:]],
        "distribution": _histogram(rets),
    }
    iv = str(res.get("interval") or "1d")
    per_year = _BARS_PER_YEAR.get(iv, 252)
    window = _VOL_WINDOW.get(iv, 20)

    raw = [p for p in (res.get("equity_curve") or []) if isinstance(p, dict) and p.get("t")]
    # hold_curve() runs on the same filter, so its list lines up with `raw`
    hold_at = hold if hold and len(hold) == len(raw) else [None] * len(raw)
    pts = [(p["t"], _f(p.get("v")), h) for p, h in zip(raw, hold_at)]
    pts = [x for x in pts if x[1] is not None]
    eq = [(t, v) for t, v, _ in pts]
    hold_vals = [h for _, _, h in pts]
    if eq:
        peak = eq[0][1]
        dd = []
        worst, worst_at, run, longest = 0.0, None, 0, 0
        start_under = None
        for i, (t, v) in enumerate(eq):
            peak = max(peak, v)
            d = (v / peak - 1) * 100 if peak > 0 else 0.0
            dd.append(d)
            if d < worst:
                worst, worst_at = d, t
            if d < -1e-9:
                start_under = start_under if start_under is not None else i
                run = i - start_under + 1
                longest = max(longest, run)
            else:
                start_under, run = None, 0
        vals = [v for _, v in eq]
        vol = _rolling_vol(vals, window, per_year)
        hold_ok = all(v is not None and v > 0 for v in hold_vals)
        hold_vol = _rolling_vol(hold_vals, window, per_year) if hold_ok else [None] * len(vals)
        tr = _trend(vals, per_year)
        bar_rets = [b / a - 1 for a, b in zip(vals, vals[1:]) if a > 0]
        hold_rets = [b / a - 1 for a, b in zip(hold_vals, hold_vals[1:]) if hold_ok and a > 0]
        sd, hsd = _std(bar_rets), _std(hold_rets)
        live = [x for x in vol if x is not None]
        out["volatility"] = {
            "window_bars": window,
            "annual_pct": round(sd * math.sqrt(per_year) * 100, 2) if sd is not None else None,
            "hold_annual_pct": round(hsd * math.sqrt(per_year) * 100, 2) if hsd is not None else None,
            "now_pct": round(live[-1], 2) if live else None,
            "peak_pct": round(max(live), 2) if live else None,
        }
        out["trend"] = None if tr is None else {
            "pace_pct": round(tr["pace_pct"], 2),
            "r2": None if tr["r2"] is None else round(tr["r2"], 3),
        }
        keep = _thin(len(eq), cap)
        out["curve"] = {
            "trend": [round(tr["fit"][i], 2) for i in keep] if tr else None,
            "vol": [None if vol[i] is None else round(vol[i], 2) for i in keep],
            "hold_vol": [None if hold_vol[i] is None else round(hold_vol[i], 2) for i in keep],
            "t": [eq[i][0] for i in keep],
            "equity": [round(eq[i][1], 2) for i in keep],
            "hold": [None if hold_vals[i] is None else round(hold_vals[i], 2) for i in keep],
            "drawdown": [round(dd[i], 3) for i in keep],
        }
        out["max_dd_at"] = worst_at
        out["longest_underwater_bars"] = longest

        # month-by-year returns: each month's last equity against the one before
        months: dict[str, float] = {}
        for t, v in eq:
            months[str(t)[:7]] = v
        prev = eq[0][1]
        grid: dict[str, list] = {}
        years: dict[str, float] = {}
        year_open: dict[str, float] = {}
        for ym in sorted(months):
            y, m = ym[:4], int(ym[5:7])
            v = months[ym]
            year_open.setdefault(y, prev)
            grid.setdefault(y, [None] * 12)[m - 1] = round((v / prev - 1) * 100, 2) if prev > 0 else None
            years[y] = round((v / year_open[y] - 1) * 100, 2) if year_open[y] > 0 else None
            prev = v
        out["monthly"] = [{"year": y, "months": grid[y], "total": years.get(y)} for y in sorted(grid)]
    return out


def shape(res: dict, resolved: dict, closes: Optional[list] = None) -> dict:
    """The engine's result, the report beside it, and the draft that was run."""
    out = dict(res)
    out.pop("price_curve", None)    # a copy of equity_curve, not prices: never read it as a benchmark
    out["report"] = report(res, hold=hold_curve(res, closes) if closes else None)
    out["draft"] = resolved["draft"]
    out["source"] = resolved["source"]
    out["period"] = resolved["period"]
    return out
