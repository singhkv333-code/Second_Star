"""Option chains for NSE F&O stocks and indices.

Two sources, one shape:

  nse_eod  NSE's own F&O bhavcopy (UDiFF, one zip a day on nsearchives, the
           same CDN sweep_market.py reads). Every listed option of every
           underlying with its close, settlement, OI, OI change, contracts
           traded, lot size and the underlying's close. It is the official
           record, it needs no login, and it is END OF DAY: the payload says
           "Close, 13 Oct" and nothing pretends it is live.
  kite     When the day's Kite session is up (production pushes
           PIVOT_KITE_ACCESS_TOKEN each morning), the strikes shown are
           re-quoted live in one batched quote() call. The bhavcopy's
           instrument names ARE Kite trading symbols (NIFTY26OCT27050PE,
           weeklies NIFTY26O1920450PE), so no instrument dump is needed, and
           the last EOD file is the baseline for today's OI change.

The underlying, its strikes, expiries and lots always come from the
bhavcopy, so a live chain and an EOD chain list the same instruments.

IV and Greeks are pivot's engine (backend.market.greeks: Black-76 on the
same-expiry forward), not a second derivation. The forward is the same-
expiry future when one is listed (monthlies), else put-call parity at the
money (weeklies), else spot — and the payload says which. An option nobody
traded keeps its price but gets no IV: a theoretical close is not a market.

Units: OI, OI change and volume are in CONTRACTS (lots), the way NSE's own
chain shows them. The bhavcopy carries OI in shares and volume in contracts;
Kite carries both in shares. Both are converted here, once.
"""
from __future__ import annotations

import csv
import io
import logging
import math
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

IST = timezone(timedelta(hours=5, minutes=30))
HERE = Path(__file__).resolve().parent
CACHE = HERE / "fo_bhav"
URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{d}_F_0000.csv.zip"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
RISK_FREE = 0.065

# The chart spells an index the way NSE's index desk does; F&O spells it the
# way the contracts do. Both spellings resolve, and the chart's is shown.
INDEX_UNDERLYING = {
    "NIFTY 50": "NIFTY", "NIFTY BANK": "BANKNIFTY", "NIFTY FIN SERVICE": "FINNIFTY",
    "NIFTY MID SELECT": "MIDCPNIFTY", "NIFTY NEXT 50": "NIFTYNXT50",
}
INDEX_CHART = {v: k for k, v in INDEX_UNDERLYING.items()}
# BSE's index options (SENSEX, BANKEX) trade on BFO, which this file does not
# carry; named so the answer is "on BSE, not covered" rather than "no options".
BSE_INDEX = {"SENSEX", "BSE SENSEX", "BANKEX", "BSE BANKEX"}

_lock = threading.Lock()
_days: dict[str, dict] = {}                 # "YYYYMMDD" -> parsed day
_miss: dict[str, float] = {}                # "YYYYMMDD" -> when it 404'd
_latest: tuple[float, str | None] = (0.0, None)


# ── the bhavcopy ───────────────────────────────────────────────────────────

def _ctx() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:                       # noqa: BLE001 — system store is fine
        return ssl.create_default_context()


