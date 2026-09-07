"""Solvency-and-value scores computed from the filed statements.

Four models, all read out of the same MC statement grids the statements page
quotes — balance sheet, P&L and the ratio sheet — so a score can never
disagree with the line it is built from:

  Altman Z   distance from distress, five weighted ratios (1968)
  Ohlson O   log-odds of distress, nine terms (1980)
  Graham     sqrt(22.5 * EPS * BVPS), the defensive-investor fair value (1949)
  DuPont     ROE decomposed into margin x turnover x leverage

Two rules run through the whole module:

**One period, one basis.** Every input for a score comes out of the SAME
period column of the SAME basis. The `latest` snapshot the page header uses
picks each field independently, so a company with a patchy filing history can
hand back Mar-25 revenue beside Mar-21 equity — fine for a stat tile, fatal
for a ratio. Here a period is chosen once, and a score is dropped whole rather
than assembled from two years.

**A missing input is a missing score, never a guessed one.** Every helper
returns None on absence and every score checks its inputs before dividing.
A bank has no current/non-current split at all, so Altman and Ohlson are not
merely null for one — they are undefined, and the payload says so in the words
of the model rather than printing an em-dash.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Optional

from backend.market import financials_db as fdb

logger = logging.getLogger(__name__)

UNIT = "Rs. Cr."

# ── reading a line item out of a grid ──────────────────────────────────────
# MC's labels are stable per company but not across them: a bank files "Book
# Value [Excl. Reval Reserve]/Share (Rs.)" where TCS files "Book Value
# [ExclRevalReserve]/Share (Rs.)", and "Price To Book Value (%)" against
# "Price/BV (X)" — same number, different sheet vocabulary. Matching is on the
# label stripped to letters and digits, and every call passes candidates in
# preference order.


def _norm(label: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (label or "").lower())


def _index(grid: dict | None) -> dict[str, dict]:
    """{normalised label: row} for one statement grid."""
    if not grid:
        return {}
    return {_norm(r.get("line_item", "")): r for r in grid.get("rows", [])}


def _pick(index: dict[str, dict], period: str, *candidates: str) -> Optional[float]:
    """First candidate line item with a real number in `period`."""
    for cand in candidates:
        row = index.get(_norm(cand))
        if row is None:
            continue
        v = (row.get("values") or {}).get(period)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        return f
    return None


def _div(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den is None or den == 0:
        return None
    return num / den


# ── the four scores ────────────────────────────────────────────────────────

def _altman_z(bs: dict, pl: dict, rt: dict, period: str) -> dict:
    """Altman (1968) Z for a listed manufacturer/service company.

        Z = 1.2*WC/TA + 1.4*RE/TA + 3.3*EBIT/TA + 0.6*MVE/TL + 1.0*Sales/TA

    MVE is the market value of equity. It is taken as the filed Price/Book
    times the filed shareholders' funds rather than today's market cap on
    purpose: every other term in the sum is as of the period end, and mixing a
    live market cap into four year-end ratios makes a number that belongs to
    no date at all.
    """
    ta = _pick(bs, period, "Total Assets", "Total Capital And Liabilities")
    ca = _pick(bs, period, "Total Current Assets")
    cl = _pick(bs, period, "Total Current Liabilities")
    re_ = _pick(bs, period, "Total Reserves and Surplus", "Reserves and Surplus")
    eq = _pick(bs, period, "Total Shareholders Funds")
    ncl = _pick(bs, period, "Total Non-Current Liabilities")

    pbt = _pick(pl, period, "Profit/Loss Before Tax")
    fin = _pick(pl, period, "Finance Costs")
    sales = _pick(pl, period, "Total Operating Revenues", "Revenue From Operations [Net]")
    pb = _pick(rt, period, "Price/BV (X)", "Price To Book Value (X)", "Price To Book Value (%)")

    if ca is None or cl is None:
        return {"value": None, "unavailable_reason": "no current/non-current split filed"}
    if ta in (None, 0) or re_ is None or pbt is None or sales is None:
        return {"value": None, "unavailable_reason": "balance sheet or P&L incomplete"}
    tl = (ncl or 0.0) + cl
    mve = (pb * eq) if (pb is not None and eq is not None) else None
    if mve is None or tl == 0:
        return {"value": None, "unavailable_reason": "no filed price/book"}

    ebit = pbt + (fin or 0.0)
    x1, x2, x3 = (ca - cl) / ta, re_ / ta, ebit / ta
    x4, x5 = mve / tl, sales / ta
    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5

    if z >= 2.99:
        band, verdict = "good", "Safe zone"
    elif z >= 1.81:
        band, verdict = "watch", "Grey zone"
    else:
        band, verdict = "risk", "Distress zone"
    return {
        "value": round(z, 2),
        "band": band,
        "verdict": verdict,
        "terms": {
            "working_capital": round(x1, 4), "retained_earnings": round(x2, 4),
            "ebit": round(x3, 4), "market_value": round(x4, 4), "sales": round(x5, 4),
        },
        "unavailable_reason": None,
    }


def _ohlson_o(bs: dict, pl: dict, cf: dict, period: str, prior: Optional[str]) -> dict:
    """Ohlson (1980) O — the log-odds of distress, so P = 1/(1+e^-O).

    The size term is the paper's log(total assets / price-level index); here it
    is the natural log of total assets in ₹ crore. Cash from operations stands
    in for funds from operations, the standard substitution when a funds
    statement is not filed separately.
    """
    ta = _pick(bs, period, "Total Assets", "Total Capital And Liabilities")
    ca = _pick(bs, period, "Total Current Assets")
    cl = _pick(bs, period, "Total Current Liabilities")
    ncl = _pick(bs, period, "Total Non-Current Liabilities")
    ni = _pick(pl, period, "Profit/Loss For The Period", "Profit/Loss From Continuing Operations",
               "Net Profit / Loss for The Year")
    ffo = _pick(cf, period, "Net CashFlow From Operating Activities",
                "Net Cash Flow From Operating Activities")
    ni_prev = _pick(pl, prior, "Profit/Loss For The Period",
                    "Profit/Loss From Continuing Operations") if prior else None

    if ca is None or cl is None:
        return {"value": None, "unavailable_reason": "no current/non-current split filed"}
    if ta in (None, 0) or ni is None:
        return {"value": None, "unavailable_reason": "balance sheet or P&L incomplete"}

    tl = (ncl or 0.0) + cl
    # Total assets stay in ₹ crore for the size term. Ohlson deflated assets
    # by a GNP price index so the term sat near log(assets in USD millions);
    # ₹ crore is the scale Indian implementations settled on and the one that
    # reproduces the published numbers for a company like RELIANCE.
    o = -1.32 - 0.407 * math.log(max(ta, 1e-6))
    o += 6.03 * (tl / ta)
    o += -1.43 * ((ca - cl) / ta)
    o += 0.0757 * (cl / ca) if ca else 0.0
    o += -1.72 * (1.0 if tl > ta else 0.0)          # OENEG: liabilities exceed assets
    o += -2.37 * (ni / ta)
    o += -1.83 * (ffo / tl) if (ffo is not None and tl) else 0.0
    o += 0.285 * (1.0 if (ni < 0 and (ni_prev or 0) < 0) else 0.0)   # INTWO: two loss years
    if ni_prev is not None and (abs(ni) + abs(ni_prev)) > 0:
        o += -0.521 * ((ni - ni_prev) / (abs(ni) + abs(ni_prev)))    # CHIN: earnings swing

    p = 1.0 / (1.0 + math.exp(-o)) if -700 < o < 700 else (0.0 if o < 0 else 1.0)
    if p < 0.05:
        band, verdict = "good", "Low distress risk"
    elif p < 0.20:
        band, verdict = "watch", "Elevated distress risk"
    else:
        band, verdict = "risk", "High distress risk"
    return {
        "value": round(o, 2),
        "probability_pct": round(p * 100, 2),
        "band": band,
        "verdict": verdict,
        "unavailable_reason": None,
    }


def _graham(rt: dict, period: str) -> dict:
    """Graham's defensive-investor number, sqrt(22.5 * EPS * BVPS).

    Undefined on a loss or on negative book — the square root has no real
    value there, and a company that lost money simply has no Graham number.
    """
    eps = _pick(rt, period, "Basic EPS (Rs.)", "Diluted EPS (Rs.)")
    bvps = _pick(rt, period, "Book Value [ExclRevalReserve]/Share (Rs.)",
                 "Book Value [Excl. Reval Reserve]/Share (Rs.)",
                 "Book Value [InclRevalReserve]/Share (Rs.)",
                 "Book Value [Incl. Reval Reserve]/Share (Rs.)")
    if eps is None or bvps is None:
        return {"value": None, "unavailable_reason": "no filed EPS or book value"}
    if eps <= 0 or bvps <= 0:
        return {"value": None, "unavailable_reason": "undefined on a loss or negative book"}
    return {
        "value": round(math.sqrt(22.5 * eps * bvps), 2),
        "eps": round(eps, 2),
        "book_value_per_share": round(bvps, 2),
        "unavailable_reason": None,
    }


def _dupont(bs: dict, pl: dict, rt: dict, period: str) -> dict:
    """ROE as the three things that make it: margin x turnover x leverage.

    Computed from the statements rather than lifted from the ratio sheet's own
    ROE line, because the point of DuPont is that the legs multiply back to
    the headline — a headline from a different source would not.
    """
    # A bank's P&L names none of these: revenue is interest earned and the
    # bottom line is "Net Profit / Loss for The Year". Same three legs, other
    # vocabulary — and interest earned is the base MC's own margin uses, so
    # the decomposition stays comparable to the ratio sheet beside it.
    ni = _pick(pl, period, "Profit/Loss For The Period", "Profit/Loss From Continuing Operations",
               "Net Profit / Loss for The Year")
    sales = _pick(pl, period, "Total Operating Revenues", "Revenue From Operations [Net]",
                  "Total Interest Earned", "Total Revenue")
    ta = _pick(bs, period, "Total Assets", "Total Capital And Liabilities")
    eq = _pick(bs, period, "Total Shareholders Funds")

    margin, turnover, leverage = _div(ni, sales), _div(sales, ta), _div(ta, eq)
    if margin is None or turnover is None or leverage is None:
        return {"value": None, "unavailable_reason": "statements incomplete"}
    roe = margin * turnover * leverage * 100
    return {
        "value": round(roe, 2),
        "margin_pct": round(margin * 100, 2),
        "asset_turnover": round(turnover, 2),
        "equity_multiplier": round(leverage, 2),
        "unavailable_reason": None,
    }


# ── the radars: one per score, each drawn from that score's own inputs ─────
# A single radar for the whole panel could only ever be one model's inputs
# wearing a neutral label. These are five (or three, or four) axes per score,
# taken straight from the formula above it, so selecting a score answers "and
# what is that number made of".
#
# Every axis reads outward-is-stronger and is scaled against a stated ceiling,
# because a radar whose axes carry different units is a shape without a
# meaning. `invert` is for the ratios where less is better — a bank's funding
# cost, a company's leverage under Ohlson — so the shape stays readable
# without the reader having to remember which spokes to flip.

def _axis(key: str, label: str, detail: str, value: Optional[float], cap: float,
          display: str, invert: bool = False) -> Optional[dict]:
    if value is None:
        return None
    frac = value / cap
    health = (1.0 - frac) if invert else frac
    return {
        "key": key, "label": label, "detail": detail,
        "value": round(value, 4), "display": display, "cap": cap,
        "scaled": round(max(0.0, min(100.0, health * 100.0)), 1),
    }


def _axes(*items: Optional[dict]) -> list[dict]:
    """Drop the axes whose input was not filed. Under three spokes there is no
    shape left to read, so the caller falls back to the company radar."""
    out = [a for a in items if a is not None]
    return out if len(out) >= 3 else []


def _pct(v: Optional[float], digits: int = 0) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def _x(v: Optional[float], digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}x"


def _radar_altman(bs: dict, pl: dict, rt: dict, period: str) -> list[dict]:
    """The five terms of Z, each as the ratio Altman weighted."""
    ta = _pick(bs, period, "Total Assets", "Total Capital And Liabilities")
    ca = _pick(bs, period, "Total Current Assets")
    cl = _pick(bs, period, "Total Current Liabilities")
    re_ = _pick(bs, period, "Total Reserves and Surplus", "Reserves and Surplus")
    eq = _pick(bs, period, "Total Shareholders Funds")
    ncl = _pick(bs, period, "Total Non-Current Liabilities")
    pbt = _pick(pl, period, "Profit/Loss Before Tax")
    fin = _pick(pl, period, "Finance Costs")
    sales = _pick(pl, period, "Total Operating Revenues", "Revenue From Operations [Net]")
    pb = _pick(rt, period, "Price/BV (X)", "Price To Book Value (X)", "Price To Book Value (%)")
    if ta in (None, 0):
        return []
    tl = ((ncl or 0.0) + cl) if cl is not None else None
    wc = _div((ca - cl) if (ca is not None and cl is not None) else None, ta)
    ebit = (pbt + (fin or 0.0)) if pbt is not None else None
    mve = (pb * eq) if (pb is not None and eq is not None) else None
    return _axes(
        _axis("liquidity", "Liquidity", "Working capital / assets", wc, 0.50, _pct(wc)),
        _axis("retained", "Retained", "Reserves / assets", _div(re_, ta), 0.80, _pct(_div(re_, ta))),
        _axis("profitability", "Profitability", "EBIT / assets", _div(ebit, ta), 0.30, _pct(_div(ebit, ta))),
        _axis("solvency", "Solvency", "Market value / liabilities", _div(mve, tl), 8.0,
              _x(_div(mve, tl), 1)),
        _axis("efficiency", "Efficiency", "Sales / assets", _div(sales, ta), 2.0, _x(_div(sales, ta))),
    )


def _radar_ohlson(bs: dict, pl: dict, cf: dict, period: str) -> list[dict]:
    """Ohlson's own inputs, turned the healthy way up.

    Three of the nine terms are binary flags and one is a two-year earnings
    swing — none of them is a spoke. What is left is the five continuous ones,
    with leverage inverted so that outward still means stronger.
    """
    ta = _pick(bs, period, "Total Assets", "Total Capital And Liabilities")
    ca = _pick(bs, period, "Total Current Assets")
    cl = _pick(bs, period, "Total Current Liabilities")
    ncl = _pick(bs, period, "Total Non-Current Liabilities")
    ni = _pick(pl, period, "Profit/Loss For The Period", "Profit/Loss From Continuing Operations",
               "Net Profit / Loss for The Year")
    ffo = _pick(cf, period, "Net CashFlow From Operating Activities",
                "Net Cash Flow From Operating Activities")
    if ta in (None, 0):
        return []
    tl = ((ncl or 0.0) + cl) if cl is not None else None
    lev = _div(tl, ta)
    wc = _div((ca - cl) if (ca is not None and cl is not None) else None, ta)
    # Size enters the model as a log, so it enters the radar as one too: the
    # span from a ₹400 Cr balance sheet to a ₹12 lakh Cr one is eight natural
    # logs, and on a linear axis every listed company but the largest would
    # pin at zero.
    size = None if ta <= 0 else max(0.0, math.log(ta) - 6.0)
    return _axes(
        _axis("size", "Size", "Log of total assets", size, 8.0,
              "—" if ta is None else f"₹{ta / 1000:.1f}k Cr"),
        _axis("equity", "Equity", "Equity share of assets",
              None if lev is None else max(0.0, 1.0 - lev), 1.0,
              _pct(None if lev is None else 1.0 - lev)),
        _axis("liquidity", "Liquidity", "Working capital / assets", wc, 0.50, _pct(wc)),
        _axis("cash", "Cash cover", "Operating cash flow / liabilities", _div(ffo, tl), 0.50,
              _pct(_div(ffo, tl))),
        _axis("profitability", "Profitability", "Net profit / assets", _div(ni, ta), 0.20,
              _pct(_div(ni, ta), 1)),
    )


def _radar_graham(rt: dict, period: str, periods: list[str]) -> list[dict]:
    """Graham's defensive tests, not the two inputs of his formula.

    The number itself is only sqrt(22.5 * EPS * BVPS) — two spokes, no shape.
    The tests behind it are the interesting part: a P/E under 15, a P/B under
    1.5, current assets twice current liabilities, an uninterrupted dividend
    and earnings that grew. Each spoke is how far past its own test the
    company is, so a full pentagon is a stock Graham would have looked at.
    """
    eps = _pick(rt, period, "Basic EPS (Rs.)", "Diluted EPS (Rs.)")
    bvps = _pick(rt, period, "Book Value [ExclRevalReserve]/Share (Rs.)",
                 "Book Value [Excl. Reval Reserve]/Share (Rs.)",
                 "Book Value [InclRevalReserve]/Share (Rs.)",
                 "Book Value [Incl. Reval Reserve]/Share (Rs.)")
    pb = _pick(rt, period, "Price/BV (X)", "Price To Book Value (X)", "Price To Book Value (%)")
    cr = _pick(rt, period, "Current Ratio (X)")
    payout = _pick(rt, period, "Dividend Payout Ratio (NP) (%)")
    roe = _pick(rt, period, "Return on Networth / Equity (%)", "Return on Equity / Networth (%)")

    pe = None
    if pb is not None and bvps is not None and eps not in (None, 0):
        pe = (pb * bvps) / eps                      # filed price/book back to a filed P/E

    # Earnings growth over as many filed years as there are, up to five.
    growth = None
    older = [p for p in periods[1:6] if _pick(rt, p, "Basic EPS (Rs.)", "Diluted EPS (Rs.)")]
    if eps and eps > 0 and older:
        back = older[-1]
        eps_then = _pick(rt, back, "Basic EPS (Rs.)", "Diluted EPS (Rs.)")
        n = periods.index(back)
        if eps_then and eps_then > 0 and n > 0:
            growth = ((eps / eps_then) ** (1.0 / n) - 1.0) * 100

    return _axes(
        _axis("earnings", "Earnings", "P/E against Graham's 15", None if pe in (None, 0) else 15.0 / pe,
              1.5, "—" if pe is None else f"{pe:.1f}x P/E"),
        _axis("book", "Book", "P/B against Graham's 1.5", None if pb in (None, 0) else 1.5 / pb,
              1.5, "—" if pb is None else f"{pb:.2f}x P/B"),
        _axis("liquidity", "Liquidity", "Current ratio against his 2.0", cr, 3.0, _x(cr)),
        _axis("dividend", "Dividend", "Payout of net profit", payout, 60.0,
              "—" if payout is None else f"{payout:.0f}%"),
        _axis("growth", "Growth", "Compound EPS growth on file", growth, 15.0,
              "—" if growth is None else f"{growth:.1f}%"),
        _axis("return", "Return", "Return on equity", roe, 25.0,
              "—" if roe is None else f"{roe:.1f}%"),
    )


def _radar_dupont(bs: dict, pl: dict, period: str) -> list[dict]:
    """The five-step DuPont: the three legs on the panel, with the margin split
    into the three things that actually move it — tax, interest, operations.
    All five multiply back to the ROE printed above them."""
    ta = _pick(bs, period, "Total Assets", "Total Capital And Liabilities")
    eq = _pick(bs, period, "Total Shareholders Funds")
    ni = _pick(pl, period, "Profit/Loss For The Period", "Profit/Loss From Continuing Operations",
               "Net Profit / Loss for The Year")
    pbt = _pick(pl, period, "Profit/Loss Before Tax")
    fin = _pick(pl, period, "Finance Costs")
    sales = _pick(pl, period, "Total Operating Revenues", "Revenue From Operations [Net]",
                  "Total Interest Earned")
    ebit = (pbt + (fin or 0.0)) if pbt is not None else None
    tax_burden, interest_burden = _div(ni, pbt), _div(pbt, ebit)
    op_margin, turnover, leverage = _div(ebit, sales), _div(sales, ta), _div(ta, eq)
    return _axes(
        _axis("tax", "Tax", "Net profit / pre-tax profit", tax_burden, 1.0, _pct(tax_burden)),
        _axis("interest", "Interest", "Pre-tax profit / EBIT", interest_burden, 1.0,
              _pct(interest_burden)),
        _axis("margin", "Margin", "EBIT / sales", op_margin, 0.30, _pct(op_margin)),
        _axis("turnover", "Asset turnover", "Sales / assets", turnover, 2.0, _x(turnover)),
        _axis("leverage", "Leverage", "Assets / equity", leverage, 3.0, _x(leverage)),
    )


def _radar_bank(rt: dict, period: str) -> list[dict]:
    """What a bank is judged on: what it earns, on what, at what spread, funded
    how, run at what cost."""
    roe = _pick(rt, period, "Return on Equity / Networth (%)", "Return on Networth / Equity (%)")
    roa = _pick(rt, period, "Return on Assets (%)")
    nim = _pick(rt, period, "Net Interest Margin (%)")
    casa = _pick(rt, period, "Casa (%)")
    cti = _pick(rt, period, "Cost to Income (%)")
    return _axes(
        _axis("roe", "Return", "Return on equity", roe, 25.0, "—" if roe is None else f"{roe:.1f}%"),
        _axis("roa", "Assets", "Return on assets", roa, 2.5, "—" if roa is None else f"{roa:.2f}%"),
        _axis("nim", "Margin", "Net interest margin", nim, 5.0, "—" if nim is None else f"{nim:.2f}%"),
        _axis("casa", "Funding", "CASA share of deposits", casa, 60.0,
              "—" if casa is None else f"{casa:.1f}%"),
        _axis("efficiency", "Efficiency", "Cost to income", cti, 100.0,
              "—" if cti is None else f"{cti:.1f}%", invert=True),
    )


def _radar_bank_spread(rt: dict, period: str) -> list[dict]:
    """Where the margin comes from and what eats it — every spoke is a share of
    the same balance sheet, so they are directly comparable."""
    ii = _pick(rt, period, "Interest Income/Total Assets (%)")
    ie = _pick(rt, period, "Interest Expenses/Total Assets (%)")
    fee = _pick(rt, period, "Non-Interest Income/Total Assets (%)")
    opex = _pick(rt, period, "Operating Expenses/Total Assets (%)")
    nim = _pick(rt, period, "Net Interest Margin (%)")
    return _axes(
        _axis("yield", "Yield", "Interest income / assets", ii, 10.0,
              "—" if ii is None else f"{ii:.2f}%"),
        _axis("cost", "Funding", "Interest expense / assets", ie, 8.0,
              "—" if ie is None else f"{ie:.2f}%", invert=True),
        _axis("fees", "Fees", "Non-interest income / assets", fee, 4.0,
              "—" if fee is None else f"{fee:.2f}%"),
        _axis("opex", "Opex", "Operating expenses / assets", opex, 6.0,
              "—" if opex is None else f"{opex:.2f}%", invert=True),
        _axis("nim", "Margin", "Net interest margin", nim, 5.0,
              "—" if nim is None else f"{nim:.2f}%"),
    )


# ── the bank pair that replaces Altman and Ohlson ──────────────────────────

def _bank_quadrant(rt: dict, period: str, prior: Optional[str], line: str,
                   label: str, caption: str) -> dict:
    """One bank ratio, read with its own previous year beside it.

    No threshold. "Above 40% CASA is good" is a rule of thumb that changes
    with the rate cycle and with the bank's own mix, and printing a verdict
    against it would be an opinion wearing a number's clothes. The year-on-year
    move is a fact, it is the thing that actually reads on these two ratios,
    and the direction is what the colour then means.
    """
    v = _pick(rt, period, line)
    if v is None:
        return {"key": _norm(label), "label": label, "caption": caption,
                "value": None, "unavailable_reason": f"{label.lower()} not filed"}
    prev = _pick(rt, prior, line) if prior else None
    out = {"key": _norm(label), "label": label, "caption": caption,
           "value": round(v, 2), "unavailable_reason": None}
    if prev is not None:
        d = v - prev
        out["delta_pp"] = round(d, 2)
        out["band"] = "good" if d >= 0 else "risk"
        out["verdict"] = (f"{abs(d):.1f} pp {'above' if d >= 0 else 'below'} {prior}"
                          if abs(d) >= 0.05 else f"Flat on {prior}")
    return out


# ── assembly ───────────────────────────────────────────────────────────────

def compute_scores(symbol: str, *, basis: str = "consolidated") -> dict:
    """The four scores plus the radar for one company. Never raises on missing
    data — an unavailable score carries the reason it is unavailable."""
    sym = (symbol or "").strip().upper()
    grids = {
        name: fdb.get_statement(sym, statement=name, basis=basis, years=12)
        for name in ("balance_sheet", "profit_loss", "cash_flow", "ratios")
    }
    bs_grid = grids["balance_sheet"]
    if not bs_grid or not bs_grid.get("rows"):
        return {"available": False, "symbol": sym, "reason": "no filed statements"}

    # One period for every score. The balance sheet leads because it is the
    # sheet both distress models are built on; the P&L and ratio grids are
    # read at that same label or not at all.
    periods = bs_grid.get("periods") or []
    if not periods:
        return {"available": False, "symbol": sym, "reason": "no filed statements"}
    period = periods[0]
    prior = periods[1] if len(periods) > 1 else None

    bs, pl, cf, rt = (_index(grids["balance_sheet"]), _index(grids["profit_loss"]),
                      _index(grids["cash_flow"]), _index(grids["ratios"]))

    # A bank files deposits and advances, not current assets. That is not a
    # gap in the data — it is a different balance sheet, and the two distress
    # models do not apply to it.
    is_bank = _pick(bs, period, "Total Current Assets") is None and (
        _pick(bs, period, "Deposits") is not None or _pick(rt, period, "Casa (%)") is not None
    )

    graham = _graham(rt, period)
    dupont = _dupont(bs, pl, rt, period)
    graham_radar = _radar_graham(rt, period, periods)
    dupont_radar = _radar_dupont(bs, pl, period)

    if is_bank:
        default_radar = _radar_bank(rt, period)
        quadrants = [
            {**_bank_quadrant(rt, period, prior, "Casa (%)", "CASA", "Funding"),
             "format": "pct", "radar": default_radar},
            {**graham, "key": "graham", "label": "Graham Number", "caption": "Value",
             "format": "rupees", "radar": graham_radar},
            {**_bank_quadrant(rt, period, prior, "Net Interest Margin (%)",
                              "Net Interest Margin", "Spread"),
             "format": "pct", "radar": _radar_bank_spread(rt, period)},
            {**dupont, "key": "dupont", "label": "DuPont ROE", "caption": "Returns",
             "format": "pct", "radar": dupont_radar},
        ]
    else:
        altman = _altman_z(bs, pl, rt, period)
        ohlson = _ohlson_o(bs, pl, cf, period, prior)
        default_radar = _radar_altman(bs, pl, rt, period)
        quadrants = [
            {**altman, "key": "altman", "label": "Altman Z", "caption": "Solvency",
             "format": "plain", "radar": default_radar},
            {**graham, "key": "graham", "label": "Graham Number", "caption": "Value",
             "format": "rupees", "radar": graham_radar},
            {**ohlson, "key": "ohlson", "label": "Ohlson O", "caption": "Distress odds",
             "format": "plain", "radar": _radar_ohlson(bs, pl, cf, period)},
            {**dupont, "key": "dupont", "label": "DuPont ROE", "caption": "Returns",
             "format": "pct", "radar": dupont_radar},
        ]

    # A score whose own inputs are too patchy to draw falls back to the
    # company radar rather than to an empty frame — the shape still belongs to
    # the same company, and an empty chart on selection reads as a broken
    # control rather than as missing data.
    for q in quadrants:
        if not q.get("radar"):
            q["radar"] = default_radar

    return {
        "available": any(q.get("value") is not None for q in quadrants),
        "symbol": sym,
        "kind": "bank" if is_bank else "corporate",
        "basis": (bs_grid or {}).get("basis", basis),
        "period": period,
        "unit": UNIT,
        "quadrants": quadrants,
        "radar": default_radar,
        "source": "moneycontrol",
    }
