"""Asking the trader to confirm a play's order: by email, and in the app.

Pivot never places a play's order on its own. Each one a play proposes waits
for the trader, who confirms it from the Inbox, from the app's pop-up (which
reads `pending`), or from the email sent the moment it is proposed.

The email's link opens a page showing the order with Confirm and Dismiss; the
link alone changes nothing, because mail scanners open every link in a message.
It is single-purpose (one proposal), expires in LINK_TTL, and is stored hashed.
"""
from __future__ import annotations

import hashlib
import html
import logging
import secrets
import time

import dataserver as ds
import mail_templates
import mailer

log = logging.getLogger("dataserver.play_confirm")
LINK_TTL = 1800
_ready = False


def _db():
    return ds._users


def init_db() -> None:
    global _ready
    if _ready:
        return
    with ds._users_lock:
        _db().execute("CREATE TABLE IF NOT EXISTS play_links (token TEXT PRIMARY KEY, event_id INTEGER NOT NULL, "
                      "user_id INTEGER NOT NULL, expires INTEGER NOT NULL)")
        _db().commit()
    _ready = True


def _key(tok: str) -> str:
    return hashlib.sha256(tok.encode()).hexdigest()


def words(ev: dict, spec: dict) -> str:
    """The order in one line, the way the Inbox says it."""
    import plays
    return "".join(x.get("t") or x.get("b") or "" for x in plays.transcribe(ev, spec)["line"]).strip()


def where(play: dict) -> str:
    if play.get("venue") != "live":
        return "Your paper book (simulated)"
    import brokers
    row = brokers.active_connection(play["user_id"])
    return f"Your {row.broker.title()} account" if row else "Your broker (none is connected)"


def ask(play: dict, ev: dict) -> None:
    """Email the trader a link to confirm this proposal. Never raises."""
    try:
        cfg = mailer.Config.from_env()
        if not cfg.configured:
            return
        init_db()
        with ds._users_lock:
            row = _db().execute("SELECT email FROM users WHERE id=?", (play["user_id"],)).fetchone()
        to = str(row[0] if row else "").strip()
        if not mailer.valid_address(to):
            return
        tok = secrets.token_urlsafe(24)
        with ds._users_lock:
            _db().execute("INSERT INTO play_links VALUES (?,?,?,?)",
                          (_key(tok), ev["id"], play["user_id"], int(time.time()) + LINK_TTL))
            _db().commit()
        mail = mail_templates.order_confirm(order=words(ev, play["spec"]), play=play["spec"]["title"],
                                            where=where(play), reason=str(ev["body"].get("reason") or ""),
                                            url=f"{cfg.origin}/confirm/{tok}", minutes=LINK_TTL // 60,
                                            origin=cfg.origin)
        mailer.send_later(mail, to, cfg, ref=f"pivot-play-{ev['id']}")
    except Exception:                                       # noqa: BLE001
        log.warning("play_confirm: the confirm email was not sent", exc_info=True)


def pending(uid: int) -> list[dict]:
    """Every open proposal, newest first, for the app's pop-up."""
    import plays
    out = []
    for e in plays.events(uid, 120):
        if e["kind"] != "proposed" or e["status"] != "open":
            continue
        p = plays._load(e["play_id"])
        if p:
            out.append({"id": e["id"], "play_id": e["play_id"], "play": p["spec"]["title"], "created": e["created"],
                        "order": words(e, p["spec"]), "where": where(p), "live": p.get("venue") == "live",
                        "reason": (e.get("body") or {}).get("reason") or ""})
    return out


def _link(tok: str) -> tuple[int, int] | None:
    init_db()
    with ds._users_lock:
        r = _db().execute("SELECT user_id, event_id, expires FROM play_links WHERE token=?", (_key(tok),)).fetchone()
    return (r[0], r[1]) if r and r[2] > time.time() else None


def _page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><meta name="robots" content="noindex"><title>{html.escape(title)}'
            '</title><style>body{margin:0;background:#f4f4f2;color:#17191e;font:15px/1.5 -apple-system,Segoe UI,Arial,'
            'sans-serif;display:grid;place-items:center;min-height:100vh}main{width:min(420px,92vw);background:#fff;'
            'border:1px solid #e2e2e5;border-radius:16px;padding:26px}h1{font-size:20px;margin:0 0 6px}.order{font-size:'
            '17px;font-weight:600;margin:16px 0 4px}.muted{color:#6b6e75;font-size:13px}.row{display:flex;gap:10px;margin-top'
            ':22px}button{flex:1;height:42px;border-radius:10px;border:1px solid #d6d6da;background:#fff;font:inherit;'
            'font-weight:600;cursor:pointer}button.go{background:#17191e;color:#fff;border-color:#17191e}</style></head>'
            f'<body><main><div class="muted">Pivot</div>{body}</main></body></html>')


def page(tok: str) -> tuple[int, str]:
    import plays
    got = _link(tok)
    if not got:
        return 410, _page("Link expired", "<h1>This link has expired</h1><p class=muted>Open Pivot to see the order in "
                                          "your Inbox.</p>")
    uid, eid = got
    e = next((x for x in plays.events(uid, 300) if x["id"] == eid), None)
    p = plays._load(e["play_id"]) if e else None
    if not e or not p or e["status"] != "open":
        return 200, _page("Already handled", "<h1>This order was already handled</h1><p class=muted>It was confirmed, "
                                             "dismissed or withdrawn. Open Pivot to see it.</p>")
    esc = html.escape
    return 200, _page("Confirm the order",
                      f"<h1>{esc(p['spec']['title'])}</h1><div class=order>{esc(words(e, p['spec']))}</div>"
                      f"<div class=muted>{esc(where(p))}</div>"
                      f"<form method=post><div class=row><button name=action value=dismiss>Dismiss</button>"
                      f"<button class=go name=action value=confirm>Confirm</button></div></form>"
                      "<p class=muted style=margin-top:18px>Pivot does not place orders on its own. This one is "
                      "placed only if you confirm it.</p>")


def act(tok: str, action: str) -> tuple[int, str]:
    import plays
    got = _link(tok)
    if not got or action not in ("confirm", "dismiss"):
        return page(tok)
    uid, eid = got
    e = next((x for x in plays.events(uid, 300) if x["id"] == eid), None)
    p = plays._load(e["play_id"]) if e else None
    try:
        plays.resolve_event(uid, eid, action)
    except plays.Bad as exc:
        return 409, _page("Not placed", f"<h1>Not placed</h1><p>{html.escape(str(exc))}</p>")
    with ds._users_lock:
        _db().execute("DELETE FROM play_links WHERE event_id=?", (eid,))
        _db().commit()
    placed = ("sent to your broker" if p and p.get("venue") == "live" else "placed in your paper book")
    return 200, _page("Done", f"<h1>Confirmed</h1><p>The order was {placed}. Pivot shows it in your Inbox.</p>"
                      if action == "confirm" else "<h1>Dismissed</h1><p>Nothing was placed.</p>")
