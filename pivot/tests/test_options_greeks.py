"""F&O P0 — owned Black-76 engine vs oracle + finite differences, and
the IV solver's status discipline (never fabricate an IV)."""
import numpy as np
import pytest

from backend.market.greeks.black76 import black76_greeks, black76_price, year_fraction
from backend.market.greeks.forward import synthetic_forward
from backend.market.greeks.iv import (
    IV_ILLIQUID,
    IV_NO_ARB,
    IV_OK,
    IV_WIDE_SPREAD,
    implied_vol,
)

# A representative grid: deep ITM → ATM → deep OTM, short and long tenor.
F = 23500.0
R = 0.065
GRID = [
    # (K, sigma, T, flag)
    (21000.0, 0.18, 0.02, 1), (23000.0, 0.14, 0.02, 1),
    (23500.0, 0.12, 0.02, 1), (24000.0, 0.13, 0.02, 1),
    (26000.0, 0.22, 0.02, 1),
    (21000.0, 0.18, 0.02, -1), (23500.0, 0.12, 0.02, -1),
    (26000.0, 0.22, 0.02, -1),
    (23500.0, 0.15, 0.25, 1), (23500.0, 0.15, 0.25, -1),
]


# ── Oracle parity (py_vollib scalar, run in a SUBPROCESS) ────────────
#
# Why a subprocess: importing py_vollib_vectorized anywhere in-process
# (backend/market/greeks/iv.py does) monkeypatches BOTH the py_vollib
# and vollib namespaces with numba dispatchers, and the patched scalar
# ``black`` has a runaway-recursion typing bug on ITM inputs. The only
# way to consult the true scalar oracle is a clean interpreter that
# never imports the vectorized package.

