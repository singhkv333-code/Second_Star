"""Regression locks for the P6 adversarial-review fixes (2026-05-30).

Each test pins a specific bug the review surfaced so it can't silently
reappear:
  1. MAJOR — the promotion gate must be ANDed with an on_track verdict (a
     decayed / execution_problem idea is NEVER promotion-ready).
  2. MAJOR — the Max DD gate must compare same-sign drawdowns (the DSL
     backtest stores DD as a POSITIVE magnitude; the forward stat is a
     NEGATIVE percent) and display them with a consistent sign.
  3. MINOR — the chat idea resolver must NOT attribute when conversation_id
     is missing (else unrelated chat ideas collapse into one).
"""
from __future__ import annotations

from backend.paper import scorecards as sc
from backend.paper.ideas import resolve_idea


# 1) Promotion gate is gated on an on_track verdict ───────────────────────

def test_decayed_idea_is_never_promotion_ready():
    """Statistically promotable (PSR/MinTRL/DSR all pass) BUT decayed vs its
    backtest -> verdict 'decayed' and promotion_ready False."""
    verdict, promo = sc._verdict_and_gate(
        n_obs=60,
        psr_val=0.99,                  # passes PSR gate
        mintrl_val=20.0,               # n_obs (60) >= mintrl
        dsr_val=0.99,                  # passes DSR gate
        cum_return_pct=1.0,            # flat-ish (not negative -> not exec)
        sharpe=0.20,                   # << half the backtest Sharpe -> decayed
        bt={"metrics": {"sharpe_ratio": 2.0, "total_return_pct": 25.0}},
    )
    assert verdict == "decayed"
    assert promo is False, "a decayed idea must not auto-advance paper->candidate"


def test_execution_problem_idea_is_never_promotion_ready():
    verdict, promo = sc._verdict_and_gate(
        n_obs=60, psr_val=0.99, mintrl_val=20.0, dsr_val=0.99,
        cum_return_pct=-3.0,           # live negative...
        sharpe=1.5,
        bt={"metrics": {"sharpe_ratio": 1.4, "total_return_pct": 20.0}},  # ...bt positive
    )
    assert verdict == "execution_problem"
    assert promo is False


def test_on_track_idea_is_promotion_ready_when_gates_pass():
    verdict, promo = sc._verdict_and_gate(
        n_obs=60, psr_val=0.99, mintrl_val=20.0, dsr_val=0.99,
        cum_return_pct=12.0, sharpe=1.6,
        bt={"metrics": {"sharpe_ratio": 1.5, "total_return_pct": 11.0}},
    )
    assert verdict == "on_track"
    assert promo is True


# 2) Max DD gate sign normalization ───────────────────────────────────────

def test_max_dd_gate_normalizes_backtest_positive_magnitude():
    """Forward DD is a negative percent; the DSL backtest stores DD as a
    positive magnitude. The gate must pass when the forward drawdown is
    SHALLOWER, and display the backtest DD with the same negative sign."""
    rows = sc._gate_rows(
        cache={"sharpe": 1.4, "cum_return_pct": 8.0,
               "max_drawdown_pct": -4.0, "psr": 0.97},
        bt={"metrics": {"sharpe_ratio": 1.5, "total_return_pct": 9.0,
                        "max_drawdown_pct": 6.0}},   # stored POSITIVE +6%
    )
    mdd = next(r for r in rows if r["label"] == "Max DD %")
    assert mdd["forward"] == -4.0
    assert mdd["backtest"] == -6.0, "backtest DD must be shown as a negative percent"
    assert mdd["pass"] is True, "forward -4% is shallower than backtest -6% -> pass"


def test_max_dd_gate_fails_when_forward_deeper():
    rows = sc._gate_rows(
        cache={"sharpe": 0.5, "cum_return_pct": -1.0,
               "max_drawdown_pct": -12.0, "psr": 0.5},
        bt={"metrics": {"sharpe_ratio": 1.5, "total_return_pct": 9.0,
                        "max_drawdown_pct": 6.0}},
    )
    mdd = next(r for r in rows if r["label"] == "Max DD %")
    assert mdd["pass"] is False, "forward -12% is deeper than backtest -6% -> fail"


# 3) Chat resolver requires a conversation_id ─────────────────────────────

def test_resolver_skips_when_dedup_key_missing(db):
    """resolve_idea must return None (leave the order unattributed) when the
    per-origin dedup key is absent — else unrelated orders collapse into ONE
    idea. The guard returns BEFORE any query/write, so a placeholder account
    id is fine and this test persists nothing (the positive path — a present
    key DOES resolve — is covered by test_paper_ideas_resolver.py)."""
    # chat without conversation_id
    assert resolve_idea(
        db, "acct-x", user_id=1, origin_kind="chat",
        conversation_id=None, label="RELIANCE",
    ) is None
    # workflow without workflow_id
    assert resolve_idea(
        db, "acct-x", user_id=1, origin_kind="workflow",
        workflow_id=None, label="WF",
    ) is None
    # strategy without strategy_id
    assert resolve_idea(
        db, "acct-x", user_id=1, origin_kind="strategy",
        strategy_id=None, label="S",
    ) is None
    # unknown origin
    assert resolve_idea(
        db, "acct-x", user_id=1, origin_kind="manual", label="M",
    ) is None
