#!/bin/bash
# Poll the universe run every 5 minutes and print progress, rate, ETA and the
# quality signals — from POSTGRES, not from the VM's log. The database is the
# source of truth: if the VM dies, the log stops but the counts stay right, and
# a monitor that reads the log would report "no movement" for a dead run and
# for a slow one identically.
#
#   bash pivotted/pipeline_monitor.sh          # append to the monitor log
VENV=${VENV:-pivot/.venv/bin/python}
LOG=${MON_LOG:-/tmp/pivot_pipeline_monitor.log}
INTERVAL=${MON_INTERVAL:-300}

while true; do
  $VENV - <<'PY' >> "$LOG" 2>&1
import sys, time, json, pathlib
sys.path.insert(0, "pivotted")
import filing_pipeline as P

STATE = pathlib.Path("/tmp/pivot_pipeline_monitor.state")
now = time.time()
with P.connect() as c, c.cursor() as cur:
    cur.execute("SELECT state,count(*) FROM filings.remodel GROUP BY 1")
    rm = dict(cur.fetchall())
    cur.execute("SELECT state,count(*) FROM filings.documents GROUP BY 1")
    docs = dict(cur.fetchall())
    if rm:
        docs = {"done": rm.get("done", 0), "fetched": 0,
                "extracted": rm.get("claimed", 0), "failed": rm.get("failed", 0)}
    # Wave 2 works the remodel queue, wave 1 the url queue. Report whichever
    # is actually running rather than a zero that looks like completion.
    cur.execute("SELECT count(*) FROM filings.remodel WHERE state='pending'")
    rm_pending = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM filings.queue WHERE state='pending'")
    pending = rm_pending or cur.fetchone()[0]
    cur.execute("SELECT count(*),count(DISTINCT symbol) FROM filings.facts")
    nfacts, nsym = cur.fetchone()
    # Quality, not just throughput. A run that speeds up while its grounding
    # rate collapses is a run getting worse, and a progress bar cannot say so.
    cur.execute("""SELECT
        count(*) FILTER (WHERE grounding='exact'),
        count(*) FILTER (WHERE unit_agrees LIKE 'DISAGREE%'),
        count(*) FILTER (WHERE kind='currency' AND value_crore IS NULL
                           AND value_raw IS NOT NULL),
        count(*) FILTER (WHERE kind='currency' AND value_raw IS NOT NULL),
        count(*) FILTER (WHERE period_ambiguous), count(*) FILTER (WHERE partial_table)
        FROM filings.facts""")
    exact, disagree, unres, cur_n, ambig, partial = cur.fetchone()
    cur.execute("""SELECT error,count(*) FROM filings.documents
                   WHERE state='failed' GROUP BY 1 ORDER BY 2 DESC LIMIT 3""")
    fails = cur.fetchall()

done = docs.get("done", 0)
prev = json.loads(STATE.read_text()) if STATE.exists() else None
rate = eta = None
first = prev is None
if prev and now > prev["t"]:
    rate = (done - prev["done"]) / ((now - prev["t"]) / 60)
    if rate > 0:
        eta = (pending + docs.get("fetched", 0) + docs.get("extracted", 0)) / rate
STATE.write_text(json.dumps({"t": now, "done": done}))

# A rate of ZERO is the single most important thing this monitor can report,
# and the first version printed it as "(first sample)" because 0.0 is falsy.
# Five consecutive ticks of a dead pipeline read as five harmless first
# samples. Stalled and starting are opposite conditions and must never share
# a label — this is the same silence-looks-like-progress failure the pipeline
# itself had when a killed thread left a queue waiting on a sentinel.
if pending == 0 and docs.get("fetched", 0) == 0 and docs.get("extracted", 0) == 0:
    # Finished and stalled both show a rate of zero, and calling a completed
    # run STALLED is the same mislabel in the other direction as calling a
    # dead one "(first sample)". Nothing pending and nothing in flight is DONE.
    note = "*** COMPLETE — nothing pending, nothing in flight ***"
elif first:
    note = "(first sample — no rate yet)"
elif rate == 0:
    note = "*** STALLED — zero documents completed since the last tick ***"
else:
    note = f"{rate:.1f} docs/min   ETA {eta/60:.1f}h"

ts = time.strftime("%H:%M:%S")
tot = done + pending + docs.get("fetched", 0) + docs.get("extracted", 0)
pct = 100 * done / max(tot, 1)
print(f"[{ts}] {done:,}/{tot:,} done ({pct:.1f}%)  pending={pending:,} "
      f"inflight={docs.get('fetched',0)+docs.get('extracted',0)} "
      f"failed={docs.get('failed',0)}")
print(f"          facts={nfacts:,} over {nsym:,} companies   {note}")
if nfacts:
    print(f"          QUALITY grounded-exact {100*exact/nfacts:.1f}%  "
          f"unresolved-units {100*unres/max(cur_n,1):.1f}%  "
          f"unit-disagree {100*disagree/max(cur_n,1):.1f}%  "
          f"period-ambig {ambig:,}  partial-table {partial:,}")
for e, n in fails:
    print(f"          FAIL {n:>4}  {(e or '')[:70]}")
PY
  sleep "$INTERVAL"
done
