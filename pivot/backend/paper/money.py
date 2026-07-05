"""Money helpers for the paper broker.

Reconciled-cash columns are Numeric(18,4); we keep all internal math in
Decimal quantized to 4 dp (paise precision) so a long fill/reserve/
release/settle chain reconciles exactly by replay — binary float would
drift cents. Cast to float() only at the JSON/API edge.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Union

# 4 decimal places — matches Numeric(18, 4).
CENTS = Decimal("0.0001")

# Fallback seed capital constant (tests import this). The live per-account
# seed is driven by ``settings.paper_seed_capital`` (env PAPER_SEED_CAPITAL,
# default 150000) in ``paper/accounts.get_or_create_account`` so a deployment
# can raise the beta budget without touching this constant or the tests.
SEED_CAPITAL = Decimal("150000.0000")

Number = Union[int, float, Decimal, str]


def to_money(x: Number) -> Decimal:
    """Quantize any numeric to a 4-dp Decimal. Routes through str() for
    floats so we never inherit binary float noise into the ledger."""
    if isinstance(x, Decimal):
        d = x
    else:
        d = Decimal(str(x))
    return d.quantize(CENTS, rounding=ROUND_HALF_UP)


def money_to_float(x: Number) -> float:
    """For the JSON edge only."""
    return float(to_money(x))
