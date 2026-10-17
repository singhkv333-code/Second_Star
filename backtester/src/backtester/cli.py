"""Backtester CLI — fields list/show, validate, universe (engine cmds: TODO)."""
from __future__ import annotations

import asyncio
import json
from datetime import date as _date

import asyncpg
import typer
from rich.console import Console
from rich.table import Table

from .config import get_settings
from .expr import compile_to_sql, parse_expression, ValidationError
from .expr import validate as _validate_expr
from .fields import load_default_registry
from .universe import universe_at


app = typer.Typer(no_args_is_help=True, add_completion=False)
fields_app = typer.Typer(no_args_is_help=True, add_completion=False)
app.add_typer(fields_app, name="fields", help="Inspect the field dictionary.")
console = Console()


# ---- fields ------------------------------------------------------------


@fields_app.command("list")
def fields_list() -> None:
    """List every available field, grouped by kind."""
    reg = load_default_registry()

    base_t = Table(title="Base fields", show_lines=False)
    base_t.add_column("name"); base_t.add_column("statement"); base_t.add_column("ttm"); base_t.add_column("unit")
    for name, spec in sorted(reg.base.items()):
        base_t.add_row(name, spec.statement, "✓" if spec.ttm_eligible else "", spec.unit)
    console.print(base_t)

    comp_t = Table(title="Computed fields", show_lines=False)
    comp_t.add_column("name"); comp_t.add_column("expression"); comp_t.add_column("unit")
    for name, spec in sorted(reg.computed.items()):
        comp_t.add_row(name, spec.expr_text, spec.unit)
    console.print(comp_t)

    console.print("[dim]Special leaves: price (latest adjusted close)[/dim]")
    console.print("[dim]Suffix _ttm available on TTM-eligible base fields.[/dim]")


@fields_app.command("show")
def fields_show(name: str = typer.Argument(...)) -> None:
    """Show one field — definition, aliases, unit."""
    reg = load_default_registry()
    try:
        spec = reg.lookup(name)
    except KeyError as e:
        raise typer.Exit(str(e))
    console.print_json(data=_spec_to_dict(spec))


def _spec_to_dict(spec) -> dict:
    d = {"name": spec.name, "kind": spec.kind, "unit": getattr(spec, "unit", None)}
    if spec.kind == "base":
        d.update(
            statement=spec.statement,
            basis_default=spec.basis_default,
            line_items=list(spec.line_items),
            ttm_eligible=spec.ttm_eligible,
            ttm=spec.ttm,
            description=spec.description,
        )
    elif spec.kind == "computed":
        d.update(expr=spec.expr_text, description=spec.description)
    return d


# ---- validate / compile ------------------------------------------------


@app.command()
def validate(expr: str = typer.Argument(..., help="Expression to validate.")) -> None:
    """Validate an expression without running it."""
    reg = load_default_registry()
    try:
        ast = parse_expression(expr)
        result = _validate_expr(ast, reg)
    except ValidationError as e:
        console.print(f"[red]invalid:[/red] {e}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]parse error:[/red] {e}")
        raise typer.Exit(1)
    console.print("[green]ok[/green]")
    console.print(f"references: {', '.join(result.referenced_fields)}")
    for w in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {w}")


@app.command(name="compile")
def compile_(expr: str = typer.Argument(...)) -> None:
    """Compile an expression to SQL and print it. Useful for debugging."""
    reg = load_default_registry()
    ast = parse_expression(expr)
    _validate_expr(ast, reg)
    compiled = compile_to_sql(ast, reg)
    console.print("[bold]Predicate (expanded):[/bold] " + compiled.expansion_text)
    console.print("[bold]Leaf fields:[/bold] " + ", ".join(s.name for s in compiled.leaf_fields))
    console.print("[bold]SQL:[/bold]")
    console.print(compiled.sql)
    console.print(f"[bold]Params (after $1=date):[/bold] {compiled.params}")


# ---- universe ----------------------------------------------------------


