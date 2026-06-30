"""End-to-end smoke test: IT connectedness on a ~40-name slice + a 2-episode
exit backtest, proving the engine computes clean betas, b_NB, HAC t-stats, the
three exit variants, and the NIFTY block without error."""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import pandas as pd

from scripts.strategy_research.v3 import universe as U, factors as F
from scripts.strategy_research.v3 import connectedness as C, exits as E, battery as B
from scripts.strategy_research._it_bt_common import WEAK_ANALOGS

rets = U.returns_matrix()
ind = U.industry_map()
r_nifty = U.series("NIFTY").reindex(rets.index)
r_brent = U.series("BRENT").reindex(rets.index)
print(f"returns matrix: {rets.shape[0]} rows x {rets.shape[1]} cols  "
      f"{rets.index.min().date()}..{rets.index.max().date()}")

it_syms = F.it_symbols(ind)
print(f"IT names: {len(it_syms)}  e.g. {it_syms[:5]}")
mkt_exit = F.mkt_exit(rets, it_syms)
it_f = F.it_factor(rets, it_syms)
print(f"MKT_exIT built from {len([c for c in rets.columns if c not in set(it_syms)])} non-IT names; "
      f"IT_f from {len(it_syms)}")

# b_NB orthogonalization (the crude deliverable, run here to prove it computes)
mkt_perp, b_NB, t_NB = F.orthogonalize(r_nifty, r_brent)
print(f"\n[b_NB] NIFTY=c+b_NB*Brent:  b_NB={b_NB:.4f}  t(HAC)={t_NB:.2f}  "
      f"resid n={len(mkt_perp)}")

# --- IT connectedness on a ~40-name slice (non-IT leaders + a few IT) ---
non_it = [c for c in rets.columns if c not in set(it_syms)][:35]
slice_leaders = non_it + it_syms[:5]
conn = C.it_connectedness(rets, mkt_exit, it_f, r_nifty, slice_leaders, ind)
print(f"\nIT connectedness on {len(conn)}-name slice (clean b_it + HAC t):")
print(f"  {'symbol':16s}{'industry':28s}{'b_nifty':>9s}{'b_it':>9s}{'t_it':>7s}{'flip':>6s}")
shown = sorted(conn.items(), key=lambda kv: kv[1]['clean']['t'])[:6] + \
        sorted(conn.items(), key=lambda kv: -kv[1]['clean']['t'])[:4]
for s, d in shown:
    print(f"  {s:16s}{d['industry'][:26]:28s}"
          f"{d['naive']['beta_nifty']:>9.3f}{d['clean']['beta_clean']:>9.3f}"
          f"{d['clean']['t']:>7.2f}{str(d['flipped']):>6s}")
flipped = [s for s, d in conn.items() if d['flipped']]
genuine = [s for s, d in conn.items() if d['clean']['verdict']['genuine']]
print(f"  -> genuine(b_it sig): {len(genuine)}  flipped(naive!=clean): {len(flipped)}")

# --- 2-episode exit backtest on a small basket of the genuine/leader names ---
basket = (genuine[:3] or non_it[:3])
weights = {s: 1.0 for s in basket}
idx = rets.index
eps = []
for a in WEAK_ANALOGS[:2]:
    pos = idx.searchsorted(pd.Timestamp(a))
    if pos + 21 < len(idx):
        eps.append((pos + 1, pos + 20))   # next-bar entry, 20-bar window
print(f"\n2-episode exit backtest on basket {basket}  episodes={eps}")
paths = E.episode_returns(eps, rets, weights)
mfe = E.mfe_analysis(paths)
print(f"  MFE: median={mfe['median_pct']}% p25={mfe['p25']}% p75={mfe['p75']}% "
      f"-> target={mfe['target_pct_declared']}%")
res = E.backtest_exits(eps, rets, weights, r_nifty,
                       target_pct=mfe['target_pct_declared'], hold_bars=20)
for mode, r in res.items():
    nc = r['nifty_comparison']
    print(f"  [{mode:6s}] per-ep%={r['per_episode_pct']}  "
          f"strat={nc['strategy_total_pct']}% nifty={nc['nifty_total_pct']}% "
          f"excess={nc['excess_pct']}% beta={nc['nifty_beta']} beat={nc['pct_episodes_beat']}%")

# --- battery on the fixed-variant curve, num_trials = screen width ---
fixed = res['fixed']
bat = B.run_battery(fixed['equity'], fixed['daily_rets'], fixed['n_episodes'],
                    num_trials=len(conn))
fs = bat['forward_stats']
print(f"\nBATTERY (fixed, num_trials={len(conn)}): verdict={bat['verdict']['verdict']} "
      f"PSR={fs['psr']} DSR={fs['deflated_sharpe']} MinTRL={fs['min_trl']} n_obs={fs['n_obs']}")
sig = B.caar_significance(fixed['per_episode_rets'])
print(f"CAAR sig: caar={sig['caar']}% classical_t={sig['classical_t']} "
      f"combined_p={sig['combined_p']}")
out, expr = B.two_dials(hit_rate=0.5, relationship_strength=None,
                        sample_n=fixed['n_episodes'],
                        verdict=bat['verdict']['verdict'],
                        caar_alignment=B._caar_alignment(sig['caar']),
                        significance_p=sig['combined_p'], cost_survival=0.6,
                        deflated_sharpe=fs['deflated_sharpe'], n_obs=fs['n_obs'],
                        min_trl=fs['min_trl'])
print(f"OUTCOME dial: {'SUPPRESSED' if out.suppressed else out.letter}  "
      f"EXPRESSION dial: {'SUPPRESSED' if expr.suppressed else expr.letter}")
tb = B.trust_block(bat, engine="basket", alignment=B.dial_to_dict(expr),
                   nifty_comparison=fixed['nifty_comparison'])
assert set(tb.keys()) == set(B.TRUST_BLOCK_KEYS), "trust_block key mismatch!"
print("trust_block keys match TRUST_BLOCK_KEYS:", sorted(tb.keys()) == sorted(B.TRUST_BLOCK_KEYS))
print("\nSMOKE TEST PASSED")
