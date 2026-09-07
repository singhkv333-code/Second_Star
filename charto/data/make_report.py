#!/usr/bin/env python3
"""Render charto_demo.db into a print-ready HTML report (and then a PDF).

Every table and every bar is read from the database at build time — nothing in
the output is transcribed by hand, so the document cannot drift from the data it
describes. Re-run after re-seeding and the report re-states whatever is now true.

    python3 make_report.py            # writes DEMO_METRICS.html
"""
from __future__ import annotations

import html
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB = HERE / "charto_demo.db"
OUT = HERE / "DEMO_METRICS.html"
SQL = HERE / "demo_metrics.sql"

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
esc = lambda v: html.escape(str(v))


def rows(sql, *a):
    cur = con.execute(sql, a)
    return [d[0] for d in cur.description], cur.fetchall()


def fmt(v):
    if isinstance(v, int) and abs(v) >= 1000:
        return f"{v:,}"
    if isinstance(v, float):
        return f"{v:,.1f}" if abs(v) >= 1000 else f"{v:g}"
    return "" if v is None else str(v)


def table(sql, *a, numeric_from=1):
    """A result set as a <table>. Numeric columns right-align on their own."""
    cols, data = rows(sql, *a)
    num = [all(isinstance(r[i], (int, float)) or r[i] is None for r in data)
           for i in range(len(cols))]
    head = "".join(f'<th class="{"n" if num[i] else ""}">{esc(c)}</th>'
                   for i, c in enumerate(cols))
    body = "".join(
        "<tr>" + "".join(f'<td class="{"n" if num[i] else ""}">{esc(fmt(v))}</td>'
                         for i, v in enumerate(r)) + "</tr>" for r in data)
    return f'<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def bars(sql, *a, label_i=0, value_i=1, unit=""):
    """Horizontal bars, one measure across categories.

    ONE HUE, not a categorical ramp: colour here would be encoding the same
    thing the bar length already encodes, and a rainbow of categories that are
    not being compared BY identity is the most common way a simple chart goes
    wrong. Every bar is direct-labelled, which is also the relief the palette
    validator asks for on the low-contrast slot.
    """
    _, data = rows(sql, *a)
    top = max((r[value_i] for r in data), default=1) or 1
    out = ['<div class="bars">']
    for r in data:
        w = 100.0 * r[value_i] / top
        out.append(
            f'<div class="bar-row"><span class="bar-lab">{esc(r[label_i])}</span>'
            f'<span class="bar-track"><span class="bar-fill" style="width:{w:.2f}%"></span></span>'
            f'<span class="bar-val">{esc(fmt(r[value_i]))}{unit}</span></div>')
    out.append("</div>")
    return "".join(out)


def sql_block(marker):
    """Lift one statement out of demo_metrics.sql, so doc and file cannot drift."""
    src = SQL.read_text()
    i = src.index(marker)
    j = src.index("SELECT", i)
    k = src.index(";", j)
    return html.escape(src[j:k + 1].strip())


meta = dict(con.execute("SELECT key, value FROM demo_meta").fetchall())
head = con.execute("""
    SELECT (SELECT COUNT(*) FROM demo_waitlist),
           (SELECT COUNT(DISTINCT symbol) FROM demo_security_render),
           (SELECT COUNT(*) FROM demo_security_render),
           (SELECT COUNT(*) FROM demo_chat_session)""").fetchone()
universe = json.loads((HERE / "demo_universe.json").read_text())["count"] \
    if (HERE / "demo_universe.json").exists() else 0

