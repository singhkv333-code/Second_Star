"""Plans — the registration unit for anything the builder researched.

THE ONE IDEA IN THIS FILE
-------------------------
A model is agentic at BUILD time and must not be at RUN time. So the turn that
screens a universe, reads the names, weighs them and decides is allowed to be
as agentic as it likes — and what it hands over is a frozen manifest of legs
that a dumb loop executes. The judgement is spent once, recorded, and never
re-litigated by a model holding real (simulated) money.

`strategies.py` already registers ONE shape: a single symbol, one condition
tree, one order. That shape cannot hold what people actually ask for —

    "buy the top 10 stocks"
    "get me the best AI stock for next month and buy it"
    "put 3 lakh into quality midcaps, and watch INFY for a breakout"

— because each of those is SEVERAL legs, most of them with no condition at
all, and `parse_draft` refuses a draft with no `trigger.compound` in it. The
builder can already EMIT those (Pivot's step registry carries `trigger.manual`
and `action.allocate_basket`); Charto simply had nowhere to put them.

A plan is that place. It holds legs; a leg is either

  **immediate** — no entry tree. It fills once, at activation, at the mark.
  **conditional** — an entry tree. At activation it becomes a `strategies` row
    and joins the tick runtime unchanged, carrying `plan_id` for provenance.

WHY QUANTITY IS NOT THE MODEL'S ARITHMETIC
------------------------------------------
A leg may arrive as a weight (`weight_pct`) or a rupee slice (`notional_inr`)
rather than a share count. Resolving that to shares is a division by a live
price, and the house rule is that the model reads and the code computes. So
the model states the SHAPE of the allocation and this module does the
arithmetic against `paper.mark_price` at activation — not at registration,
because the price at the moment the user presses is the price that fills.

REGISTERING IS NOT FILLING
--------------------------
`register()` stores a plan in `draft`. Nothing moves. `activate()` is a
separate, user-pressed act, and it is the only function here that spends
anything. That separation is the whole safety model of this file: a research
turn can end in a registered plan without a turn of the conversation having
moved money, and the user sees the manifest before it does.

A plan is never partially armed silently. `activate()` runs every leg,
records what each one did, and reports the failures by name — a basket where
three of ten legs had no price is a plan with seven filled legs and three
named refusals, not a success and not an exception.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import dataserver as ds
import paper
import strategies

log = logging.getLogger("charto.plans")

STATES = ("draft", "active", "retired")
LEG_STATES = ("pending", "filled", "armed", "rejected")

# A plan is a manifest, not a portfolio construction engine. Twenty legs is
# already more than anyone reads on a card, and it is the same ceiling Pivot's
# own `action.allocate_basket` sets on the other side of the seam.
MAX_LEGS = 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id  INTEGER NOT NULL REFERENCES users(id),
  name     TEXT NOT NULL,
  intent   TEXT NOT NULL DEFAULT '',   -- the user's ask, verbatim
  rationale TEXT NOT NULL DEFAULT '',  -- why THIS plan, in the model's words
  capital  REAL,                       -- rupees the model chose to deploy
  spec     TEXT NOT NULL DEFAULT '{}', -- assumptions, evidence, horizon
  state    TEXT NOT NULL DEFAULT 'draft',
  chat_id  TEXT NOT NULL DEFAULT '',
  last_error TEXT NOT NULL DEFAULT '',
  created  INTEGER NOT NULL,
  updated  INTEGER NOT NULL,
  activated INTEGER);
CREATE INDEX IF NOT EXISTS plan_user ON plans(user_id, state);

CREATE TABLE IF NOT EXISTS plan_legs (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  plan_id  INTEGER NOT NULL,
  user_id  INTEGER NOT NULL,
  seq      INTEGER NOT NULL DEFAULT 0,
  symbol   TEXT NOT NULL,
  side     TEXT NOT NULL DEFAULT 'BUY',
  -- exactly one of these three says how big the leg is
  quantity REAL,
  weight_pct REAL,
  notional REAL,
  why      TEXT NOT NULL DEFAULT '',   -- why this name is in the plan
  interval TEXT NOT NULL DEFAULT '1d',
  entry    TEXT,                       -- DSL tree, or NULL for fill-now
  exit     TEXT,
  state    TEXT NOT NULL DEFAULT 'pending',
  resolved_qty REAL,                   -- what the arithmetic produced
  fill_price REAL,
  order_id TEXT,
  strategy_id INTEGER,                 -- set when a conditional leg is armed
  detail   TEXT NOT NULL DEFAULT '',
  created  INTEGER NOT NULL,
  updated  INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS plan_leg_plan ON plan_legs(plan_id, seq);
"""

