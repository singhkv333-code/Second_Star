"""Shared setups — a chart desk published read-only, and copied as a template.

WHAT A SETUP IS
---------------
A frozen SNAPSHOT of one desk at the moment it was published: the pane grid,
each pane's symbol and interval, the drawings, the chat's scene, the indicator
set, the volume profile — plus, only when the author asks for it, the
conversation that produced them. It is the research a trader wants to hand to
someone else, as they had it on screen.

WHY A SNAPSHOT AND NOT A LIVE LINK
----------------------------------
`layouts.share_token` (dataserver.py) is a LIVE link: it always shows the
layout's current state, so an idea someone read on Monday can silently become
a different idea by Friday. Published research has to hold still to be worth
reading, which is how TradingView ideas and Figma's community files both work:
what you publish is what people see, and re-publishing is a deliberate act
(`update`) that keeps the same link and counters.

WHAT A VIEWER GETS, AND WHAT THEY NEVER GET
-------------------------------------------
Gets: the snapshot, the title and note, the author's DISPLAY NAME, the dates,
views and copies, and the credit line of every setup this one was built from.
Never: the author's email, their other layouts, their layout id, or any turn
of any conversation they did not choose to include. The shared chat is text
only — no screenshots, no tool panels, no chart-context envelopes.

COPYING ("Make it mine")
------------------------
A copy is an ordinary layout in the viewer's own account: fully editable,
independent of the original (a later re-publish does not reach it), and
carrying a frozen `origin` credit. If that copy is published in turn, its
setup records the chain, so credit survives the original being unpublished.
The author can switch copying off; the link then stays view-only.

SAFETY
------
- Tokens are 18 random bytes (`secrets.token_urlsafe`), unguessable and
  unlisted. Unpublishing DELETES the row, so the link is dead the moment it is
  switched off. Copies already taken are the copier's and stay.
- Every string in a published spec is scrubbed of `<` and `>`. The chart
  renders labels on canvas, but some side panels echo them into markup, and a
  setup is the one place in the app where another person's text is drawn in
  your page, next to your sign-in token.
- Sizes are capped (spec, chat turns, characters) and publishing is rate
  limited per user, so a link cannot be used to push megabytes at viewers.

Bound to dataserver's account connection and lock (`bind`), the same way
entitlements.py is: a copy writes a row into `layouts`, and that must go
through the one connection and the one lock that own that table.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time

_con: sqlite3.Connection | None = None
_lock: threading.Lock | None = None
_free_name = None           # dataserver._layout_free_name (caller holds lock)

SPEC_MAX = 1_500_000        # bytes of JSON; a heavy desk is ~200 KB
THUMB_MAX = 220_000         # same guard as a layout thumbnail
TITLE_MAX, NOTE_MAX = 120, 2_000
CHAT_TURNS, CHAT_CHARS = 60, 6_000
LINEAGE_MAX = 5             # credits kept up the chain
PER_USER = 100              # live setups per account
PUBLISH_PER_HOUR = 30
VIEW_WINDOW_S = 1_800       # one viewer counts once per half hour

_SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_setups (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  token TEXT NOT NULL UNIQUE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  layout_id INTEGER,                 -- owner-side only, never sent to viewers
  title TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  symbol TEXT NOT NULL,
  symbols TEXT NOT NULL DEFAULT '',
  interval TEXT NOT NULL DEFAULT '',
  spec TEXT NOT NULL,
  chat TEXT,                         -- NULL: the conversation was not shared
  thumb TEXT NOT NULL DEFAULT '',
  allow_copy INTEGER NOT NULL DEFAULT 1,
  lineage TEXT NOT NULL DEFAULT '[]',-- frozen credits, nearest first
  created INTEGER NOT NULL,
  updated INTEGER NOT NULL,
  views INTEGER NOT NULL DEFAULT 0,
  copies INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS shared_setups_user
  ON shared_setups(user_id, updated DESC);
-- one row per (setup, copier): `copies` counts people, not clicks
CREATE TABLE IF NOT EXISTS setup_copies (
  setup_id INTEGER NOT NULL REFERENCES shared_setups(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id),
  created INTEGER NOT NULL,
  PRIMARY KEY (setup_id, user_id));
"""

_publish_log: dict[int, list[float]] = {}
_seen: dict[tuple[str, str], float] = {}
_mem_lock = threading.Lock()


