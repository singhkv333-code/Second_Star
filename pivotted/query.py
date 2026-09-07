"""One read-only SQL surface over the stores nothing else derives.

WHY ONE TOOL AND NOT TWELVE. The seven stores reached from here — quarterly
results, shareholding and pledge, extracted filing facts, segment mix, pattern
base rates, order flow, identity — are flat, wide, already-computed fact
tables. They need ACCESS, not a derivation layer, and a tool apiece would cost
~3,000 tokens on every turn against a table that is already 24 tools / ~8.6k.
This costs ~350 and does not grow when the eighth store arrives.

WHERE THE LINE IS. Anything a curated derivation already exists for stays
behind its tool. `mc.statement_lines` carries line-item synonyms across years
and bases, a consolidated->standalone preference and a recency floor, all of
it encoded in `backend.market.financials_db` and shared with the stock page;
SQL written against those raw rows would produce a second, silently different
set of numbers for the same company. So annual P&L, balance sheet, ratios and
cash flow are served by `get_statement`/`get_fundamentals`, and the tool
description says so rather than a check refusing it.

WHAT IS GUARDED, AND WHAT DELIBERATELY IS NOT. The model writes whatever SQL
it likes — CTEs, windows, joins, aggregates. Nothing inspects the query for
shape or intent, because every wall of that kind (a `^SELECT` regex, a column
allowlist, a fixed WHERE template) refuses correct queries far more often than
it catches bad ones: the regex alone rejects every CTE. What holds instead is
the DATABASE's own read-only mode, a statement timeout, and caps on rows and
bytes returned. Those are safety, not semantics.

PROGRESSIVE DISCLOSURE. No schema sits in the context. `sql` omitted returns
one dataset's columns, and — the path that actually matters — so does a failed
query. The model guesses `period_end` and `revenue`, is right most of the
time, and self-corrects in one round when it is not, instead of paying a
lookup round-trip before every question.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import logging
import pathlib
import sqlite3
import threading
import time

import fundamentals as fnd

MAX_ROWS = 500          # rows returned; the 501st only sets `truncated`
MAX_CELL = 600          # chars per cell — filings quotes run to paragraphs
MAX_CHARS = 140_000     # whole-payload ceiling, ~35k tokens worst case
TIMEOUT_S = 15          # Azure PG is a shared read pool; SQLite holds 497M bars


# The dataset is the unit the model chooses, and it is a SEMANTIC domain, not
# a database: `quarterly` and `identity` are the same Postgres, `patterns` and
# `flows` the same SQLite. Each carries one hand-written line — grain, key,
# and the one thing that is wrong to assume. That line is the entire semantic
# layer, and it is what separates ~60% accuracy on a raw schema from ~90%.
DATASETS: dict[str, dict] = {
    "quarterly": {
        "db": "pivot",
        "tables": ["quarterly_metrics", "quarterly_statement_lines",
                   "result_filings"],
        "note": ("One row per company x quarter. `quarterly_metrics` holds 40 "
                 "pre-computed figures including revenue_yoy_pct, "
                 "net_profit_qoq_pct and the TTM set; "
                 "`quarterly_statement_lines` is the raw filed line items. "
                 "`period_end` is a date and `qi` orders quarters. Key: "
                 "symbol, also sc_id and isin. Covers 3,799 companies, not "
                 "the 11,256 the annual filings reach — a company with no "
                 "rows here may still have annual statements."),
    },
    "shareholding": {
        "db": "financials",
        "tables": ["shp.filings", "shp.category", "shp.holder", "shp.sbo"],
        "note": ("One `shp.filings` row per company x quarter_end carrying "
                 "the headline promoter_pct, promoter_pledged and "
                 "promoter_encumbered_pct; category, holder and sbo detail "
                 "hang off it by filing_id. Key: symbol or isin, on "
                 "shp.filings."),
    },
    "filings": {
        "db": "financials",
        "tables": ["filings.facts", "filings.documents"],
        "note": ("One row per fact extracted from an annual report. Filter "
                 "`task` to a family (segments, cost_structure, "
                 "receivables_ageing, debt_terms, contingent, workforce, "
                 "schedule3_ratios, related_party, cwip_ageing, audit, "
                 "strategy, special_metrics). `status` is reported, nil or "
                 "not_disclosed — a nil row means the company said zero, "
                 "which is an answer. `value_crore` is the normalised "
                 "number, `grounding` how the fact was located in the "
                 "document, and `unit_agrees` is false where the resolver "
                 "and the model read the units differently. Key: symbol."),
    },
    "segments": {
        "db": "enrich",
        "tables": ["enrich.tijori_enrichment", "enrich.company_profile"],
        "note": ("Revenue mix and market share as JSON in "
                 "tijori_enrichment (has_revenue_mix flags which rows carry "
                 "it); business profile and institutional holding "
                 "percentages in company_profile. Key: sc_id — the `ticker` "
                 "column in this store is corrupted and must never be joined "
                 "on."),
    },
    "patterns": {
        "db": "charto",
        "tables": ["pattern_stats", "pattern_stats_meta"],
        "note": ("Measured hit rate, control base rate and edge in "
                 "percentage points for each pattern kind x interval x "
                 "horizon, with the sample size behind it. There is no "
                 "symbol column: this is the CROSS-SECTION, which is the "
                 "only thing here no other tool reaches — for one pattern on "
                 "one company, evaluate_pattern reads that company's own "
                 "instances and is the better answer."),
    },
    "flows": {
        "db": "charto",
        "tables": ["mkt.deals", "deals", "delivery", "fut_oi", "vp_screen"],
        "note": ("Order flow, daily: bulk and block deals, delivery "
                 "percentage, futures open interest, volume-profile levels. "
                 "This is the CROSS-SECTION — ranking, counting or "
                 "aggregating across the market. For one symbol's flows or "
                 "one named client's trades, get_flows and get_deals already "
                 "carry the percentile, the OI quadrant read and the "
                 "client-name folding, and are the better answer. Query "
                 "`mkt.deals`, the market-wide sweep over ~3,400 symbols; "
                 "the bare `deals` table holds only the few dozen locally "
                 "hydrated symbols and under-reports as though it were the "
                 "whole truth."),
    },
    "identity": {
        "db": "pivot",
        "tables": ["company_identity", "instrument_master"],
        "note": ("symbol <-> ISIN <-> Moneycontrol sc_id <-> BSE code for "
                 "5,206 companies, and the tradable instrument master "
                 "(equities, indices, options, futures). Use it to reach a "
                 "company across datasets that key differently."),
    },
}


# ── identity ────────────────────────────────────────────────────────────────
#
# The single largest failure mode available here, and the reason `symbol` is
# an argument rather than something the model writes into its WHERE. These
# stores key on three different identifiers: pivot_db and charto on the NSE
# symbol, mc/enrich on Moneycontrol's sc_id, shp on ISIN. A model that writes
# `WHERE sc_id = 'TCS'` gets zero rows and calls it "no data"; one that writes
# `WHERE ticker = 'ACC'` against enrich gets Active Clothing's row labelled
# "ACC Limited" — right name, wrong company. So all three are resolved once,
# here, through the audited resolver, and bound as :symbol / :sc_id / :isin.
_ident_lock = threading.Lock()
_ident_cache: dict[str, dict] = {}

# The four ways in are RANKED, not OR-ed, for the reason `fundamentals.
# _RESOLVE_SQL` documents at length: Moneycontrol's sc_id is a short internal
# code that collides with real NSE tickers of unrelated companies. A flat OR
# matched BLS E-Services on `mc_sc_id = 'BEL'` and answered a question about
# Bharat Electronics with it — right ticker in, wrong company out, every
# number downstream real and belonging to somebody else. The traded symbol
# wins, then ISIN, then an exact name, and sc_id only when nothing else
# matched at all.
_IDENT_SQL = """
SELECT isin, verified_symbol, mc_sc_id, verified_name FROM (
    SELECT isin, verified_symbol, mc_sc_id, verified_name, mc_is_primary,
           1 AS pri FROM public.company_identity
     WHERE upper(verified_symbol) = upper(:s)
    UNION ALL
    SELECT isin, verified_symbol, mc_sc_id, verified_name, mc_is_primary, 2
      FROM public.company_identity WHERE upper(isin) = upper(:s)
    UNION ALL
    SELECT isin, verified_symbol, mc_sc_id, verified_name, mc_is_primary, 3
      FROM public.company_identity WHERE upper(verified_name) = upper(:s)
    UNION ALL
    SELECT isin, verified_symbol, mc_sc_id, verified_name, mc_is_primary, 4
      FROM public.company_identity WHERE upper(mc_sc_id) = upper(:s)
) x ORDER BY pri, mc_is_primary DESC NULLS LAST LIMIT 1
"""


def identity(symbol: str) -> dict:
    """{symbol, sc_id, isin, name} for a ticker, name, ISIN or sc_id."""
    key = (symbol or "").strip().upper()
    if not key:
        return {}
    with _ident_lock:
        if key in _ident_cache:
            return _ident_cache[key]
    out = {"symbol": key, "sc_id": None, "isin": None, "name": None}
    try:
        from sqlalchemy import text
        s = _session("pivot")
        try:
            row = s.execute(text(_IDENT_SQL), {"s": key}).fetchone()
        finally:
            s.close()
        if row:
            out.update(isin=row[0], symbol=row[1] or key,
                       sc_id=row[2], name=row[3])
    except Exception:                              # noqa: BLE001
        logging.exception("pivotted.query: identity lookup failed")
    # The audited resolver outranks company_identity's mc_sc_id: it is the one
    # that knows nse_symbol must beat sc_id, which is why BEL is Bharat
    # Electronics here and BLS E-Services in the naive ordering.
    try:
        got = fnd.resolve(out["symbol"] or key)
        if got:
            out["sc_id"] = got
    except Exception:                              # noqa: BLE001
        pass
    with _ident_lock:
        _ident_cache[key] = out
    return out


# ── connections — all four already exist and are pooled ─────────────────────

def _session(db: str):
    """A pooled SQLAlchemy session for one of pivot's three Postgres DBs."""
    fnd._pivot()                       # sets sys.path, loads pivot/.env
    from backend import database as D
    factory = {"pivot": D.SessionLocal,
               "financials": D.FinancialsSessionLocal,
               "enrich": D.EnrichSessionLocal}[db]
    if factory is None:
        raise RuntimeError(f"{db} store is not configured (DSN unset)")
    return factory()