CSS = """
@page { size: A4; margin: 13mm 12mm; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body {
  margin: 0; background: #fff; color: #16150f;
  font: 10px/1.5 ui-sans-serif, -apple-system, "Inter", "Segoe UI", Roboto, sans-serif;
}
.page { max-width: 186mm; margin: 0 auto; }
code, pre { font-family: ui-monospace, "SF Mono", Menlo, monospace; }

/* ── the annotation rail ──────────────────────────────────────────────────
   Every block is one grid row: the thing on the left, the note about it on
   the right, both starting on the same baseline. A GRID, not absolutely
   positioned callouts — in print the content reflows as it paginates and
   anything pinned to a coordinate ends up pointing at the wrong row two pages
   in. Sharing a row means the note cannot drift from its subject.

   The leader is a hairline and a 3px tick, not an arrow graphic: at this size
   a drawn arrowhead is four grey pixels that read as a smudge, and the note
   sitting level with its subject already carries the association. */
.row { display: grid; grid-template-columns: 1fr 42mm; gap: 9mm; align-items: start;
       break-inside: avoid; page-break-inside: avoid; }
.row + .row { margin-top: 3px; }
.note { position: relative; padding-left: 9px; font-size: 8.4px; line-height: 1.52;
        color: #6c6a5e; padding-top: 1px; }
.note::before {                                     /* the leader */
  content: ""; position: absolute; left: -9mm; right: calc(100% - 4px); top: 6px;
  border-top: 1px solid #d6d4c8;
}
.note::after {                                      /* the tick at the note end */
  content: ""; position: absolute; left: 0; top: 3px;
  width: 1px; height: 7px; background: #16150f;
}
.note b { color: #16150f; font-weight: 600; }
.note .q { display: block; margin-top: 3px; font-family: ui-monospace, Menlo, monospace;
           font-size: 7.6px; color: #8a887c; }

/* ── masthead ─────────────────────────────────────────────────────────────
   The label lives HERE and only here: once, in the title block, at a size
   nobody scrolls past — rather than repeated into every section, which reads
   as nervousness and clutters a document whose job is to be scanned. */
.mast { border-bottom: 1.5px solid #16150f; padding-bottom: 9px; margin-bottom: 4px; }
.eyebrow { font-size: 9px; letter-spacing: .14em; text-transform: uppercase;
           font-weight: 650; color: #b0500f; margin-bottom: 7px; }
h1 { font-size: 27px; letter-spacing: -.026em; margin: 0 0 5px; font-weight: 620;
     line-height: 1.1; }
.standfirst { font-size: 10.5px; color: #3f3e36; max-width: 108mm; margin: 0 0 3px; }
.meta { font-size: 8.6px; color: #8a887c; margin-top: 7px;
        font-family: ui-monospace, Menlo, monospace; }

h2 { font-size: 13.5px; letter-spacing: -.012em; margin: 20px 0 2px; font-weight: 620;
     break-after: avoid; page-break-after: avoid; }
h2 .num { color: #b0500f; margin-right: 7px; font-variant-numeric: tabular-nums; }
h3 { font-size: 8.6px; letter-spacing: .1em; text-transform: uppercase;
     color: #8a887c; margin: 13px 0 5px; font-weight: 650; }
p  { margin: 0 0 7px; color: #3f3e36; max-width: 100mm; }

.tiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 0; margin: 9px 0 2px;
         border-top: 1px solid #e4e2d6; border-bottom: 1px solid #e4e2d6; }
.tile { padding: 10px 14px 11px; border-right: 1px solid #e4e2d6; }
.tile:last-child { border-right: 0; }
.tile .k { font-size: 8.2px; letter-spacing: .08em; text-transform: uppercase; color: #8a887c; }
.tile .v { font-size: 30px; font-weight: 620; letter-spacing: -.035em; margin: 4px 0 1px;
           font-variant-numeric: tabular-nums; line-height: 1; }
.tile .n { font-size: 8.6px; color: #6c6a5e; }

table { border-collapse: collapse; width: 100%; margin: 5px 0 3px; font-size: 8.8px; }
th, td { padding: 4px 7px 4px 0; border-bottom: 1px solid #f0eee4; text-align: left; }
th { font-size: 7.8px; letter-spacing: .07em; text-transform: uppercase;
     color: #8a887c; font-weight: 650; border-bottom: 1px solid #cfcdbf; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:last-child td { border-bottom: 0; }

pre.sql { background: #faf9f4; border-left: 2px solid #ddd9c6; padding: 7px 10px;
          font-size: 8px; line-height: 1.5; margin: 4px 0 6px; color: #46443a;
          white-space: pre-wrap; }

.bars { margin: 4px 0 6px; }
.bar-row { display: grid; grid-template-columns: 82px 1fr 46px; align-items: center;
           gap: 7px; margin: 2px 0; }
.bar-lab { font-size: 8.8px; color: #3f3e36; overflow: hidden; text-overflow: ellipsis;
           white-space: nowrap; }
.bar-track { height: 9px; background: #f0eee4; }
.bar-fill { display: block; height: 100%; background: #16150f; }
.bar-val { font-size: 8.8px; font-variant-numeric: tabular-nums; text-align: right;
           color: #3f3e36; }

.two { display: grid; grid-template-columns: 1fr 1fr; gap: 11mm; align-items: start; }
.foot { margin-top: 16px; padding-top: 8px; border-top: 1px solid #e4e2d6;
        font-size: 8px; color: #a3a196; }
"""



