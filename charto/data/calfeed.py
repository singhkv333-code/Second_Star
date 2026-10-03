"""The Calendar widget's data: what is scheduled, from sources we can stand on.

Three feeds, merged into one dated list:

  macro    RBI MPC decisions, FOMC decisions, US and India CPI prints — from
           pivot's own macro calendar (pivot/backend/macro_events/calendar.py),
           which is hand-kept from the RBI, Fed, BLS and MOSPI schedules. Rows
           that file marks APPROX stay marked: the date is the usual one, not
           a published one.
  board    NSE's event calendar: board meetings with their stated purpose —
           financial results, dividends, fund raising.
  actions  NSE's corporate actions: ex-dates for dividends, bonuses, splits,
           rights and buybacks.

NSE is read the way pivotted/fetch_filings_sample.py reads it (a session on
nseindia.com, then the JSON API with it as referrer). Each feed is cached for
30 minutes and a failed refresh serves the last good copy, labelled stale; a
feed that never answered is reported as unavailable, never filled in. No
yfinance: it rate-limits quickly and has no Indian corporate calendar.
"""
from __future__ import annotations

import http.cookiejar
import json
import re
import ssl
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
_TTL = 1800.0
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_PIVOT = Path(__file__).resolve().parents[2] / "pivot"

_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()
_busy: dict[str, threading.Event] = {}


def _ssl():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


# ── macro: the hand-kept schedule ───────────────────────────────────────────

_MACRO = {
    "rbi_mpc": ("IN", "RBI policy decision", "The repo rate and the MPC's stance"),
    "india_cpi": ("IN", "India CPI inflation", "MOSPI's consumer price index for the month"),
    "us_fomc": ("US", "FOMC rate decision", "The Federal Reserve's policy rate"),
    "us_cpi": ("US", "US CPI inflation", "BLS consumer price index for the month"),
}


def _macro() -> list[dict]:
    root = str(_PIVOT)
    if root not in sys.path and _PIVOT.is_dir():
        sys.path.insert(0, root)
    try:
        from backend.macro_events import calendar as cal  # type: ignore
    except Exception:                          # noqa: BLE001 — reported as unavailable
        return []
    out = []
    for kind, (region, title, detail) in _MACRO.items():
        for ev in cal.events_for_kind(kind):
            at = ev.fire_at_utc.astimezone(IST)
            out.append({
                "date": at.date().isoformat(), "time": at.strftime("%H:%M"),
                "kind": "macro", "region": region, "title": title, "detail": detail,
                "approx": "APPROX" in ev.label, "importance": "high",
                "source": "Pivot macro calendar",
            })
    return out


# ── NSE ─────────────────────────────────────────────────────────────────────

def _nse(path: str) -> list:
    jar = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar),
                                     urllib.request.HTTPSHandler(context=_ssl()))
    op.addheaders = [("User-Agent", _UA), ("Accept", "application/json, text/plain, */*"),
                     ("Accept-Language", "en-US,en;q=0.9"),
                     ("Referer", "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar")]
    try:
        op.open("https://www.nseindia.com/", timeout=8).read(256)
    except Exception:                          # noqa: BLE001 — the API often answers anyway
        pass
    with op.open(f"https://www.nseindia.com/api/{path}", timeout=12) as r:
        d = json.loads(r.read(4_000_000))
    return d if isinstance(d, list) else d.get("data", []) if isinstance(d, dict) else []


def _day(s: str) -> str | None:
    try:
        return datetime.strptime(s.strip(), "%d-%b-%Y").date().isoformat()
    except (ValueError, AttributeError):
        return None


def _board() -> list[dict]:
    out = []
    for r in _nse("event-calendar?index=equities"):
        d = _day(r.get("date", ""))
        if not d:
            continue
        purpose = (r.get("purpose") or "").strip()
        kind = "results" if re.search(r"financial result", purpose, re.I) else "board"
        detail = re.sub(r"\s+", " ", (r.get("bm_desc") or purpose)).strip()
        out.append({
            "date": d, "time": None, "kind": kind, "region": "IN",
            "symbol": (r.get("symbol") or "").upper(), "title": (r.get("company") or r.get("symbol") or "").strip(),
            "purpose": purpose, "detail": detail[:220], "source": "NSE event calendar",
        })
    return out


