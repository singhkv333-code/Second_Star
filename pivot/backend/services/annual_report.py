"""Read a company's annual report: the pages that answer a question.

The filing pipeline (pivotted/filing_pipeline.py) stored every annual report's
extracted text on Blob, one `===== [PAGE n] =====` marker per page, and its
row in `filings.documents`. A report runs to ~350 pages (~230k tokens), too
much to hand the model whole, so this returns the pages that best match the
model's own question, or the exact pages it asks for. The model reads them and
cites the page; nothing here interprets the text.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import unicodedata
from collections import Counter, OrderedDict
from typing import Any, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

BLOB_ACCOUNT = "https://pivotmarketdata.blob.core.windows.net"
BLOB_CONTAINER = "filings"
_PAGE_RE = re.compile(r"=====\s*\[PAGE (\d+)\]\s*=====")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9.%&-]*")
_STOP = frozenset(
    "the of and to in for on a an is are was were by with as at from that this "
    "its it be or has have which what how did does company year years".split())

_LIGATURES = str.maketrans({"\u019f": "ti", "\u01a0": "ti", "\u0283": "ti"})
PAGE_CHARS = 3500      # one page, as the model sees it
TOTAL_CHARS = 16000    # all pages in one result

_lock = threading.Lock()
_container = None
_docs: "OrderedDict[str, dict[int, str]]" = OrderedDict()   # sha -> {page: text}
_DOCS_KEPT = 6


def _blob():
    global _container
    with _lock:
        if _container is None:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient
            _container = BlobServiceClient(
                BLOB_ACCOUNT, credential=DefaultAzureCredential()
            ).get_container_client(BLOB_CONTAINER)
        return _container


def _pages(sha: str, blob_path: str) -> dict[int, str]:
    with _lock:
        if sha in _docs:
            _docs.move_to_end(sha)
            return _docs[sha]
    raw = _blob().download_blob(blob_path).readall().decode("utf-8", "replace")
    # PDF fonts set "ti", "fi" and friends as single glyphs ("construcƟon");
    # unfolded, the words read and match as words.
    raw = unicodedata.normalize("NFKC", raw).translate(_LIGATURES)
    marks = list(_PAGE_RE.finditer(raw))
    pages = {
        int(m.group(1)): raw[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(raw)].strip()
        for i, m in enumerate(marks)
    }
    with _lock:
        _docs[sha] = pages
        while len(_docs) > _DOCS_KEPT:
            _docs.popitem(last=False)
    return pages


def _terms(s: str) -> list[str]:
    return [w for w in _WORD_RE.findall(s.lower()) if w not in _STOP and len(w) > 1]


def _rank(pages: dict[int, str], query: str, k: int) -> list[int]:
    """BM25 over pages: which pages use the question's words most."""
    q = set(_terms(query))
    if not q:
        return []
    toks = {n: Counter(_terms(t)) for n, t in pages.items()}
    lens = {n: sum(c.values()) or 1 for n, c in toks.items()}
    avg = sum(lens.values()) / max(len(lens), 1)
    df = {w: sum(1 for c in toks.values() if w in c) for w in q}
    N = len(pages)
    scores = {}
    for n, c in toks.items():
        s = 0.0
        for w in q:
            f = c.get(w, 0)
            if f:
                idf = math.log(1 + (N - df[w] + 0.5) / (df[w] + 0.5))
                s += idf * f * 2.2 / (f + 1.2 * (0.25 + 0.75 * lens[n] / avg))
        if s:
            scores[n] = s
    return sorted(scores, key=scores.get, reverse=True)[:k]


def _find_doc(symbol: str, year: Optional[str]) -> Optional[dict]:
    from backend.market.financials_db import _session

    with _session() as db:
        rows = db.execute(text(
            "SELECT sha256, title, period, url, pages, blob_text FROM filings.documents "
            "WHERE symbol = :s AND state = 'done' AND blob_text IS NOT NULL "
            "ORDER BY filed_at DESC NULLS LAST"), {"s": symbol}).mappings().all()
    if not rows:
        return None
    if year:
        want = re.sub(r"\D", "", year)
        for r in rows:
            if want and want in re.sub(r"\D", "", r["period"] or ""):
                return dict(r)
    return dict(rows[0])


def available_periods(symbol: str) -> list[str]:
    from backend.market.financials_db import _session

    with _session() as db:
        return [r[0] for r in db.execute(text(
            "SELECT period FROM filings.documents WHERE symbol = :s AND state = 'done' "
            "ORDER BY filed_at DESC NULLS LAST"), {"s": symbol}).fetchall()]


def read(symbol: str, query: str = "", pages: Optional[list[int]] = None,
         year: Optional[str] = None) -> dict[str, Any]:
    symbol = symbol.strip().upper()
    doc = _find_doc(symbol, year)
    if doc is None:
        return {"error": f"no annual report on file for {symbol}"}
    text_by_page = _pages(doc["sha256"], doc["blob_text"])
    total = len(text_by_page)
    if pages:
        chosen = [p for p in dict.fromkeys(int(p) for p in pages) if p in text_by_page][:6]
        more: list[int] = []
    else:
        ranked = _rank(text_by_page, query, 14)
        chosen, more = ranked[:5], ranked[5:]
    out, used = [], 0
    for p in chosen:
        body = text_by_page[p][:PAGE_CHARS]
        if used + len(body) > TOTAL_CHARS:
            break
        used += len(body)
        out.append({"page": p, "text": body})
    return {
        "report": doc["title"], "period": doc["period"], "pages_total": total,
        "url": doc["url"],
        "pages": out,
        "other_matching_pages": more,
        "cite_as": f"([Annual report {doc['period']}, p.N]({doc['url']}#page=N))",
        "other_years": [p for p in available_periods(symbol) if p != doc["period"]],
        **({} if out else {"note": "no page matched; try other words or read pages by number"}),
    }
