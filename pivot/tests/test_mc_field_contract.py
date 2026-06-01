"""Live data-contract test for the mc fundamental field registry.

The expr factor engine (Engine 1, ``pivot-backtester``) compiles user-facing
fields — ``revenue``, ``roe``, the pre-computed ratios — to SQL against the live
``mc`` schema in the ``financials`` DB. That field <-> schema contract has
drifted silently before:

  * the TTM CTE filtered a ``statement = 'quarterly_results'`` that the live data
    does not have (it is annual-only), zeroing every TTM field — hence ``roe`` /
    ``pe_ratio`` returned no companies even though the data was there;
  * several ``line_items`` lists named columns Moneycontrol no longer emits
    (``revenue`` -> "Net Sales", ``cash_from_operations`` spelling, etc).

Unit tests assert SQL *structure* against a registry; they cannot catch this,
because the drift is between our YAML and the real data. This test runs the
compiled SQL for the headline fields against the real DB and asserts each returns
a non-trivial universe, so a future drift fails here instead of shipping a
silently-empty screener.

Skips cleanly when the financials DB is unreachable (e.g. CI without Postgres).
"""
from __future__ import annotations

import asyncio
import os
from datetime import date
from pathlib import Path

import asyncpg
import pytest

from backtester.universe import universe_at

AS_OF = date(2024, 6, 3)


def _real_financials_dsn() -> str | None:
    """The live ``financials`` Postgres DSN, resolved independently of the test
    conftest (which clobbers DATABASE_URL/settings with sqlite at import time).

    Order: ``FINANCIALS_DSN`` env override -> the ``DATABASE_URL`` line in the
    repo ``.env`` (with the db name swapped to ``financials``). Returns ``None``
    if no Postgres DSN can be found, so the test skips rather than fails.
    """
    explicit = os.environ.get("FINANCIALS_DSN")
    if explicit and explicit.startswith(("postgresql://", "postgres://")):
        return explicit

    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.is_file():
        return None
    base: str | None = None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL=") and "postgres" in line:
            base = line.split("=", 1)[1].strip().strip('"').strip("'")
            break
    if not base:
        return None
    base = base.replace("postgresql+psycopg2://", "postgresql://").replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    if "/financials" in base:
        return base
    head, _, _ = base.rpartition("/")
    return f"{head}/financials"

# (expression, basis, floor). Floors are deliberately conservative — far below
# today's live counts, far above the zero the old broken contract produced — so
# the test catches drift-to-empty without being brittle to normal data churn.
CONTRACT: list[tuple[str, str, int]] = [
    # TTM leaf resolves via the annual fallback (was 0 under the old CTE).
    ("net_profit_ttm > 0", "consolidated", 500),
    ("roe > 0", "consolidated", 150),               # derived; depends on net_profit_ttm
    # line_item mappings that had drifted.
    ("revenue > 0", "consolidated", 1000),
    ("cash_from_operations > 0", "consolidated", 500),
    # pre-computed ratios promoted to first-class fields.
    ("return_on_equity > 15", "consolidated", 50),
    ("net_profit_margin > 10", "consolidated", 50),
    ("interest_coverage > 3", "consolidated", 50),
    ("debt_to_equity_ratio < 0.5", "consolidated", 50),
    ("price_to_book < 3", "consolidated", 50),
    # a realistic multi-factor quality screen, end to end.
    (
        "return_on_equity > 15 AND debt_to_equity_ratio < 0.5 AND net_profit_margin > 10",
        "consolidated",
        20,
    ),
    # cross-sectional transforms (Phase 2.1) compiled to the two-level CTE and run
    # against real data: industry-neutral factor + top industry-neutral decile.
    ("neutralize(return_on_equity) > 0", "consolidated", 50),
    ("decile(neutralize(return_on_equity)) == 10", "consolidated", 20),
]


def test_mc_field_contract_returns_real_universes():
    dsn = _real_financials_dsn()
    if not dsn:
        pytest.skip("no Postgres financials DSN resolvable; skipping live contract test")

    async def run() -> list[str]:
        try:
            conn = await asyncpg.connect(dsn=dsn, timeout=5)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"financials DB unreachable; skipping live contract test: {e}")
        failures: list[str] = []
        try:
            for expr, basis, floor in CONTRACT:
                snap = await universe_at(conn, expr, AS_OF, basis=basis)
                n = len(snap.rows)
                if n < floor:
                    failures.append(f"  {expr!r} @ {basis}: {n} companies (expected >= {floor})")
        finally:
            await conn.close()
        return failures

    failures = asyncio.run(run())
    assert not failures, (
        "Field <-> mc schema contract has drifted (compiled SQL returns too few "
        "companies). Check base_fields.yaml line_items and the TTM mapping in "
        "compiler._emit_one_cte:\n" + "\n".join(failures)
    )