# `strategies` predates this file, so the provenance column is added rather
# than declared. A conditional leg's armed row has to be traceable back to the
# plan that reasoned it into existence, or the strategies list becomes a pile
# of rules with no account of where they came from.
_STRATEGY_PLAN_COL = (
    "ALTER TABLE strategies ADD COLUMN plan_id INTEGER NOT NULL DEFAULT 0")

_ready = False


def _db():
    return ds._users


def init_db() -> None:
    global _ready
    if _ready:
        return
    strategies.init_db()          # `strategies` must exist before it is altered
    with ds._users_lock:
        _db().executescript(_SCHEMA)
        try:
            _db().execute(_STRATEGY_PLAN_COL)
        except Exception:                                   # noqa: BLE001
            pass                  # already there — the only expected failure
        _db().commit()
    _ready = True


class Unbuildable(Exception):
    """The plan cannot be registered, and the message says exactly why."""


# ── reading what the model sent ──────────────────────────────────────


def _leg_in(raw: Any, seq: int) -> dict:
    """One leg, validated. Raises `Unbuildable` naming the leg, not the index.

    A leg has to say WHICH instrument and HOW MUCH, and 'how much' may be a
    share count, a weight or a rupee slice — three spellings of one fact, and
    exactly one of them may be present. Two would be a contradiction the
    arithmetic would have to guess its way out of.
    """
    if not isinstance(raw, dict):
        raise Unbuildable(f"leg {seq + 1} is not an object")
    sym = str(raw.get("symbol") or "").upper().strip()
    if not sym:
        raise Unbuildable(f"leg {seq + 1} does not name an instrument")
    side = str(raw.get("side") or "BUY").upper().strip()
    if side != "BUY":
        # Same reason `strategies.parse_draft` refuses one: the book is
        # long-only, and refusing here names the plan rather than an order.
        raise Unbuildable(
            f"{sym} is a {side} leg, and the paper book is long-only — it "
            "holds what it has bought and sells what it holds.")

    sizes = {k: raw.get(k) for k in ("quantity", "weight_pct", "notional_inr")
             if raw.get(k) not in (None, "", 0)}
    if len(sizes) > 1:
        raise Unbuildable(
            f"{sym} carries {' and '.join(sorted(sizes))} — a leg is sized "
            "ONE way. Send a share count, a weight, or a rupee slice.")
    if not sizes:
        raise Unbuildable(
            f"{sym} has no size: give it `quantity`, `weight_pct` or "
            "`notional_inr`.")

    def _num(key):
        if key not in sizes:
            return None
        try:
            v = float(sizes[key])
        except (TypeError, ValueError):
            raise Unbuildable(f"{sym}'s {key} is not a number") from None
        if v <= 0:
            raise Unbuildable(f"{sym}'s {key} must be positive")
        return v

    iv = str(raw.get("interval") or "1d").lower()
    interval = strategies._INTERVALS.get(iv)
    if not interval:
        raise Unbuildable(
            f"{sym} asks for {iv} bars, and Charto folds "
            f"{', '.join(sorted(set(strategies._INTERVALS.values())))}.")

    return {
        "seq": seq, "symbol": sym, "side": side,
        "quantity": _num("quantity"),
        "weight_pct": _num("weight_pct"),
        "notional": _num("notional_inr"),
        "why": str(raw.get("why") or "")[:400],
        "interval": interval,
        "entry": raw.get("entry"), "exit": raw.get("exit"),
    }


