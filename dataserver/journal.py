"""Durable, user-owned trade journal for Charto.

Facts stay typed so P&L can be reproduced. Meaning stays flexible: plans,
reviews, playbooks, tags and custom fields are JSON authored by the trader (or
proposed by chat), never a code-side taxonomy.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from os import environ

# The chart itself, for the chat surface at the foot of this file: a spoken
# time becomes a real bar through the same parser and the same store the
# candles came from. Safe at import because dataserver aliases itself into
# sys.modules before it imports this module — the same requirement alerts.py
# has, and for the same reason.
import dataserver as ds


DB_PATH = Path(environ.get("CHARTO_USERS_DB") or Path(__file__).parent / "charto_users.db")
_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row
_db.execute("PRAGMA foreign_keys=ON")
_db.execute("PRAGMA journal_mode=WAL")
_db.execute("PRAGMA busy_timeout=10000")
_lock = threading.Lock()

_db.executescript("""
CREATE TABLE IF NOT EXISTS journal_trades (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  symbol TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('long','short')),
  status TEXT NOT NULL DEFAULT 'closed' CHECK(status IN ('open','closed')),
  opened_at INTEGER NOT NULL,
  closed_at INTEGER,
  quantity REAL NOT NULL,
  entry_price REAL NOT NULL,
  exit_price REAL,
  fees REAL NOT NULL DEFAULT 0,
  initial_risk REAL,
  currency TEXT NOT NULL DEFAULT 'INR',
  source TEXT NOT NULL DEFAULT 'manual',
  external_id TEXT,
  plan TEXT NOT NULL DEFAULT '{}',
  review TEXT NOT NULL DEFAULT '{}',
  custom TEXT NOT NULL DEFAULT '{}',
  tags TEXT NOT NULL DEFAULT '[]',
  playbook_id INTEGER,
  created INTEGER NOT NULL,
  updated INTEGER NOT NULL,
  UNIQUE(user_id, source, external_id)
);
CREATE INDEX IF NOT EXISTS journal_trade_recent ON journal_trades(user_id, opened_at DESC);
CREATE INDEX IF NOT EXISTS journal_trade_symbol ON journal_trades(user_id, symbol, opened_at DESC);

