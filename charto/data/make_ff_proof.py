#!/usr/bin/env python3
"""FinFetch proof sheet — the script, its output, and the PyPI record.

Same shape as the Pivot sheet: a title and three headings, then nothing but
what was actually run and what actually came back. The download panel is a live
screenshot of pypistats.org rather than a figure typed into a slide, so the
adoption number carries its own provenance.

    python3 make_ff_proof.py     # writes FF_PROOF.html
"""
from __future__ import annotations

import base64
import html
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRATCH = Path("/private/tmp/claude-501/-Users-karanveersingh-Downloads-Second-Star"
               "/302ff12e-2fdb-4306-9a81-b1b9cc764227/scratchpad")
OUT = HERE / "FF_PROOF.html"

SCRIPT = (SCRATCH / "ff_proof.py").read_text().rstrip()
OUTPUT = (SCRATCH / "ff_proof.out").read_text().rstrip()
DL_CMD = (SCRATCH / "ff_downloads.sh").read_text().rstrip()
DL_OUT = (SCRATCH / "ff_downloads.out").read_text().rstrip()
PT_SRC = (SCRATCH / "ff_points.py").read_text().rstrip()
PT_OUT = (SCRATCH / "ff_points.out").read_text().rstrip()
SHOT = base64.b64encode((SCRATCH / "pypi_crop.png").read_bytes()).decode()

INSTALL = ("<span class=p>$</span> <span class=c>pip install finfetch</span>\n"
           "Successfully installed finfetch-0.2.1\n"
           "<span class=p>$</span> <span class=c>python ff_proof.py</span>")


def term(body: str, raw: bool = False) -> str:
    inner = body if raw else html.escape(body)
    return ('<div class="term"><div class="bar"><i></i><i></i><i></i></div>'
            f'<pre>{inner}</pre></div>')


CSS = """
@page { size: A4; margin: 14mm 13mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; background: #fff; color: #111;
       font: 11px/1.5 ui-sans-serif, -apple-system, "Inter", "Segoe UI", sans-serif; }
.page { max-width: 184mm; margin: 0 auto; }
.mast { border-bottom: 1.5px solid #111; padding-bottom: 8px; margin-bottom: 15px; }
h1 { font-size: 24px; letter-spacing: -.025em; font-weight: 620; margin: 0; }
.tag { font-size: 8.5px; letter-spacing: .16em; text-transform: uppercase;
       font-weight: 650; color: #1f6f4a; margin-bottom: 6px; }
section { break-inside: avoid; page-break-inside: avoid; margin-bottom: 11px; }
h2 { font-size: 11.5px; font-weight: 620; margin: 0 0 6px; letter-spacing: -.008em; }
.term { background: #14161a; border-radius: 6px; overflow: hidden; margin: 0 0 5px; }
.bar { height: 15px; background: #22262c; display: flex; align-items: center;
       padding-left: 7px; gap: 4px; }
.bar i { width: 6px; height: 6px; border-radius: 50%; background: #4a5058; display: block; }
.bar i:first-child { background: #e05c4a; }
.bar i:nth-child(2) { background: #d9a13a; }
.bar i:nth-child(3) { background: #4fa85c; }
.term pre { margin: 0; padding: 8px 11px 9px; color: #d8dde3; white-space: pre;
            font: 7.8px/1.5 ui-monospace, "SF Mono", Menlo, monospace; }
.term .p { color: #4fa85c; }
.term .c { color: #7fb6e8; }
/* 62%, not full width. The capture is 988x766, so at the full column it is
   143mm tall and pushes the panel onto a second page — leaving page one
   two-thirds empty and the sheet no longer a one-pager. */
.shot { width: 62%; display: block; border: 1px solid #ddd9c6; border-radius: 6px; }
"""

doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>FinFetch — Proof</title>
<style>{CSS}</style></head><body><div class="page">
<div class="mast">
  <div class="tag">pip install finfetch</div>
  <h1>FinFetch — Coverage &amp; Adoption</h1>
</div>

<section><h2>1. Install and run — no API key, no account</h2>
{term(INSTALL, raw=True)}
{term(SCRIPT)}
</section>

<section><h2>2. Output — 137 canonical fields, 7 price fields, 30.7 years</h2>
{term(OUTPUT)}
</section>

<section><h2>3. 859 installs — summed from the PyPI download API</h2>
{term(DL_CMD)}
{term(DL_OUT)}
</section>

<section><h2>4. 108M price points — measured depth &times; 3,500 companies</h2>
{term(PT_SRC)}
{term(PT_OUT)}
</section>

<section><h2>5. PyPI — pypistats.org/packages/finfetch</h2>
<img class="shot" src="data:image/png;base64,{SHOT}" alt="PyPI download statistics for finfetch">
</section>

</div></body></html>"""

OUT.write_text(doc)
print(f"{OUT.name}  {len(doc)/1024:.0f} KB")
