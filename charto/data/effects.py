"""What an order would do, and the laws every order obeys.

Two checks sit between a play and the paper books, and neither knows what
kind of play it is looking at:

- **The effect.** At compile, the play's ticket is worked out against the
  trader's book as it stands, without placing anything: which names change,
  by how much, and which are left alone. The chat model reads it beside the
  trader's words and fixes the play when they differ. Code cannot know that
  "sell half my HAL" meant the portfolio's HAL; the model can, once it sees
  that the order would refuse or sell something else.
- **The laws.** Every ticket, before it is proposed or placed, is reduced to
  plain orders and checked against rules that hold for any play: it trades
  only names the play names, it never sells more than is held, whole shares
  for listed equity and whole lots for F&O, no order above the play's limit,
  and a rebalance moves each name toward its target, never away. A broken
  law is a bug in the code that built the ticket, so the ticket is refused
  and logged rather than placed.

The effect also lists any field of `do` the ticket never read: a size the
code ignored is an instruction silently dropped, and the model is told so.
"""
from __future__ import annotations

import logging
import math

log = logging.getLogger("effects")

ds = None
_paper = None
customsec = None


def bind(module, paper_mod, customsec_mod) -> None:
    global ds, _paper, customsec
    ds, _paper, customsec = module, paper_mod, customsec_mod


# ── reading what the ticket path used ───────────────────────────────────────