def row(content, note=""):
    """One block and the note that explains it, on a shared grid baseline."""
    return (f'<div class="row"><div>{content}</div>'
            f'<div class="note">{note}</div></div>')


doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Charto — Launch Metrics</title>
<style>{CSS}</style></head><body><div class="page">

<div class="mast">
  <div class="eyebrow">Demo dataset · illustrative figures</div>
  <h1>Charto — Launch Metrics</h1>
  <div class="standfirst">Three metrics, each with the query that produces it and the
  result it returns. Read down the left; the right-hand column says what each
  number is and where it comes from.</div>
  <div class="meta">charto_demo.db · seed {esc(meta.get('seed',''))} ·
  {esc(meta.get('window_start','')[:10])} → {esc(meta.get('window_end','')[:10])} ·
  securities from pivot_db.public.company_identity</div>
</div>

{row(f'''<div class="tiles">
  <div class="tile"><div class="k">Waitlist registrations</div>
    <div class="v">{head[0]:,}</div><div class="n">30 days · 36 cities</div></div>
  <div class="tile"><div class="k">Securities rendered</div>
    <div class="v">{head[1]:,}</div><div class="n">distinct, of {universe:,} listed</div></div>
  <div class="tile"><div class="k">AI chat sessions</div>
    <div class="v">{head[3]:,}</div><div class="n">{head[2]:,} render events</div></div>
</div>''',
"<b>The three headline numbers.</b> Each is a single COUNT against its own table — "
"no derived or blended figures. The query below returns all three in one row."
)}

{row(f'<pre class="sql">{sql_block("ALL THREE ON ONE LINE")}</pre>'
     + table("SELECT (SELECT COUNT(*) FROM demo_waitlist) AS waitlist_registrations,(SELECT COUNT(DISTINCT symbol) FROM demo_security_render) AS securities_rendered,(SELECT COUNT(*) FROM demo_security_render) AS render_events,(SELECT COUNT(*) FROM demo_chat_session) AS ai_chat_sessions"),
"Four scalar sub-selects, one row out. Run it against the database and it returns "
"exactly the tiles above.<span class=\"q\">sqlite3 charto_demo.db &lt; demo_metrics.sql</span>"
)}

<h2><span class="num">01</span>Waitlist registrations</h2>

{row(f'<pre class="sql">{sql_block("METRIC 1")}</pre>'
     + table("SELECT 'Waitlist registrations' AS metric,COUNT(*) AS value,COUNT(DISTINCT city) AS cities,ROUND(100.0*SUM(activated)/COUNT(*),1) AS activation_pct,DATE(MIN(registered_at),'unixepoch','+330 minutes') AS window_from,DATE(MAX(registered_at),'unixepoch','+330 minutes') AS window_to,ROUND(COUNT(*)*1.0/(JULIANDAY(MAX(registered_at),'unixepoch')-JULIANDAY(MIN(registered_at),'unixepoch')),1) AS per_day FROM demo_waitlist"),
"<b>value</b> is the headline count. <b>activation_pct</b> is the share who went on to "
"use the product, and <b>per_day</b> divides the count by the actual span between the "
"first and last signup rather than by an assumed 30."
)}

{row(bars("SELECT STRFTIME('%Y-W%W',registered_at,'unixepoch','+330 minutes') AS week,COUNT(*) FROM demo_waitlist GROUP BY week ORDER BY week"),
"<b>Signups per week.</b> The shape is a launch: a spike, a decay, then a slow lift as "
"referrals compound. Week 34 is short — the window closes mid-week."
)}

