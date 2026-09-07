#!/usr/bin/env python3
"""ElevenLabs credit grant — one page, serif, almost no prose.

The API response is the proof: it is ElevenLabs' own endpoint returning this
account's grant size, so the document does not have to argue for the number.
The only thing added is the arithmetic that turns credits into dollars, shown
in full rather than asserted.

    python3 make_el_proof.py     # writes EL_PROOF.html
"""
from __future__ import annotations

import base64
import html
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRATCH = Path("/private/tmp/claude-501/-Users-karanveersingh-Downloads-Second-Star"
               "/302ff12e-2fdb-4306-9a81-b1b9cc764227/scratchpad")
OUT = HERE / "EL_PROOF.html"

RESPONSE = (SCRATCH / "el_sub.out").read_text().rstrip()
SHOT = base64.b64encode((SCRATCH / "el_billing.png").read_bytes()).decode()

CMD = ('<span class="p">$</span> <span class="c">curl -s -H "xi-api-key: $ELEVENLABS_API_KEY" \\\n'
       '       https://api.elevenlabs.io/v1/user/subscription</span>')

GRANT = 33_010_000
BUSINESS_CREDITS, BUSINESS_MONTHLY = 6_000_000, 990
months = GRANT / BUSINESS_CREDITS
value = months * BUSINESS_MONTHLY
reset = datetime.fromtimestamp(1816959936).strftime("%d %B %Y")

CSS = """
@page { size: A4; margin: 16mm 20mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; background: #fff; color: #000;
       font: 12pt/1.45 "Times New Roman", Times, serif; }
.page { max-width: 170mm; margin: 0 auto; }
h1 { font-size: 20pt; font-weight: 700; margin: 0 0 2mm; letter-spacing: -.01em; }
.sub { font-size: 11pt; font-style: italic; color: #333; margin: 0 0 7mm; }
hr { border: 0; border-top: 1px solid #000; margin: 0 0 6mm; }
h2 { font-size: 12pt; font-weight: 700; margin: 5mm 0 2mm; }

/* The response keeps a monospace face on purpose — it is a machine's answer,
   and setting JSON in Times would make it read as something retyped by hand. */
pre { margin: 0; padding: 4mm 5mm; background: #f4f2ec; border: 1px solid #ccc7b8;
      font: 8pt/1.42 "Courier New", Courier, monospace; white-space: pre;
      color: #111; }
pre .p { color: #444; }
pre .c { color: #111; }

table { border-collapse: collapse; margin: 2mm 0 0; font-size: 11pt; }
td { padding: 1.4mm 9mm 1.4mm 0; }
td.n { text-align: right; padding-right: 0; }
.rule td { border-top: 1px solid #000; font-weight: 700; }
.note { font-size: 10pt; color: #333; margin: 3mm 0 0; font-style: italic; }
.shot { width: 76mm; display: block; border: 1px solid #ccc7b8; margin: 0 0 4mm; }
"""

doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>ElevenLabs — Credit Grant</title>
<style>{CSS}</style></head><body><div class="page">

<h1>ElevenLabs &mdash; Credit Grant</h1>
<div class="sub">33,010,000 credits &middot; active to {reset}</div>
<hr>

<h2>Grant</h2>
<img class="shot" src="data:image/png;base64,{SHOT}" alt="ElevenLabs billing panel showing the Grant Plan">
<pre>{CMD}

{html.escape(RESPONSE)}</pre>

<h2>Value at list price</h2>
<table>
  <tr><td>Grant</td><td class="n">33,010,000 credits</td></tr>
  <tr><td>Business plan</td><td class="n">6,000,000 credits &middot; $990 / month</td></tr>
  <tr><td>Equivalent</td><td class="n">{months:.2f} months</td></tr>
  <tr class="rule"><td>Value</td><td class="n">${value:,.0f}</td></tr>
</table>
<p class="note">Non-transferable service credit. 47,041 credits used to date (0.14%).</p>

</div></body></html>"""

OUT.write_text(doc)
print(f"{OUT.name}  ·  {months:.2f} months  ·  ${value:,.0f}")