def parse(spec: dict) -> dict:
    """A registration payload → the rows that will be written.

    Everything refusable is refused HERE, before a single row exists. A plan
    that half-registers is worse than one that did not: the user is told it
    was stored, and what was stored is not what the card showed.
    """
    legs_raw = spec.get("legs")
    if not isinstance(legs_raw, list) or not legs_raw:
        raise Unbuildable("A plan needs at least one leg to be a plan.")
    if len(legs_raw) > MAX_LEGS:
        raise Unbuildable(
            f"{len(legs_raw)} legs — a plan holds at most {MAX_LEGS}.")

    legs = [_leg_in(r, i) for i, r in enumerate(legs_raw)]

    dupes = sorted({l["symbol"] for l in legs
                    if sum(1 for x in legs if x["symbol"] == l["symbol"]) > 1})
    if dupes:
        raise Unbuildable(
            f"{', '.join(dupes)} appears more than once. One leg per "
            "instrument — combine them into a single weight.")

    weighted = [l for l in legs if l["weight_pct"] is not None]
    capital = spec.get("capital_inr")
    try:
        capital = float(capital) if capital not in (None, "") else None
    except (TypeError, ValueError):
        capital = None
    if capital is not None and capital <= 0:
        capital = None
    if weighted and capital is None:
        raise Unbuildable(
            "The legs are weighted, so the plan needs `capital_inr` to turn "
            "those weights into share counts. Choose an amount and say what "
            "you chose.")
    if weighted:
        total = sum(l["weight_pct"] for l in weighted)
        # Weights are the model's, and a model that emits 9 legs at 11% each
        # has made an arithmetic slip rather than a decision. Wide enough not
        # to nag about rounding, tight enough that a real slip is caught.
        if not 95.0 <= total <= 105.0:
            raise Unbuildable(
                f"The weights total {total:.1f}%, not 100%. Re-weight the "
                "legs so they add up.")

    name = str(spec.get("name") or "").strip()[:160]
    if not name:
        name = f"{len(legs)}-leg plan" if len(legs) > 1 else legs[0]["symbol"]

    return {
        "name": name,
        "intent": str(spec.get("intent") or "")[:1200],
        "rationale": str(spec.get("rationale") or "")[:2000],
        "capital": capital,
        "legs": legs,
        "spec": {
            "assumptions": [str(a)[:300] for a in
                            (spec.get("assumptions") or [])][:12],
            "evidence": [str(e)[:300] for e in (spec.get("evidence") or [])][:12],
            "horizon": str(spec.get("horizon") or "")[:120],
            "review": str(spec.get("review") or "")[:300],
        },
    }


# ── the store ────────────────────────────────────────────────────────

_PLAN_COLS = ("id", "user_id", "name", "intent", "rationale", "capital",
              "spec", "state", "chat_id", "last_error", "created", "updated",
              "activated")
_LEG_COLS = ("id", "plan_id", "user_id", "seq", "symbol", "side", "quantity",
             "weight_pct", "notional", "why", "interval", "entry", "exit",
             "state", "resolved_qty", "fill_price", "order_id", "strategy_id",
             "detail", "created", "updated")


def _leg_public(d: dict) -> dict:
    return {
        "symbol": d["symbol"], "side": d["side"], "why": d["why"],
        "weight_pct": d["weight_pct"], "notional_inr": d["notional"],
        "quantity": (paper._qty_out(d["resolved_qty"])
                     if d["resolved_qty"] is not None
                     else (paper._qty_out(d["quantity"])
                           if d["quantity"] is not None else None)),
        "conditional": bool(d["entry"]),
        "interval": d["interval"],
        "state": d["state"],
        "fill_price": None if d["fill_price"] is None else paper.f(d["fill_price"]),
        "strategy_id": d["strategy_id"],
        "detail": d["detail"],
    }


def _public(p: dict, legs: list[dict]) -> dict:
    try:
        spec = json.loads(p["spec"] or "{}")
    except (TypeError, ValueError):
        spec = {}
    return {
        "id": p["id"], "name": p["name"], "state": p["state"],
        "intent": p["intent"], "rationale": p["rationale"],
        "capital_inr": None if p["capital"] is None else paper.f(p["capital"]),
        "assumptions": spec.get("assumptions") or [],
        "evidence": spec.get("evidence") or [],
        "horizon": spec.get("horizon") or "",
        "review": spec.get("review") or "",
        "legs": [_leg_public(l) for l in legs],
        "last_error": p["last_error"] or "",
        "created": paper._iso(p["created"]),
        "activated": paper._iso(p["activated"]) if p["activated"] else None,
    }