def _sqlite():
    """A private connection to charto's store, read-only for its whole life.

    Deliberately NOT `ds._con`. That is one connection per worker thread,
    shared with 129 other call sites, and `PRAGMA query_only` plus a progress
    handler are connection-wide state — leaking either into the thread that
    next writes `news_cache` is a worse bug than the one they prevent. A fresh
    handle costs about a millisecond and the tables reached here are small.
    """
    import sys
    charto = str(pathlib.Path(__file__).resolve().parent.parent
                 / "charto" / "data")
    if charto not in sys.path:
        sys.path.insert(0, charto)
    import dataserver as ds
    c = sqlite3.connect(str(ds.DB_PATH), timeout=30.0)
    if getattr(ds, "_HAVE_MKT", False):
        try:
            c.execute("ATTACH DATABASE ? AS mkt", (str(ds._MKT_PATH),))
        except sqlite3.Error as exc:               # noqa: BLE001
            logging.warning("pivotted.query: mkt attach failed: %s", exc)
    c.execute("PRAGMA query_only=ON")
    deadline = time.monotonic() + TIMEOUT_S
    # SQLite has no statement_timeout. The progress handler is called every N
    # VM instructions and aborts when it returns non-zero — which is what
    # stops a join onto `bars` (497M rows) from holding a worker forever.
    c.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0,
                           20_000)
    return c


