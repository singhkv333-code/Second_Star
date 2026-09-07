#!/usr/bin/env python3
"""Phase 2: backfill full financials (P&L, balance sheet, cash flow, ratios) for
companies missing them, from MoneyControl's mcapi -- into an Azure STAGING table
first, so nothing touches the live `mc.statement_lines` (or local disk) until an
explicit, deduped promotion.

SOURCE
  https://api.moneycontrol.com/mcapi/v1/quarterly-earning/{statement}?sc_id=X&deviceType=W
  statement in {profit-loss, balance-sheet, cash-flow, ratios}
  response.data.headers        -> ordered {display_label: [field_key, css]};
                                  empty field_key + darkbg == SECTION header.
  response.data.standardResult  -> standalone periods (list of {yrc, <field_key>: value})
  response.data.consolidatedResult -> consolidated periods
  This exactly reconstructs the existing source='mc_api' rows (human line_item +
  section names), so the backfill matches what other companies already have.

FLOW (matches the agreed staging model)
  1. fetch  -> parse -> batch-INSERT into mc._backfill_stage  (8 conns, resumable)
  2. inspect the staging table via SQL (row counts, sample companies)
  3. --promote -> one atomic INSERT..SELECT into mc.statement_lines, INSERT-ONLY,
     dedup on (sc_id, statement, basis, period_end, section, line_item, value_numeric)
     so nothing is double-filled or overwritten.
  4. --drop-stage when done.

SAFETY
  * The live table is never written during fetch. Promotion is insert-only.
  * Resumable: a company already present in staging is skipped.
  * Zero local-disk footprint: rows stream mcapi -> memory -> Azure staging.

USAGE
  cd pivot
  .venv/bin/python scripts/backfill_financials_from_mcapi.py --limit 20     # small batch into staging
  .venv/bin/python scripts/backfill_financials_from_mcapi.py                # all missing-fundamentals cos
  .venv/bin/python scripts/backfill_financials_from_mcapi.py --stage-stats  # inspect staging
  .venv/bin/python scripts/backfill_financials_from_mcapi.py --promote      # atomic deduped promote
  .venv/bin/python scripts/backfill_financials_from_mcapi.py --drop-stage
"""
from __future__ import annotations

import argparse
import calendar
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone

import requests
from psycopg2.extras import execute_values
from sqlalchemy import create_engine, text

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(HERE, "..", ".env")
STAGE = "mc._backfill_stage"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
BASE = "https://api.moneycontrol.com/mcapi/v1/quarterly-earning"
MAX_CONN = 8

# endpoint token -> statement enum
STATEMENTS = {
    "profit-loss": "profit_loss",
    "balance-sheet": "balance_sheet",
    "cash-flow": "cash_flow",
    "ratios": "ratios",
}
# money statements are in Rs. Cr.; ratios carry mixed units -> leave blank.
UNIT = {"profit_loss": "Rs. Cr.", "balance_sheet": "Rs. Cr.",
        "cash_flow": "Rs. Cr.", "ratios": ""}
_META_KEYS = {"year", "months", "ent_date", "yrc0", "yrc", "str_month",
              "str_year", "noofmonths"}

_local = threading.local()