def register(uid: int, spec: dict, *, chat_id: str = "") -> dict:
    """Store a plan. Nothing fills, nothing arms, no money moves.

    The plan lands in `draft` and stays there until someone presses activate.
    That is deliberate and it is the safety model: a turn may research, decide
    and register without a turn of conversation having spent anything.
    """
    init_db()
    parsed = parse(spec)                  # raises Unbuildable with the reason

    # A conditional leg needs the engine that evaluates its tree. Refusing at
    # the door beats storing a rule nothing can ever check.
    if any(l["entry"] for l in parsed["legs"]):
        import execution_bridge
        ready, why = execution_bridge.available()
        if not ready:
            raise Unbuildable(why)

    now = int(time.time())
    with ds._users_lock:
        cur = _db().execute(
            "INSERT INTO plans (user_id, name, intent, rationale, capital, "
            "spec, state, chat_id, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (int(uid), parsed["name"], parsed["intent"], parsed["rationale"],
             parsed["capital"], json.dumps(parsed["spec"]), "draft",
             str(chat_id or "")[:80], now, now))
        pid = cur.lastrowid
        for l in parsed["legs"]:
            _db().execute(
                "INSERT INTO plan_legs (plan_id, user_id, seq, symbol, side, "
                "quantity, weight_pct, notional, why, interval, entry, exit, "
                "state, created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, int(uid), l["seq"], l["symbol"], l["side"], l["quantity"],
                 l["weight_pct"], l["notional"], l["why"], l["interval"],
                 json.dumps(l["entry"]) if l["entry"] else None,
                 json.dumps(l["exit"]) if l["exit"] else None,
                 "pending", now, now))
        _db().commit()
    paper.get_or_create_account(uid)   # the book exists the moment a plan does
    return get(uid, pid)[1]


def _rows(uid: int, pid: int):
    with ds._users_lock:
        p = _db().execute(
            "SELECT %s FROM plans WHERE id=? AND user_id=?" % ", ".join(_PLAN_COLS),
            (int(pid), int(uid))).fetchone()
        if p is None:
            return None, []
        legs = _db().execute(
            "SELECT %s FROM plan_legs WHERE plan_id=? ORDER BY seq"
            % ", ".join(_LEG_COLS), (int(pid),)).fetchall()
    return dict(zip(_PLAN_COLS, p)), [dict(zip(_LEG_COLS, r)) for r in legs]


def get(uid: int, pid: int) -> tuple[int, dict]:
    init_db()
    p, legs = _rows(uid, pid)
    if p is None:
        return 404, {"error": "no such plan"}
    return 200, _public(p, legs)


def api_list(uid: int, state: str = "") -> tuple[int, dict]:
    init_db()
    q = "SELECT %s FROM plans WHERE user_id=?" % ", ".join(_PLAN_COLS)
    args: list = [int(uid)]
    if state:
        q += " AND state=?"; args.append(state)
    else:
        q += " AND state != 'retired'"
    q += " ORDER BY id DESC LIMIT 50"
    with ds._users_lock:
        rows = _db().execute(q, args).fetchall()
    out = []
    for r in rows:
        p = dict(zip(_PLAN_COLS, r))
        with ds._users_lock:
            legs = _db().execute(
                "SELECT %s FROM plan_legs WHERE plan_id=? ORDER BY seq"
                % ", ".join(_LEG_COLS), (p["id"],)).fetchall()
        out.append(_public(p, [dict(zip(_LEG_COLS, x)) for x in legs]))
    return 200, {"plans": out}


def api_delete(uid: int, pid: int) -> tuple[int, dict]:
    """Retire, never erase — a plan that filled orders is their provenance.

    Retiring also pauses every conditional leg it armed. A plan the user has
    put away must not keep trading; leaving its rules armed would be the same
    class of lie as a strategy that says 'saved' and does nothing, inverted.
    """
    init_db()
    p, legs = _rows(uid, pid)
    if p is None:
        return 404, {"error": "no such plan"}
    now = int(time.time())
    with ds._users_lock:
        _db().execute("UPDATE plans SET state='retired', updated=? WHERE id=? "
                      "AND user_id=?", (now, int(pid), int(uid)))
        sids = [l["strategy_id"] for l in legs if l["strategy_id"]]
        if sids:
            _db().execute(
                "UPDATE strategies SET state='paused', updated=? WHERE id IN "
                "(%s) AND user_id=? AND state='armed'"
                % ",".join("?" * len(sids)), [now, *sids, int(uid)])
        _db().commit()
    if sids:
        strategies.load_index()
    return get(uid, pid)