def _download(day: date) -> bytes | None:
    """The day's zip, from disk or from NSE. None = no file (a holiday, or
    not published yet); an outage raises, so it is never mistaken for one."""
    key = day.strftime("%Y%m%d")
    path = CACHE / f"fo_{key}.csv.zip"
    if path.exists() and path.stat().st_size > 1000:
        return path.read_bytes()
    req = urllib.request.Request(URL.format(d=key), headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=20, context=_ctx()) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    try:
        CACHE.mkdir(exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(raw)
        tmp.replace(path)
        for old in sorted(CACHE.glob("fo_*.csv.zip"))[:-12]:   # keep a fortnight
            old.unlink(missing_ok=True)
    except OSError:
        pass
    return raw


def _num(v: str) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse(raw: bytes) -> dict:
    """{underlying: {"spot", "lot", "futs": {expiry: close}, "opts": {expiry:
    {strike: {"CE": row, "PE": row}}}}} plus the trade date."""
    z = zipfile.ZipFile(io.BytesIO(raw))
    text = z.read(z.namelist()[0]).decode("utf-8", "replace")
    unds: dict[str, dict] = {}
    trade = ""
    for r in csv.DictReader(io.StringIO(text)):
        tp = r.get("FinInstrmTp")
        if tp not in ("IDO", "STO", "IDF", "STF"):
            continue
        trade = trade or r.get("TradDt", "")
        u = unds.setdefault(r["TckrSymb"], {"spot": 0.0, "lot": 0, "futs": {},
                                            "futsym": {}, "opts": {}, "index": tp[0] == "I"})
        exp = r["XpryDt"]
        lot = int(_num(r.get("NewBrdLotQty")) or 0)
        u["spot"] = u["spot"] or _num(r.get("UndrlygPric"))
        if tp in ("IDF", "STF"):
            u["futs"][exp] = _num(r.get("ClsPric"))
            u["futsym"][exp] = r.get("FinInstrmNm", "")
            continue
        u["lot"] = u["lot"] or lot
        lot = lot or u["lot"] or 1
        side = r.get("OptnTp")
        if side not in ("CE", "PE"):
            continue
        u["opts"].setdefault(exp, {}).setdefault(_num(r["StrkPric"]), {})[side] = {
            "sym": r.get("FinInstrmNm", ""),
            "ltp": _num(r.get("ClsPric")),
            "prev": _num(r.get("PrvsClsgPric")),
            "settle": _num(r.get("SttlmPric")),
            "oi": round(_num(r.get("OpnIntrst")) / lot),
            "oi_chg": round(_num(r.get("ChngInOpnIntrst")) / lot),
            "volume": int(_num(r.get("TtlTradgVol"))),
            "lot": lot,
        }
    return {"trade_date": trade, "unds": unds}


def _day(day: date) -> dict | None:
    key = day.strftime("%Y%m%d")
    with _lock:
        if key in _days:
            return _days[key]
        # a missing TODAY is retried every 20 min (NSE posts it in the evening);
        # a missing past day is a holiday and stays missing
        if key in _miss and (day < datetime.now(IST).date() or time.time() - _miss[key] < 1200):
            return None
    raw = _download(day)
    if raw is None:
        with _lock:
            _miss[key] = time.time()
        return None
    parsed = _parse(raw)
    with _lock:
        _days[key] = parsed
        for old in sorted(_days)[:-3]:
            _days.pop(old, None)
    return parsed


def latest(before: date | None = None) -> dict | None:
    """The newest published day (strictly before `before` when given)."""
    global _latest
    if before is None and time.time() - _latest[0] < 900 and _latest[1]:
        return _days.get(_latest[1]) or _day(datetime.strptime(_latest[1], "%Y%m%d").date())
    d = before - timedelta(days=1) if before else datetime.now(IST).date()
    for _ in range(12):
        if d.weekday() < 5:
            got = _day(d)
            if got:
                if before is None:
                    _latest = (time.time(), d.strftime("%Y%m%d"))
                return got
        d -= timedelta(days=1)
    return None


# ── names ──────────────────────────────────────────────────────────────────

def underlying_of(symbol: str) -> str:
    s = str(symbol or "").strip().upper()
    if s.startswith("NSE:"):
        s = s[4:]
    return INDEX_UNDERLYING.get(s, s)


def chart_symbol(und: str) -> str:
    return INDEX_CHART.get(und, und)


def has_options(symbol: str) -> bool:
    eod = latest()
    return bool(eod and eod["unds"].get(underlying_of(symbol), {}).get("opts"))


def underlyings() -> list[dict]:
    eod = latest() or {"unds": {}}
    return sorted(({"underlying": u, "symbol": chart_symbol(u), "index": d["index"]}
                   for u, d in eod["unds"].items() if d["opts"]),
                  key=lambda x: (not x["index"], x["underlying"]))


# ── the live layer ─────────────────────────────────────────────────────────

_kite = None
_kite_at = 0.0
_kite_dead_until = 0.0


def _kite_quote(keys: list[str]) -> dict:
    """{key: quote} for the keys, or {} when there is no Kite session. Same
    client and back-off as the depth widget (depth.py)."""
    global _kite, _kite_at, _kite_dead_until
    if not keys or time.time() < _kite_dead_until:
        return {}
    try:
        if _kite is None or time.time() - _kite_at > 600:
            import kite_stream
            _kite = kite_stream._kite_client(kite_stream._kite_access_token())
            _kite_at = time.time()
        out: dict = {}
        for i in range(0, len(keys), 200):
            out.update(_kite.quote(keys[i:i + 200]) or {})
        return out
    except BaseException as exc:            # noqa: BLE001 — SystemExit from a mock token too
        logging.info("option chain: no live Kite quotes (%s)", str(exc)[:120])
        _kite = None
        # no session at all (SystemExit from the token lookup) will not appear
        # by itself in a minute; a dropped call might
        _kite_dead_until = time.time() + (900 if isinstance(exc, SystemExit) else 120)
        return {}


def _top(depth: dict | None, side: str) -> float:
    try:
        return float(((depth or {}).get(side) or [{}])[0].get("price") or 0.0)
    except (TypeError, ValueError, IndexError, AttributeError):
        return 0.0


# ── greeks: pivot's engine ─────────────────────────────────────────────────

def _greeks():
    root = str(HERE.parent / "api")
    if root not in sys.path:
        sys.path.insert(0, root)
    from backend.market.greeks.black76 import black76_greeks, year_fraction
    from backend.market.greeks.forward import synthetic_forward
    from backend.market.greeks.iv import IV_OK, IV_WIDE_SPREAD, implied_vol
    return black76_greeks, year_fraction, synthetic_forward, implied_vol, (IV_OK, IV_WIDE_SPREAD)


def _decorate(rows: list[dict], F: float, T: float) -> bool:
    """IV + Greeks on every traded side, in place. False when the engine is
    unavailable (the chain still ships, without them)."""
    try:
        import numpy as np
        black76_greeks, _, _, implied_vol, ok = _greeks()
    except Exception as exc:                # noqa: BLE001
        logging.warning("option chain: greeks engine unavailable: %s", exc)
        return False
    if F <= 0 or T <= 0:
        return True
    for side, flag in (("ce", 1.0), ("pe", -1.0)):
        live = [r[side] for r in rows if r.get(side) and r[side]["ltp"] > 0
                and (r[side]["volume"] > 0 or r[side].get("bid"))]
        if not live:
            continue
        K = np.array([q["_k"] for q in live])
        px = np.array([q.get("mid") or q["ltp"] for q in live])
        bid = np.array([q.get("bid") or 0.0 for q in live]) if live[0].get("bid") is not None else None
        ask = np.array([q.get("ask") or 0.0 for q in live]) if bid is not None else None
        iv, status = implied_vol(px, F, K, T, flag, r=RISK_FREE, bid=bid, ask=ask)
        use = np.array([s in ok for s in status]) & np.isfinite(iv)
        g = black76_greeks(F, K, np.where(use, iv, 0.2), np.full_like(K, T), r=RISK_FREE, flag=flag)
        for j, q in enumerate(live):
            if use[j]:
                q["iv"] = round(float(iv[j]) * 100, 2)
                q["delta"] = round(float(g["delta"][j]), 3)
                q["gamma"] = round(float(g["gamma"][j]), 5)
                q["theta"] = round(float(g["theta"][j]), 2)
                q["vega"] = round(float(g["vega"][j]), 2)
    return True


# ── the chain ──────────────────────────────────────────────────────────────

def _alive(e: str, now: datetime) -> bool:
    """An expiry still trades until 15:30 IST on its day."""
    today = now.strftime("%Y-%m-%d")
    return e > today or (e == today and (now.hour, now.minute) < (15, 30))


def _pick_expiry(exps: list[str], want: str | None, now: datetime) -> str | None:
    today = now.strftime("%Y-%m-%d")
    live = [e for e in exps if _alive(e, now)] or exps[-1:]
    if not want:
        return live[0] if live else None
    w = str(want).strip()
    if w in exps:
        return w
    try:                                    # "27 Oct", "27-Oct-2026", "2026-10-27"
        for fmt in ("%d %b %Y", "%d-%b-%Y", "%d %b", "%d-%b", "%Y-%m-%d", "%d %B", "%d %B %Y"):
            try:
                d = datetime.strptime(w.title() if "%b" in fmt or "%B" in fmt else w, fmt)
                if d.year == 1900:
                    d = d.replace(year=int(today[:4]))
                iso = d.strftime("%Y-%m-%d")
                return min(live, key=lambda e: abs((date.fromisoformat(e) - d.date()).days)) \
                    if iso not in exps else iso
            except ValueError:
                continue
    except Exception:                       # noqa: BLE001
        pass
    lw = w.lower()
    if lw in ("next", "next week", "second"):
        return live[1] if len(live) > 1 else live[0]
    return None


def chain(symbol: str, expiry: str | None = None, strikes: int = 12, *,
          live: bool = True) -> dict:
    sym = str(symbol or "").strip().upper()
    und = underlying_of(sym)
    if sym in BSE_INDEX:
        return {"available": False, "symbol": sym,
                "reason": f"{sym} options trade on BSE, which this chain does not cover yet. "
                          "NIFTY 50 and NIFTY BANK are covered."}
    try:
        eod = latest()
    except Exception as exc:                # noqa: BLE001 — NSE unreachable
        return {"available": False, "symbol": sym,
                "reason": "NSE's F&O file could not be fetched just now.", "detail": str(exc)[:160]}
    if not eod:
        return {"available": False, "symbol": sym,
                "reason": "No NSE F&O file was found for the last two weeks."}
    u = eod["unds"].get(und)
    if not u or not u["opts"]:
        return {"available": False, "symbol": sym, "underlying": und,
                "reason": f"{sym} has no options listed on NSE. Options trade on about "
                          f"{len(eod['unds'])} names: the main indices and large stocks."}

    now = datetime.now(IST)
    exps = sorted(u["opts"])
    exp = _pick_expiry(exps, expiry, now)
    if exp is None:
        return {"available": False, "symbol": sym, "underlying": und,
                "reason": f"No expiry matches '{expiry}'.", "expiries": exps}
    ladder = u["opts"][exp]
    lot = u["lot"]
    rows = []
    for k in sorted(ladder):
        row = {"strike": k}
        for side in ("CE", "PE"):
            q = ladder[k].get(side)
            if q:
                row[side.lower()] = {**{f: q[f] for f in ("ltp", "prev", "oi", "oi_chg", "volume")},
                                     "sym": q["sym"], "_k": k}
        rows.append(row)

    spot, source, as_of = u["spot"], "nse_eod", eod["trade_date"]
    fut_px = u["futs"].get(exp)
    as_of_dt = datetime.combine(date.fromisoformat(as_of), datetime.min.time()).replace(
        hour=15, minute=30, tzinfo=IST)

    # ── live: re-quote what will be shown, plus spot and the future ──
    centre = spot
    step_rows = rows
    if live:
        near = sorted(rows, key=lambda r: abs(r["strike"] - centre))[:max(8, min(int(strikes or 12), 40)) * 2 + 2]
        keys = [f"NFO:{r[s]['sym']}" for r in near for s in ("ce", "pe") if r.get(s)]
        spot_key = f"NSE:{chart_symbol(und)}"
        fut_key = f"NFO:{u['futsym'].get(exp)}" if u["futsym"].get(exp) else None
        got = _kite_quote(keys + [spot_key] + ([fut_key] if fut_key else []))
        if got:
            base = latest(before=now.date()) if as_of >= now.strftime("%Y-%m-%d") else eod
            base_u = (base or {}).get("unds", {}).get(und, {}).get("opts", {}).get(exp, {})
            source, as_of_dt = "kite", now
            as_of = now.strftime("%Y-%m-%d %H:%M")
            spot = float((got.get(spot_key) or {}).get("last_price") or spot)
            if fut_key and got.get(fut_key):
                fut_px = float(got[fut_key].get("last_price") or fut_px or 0) or fut_px
            for r in near:
                for s in ("ce", "pe"):
                    q = r.get(s)
                    kq = got.get(f"NFO:{q['sym']}") if q else None
                    if not kq:
                        continue
                    b = (base_u.get(r["strike"]) or {}).get(s.upper())
                    oi = round(float(kq.get("oi") or 0) / (lot or 1))
                    q.update({"ltp": float(kq.get("last_price") or 0.0),
                              "prev": float((kq.get("ohlc") or {}).get("close") or q["prev"]),
                              "oi": oi, "oi_chg": oi - b["oi"] if b else q["oi_chg"],
                              "volume": round(float(kq.get("volume") or 0) / (lot or 1)),
                              "bid": _top(kq.get("depth"), "buy"), "ask": _top(kq.get("depth"), "sell")})
                    if q["bid"] > 0 and q["ask"] >= q["bid"]:
                        q["mid"] = (q["bid"] + q["ask"]) / 2

    for r in rows:
        for s in ("ce", "pe"):
            q = r.get(s)
            if q and q["prev"] > 0 and q["ltp"] > 0:
                q["chg"] = round(q["ltp"] - q["prev"], 2)
                q["chg_pct"] = round((q["ltp"] / q["prev"] - 1) * 100, 1)

    # ── forward, time, greeks ──
    try:
        _, year_fraction, synthetic_forward, _, _ = _greeks()
        T = year_fraction(date.fromisoformat(exp), now=as_of_dt)
    except Exception:                       # noqa: BLE001
        synthetic_forward, T = None, max(0.0, (date.fromisoformat(exp) - as_of_dt.date()).days) / 365.0
    F, basis = 0.0, "spot"
    if fut_px:
        F, basis = float(fut_px), "future"
    elif synthetic_forward:
        both = [r for r in rows if r.get("ce") and r.get("pe") and r["ce"]["volume"] and r["pe"]["volume"]]
        syn = synthetic_forward([r["strike"] for r in both], [r["ce"]["ltp"] for r in both],
                                [r["pe"]["ltp"] for r in both], spot, T, RISK_FREE) if both else None
        if syn and abs(syn / spot - 1) < 0.05:
            F, basis = float(syn), "parity"
    F = F or spot
    greeks = _decorate(rows, F, T)

    # ── the reads ──
    atm = min(rows, key=lambda r: abs(r["strike"] - F))["strike"]
    ks = [r["strike"] for r in rows]
    i_atm = ks.index(atm)
    gaps = sorted(b - a for a, b in zip(ks[max(0, i_atm - 4):i_atm + 4], ks[max(0, i_atm - 4) + 1:i_atm + 5]))
    step = gaps[len(gaps) // 2] if gaps else None

    def tot(s, f):
        return sum((r.get(s) or {}).get(f) or 0 for r in rows)

    call_oi, put_oi = tot("ce", "oi"), tot("pe", "oi")
    call_vol, put_vol = tot("ce", "volume"), tot("pe", "volume")
    pain, max_pain = None, None
    if call_oi or put_oi:
        for t in ks:
            p = sum(max(0.0, t - r["strike"]) * ((r.get("ce") or {}).get("oi") or 0)
                    + max(0.0, r["strike"] - t) * ((r.get("pe") or {}).get("oi") or 0) for r in rows)
            if pain is None or p < pain:
                pain, max_pain = p, t

    def ranked(s, f, n=3, neg=False):
        got = [r for r in rows if r.get(s) and (r[s].get(f) or 0) != 0]
        got.sort(key=lambda r: r[s][f], reverse=not neg)
        return [{"strike": r["strike"], "oi": r[s]["oi"], "oi_chg": r[s]["oi_chg"]}
                for r in got[:n] if (r[s][f] < 0 if neg else r[s][f] > 0)]

    a = next(r for r in rows if r["strike"] == atm)
    ivs = [a[s]["iv"] for s in ("ce", "pe") if a.get(s) and a[s].get("iv")]
    atm_iv = round(sum(ivs) / len(ivs), 2) if ivs else None
    straddle = sum(a[s]["ltp"] for s in ("ce", "pe") if a.get(s)) if a.get("ce") and a.get("pe") else None
    sd_move = F * atm_iv / 100 * math.sqrt(T) if atm_iv and T > 0 else None

    n = len(rows) if not strikes or int(strikes) <= 0 else max(3, min(int(strikes), 60))
    lo, hi = max(0, i_atm - n), min(len(rows), i_atm + n + 1)
    shown = rows[lo:hi]
    for r in rows:
        for s in ("ce", "pe"):
            if r.get(s):
                r[s].pop("_k", None)
                r[s].pop("mid", None)

    futs = set(u["futs"])
    dte = (date.fromisoformat(exp) - now.date()).days
    return {
        "available": True,
        "symbol": chart_symbol(und), "underlying": und, "index": u["index"],
        "source": source, "as_of": as_of,
        "as_of_label": (f"Live · {now:%H:%M}" if source == "kite"
                        else f"Close · {date.fromisoformat(as_of):%d %b %Y}"),
        "expiry": exp, "dte": dte,
        "expiries": [{"date": e, "dte": (date.fromisoformat(e) - now.date()).days,
                      "monthly": e in futs} for e in exps if _alive(e, now)] or
                    [{"date": exp, "dte": dte, "monthly": exp in futs}],
        "lot_size": lot, "spot": round(spot, 2), "forward": round(F, 2), "forward_basis": basis,
        "atm_strike": atm, "step": step,
        "atm_iv": atm_iv,
        "expected_move": ({"abs": round(straddle, 2), "pct": round(straddle / spot * 100, 2),
                           "basis": "ATM straddle"} if straddle and spot else None),
        "one_sd_move": round(sd_move, 2) if sd_move else None,
        "pcr_oi": round(put_oi / call_oi, 2) if call_oi else None,
        "pcr_volume": round(put_vol / call_vol, 2) if call_vol else None,
        "max_pain": max_pain,
        "totals": {"call_oi": call_oi, "put_oi": put_oi,
                   "call_oi_chg": tot("ce", "oi_chg"), "put_oi_chg": tot("pe", "oi_chg"),
                   "call_volume": call_vol, "put_volume": put_vol},
        "walls": {"call": ranked("ce", "oi"), "put": ranked("pe", "oi")},
        "oi_added": {"call": ranked("ce", "oi_chg"), "put": ranked("pe", "oi_chg")},
        "oi_cut": {"call": ranked("ce", "oi_chg", neg=True), "put": ranked("pe", "oi_chg", neg=True)},
        "greeks": greeks,
        "strikes_total": len(rows), "rows": shown,
        "units": "OI, OI change and volume are in contracts (lots)",
    }


def digest(c: dict, rows: int = 9) -> dict:
    """The chain for the model: every summary read, and a narrow strip of
    strikes around the money — the card carries the rest."""
    if not c.get("available"):
        return c
    keep = ("symbol", "underlying", "source", "as_of_label", "expiry", "dte", "lot_size", "spot",
            "forward", "forward_basis", "atm_strike", "atm_iv", "expected_move", "one_sd_move",
            "pcr_oi", "pcr_volume", "max_pain", "totals", "walls", "oi_added", "oi_cut", "units")
    out = {k: c.get(k) for k in keep}
    out["expiries"] = [e["date"] + (" (monthly)" if e["monthly"] else "") for e in c["expiries"][:8]]
    i = next((j for j, r in enumerate(c["rows"]) if r["strike"] == c["atm_strike"]), 0)
    strip = c["rows"][max(0, i - rows // 2): i + rows // 2 + 1]
    pick = ("ltp", "chg_pct", "oi", "oi_chg", "volume", "iv", "delta")
    out["near_money"] = [{"strike": r["strike"],
                          **{s: {f: r[s].get(f) for f in pick if r[s].get(f) is not None}
                             for s in ("ce", "pe") if r.get(s)}} for r in strip]
    return out
