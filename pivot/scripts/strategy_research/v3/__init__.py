"""v3 event-driven strategy research — full NIFTY-500 universe, clean factor
model (MKT_exIT / MKT_perpBrent / monsoon), Newey-West HAC connectedness, three
exit variants, and the v2 Trust Battery reused verbatim.

All data = yfinance daily (auto_adjust). Factors are built FROM NIFTY-500
constituent returns, never from index tickers yfinance may lack.
"""
