"""Regression guard for the risk-parity (ERC) fixed-point bug.

An earlier version of ``weighting._risk_parity_weights`` renormalised the
running weight vector to sum-to-1 after EVERY cyclical-coordinate sweep, not
just once at the end. That rescaling moves the target the per-asset quadratic
solve is aiming at on every sweep, so the iteration still "converges"
(successive deltas shrink to ~0) but to a fixed point that does NOT satisfy
equal risk contribution once any off-diagonal correlation is present — verified
against an 8-asset correlated covariance where the buggy version produced
risk-contribution shares spread over roughly [-0.07, +0.29] (even a negative
share) instead of the equal 1/8 each.

These tests pin the scale-invariance property that makes the bug detectable
without cross-checking every basket build by hand:
  * for 2 assets, true ERC weights are ALWAYS w_i ∝ 1/σ_i, independent of the
    correlation between them (the cross term cancels algebraically);
  * for n assets, every asset's realised share of total portfolio variance
    must be equal (within numerical tolerance) at convergence.

Hermetic — pure numpy, no I/O.
"""
from __future__ import annotations

import numpy as np

from backend.services.weighting import _risk_parity_weights


def _risk_contribution_shares(cov: np.ndarray, w: np.ndarray) -> np.ndarray:
    rc = w * (cov @ w)
    return rc / rc.sum()


def test_erc_two_asset_is_correlation_invariant() -> None:
    """True 2-asset ERC weights depend only on relative volatility, never on
    correlation (a well-known closed-form special case) — the sigma=0.10 vs
    0.20 pair should land at (2/3, 1/3) regardless of rho."""
    sig_a, sig_b = 0.10, 0.20
    expected = np.array([sig_b, sig_a]) / (sig_a + sig_b)  # inverse-vol == true ERC here

    for rho in (0.0, 0.5, 0.9, -0.3):
        cov = np.array(
            [
                [sig_a**2, rho * sig_a * sig_b],
                [rho * sig_a * sig_b, sig_b**2],
            ]
        )
        w = _risk_parity_weights(cov)
        assert np.allclose(w, expected, atol=1e-6), (rho, w, expected)


def test_erc_equalises_risk_contribution_with_correlation() -> None:
    """A realistic correlated multi-asset covariance must converge to EQUAL
    risk-contribution shares — the property the scheme is named for. This is
    the exact shape that exposed the renormalization bug (an 8-asset basket
    with meaningful pairwise correlation)."""
    rng = np.random.default_rng(42)
    n = 8
    vols = rng.uniform(0.15, 0.40, n)
    a = rng.standard_normal((n, n)) * 0.3 + np.eye(n)
    corr = a @ a.T
    d = np.sqrt(np.diag(corr))
    corr = corr / np.outer(d, d)
    cov = np.outer(vols, vols) * corr

    w = _risk_parity_weights(cov)
    assert np.all(w >= -1e-9), w
    shares = _risk_contribution_shares(cov, w)
    assert np.all(shares > 0), (
        "a risk-parity solution must never assign a NEGATIVE risk-contribution "
        f"share to a long-only holding: {shares}"
    )
    assert shares.max() - shares.min() < 1e-6, (
        f"risk contributions are not equal (spread={shares.max() - shares.min():.4f}): {shares}"
    )