# ── catalog — introspected, never hand-maintained ───────────────────────────
#
# Generated from the live database so a table that is VM-only (fut_oi,
# delivery) is simply absent from a laptop's catalog rather than advertised
# and then failing. Cached per process; these schemas change on the order of
# months.
_cat_lock = threading.Lock()
_catalog: dict[str, dict] = {}

_PG_COLS = """
SELECT c.relname, n.nspname, a.attname, format_type(a.atttypid, a.atttypmod),
       c.reltuples::bigint
  FROM pg_attribute a
  JOIN pg_class c ON c.oid = a.attrelid
  JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE a.attnum > 0 AND NOT a.attisdropped
   AND n.nspname || '.' || c.relname = ANY(:full)
 ORDER BY c.relname, a.attnum
"""


def catalog(dataset: str) -> dict:
    with _cat_lock:
        if dataset in _catalog:
            return _catalog[dataset]
    spec = DATASETS[dataset]
    tables: dict[str, dict] = {}
    if spec["db"] == "charto":
        c = _sqlite()
        try:
            for t in spec["tables"]:
                try:
                    cur = c.execute(f"SELECT * FROM {t} LIMIT 0")
                except sqlite3.Error:
                    continue           # VM-only, or not synced on this host
                cols = ", ".join(d[0] for d in cur.description)
                n = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                tables[t] = {"rows": n, "columns": cols}
        finally:
            c.close()
    else:
        from sqlalchemy import text
        full = [t if "." in t else f"public.{t}" for t in spec["tables"]]
        s = _session(spec["db"])
        try:
            rows = s.execute(text(_PG_COLS), {"full": full}).fetchall()
        finally:
            s.close()
        for rel, nsp, col, typ, n in rows:
            name = rel if nsp == "public" else f"{nsp}.{rel}"
            e = tables.setdefault(name, {"rows": int(n), "columns": []})
            e["columns"].append(f"{col} {typ}")
        for e in tables.values():
            e["columns"] = ", ".join(e["columns"])
    out = {"dataset": dataset, "note": spec["note"], "tables": tables}
    with _cat_lock:
        _catalog[dataset] = out
    return out


