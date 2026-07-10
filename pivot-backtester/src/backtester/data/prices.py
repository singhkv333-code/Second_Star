"""yfinance backfill into mc.daily_prices.

Pragmatic v1: pulls auto-adjusted close + OHLC for any company with an
``nse_symbol`` populated. Uses ``<symbol>.NS``. Skips companies without a
mapping. Idempotent — uses ON CONFLICT to upsert.
"""
from __future__ import annotations

import asyncio
from datetime import date

import asyncpg


async def backfill_prices(
    pool: asyncpg.Pool,
    *,
    since: date,
    until: date | None = None,
    sc_ids: list[str] | None = None,
    sleep_between: float = 0.6,
) -> dict:
    """Returns a summary dict {ok, no_symbol, errors, rows_inserted}."""
    import yfinance as yf
    import pandas as pd

    until = until or date.today()

    async with pool.acquire() as conn:
        if sc_ids:
            rows = await conn.fetch(
                "SELECT sc_id, nse_symbol FROM mc.companies "
                "WHERE sc_id = ANY($1::text[]) AND nse_symbol IS NOT NULL",
                sc_ids,
            )
        else:
            rows = await conn.fetch(
                "SELECT sc_id, nse_symbol FROM mc.companies "
                "WHERE nse_symbol IS NOT NULL"
            )

    summary = {"ok": 0, "no_symbol": 0, "errors": 0, "rows_inserted": 0, "skipped": []}

    if sc_ids:
        # Count those without symbols.
        async with pool.acquire() as conn:
            missing = await conn.fetchval(
                "SELECT COUNT(*) FROM mc.companies "
                "WHERE sc_id = ANY($1::text[]) AND nse_symbol IS NULL",
                sc_ids,
            )
        summary["no_symbol"] = int(missing or 0)

    for row in rows:
        sc_id = row["sc_id"]
        nse = row["nse_symbol"]
        try:
            df = yf.download(
                f"{nse}.NS",
                start=since.isoformat(),
                end=(until).isoformat(),
                auto_adjust=True,
                progress=False,
                threads=False,
            )
        except Exception as e:
            summary["errors"] += 1
            summary["skipped"].append({"sc_id": sc_id, "reason": str(e)[:120]})
            continue

        if df is None or df.empty:
            summary["skipped"].append({"sc_id": sc_id, "reason": "no data"})
            continue

        # yfinance returns multiindex columns when threads=True; with threads=False
        # we still sometimes see them. Flatten defensively.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        records = []
        for ts, r in df.iterrows():
            d = ts.date() if hasattr(ts, "date") else ts
            close = _safe_float(r.get("Close"))
            if close is None or close <= 0:
                continue
            records.append((
                sc_id, d,
                _safe_float(r.get("Open")),
                _safe_float(r.get("High")),
                _safe_float(r.get("Low")),
                close,
                close,
                int(r.get("Volume") or 0),
                "yfinance",
            ))

        if records:
            async with pool.acquire() as conn:
                await conn.executemany(
                    """
                    INSERT INTO mc.daily_prices
                      (sc_id, trade_date, open, high, low, close, close_raw, volume, source)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                    ON CONFLICT (sc_id, trade_date) DO UPDATE
                      SET close = EXCLUDED.close,
                          open = EXCLUDED.open,
                          high = EXCLUDED.high,
                          low = EXCLUDED.low,
                          volume = EXCLUDED.volume,
                          source = EXCLUDED.source
                    """,
                    records,
                )
            summary["ok"] += 1
            summary["rows_inserted"] += len(records)

        await asyncio.sleep(sleep_between)

    return summary


def _safe_float(v):
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None
