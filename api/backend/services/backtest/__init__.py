"""Statistical-rigor toolkit for the backtester (the "trust ladder").

Sub-packages:
  * ``validation`` — overfitting / robustness machinery: Monte-Carlo resampling,
    walk-forward, CPCV→PBO, … (Bailey & Lopez de Prado, Pardo, Masters/Aronson).

The track-record battery (PSR / Deflated-Sharpe / MinTRL) lives in
``backend.services.forward_stats`` and is shared with the live paper scorecards;
this package adds the methods that need re-runs or resampling.
"""