def bind(con: sqlite3.Connection, lock: threading.Lock, free_name) -> None:
    """Point this module at the account database and create its tables.

    `layouts.origin` arrives as an ALTER for the reason dataserver gives for
    its own columns: the CREATE there is a no-op on an existing database."""
    global _con, _lock, _free_name
    _con, _lock, _free_name = con, lock, free_name
    with _lock:
        _con.executescript(_SCHEMA)
        try:
            _con.execute("ALTER TABLE layouts ADD COLUMN origin "
                         "TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass                                    # already there
        _con.commit()


# ── cleaning what comes in ────────────────────────────────────────────────

def _scrub(v, depth: int = 0):
    """Every string with its angle brackets removed, recursively. See SAFETY."""
    if depth > 40:
        return None
    if isinstance(v, str):
        return v.replace("<", "").replace(">", "")
    if isinstance(v, list):
        return [_scrub(x, depth + 1) for x in v]
    if isinstance(v, dict):
        return {str(k)[:64]: _scrub(x, depth + 1) for k, x in v.items()}
    return v


def _text(v, cap: int) -> str:
    return " ".join(str(v or "").replace("<", "").replace(">", "")
                    .split()).strip()[:cap]


def _clean_spec(spec) -> tuple[dict | None, str]:
    if not isinstance(spec, dict):
        return None, "a setup needs the chart it shares"
    charts = spec.get("charts")
    if not isinstance(charts, list) or not charts or not isinstance(charts[0], dict) \
            or not str(charts[0].get("symbol") or "").strip():
        return None, "a setup needs at least one chart with a symbol"
    spec = dict(spec)
    spec.pop("chat", None)          # conversations travel only via `chat`
    clean = _scrub(spec)
    if len(json.dumps(clean)) > SPEC_MAX:
        return None, "this desk is too large to share"
    return clean, ""


def _clean_chat(turns) -> list[dict] | None:
    """Text-only turns, capped. None when nothing worth sharing remains."""
    if not isinstance(turns, list):
        return None
    out = []
    for t in turns[-CHAT_TURNS:]:
        if not isinstance(t, dict) or t.get("role") not in ("user", "assistant"):
            continue
        body = str(t.get("content") or "").strip()
        if not body:
            continue
        rec = {"role": t["role"], "content": body[:CHAT_CHARS]}
        if t.get("symbol"):
            rec["symbol"] = _text(t["symbol"], 24).upper()
        if isinstance(t.get("ts"), (int, float)):
            rec["ts"] = int(t["ts"])
        out.append(rec)
    return out or None


def _clean_thumb(v) -> str:
    s = str(v or "")
    if not s.startswith(("data:image/jpeg;base64,", "data:image/png;base64,")):
        return ""
    return s if len(s) <= THUMB_MAX else ""


def _symbols(spec: dict) -> list[str]:
    return list(dict.fromkeys(
        str(c.get("symbol") or "").upper()[:24]
        for c in spec.get("charts") or [] if isinstance(c, dict) and c.get("symbol")))


def _rate_ok(uid: int) -> bool:
    now = time.time()
    with _mem_lock:
        hits = [t for t in _publish_log.get(uid, []) if now - t < 3600]
        if len(hits) >= PUBLISH_PER_HOUR:
            _publish_log[uid] = hits
            return False
        hits.append(now)
        _publish_log[uid] = hits
    return True


def _origin_of(uid: int, layout_id) -> list[dict]:
    """The credit chain a layout carries, if it was copied from a setup."""
    if not layout_id:
        return []
    row = _con.execute("SELECT origin FROM layouts WHERE user_id=? AND id=?",
                       (uid, int(layout_id))).fetchone()
    if not row or not row[0]:
        return []
    try:
        chain = json.loads(row[0])
    except ValueError:
        return []
    return chain if isinstance(chain, list) else []


# ── the author's side ─────────────────────────────────────────────────────

def publish(uid: int, author: str, body: dict) -> tuple[int, dict]:
    """Create a setup, or (with `token`) re-publish one the caller owns."""
    token = str(body.get("token") or "").strip()
    if body.get("delete"):
        return unpublish(uid, token)
    spec, why = _clean_spec(body.get("spec"))
    if spec is None:
        return 400, {"error": why}
    title = _text(body.get("title"), TITLE_MAX)
    if not title:
        return 400, {"error": "give the setup a title"}
    note = str(body.get("note") or "").replace("<", "").replace(">", "")[:NOTE_MAX].strip()
    chat = _clean_chat(body.get("chat")) if body.get("include_chat") else None
    thumb = _clean_thumb(body.get("thumb"))
    allow_copy = 0 if body.get("allow_copy") is False else 1
    syms = _symbols(spec)
    first = (spec.get("charts") or [{}])[0]
    interval = _text(first.get("interval"), 8)
    layout_id = body.get("layout_id")
    layout_id = int(layout_id) if str(layout_id or "").isdigit() else None
    now = int(time.time())
    if not _rate_ok(uid):
        return 429, {"error": "you have published a lot in the last hour; "
                              "try again a little later"}
    with _lock:
        if layout_id and not _con.execute(
                "SELECT 1 FROM layouts WHERE user_id=? AND id=?",
                (uid, layout_id)).fetchone():
            layout_id = None                      # not theirs: don't link it
        if token:
            row = _con.execute("SELECT id FROM shared_setups WHERE token=? AND "
                               "user_id=?", (token, uid)).fetchone()
            if not row:
                return 404, {"error": "no such setup"}
            sets = ("title=?, note=?, symbol=?, symbols=?, interval=?, spec=?, "
                    "chat=?, allow_copy=?, updated=?")
            args: list = [title, note, syms[0], ",".join(syms), interval,
                          json.dumps(spec),
                          json.dumps(chat) if chat else None, allow_copy, now]
            if thumb:
                sets += ", thumb=?"
                args.append(thumb)
            _con.execute(f"UPDATE shared_setups SET {sets} WHERE id=?",
                         (*args, row[0]))
            _con.commit()
            return 200, {"token": token, "updated": now, "created": False}
        live = _con.execute("SELECT COUNT(*) FROM shared_setups WHERE user_id=?",
                            (uid,)).fetchone()[0]
        if live >= PER_USER:
            return 409, {"error": f"you already have {PER_USER} shared setups; "
                                  "unpublish one to share another"}
        lineage = _origin_of(uid, layout_id)[:LINEAGE_MAX]
        token = secrets.token_urlsafe(18)
        _con.execute(
            "INSERT INTO shared_setups (token, user_id, layout_id, title, note, "
            "symbol, symbols, interval, spec, chat, thumb, allow_copy, lineage, "
            "created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (token, uid, layout_id, title, note, syms[0], ",".join(syms),
             interval, json.dumps(spec), json.dumps(chat) if chat else None,
             thumb, allow_copy, json.dumps(lineage), now, now))
        _con.commit()
    return 201, {"token": token, "updated": now, "created": True}