CREATE TABLE IF NOT EXISTS journal_playbooks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  spec TEXT NOT NULL DEFAULT '{}',
  created INTEGER NOT NULL,
  updated INTEGER NOT NULL,
  UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS journal_revisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id),
  trade_id INTEGER NOT NULL REFERENCES journal_trades(id) ON DELETE CASCADE,
  origin TEXT NOT NULL,
  before_json TEXT NOT NULL,
  after_json TEXT NOT NULL,
  created INTEGER NOT NULL
);
""")
_db.commit()


def _json(raw, fallback):
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
        return value if isinstance(value, type(fallback)) else fallback
    except (TypeError, ValueError):
        return fallback


def _trade(row):
    d = dict(row)
    for key, fallback in (("plan", {}), ("review", {}), ("custom", {}), ("tags", [])):
        d[key] = _json(d[key], fallback)
    direction = 1 if d["side"] == "long" else -1
    if d["exit_price"] is None:
        d.update({"gross_pnl": None, "net_pnl": None, "r_multiple": None})
    else:
        gross = (d["exit_price"] - d["entry_price"]) * d["quantity"] * direction
        net = gross - d["fees"]
        risk = d["initial_risk"]
        d.update({"gross_pnl": round(gross, 4), "net_pnl": round(net, 4),
                  "r_multiple": round(net / risk, 3) if risk and risk > 0 else None})
    d["reviewed"] = bool(d["review"])
    return d


def _owned(uid, trade_id):
    return _db.execute("SELECT * FROM journal_trades WHERE user_id=? AND id=?",
                       (uid, trade_id)).fetchone()


def api_bootstrap(uid):
    with _lock:
        rows = _db.execute("SELECT * FROM journal_trades WHERE user_id=? ORDER BY opened_at DESC, id DESC",
                           (uid,)).fetchall()
        books = [dict(r) for r in _db.execute(
            "SELECT * FROM journal_playbooks WHERE user_id=? ORDER BY name", (uid,)).fetchall()]
    for b in books:
        b["spec"] = _json(b["spec"], {})
    trades = [_trade(r) for r in rows]
    return 200, {"trades": trades, "playbooks": books, "overview": overview(trades)}


def overview(trades):
    closed = [t for t in trades if t["net_pnl"] is not None]
    wins = [t for t in closed if t["net_pnl"] > 0]
    losses = [t for t in closed if t["net_pnl"] < 0]
    gross_win = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    rs = [t["r_multiple"] for t in closed if t["r_multiple"] is not None]
    adhered = [t for t in closed if t["review"].get("adherence") in (True, False)]
    return {
        "count": len(trades), "closed": len(closed),
        "net_pnl": round(sum(t["net_pnl"] for t in closed), 2),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "expectancy_r": round(sum(rs) / len(rs), 2) if rs else None,
        "adherence": round(sum(bool(t["review"]["adherence"]) for t in adhered) / len(adhered) * 100, 1) if adhered else None,
        "reviewed": sum(t["reviewed"] for t in trades),
    }


def api_get(uid, trade_id):
    with _lock:
        row = _owned(uid, trade_id)
    return (200, {"trade": _trade(row)}) if row else (404, {"error": "trade not found"})


def _clean(body, creating=False):
    out = {}
    text = {"symbol", "side", "status", "currency", "source", "external_id"}
    nums = {"opened_at", "closed_at", "quantity", "entry_price", "exit_price", "fees", "initial_risk", "playbook_id"}
    objects = {"plan": dict, "review": dict, "custom": dict, "tags": list}
    for key in text:
        if key in body:
            out[key] = str(body[key]).strip()
    if "symbol" in out:
        out["symbol"] = out["symbol"].upper()[:32]
    for key in nums:
        if key in body:
            out[key] = None if body[key] in (None, "") else float(body[key])
            if key in ("opened_at", "closed_at", "playbook_id") and out[key] is not None:
                out[key] = int(out[key])
    for key, typ in objects.items():
        if key in body:
            if not isinstance(body[key], typ):
                raise ValueError(f"{key} must be {typ.__name__}")
            out[key] = json.dumps(body[key], separators=(",", ":"))
    if creating:
        for key in ("symbol", "side", "opened_at", "quantity", "entry_price"):
            if key not in out or out[key] in ("", None):
                raise ValueError(f"{key} is required")
    if out.get("side") not in (None, "long", "short"):
        raise ValueError("side must be long or short")
    if out.get("status") not in (None, "open", "closed"):
        raise ValueError("status must be open or closed")
    if any(out.get(k, 1) <= 0 for k in ("quantity", "entry_price") if out.get(k) is not None):
        raise ValueError("quantity and entry price must be positive")
    return out


def api_create(uid, body):
    try:
        data = _clean(body, True)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    now = int(time.time())
    defaults = {"status": "closed" if data.get("exit_price") is not None else "open",
                "fees": 0, "currency": "INR", "source": "manual",
                "plan": "{}", "review": "{}", "custom": "{}", "tags": "[]"}
    defaults.update(data)
    cols = ["user_id", *defaults.keys(), "created", "updated"]
    vals = [uid, *defaults.values(), now, now]
    try:
        with _lock:
            cur = _db.execute(f"INSERT INTO journal_trades ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})", vals)
            _db.commit()
            row = _owned(uid, cur.lastrowid)
    except sqlite3.IntegrityError as exc:
        return 409, {"error": "this imported execution already exists" if "UNIQUE" in str(exc) else str(exc)}
    return 201, {"trade": _trade(row)}


def api_patch(uid, trade_id, body):
    with _lock:
        old = _owned(uid, trade_id)
    if not old:
        return 404, {"error": "trade not found"}
    if body.get("delete"):
        with _lock:
            _db.execute("DELETE FROM journal_trades WHERE user_id=? AND id=?", (uid, trade_id))
            _db.commit()
        return 200, {"deleted": trade_id}
    try:
        data = _clean(body)
    except ValueError as exc:
        return 400, {"error": str(exc)}
    if not data:
        return 400, {"error": "nothing to update"}
    now = int(time.time())
    with _lock:
        _db.execute("INSERT INTO journal_revisions(user_id,trade_id,origin,before_json,after_json,created) VALUES(?,?,?,?,?,?)",
                    (uid, trade_id, str(body.get("origin") or "user")[:24],
                     json.dumps(_trade(old)), json.dumps(body), now))
        sets = ",".join(f"{k}=?" for k in data)
        _db.execute(f"UPDATE journal_trades SET {sets},updated=? WHERE user_id=? AND id=?",
                    (*data.values(), now, uid, trade_id))
        _db.commit()
        row = _owned(uid, trade_id)
    return 200, {"trade": _trade(row)}


def api_playbook(uid, body, book_id=None):
    name = str(body.get("name") or "").strip()
    if not name:
        return 400, {"error": "playbook name required"}
    spec = body.get("spec") or {}
    if not isinstance(spec, dict):
        return 400, {"error": "spec must be an object"}
    now = int(time.time())
    with _lock:
        if book_id:
            cur = _db.execute("UPDATE journal_playbooks SET name=?,description=?,spec=?,updated=? WHERE user_id=? AND id=?",
                              (name, str(body.get("description") or ""), json.dumps(spec), now, uid, book_id))
            if not cur.rowcount:
                return 404, {"error": "playbook not found"}
        else:
            cur = _db.execute("INSERT INTO journal_playbooks(user_id,name,description,spec,created,updated) VALUES(?,?,?,?,?,?)",
                              (uid, name, str(body.get("description") or ""), json.dumps(spec), now, now))
            book_id = cur.lastrowid
        _db.commit()
        row = _db.execute("SELECT * FROM journal_playbooks WHERE user_id=? AND id=?", (uid, book_id)).fetchone()
    out = dict(row); out["spec"] = _json(out["spec"], {})
    return 200, {"playbook": out}


# ══ the chat surface ═══════════════════════════════════════════════════════
#
# WHY THIS IS NOT "THE FORM, BUT TYPED AT A CHATBOT"
# --------------------------------------------------
# A useful trade row is expensive: symbol, side, time, quantity, entry, exit,
# fees, and — the one that makes the whole journal worth keeping — the initial
# risk, without which r_multiple is null and expectancy, adherence and profit
# factor are all dead columns. Asking a person to dictate nine numbers is
# asking them to abandon the journal, which is what happens to most journals.
#
# But a Charto user is looking at the trade while they describe it. The screen
# already holds the instrument, the bars the fill happened on, and often the
# PLAN itself as a position drawn by plan_position — side, entry, stop,
# targets, quantity and risk, already agreed. So the rule here is: the user
# supplies meaning, the chart supplies numbers, and nobody retypes what is
# already on screen.
#
#   · an ADDRESS instead of a price — "I bought at yesterday's close", "I got
#     in at 09:20" resolves against the real bar and records WHICH bar, so the
#     entry is checkable rather than remembered.
#   · from_drawing — a plan_position drawing becomes a row in one argument.
#     A planned trade is already fully specified; making the trader say it
#     again is the friction that empties journals.
#   · a stop becomes initial_risk (|entry − stop| × qty) rather than being
#     lost in prose, because that is the field the statistics need.
#   · partial is fine — symbol, side, quantity and an entry open a row; the
#     exit, the review and the lesson arrive later in the same conversation.
#
# Nothing here reads the user's words. The model composes the call; this side
# resolves it against real bars and refuses what it cannot ground.


def _bar_at(symbol: str, when: str, interval: str = "1m"):
    """The real bar a spoken time lands on — (bar, label) or (None, reason).

    Resolution is the chart's own: ds._parse_ist accepts exactly the format
    the model has ever been shown, and ds._rows answers from the same store
    the candles came from. The bar RETURNED is the last one at or before the
    stated time, which is the bar a fill at that moment actually printed in.
    """
    ts = ds._parse_ist(when)
    if ts is None:
        return None, (f"'{when}' is not a time I can read — use the chart's "
                      f"own format, e.g. '03 Aug 2026 09:20'")
    prev = getattr(ds._req, "symbol", "")
    ds._req.symbol = str(symbol or prev).upper()
    try:
        rows = ds._rows(interval, 3, to=ts)
    except Exception as exc:                                # noqa: BLE001
        return None, f"could not read {symbol} bars at {when}: {exc}"
    finally:
        ds._req.symbol = prev
    if not rows:
        return None, f"{symbol} has no bars at {when}"
    b = rows[-1]
    return b, ds._ist(b[0])


_BAR_FIELD = {"open": 1, "high": 2, "low": 3, "close": 4}


def _position_drawing(ref: str) -> dict | None:
    """A plan_position drawing, as the request currently sees it.

    Read from the envelope rather than from what a tool drew earlier: the user
    can DRAG a plan, and a journal row built from a stale copy would record a
    trade they did not take.
    """
    got = getattr(ds._drawings, "chat_by_id", {}).get(str(ref or "").upper())
    if got and got.get("kind") == "position":
        return got
    return None


def _need_account():
    return {"error": "the journal needs an account",
            "_note": ("Say the user must sign in — the journal is stored "
                      "server-side against their account, not in this tab. "
                      "Do not offer to remember the trade in conversation.")}


def tool_log_trade(symbol: str = "", side: str = "", quantity: float = 0,
                   entry_price=None, entry_at: str = "", exit_price=None,
                   exit_at: str = "", stop=None, initial_risk=None,
                   fees: float = 0, tags=None, thesis: str = "",
                   plan=None, review=None, from_drawing: str = "",
                   interval: str = "1m", user_id: int = 0) -> dict:
    if not user_id:
        return _need_account()
    body: dict = {"source": "chat", "currency": "INR"}
    resolved: list = []

    drawn = _position_drawing(from_drawing) if from_drawing else None
    if from_drawing and not drawn:
        return {"error": f"no position drawing '{from_drawing}' on this chart",
                "_note": ("Only a plan drawn by plan_position carries a side, "
                          "entry, stop and size. List what is on the chart "
                          "instead of guessing a ref, or take the numbers from "
                          "the user.")}
    if drawn:
        side = side or str(drawn.get("side") or "")
        entry_price = drawn.get("entry") if entry_price is None else entry_price
        stop = drawn.get("stop") if stop is None else stop
        quantity = quantity or drawn.get("qty") or 0
        if initial_risk is None and drawn.get("risk_amount"):
            initial_risk = drawn["risk_amount"]
        resolved.append(f"from the plan drawn as {from_drawing}")

    body["symbol"] = str(symbol or ds._sym()).upper()
    body["side"] = str(side or "").lower()
    if body["side"] not in ("long", "short"):
        return {"error": "side must be long or short",
                "_note": "Ask which way the trade was, and do not assume long."}

    # entry: a price if given, otherwise the bar the stated time landed on
    if entry_price is None and entry_at:
        bar, label = _bar_at(body["symbol"], entry_at, interval)
        if bar is None:
            return {"error": label}
        entry_price = bar[_BAR_FIELD["close"]]
        body["opened_at"] = int(bar[0])
        resolved.append(f"entry {entry_price} read off the {label} bar")
    if entry_price is None:
        return {"error": "no entry price",
                "_note": ("Give entry_price, or entry_at as a time on this "
                          "chart and it is read off that bar.")}
    body["entry_price"] = float(entry_price)
    if "opened_at" not in body:
        body["opened_at"] = int(time.time())

    if exit_price is None and exit_at:
        bar, label = _bar_at(body["symbol"], exit_at, interval)
        if bar is None:
            return {"error": label}
        exit_price = bar[_BAR_FIELD["close"]]
        body["closed_at"] = int(bar[0])
        resolved.append(f"exit {exit_price} read off the {label} bar")
    if exit_price is not None:
        body["exit_price"] = float(exit_price)
        body["status"] = "closed"
        body.setdefault("closed_at", int(time.time()))
    else:
        body["status"] = "open"

    try:
        body["quantity"] = float(quantity or 0)
    except (TypeError, ValueError):
        return {"error": "quantity must be a number"}
    if body["quantity"] <= 0:
        return {"error": "quantity is required",
                "_note": "Ask how many shares/lots — it cannot be inferred."}
    body["fees"] = float(fees or 0)

    # The field the statistics live on. Derived from a stop the user has
    # already said rather than asked for a second time, because a row without
    # it can never carry an R-multiple.
    if initial_risk is None and stop is not None:
        try:
            initial_risk = abs(body["entry_price"] - float(stop)) * body["quantity"]
            resolved.append(f"risk {round(initial_risk, 2)} from the "
                            f"{stop} stop")
        except (TypeError, ValueError):
            initial_risk = None
    if initial_risk is not None:
        body["initial_risk"] = float(initial_risk)

    p = dict(plan or {})
    if thesis:
        p["thesis"] = thesis
    if stop is not None:
        p.setdefault("stop", float(stop))
    if drawn and drawn.get("targets"):
        p.setdefault("targets", drawn["targets"])
    if p:
        body["plan"] = p
    if review:
        body["review"] = dict(review)
    if tags:
        body["tags"] = [str(t) for t in tags]

    code, out = api_create(user_id, body)
    if code >= 400:
        return out
    t = out["trade"]
    miss = []
    if t.get("initial_risk") in (None, 0):
        miss.append("no initial risk recorded, so this trade cannot carry an "
                    "R-multiple — offer to add the stop it was taken with")
    if t["status"] == "open":
        miss.append("it is open; closing it later is an update, not a new row")
    return {"trade": t, "_render_hint": "journal_card",
            "_note": (f"Logged trade {t['id']}: {t['side']} {t['quantity']} "
                      f"{t['symbol']} at {t['entry_price']}"
                      + (f", out at {t['exit_price']} for a net "
                         f"{t['net_pnl']}" if t["status"] == "closed" else "")
                      + ". " + ("Resolved " + "; ".join(resolved) + ". "
                                if resolved else "")
                      + (" ".join(miss) if miss else "")
                      + " Quote the numbers as stored — they are the record.")}


def tool_list_trades(symbol: str = "", status: str = "", limit: int = 20,
                     user_id: int = 0) -> dict:
    """The journal as the user's own record, with the statistics it exists for."""
    if not user_id:
        return _need_account()
    _code, out = api_bootstrap(user_id)
    trades = out["trades"]
    sym = str(symbol or "").upper().strip()
    if sym:
        trades = [t for t in trades if t["symbol"] == sym]
    st = str(status or "").lower().strip()
    if st:
        if st not in ("open", "closed"):
            return {"error": "status is 'open' or 'closed'"}
        trades = [t for t in trades if t["status"] == st]
    shown = trades[:max(1, min(int(limit or 20), 100))]
    return {"trades": shown, "showing": len(shown), "matched": len(trades),
            "overview": overview(trades) if (sym or st) else out["overview"],
            "playbooks": [{"id": b["id"], "name": b["name"]} for b in out["playbooks"]],
            "_note": ("`overview` is computed from the rows, not stored: "
                      "expectancy_r and adherence are null until trades carry "
                      "an initial risk and a review, which is worth saying "
                      "when they are. Each trade's `id` is what update_trade "
                      "takes. net_pnl is after fees.")}