# ── activation: the only thing here that spends ──────────────────────


def _resolve_qty(leg: dict, capital: Optional[float]) -> tuple[Optional[int],
                                                               str]:
    """(shares, why-not). The arithmetic the model is not allowed to do.

    A share count passes through. A weight or a rupee slice is divided by the
    mark AT THIS MOMENT — not at registration — because the price that fills
    is the price when the user pressed, and a quantity computed an hour ago
    against a stale mark is a different order wearing the same number.
    """
    if leg["quantity"] is not None:
        return int(leg["quantity"]), ""
    if leg["notional"] is not None:
        rupees = float(leg["notional"])
    elif leg["weight_pct"] is not None and capital:
        rupees = float(capital) * float(leg["weight_pct"]) / 100.0
    else:
        return None, "the leg has no size the runtime can read"
    px = paper.mark_price(leg["symbol"])
    if px is None:
        return None, f"no price for {leg['symbol']} to size against"
    qty = int(rupees // float(px))
    if qty <= 0:
        return None, (f"₹{rupees:,.0f} does not buy one share of "
                      f"{leg['symbol']} at ₹{float(px):,.2f}")
    return qty, ""


def _leg_done(leg_id: int, **fields) -> None:
    fields["updated"] = int(time.time())
    sets = ", ".join(f"{k}=?" for k in fields)
    with ds._users_lock:
        _db().execute(f"UPDATE plan_legs SET {sets} WHERE id=?",
                      [*fields.values(), int(leg_id)])
        _db().commit()


def activate(uid: int, pid: int) -> tuple[int, dict]:
    """Run every leg once. Immediate legs fill; conditional legs arm.

    Every leg is attempted even after one fails. A basket where three names
    had no price is a plan with seven fills and three named refusals — the
    honest outcome — not an exception that hides the seven.
    """
    init_db()
    p, legs = _rows(uid, pid)
    if p is None:
        return 404, {"error": "no such plan"}
    if p["state"] == "active":
        return 409, {"error": "this plan is already active",
                     "plan": _public(p, legs)}
    if p["state"] == "retired":
        return 409, {"error": "this plan was retired"}

    capital = p["capital"]
    filled, armed, refused = [], [], []

    for leg in legs:
        if leg["state"] in ("filled", "armed"):
            continue
        qty, why = _resolve_qty(leg, capital)
        if qty is None:
            _leg_done(leg["id"], state="rejected", detail=why)
            refused.append({"symbol": leg["symbol"], "reason": why})
            continue

        if leg["entry"]:
            # A conditional leg is a strategy. It is handed to the module that
            # already owns the tick loop rather than re-implemented here, so
            # the rule that runs is still the exact tree the card showed.
            try:
                sid = _arm_leg(uid, pid, leg, qty, p["name"])
            except Exception as exc:                        # noqa: BLE001
                reason = str(exc)[:300]
                _leg_done(leg["id"], state="rejected", detail=reason,
                          resolved_qty=qty)
                refused.append({"symbol": leg["symbol"], "reason": reason})
                continue
            _leg_done(leg["id"], state="armed", resolved_qty=qty,
                      strategy_id=sid, detail="")
            armed.append({"symbol": leg["symbol"], "quantity": qty,
                          "strategy_id": sid})
            continue

        try:
            res = paper.place_order(
                uid, leg["symbol"], "BUY", qty, order_type="MARKET",
                source="plan", origin_kind="plan_leg")
        except paper.Reject as exc:
            _leg_done(leg["id"], state="rejected", detail=exc.reason,
                      resolved_qty=qty)
            refused.append({"symbol": leg["symbol"], "reason": exc.reason})
            continue
        except Exception as exc:                            # noqa: BLE001
            log.exception("plan %s leg %s failed", pid, leg["symbol"])
            reason = str(exc)[:300]
            _leg_done(leg["id"], state="rejected", detail=reason,
                      resolved_qty=qty)
            refused.append({"symbol": leg["symbol"], "reason": reason})
            continue
        fill = res.get("fill_price")
        _leg_done(leg["id"], state="filled", resolved_qty=qty,
                  fill_price=fill, order_id=res.get("order_id"), detail="")
        filled.append({"symbol": leg["symbol"], "quantity": qty,
                       "price": paper.f(fill) if fill is not None else None})

    now = int(time.time())
    # A plan where every leg was refused never became active — saying it did
    # would be the "saved the strategy" lie in a new place.
    became = "active" if (filled or armed) else "draft"
    note = ("" if not refused else
            "; ".join(f"{r['symbol']}: {r['reason']}" for r in refused)[:400])
    with ds._users_lock:
        _db().execute(
            "UPDATE plans SET state=?, activated=?, last_error=?, updated=? "
            "WHERE id=?", (became, now if became == "active" else None,
                           note, now, int(pid)))
        _db().commit()
    if armed:
        strategies.load_index()

    code, body = get(uid, pid)
    body["result"] = {"filled": filled, "armed": armed, "refused": refused}
    return code, body


def _arm_leg(uid: int, pid: int, leg: dict, qty: int, plan_name: str) -> int:
    """One conditional leg → an armed `strategies` row carrying `plan_id`.

    Written directly rather than through `strategies.save()` because save()
    takes a workflow DRAFT and re-parses it; the tree here has already been
    validated and re-serialising it through a draft shape only adds a place
    for it to change meaning.
    """
    now = int(time.time())
    name = f"{plan_name} · {leg['symbol']}"[:160]
    spec = {"description": leg["why"], "readback": leg["why"],
            "source": "plan", "plan_id": pid}
    with ds._users_lock:
        cur = _db().execute(
            "INSERT INTO strategies (user_id, name, symbol, interval, side, "
            "quantity, spec, entry, exit, state, note, chat_id, plan_id, "
            "created, updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (int(uid), name, leg["symbol"], leg["interval"], "BUY", qty,
             json.dumps(spec), leg["entry"], leg["exit"], "armed",
             leg["why"][:400], "", int(pid), now, now))
        sid = cur.lastrowid
        _db().commit()
    return sid


