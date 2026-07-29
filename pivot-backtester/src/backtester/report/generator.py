"""Generate a self-contained HTML report.

No external CDN — equity curve is rendered as inline SVG. Designed to be
shippable as a single file the user can open or attach.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<title>Backtest — {expression_short}</title>
<style>
:root {{ color-scheme: dark; }}
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  background: #0d0d0f; color: #e5e5e7; margin: 0; padding: 32px;
  max-width: 1100px; margin-left: auto; margin-right: auto;
}}
h1, h2, h3 {{ font-weight: 500; }}
h1 {{ font-size: 22px; margin: 0 0 4px 0; }}
.meta {{ color: #8d8d92; font-size: 13px; margin-bottom: 20px; }}
.callout {{
  background: rgba(255,200,80,.06); border: 1px solid rgba(255,200,80,.18);
  padding: 10px 14px; border-radius: 8px; font-size: 13px; margin-bottom: 18px;
}}
.section {{ margin-bottom: 28px; padding: 18px; background: #161618; border-radius: 12px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th, td {{ padding: 6px 10px; text-align: left; border-bottom: 1px solid #2a2a2e; }}
th {{ color: #b3b3b7; font-weight: 500; }}
.metric-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }}
.metric {{ background: #1f1f22; padding: 12px 14px; border-radius: 8px; }}
.metric .label {{ color: #8d8d92; font-size: 12px; }}
.metric .value {{ font-size: 22px; font-weight: 500; margin-top: 2px; }}
.pos {{ color: #4ade80; }} .neg {{ color: #f87171; }}
svg {{ display: block; width: 100%; height: 280px; }}
.expression {{ font-family: "SF Mono", monospace; font-size: 13px; background: #1f1f22; padding: 8px 10px; border-radius: 6px; }}
.warn {{ color: #fbbf24; font-size: 12px; }}
.included-y {{ color: #4ade80; }} .included-n {{ color: #f87171; }}
</style>
</head>
<body>
<h1>{expression_html}</h1>
<div class="meta">
  {start} → {end} &middot; rebalance {rebalance} &middot; equal-weight &middot;
  next-open execution &middot; {slippage_bps} bps slippage / {commission_bps} bps commission
</div>

{warnings_html}

<div class="section">
  <h2>Equity curve</h2>
  {svg}
</div>

<div class="section">
  <h2>Headline metrics</h2>
  <div class="metric-grid">
{metrics_html}
  </div>
</div>

<div class="section">
  <h2>Rebalances ({n_rebalances})</h2>
  <table>
    <tr><th>date</th><th>universe</th><th>tradeable</th><th>n trades</th><th>portfolio value</th></tr>
    {rebalance_rows}
  </table>
</div>

<div class="section">
  <h2>Audit trail — first rebalance ({first_rb_date})</h2>
  <p class="meta">Every company evaluated at this date with the actual values used.
  This is the trust-builder: confirm none of these values are forward-looking before believing the result.</p>
  <table>
    <tr><th>sc_id</th><th>company</th>{leaf_th}<th>included</th></tr>
    {audit_rows}
  </table>
</div>

<div class="section">
  <h2>All trades ({n_trades})</h2>
  <table>
    <tr><th>date</th><th>sc_id</th><th>side</th><th>shares</th><th>price</th><th>value</th></tr>
    {trade_rows}
  </table>
</div>

<div class="section meta">
  Past performance does not guarantee future results.
  This is a simulation built on point-in-time fundamentals; the report header lists
  any data caveats (heuristic filing dates, missing delisting backfill, etc).
  Generated {generated_at}.
</div>
</body></html>
"""