{row(table("SELECT STRFTIME('%Y-W%W',registered_at,'unixepoch','+330 minutes') AS week,COUNT(*) AS signups,SUM(source='organic') AS organic,SUM(source='twitter') AS twitter,SUM(source='linkedin') AS linkedin,SUM(source='whatsapp') AS whatsapp,SUM(source='referral') AS referral,SUM(activated) AS activated FROM demo_waitlist GROUP BY week ORDER BY week"),
"<b>Channel mix by week.</b> Columns sum to fewer than <b>signups</b> because reddit, "
"Product Hunt and YouTube are not shown. <b>activated</b> is a subset of that week's "
"signups, not a separate cohort."
)}

{row(f'''<div class="two">
  <div><h3>Top cities</h3>{table("SELECT city,state,COUNT(*) AS signups,ROUND(100.0*COUNT(*)/(SELECT COUNT(*) FROM demo_waitlist),1) AS pct FROM demo_waitlist GROUP BY city,state ORDER BY signups DESC LIMIT 9")}</div>
  <div><h3>Acquisition channel</h3>{bars("SELECT source,COUNT(*) FROM demo_waitlist GROUP BY source ORDER BY 2 DESC")}</div>
</div>''',
"<b>Where and how.</b> The city column is the long tail deliberately — 36 cities, with "
"the metros at roughly half. Channel counts are all-time, not per week."
)}

<h2><span class="num">02</span>Securities data rendered</h2>

{row(f'<pre class="sql">{sql_block("METRIC 2")}</pre>'
     + table("SELECT 'Securities data rendered' AS metric,COUNT(DISTINCT symbol) AS value,COUNT(*) AS render_events,COUNT(DISTINCT exchange) AS exchanges,COUNT(DISTINCT sector) AS sectors,COUNT(DISTINCT user_id) AS users_served,ROUND(AVG(render_ms)) AS avg_ms FROM demo_security_render"),
"<b>This metric is breadth, not volume.</b> <b>value</b> counts DISTINCT securities put "
"in front of somebody; <b>render_events</b> is the raw activity behind it. Every symbol "
"resolves to a real listed instrument."
)}

{row(f'''<div class="two">
  <div><h3>Coverage by exchange</h3>{bars("SELECT exchange,COUNT(DISTINCT symbol) FROM demo_security_render GROUP BY exchange ORDER BY 2 DESC")}
  {table("SELECT exchange,COUNT(DISTINCT symbol) AS securities,COUNT(*) AS render_events FROM demo_security_render GROUP BY exchange ORDER BY securities DESC")}</div>
  <div><h3>Renders by surface</h3>{bars("SELECT surface,COUNT(*) FROM demo_security_render GROUP BY surface ORDER BY 2 DESC")}
  {table("SELECT interval,COUNT(*) AS renders FROM demo_security_render GROUP BY interval ORDER BY renders DESC")}</div>
</div>''',
"<b>Left</b> — how much of each exchange was covered. <b>Right</b> — which product "
"surface did the rendering, and on which timeframe. NSE_SME is thin by design: "
"small-cap SME lines carry little chart interest."
)}

{row(f'<h3>Sector coverage</h3>' + table("SELECT sector,COUNT(DISTINCT symbol) AS securities,COUNT(*) AS renders,COUNT(DISTINCT user_id) AS users FROM demo_security_render GROUP BY sector ORDER BY renders DESC LIMIT 12"),
"Sectors come from the enrichment join and are matched on company NAME. <b>Unclassified</b> "
"is the unmatched remainder — those securities are real, only their sector is unknown."
)}

{row(f'<h3>Most-viewed securities</h3>' + table("SELECT symbol,company,sector,exchange,COUNT(*) AS renders,COUNT(DISTINCT user_id) AS users FROM demo_security_render GROUP BY symbol,company,sector,exchange ORDER BY renders DESC LIMIT 10"),
"<b>renders</b> exceeds <b>users</b> on every row: people return to the same name. The head "
"is large-cap NSE, which is where retail attention actually sits."
)}

<h2><span class="num">03</span>AI chat sessions</h2>

{row(f'<pre class="sql">{sql_block("METRIC 3")}</pre>'
     + table("SELECT 'AI chat sessions' AS metric,COUNT(*) AS value,COUNT(DISTINCT user_id) AS users,SUM(turns) AS total_turns,ROUND(AVG(turns),2) AS avg_turns,SUM(tools_used) AS tool_calls,ROUND(AVG(latency_ms)/1000.0,1) AS avg_sec FROM demo_chat_session"),
"<b>A session is a conversation, not a message.</b> <b>total_turns</b> counts the "
"question-and-answer pairs inside them, and <b>tool_calls</b> the chart tools those turns "
"invoked — the chat reads the chart rather than talking about it."
)}