_ACT = [("dividend", r"dividend"), ("bonus", r"bonus"), ("split", r"split|sub-?division"),
        ("rights", r"rights"), ("buyback", r"buy ?back")]


def _actions() -> list[dict]:
    out = []
    for r in _nse("corporates-corporateActions?index=equities"):
        d = _day(r.get("exDate", ""))
        if not d:
            continue
        subject = re.sub(r"\s+", " ", (r.get("subject") or "")).strip()
        what = next((k for k, rx in _ACT if re.search(rx, subject, re.I)), "action")
        rec = _day(r.get("recDate", "") or "")
        out.append({
            "date": d, "time": None, "kind": "action", "action": what, "region": "IN",
            "symbol": (r.get("symbol") or "").upper(), "title": (r.get("comp") or r.get("symbol") or "").strip(),
            "detail": subject + (f" · record date {rec}" if rec and rec != d else ""),
            "source": "NSE corporate actions",
        })
    return out


_FEEDS = {"macro": _macro, "board": _board, "actions": _actions}
_NAMES = {"macro": "Pivot macro calendar", "board": "NSE event calendar", "actions": "NSE corporate actions"}


def _feed(name: str) -> tuple[list[dict], dict]:
    """(rows, status) for one feed — cached, coalesced, stale on failure."""
    while True:
        with _lock:
            hit = _cache.get(name)
            if hit and time.time() - hit[0] < _TTL:
                return hit[1], {"id": name, "name": _NAMES[name], "ok": True, "as_of": hit[0]}
            ev = _busy.get(name)
            owner = ev is None
            if owner:
                ev = _busy[name] = threading.Event()
        if not owner:
            ev.wait(15)
            continue
        try:
            rows = _FEEDS[name]()
            if name == "macro" and not rows:
                raise RuntimeError("the macro calendar could not be loaded")
            with _lock:
                _cache[name] = (time.time(), rows)
            return rows, {"id": name, "name": _NAMES[name], "ok": True, "as_of": time.time()}
        except Exception as e:                 # noqa: BLE001 — one dead feed must not sink the rest
            with _lock:
                stale = _cache.get(name)
            if stale:
                return stale[1], {"id": name, "name": _NAMES[name], "ok": True, "stale": True, "as_of": stale[0]}
            return [], {"id": name, "name": _NAMES[name], "ok": False, "error": type(e).__name__}
        finally:
            with _lock:
                _busy.pop(name, None)
            ev.set()


def calendar(days: int = 14, kinds: list[str] | None = None, symbols: list[str] | None = None) -> dict:
    """Everything scheduled from today (IST) through `days` ahead."""
    days = max(1, min(int(days or 14), 62))
    want = [k for k in (kinds or list(_FEEDS)) if k in _FEEDS] or list(_FEEDS)
    today = datetime.now(IST).date()
    lo, hi = today.isoformat(), (today + timedelta(days=days)).isoformat()
    syms = {s.strip().upper() for s in (symbols or []) if s.strip()}
    threads, got = [], {}
    for k in want:
        t = threading.Thread(target=lambda k=k: got.__setitem__(k, _feed(k)), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(20)
    items, sources, seen = [], [], set()
    for k in want:
        rows, st = got.get(k, ([], {"id": k, "name": _NAMES[k], "ok": False, "error": "timeout"}))
        sources.append(st)
        for r in rows:
            if not (lo <= r["date"] <= hi):
                continue
            if syms and r["kind"] != "macro" and r.get("symbol") not in syms:
                continue
            key = (r["date"], r["kind"], r.get("symbol"), r.get("title"), r.get("detail"))
            if key in seen:                    # NSE lists an action once per series
                continue
            seen.add(key)
            items.append(r)
    items.sort(key=lambda r: (r["date"], r["time"] or "99:99", r["kind"] != "macro", r.get("title", "")))
    return {"from": lo, "to": hi, "items": items, "sources": sources, "today": today.isoformat()}


if __name__ == "__main__":
    d = calendar(14)
    print(json.dumps({"n": len(d["items"]), "sources": d["sources"], "first": d["items"][:4]}, indent=1, default=str))