def generate_html_report(
    *,
    expression: str,
    start, end,
    rebalance: str,
    slippage_bps: float,
    commission_bps: float,
    equity_curve: list[dict],
    benchmark_curve: list[dict] | None,
    metrics: dict,
    rebalances: list[dict],
    trades: list[dict],
    universe_audit: list[dict],
    leaf_fields: list[str],
    warnings: list[str],
    output_path: Path,
) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    svg = _render_equity_svg(equity_curve, benchmark_curve)

    # Metric tiles
    tiles = _metric_tiles(metrics)

    # Rebalance table
    rb_rows = "\n    ".join(
        f"<tr><td>{r['date']}</td><td>{r['universe_size']}</td>"
        f"<td>{r['tradeable']}</td><td>{r['n_trades']}</td>"
        f"<td>₹{r['portfolio_value_before']:,.0f}</td></tr>"
        for r in rebalances
    ) or "<tr><td colspan=5>(none)</td></tr>"

    # Audit table
    leaf_th = "".join(f"<th>{html.escape(lf)}</th>" for lf in leaf_fields)
    if universe_audit:
        audit_rows = "\n    ".join(
            _audit_row(r, leaf_fields) for r in universe_audit
        )
        first_rb_date = rebalances[0]["date"] if rebalances else "—"
    else:
        audit_rows = "<tr><td colspan=99>(empty universe at first rebalance)</td></tr>"
        first_rb_date = rebalances[0]["date"] if rebalances else "—"

    # Trades table — cap at 200 rows for browser sanity
    cap = 200
    trade_rows = "\n    ".join(
        f"<tr><td>{t['date']}</td><td>{t['sc_id']}</td><td>{t['side']}</td>"
        f"<td>{t['shares']}</td><td>₹{t['price']:,.2f}</td>"
        f"<td>₹{t.get('cost', t.get('proceeds', 0)):,.0f}</td></tr>"
        for t in trades[:cap]
    ) or "<tr><td colspan=6>(no trades — empty universe throughout)</td></tr>"
    if len(trades) > cap:
        trade_rows += (
            f"<tr><td colspan=6 class='meta'>... {len(trades) - cap} more trades truncated.</td></tr>"
        )

    warnings_html = ""
    if warnings:
        sample = warnings[:3]
        more = len(warnings) - 3
        items = "<br>".join(html.escape(w) for w in sample)
        if more > 0:
            items += f"<br><span class='warn'>... and {more} more.</span>"
        warnings_html = f"<div class='callout'>{items}</div>"

    expr_html = f'<span class="expression">{html.escape(expression)}</span>'

    html_text = _TEMPLATE.format(
        expression_short=html.escape(expression[:80]),
        expression_html=expr_html,
        start=start, end=end, rebalance=rebalance,
        slippage_bps=slippage_bps, commission_bps=commission_bps,
        warnings_html=warnings_html,
        svg=svg,
        metrics_html=tiles,
        n_rebalances=len(rebalances),
        rebalance_rows=rb_rows,
        first_rb_date=first_rb_date,
        leaf_th=leaf_th,
        audit_rows=audit_rows,
        n_trades=len(trades),
        trade_rows=trade_rows,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    output_path.write_text(html_text, encoding="utf-8")
    return output_path


# ---- Equity-curve SVG ---------------------------------------------------


def _render_equity_svg(curve: list[dict], bench: list[dict] | None) -> str:
    if not curve:
        return "<svg viewBox='0 0 800 280'><text x='10' y='20' fill='#888'>no data</text></svg>"
    width, height = 800, 280
    pad_l, pad_r, pad_t, pad_b = 50, 20, 20, 26

    series = [(curve, "#4ade80", "strategy")]
    if bench:
        series.append((bench, "#60a5fa", "benchmark"))

    all_vals = []
    for s, _, _ in series:
        all_vals.extend(p["value"] for p in s)
    vmin = min(all_vals) * 0.98
    vmax = max(all_vals) * 1.02
    n = len(curve)

    def x(i): return pad_l + (i / max(n - 1, 1)) * (width - pad_l - pad_r)
    def y(v): return pad_t + (1 - (v - vmin) / max(vmax - vmin, 1e-9)) * (height - pad_t - pad_b)

    parts = [f"<svg viewBox='0 0 {width} {height}' xmlns='http://www.w3.org/2000/svg'>"]
    parts.append(
        f"<rect x=0 y=0 width={width} height={height} fill='#161618' rx=8/>"
    )
    # Grid + axis labels
    for frac, val in [(0, vmin), (0.5, (vmin + vmax) / 2), (1, vmax)]:
        yy = pad_t + frac * (height - pad_t - pad_b)
        parts.append(f"<line x1={pad_l} y1={yy} x2={width - pad_r} y2={yy} stroke='#2a2a2e' stroke-width=1/>")
        parts.append(f"<text x=8 y={yy + 4} fill='#8d8d92' font-size=11>₹{val:,.0f}</text>")

    # Plot each series
    for s, color, label in series:
        # Resample to align indices when lengths differ
        m = len(s)
        pts = []
        for i, p in enumerate(s):
            xi = pad_l + (i / max(m - 1, 1)) * (width - pad_l - pad_r)
            yi = y(p["value"])
            pts.append(f"{xi:.1f},{yi:.1f}")
        parts.append(
            f"<polyline fill='none' stroke='{color}' stroke-width='1.5' points='{' '.join(pts)}'/>"
        )

    # Legend
    legend_x = width - pad_r - 130
    parts.append(f"<rect x={legend_x - 6} y={pad_t} width=130 height={22 * len(series)} fill='#0d0d0f' rx=4/>")
    for i, (_, color, label) in enumerate(series):
        ly = pad_t + 14 + i * 22
        parts.append(f"<rect x={legend_x} y={ly - 8} width=10 height=10 fill='{color}'/>")
        parts.append(f"<text x={legend_x + 16} y={ly} fill='#e5e5e7' font-size=12>{label}</text>")

    parts.append("</svg>")
    return "".join(parts)


def _metric_tiles(metrics: dict) -> str:
    def fmt_pct(v): return f"{v:+.1f}%" if v is not None else "—"
    def cls(v): return "pos" if (v or 0) > 0 else "neg" if (v or 0) < 0 else ""
    tiles = [
        ("Total return", fmt_pct(metrics.get("total_return_pct")), cls(metrics.get("total_return_pct"))),
        ("CAGR", fmt_pct(metrics.get("cagr_pct")), cls(metrics.get("cagr_pct"))),
        ("Vol (ann.)", f"{metrics.get('annualised_vol_pct', 0):.1f}%", ""),
        ("Sharpe", f"{metrics.get('sharpe', 0):.2f}", ""),
        ("Max drawdown", f"{metrics.get('max_drawdown_pct', 0):.1f}%", "neg"),
        ("Calmar", f"{metrics.get('calmar', 0):.2f}", ""),
        ("Sortino", f"{metrics.get('sortino', 0):.2f}", ""),
        ("vs benchmark", fmt_pct(metrics.get("alpha_vs_benchmark_pct")), cls(metrics.get("alpha_vs_benchmark_pct"))),
    ]
    return "\n".join(
        f"    <div class='metric'><div class='label'>{lab}</div>"
        f"<div class='value {c}'>{val}</div></div>"
        for lab, val, c in tiles
    )


def _audit_row(r: dict, leaf_fields: list[str]) -> str:
    cells = [
        f"<td>{html.escape(str(r.get('sc_id', '')))}</td>",
        f"<td>{html.escape(str(r.get('company_name', '')))}</td>",
    ]
    for lf in leaf_fields:
        v = r.get(f"{lf}_val")
        cells.append(f"<td>{'—' if v is None else f'{float(v):,.4f}'}</td>")
    cells.append("<td class='included-y'>✓</td>")
    return "<tr>" + "".join(cells) + "</tr>"
