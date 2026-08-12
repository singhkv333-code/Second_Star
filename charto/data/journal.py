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