@app.command()
def universe(
    expr: str = typer.Option(..., "--expr"),
    as_of: str = typer.Option(..., "--as-of", help="YYYY-MM-DD"),
    basis: str = typer.Option("consolidated", "--basis"),
    limit: int = typer.Option(50, "--limit"),
) -> None:
    """Inspect the qualifying universe at a single date."""
    settings = get_settings()
    parsed_date = _date.fromisoformat(as_of)

    async def _go():
        conn = await asyncpg.connect(dsn=settings.financials_dsn)
        try:
            return await universe_at(conn, expr, parsed_date, basis=basis)
        finally:
            await conn.close()

    snap = asyncio.run(_go())
    if not snap.rows:
        console.print("[yellow]universe is empty at this date[/yellow]")
        return
    t = Table(title=f"Universe @ {parsed_date} — {len(snap.rows)} companies")
    cols = list(snap.rows[0].keys())
    for c in cols: t.add_column(c)
    for r in snap.rows[:limit]:
        t.add_row(*[("" if r[c] is None else str(r[c])) for c in cols])
    console.print(t)
    if len(snap.rows) > limit:
        console.print(f"[dim]... {len(snap.rows) - limit} more rows truncated.[/dim]")


@app.command()
def run(
    expr: str = typer.Option(..., "--expr", help="Predicate, e.g. 'pe_ratio < 15 AND roe > 12'."),
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="YYYY-MM-DD"),
    rebalance: str = typer.Option("Q", "--rebalance", help="D|W|M|Q|Y"),
    capital: float = typer.Option(1_000_000.0, "--capital"),
    benchmark: str = typer.Option(None, "--benchmark", help="An sc_id whose price series to compare against."),
    report_path: str = typer.Option("./reports/backtest.html", "--report"),
    basis: str = typer.Option("consolidated", "--basis"),
    print_summary: bool = typer.Option(True, "--print-summary/--no-summary"),
) -> None:
    """Run a backtest end-to-end and write an HTML report."""
    from datetime import date as _d
    from pathlib import Path
    from .engine import BacktestConfig, run_backtest as _run
    from .metrics import compute_metrics
    from .report import generate_html_report

    cfg = BacktestConfig(
        expression=expr,
        start=_d.fromisoformat(start),
        end=_d.fromisoformat(end),
        rebalance=rebalance,
        starting_capital=capital,
        benchmark_sc_id=benchmark,
        basis=basis,
    )
    settings = get_settings()

    async def _go():
        return await _run(settings.financials_dsn, cfg)

    result = asyncio.run(_go())
    metrics = compute_metrics(
        result.equity_curve,
        benchmark_curve=result.benchmark_curve,
        risk_free_rate=settings.risk_free_rate,
        trades=result.trades,
    ).to_dict()

    output = generate_html_report(
        expression=expr,
        start=cfg.start, end=cfg.end,
        rebalance=cfg.rebalance,
        slippage_bps=cfg.slippage_bps, commission_bps=cfg.commission_bps,
        equity_curve=result.equity_curve,
        benchmark_curve=result.benchmark_curve,
        metrics=metrics,
        rebalances=result.rebalances,
        trades=result.trades,
        universe_audit=result.universe_audit,
        leaf_fields=result.leaf_fields,
        warnings=result.warnings,
        output_path=Path(report_path),
    )

    if print_summary:
        console.print(f"[green]ok[/green] report: {output}")
        console.print_json(data={
            "expression": expr,
            "start": start, "end": end, "rebalance": rebalance,
            "metrics": metrics,
            "n_rebalances": len(result.rebalances),
            "n_trades": len(result.trades),
            "warnings": result.warnings[:5],
        })


@app.command(name="backfill-prices")
def backfill_prices_cmd(
    since: str = typer.Option(..., "--since", help="YYYY-MM-DD"),
    until: str = typer.Option(None, "--until"),
    sc_ids: str = typer.Option(None, "--sc-ids", help="Comma-separated subset; default: every nse-mapped company."),
) -> None:
    """yfinance backfill of mc.daily_prices for NSE-mapped companies."""
    from datetime import date as _d
    from .data.prices import backfill_prices

    settings = get_settings()
    since_d = _d.fromisoformat(since)
    until_d = _d.fromisoformat(until) if until else None
    ids = [s.strip() for s in sc_ids.split(",")] if sc_ids else None

    async def _go():
        pool = await asyncpg.create_pool(dsn=settings.financials_dsn, min_size=1, max_size=4)
        try:
            return await backfill_prices(pool, since=since_d, until=until_d, sc_ids=ids)
        finally:
            await pool.close()

    summary = asyncio.run(_go())
    console.print_json(data=summary)


if __name__ == "__main__":
    app()
