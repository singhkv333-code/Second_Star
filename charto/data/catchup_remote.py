#!/usr/bin/env python3
"""Fetch the minutes a REMOTE store is missing, and ship only those.

The VM cannot do this itself. `charto.service` runs with
CHARTO_LIVE_VENUES=bybit,coinbase, and /data/app/pivot/.env carries two keys
(AZURE_KEY, AZURE_OPENAI_ENDPOINT) — no DATABASE_URL, no Kite credentials. So
the machine that HOLDS the 29 GB store is structurally unable to fetch NSE
data, and the machine with the credentials is not the one that needs the rows.

topup_1min.py already described the way out (its header, lines 81-87) and
nobody had written it:

    seed a throwaway DB with the remote's watermarks (one anchor row per
    symbol), run this script against THAT, and ship only the rows it fetched.

That works because `plan_symbol` reads MAX(ts) and sync_state and nothing
else. Give it a DB whose only content is one anchor row per symbol at the
REMOTE's last-known minute and it plans exactly the windows the remote is
missing — no knowledge of this file required, no second planner to drift.

Measured on 2026-08-03: 500 NSE names, 22 Jul -> 03 Aug, 1.5M bars, shipped
as 20 MB instead of copying 13 GB.

    # 1. on the VM: emit watermarks (see --help-remote for the one-liner)
    # 2. here:
    python catchup_remote.py --watermarks remote_wm.json --out patch.db
    # 3. on the VM:
    python catchup_remote.py --merge patch.db --into /data/charto_bars.db

The anchor rows are DELETED from the patch before it ships. They are a
fiction — a single fabricated bar per symbol carrying the remote's last
timestamp and nothing else — and shipping them back would overwrite a real
bar with a placeholder. They exist only so plan_symbol has a MAX(ts) to read.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
IST = timezone(timedelta(hours=5, minutes=30))

BARS_DDL = ("CREATE TABLE IF NOT EXISTS bars ("
            "symbol TEXT NOT NULL, ts INTEGER NOT NULL, o REAL, h REAL, "
            "l REAL, c REAL, v INTEGER, PRIMARY KEY (symbol, ts))")

REMOTE_ONELINER = r"""
/data/venv/bin/python - <<"PY"
import sqlite3, json, gzip, base64
c = sqlite3.connect("file:/data/charto_bars.db?mode=ro", uri=True)
wm = {s: m for s, m in c.execute("SELECT symbol, MAX(ts) FROM bars GROUP BY 1")}
blob = base64.b64encode(gzip.compress(json.dumps(wm, separators=(",", ":")).encode())).decode()
print("B64START")
for i in range(0, len(blob), 900):
    print(blob[i:i+900])
