"""Full-universe filing pipeline: discover → fetch → extract → model, all on Azure.

THREE STAGES, ONE PROCESS, NEVER THREE RUNS.

Run sequentially and the model sits idle for the whole download, and the
downloader sits idle for the whole model pass. The three stages are bound by
three DIFFERENT resources, so each gets its own pool sized to its own limit and
they are joined by BOUNDED queues:

    discover/fetch  --q(bounded)-->  extract  --q(bounded)-->  model
    network I/O                      CPU                       Azure TPM
    threads, per-host semaphores     processes = cpu_count     threads

The queues must be bounded. Unbounded, the fetcher races ahead and puts the
whole ~30GB of PDFs on disk before the model has finished its first company;
bounded, a slow model stage applies backpressure up the chain and nothing
stalls anything else.

Per-host semaphores matter too: recon measured NSE at 14.5 companies/s and BSE
at 5.8/s, and a single shared pool lets the slower exchange throttle the faster.

WHY THE STAGE SIZES ARE WHAT THEY ARE (all measured, none guessed)
  * Azure quota, read from live response headers: gpt-5.4-mini 5,000 RPM /
    5,000,000 TPM; gpt-5.6-luna 7,000 RPM / 7,000,000 TPM. At ~6-8k tokens a
    call, TPM binds ~7x before RPM does — so workers are sized against TOKENS
    and the request cap is irrelevant.
  * Deterministic parse is 0.20s for a 388-page report; the CPU cost is PyMuPDF
    turning the PDF into text, which is why that stage is processes (the GIL
    makes threads pointless there) and everything else is threads.

STORAGE — everything lands in Azure, nothing of record stays on this laptop.
  * PDFs and extracted text  -> Blob (pivotmarketdata/filings)
  * documents + facts        -> Postgres (Azure, Central India), schema `filings`
  * The local PDF is DELETED once its text is safely in Blob. 3,000 reports of
    PDF is ~30GB; the text is ~5GB. Keeping both on a laptop is how a run dies
    at 80%.

RESUME IS NOT OPTIONAL. Every document is keyed by sha256 and carries a state
(discovered → fetched → extracted → done / failed). A restart re-queues only
what is unfinished, so a crash costs minutes rather than the whole run.

    pivot/.venv/bin/python pivotted/filing_pipeline.py --init
    pivot/.venv/bin/python pivotted/filing_pipeline.py --discover --limit 50
    pivot/.venv/bin/python pivotted/filing_pipeline.py --run
    pivot/.venv/bin/python pivotted/filing_pipeline.py --status
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import queue
import re
import ssl
import sys
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import psycopg2                       # noqa: E402
import psycopg2.extras                # noqa: E402

import fetch_filings_sample as FF     # noqa: E402  — reuse the measured fetchers
import filing_llm as L                # noqa: E402
from filing_extract import parse      # noqa: E402

# ────────────────────────────────────────────────────────────── config

BLOB_ACCOUNT = "https://pivotmarketdata.blob.core.windows.net"
BLOB_CONTAINER = "filings"

FETCH_WORKERS = int(os.environ.get("PIPE_FETCH_WORKERS", "12"))
EXTRACT_WORKERS = int(os.environ.get("PIPE_EXTRACT_WORKERS", str(max(2, (os.cpu_count() or 4) - 1))))
LLM_WORKERS = int(os.environ.get("PIPE_LLM_WORKERS", "48"))
Q_DEPTH = int(os.environ.get("PIPE_QUEUE_DEPTH", "64"))

# Recon measured these; one shared pool lets the slower host throttle the faster.
NSE_CONC = int(os.environ.get("PIPE_NSE_CONC", "6"))
BSE_CONC = int(os.environ.get("PIPE_BSE_CONC", "4"))

WORK = Path(os.environ.get("PIPE_WORKDIR", "/tmp/pivot_filings"))
KEEP_LOCAL_PDF = os.environ.get("PIPE_KEEP_PDF") == "1"

_dsn = os.environ.get("FILINGS_DSN") or os.environ.get("FINANCIALS_DSN")


def dsn() -> str:
    global _dsn
    if not _dsn:
        env = (HERE.parent / "pivot" / ".env").read_text(encoding="utf-8")
        m = re.search(r"^FINANCIALS_DSN=(.*)$", env, re.M)
        if not m:
            raise SystemExit("no FINANCIALS_DSN in pivot/.env")
        _dsn = m.group(1).strip()
    return _dsn


# THE REAL CEILING IS POSTGRES, NOT AZURE.
#
# Measured on the server: max_connections=50, superuser_reserved=10, and 22
# already held by the product — about 18 usable. Meanwhile 42 model workers use
# 10.5% of luna's 7M TPM, and quota would not bind until roughly 400 workers.
# So a second LLM deployment buys nothing; the database is ~20x tighter than
# the model quota and is what actually decides how wide this can run.
#
# Worse, `with psycopg2.connect(...) as c` COMMITS the transaction but does NOT
# close the connection — a very old psycopg2 trap. Every call site here leaked
# one. Ten documents survived on garbage collection; 150 workers would exhaust
# all 50 connections in seconds and fail documents that were fine.
#
# So: a small fixed pool, deliberately far below the limit, shared by every
# stage. Workers wait microseconds for a connection instead of opening one.
_POOL_SIZE = int(os.environ.get("PIPE_PG_POOL", "8"))
_pool = None
_pool_lock = threading.Lock()
# psycopg2's ThreadedConnectionPool RAISES "connection pool exhausted" the
# moment demand exceeds the pool — it does not wait. Under 150 workers that
# turns a queueing problem into failed documents. The semaphore supplies the
# blocking the pool refuses to: callers wait for a slot, so getconn() is only
# ever called when one is free.
_slots = None


def _get_pool():
    global _pool, _slots
    with _pool_lock:
        if _pool is None:
            from psycopg2.pool import ThreadedConnectionPool
            _pool = ThreadedConnectionPool(1, _POOL_SIZE, dsn())
            _slots = threading.Semaphore(_POOL_SIZE)
        return _pool


class connect:
    """Pooled connection + transaction. Returns the connection to the pool."""

    def __enter__(self):
        pool = _get_pool()
        _slots.acquire()
        try:
            self._c = pool.getconn()
        except Exception:
            _slots.release()
            raise
        return self._c

    def __exit__(self, et, ev, tb):
        try:
            if et is None:
                self._c.commit()
            else:
                self._c.rollback()
        finally:
            _get_pool().putconn(self._c)
            _slots.release()
        return False


# ────────────────────────────────────────────────────────────── schema

DDL = """
CREATE SCHEMA IF NOT EXISTS filings;