# ── execution ───────────────────────────────────────────────────────────────

def _cell(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    if isinstance(v, (bytes, memoryview)):
        return f"<{len(bytes(v))} bytes>"
    if isinstance(v, str) and len(v) > MAX_CELL:
        return v[:MAX_CELL] + f"... (+{len(v) - MAX_CELL} chars)"
    return v


def _shape(cols, rows, dataset, t0):
    truncated = len(rows) > MAX_ROWS
    rows = [[_cell(v) for v in r] for r in rows[:MAX_ROWS]]
    # A second pass on total size: 500 rows x 30 wide columns of filings text
    # is a bigger reply than any question needs, and the row cap alone does
    # not bound it.
    size = sum(len(str(v)) for r in rows for v in r)
    if size > MAX_CHARS and rows:
        keep = max(1, int(len(rows) * MAX_CHARS / size))
        rows, truncated = rows[:keep], True
    out = {"dataset": dataset, "columns": list(cols), "rows": rows,
           "row_count": len(rows), "elapsed_ms": int((time.monotonic() - t0) * 1000)}
    if not rows:
        # Empty has two causes that mean opposite things — a filter that does
        # not match the stored vocabulary, and a company this store genuinely
        # does not cover. Reporting the first as the second is how a covered
        # company gets told it has no results, so the model is asked to
        # separate them rather than left to guess.
        out["_note"] = ("No rows matched. That is either a filter that does "
                        "not match how this store spells things, or real "
                        "absence — widen the query once (drop the narrowest "
                        "condition, or select the distinct values of the "
                        "column you filtered on) before reporting the data "
                        "as missing.")
    if truncated:
        out["truncated"] = True
        out["_note"] = ("Results were cut off. Say the list is partial, or "
                        "re-ask with an aggregate, a tighter WHERE or an "
                        "ORDER BY that puts what matters first.")
    return out


def tool_query(dataset: str = "", sql: str = "", symbol: str = "") -> dict:
    if dataset not in DATASETS:
        return {"error": f"unknown dataset {dataset!r}",
                "datasets": {k: v["note"] for k, v in DATASETS.items()}}
    try:
        cat = catalog(dataset)
    except Exception as exc:                       # noqa: BLE001
        logging.exception("pivotted.query: catalog failed")
        return {"error": f"{dataset} store is unavailable: {exc}",
                "_note": "Say this data is unavailable; do not answer from memory."}
    if not (sql or "").strip():
        return cat

    params = identity(symbol) if symbol else {}
    t0 = time.monotonic()
    try:
        if DATASETS[dataset]["db"] == "charto":
            c = _sqlite()
            try:
                cur = c.execute(sql, params)
                return _shape([d[0] for d in cur.description or []],
                              cur.fetchmany(MAX_ROWS + 1), dataset, t0)
            finally:
                c.close()
        from sqlalchemy import text
        stmt = text(sql)
        # text() only compiles the binds it actually found, and its lookbehind
        # already excludes Postgres `::` casts. Passing the rest would raise,
        # so hand it exactly what the statement asked for.
        bound = {k: params.get(k) for k in stmt._bindparams}
        s = _session(DATASETS[dataset]["db"])
        try:
            # Read-only is the DATABASE's, not a parser's: this admits every
            # CTE, window and aggregate the model can write, and refuses every
            # write, including ones hidden in a function call.
            s.execute(text("SET TRANSACTION READ ONLY"))
            s.execute(text(f"SET LOCAL statement_timeout = '{TIMEOUT_S * 1000}ms'"))
            res = s.execute(stmt, bound)
            return _shape(res.keys(), res.fetchmany(MAX_ROWS + 1), dataset, t0)
        finally:
            s.rollback()
            s.close()
    except Exception as exc:                       # noqa: BLE001
        msg = str(getattr(exc, "orig", exc)).strip().splitlines()[0][:400]
        return {"error": msg, "dataset": dataset, "note": cat["note"],
                "tables": cat["tables"],
                "_note": ("The query failed — the columns that do exist are "
                          "above. Fix it and call again; do not report this "
                          "as missing data until a corrected query returns "
                          "nothing.")}
