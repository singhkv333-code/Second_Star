#!/usr/bin/env python3
"""Monitored parallel V3 edge-pattern run over an indexed bars_1d table."""
from __future__ import annotations
import argparse, json, sqlite3, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
import chart_patterns_v3 as v3
import dataserver as ds

def task(db, out, symbol):
    t=time.time(); c=sqlite3.connect(f"file:{db}?mode=ro",uri=True)
    rows=c.execute("select ts,o,h,l,c,v from bars_1d where symbol=? order by ts",(symbol,)).fetchall();c.close()
    events=v3.event_driven_edge_patterns(rows,symbol,set(ds._EDGE_ONLY))
    p=Path(out)/symbol.replace(' ','_').replace('/','_');p.mkdir(parents=True,exist_ok=True)
    (p/'events.json').write_text(json.dumps({'symbol':symbol,'interval':'1d','bars':len(rows),'events':events,'seconds':round(time.time()-t,3)},separators=(',',':'))+'\n')
    return symbol,len(rows),len(events),round(time.time()-t,3)

def main():
    a=argparse.ArgumentParser();a.add_argument('--db',required=True);a.add_argument('--out',required=True);a.add_argument('--workers',type=int,default=12);a.add_argument('--heartbeat',required=True);x=a.parse_args()
    c=sqlite3.connect(f"file:{x.db}?mode=ro",uri=True); syms=[r[0] for r in c.execute('select symbol from bars_1d group by symbol order by symbol')];c.close()
    Path(x.out).mkdir(parents=True,exist_ok=True);hb=Path(x.heartbeat);done=[];fail=[];start=time.time()
    def ping(status='running'):
        s={'phase':'v3_daily','status':status,'updated_epoch':time.time(),'completed':len(done),'total':len(syms),'failed':len(fail),'elapsed_seconds':round(time.time()-start,1)};hb.write_text(json.dumps(s)+'\n');print(json.dumps(s),flush=True)
    ping()
    with ProcessPoolExecutor(max_workers=min(x.workers,len(syms))) as pool:
        jobs={pool.submit(task,x.db,x.out,s):s for s in syms}
        while jobs:
            ready=[]
            try:
                for f in as_completed(jobs,timeout=30):ready.append(f)
            except TimeoutError:pass
            for f in ready:
                s=jobs.pop(f)
                try:done.append(f.result())
                except Exception as e:fail.append((s,repr(e)))
            ping()
    (Path(x.out)/'manifest.json').write_text(json.dumps({'version':v3.DETECTOR_VERSION,'tasks':done,'failures':fail},indent=2)+'\n');ping('failed' if fail else 'complete');return 1 if fail else 0
if __name__=='__main__':raise SystemExit(main())
