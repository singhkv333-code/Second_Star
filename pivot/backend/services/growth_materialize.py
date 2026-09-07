"""Materialized growth metrics for the screener.

The growth CTE class (revenue/net-profit/EPS growth, YoY or N-year CAGR)
recomputes paired-period aggregates over 12y of ``mc.statement_lines`` per
request — measured 20-55s per query, and >7 concurrent exhausts the
financials pool (TimeoutError for every fundamentals consumer). The numbers
themselves change only when new filings land, so this module precomputes
them into ``mc.growth_metrics_mat`` (one row per metric x horizon x
company) and the screener reads that table indexed instead.

Contract:
  - ``build_growth_metrics()`` — full rebuild, shard-by-shard (each shard
    = one metric x one gy horizon in its own transaction with a locally
    raised statement_timeout, since the app engines cap at 60s).
  - ``mat_shard_fresh(metric, gy)`` — cheap cached freshness probe the
    screener calls per growth field; False (fail-open to the live CTE)
    when the table is missing, empty for that shard, or stale >36h. The
    live CTE stays the single source of truth for the SQL semantics —
    the INSERT below mirrors it 1:1 (same items lists via _FIELD_DEFS,
    same pairing, same consolidated-preferred dedup).

Scheduled nightly from backend/scheduler.py (module-level job fn —
APScheduler closures kill the scheduler).
"""
from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import text

logger = logging.getLogger(__name__)

GROWTH_METRICS: tuple[str, ...] = (
    "revenue_growth", "net_profit_growth", "eps_growth",
)
GY_HORIZONS: tuple[int, ...] = (1, 2, 3, 4, 5)

_TABLE = "mc.growth_metrics_mat"

_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    metric        text        NOT NULL,
    gy            integer     NOT NULL,
    sc_id         text        NOT NULL,
    g             numeric     NOT NULL,
    latest_end    date        NOT NULL,
    refreshed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (metric, gy, sc_id)
)
"""

# Mirrors the live growth CTE in fundamentals_screen.py (kind == "growth")
# minus the :floor gate — latest_end is stored so the runtime floor still
# applies at read time. gy and the growth expression are interpolated as
# literals exactly like the live query (planner needs the rn literal).
_SHARD_INSERT_TMPL = """
INSERT INTO {table} (metric, gy, sc_id, g, latest_end)
WITH base_raw AS (
    SELECT DISTINCT ON (sl.sc_id, sl.basis, sl.period_end)
           sl.sc_id, sl.basis, sl.value_numeric AS v, sl.period_end
    FROM mc.statement_lines sl
    WHERE sl.line_item = ANY(:items)
      AND sl.value_numeric IS NOT NULL
      AND sl.period_end IS NOT NULL
      AND (sl.statement <> 'ratios' OR sl.source IN ('mc_html', 'mc_api'))
    ORDER BY sl.sc_id, sl.basis, sl.period_end DESC,
             array_position(CAST(:items AS text[]), sl.line_item)
),
base AS (
    SELECT sc_id, basis, v, period_end,
           row_number() OVER (
               PARTITION BY sc_id, basis
               ORDER BY period_end DESC NULLS LAST) AS rn
    FROM base_raw
),
paired AS (
    SELECT b1.sc_id, b1.basis, b1.period_end AS latest_end,
           {growth_expr} AS g
    FROM base b1 JOIN base b2
      ON b1.sc_id = b2.sc_id AND b1.basis = b2.basis
     AND b1.rn = 1 AND b2.rn = {rn2}
)
SELECT :metric, {gy}, sc_id, g, latest_end
FROM (
    SELECT DISTINCT ON (sc_id) sc_id, g, latest_end
    FROM paired
    WHERE g IS NOT NULL
    ORDER BY sc_id, (basis = 'consolidated') DESC
) dedup
"""


def _growth_expr(gy: int) -> str:
    if gy == 1:
        return ("CASE WHEN b2.v <> 0 "
                "THEN (b1.v - b2.v) / abs(b2.v) * 100.0 END")
    return (
        "CASE WHEN b1.v > 0 AND b2.v > 0 "
        "AND (b1.period_end - b2.period_end) > 0 "
        "THEN (power(b1.v / b2.v, "
        "365.25 / (b1.period_end - b2.period_end)) - 1) * 100.0 END"
    )


def build_growth_metrics() -> dict[str, Any]:
    """Full rebuild of every (metric, gy) shard. Returns per-shard stats."""
    from backend.database import financials_engine
    from backend.services.fundamentals_screen import _FIELD_DEFS

    stats: dict[str, Any] = {}
    with financials_engine.begin() as conn:
        conn.execute(text("SET LOCAL statement_timeout = '120s'"))
        conn.execute(text(_CREATE_SQL))

    for metric in GROWTH_METRICS:
        items = list(_FIELD_DEFS[metric]["items"])
        for gy in GY_HORIZONS:
            t0 = time.monotonic()
            sql = _SHARD_INSERT_TMPL.format(
                table=_TABLE, growth_expr=_growth_expr(gy),
                rn2=gy + 1, gy=gy,
            )
            try:
                with financials_engine.begin() as conn:
                    # Shard queries legitimately exceed the app engines'
                    # 60s statement_timeout — raise it for this txn only.
                    conn.execute(text("SET LOCAL statement_timeout = '300s'"))
                    conn.execute(
                        text(f"DELETE FROM {_TABLE} "
                             "WHERE metric = :m AND gy = :g"),
                        {"m": metric, "g": gy})
                    r = conn.execute(text(sql), {"items": items, "metric": metric})
                    n = r.rowcount
                stats[f"{metric}:{gy}"] = {
                    "rows": n, "s": round(time.monotonic() - t0, 1)}
                logger.info("growth_mat %s gy=%d: %d rows in %.1fs",
                            metric, gy, n, time.monotonic() - t0)
            except Exception as e:  # noqa: BLE001 — one bad shard never kills the rest
                stats[f"{metric}:{gy}"] = {"error": f"{type(e).__name__}: {e}"[:200]}
                logger.warning("growth_mat %s gy=%d failed: %s", metric, gy, e)
    _FRESH_CACHE.clear()  # next screener call re-probes
    return stats


# ── Screener-side freshness probe ───────────────────────────────────

_FRESH_TTL_S = 600.0
_STALE_AFTER_S = 36 * 3600  # nightly job + a missed day of slack
_FRESH_CACHE: dict[tuple[str, int], tuple[bool, float]] = {}


def mat_shard_fresh(metric: str, gy: int) -> bool:
    """True when mc.growth_metrics_mat can serve this (metric, gy) shard.
    Cheap (one indexed count/max, cached ~10 min per shard); False on any
    error so the screener fails open to the live CTE."""
    key = (metric, int(gy))
    hit = _FRESH_CACHE.get(key)
    now = time.monotonic()
    if hit is not None and now - hit[1] < _FRESH_TTL_S:
        return hit[0]
    fresh = False
    try:
        from backend.database import financials_engine
        with financials_engine.connect() as conn:
            row = conn.execute(
                text(f"SELECT count(*), "
                     f"extract(epoch FROM now() - max(refreshed_at)) "
                     f"FROM {_TABLE} WHERE metric = :m AND gy = :g"),
                {"m": metric, "g": int(gy)}).one()
        fresh = bool(row[0]) and row[1] is not None and float(row[1]) < _STALE_AFTER_S
    except Exception as e:  # noqa: BLE001 — table missing / transient: use live CTE
        logger.debug("growth_mat freshness probe failed (%s gy=%s): %s",
                     metric, gy, e)
        fresh = False
    _FRESH_CACHE[key] = (fresh, now)
    return fresh
