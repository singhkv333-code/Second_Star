"""safe_compute — the COMPUTE lane's sandbox.

Two things matter and both are tested here:
  1. The calculator actually calculates (the percentile-ranking ask that
     motivated the lane, plus the common finance transforms).
  2. The whitelist holds — imports, attribute escapes, dunders, loops,
     and resource bombs are all rejected or die in the child process,
     never in the API worker.
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


# ── Whitelist holds ───────────────────────────────────────────────────


@pytest.mark.parametrize("code", [
    "import os",                                # Import node
    "__import__('os')",                         # dunder name
    "open('/etc/passwd')",                      # non-whitelisted call
    "''.__class__.__mro__",                     # attribute escape
    "(1).__class__",                            # attribute escape via int
    "[x for x in ().__class__.__bases__]",      # attribute in comprehension
    "for i in range(3): pass",                  # loops excluded
    "while True: pass",                         # loops excluded
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