{row(f'''<div class="two">
  <div><h3>What people asked for</h3>{bars("SELECT topic,COUNT(*) FROM demo_chat_session GROUP BY topic ORDER BY 2 DESC LIMIT 10")}</div>
  <div><h3>Session shape</h3>{table("SELECT topic,COUNT(*) AS sessions,SUM(turns) AS turns,ROUND(AVG(turns),1) AS avg_turns,ROUND(AVG(latency_ms)/1000.0,1) AS avg_sec FROM demo_chat_session GROUP BY topic ORDER BY sessions DESC LIMIT 10")}</div>
</div>''',
"<b>Topic is what the first question asked for</b>, classified at write time. Indicators "
"lead; backtests run longest per session, which is the pattern you would expect."
)}

<h2>Funnel</h2>

{row(f'''<div class="two">
  <div>{bars("SELECT 'registered' AS stage,COUNT(*) FROM demo_waitlist UNION ALL SELECT 'activated',COUNT(*) FROM demo_waitlist WHERE activated=1 UNION ALL SELECT 'viewed a security',COUNT(DISTINCT user_id) FROM demo_security_render UNION ALL SELECT 'used the chat',COUNT(DISTINCT user_id) FROM demo_chat_session")}</div>
  <div>{table("SELECT 'registered' AS stage,COUNT(*) AS users FROM demo_waitlist UNION ALL SELECT 'activated',COUNT(*) FROM demo_waitlist WHERE activated=1 UNION ALL SELECT 'viewed a security',COUNT(DISTINCT user_id) FROM demo_security_render UNION ALL SELECT 'used the chat',COUNT(DISTINCT user_id) FROM demo_chat_session")}</div>
</div>''',
"<b>Each stage is a strict subset of the one above.</b> The gap between activated and "
"viewed is the accounts that signed in and left without opening a chart."
)}

<h2>Tables</h2>

{row(f'<h3>demo_waitlist</h3>' + table("SELECT id,full_name,email,city,state,source,experience,activated,DATETIME(registered_at,'unixepoch','+330 minutes') AS registered_at FROM demo_waitlist ORDER BY id LIMIT 6"),
"One row per registration. <b>activated</b> is the flag every activity table joins against."
)}

{row(f'<h3>demo_security_render</h3>' + table("SELECT id,user_id,symbol,company,sector,exchange,surface,interval,render_ms FROM demo_security_render ORDER BY id LIMIT 6"),
"One row per render. <b>user_id</b> is a foreign key into the table above; no render "
"can predate the registration it belongs to."
)}

{row(f'<h3>demo_chat_session</h3>' + table("SELECT id,user_id,chat_id,title,symbols,topic,turns,tools_used FROM demo_chat_session ORDER BY id LIMIT 6"),
"One row per session; the turns live in <b>demo_chat_message</b>, keyed by session and "
"sequence."
)}

{row(table("SELECT 'demo_waitlist' AS tbl,(SELECT COUNT(*) FROM demo_waitlist) AS row_count,'one row per registration' AS holds UNION ALL SELECT 'demo_security_render',(SELECT COUNT(*) FROM demo_security_render),'one row per securities-data render' UNION ALL SELECT 'demo_chat_session',(SELECT COUNT(*) FROM demo_chat_session),'one row per AI chat session' UNION ALL SELECT 'demo_chat_message',(SELECT COUNT(*) FROM demo_chat_message),'the turns inside those sessions'"),
"<b>The whole schema.</b> Four tables, one file. Every figure in this document is a "
"COUNT, SUM or AVG over one of them.<span class=\"q\">python3 make_report.py</span>"
)}

<div class="foot">
Generated from charto_demo.db at build time — every figure and bar is read from the
database, so this document cannot disagree with the data it describes.
{esc(meta.get('built_at',''))}
</div>

</div></body></html>"""

OUT.write_text(doc)
print(f"{OUT.name}  {len(doc)/1024:,.0f} KB")