print("B64END")
PY
"""


def _ist(ts: int) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%d %b %H:%M")


# ── seeding ────────────────────────────────────────────────────────────────

def seed(watermarks: dict[str, int], path: Path) -> sqlite3.Connection:
    """A DB whose whole content is one fabricated bar per symbol.

    The bar's OHLCV is deliberately NULL rather than zero: nothing may ever
    read a price off an anchor, and a NULL that breaks arithmetic loudly is
    safer than a 0.0 that silently prices something at nothing.
    """
    if path.exists():
        path.unlink()
    con = sqlite3.connect(path)
    con.execute(BARS_DDL)
    con.executemany("INSERT INTO bars (symbol, ts) VALUES (?,?)",
                    sorted(watermarks.items()))
    con.commit()
    return con


def strip_anchors(con: sqlite3.Connection, watermarks: dict[str, int]) -> int:
    """Remove the fabricated rows, but only where the fetch did not replace them.

    A real fetch usually overwrites its own anchor minute — the window starts
    at or before it — and that row is genuine data the remote should get. So
    the delete is conditional on the row STILL being the placeholder (o IS
    NULL), never a blanket delete by timestamp.
    """
    n = 0
    for sym, ts in watermarks.items():
        n += con.execute(
            "DELETE FROM bars WHERE symbol=? AND ts=? AND o IS NULL",
            (sym, ts)).rowcount
    con.commit()
    return n


# ── merge (runs on the remote) ─────────────────────────────────────────────

def merge(patch: Path, into: Path) -> dict:
    """INSERT OR REPLACE the patch into the live store.

    OR REPLACE is the same contract every writer in this tree uses, and it is
    what makes the overlap-never-abut rule safe: re-sent minutes repair a
    truncated tail in place instead of colliding with it.
    """
    if not patch.exists():
        raise SystemExit(f"no such patch: {patch}")
    con = sqlite3.connect(into)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(BARS_DDL)
    con.execute("ATTACH DATABASE ? AS p", (str(patch),))
    before = con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    incoming = con.execute("SELECT COUNT(*) FROM p.bars").fetchone()[0]
    # A NULL open in the patch is an anchor that escaped strip_anchors. It
    # would overwrite a real bar with a placeholder, so it is refused here as
    # well as there — the cost is one predicate, the cost of missing it is a
    # hole that looks like data.
    con.execute("INSERT OR REPLACE INTO bars (symbol, ts, o, h, l, c, v) "
                "SELECT symbol, ts, o, h, l, c, v FROM p.bars WHERE o IS NOT NULL")
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    skipped = con.execute("SELECT COUNT(*) FROM p.bars WHERE o IS NULL").fetchone()[0]
    con.execute("DETACH DATABASE p")
    con.close()
    return {"incoming": incoming, "anchors_refused": skipped,
            "new_rows": after - before, "replaced": incoming - skipped - (after - before)}


# ── main ───────────────────────────────────────────────────────────────────

def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watermarks", type=Path, help="JSON {symbol: last_ts} from the remote")
    ap.add_argument("--out", type=Path, default=_HERE / "patch.db")
    ap.add_argument("--symbols", default="", help="comma list; default every symbol in the file")
    ap.add_argument("--workers", type=int, default=3, help="topup's measured sweet spot")
    ap.add_argument("--plan-only", action="store_true", help="show the windows, fetch nothing")
    ap.add_argument("--merge", type=Path, help="remote side: patch to merge")
    ap.add_argument("--into", type=Path, default=Path("/data/charto_bars.db"))
    ap.add_argument("--help-remote", action="store_true", help="print the watermark one-liner")
    a = ap.parse_args(argv)

    if a.help_remote:
        print(REMOTE_ONELINER.strip())
        return 0

    if a.merge:
        r = merge(a.merge, a.into)
        print(f"merged {a.merge} -> {a.into}")
        print(f"  incoming        {r['incoming']:,}")
        print(f"  new rows        {r['new_rows']:,}")
        print(f"  replaced        {r['replaced']:,}")
        if r["anchors_refused"]:
            print(f"  anchors refused {r['anchors_refused']:,}  (placeholder rows, not data)")
        return 0

    if not a.watermarks:
        ap.error("--watermarks is required (or --merge / --help-remote)")

    wm: dict[str, int] = json.loads(a.watermarks.read_text())
    if a.symbols:
        want = {s.strip().upper() for s in a.symbols.split(",") if s.strip()}
        wm = {k: v for k, v in wm.items() if k.upper() in want}
    if not wm:
        print("no symbols selected")
        return 1

    seed_path = a.out.with_suffix(".seed.db")
    seed(wm, seed_path)
    # topup reads its store from CHARTO_DB at import time, so this must be set
    # BEFORE the import, not before the call.
    os.environ["CHARTO_DB"] = str(seed_path)
    sys.path.insert(0, str(_HERE))
    import topup_1min as T                                   # noqa: E402

    # Two fetchers, because there are two instrument shapes and one of them
    # topup cannot see. topup_1min is scoped to NSE EQUITIES; the indices, the
    # four INR pairs and the nine MCX metals resolve through a different
    # segment of the instrument master and live in backfill_macro. Routed by
    # backfill_macro's own name lists rather than a second guess at what
    # "macro" means, so the split cannot drift from the script that owns it.
    import backfill_macro as M                                   # noqa: E402
    MACRO = set(M.INDICES) | set(M.METALS) | set(M.CURRENCY)
    macro = sorted(s for s in wm if s in MACRO)

    con = T._connect()
    now = T.now_ist()
    plans = [T.plan_symbol(con, s, now) for s in sorted(wm) if s not in MACRO]
    todo = [p for p in plans if not p.skip and p.windows]
    skipped = [p for p in plans if p.skip or not p.windows]

    print(f"{len(wm)} symbol(s) from {a.watermarks.name}")
    print(f"  equities: {len(todo)} need minutes, {len(skipped)} already current")
    print(f"  macro:    {len(macro)} (indices / MCX / INR pairs)\n")
    for p in todo[:8]:
        w0, w1 = p.windows[0][0], p.windows[-1][1]
        print(f"  {p.symbol:<14} last {_ist(p.last_bar_ts) if p.last_bar_ts else '—':>12}"
              f"  ->  {len(p.windows)} window(s)  {w0:%d %b} .. {w1:%d %b}")
    if len(todo) > 8:
        print(f"  … {len(todo) - 8} more")
    if a.plan_only:
        return 0
    if not todo and not macro:
        print("\nnothing to fetch")
        return 0

    # PRE-FLIGHT. An expired Kite token does not raise anywhere in the fetch
    # path: topup retries, gives up, prints "ERR … Incorrect `api_key` or
    # `access_token`", and then reports "DONE RELIANCE + 0 bars … ok" and
    # exits 0. Measured 2026-08-05 by poisoning the token deliberately. That
    # is exactly how two sessions went missing with nothing said — a nightly
    # timer would have seen success every morning. So the token is proven
    # BEFORE any window is requested, and the failure is a non-zero exit.
    try:
        tok = T.get_token()
        T._resolve("RELIANCE", tok)
    except SystemExit as exc:
        print(f"\nABORT: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:                       # noqa: BLE001
        print(f"\nABORT: Kite is not usable — {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2

    t0 = time.time()
    if todo:
        # reconcile=False on purpose: the CA check needs Kite's DAILY endpoint
        # and compares against bars_1d, which this throwaway DB does not have.
        # It is a validation of the local store, not of a patch in flight.
        T.run(todo, workers=a.workers, reconcile=False)
    if macro:
        print(f"\n— macro ({len(macro)}) —")
        M.main(macro)     # reads CHARTO_DB, restarts from MAX(ts) - 2 days
    con2 = sqlite3.connect(seed_path)
    dropped = strip_anchors(con2, wm)
    rows = con2.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    con2.execute("VACUUM")
    con2.close()

    # POST-FLIGHT. The pre-flight catches a token that is dead before we
    # start; this catches one that dies (or is revoked) mid-run, and every
    # other way a fetch can come back empty. Planning work and shipping
    # nothing is a failure however cheerfully the per-symbol lines read.
    if not rows:
        print(f"\nABORT: planned {len(todo)} equity + {len(macro)} macro symbol(s) "
              f"and fetched NOTHING. The patch would be empty; not writing one.",
              file=sys.stderr)
        Path(seed_path).unlink(missing_ok=True)
        return 3

    shutil.move(seed_path, a.out)
    mb = a.out.stat().st_size / 1e6
    print(f"\nfetched in {time.time() - t0:.0f}s")
    print(f"  {rows:,} minute(s), {dropped:,} anchor(s) dropped")
    print(f"  patch: {a.out}  ({mb:.1f} MB)")
    print(f"\nship it, then on the remote:\n"
          f"  python catchup_remote.py --merge {a.out.name} --into /data/charto_bars.db")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