def _load_env() -> dict:
    env = {}
    with open(ENV_PATH) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def _session() -> requests.Session:
    s = getattr(_local, "sess", None)
    if s is None:
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept": "application/json, text/plain, */*",
                          "Referer": "https://www.moneycontrol.com/"})
        _local.sess = s
    return s


def _num(v) -> float | None:
    if v is None:
        return None
    t = str(v).strip().replace(",", "")
    if t in ("", "-", "--", "NA", "N.A."):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _period_end(yrc) -> date | None:
    """yrc = YYYYMM int -> last day of that month."""
    try:
        y, m = int(yrc) // 100, int(yrc) % 100
        if not (1 <= m <= 12):
            return None
        return date(y, m, calendar.monthrange(y, m)[1])
    except (TypeError, ValueError):
        return None


def _period_label(row) -> str:
    lbl = (row.get("yrc0") or "").strip()
    if lbl:
        return lbl
    pe = _period_end(row.get("yrc"))
    return pe.strftime("%b %y") if pe else str(row.get("yrc") or "")


def _fetch(sc_id: str, ep: str) -> dict | None:
    url = f"{BASE}/{ep}?sc_id={sc_id}&deviceType=W"
    for attempt in range(3):
        try:
            r = _session().get(url, timeout=15)
            if r.status_code == 200:
                d = r.json()
                return d.get("data") if d.get("success") else None
            if r.status_code in (429, 503):
                time.sleep(1.5 * (attempt + 1))
                continue
            return None
        except Exception:
            if attempt < 2:
                time.sleep(1.0)
    return None


def _parse(sc_id: str, statement: str, data: dict) -> list[dict]:
    """Reconstruct long-format rows from a statement response, using `headers`
    for the human labels + section grouping (mirrors source='mc_api')."""
    headers = data.get("headers") or {}
    if not isinstance(headers, dict) or not headers:
        return []
    # ordered list of (label, field_key, is_section)
    layout, section = [], ""
    order = 0
    for label, meta in headers.items():
        key = (meta[0] if isinstance(meta, (list, tuple)) and meta else "") or ""
        if not key:                      # section header row
            section = label.strip()
            continue
        if key in _META_KEYS:
            continue
        order += 1
        layout.append((label.strip(), key, section, order))

    out = []
    now = datetime.now(timezone.utc)
    for basis, arr_key in (("standalone", "standardResult"),
                           ("consolidated", "consolidatedResult")):
        for row in (data.get(arr_key) or []):
            pe = _period_end(row.get("yrc"))
            if pe is None:
                continue
            plabel = _period_label(row)
            for label, key, section, order in layout:
                if key not in row:
                    continue
                raw = row.get(key)
                out.append({
                    "sc_id": sc_id, "statement": statement, "basis": basis,
                    "period_label": plabel, "period_end": pe,
                    "period_kind": "annual", "section": section,
                    "line_item": label, "line_order": order,
                    "value_text": None if raw is None else str(raw),
                    "value_numeric": _num(raw), "unit": UNIT[statement],
                    "source": "mc_api", "yrc": int(row["yrc"]),
                    "scraped_at": now,
                })
    return out


def _ensure_stage(eng) -> None:
    with eng.begin() as c:
        c.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {STAGE} (
              sc_id text, statement text, basis text, period_label text,
              period_end date, period_kind text, section text, line_item text,
              line_order int, value_text text, value_numeric numeric,
              unit text, source text, yrc int, scraped_at timestamptz
            )"""))
        c.execute(text(f"CREATE INDEX IF NOT EXISTS _stage_scid ON {STAGE} (sc_id)"))


_COLS = ("sc_id", "statement", "basis", "period_label", "period_end",
         "period_kind", "section", "line_item", "line_order", "value_text",
         "value_numeric", "unit", "source", "yrc", "scraped_at")
_INSERT_SQL = f"INSERT INTO {STAGE} ({','.join(_COLS)}) VALUES %s"


def _bulk_insert(eng, rows: list[dict]) -> None:
    """One multi-row INSERT per ~1000 rows via psycopg2 execute_values -- vs
    executemany's one round-trip PER ROW, which over Azure's ~62ms RTT made a
    1,700-row company take ~100s just to insert."""
    tuples = [tuple(r[c] for c in _COLS) for r in rows]
    with eng.begin() as c:
        raw = c.connection.dbapi_connection
        with raw.cursor() as cur:
            execute_values(cur, _INSERT_SQL, tuples, page_size=1000)


def _do_company(eng, sc_id: str) -> tuple[str, int, int]:
    rows: list[dict] = []
    ok_eps = 0
    for ep, statement in STATEMENTS.items():
        data = _fetch(sc_id, ep)
        if not data:
            continue
        parsed = _parse(sc_id, statement, data)
        if parsed:
            ok_eps += 1
            rows.extend(parsed)
    if rows:
        _bulk_insert(eng, rows)
    return sc_id, ok_eps, len(rows)


def cmd_fetch(eng, args) -> None:
    _ensure_stage(eng)
    with eng.connect() as c:
        # Target INCOMPLETE companies: fewer than the 4 statement types present
        # (captures both fully-missing AND partial-coverage -- e.g. a company
        # with only P&L). Gap-fill promotion then completes them without
        # touching existing rows. Exclude ISIN-like junk sc_ids (leading digits
        # / >8 chars) -- non-company rows mcapi has no financials for. Order
        # real/live first (a symbol, and partial-coverage before fully-missing)
        # so the run front-loads the fillable ones.
        targets = [r[0] for r in c.execute(text("""
            WITH cov AS (
              SELECT sc_id, count(DISTINCT statement) AS nstmt
              FROM mc.statement_lines GROUP BY sc_id
            )
            SELECT co.sc_id FROM mc.companies co
            LEFT JOIN cov ON cov.sc_id = co.sc_id
            WHERE COALESCE(cov.nstmt, 0) < 4
              AND co.sc_id ~ '^[A-Za-z]'
              AND length(co.sc_id) <= 8
            ORDER BY (co.nse_symbol IS NOT NULL OR co.bse_code IS NOT NULL) DESC,
                     COALESCE(cov.nstmt, 0) DESC,   -- partial (real) before empty
                     co.sc_id
        """)).fetchall()]
        already = {r[0] for r in c.execute(text(f"SELECT DISTINCT sc_id FROM {STAGE}")).fetchall()}
    targets = [t for t in targets if t not in already]
    if args.limit:
        targets = targets[: args.limit]
    print(f"targets missing fundamentals: {len(targets)} (already staged skipped)")
    if not targets:
        print("nothing to fetch.")
        return

    t0 = time.monotonic()
    done = cos_with_data = total_rows = 0
    with ThreadPoolExecutor(max_workers=MAX_CONN) as pool:
        futs = {pool.submit(_do_company, eng, sc): sc for sc in targets}
        for fut in as_completed(futs):
            sc = futs[fut]
            try:
                _sc, eps, n = fut.result()
                done += 1
                if n:
                    cos_with_data += 1
                    total_rows += n
            except Exception as e:  # noqa: BLE001
                done += 1
                print(f"  ERR {sc}: {str(e)[:80]}", flush=True)
            if done % 25 == 0 or done == len(targets):
                rate = done / max(0.1, time.monotonic() - t0)
                print(f"  {done}/{len(targets)} cos | with_data={cos_with_data} "
                      f"rows={total_rows} | {rate:.1f} co/s", flush=True)
    print(f"\nSTAGED {total_rows} rows for {cos_with_data}/{len(targets)} companies "
          f"in {(time.monotonic()-t0)/60:.1f} min. Inspect with --stage-stats, "
          f"then --promote.")


def cmd_stage_stats(eng, _args) -> None:
    with eng.connect() as c:
        n = c.execute(text(f"SELECT count(*) FROM {STAGE}")).scalar()
        cos = c.execute(text(f"SELECT count(DISTINCT sc_id) FROM {STAGE}")).scalar()
        print(f"staging rows: {n:,} across {cos:,} companies")
        for r in c.execute(text(f"""SELECT statement, basis, count(*), count(distinct sc_id),
                min(period_end), max(period_end) FROM {STAGE} GROUP BY 1,2 ORDER BY 1,2""")).fetchall():
            print(f"  {r[0]:14} {r[1]:12} rows={r[2]:>8} cos={r[3]:>5} {r[4]}..{r[5]}")
        print("sample company:")
        s = c.execute(text(f"SELECT sc_id FROM {STAGE} LIMIT 1")).scalar()
        for r in c.execute(text(f"""SELECT statement,basis,section,line_item,value_numeric,unit,period_label
                FROM {STAGE} WHERE sc_id=:s ORDER BY statement,line_order LIMIT 6"""), {"s": s}).fetchall():
            print(f"    [{r[0]}/{r[1]}] {r[2]!r} | {r[3]!r} = {r[4]} {r[5]} ({r[6]})")


def cmd_promote(eng, _args) -> None:
    """Atomic, insert-only promotion into the live table.

    Dedup is on the LOGICAL cell -- (sc_id, statement, basis, period_label,
    line_item), WITHOUT line_order -- because our mcapi line_order differs from
    the existing rows' ordering for ~88% of overlapping cells; keying on
    line_order would insert a duplicate for every one of them. So:
      * DISTINCT ON the logical cell collapses any staging-internal repeats;
      * NOT EXISTS on the logical cell skips any cell already present (mc_html
        OR mc_api), completing partial companies without touching or
        duplicating what they have. It rides `statement_lines_period_idx`
        (sc_id, statement, basis, period_label) so it stays index-backed.
    Because a skipped cell shares all 5 logical cols with a live row, the
    surviving inserts can never collide on uq_statement_cell either."""
    with eng.begin() as c:
        before = c.execute(text("SELECT count(*) FROM mc.statement_lines")).scalar()
        c.execute(text(f"""
            INSERT INTO mc.statement_lines
              (sc_id, statement, basis, period_label, period_end, period_kind,
               section, line_item, line_order, value_text, value_numeric, unit,
               source, scraped_at, page_no, source_url)
            SELECT DISTINCT ON (g.sc_id, g.statement, g.basis, g.period_label, g.line_item)
               g.sc_id, g.statement::mc.statement_type, g.basis::mc.basis,
               g.period_label, g.period_end, g.period_kind, g.section, g.line_item,
               g.line_order, g.value_text, g.value_numeric, g.unit, g.source, g.scraped_at,
               -- page_no + source_url are NOT NULL on statement_lines; match the
               -- existing mc_api convention (page_no=1, the MC financials URL).
               1,
               'https://www.moneycontrol.com/markets/financials/'
                 || CASE g.statement WHEN 'profit_loss' THEN 'profit-loss'
                                     WHEN 'balance_sheet' THEN 'balance-sheet'
                                     WHEN 'cash_flow' THEN 'cash-flow'
                                     ELSE 'ratios' END
                 || '/' || g.sc_id || '/'
            FROM {STAGE} g
            WHERE NOT EXISTS (
              SELECT 1 FROM mc.statement_lines s
              WHERE s.sc_id = g.sc_id
                AND s.statement = g.statement::mc.statement_type
                AND s.basis = g.basis::mc.basis
                AND s.period_label = g.period_label
                AND s.line_item = g.line_item
            )
            ORDER BY g.sc_id, g.statement, g.basis, g.period_label, g.line_item, g.line_order
            """))
        after = c.execute(text("SELECT count(*) FROM mc.statement_lines")).scalar()
    print(f"PROMOTED {after - before:,} new rows into mc.statement_lines "
          f"({before:,} -> {after:,}). Existing cells skipped. --drop-stage when satisfied.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--stage-stats", action="store_true")
    ap.add_argument("--promote", action="store_true")
    ap.add_argument("--drop-stage", action="store_true")
    args = ap.parse_args()

    env = _load_env()
    # exactly MAX_CONN pooled connections (no overflow) for the 8-way fetch.
    eng = create_engine(env["FINANCIALS_DSN"], pool_size=MAX_CONN,
                        max_overflow=0, pool_pre_ping=True)

    if args.drop_stage:
        with eng.begin() as c:
            c.execute(text(f"DROP TABLE IF EXISTS {STAGE}"))
        print(f"dropped {STAGE}")
        return
    if args.stage_stats:
        cmd_stage_stats(eng, args)
        return
    if args.promote:
        cmd_promote(eng, args)
        return
    cmd_fetch(eng, args)


if __name__ == "__main__":
    main()