-- One row per PDF, keyed by content hash. The hash is the resume key AND the
-- dedupe key: the same annual report is often linked from both exchanges.
CREATE TABLE IF NOT EXISTS filings.documents (
    sha256      TEXT PRIMARY KEY,
    symbol      TEXT NOT NULL,
    exchange    TEXT NOT NULL,
    doc_kind    TEXT NOT NULL,
    title       TEXT,
    period      TEXT,
    filed_at    TIMESTAMPTZ,
    url         TEXT NOT NULL,
    bytes       BIGINT,
    pages       INTEGER,
    blob_pdf    TEXT,
    blob_text   TEXT,
    state       TEXT NOT NULL DEFAULT 'discovered',
    error       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS documents_state ON filings.documents(state);
CREATE INDEX IF NOT EXISTS documents_symbol ON filings.documents(symbol);

-- A URL we have seen but not yet hashed. Discovery is cheap and fetching is
-- not, so the two are separated and the queue survives a restart.
CREATE TABLE IF NOT EXISTS filings.queue (
    url         TEXT PRIMARY KEY,
    symbol      TEXT NOT NULL,
    exchange    TEXT NOT NULL,
    doc_kind    TEXT NOT NULL,
    title       TEXT,
    period      TEXT,
    filed_at    TIMESTAMPTZ,
    state       TEXT NOT NULL DEFAULT 'pending',
    error       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS queue_state ON filings.queue(state);

-- Every column the model stage produces, plus the three verdict flags. The
-- flags SHIP: unit_agrees carries a real 13%-of-currency-facts disagreement
-- between two independent readings, and resolving it silently either way would
-- be inventing certainty we do not have.
CREATE TABLE IF NOT EXISTS filings.facts (
    id           BIGSERIAL PRIMARY KEY,
    doc_sha      TEXT NOT NULL REFERENCES filings.documents(sha256) ON DELETE CASCADE,
    symbol       TEXT NOT NULL,
    task         TEXT NOT NULL,
    grp          TEXT,
    label        TEXT,
    kind         TEXT,
    rollup       BOOLEAN,
    value_text   TEXT,
    unit_text    TEXT,
    period       TEXT,
    basis        TEXT,
    note         TEXT,
    quote        TEXT,
    status       TEXT,
    value_raw    DOUBLE PRECISION,
    unit         TEXT,
    value_crore  DOUBLE PRECISION,
    page         INTEGER,
    grounding    TEXT,
    unit_source  TEXT,
    unit_agrees  TEXT,
    period_ambiguous BOOLEAN DEFAULT FALSE,
    partial_table    BOOLEAN DEFAULT FALSE,
    model        TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS facts_doc ON filings.facts(doc_sha);
CREATE INDEX IF NOT EXISTS facts_symbol_task ON filings.facts(symbol, task);
"""


def init_db() -> None:
    with connect() as c, c.cursor() as cur:
        cur.execute(DDL)
    print("schema filings.* ready on", urllib.parse.urlparse(dsn()).hostname)


# ────────────────────────────────────────────────────────────── blob

_blob_lock = threading.Lock()
_blob_client = None


def blob():
    """One container client, lazily built, shared across threads.

    Auth is the signed-in Azure identity (DefaultAzureCredential), not an
    account key. The key is a long-lived secret that would have to live on disk
    and in every worker; the identity is already present and expires on its own.
    """
    global _blob_client
    with _blob_lock:
        if _blob_client is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient
            svc = BlobServiceClient(BLOB_ACCOUNT, credential=DefaultAzureCredential())
            _blob_client = svc.get_container_client(BLOB_CONTAINER)
        return _blob_client


def blob_put(name: str, data: bytes, content_type: str) -> str:
    from azure.storage.blob import ContentSettings
    blob().upload_blob(name, data, overwrite=True,
                       content_settings=ContentSettings(content_type=content_type))
    return name


# ────────────────────────────────────────────────────────────── discover

def discover(symbols_nse: list[str], bse: list[tuple[str, str]], per_symbol: int) -> int:
    """Write candidate URLs to filings.queue. Cheap, restartable, no downloads."""
    rows: list[tuple] = []
    op_bse = FF.bse_op() if bse else None

    def _ts(v):
        """BSE prints '-' where it has no date, and Postgres refuses it.

        The whole discovery aborted on one such row, mid-insert — five sample
        companies had never produced one. A missing date is NULL, not an error,
        and it must never be able to cost 4,500 other companies their place in
        the queue.
        """
        if not v or not isinstance(v, str):
            return v or None
        v = v.strip()
        return v if re.search(r"\d{4}", v) else None

    def add(sym, ex, kind, items):
        for it in items:
            u = FF.clean_url(it.get("url"))
            if u:
                rows.append((u, sym, ex, kind, (it.get("title") or "")[:400],
                             (it.get("period") or None), _ts(it.get("filed_at"))))

    lock = threading.Lock()

    def collect(sym, ex_name, items):
        with lock:
            add(sym, ex_name, "annual_report", items)

    with ThreadPoolExecutor(max_workers=NSE_CONC) as ex:
        for sym, items in zip(symbols_nse,
                              ex.map(lambda s: _safe(FF.nse_annual, s, per_symbol),
                                     symbols_nse)):
            collect(sym, "NSE", items)
    # BSE was a serial loop, which is fine for five companies and hopeless for
    # 2,230 — the sample never exposed it because the sample had five.
    if bse:
        with ThreadPoolExecutor(max_workers=BSE_CONC) as ex:
            for (sym, _code), items in zip(
                    bse, ex.map(lambda sc: _safe(FF.bse_annual, sc[0], sc[1],
                                                 per_symbol, op_bse), bse)):
                collect(sym, "BSE", items)

    if not rows:
        return 0
    # Chunked: a single execute_values of ~10,000 rows is one enormous
    # statement over an RTT-bound link, and a failure loses all of it.
    n = 0
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        try:
            with connect() as c, c.cursor() as cur:
                psycopg2.extras.execute_values(cur, """
                    INSERT INTO filings.queue
                        (url,symbol,exchange,doc_kind,title,period,filed_at)
                    VALUES %s ON CONFLICT (url) DO NOTHING""", chunk)
                n += cur.rowcount
        except Exception as exc:  # noqa: BLE001
            # One malformed row must cost its own chunk at most. Retry the
            # chunk row-by-row so the other 499 still land, and name the ones
            # that do not rather than losing them silently.
            print(f"  chunk {i} failed ({type(exc).__name__}) — retrying singly")
            for r in chunk:
                try:
                    with connect() as c, c.cursor() as cur:
                        cur.execute("INSERT INTO filings.queue (url,symbol,"
                                    "exchange,doc_kind,title,period,filed_at) "
                                    "VALUES (%s,%s,%s,%s,%s,%s,%s) "
                                    "ON CONFLICT (url) DO NOTHING", r)
                        n += cur.rowcount
                except Exception as e2:  # noqa: BLE001
                    print(f"    dropped {r[1]} {r[0][:70]}: {e2}")
    return n


def universe(include_sme: bool = False) -> tuple[list[str], list[tuple[str, str]]]:
    """The listed universe, from company_identity — NOT from mc.companies.

    mc.companies is not an identity source: its names are truncated to 15
    characters, its sc_id collides across companies, and its nse_symbol column
    holds BSE codes for some rows. company_identity is the reconciled table and
    is what every other pivotted job keys on.

    SME is off by default. 516 of the 5,019 are NSE_SME, which file far less and
    would spend fetch budget for very little.
    """
    import re as _re
    import pathlib
    env = (HERE.parent / "pivot" / ".env").read_text(encoding="utf-8")
    m = _re.search(r"^DATABASE_URL=(.*)$", env, _re.M)
    conn = psycopg2.connect(m.group(1).strip())
    try:
        with conn.cursor() as cur:
            ex = ("('NSE','NSE_SME')" if include_sme else "('NSE')")
            cur.execute(f"""SELECT DISTINCT verified_symbol FROM company_identity
                            WHERE verified_exchange IN {ex}
                              AND verified_symbol IS NOT NULL
                            ORDER BY 1""")
            nse = [r[0] for r in cur.fetchall()]
            cur.execute("""SELECT DISTINCT verified_symbol, verified_bse_code
                           FROM company_identity
                           WHERE verified_exchange='BSE'
                             AND verified_bse_code IS NOT NULL
                           ORDER BY 1""")
            bse = [(s, str(c)) for s, c in cur.fetchall()]
    finally:
        conn.close()
    return nse, bse


def _safe(fn, *a):
    try:
        return fn(*a) or []
    except Exception as exc:  # noqa: BLE001 — one dead symbol must not stop discovery
        print(f"  discover failed {a[:2]}: {type(exc).__name__}: {exc}")
        return []


# ────────────────────────────────────────────────────────────── stages

class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.d: dict[str, int] = {}

    def bump(self, k: str, n: int = 1):
        with self.lock:
            self.d[k] = self.d.get(k, 0) + n

    def snap(self) -> dict:
        with self.lock:
            return dict(self.d)


STATS = Stats()
_HOST_SEM = {"NSE": threading.Semaphore(NSE_CONC), "BSE": threading.Semaphore(BSE_CONC)}


def stage_fetch(item: dict) -> dict | None:
    """Download the PDF, hash it, push it to Blob. Never keeps it locally."""
    url, ex = item["url"], item["exchange"]
    op = FF.opener("https://www.nseindia.com/" if ex == "NSE"
                   else "https://www.bseindia.com/")
    with _HOST_SEM[ex]:
        try:
            data = FF.download(op, url)
        except Exception as exc:  # noqa: BLE001
            _queue_state(url, "failed", f"{type(exc).__name__}: {exc}")
            STATS.bump("fetch_failed")
            return None
    if not data or len(data) < 5000 or not data[:5].startswith(b"%PDF"):
        _queue_state(url, "failed", f"not a pdf ({len(data or b'')} bytes)")
        STATS.bump("fetch_notpdf")
        return None

    sha = hashlib.sha256(data).hexdigest()
    name = f"pdf/{item['symbol']}/{sha}.pdf"
    try:
        blob_put(name, data, "application/pdf")
    except Exception as exc:  # noqa: BLE001
        _queue_state(url, "failed", f"blob: {type(exc).__name__}: {exc}")
        STATS.bump("blob_failed")
        return None

    with connect() as c, c.cursor() as cur:
        cur.execute("""INSERT INTO filings.documents
              (sha256,symbol,exchange,doc_kind,title,period,filed_at,url,bytes,
               blob_pdf,state)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'fetched')
              ON CONFLICT (sha256) DO NOTHING""",
                    (sha, item["symbol"], ex, item["doc_kind"], item.get("title"),
                     item.get("period"), item.get("filed_at"), url, len(data), name))
        fresh = cur.rowcount == 1
        cur.execute("UPDATE filings.queue SET state='done',updated_at=now() "
                    "WHERE url=%s", (url,))
    STATS.bump("fetched")
    if not fresh:
        # Same report linked from both exchanges — already stored, already
        # extracted. Re-running the model on it would double the bill.
        STATS.bump("duplicate_pdf")
        return None
    return {"sha256": sha, "symbol": item["symbol"], "pdf": data}


def stage_extract(job: dict) -> dict | None:
    """PDF -> text -> Blob. The heavy CPU step; text is what everything reads."""
    dest = WORK / f"{job['sha256']}.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        # extract_text returns (dest, page_count, chars) — NOT a page count.
        # Taking the tuple whole put it into an INTEGER column, and the
        # resulting error surfaced through f.result() in the extract loop,
        # killed that thread, and left the model queue waiting on a sentinel
        # that could never arrive. The pipeline hung instead of failing.
        written, pages, _chars = FF.extract_text(job["pdf"], dest)
    except Exception as exc:  # noqa: BLE001
        _doc_state(job["sha256"], "failed", f"extract: {type(exc).__name__}: {exc}")
        STATS.bump("extract_failed")
        return None
    if not written or not pages:
        _doc_state(job["sha256"], "failed", "no text layer (0 pages)")
        STATS.bump("extract_empty")
        return None
    text = dest.read_bytes()
    name = f"text/{job['symbol']}/{job['sha256']}.txt"
    try:
        blob_put(name, text, "text/plain; charset=utf-8")
    except Exception as exc:  # noqa: BLE001
        _doc_state(job["sha256"], "failed", f"blob text: {exc}")
        return None
    with connect() as c, c.cursor() as cur:
        cur.execute("UPDATE filings.documents SET pages=%s,blob_text=%s,"
                    "state='extracted',updated_at=now() WHERE sha256=%s",
                    (pages, name, job["sha256"]))
    STATS.bump("extracted")
    STATS.bump("pages", pages)
    return {"sha256": job["sha256"], "symbol": job["symbol"], "text_path": dest}


def stage_model(job: dict) -> None:
    """The seven model tasks for one document, then the facts into Postgres."""
    d = parse(job["text_path"])
    d.symbol = job["symbol"]
    if not d.pages:
        _doc_state(job["sha256"], "failed", "parsed to 0 pages")
        return
    # The seven tasks fan out rather than queue. Sequentially they made one
    # document 7 x 22s = ~156s of wall time, which pinned a parsed report in
    # memory for the whole of it; total API concurrency then equalled the
    # number of documents resident. filing_llm's global semaphore is what
    # actually bounds calls against the deployment's TPM, so tasks are free to
    # run together and a document now clears in roughly the time of one call.
    facts: list[L.LLMFact] = []
    with ThreadPoolExecutor(len(L.TASKS)) as tex:
        for got, err in tex.map(lambda t: L.run_task(d, t), L.TASKS):
            if err:
                STATS.bump("llm_task_error")
            facts += got
    kept = [f for f in facts if not f.drop_reason]
    kept, _ = L.dedupe(kept)
    L.check_sums(kept)           # sets period_ambiguous / partial_table flags
    # One round trip, not two. Azure PG is in Central India and every operation
    # is RTT-bound — the pool stress test measured ~280ms per trivial query
    # under contention, so a round trip saved per document is real time saved
    # across 3,000 of them. Doing both in one transaction also means facts and
    # the 'done' flag commit together: a crash between them would otherwise
    # leave a document marked complete with no facts, which resume cannot see.
    _store_facts(job["sha256"], kept, mark_done=True)
    STATS.bump("modelled")
    STATS.bump("facts", len(kept))
    STATS.bump("dropped", len(facts) - len(kept))
    if not KEEP_LOCAL_PDF:
        job["text_path"].unlink(missing_ok=True)


COLS = ("doc_sha symbol task grp label kind rollup value_text unit_text period "
        "basis note quote status value_raw unit value_crore page grounding "
        "unit_source unit_agrees period_ambiguous partial_table model").split()


def _store_facts(sha: str, facts: list, mark_done: bool = False) -> None:
    rows = []
    for f in facts:
        a = asdict(f)
        rows.append((sha, a["symbol"], a["task"], a["group"], a["label"], a["kind"],
                     a["rollup"], a["value_text"], a["unit_text"], a["period"],
                     a["basis"], a["note"], a["quote"], a["status"], a["value_raw"],
                     a["unit"], a["value_crore"], a["page"], a["grounding"],
                     a["unit_source"], a["unit_agrees"], a["period_ambiguous"],
                     a["partial_table"], L.LLM_MODEL))
    with connect() as c, c.cursor() as cur:
        if rows:
            psycopg2.extras.execute_values(
                cur, f"INSERT INTO filings.facts ({','.join(COLS)}) VALUES %s", rows)
        if mark_done:
            # A document that yielded no facts is still DONE — leaving it
            # unmarked would make resume re-run it every restart for ever.
            cur.execute("UPDATE filings.documents SET state='done',"
                        "updated_at=now() WHERE sha256=%s", (sha,))


CLAIM_BATCH = int(os.environ.get("PIPE_CLAIM_BATCH", "40"))

# Wave 2 runs the model over text ALREADY on Blob — no re-fetch, no re-extract.
# Only the tasks named here are run, so the first seven are not paid for twice.
TASK_FILTER = [t for t in (os.environ.get("PIPE_TASKS") or "").split(",") if t]

REMODEL_DDL = """
CREATE TABLE IF NOT EXISTS filings.remodel (
    doc_sha   TEXT PRIMARY KEY REFERENCES filings.documents(sha256) ON DELETE CASCADE,
    symbol    TEXT NOT NULL,
    blob_text TEXT NOT NULL,
    state     TEXT NOT NULL DEFAULT 'pending',
    error     TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS remodel_state ON filings.remodel(state);
"""


def remodel_seed() -> int:
    """Queue every completed document for the second task wave."""
    with connect() as c, c.cursor() as cur:
        cur.execute(REMODEL_DDL)
        cur.execute("""INSERT INTO filings.remodel (doc_sha,symbol,blob_text)
                       SELECT sha256,symbol,blob_text FROM filings.documents
                       WHERE state='done' AND blob_text IS NOT NULL
                       ON CONFLICT (doc_sha) DO NOTHING""")
        n = cur.rowcount
        cur.execute("""UPDATE filings.remodel SET state='pending'
                       WHERE state='claimed'
                         AND updated_at < now() - interval '30 minutes'""")
    return n


def remodel_claim(n: int) -> list[tuple]:
    with connect() as c, c.cursor() as cur:
        cur.execute("""
            UPDATE filings.remodel r SET state='claimed', updated_at=now()
            WHERE r.doc_sha IN (
                SELECT doc_sha FROM filings.remodel WHERE state='pending'
                FOR UPDATE SKIP LOCKED LIMIT %s)
            RETURNING r.doc_sha, r.symbol, r.blob_text""", (n,))
        return cur.fetchall()


def stage_remodel(row: tuple) -> None:
    """One document, wave-2 tasks only, text pulled straight from Blob."""
    sha, sym, blob_text = row
    dest = WORK / f"rm_{sha}.txt"
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        dest.write_bytes(blob().download_blob(blob_text).readall())
        d = parse(dest)
        d.symbol = sym
        if not d.pages:
            raise RuntimeError("parsed to 0 pages")
        tasks = [t for t in L.TASKS if not TASK_FILTER or t.name in TASK_FILTER]
        facts: list[L.LLMFact] = []
        with ThreadPoolExecutor(len(tasks)) as tex:
            for got, err in tex.map(lambda t: L.run_task(d, t), tasks):
                if err:
                    STATS.bump("llm_task_error")
                facts += got
        kept = [f for f in facts if not f.drop_reason]
        kept, _ = L.dedupe(kept)
        L.check_sums(kept)
        _store_facts(sha, kept)
        with connect() as c, c.cursor() as cur:
            cur.execute("UPDATE filings.remodel SET state='done',updated_at=now() "
                        "WHERE doc_sha=%s", (sha,))
        STATS.bump("modelled")
        STATS.bump("facts", len(kept))
        STATS.bump("dropped", len(facts) - len(kept))
    except Exception as exc:  # noqa: BLE001
        with connect() as c, c.cursor() as cur:
            cur.execute("UPDATE filings.remodel SET state='failed',error=%s,"
                        "updated_at=now() WHERE doc_sha=%s",
                        (f"{type(exc).__name__}: {exc}"[:500], sha))
        STATS.bump("remodel_failed")
    finally:
        dest.unlink(missing_ok=True)


def run_remodel() -> int:
    """Model-only pass. One stage, so every worker stays on the model the
    whole time — there is no fetch or extract phase to idle behind."""
    WORK.mkdir(parents=True, exist_ok=True)
    with connect() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM filings.remodel WHERE state='pending'")
        n0 = cur.fetchone()[0]
    tasks = [t.name for t in L.TASKS if not TASK_FILTER or t.name in TASK_FILTER]
    print(f"remodel  pending={n0}  tasks={tasks}")
    print(f"  docs-in-flight={LLM_WORKERS}  api-calls="
          f"{os.environ.get('FILING_LLM_CONCURRENCY','48')}  pg-pool={_POOL_SIZE}")
    if not n0:
        print("  nothing to do — run --remodel-seed first")
        return 0
    t0 = time.time()
    with ThreadPoolExecutor(LLM_WORKERS) as ex:
        futs: list = []
        while True:
            batch = remodel_claim(CLAIM_BATCH)
            if not batch:
                break
            futs += [ex.submit(stage_remodel, r) for r in batch]
            # Keep the pool fed but never let the future list grow without
            # bound; drain finished work before claiming the next batch.
            while len(futs) >= LLM_WORKERS * 2:
                futs = [f for f in futs if not f.done()]
                time.sleep(0.3)
            s = STATS.snap()
            print(f"  [{(time.time()-t0)/60:5.1f}m] modelled={s.get('modelled',0)} "
                  f"facts={s.get('facts',0)} inflight={len(futs)}", flush=True)
        for f in futs:
            f.result()
    el = time.time() - t0
    s = STATS.snap()
    print(f"\n=== REMODEL DONE in {el/60:.1f} min ===")
    for k in sorted(s):
        print(f"  {k:18s} {s[k]:,}")
    return 0


def claim(n: int) -> list[dict]:
    """Take n pending rows for THIS process, atomically.

    The VM has 32 cores and one Python process was using 1.4 of them — 218
    threads sharing one GIL, 96% of the machine idle. The fix is more
    PROCESSES, and the moment there is more than one they must not fetch the
    same documents: that would double the downloads and double the Azure bill
    for nothing.

    FOR UPDATE SKIP LOCKED is the Postgres work-queue primitive for exactly
    this — each worker locks a disjoint set and never waits on another's rows.
    """
    with connect() as c, c.cursor() as cur:
        cur.execute("""
            UPDATE filings.queue q SET state='claimed', updated_at=now()
            WHERE q.url IN (
                SELECT url FROM filings.queue WHERE state='pending'
                ORDER BY symbol FOR UPDATE SKIP LOCKED LIMIT %s)
            RETURNING q.url,q.symbol,q.exchange,q.doc_kind,q.title,q.period,
                      q.filed_at""", (n,))
        return [dict(zip(("url", "symbol", "exchange", "doc_kind", "title",
                          "period", "filed_at"), r)) for r in cur.fetchall()]


def _queue_state(url: str, state: str, err: str | None = None) -> None:
    with connect() as c, c.cursor() as cur:
        cur.execute("UPDATE filings.queue SET state=%s,error=%s,updated_at=now() "
                    "WHERE url=%s", (state, (err or "")[:500], url))


def _doc_state(sha: str, state: str, err: str | None = None) -> None:
    with connect() as c, c.cursor() as cur:
        cur.execute("UPDATE filings.documents SET state=%s,error=%s,updated_at=now() "
                    "WHERE sha256=%s", (state, (err or "")[:500], sha))


# ────────────────────────────────────────────────────────────── the pipeline

def run(limit: int | None) -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    q_extract: queue.Queue = queue.Queue(maxsize=Q_DEPTH)
    q_model: queue.Queue = queue.Queue(maxsize=Q_DEPTH)
    DONE = object()

    # Reclaim anything a dead process was holding. A worker that dies mid-batch
    # leaves its rows 'claimed' for ever otherwise, and they are invisible to
    # every other worker — the queue would slowly leak itself empty.
    with connect() as c, c.cursor() as cur:
        cur.execute("""UPDATE filings.queue SET state='pending'
                       WHERE state='claimed'
                         AND updated_at < now() - interval '30 minutes'""")
        if cur.rowcount:
            print(f"  reclaimed {cur.rowcount} rows from a dead worker")
        # Documents from a previous run that died partway. Resume must cover
        # EVERY intermediate state, not just the last one — the first version
        # resumed only 'extracted' and silently did nothing for ten documents
        # sitting in 'fetched', which reads as "no work to do" rather than as a
        # gap. The PDF is already in Blob, so re-fetching it is free.
        cur.execute("SELECT sha256,symbol,blob_text FROM filings.documents "
                    "WHERE state='extracted'")
        resume = cur.fetchall()
        cur.execute("SELECT sha256,symbol,blob_pdf FROM filings.documents "
                    "WHERE state='fetched' AND blob_pdf IS NOT NULL")
        refetch = cur.fetchall()

    with connect() as c, c.cursor() as cur:
        cur.execute("SELECT count(*) FROM filings.queue WHERE state='pending'")
        n_pending = cur.fetchone()[0]
    print(f"pipeline  pending_urls={n_pending}  resume_extracted={len(resume)}"
          f"  resume_fetched={len(refetch)}")
    print(f"  fetch={FETCH_WORKERS}t (NSE {NSE_CONC}/BSE {BSE_CONC})  "
          f"extract={EXTRACT_WORKERS}t  docs-in-flight={LLM_WORKERS}  "
          f"api-calls={os.environ.get('FILING_LLM_CONCURRENCY','48')}  "
          f"pg-pool={_POOL_SIZE}  queues={Q_DEPTH}  "
          f"{L.LLM_MODEL}@{L.LLM_EFFORT}")
    if not n_pending and not resume and not refetch:
        print("  nothing to do — run --discover first")
        return 0

    t0 = time.time()
    stop = threading.Event()

    # A stage thread that dies takes the whole run with it: the next queue never
    # receives its sentinel and every downstream worker blocks for ever, which
    # looks exactly like "still working". So the sentinel is put in a `finally`
    # and no future's exception is ever allowed to escape a loop.
    def _harvest(futs, sink):
        for f in [x for x in futs if x.done()]:
            futs.remove(f)
            try:
                r = f.result()
            except Exception as exc:  # noqa: BLE001
                STATS.bump("stage_exception")
                print(f"  !! {type(exc).__name__}: {exc}", flush=True)
                continue
            if r is not None and sink is not None:
                sink.put(r)

    def _from_blob(row):
        sha, sym, name = row
        try:
            return {"sha256": sha, "symbol": sym,
                    "pdf": blob().download_blob(name).readall()}
        except Exception as exc:  # noqa: BLE001
            _doc_state(sha, "failed", f"blob re-read: {exc}")
            return None

    def fetch_loop():
        try:
            with ThreadPoolExecutor(FETCH_WORKERS) as ex:
                for job in ex.map(_from_blob, refetch):
                    if job:
                        q_extract.put(job)
                got = 0
                while True:
                    # Claim as we go, not all up front. Claiming the whole
                    # queue would hand one process every row and leave the
                    # other processes with nothing to do.
                    batch = claim(min(CLAIM_BATCH, (limit - got) if limit else CLAIM_BATCH))
                    if not batch:
                        break
                    got += len(batch)
                    for job in ex.map(stage_fetch, batch):
                        if job:
                            q_extract.put(job)   # blocks when full = backpressure
                    if limit and got >= limit:
                        break
        finally:
            q_extract.put(DONE)

    def extract_loop():
        try:
            with ThreadPoolExecutor(EXTRACT_WORKERS) as ex:
                futs = []
                while True:
                    job = q_extract.get()
                    if job is DONE:
                        break
                    futs.append(ex.submit(stage_extract, job))
                    _harvest(futs, q_model)   # drain eagerly so memory stays flat
                while futs:
                    _harvest(futs, q_model)
                    time.sleep(0.2)
        finally:
            q_model.put(DONE)

    def model_loop():
        with ThreadPoolExecutor(LLM_WORKERS) as ex:
            futs = []
            for sha, sym, blob_text in resume:
                # The local text is deleted once it is safely in Blob, so a
                # resume that only looked on disk would find nothing and skip
                # every document silently — the same failure as the 'fetched'
                # resume. Pull it back from Blob instead.
                p = WORK / f"{sha}.txt"
                if not p.exists() and blob_text:
                    try:
                        p.write_bytes(blob().download_blob(blob_text).readall())
                    except Exception as exc:  # noqa: BLE001
                        _doc_state(sha, "failed", f"blob text re-read: {exc}")
                        continue
                if p.exists():
                    futs.append(ex.submit(stage_model,
                                          {"sha256": sha, "symbol": sym, "text_path": p}))
            while True:
                job = q_model.get()
                if job is DONE:
                    break
                futs.append(ex.submit(stage_model, job))
                _harvest(futs, None)
            while futs:
                _harvest(futs, None)
                time.sleep(0.2)

    threads = [threading.Thread(target=fn, name=n, daemon=True) for fn, n in
               ((fetch_loop, "fetch"), (extract_loop, "extract"), (model_loop, "model"))]
    for t in threads:
        t.start()

    # The monitor is part of the pipeline, not an afterthought. A stage that
    # silently stops looks identical to a stage that is merely slow unless
    # something prints the queue depths.
    def monitor():
        last = {}
        while not stop.is_set():
            time.sleep(20)
            s = STATS.snap()
            el = time.time() - t0
            rate = s.get("modelled", 0) / max(el / 60, 0.01)
            delta = {k: s[k] - last.get(k, 0) for k in s}
            last = dict(s)
            print(f"  [{el/60:5.1f}m] q_ext={q_extract.qsize():>3} "
                  f"q_mod={q_model.qsize():>3} | fetched={s.get('fetched',0)} "
                  f"extracted={s.get('extracted',0)} modelled={s.get('modelled',0)} "
                  f"facts={s.get('facts',0)} | {rate:.1f} docs/min | "
                  f"+{delta.get('modelled',0)} since last",
                  flush=True)
            if delta.get("fetched", 0) == 0 and delta.get("modelled", 0) == 0 \
                    and q_extract.qsize() == 0 and q_model.qsize() == 0:
                print("      (no movement in 20s — stages idle or blocked)", flush=True)

    mon = threading.Thread(target=monitor, daemon=True)
    mon.start()
    for t in threads:
        t.join()
    stop.set()

    el = time.time() - t0
    s = STATS.snap()
    print(f"\n=== PIPELINE DONE in {el/60:.1f} min ===")
    for k in sorted(s):
        print(f"  {k:18s} {s[k]:,}")
    if s.get("modelled"):
        print(f"  {el/s['modelled']:.1f}s per document end-to-end")
    return 0


def status() -> int:
    with connect() as c, c.cursor() as cur:
        for tbl, col in (("filings.queue", "state"), ("filings.documents", "state")):
            cur.execute(f"SELECT {col},count(*) FROM {tbl} GROUP BY 1 ORDER BY 2 DESC")
            print(f"  {tbl}: {dict(cur.fetchall()) or '(empty)'}")
        cur.execute("SELECT count(*),count(DISTINCT doc_sha),count(DISTINCT symbol) "
                    "FROM filings.facts")
        n, docs, syms = cur.fetchone()
        print(f"  filings.facts: {n:,} facts over {docs:,} documents / {syms:,} companies")
        if n:
            cur.execute("SELECT task,count(*) FROM filings.facts GROUP BY 1 ORDER BY 2 DESC")
            print(f"    by task: {dict(cur.fetchall())}")
            cur.execute("SELECT count(*) FROM filings.facts "
                        "WHERE unit_agrees LIKE 'DISAGREE%%'")
            print(f"    unit disagreements needing review: {cur.fetchone()[0]:,}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--per-symbol", type=int, default=2)
    ap.add_argument("--symbols", help="comma-separated NSE symbols (default: sample)")
    ap.add_argument("--remodel-seed", action="store_true",
                    help="queue every done document for the wave-2 tasks")
    ap.add_argument("--remodel", action="store_true",
                    help="run wave-2 tasks over text already on Blob")
    ap.add_argument("--universe", action="store_true",
                    help="every listed company from company_identity")
    ap.add_argument("--sme", action="store_true", help="include NSE_SME")
    a = ap.parse_args()
    if a.init:
        init_db()
    if a.discover:
        if a.universe:
            nse, bse = universe(a.sme)
            print(f"universe: {len(nse)} NSE + {len(bse)} BSE"
                  f"{' (incl SME)' if a.sme else ''}")
        else:
            nse, bse = (a.symbols.split(",") if a.symbols else FF.NSE_SAMPLE), []
        t = time.time()
        n = discover(nse, bse, a.per_symbol)
        print(f"discovered {n} new urls from {len(nse)+len(bse)} companies "
              f"in {time.time()-t:.0f}s")
    if a.remodel_seed:
        print(f"queued {remodel_seed()} documents for remodel")
    if a.remodel:
        raise SystemExit(run_remodel())
    if a.run:
        raise SystemExit(run(a.limit))
    if a.status:
        raise SystemExit(status())
    if not any((a.init, a.discover, a.run, a.status)):
        ap.print_help()