def unpublish(uid: int, token: str) -> tuple[int, dict]:
    with _lock:
        cur = _con.execute("DELETE FROM shared_setups WHERE token=? AND user_id=?",
                           (token, uid))
        _con.commit()
    if not cur.rowcount:
        return 404, {"error": "no such setup"}
    return 200, {"token": token, "deleted": True}


def mine(uid: int, layout_id: int | None = None) -> list[dict]:
    """The author's own setups, newest first — stats, no spec."""
    q = ("SELECT token, title, note, symbol, symbols, interval, created, "
         "updated, views, copies, allow_copy, chat IS NOT NULL, layout_id "
         "FROM shared_setups WHERE user_id=?")
    args: list = [uid]
    if layout_id:
        q += " AND layout_id=?"
        args.append(layout_id)
    with _lock:
        rows = _con.execute(q + " ORDER BY updated DESC", args).fetchall()
    return [{"token": r[0], "title": r[1], "note": r[2], "symbol": r[3],
             "symbols": [s for s in r[4].split(",") if s], "interval": r[5],
             "created": r[6], "updated": r[7], "views": r[8], "copies": r[9],
             "allow_copy": bool(r[10]), "has_chat": bool(r[11]),
             "layout_id": r[12]} for r in rows]


# ── the viewer's side ─────────────────────────────────────────────────────

def _count_view(setup_id: int, token: str, viewers: list[str]) -> bool:
    """One view per person per window. A person is their address AND, when
    signed in, their account: reading signed out and then signing up through
    "Make it mine" reloads the page as the same person, and must not count
    twice."""
    now = time.time()
    with _mem_lock:
        keys = [(token, v) for v in viewers if v]
        seen = any(now - _seen.get(k, 0) < VIEW_WINDOW_S for k in keys)
        for k in keys:
            _seen[k] = now
        if seen:
            return False
        if len(_seen) > 50_000:                     # bounded, oldest out
            for k in sorted(_seen, key=_seen.get)[:10_000]:
                _seen.pop(k, None)
    with _lock:
        _con.execute("UPDATE shared_setups SET views=views+1 WHERE id=?",
                     (setup_id,))
        _con.commit()
    return True


