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


# ── the radar: the ratios the scores are made of ───────────────────────────
# Each axis is a real filed ratio scaled against a stated ceiling, because a
# radar whose axes carry different units is a shape without a meaning. The
# ceiling is the value at which an axis reads full — not a maximum the company
# cannot pass; anything above simply pins at 100.

_CORPORATE_AXES = [
    ("liquidity", "Liquidity", "Working capital / assets", 0.50),
    ("retained", "Retained", "Reserves / assets", 0.80),
    ("profitability", "Profitability", "EBIT / assets", 0.30),
    ("solvency", "Solvency", "Market value / liabilities", 8.0),
    ("efficiency", "Efficiency", "Sales / assets", 2.0),
]

_BANK_AXES = [
    ("roe", "Return", "Return on equity", 25.0),
    ("roa", "Assets", "Return on assets", 2.5),
    ("nim", "Margin", "Net interest margin", 5.0),
    ("casa", "Funding", "CASA share of deposits", 60.0),
    ("efficiency", "Efficiency", "100 - cost to income", 60.0),
]


def _axis(key: str, label: str, detail: str, value: Optional[float], cap: float,
          display: str) -> dict:
    scaled = None if value is None else max(0.0, min(100.0, value / cap * 100.0))
    return {
        "key": key, "label": label, "detail": detail,
        "value": None if value is None else round(value, 4),
        "display": display, "cap": cap,
        "scaled": None if scaled is None else round(scaled, 1),
    }


def _corporate_radar(altman: dict) -> list[dict]:
    t = altman.get("terms") or {}
    out = []
    for key, label, detail, cap in _CORPORATE_AXES:
        src = {"liquidity": "working_capital", "retained": "retained_earnings",
               "profitability": "ebit", "solvency": "market_value",
               "efficiency": "sales"}[key]
        v = t.get(src)
        if key == "solvency":
            display = "—" if v is None else f"{v:.1f}x"
        elif key == "efficiency":
            display = "—" if v is None else f"{v:.2f}x"
        else:
            display = "—" if v is None else f"{v * 100:.0f}%"
        out.append(_axis(key, label, detail, v, cap, display))
    return out


def _bank_radar(rt: dict, period: str) -> list[dict]:
    roe = _pick(rt, period, "Return on Equity / Networth (%)", "Return on Networth / Equity (%)")
    roa = _pick(rt, period, "Return on Assets (%)")
    nim = _pick(rt, period, "Net Interest Margin (%)")
    casa = _pick(rt, period, "Casa (%)")
    cti = _pick(rt, period, "Cost to Income (%)")
    eff = None if cti is None else max(0.0, 100.0 - cti)
    vals = {"roe": roe, "roa": roa, "nim": nim, "casa": casa, "efficiency": eff}
    return [
        _axis(key, label, detail, vals[key], cap,
              "—" if vals[key] is None else f"{vals[key]:.1f}%")
        for key, label, detail, cap in _BANK_AXES
    ]


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

    if is_bank:
        quadrants = [
            {**_bank_quadrant(rt, period, prior, "Casa (%)", "CASA", "Funding"),
             "format": "pct"},
            {**graham, "key": "graham", "label": "Graham Number", "caption": "Value",
             "format": "rupees"},
            {**_bank_quadrant(rt, period, prior, "Net Interest Margin (%)",
                              "Net Interest Margin", "Spread"), "format": "pct"},
            {**dupont, "key": "dupont", "label": "DuPont ROE", "caption": "Returns",
             "format": "pct"},
        ]
        radar = _bank_radar(rt, period)
    else:
        altman = _altman_z(bs, pl, rt, period)
        ohlson = _ohlson_o(bs, pl, cf, period, prior)
        quadrants = [
            {**altman, "key": "altman", "label": "Altman Z", "caption": "Solvency",
             "format": "plain"},
            {**graham, "key": "graham", "label": "Graham Number", "caption": "Value",
             "format": "rupees"},
            {**ohlson, "key": "ohlson", "label": "Ohlson O", "caption": "Distress odds",
             "format": "plain"},
            {**dupont, "key": "dupont", "label": "DuPont ROE", "caption": "Returns",
             "format": "pct"},
        ]
        radar = _corporate_radar(altman)

    return {
        "available": any(q.get("value") is not None for q in quadrants),
        "symbol": sym,
        "kind": "bank" if is_bank else "corporate",
        "basis": (bs_grid or {}).get("basis", basis),
        "period": period,
        "unit": UNIT,
        "quadrants": quadrants,
        "radar": radar,
        "source": "moneycontrol",
    }