# ── the model's surface ──────────────────────────────────────────────


def _who(user_id: int) -> int:
    return int(user_id or 0)


def tool_register_plan(user_id: int = 0, **spec) -> dict:
    """Freeze a researched plan into a manifest the runtime can execute.

    Returns the plan as stored, plus what activating it would do. It does NOT
    activate: the reply should say what is registered and that the card's
    button starts it.
    """
    uid = _who(user_id)
    if not uid:
        return {"error": "sign_in_required",
                "detail": "A plan is stored against an account. Ask the user "
                          "to sign in, then register it."}
    chat_id = str(getattr(ds._req, "chat_id", "") or "")
    try:
        plan = register(uid, spec, chat_id=chat_id)
    except Unbuildable as exc:
        return {"error": "plan_rejected", "detail": str(exc)}
    except Exception as exc:                                # noqa: BLE001
        log.exception("register_plan failed")
        return {"error": "plan_failed", "detail": str(exc)[:400]}
    n_cond = sum(1 for l in plan["legs"] if l["conditional"])
    plan["_render_hint"] = "plan_card"
    plan["summary"] = {
        "legs": len(plan["legs"]),
        "immediate": len(plan["legs"]) - n_cond,
        "conditional": n_cond,
        "state": "draft",
    }
    plan["next"] = ("Registered, not started. The card's Activate button "
                    "fills the immediate legs at the mark and arms the "
                    "conditional ones against the live tick.")
    return plan


def tool_list_plans(user_id: int = 0, state: str = "") -> dict:
    uid = _who(user_id)
    if not uid:
        return {"plans": [], "detail": "Nothing is stored for a signed-out user."}
    _, body = api_list(uid, state)
    return body


def tool_plan_status(user_id: int = 0, plan_id: int = 0) -> dict:
    uid = _who(user_id)
    if not uid:
        return {"error": "sign_in_required"}
    code, body = get(uid, int(plan_id))
    if code != 200:
        return {"error": "no_such_plan", "detail": f"No plan {plan_id}."}
    return body


def tool_retire_plan(user_id: int = 0, plan_id: int = 0) -> dict:
    uid = _who(user_id)
    if not uid:
        return {"error": "sign_in_required"}
    code, body = api_delete(uid, int(plan_id))
    if code != 200:
        return {"error": "no_such_plan", "detail": f"No plan {plan_id}."}
    return body
