#!/usr/bin/env python3
"""One-page proof sheet: title, then the query and its output, three times.

Deliberately almost wordless. The document makes its case by showing the exact
command that was run and the exact bytes that came back — anything written
around that is the author asserting rather than the database answering.

Output is captured by running sqlite3 for real, not transcribed, so the blocks
below cannot drift from what the database actually returns.

    python3 make_proof.py        # writes PROOF.html
"""
from __future__ import annotations

import html
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "charto_demo.db"
SQL = HERE / "demo_metrics.sql"
OUT = HERE / "PROOF.html"

TITLES = ["Waitlist registrations", "Securities data rendered", "AI chat sessions"]


def query(n: int) -> str:
    s = SQL.read_text()
    i = s.index(f"METRIC {n} ")
    j = s.index("SELECT", i)
    k = s.index(";", j)
    return s[j:k + 1].strip()


def run(q: str) -> str:
    r = subprocess.run(["sqlite3", "-header", "-column", str(DB), q],
                       capture_output=True, text=True, check=True)
    return r.stdout.rstrip("\n")


def term(prompt: str, body: str) -> str:
    """A terminal window. The three dots are the whole chrome — enough for the
    block to read as a captured session rather than a styled code sample."""
    return (f'<div class="term"><div class="bar">'
            f'<i></i><i></i><i></i></div>'
            f'<pre>{prompt}{html.escape(body)}</pre></div>')


blocks = []
for n in (1, 2, 3):
    q = query(n)
    o = run(q)
    cmd = ('<span class="p">$</span> <span class="c">sqlite3 -header -column '
           'charto_demo.db</span>\n')
    blocks.append(
        f'<section><h2>{n}. {TITLES[n-1]}</h2>'
        + term(cmd, q + "\n")
        + term("", o)
        + "</section>")

CSS = """
@page { size: A4; margin: 14mm 13mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; background: #fff; color: #111;
       font: 11px/1.5 ui-sans-serif, -apple-system, "Inter", "Segoe UI", sans-serif; }
.page { max-width: 184mm; margin: 0 auto; }
.mast { border-bottom: 1.5px solid #111; padding-bottom: 8px; margin-bottom: 16px; }
h1 { font-size: 24px; letter-spacing: -.025em; font-weight: 620; margin: 0; }
.tag { font-size: 8.5px; letter-spacing: .16em; text-transform: uppercase;
       font-weight: 650; color: #b0500f; margin-bottom: 6px; }
section { break-inside: avoid; page-break-inside: avoid; margin-bottom: 15px; }
h2 { font-size: 11.5px; font-weight: 620; margin: 0 0 6px; letter-spacing: -.008em; }
.term { background: #14161a; border-radius: 6px; overflow: hidden; margin: 0 0 5px; }
.bar { height: 15px; background: #22262c; display: flex; align-items: center;
       padding-left: 7px; gap: 4px; }
.bar i { width: 6px; height: 6px; border-radius: 50%; background: #4a5058; display: block; }
.bar i:first-child { background: #e05c4a; }
.bar i:nth-child(2)  { background: #d9a13a; }
.bar i:nth-child(3)  { background: #4fa85c; }
.term pre { margin: 0; padding: 8px 11px 9px; color: #d8dde3; white-space: pre;
            font: 7.6px/1.45 ui-monospace, "SF Mono", Menlo, monospace; }
.term .p { color: #4fa85c; }
.term .c { color: #7fb6e8; }
"""

doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Pivot — Launch Metrics</title>
<style>{CSS}</style></head><body><div class="page">
<div class="mast">
  <div class="tag">Demo dataset</div>
  <h1>Pivot — Launch Metrics</h1>
</div>
{''.join(blocks)}
</div></body></html>"""

OUT.write_text(doc)
print(f"{OUT.name}  {len(doc)/1024:.0f} KB")
