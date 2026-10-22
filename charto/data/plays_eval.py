"""Plays, judged by what reaches the paper books.

Each case seeds a paper book, sends one trader message through the live chat,
fires every play the turn built on its latest bar (confirming any proposal,
as the trader's tap would), and compares the books afterwards with what the
message asked for. The check is the end state, not the wording of the reply
or the tool calls on the way: a play that said the right thing and sold the
wrong name fails, and one that took a different route to the right book
passes. Expectations are tolerances on the book (which names change, by how
much, which stay as they were), so the model keeps its freedom in how it
builds.

    python3.11 plays_eval.py --token <file> [--only 0,3 | --tag said] [--k 2]

Cases carry tags naming the kind of error they probe ("said": a number the
trader gave reaches the order), so a fix is checked against its own cases
rather than by re-running everything.

The server must run with CHARTO_PLAYS=1 on the same CHARTO_USERS_DB and
CHARTO_EVENTS_DB as this process (they are read from the environment). --k
runs every case k times and reports pass^k, the share that passed every time.
Each line carries the triad: seconds, input tokens, and the verdict with what
differed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

os.environ.setdefault("CHARTO_PLAYS", "0")      # this process fires by hand; the server runs the loop

CASES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plays_eval.json")


def _chat(base: str, token: str, msg: str, tag: str) -> dict:
    body = {"messages": [{"role": "user", "content": msg}], "stream": True, "harness": "v2", "plays": True,
            "chat_id": f"plays-eval-{tag}-{int(time.time() * 1000)}", "context": {"surface": "plays"}}
    req = urllib.request.Request(base + "/chat", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
    t0, text, tools, done = time.time(), "", [], {}
    with urllib.request.urlopen(req, timeout=600) as r:
        buf = b""
        for chunk in iter(lambda: r.read(1024), b""):
            buf += chunk
            while b"\n\n" in buf:
                ev, buf = buf.split(b"\n\n", 1)
                for line in ev.decode().split("\n"):
                    if not line.startswith("data:"):
                        continue
                    d = json.loads(line[5:])
                    if d.get("type") == "tool_start":
                        tools.append(d.get("name"))
                    elif d.get("type") == "delta":
                        text += d.get("text", "")
                    elif d.get("type") == "done":
                        done = d
    u = done.get("usage") or {}
    return {"secs": round(time.time() - t0, 1), "in": u.get("input_tokens"), "out": u.get("output_tokens"),
            "rounds": done.get("rounds"), "tools": tools, "reply": text}


class Books:
    """The two paper books of one account, read and set from this process."""

    def __init__(self, uid: int):
        import dataserver as ds
        import daybook
        import paper
        import plays
        plays.bind(ds, None, paper)
        daybook.bind(ds)
        daybook.on_event = plays._day_event
        ds._req.user = (uid, "")
        self.ds, self.paper, self.daybook, self.plays, self.uid = ds, paper, daybook, plays, uid

    def held(self) -> dict[str, float]:
        return {r["symbol"]: float(r["quantity"]) for r in self.paper.api_holdings(self.uid)[1]
                if float(r["quantity"])}

    def day(self) -> list[dict]:
        return [{"symbol": p["symbol"], "side": p["side"], "qty": p["qty"], "stop": p["stop_px"],
                 "target": p["target_px"]} for p in self.daybook.open_positions(self.uid)]

    def seed(self, case: dict) -> None:
        for p in self.daybook.open_positions(self.uid):
            self.daybook.close(p, p["mark_px"] or p["entry_px"], "eval reset", notify=False)
        # each case starts with no plays: the model must build, not edit a
        # play an earlier case left behind (this is a throwaway eval account)
        for (pid,) in self.ds._users.execute("SELECT id FROM plays WHERE user_id=?", (self.uid,)).fetchall():
            self.plays.act(self.uid, pid, "delete")
        want = case.get("book") or {}
        for sym, q in self.held().items():
            d = q - want.get(sym, 0)
            if d > 0:
                self.paper.place_order(self.uid, sym, "SELL", d, source="eval")
        for sym, q in want.items():
            d = q - self.held().get(sym, 0)
            if d > 0:
                self.paper.place_order(self.uid, sym, "BUY", d, source="eval")
        for pos in case.get("day") or []:
            legs = self.daybook.resolve({"kind": pos["side"], "symbol": pos["symbol"],
                                         "size": {"qty": pos["qty"]}}, "MIS")
            self.daybook.open_group(self.uid, None, legs, {})

    def last_play(self) -> int:
        r = self.ds._users.execute("SELECT COALESCE(MAX(id), 0) FROM plays WHERE user_id=?", (self.uid,)).fetchone()
        return int(r[0])

    def fire_new(self, after: int, force: bool = False) -> list[dict]:
        """What the plays built after `after` did, with their proposals
        confirmed as the trader's tap would. A play arms from the latest bar,
        so only what it did at once counts; `force` also fires each
        conditional play on its latest bar, to test the order it would place."""
        out = []
        rows = self.ds._users.execute("SELECT id FROM plays WHERE user_id=? AND id>? ORDER BY id",
                                      (self.uid, after)).fetchall()
        for (pid,) in rows:
            play = self.plays._load(pid)
            spec = play["spec"]
            seen = [e for e in self.plays.events(self.uid, 200) if e.get("play_id") == pid]
            if force and not seen and play["state"] == "armed" and not spec.get("on"):
                data, _ = self.plays.frame(spec)
                series, _ = self.plays.run_measures(spec, data)
                self.plays._fire(play, data, series, len(data["t"]) - 1, late=False)
                seen = [e for e in self.plays.events(self.uid, 200) if e.get("play_id") == pid]
            if not seen:
                out.append({"play": pid, "kind": spec["do"]["kind"], "event": "armed, nothing yet", "why": ""})
            for e in seen:
                if e["kind"] == "proposed" and e.get("status") == "open":
                    try:
                        got = self.plays.resolve_event(self.uid, e["id"], "confirm")["result"]
                        e = {**got, "kind": f"proposed, confirmed → {got['kind']}"}
                    except self.plays.Bad as exc:
                        e = {**e, "kind": "confirm refused", "why": str(exc)}
                out.append({"play": pid, "kind": spec["do"]["kind"], "event": e["kind"],
                            "why": (e.get("body") or {}).get("reason") or e.get("why") or ""})
        return out


def judge(case: dict, before: dict, after: dict, day: list[dict], built: list[dict]) -> list[str]:
    """What differs from the expectation, in words; [] is a pass."""
    exp, bad = case["expect"], []
    names = set(before) | set(after)
    for sym, want in (exp.get("changes") or {}).items():
        d = after.get(sym, 0) - before.get(sym, 0)
        lo, hi = (want, want) if isinstance(want, (int, float)) else want
        if not lo - 1e-9 <= d <= hi + 1e-9:
            bad.append(f"{sym} moved {d:+g}, expected {want}")
    for sym in exp.get("untouched") or []:
        if abs(after.get(sym, 0) - before.get(sym, 0)) > 1e-9:
            bad.append(f"{sym} was not named and moved {before.get(sym, 0):g} → {after.get(sym, 0):g}")
    if exp.get("only_changes"):
        moved = {s for s in names if abs(after.get(s, 0) - before.get(s, 0)) > 1e-9}
        extra = moved - set(exp.get("changes") or {})
        if extra:
            bad.append(f"also moved {', '.join(sorted(extra))}")
    if exp.get("weights"):
        px = {s: _px(s) for s in exp["weights"]}
        tot = sum(after.get(s, 0) * px[s] for s in px)
        for s, (lo, hi) in exp["weights"].items():
            w = after.get(s, 0) * px[s] / tot if tot else 0
            if not lo <= w <= hi:
                bad.append(f"{s} ends at {w:.1%}, expected {lo:.0%}–{hi:.0%}")
    for want in exp.get("day") or []:
        hit = [p for p in day if p["symbol"] == want["symbol"] and p["side"] == want["side"]]
        q = sum(p["qty"] for p in hit)
        lo, hi = want.get("qty", [1e-9, 1e18]) if isinstance(want.get("qty"), list) else \
            (want.get("qty", 1e-9), want.get("qty", 1e18))
        if not hit or not lo <= q <= hi:
            bad.append(f"day book holds {q:g} {want['side']} {want['symbol']}, expected {want.get('qty', 'some')}")
        for k in ("stop", "target"):
            if want.get(k) and not all(p[k] for p in hit):
                bad.append(f"the {want['symbol']} {want['side']} has no {k}")
    if exp.get("no_day") and day:
        bad.append(f"opened {len(day)} day position(s)")
    if exp.get("kinds"):
        got = sorted({b["kind"] for b in built})
        if got != sorted(exp["kinds"]):
            bad.append(f"built {got or 'nothing'}, expected {sorted(exp['kinds'])}")
    if exp.get("refused") and built and any(b["event"].endswith(("filled", "exited")) for b in built):
        bad.append("expected an honest refusal and it traded")
    return bad


def _px(sym: str) -> float:
    import paper
    return float(paper.mark_price(sym) or 0)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:5184")
    ap.add_argument("--token", required=True, help="file holding a bearer token for the eval account")
    ap.add_argument("--uid", type=int, required=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--tag", default="", help="run only the cases with this tag (one kind of error)")
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--cases", default=CASES)
    a = ap.parse_args()
    cases = json.load(open(a.cases))
    pick = [int(x) for x in a.only.split(",") if x] or \
        [i for i, c in enumerate(cases) if not a.tag or a.tag in (c.get("tags") or [])]
    token = open(a.token).read().strip()
    books = Books(a.uid)
    results = {}
    for i in pick:
        case = cases[i]
        for k in range(a.k):
            books.seed(case)
            before, mark = books.held(), books.last_play()
            turn = _chat(a.base, token, case["prompt"], f"{i}-{k}")
            built = books.fire_new(mark, force=bool(case.get("fire")))
            after, day = books.held(), books.day()
            bad = judge(case, before, after, day, built)
            results.setdefault(i, []).append(not bad)
            print(f"[{i}.{k}] {case['prompt']!r}  {turn['secs']}s · in {turn['in']} · out {turn['out']} · "
                  f"rounds {turn['rounds']} · {'PASS' if not bad else 'FAIL'}")
            for b in built:
                print(f"      play {b['play']} {b['kind']} → {b['event']}" + (f" ({b['why'][:160]})" if b['why'] else ""))
            print(f"      book {before} → {after}" + (f" · day {day}" if day else ""))
            for b in bad:
                print(f"      ✗ {b}")
            print("      reply: " + turn["reply"][:400].replace("\n", " | "))
            sys.stdout.flush()
    n = len(results)
    p1 = sum(r[0] for r in results.values())
    pk = sum(all(r) for r in results.values())
    print(f"\npass^1 {p1}/{n}" + (f" · pass^{a.k} {pk}/{n}" if a.k > 1 else ""))


if __name__ == "__main__":
    main()