_ORACLE_SCRIPT = """
import json, sys
from py_vollib.black import black
from py_vollib.black.greeks import analytical as g
out = []
for K, sigma, T, flag in json.loads(sys.argv[1]):
    fc = "c" if flag == 1 else "p"
    F, R = 23500.0, 0.065
    out.append({
        "price": black(fc, F, K, T, R, sigma),
        "delta": g.delta(fc, F, K, T, R, sigma),
        "gamma": g.gamma(fc, F, K, T, R, sigma),
        "vega": g.vega(fc, F, K, T, R, sigma),
        "theta": g.theta(fc, F, K, T, R, sigma),
    })
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def oracle_values():
    import json
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-c", _ORACLE_SCRIPT, json.dumps(GRID)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        pytest.skip(f"py_vollib oracle unavailable: {proc.stderr[-200:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_price_and_greeks_match_py_vollib_oracle(oracle_values):
    for (K, sigma, T, flag), oracle in zip(GRID, oracle_values):
        g = black76_greeks(F, K, sigma, T, r=R, flag=flag)
        label = f"K={K} sigma={sigma} T={T} flag={flag}"
        assert float(g["price"]) == pytest.approx(oracle["price"], abs=1e-6), label
        assert float(g["delta"]) == pytest.approx(oracle["delta"], abs=1e-8), label
        assert float(g["gamma"]) == pytest.approx(oracle["gamma"], abs=1e-8), label
        # py_vollib vega is per-1%-vol, theta per-day — our conventions too.
        assert float(g["vega"]) == pytest.approx(oracle["vega"], abs=1e-8), label
        assert float(g["theta"]) == pytest.approx(oracle["theta"], abs=1e-6), label


# ── Finite-difference checks (no external dep — always run) ──────────


@pytest.mark.parametrize("K,sigma,T,flag", GRID)
def test_greeks_match_finite_difference(K, sigma, T, flag):
    g = black76_greeks(F, K, sigma, T, r=R, flag=flag)
    h = 1e-4 * F
    p_up = float(black76_price(F + h, K, sigma, T, r=R, flag=flag))
    p_dn = float(black76_price(F - h, K, sigma, T, r=R, flag=flag))
    p_0 = float(black76_price(F, K, sigma, T, r=R, flag=flag))
    assert float(g["delta"]) == pytest.approx((p_up - p_dn) / (2 * h), rel=1e-4, abs=1e-6)
    assert float(g["gamma"]) == pytest.approx(
        (p_up - 2 * p_0 + p_dn) / (h * h), rel=1e-3, abs=1e-8)

    hv = 1e-5
    v_up = float(black76_price(F, K, sigma + hv, T, r=R, flag=flag))
    v_dn = float(black76_price(F, K, sigma - hv, T, r=R, flag=flag))
    assert float(g["vega"]) == pytest.approx(
        (v_up - v_dn) / (2 * hv) / 100.0, rel=1e-4, abs=1e-6)

    # Central difference in T — one-sided FD carries O(h) truncation
    # error that the high theta-curvature of short-dated OTM strikes
    # turns into a false failure.
    ht = min(1e-6, T / 100.0)
    t_dn = float(black76_price(F, K, sigma, T - ht, r=R, flag=flag))
    t_up = float(black76_price(F, K, sigma, T + ht, r=R, flag=flag))
    fd_theta_per_year = (t_dn - t_up) / (2 * ht)
    assert float(g["theta"]) == pytest.approx(
        fd_theta_per_year / 365.0, rel=1e-3, abs=1e-6)


def test_degenerate_inputs_collapse_to_intrinsic_not_nan():
    g = black76_greeks(F, 23000.0, 0.0, 0.02, r=R, flag=1)
    assert float(g["price"]) == pytest.approx(
        np.exp(-R * 0.02) * 500.0, rel=1e-9)
    expired = black76_greeks(F, 23000.0, 0.2, 0.0, r=R, flag=1)
    assert float(expired["price"]) == pytest.approx(500.0)
    for k in ("price", "delta", "gamma", "theta", "vega"):
        assert np.isfinite(float(g[k])) and np.isfinite(float(expired[k]))


def test_put_call_parity():
    K, sigma, T = 23700.0, 0.14, 0.05
    c = float(black76_price(F, K, sigma, T, r=R, flag=1))
    p = float(black76_price(F, K, sigma, T, r=R, flag=-1))
    assert c - p == pytest.approx(np.exp(-R * T) * (F - K), abs=1e-9)


# ── IV solver: round-trip + status discipline ────────────────────────


def test_iv_round_trips_through_price():
    K = np.array([22500.0, 23000.0, 23500.0, 24000.0, 24500.0])
    sigma_true = np.array([0.16, 0.13, 0.115, 0.125, 0.15])
    T, flag = 0.04, 1.0
    price = black76_price(F, K, sigma_true, T, r=R, flag=flag)
    iv, status = implied_vol(price, F, K, T, flag, r=R)
    assert all(s == IV_OK for s in status)
    np.testing.assert_allclose(iv, sigma_true, rtol=1e-5)


def test_iv_round_trips_for_puts():
    K = np.array([23000.0, 23500.0, 24000.0])
    sigma_true = np.array([0.14, 0.12, 0.135])
    price = black76_price(F, K, sigma_true, 0.04, r=R, flag=-1.0)
    iv, status = implied_vol(price, F, K, 0.04, -1.0, r=R)
    assert all(s == IV_OK for s in status)
    np.testing.assert_allclose(iv, sigma_true, rtol=1e-5)


def test_iv_no_arb_violation_is_flagged_not_solved():
    # A call priced BELOW intrinsic and one priced above the forward bound.
    K = np.array([22000.0, 23500.0])
    bad = np.array([1000.0, 24000.0])  # intrinsic ≈ 1497, F·df ≈ 23449
    iv, status = implied_vol(bad, F, K, 0.04, 1.0, r=R)
    assert list(status) == [IV_NO_ARB, IV_NO_ARB]
    assert np.isnan(iv).all()


def test_iv_dead_quote_is_illiquid():
    iv, status = implied_vol(
        np.array([100.0]), F, np.array([23500.0]), 0.04, 1.0, r=R,
        bid=np.array([0.0]), ask=np.array([0.0]),
    )
    assert list(status) == [IV_ILLIQUID]
    assert np.isnan(iv).all()


def test_iv_wide_spread_is_solved_but_flagged():
    K = np.array([23500.0])
    price = black76_price(F, K, 0.12, 0.04, r=R, flag=1.0)
    mid = float(price[0])
    iv, status = implied_vol(
        np.array([mid]), F, K, 0.04, 1.0, r=R,
        bid=np.array([mid * 0.9]), ask=np.array([mid * 1.1]),  # 20% spread
    )
    assert list(status) == [IV_WIDE_SPREAD]
    assert iv[0] == pytest.approx(0.12, rel=1e-4)


def test_synthetic_forward_recovers_from_parity():
    T = 0.04
    K = [23400.0, 23500.0, 23600.0]
    calls = [float(black76_price(F, k, 0.12, T, r=R, flag=1)) for k in K]
    puts = [float(black76_price(F, k, 0.12, T, r=R, flag=-1)) for k in K]
    syn = synthetic_forward(K, calls, puts, spot=23480.0, T=T, r=R)
    assert syn == pytest.approx(F, rel=1e-6)


def test_year_fraction_intraday_clock_and_segment_close():
    from datetime import date, datetime
    import pytz

    ist = pytz.timezone("Asia/Kolkata")
    now = ist.localize(datetime(2026, 6, 4, 9, 30))
    t_nse = year_fraction(date(2026, 6, 4), segment="NFO-OPT", now=now)
    t_mcx = year_fraction(date(2026, 6, 4), segment="MCX-OPT", now=now)
    assert t_nse == pytest.approx(6.0 / (365.0 * 24.0), rel=1e-9)   # → 15:30
    assert t_mcx == pytest.approx(14.0 / (365.0 * 24.0), rel=1e-9)  # → 23:30
    # Expired floors at zero, never negative.
    assert year_fraction(date(2026, 6, 3), now=now) == 0.0
