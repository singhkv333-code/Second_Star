"""safe_compute — the COMPUTE lane's sandbox.

Two things matter and both are tested here:
  1. The calculator actually calculates (the percentile-ranking ask that
     motivated the lane, plus the common finance transforms, plus bounded
     `for` loops for basket/weight-style computations).
  2. The whitelist holds — imports, attribute escapes, dunders, `while`,
     defs, and resource bombs are all rejected or die in the child
     process, never in the API worker. `for` is allowed but stays bounded
     by the same range-size cap / CPU rlimit / wall-clock timeout that
     already bounded comprehensions and lambdas.
"""
from __future__ import annotations

from backend.services.safe_compute import (
    ComputeValidationError,
    run_compute,
    validate_code,
)
import pytest


# ── The motivating case ───────────────────────────────────────────────


def test_percentile_ranking_of_stocks() -> None:
    code = (
        "vals = {'TCS': 3.2, 'INFY': 1.8, 'WIPRO': 0.9, 'HCLTECH': 2.5}\n"
        "s = sorted(vals.values())\n"
        "{k: round(100 * sum(1 for x in s if x <= v) / len(s)) "
        "for k, v in vals.items()}"
    )
    res = run_compute(code)
    assert res.ok, res.error
    assert res.result == {"TCS": 100, "INFY": 50, "WIPRO": 25, "HCLTECH": 75}


def test_pnl_what_if() -> None:
    # "if RELIANCE falls 8% what's my P&L on 50 shares @ ₹1,520"
    res = run_compute("qty=50; entry=1520.0; round(qty*entry*-0.08, 2)")
    assert res.ok
    assert res.result == -6080.0


def test_statistics_module() -> None:
    res = run_compute("statistics.median([12, 4, 7, 19, 3])")
    assert res.ok
    assert res.result == 7


def test_cagr_from_endpoints() -> None:
    res = run_compute("round(((181000/100000) ** (1/5) - 1) * 100, 2)")
    assert res.ok
    assert res.result == pytest.approx(12.61, abs=0.01)


def test_lambda_sort_key() -> None:
    res = run_compute(
        "rows=[('A', 3), ('B', 1), ('C', 2)]\n"
        "[k for k, _ in sorted(rows, key=lambda r: -r[1])]"
    )
    assert res.ok
    assert res.result == ["A", "C", "B"]


# ── `for` loops (bounded, previously crashed the sandbox) ──────────────


def test_for_loop_basket_weighting() -> None:
    # The bug: the model built a basket with a plain `for` loop instead of
    # a comprehension and the sandbox rejected the whole call, discarding
    # an already-correct upstream result.
    code = (
        "prices = {'TCS': 3900, 'INFY': 1800, 'WIPRO': 550}\n"
        "capital = 100000\n"
        "alloc = {}\n"
        "for sym, px in prices.items():\n"
        "    alloc[sym] = int(capital / len(prices) / px)\n"
        "alloc"
    )
    res = run_compute(code)
    assert res.ok, res.error
    assert res.result == {"TCS": 8, "INFY": 18, "WIPRO": 60}


def test_dict_merge_with_bitor() -> None:
    # Reported live 2026-07-14: a basket-allocation compute call used
    # Python's `|` dict-merge operator and the whole call was rejected
    # as "disallowed construct: BitOr", discarding an already-correct
    # build_strategy result and aborting the turn.
    code = (
        "sleeve = {'TCS': 40000, 'INFY': 18000}\n"
        "gold = {'SGB': 33000, 'total': 500000}\n"
        "sleeve | gold"
    )
    res = run_compute(code)
    assert res.ok, res.error
    assert res.result == {"TCS": 40000, "INFY": 18000, "SGB": 33000, "total": 500000}


def test_for_loop_with_if_break_continue() -> None:
    code = (
        "nums = [1,2,3,4,5,6,7,8,9,10]\n"
        "total = 0\n"
        "for n in nums:\n"
        "    if n == 6:\n"
        "        break\n"
        "    if n % 2 == 0:\n"
        "        continue\n"
        "    total += n\n"
        "total"
    )
    res = run_compute(code)
    assert res.ok, res.error
    assert res.result == 9


def test_for_loop_still_bounded_by_range_cap() -> None:
    # Allowing `for` at the AST level must not remove the runtime bound —
    # a for-loop over an oversized range still dies honestly in the child.
    res = run_compute("total = 0\nfor i in range(10_000_000):\n    total += i\ntotal")
    assert not res.ok
    assert "range too large" in (res.error or "")


# ── Whitelist holds ───────────────────────────────────────────────────


@pytest.mark.parametrize("code", [
    "import os",                                # Import node
    "__import__('os')",                         # dunder name
    "open('/etc/passwd')",                      # non-whitelisted call
    "''.__class__.__mro__",                     # attribute escape
    "(1).__class__",                            # attribute escape via int
    "[x for x in ().__class__.__bases__]",      # attribute in comprehension
    "while True: pass",                         # unbounded loop excluded
    "def f(): return 1",                        # defs excluded
    "exec('1+1')",                              # non-whitelisted call
    "eval('1+1')",                              # non-whitelisted call
    "getattr(1, 'real')",                       # non-whitelisted call
    "2 ** 999999",                              # exponent cap
    "99999999999999999999 + 1",                 # const-int literal cap
    "'{0.__class__}'.format(1)",                # str.format traversal gadget
    "dict.mro()",                               # type.mro gadget
])
def test_rejected_constructs(code: str) -> None:
    with pytest.raises(ComputeValidationError):
        validate_code(code)


def test_rejected_code_returns_error_not_raise() -> None:
    res = run_compute("import os")
    assert not res.ok
    assert "disallowed" in (res.error or "").lower() or "Import" in (res.error or "")


def test_huge_range_dies_in_child() -> None:
    res = run_compute("sum(range(10**9))")
    assert not res.ok
    assert "range too large" in (res.error or "")


def test_recursion_dies_in_child_not_worker() -> None:
    res = run_compute("f = lambda x: f(x)\nf(1)")
    assert not res.ok  # RecursionError in the child, relayed honestly


def test_syntax_error_is_honest() -> None:
    res = run_compute("1 +")
    assert not res.ok
    assert "syntax" in (res.error or "").lower()


def test_empty_code() -> None:
    res = run_compute("   ")
    assert not res.ok