class Seen(dict):
    """A `do` that remembers which of its fields were read."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.read: set[str] = set()

    def __getitem__(self, k):
        self.read.add(k)
        return super().__getitem__(k)

    def get(self, k, default=None):
        self.read.add(k)
        return super().get(k, default)

    def __contains__(self, k):
        self.read.add(k)
        return super().__contains__(k)


def derive(do: dict, **over) -> dict:
    """A copy of `do` with some fields changed whose reads still count as
    reads of the original."""
    if isinstance(do, Seen):
        d = Seen({**dict(do.items()), **over})
        d.read = do.read
        return d
    return {**do, **over}


def unread(seen: Seen) -> list[str]:
    """Fields that hold a value the ticket never looked at."""
    return sorted(k for k, v in seen.items() if k not in seen.read and v not in (None, "", [], {}, False))


# ── tickets as plain orders ─────────────────────────────────────────────────

def _crypto(sym: str) -> bool:
    return sym.endswith(("USDT", "USD", "BTC")) or sym.endswith("-PERP")


def orders(t: dict) -> list[dict]:
    """Any ticket shape as [{book, symbol, under, side, qty, price, lot}].
    side is BUY or SELL for the delivery book; LONG, SHORT or CLOSE for the
    day book."""
    out = []
    if t.get("book") == "day":
        for lg in t.get("legs") or []:
            side = "CLOSE" if t.get("close") else {"long": "LONG", "buy": "LONG", "short": "SHORT",
                                                   "sell": "SHORT"}.get(str(lg.get("side")).lower(), "?")
            out.append({"book": "day", "symbol": lg["symbol"], "under": lg.get("underlying") or "",
                        "open": lg.get("open"), "held_side": str(lg.get("side") or "").lower(),
                        "side": side, "qty": float(lg.get("qty") or 0), "price": lg.get("px"),
                        "lot": int(lg.get("lot") or 1), "kind": lg.get("kind") or "",
                        "fx": float(lg.get("fx") or 1.0)})
        return out
    legs = t.get("legs") if (t.get("basket") or t.get("rebalance")) else [t]
    for lg in legs or []:
        out.append({"book": "delivery", "symbol": lg["symbol"], "under": "", "side": lg["side"],
                    "qty": float(lg.get("qty") or 0), "price": lg.get("price"), "lot": 1,
                    "kind": "crypto" if _crypto(lg["symbol"]) else "equity", "fx": 1.0})
    return out


# ── the laws ────────────────────────────────────────────────────────────────

def holdings(uid: int) -> dict[str, float]:
    if _paper is None:
        return {}
    try:
        rows = _paper.api_holdings(uid)[1]
    except Exception:                                       # noqa: BLE001
        return {}
    out: dict[str, float] = {}
    for r in rows or []:
        out[r["symbol"]] = out.get(r["symbol"], 0.0) + float(r.get("quantity") or 0)
    return {s: q for s, q in out.items() if q}


def scope(play: dict, held: dict[str, float] | None = None) -> set[str]:
    """Every name the play may trade: the names its spec gives, the members of
    a security it names, and what it already holds. A planner that rebalances
    the whole portfolio may trade anything in it."""
    spec, do = play["spec"], play["spec"]["do"]
    names = {spec.get("symbol"), do.get("symbol"), do.get("underlying"), spec.get("member"),
             *((spec.get("universe") or {}).get("symbols") or []), *(do.get("names") or []),
             *(do.get("targets") or {}), *(do.get("universe") or []), *(play.get("held_legs") or {})}
    if play.get("held_sym") and play.get("held_qty"):
        names.add(play["held_sym"])
    if do.get("kind") == "rebalance" and do.get("of") == "portfolio" and \
            spec["contract"].get("discretion") == "plan":
        names |= set(held or {})
    names = {str(s) for s in names if s and s not in ("*", "rebalance")}
    for s in list(names):
        if customsec is not None and customsec.is_custom(s):
            try:
                names |= set(customsec.members(s))
            except Exception:                               # noqa: BLE001
                pass
    return names


def laws(play: dict, t: dict, held: dict[str, float] | None = None) -> list[str]:
    """What is wrong with this ticket, in words; [] when nothing is."""
    if held is None:
        held = holdings(play["user_id"])
    od = orders(t)
    bad = []
    allowed = scope(play, held)
    m = play["spec"].get("mandate") or {}
    selling: dict[str, float] = {}
    for o in od:
        name = o["symbol"]
        if name not in allowed and o["under"] not in allowed:
            bad.append(f"{o['side'].lower()} {name} is outside the names this play trades "
                       f"({', '.join(sorted(allowed)) or 'none'})")
        q = o["qty"]
        if not (q > 0 and math.isfinite(q)):
            bad.append(f"{name}: quantity {q:g} is not a positive number")
            continue
        if o["kind"] == "equity" and abs(q - round(q)) > 1e-9:
            bad.append(f"{name}: {q:g} is not a whole number of shares")
        if o["lot"] > 1 and abs(q / o["lot"] - round(q / o["lot"])) > 1e-9:
            bad.append(f"{name}: {q:g} is not a whole number of lots of {o['lot']}")
        if o["side"] == "SELL":
            selling[name] = selling.get(name, 0.0) + q
        if o["book"] == "delivery" and o["side"] == "BUY" and o["price"] and m.get("max_order_inr"):
            inr = q * float(o["price"])
            if inr > float(m["max_order_inr"]) * 1.0001 + 1:
                bad.append(f"buy of ₹{inr:,.0f} {name} is above the play's ₹{m['max_order_inr']:,.0f} "
                           f"order limit")
    for name, q in selling.items():
        if q > held.get(name, 0.0) + 1e-9:
            bad.append(f"sells {q:g} {name} and the paper portfolio holds {held.get(name, 0.0):g}")
    if t.get("rebalance"):
        before, targets = t.get("before") or {}, t.get("targets") or {}
        for o in od:
            w0, w1 = before.get(o["symbol"], 0.0), targets.get(o["symbol"], 0.0)
            if o["side"] == "SELL" and w1 > w0 + 1e-6:
                bad.append(f"sells {o['symbol']} though it is under its target ({w0:.1%} → {w1:.1%})")
            if o["side"] == "BUY" and w1 < w0 - 1e-6:
                bad.append(f"buys {o['symbol']} though it is over its target ({w0:.1%} → {w1:.1%})")
    return bad


def vet(play: dict, t: dict) -> str:
    """'' when the ticket may go ahead, else the reason it may not. A broken
    law is logged as the bug it is."""
    try:
        bad = laws(play, t)
    except Exception as exc:                                # noqa: BLE001
        # a check that could not run has not passed: nothing is placed on it
        log.exception("effects: could not check play %s", play.get("id"))
        return f"a safety check could not run ({type(exc).__name__}), so nothing was placed"
    if bad:
        log.error("effects: play %s ticket broke a law: %s", play.get("id"), "; ".join(bad))
        return "a safety check refused it: " + "; ".join(bad)
    return ""


# ── the effect, in words ────────────────────────────────────────────────────

def _q(x: float) -> str:
    return f"{x:,.6f}".rstrip("0").rstrip(".") if abs(x - round(x)) > 1e-9 else f"{x:,.0f}"


def describe(t: dict, held: dict[str, float]) -> dict:
    """{orders: [words], changes: {sym: [before, after]}, untouched: [...]}"""
    od = orders(t)
    words, changes = [], {}
    for o in od:
        px = o["price"]
        inr = f" (about ₹{o['qty'] * float(px) * o['fx']:,.0f})" if px else ""
        name = o["symbol"]
        if o["book"] == "delivery":
            have = held.get(name, 0.0)
            after = have + (o["qty"] if o["side"] == "BUY" else -o["qty"])
            changes[name] = [have, after]
            of = f" of the {_q(have)} held" if o["side"] == "SELL" else ""
            words.append(f"{o['side'].lower()} {_q(o['qty'])} {name}{of}{inr}; holding {_q(have)} → {_q(after)}")
        elif o["side"] == "CLOSE":
            have = o["open"] if o["open"] is not None else o["qty"]
            verb = "buy back" if o["held_side"] in ("short", "sell") else "sell"
            words.append(f"{verb} {_q(o['qty'])} of the {_q(have)} {name} held {o['held_side']} in the day "
                         f"book; position {_q(have)} → {_q(have - o['qty'])}")
        else:
            words.append(f"{'go long' if o['side'] == 'LONG' else 'go short'} {_q(o['qty'])} {name} in the "
                         f"day book{inr}")
    touched = {o["symbol"] for o in od} | {o["under"] for o in od if o["under"]}
    return {"orders": words, "changes": changes,
            "untouched": sorted(s for s in held if s not in touched)}


def preview(play: dict, ticket_fn, price: float) -> dict:
    """What the play would place if it fired now, against the trader's book
    as it stands. Nothing is placed."""
    seen = Seen(play["spec"]["do"])
    probe = {**play, "spec": {**play["spec"], "do": seen}}
    held = holdings(play["user_id"])
    try:
        t, why = ticket_fn(probe, price)
    except Exception as exc:                                # noqa: BLE001
        log.warning("effects: preview failed for play %s", play.get("id"), exc_info=True)
        return {"refused": f"could not work out the order ({type(exc).__name__}: {exc})"}
    out: dict = {}
    if not t:
        out["refused"] = why
    elif t.get("rebalance") and not t.get("legs"):
        out["orders"] = []
        out["note"] = "every name is within its band of the target, so it would trade nothing now"
    else:
        out.update(describe(t, held))
        bad = laws(play, t, held)
        if bad:
            out["broken"] = bad
    skipped = unread(seen) if t else []
    if skipped:
        out["ignored"] = skipped
    if len(out.get("untouched") or []) > 12:
        out["untouched"] = out["untouched"][:12] + [f"and {len(out['untouched']) - 12} more"]
    return out