def tool_update_trade(trade_id: int = 0, exit_price=None, exit_at: str = "",
                      status: str = "", fees=None, initial_risk=None,
                      stop=None, tags=None, plan=None, review=None,
                      lesson: str = "", adherence=None, emotion: str = "",
                      interval: str = "1m", user_id: int = 0) -> dict:
    """Close a trade, price its exit off a bar, or write the review.

    The review fields are named individually because they are the ones a
    trader says out loud — "I moved my stop", "I was impatient", "it followed
    the plan" — and because a merge that silently dropped an existing lesson
    would lose the only part of the row that was hard to write.
    """
    if not user_id:
        return _need_account()
    if not trade_id:
        return {"error": "which trade? call list_trades for the ids"}
    code, cur = api_get(user_id, int(trade_id))
    if code >= 400:
        return cur
    old = cur["trade"]
    body: dict = {"origin": "chat"}
    resolved = []

    if exit_price is None and exit_at:
        bar, label = _bar_at(old["symbol"], exit_at, interval)
        if bar is None:
            return {"error": label}
        exit_price = bar[_BAR_FIELD["close"]]
        body["closed_at"] = int(bar[0])
        resolved.append(f"exit {exit_price} read off the {label} bar")
    if exit_price is not None:
        body["exit_price"] = float(exit_price)
        body["status"] = status or "closed"
        body.setdefault("closed_at", int(time.time()))
    elif status:
        body["status"] = str(status).lower()
    if fees is not None:
        body["fees"] = float(fees)
    if initial_risk is None and stop is not None and old.get("quantity"):
        initial_risk = abs(old["entry_price"] - float(stop)) * old["quantity"]
        resolved.append(f"risk {round(initial_risk, 2)} from the {stop} stop")
    if initial_risk is not None:
        body["initial_risk"] = float(initial_risk)
    if tags is not None:
        body["tags"] = [str(t) for t in tags]
    # plan and review are the user's own structures: merged over what is
    # there, never replaced, so writing a lesson cannot erase the thesis.
    if plan or stop is not None:
        merged = dict(old.get("plan") or {})
        merged.update(dict(plan or {}))
        if stop is not None:
            merged["stop"] = float(stop)
        body["plan"] = merged
    if review or lesson or adherence is not None or emotion:
        merged = dict(old.get("review") or {})
        merged.update(dict(review or {}))
        if lesson:
            merged["lesson"] = lesson
        if emotion:
            merged["emotion"] = emotion
        if adherence is not None:
            merged["adherence"] = bool(adherence)
        body["review"] = merged
    if len(body) == 1:
        return {"error": "nothing to change",
                "_note": ("Name what changes — the exit, the fees, the stop "
                          "that defines risk, a tag, or the review.")}
    code, out = api_patch(user_id, int(trade_id), body)
    if code >= 400:
        return out
    t = out["trade"]
    return {"trade": t, "_render_hint": "journal_card",
            "_note": (f"Trade {t['id']} updated. "
                      + ("Resolved " + "; ".join(resolved) + ". " if resolved else "")
                      + (f"It is closed: net {t['net_pnl']}"
                         + (f", {t['r_multiple']}R" if t.get("r_multiple") is not None
                            else " — no R-multiple, it carries no initial risk")
                         + ". " if t["status"] == "closed" else "It is still open. ")
                      + "The revision is kept, so the earlier version is not lost.")}