def view(token: str, viewer_uid: int | None, viewer_key: str,
         thumb: bool = False) -> tuple[int, dict]:
    """A setup for anyone holding the link. No account needed."""
    tok = (token or "").strip()
    if len(tok) < 16:
        return 404, {"error": "not found"}
    with _lock:
        r = _con.execute(
            "SELECT s.id, s.title, s.note, s.symbol, s.symbols, s.interval, "
            "s.spec, s.chat, s.allow_copy, s.lineage, s.created, s.updated, "
            "s.views, s.copies, s.user_id, u.name, s.thumb "
            "FROM shared_setups s JOIN users u ON u.id = s.user_id "
            "WHERE s.token=?", (tok,)).fetchone()
    if not r:
        return 404, {"error": "this setup is no longer shared"}
    own = viewer_uid == r[14]
    counted = (not own) and _count_view(
        r[0], tok, [viewer_key] + ([f"u{viewer_uid}"] if viewer_uid else []))
    try:
        spec = json.loads(r[6])
        chat = json.loads(r[7]) if r[7] else None
        lineage = json.loads(r[9] or "[]")
    except ValueError:
        return 500, {"error": "this setup's saved state is unreadable"}
    out = {"token": tok, "title": r[1], "note": r[2], "symbol": r[3],
           "symbols": [s for s in r[4].split(",") if s], "interval": r[5],
           "spec": spec, "chat": chat, "allow_copy": bool(r[8]),
           "lineage": lineage, "created": r[10], "updated": r[11],
           # this visit included: the row was read before it was counted
           "views": r[12] + (1 if counted else 0),
           "copies": r[13], "by": (r[15] or "").strip() or "A Pivot trader",
           "own": own, "read_only": True}
    if thumb:
        out["thumb"] = r[16]
    return 200, out


def copy(uid: int, token: str) -> tuple[int, dict]:
    """Make it mine: the setup becomes a new, editable layout of the caller's.

    The conversation, when one was shared, is handed back for the client to
    file as a new conversation of the viewer's — the chat archive lives in the
    browser (chat.js), and the layout points at it by `chat_id`."""
    tok = (token or "").strip()
    now = int(time.time())
    with _lock:
        r = _con.execute(
            "SELECT s.id, s.title, s.symbols, s.spec, s.chat, s.thumb, "
            "s.allow_copy, s.lineage, s.user_id, u.name FROM shared_setups s "
            "JOIN users u ON u.id = s.user_id WHERE s.token=?", (tok,)).fetchone()
        if not r:
            return 404, {"error": "this setup is no longer shared"}
        if not r[6] and r[8] != uid:
            return 403, {"error": "the author has made this setup view-only"}
        chat = json.loads(r[4]) if r[4] else None
        chat_id = f"c{secrets.token_hex(6)}" if chat else ""
        try:
            prior = json.loads(r[7] or "[]")
        except ValueError:
            prior = []
        credit = {"token": tok, "title": r[1],
                  "by": (r[9] or "").strip() or "A Pivot trader",
                  "copied": now}
        origin = json.dumps(([credit] + prior)[:LINEAGE_MAX])
        name = _free_name(uid, r[1])
        _con.execute(
            "INSERT INTO layouts (user_id, name, spec, symbols, created, updated, "
            "opened, autosave, chat_id, thumb, origin) "
            # autosave ON: the point of a copy is to keep working on it, and
            # a template whose edits vanish on reload would betray that
            "VALUES (?,?,?,?,?,?,?,1,?,?,?)",
            (uid, name, r[3], r[2], now, now, now, chat_id, r[5], origin))
        lid = _con.execute("SELECT id FROM layouts WHERE user_id=? AND name=?",
                           (uid, name)).fetchone()[0]
        if r[8] != uid:
            ins = _con.execute("INSERT OR IGNORE INTO setup_copies VALUES (?,?,?)",
                               (r[0], uid, now))
            if ins.rowcount:
                _con.execute("UPDATE shared_setups SET copies=copies+1 WHERE id=?",
                             (r[0],))
        _con.commit()
    return 200, {"layout_id": lid, "name": name, "chat_id": chat_id,
                 "chat": chat, "symbol": (r[2].split(",") or [""])[0],
                 "origin": json.loads(origin)}
