"""Main backtest loop.

Pragmatic v1 — equal-weight, T+1 next-open execution, fixed slippage and
commission. Assumes prices are already populated in mc.daily_prices for the
companies and dates being tested.

Flow per rebalance date T:
  1. Run universe query at T → list of sc_ids.
  2. Compute target equal-weight allocation in INR.
  3. Generate trade list = diff vs current positions.
  4. Execute next business day at the open price (close as fallback) with
     bps slippage and commission.
  5. Mark to market every trading day until next rebalance.

If a rebalance lands the universe empty, the engine holds 100% cash.

Results are returned as a dict ready for JSON serialisation, plus a small
DataFrame-shaped equity curve.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, timedelta

import asyncpg

from ..expr import compile_to_sql, parse_expression
from ..fields import Registry, load_default_registry
from ..universe import universe_at
from .calendar import next_business_day, rebalance_dates


@dataclass
class BacktestConfig:
    expression: str
    start: date
    end: date
    rebalance: str = "Q"
    starting_capital: float = 1_000_000.0
    slippage_bps: float = 10.0
    commission_bps: float = 3.0
    min_price: float = 5.0
    benchmark_sc_id: str | None = None     # an sc_id whose price series is the benchmark
    basis: str = "consolidated"


@dataclass
class BacktestResult:
    config: BacktestConfig
    equity_curve: list[dict]               # [{"date":..., "value":..., "cash":..., "n":...}]
    rebalances: list[dict]                 # one per rebalance date
    trades: list[dict]                     # every executed trade
    universe_audit: list[dict]             # the FIRST rebalance: company-by-company qualification
    benchmark_curve: list[dict] | None
    leaf_fields: list[str]
    referenced_fields: list[str]
    warnings: list[str] = field(default_factory=list)


# ---- Public entry ------------------------------------------------------


async def run_backtest(
    pool_or_dsn,
    cfg: BacktestConfig,
    *,
    registry: Registry | None = None,
) -> BacktestResult:
    registry = registry or load_default_registry()

    # Accept either a pool or a DSN string for ergonomics.
    own_pool = False
    if isinstance(pool_or_dsn, str):
        pool = await asyncpg.create_pool(dsn=pool_or_dsn, min_size=1, max_size=4)
        own_pool = True
    else:
        pool = pool_or_dsn

    try:
        return await _run(pool, cfg, registry)
    finally:
        if own_pool:
            await pool.close()


# ---- Internals ---------------------------------------------------------


async def _run(pool, cfg: BacktestConfig, registry: Registry) -> BacktestResult:
    rb_dates = rebalance_dates(cfg.start, cfg.end, cfg.rebalance)
    if not rb_dates:
        raise ValueError("no rebalance dates in the requested window")

    # Pre-compile the expression once (the SQL is the same for every date,
    # only the $1 parameter changes).
    ast = parse_expression(cfg.expression)
    compiled = compile_to_sql(ast, registry, basis=cfg.basis)

    cash = cfg.starting_capital
    holdings: dict[str, float] = {}      # sc_id -> shares

    rebalances: list[dict] = []
    trades: list[dict] = []
    equity_curve: list[dict] = []
    universe_audit: list[dict] = []
    warnings: list[str] = []

    async with pool.acquire() as conn:
        # Walk through every business day, performing rebalances on rb_dates.
        all_days = _all_business_days(cfg.start, cfg.end)
        rb_set = set(rb_dates)

        for i, day in enumerate(all_days):
            # Rebalance check.
            if day in rb_set:
                snap_rows = await conn.fetch(
                    compiled.sql, day, *compiled.params
                )
                snap = [dict(r) for r in snap_rows]
                if i == 0 or not universe_audit:
                    universe_audit = list(snap)

                # Determine target allocation: equal weight across qualifying
                # names that have a tradeable price tomorrow.
                trade_day = _next_business_day_or_same(all_days, i + 1)
                target_sc_ids = [r["sc_id"] for r in snap]
                if not target_sc_ids:
                    warnings.append(f"empty universe at {day}; holding cash")

                # Get next-day open prices for all names involved (target ∪ held).
                involved = sorted(set(target_sc_ids) | set(holdings.keys()))
                exec_prices = await _open_prices(conn, involved, trade_day)

                # Mark portfolio to market BEFORE rebalance using yesterday's close
                # so cash + position values reconcile.
                mtm_prices = await _close_prices(conn, list(holdings.keys()), day)
                portfolio_value = cash + sum(
                    sh * mtm_prices.get(s, 0.0) for s, sh in holdings.items()
                )

                # Filter targets to those with valid execution price.
                tradeable_targets = [
                    s for s in target_sc_ids
                    if exec_prices.get(s) is not None
                    and exec_prices[s] >= cfg.min_price
                ]
                n = len(tradeable_targets)
                # Reserve a small headroom so target_qty * (price+slippage) +
                # commission fits inside target_value. Otherwise the buy gets
                # skipped on rebalances where target_value ≈ portfolio_value.
                cost_overhead = 1 + (cfg.commission_bps / 10000)
                target_value = (portfolio_value / n) if n else 0.0
                target_shares = {
                    s: int((target_value / cost_overhead) // _exec_buy(exec_prices[s], cfg))
                    for s in tradeable_targets
                }

                # Diff and trade.
                rb_trades = []
                # Sells first (frees cash for buys).
                for s, shares in list(holdings.items()):
                    target_shares.setdefault(s, 0)
                    if target_shares[s] < shares and exec_prices.get(s):
                        sell_qty = shares - target_shares[s]
                        sell_px = _exec_sell(exec_prices[s], cfg)
                        proceeds = sell_qty * sell_px
                        proceeds -= proceeds * (cfg.commission_bps / 10000)
                        cash += proceeds
                        if target_shares[s] == 0:
                            del holdings[s]
                        else:
                            holdings[s] = target_shares[s]
                        rb_trades.append({
                            "date": str(trade_day),
                            "sc_id": s,
                            "side": "SELL",
                            "shares": sell_qty,
                            "price": round(sell_px, 4),
                            "proceeds": round(proceeds, 2),
                        })

                # Buys.
                for s in tradeable_targets:
                    held = holdings.get(s, 0)
                    if target_shares[s] > held:
                        buy_qty = target_shares[s] - held
                        buy_px = _exec_buy(exec_prices[s], cfg)
                        cost = buy_qty * buy_px
                        cost += cost * (cfg.commission_bps / 10000)
                        if cost <= cash + 1e-6:
                            cash -= cost
                            holdings[s] = held + buy_qty
                            rb_trades.append({
                                "date": str(trade_day),
                                "sc_id": s,
                                "side": "BUY",
                                "shares": buy_qty,
                                "price": round(buy_px, 4),
                                "cost": round(cost, 2),
                            })
                        else:
                            warnings.append(
                                f"skipped buy of {buy_qty} {s} at {trade_day}: "
                                f"need {cost:.0f} but only {cash:.0f} cash"
                            )

                trades.extend(rb_trades)
                rebalances.append({
                    "date": str(day),
                    "trade_date": str(trade_day),
                    "universe_size": len(snap),
                    "tradeable": n,
                    "n_trades": len(rb_trades),
                    "portfolio_value_before": round(portfolio_value, 2),
                })

            # Daily MTM at close.
            close_prices = await _close_prices(conn, list(holdings.keys()), day)
            position_value = sum(
                sh * close_prices.get(s, 0.0) for s, sh in holdings.items()
            )
            equity_curve.append({
                "date": str(day),
                "value": round(cash + position_value, 2),
                "cash": round(cash, 2),
                "positions": len(holdings),
            })

        # Benchmark
        benchmark_curve = None
        if cfg.benchmark_sc_id:
            benchmark_curve = await _benchmark_curve(
                conn, cfg.benchmark_sc_id,
                all_days, cfg.starting_capital,
            )

    return BacktestResult(
        config=cfg,
        equity_curve=equity_curve,
        rebalances=rebalances,
        trades=trades,
        universe_audit=universe_audit,
        benchmark_curve=benchmark_curve,
        leaf_fields=[s.name for s in compiled.leaf_fields],
        referenced_fields=compiled.referenced_fields,
        warnings=warnings,
    )


def _exec_buy(price: float, cfg: BacktestConfig) -> float:
    return price * (1 + cfg.slippage_bps / 10000)


def _exec_sell(price: float, cfg: BacktestConfig) -> float:
    return price * (1 - cfg.slippage_bps / 10000)


def _all_business_days(start: date, end: date) -> list[date]:
    out = []
    d = next_business_day(start)
    while d <= end:
        out.append(d)
        d = next_business_day(d + timedelta(days=1))
    return out


def _next_business_day_or_same(days: list[date], idx: int) -> date:
    if idx < len(days):
        return days[idx]
    return days[-1]


async def _open_prices(conn, sc_ids: list[str], d: date) -> dict[str, float]:
    if not sc_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (sc_id) sc_id,
               COALESCE(open, close)::float8 AS px
        FROM mc.daily_prices
        WHERE sc_id = ANY($1::text[])
          AND trade_date >= $2
        ORDER BY sc_id, trade_date ASC
        """,
        sc_ids, d,
    )
    return {r["sc_id"]: r["px"] for r in rows}


async def _close_prices(conn, sc_ids: list[str], d: date) -> dict[str, float]:
    if not sc_ids:
        return {}
    rows = await conn.fetch(
        """
        SELECT DISTINCT ON (sc_id) sc_id, close::float8 AS px
        FROM mc.daily_prices
        WHERE sc_id = ANY($1::text[])
          AND trade_date <= $2
        ORDER BY sc_id, trade_date DESC
        """,
        sc_ids, d,
    )
    return {r["sc_id"]: r["px"] for r in rows}


async def _benchmark_curve(
    conn, sc_id: str, days: list[date], starting_capital: float,
) -> list[dict]:
    rows = await conn.fetch(
        """
        SELECT trade_date, close::float8 AS px
        FROM mc.daily_prices
        WHERE sc_id = $1 AND trade_date BETWEEN $2 AND $3
        ORDER BY trade_date
        """,
        sc_id, days[0], days[-1],
    )
    if not rows:
        return []
    base = float(rows[0]["px"])
    by_date = {r["trade_date"]: float(r["px"]) for r in rows}
    last = base
    out = []
    for d in days:
        px = by_date.get(d, last)
        last = px
        out.append({"date": str(d), "value": round(starting_capital * (px / base), 2)})
    return out
