"""The model stage: read what regex provably cannot, and prove every word of it.

`filing_extract.py` segments the document and resolves units deterministically.
It stops where the shape of the information stops being regular — and the
held-out run measured exactly where that is: `contingent_tax_demand` was fitted
to TCS's sentence and scored 0/20 on unseen companies, because contingent
liabilities take four structurally different shapes in ten companies. The same
is true of segment tables, geography splits, and every company-specific
operating metric (order book, capacity utilisation, USFDA site status, AUM),
which is precisely the material nobody else publishes and therefore the whole
reason for reading the PDFs at all.

So this stage hands those to a model. The danger is obvious: a model asked for
"the contingent liability" will always produce a number. KPGEL states it "has
not provided for any contingent liability" and prints an unrelated
"` 547.25 lakhs" two lines below; a confident wrong answer is worse than none.

THE CONTRACT THAT MAKES IT SAFE — the model READS, this file COMPUTES.

  * The model returns the number VERBATIM as printed (`value_text`), the unit
    phrase VERBATIM as printed (`unit_text`), and the sentence or row it took
    them from (`quote`). It never converts, never adds up, never rounds.
  * Every fact is then GROUNDED mechanically: the quote must occur in the text
    we sent, the digits must occur in the quote, and the unit phrase must occur
    in the window. A fact failing any of those is dropped, not down-weighted.
  * The page is recomputed from where the quote was FOUND, never taken from the
    model — a model asked to count pages will guess one.
  * Unit conversion to crore runs through the same TO_CRORE table the
    deterministic stage uses. There is one unit authority in this pipeline.
  * `status` carries `nil` and `not_disclosed` as first-class answers, so
    "the company says there are none" is expressible without inventing a zero.

Windows are deliberately GENEROUS. Section anchors are how the deterministic
stage finds text, and a missed heading there must not cost a company its data
here — so each task takes the union of its sections AND keyword hits anywhere
in the document. The nearest preceding unit declaration is injected at the top
of every window, because a window that starts below "(` in crore)" is a window
whose numbers are unreadable.

    pivot/.venv/bin/python pivotted/filing_llm.py --dir filings_sample
    pivot/.venv/bin/python pivotted/filing_llm.py --dir filings_holdout
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from filing_extract import (  # noqa: E402
    Doc, PAGE_RE, TO_CRORE, _U, normalise, parse, to_crore, unit_key,
    INDIAN_GROUP_RE,
)

sys.path.insert(0, str(HERE.parent / "charto" / "data"))
import dataserver as ds  # noqa: E402  — one Azure key for the whole repo

LLM_EFFORT = os.environ.get("FILING_LLM_EFFORT", "low")
LLM_WORKERS = int(os.environ.get("FILING_LLM_WORKERS", "12"))
_TIMEOUT = 180


def _ssl_ctx():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


# ────────────────────────────────────────────────────────────── windows

WINDOW_RADIUS = 2600      # chars of context to keep after an anchor
MAX_WINDOWS = 6
MAX_WINDOW_CHARS = 4200   # per-window cap — see build_windows()
MAX_TASK_CHARS = 18000    # hard budget per task per document


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    spans.sort()
    out: list[tuple[int, int]] = []
    for a, b in spans:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def build_windows(d: Doc, sections: list[str], keywords: list[str]) -> list[str]:
    """Several small windows at the densest anchor sites, never one big one.

    Anchors come from two sources on purpose. Sections give clean text when the
    heading was found; keywords are the safety net for when it was not —
    `chairman_letter` is titled in only 5 of 19 filings, and a task depending
    solely on segmentation would inherit every such miss.

    The per-window cap is not tidiness, it is the fix for a measured failure.
    The first version merged overlapping spans and let them take the budget in
    length order. In a 384-page report the longest merged span runs to tens of
    thousands of characters, so it consumed the entire allowance and the model
    saw ONE window from whichever cluster happened to be biggest — often the
    contents page. TCS returned zero contingent liabilities, zero segments,
    zero geography and zero related-party transactions, all of which it plainly
    discloses, while small filers whose spans fit under the cap looked fine.
    A budget spent in one place is a budget spent in the wrong place.
    """
    norm_doc = _norm_flat(d.text)
    anchors: list[int] = []

    for s in d.sections:
        if s.name in sections:
            anchors.append(s.char_start)
    for kw in keywords:
        # norm_doc is index-aligned with d.text (see _norm_flat)
        anchors += [m.start() for m in re.finditer(kw, norm_doc, re.I)]
    if not anchors:
        return []
    anchors.sort()

    spans = [(max(0, a - WINDOW_RADIUS // 3),
              min(len(d.text), a + WINDOW_RADIUS)) for a in anchors]
    merged = _merge(spans)

    # Rank by how many anchors a span contains, not by how long it is. A table
    # is where the vocabulary CLUSTERS; a long span is often just prose that
    # mentioned the phrase once.
    scored = []
    for a, b in merged:
        n = sum(1 for x in anchors if a <= x < b)
        scored.append((n, a, b))
    scored.sort(key=lambda t: (-t[0], t[1]))

    chosen, total = [], 0
    for n, a, b in scored:
        if total >= MAX_TASK_CHARS or len(chosen) >= MAX_WINDOWS:
            break
        # Centre the cap on the anchor cluster so truncation never cuts the
        # table off at its header.
        inside = [x for x in anchors if a <= x < b]
        lo = max(a, min(inside) - WINDOW_RADIUS // 3) if inside else a
        b = min(b, lo + MAX_WINDOW_CHARS, lo + (MAX_TASK_CHARS - total))
        if b - lo < 200:
            continue
        chosen.append((lo, b))
        total += b - lo
    chosen = _merge(chosen)

    out = []
    for a, b in chosen:
        u = d.resolve_unit(a)
        # The declaration that governs this text may sit above the window. Carry
        # it in explicitly and SAY it was carried, so a table with its own header
        # can still override it — that override is trap #1's whole point.
        head = (f"[unit in force where this extract begins, carried forward from "
                f"page {u.page}: {u.raw!r} — a table below with its own unit "
                f"header overrides this]\n" if u else
                "[no unit declaration precedes this extract]\n")
        out.append(head + d.text[a:b])
    return out


def _norm_flat(text: str) -> str:
    """Lowercase, index-preserving. Length must equal the input's, since window
    offsets found here index straight back into d.text."""
    return text.lower()


# ────────────────────────────────────────────────────────────── schema

# One item shape for every task. strict:true requires each property to appear in
# `required`, so optionality is expressed as a nullable type rather than omission
# — which also means the model must consciously answer "null" instead of quietly
# dropping the field.
ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["group", "label", "kind", "rollup", "value_text", "unit_text",
                 "period", "basis", "note", "quote", "status"],
    "properties": {
        # `group` exists so the sum check can add the right things together.
        # Without it, CAMEXLTD's segment note summed revenue + operating income
        # + assets into one bucket and "failed" a table the model had read
        # perfectly; its contingent note summed the liabilities table with the
        # capital-commitments table sitting under the same date. Naming the
        # table is READING, which is the model's job — the adding stays here.
        "group": {"type": "string",
                  "description": "the table or measure this row belongs to, as "
                                 "the document heads it — e.g. \"Segment Revenue\", "
                                 "\"Segment Assets\", \"Contingent Liabilities\", "
                                 "\"Capital Commitments\". Rows of one table must "
                                 "carry byte-identical group strings."},
        "label": {"type": "string",
                  "description": "what this is, worded as the document words it"},
        # A headcount is not money. The deterministic stage learned this by
        # turning "75 permanent employees" into 0.75 crore; here the same error
        # arrived as unit_text "employees" and "% to total turnover", counted as
        # unresolved currency. Only `currency` is ever unit-scaled.
        "kind": {"type": "string",
                 "enum": ["currency", "count", "percent", "ratio", "text"]},
        # Marks a row DERIVED from other rows of the same table. Without it the
        # sum check added HDFCBANK's "Income from operations (1) + (2) - (3)" to
        # the seven segments it was computed from and reported a 1.68x error
        # against a table the model had read correctly.
        "rollup": {"type": "boolean",
                   "description": "true if this row is a total, subtotal, "
                                  "elimination, or otherwise arithmetically "
                                  "derived from other rows of the same table"},
        "value_text": {"type": ["string", "null"],
                       "description": "the number EXACTLY as printed, digits and "
                                      "separators unchanged. null if none."},
        "unit_text": {"type": ["string", "null"],
                      "description": "the unit phrase exactly as printed in the "
                                     "extract, e.g. \"(` in crore)\". null if none."},
        "period": {"type": ["string", "null"]},
        "basis": {"type": ["string", "null"], "enum": ["standalone", "consolidated", None]},
        "note": {"type": ["string", "null"],
                 "description": "one short clause of qualifying detail"},
        "quote": {"type": "string",
                  "description": "verbatim span copied from the extract, "
                                 "containing the number if there is one"},
        "status": {"type": "string", "enum": ["reported", "nil", "not_disclosed"]},
    },
}
SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {"items": {"type": "array", "items": ITEM}},
}

SYSTEM = """\
You extract facts from Indian annual reports for a database. You are reading \
raw text extracted from a PDF, so tables arrive as loose columns, sentences \
wrap mid-line, and the rupee sign may render as ` C K I H J ~ or a dash.

Rules, in order of importance:

1. COPY, NEVER COMPUTE. `value_text` is the number exactly as printed — same \
digits, same commas, same brackets. Never convert lakh to crore, never total a \
column, never round, never fill a blank from another figure.
2. `quote` must be copied character-for-character from the extract, long enough \
to contain the number and its label. If you cannot copy it, do not report it.
3. `unit_text` is the unit phrase as printed. Prefer a unit header belonging to \
the table you are reading over the carried-forward one named in brackets at the \
top of the extract. If the figure is in absolute rupees, say so in unit_text \
using the document's own words.
4. If the document says there are none — "Nil", "not applicable", "the company \
has not provided for any" — return one item with status "nil" and value_text \
null. Do NOT attach a nearby unrelated number to it. If the topic is simply \
absent from the extract, return nothing at all rather than a guess.
5. Report figures for every period the extract prints, each as its own item, \
with `period` filled in.
6. One row of a table is one item. Give every row of the same table the same \
`group` string, and give a different table a different one — a revenue table \
and an assets table printed under one heading are two tables.
7. Set `rollup` true for any row derived from other rows of the same table — \
totals, subtotals, eliminations, inter-segment adjustments, "less:" lines, and \
lines whose label is itself a formula. Set `kind` to what the figure IS: money \
is `currency`, a headcount or unit count is `count`, anything printed with a \
per-cent sign is `percent`, a multiple or times-covered figure is `ratio`, and \
a status with no number is `text`.
8. Never report a number you did not see in the extract."""


# ────────────────────────────────────────────────────────────── tasks

@dataclass
class Task:
    name: str
    sections: list[str]
    keywords: list[str]
    instruction: str


TASKS: list[Task] = [
    Task(
        "contingent",
        ["contingent", "notes", "balance_sheet"],
        [r"contingent liabilit", r"claims against the company not acknowledged",
         r"commitments\b.{0,40}capital", r"bank guarantee"],
        "Extract every contingent liability and commitment, itemised as the "
        "document itemises them (tax demands split by tax type, claims not "
        "acknowledged as debts, guarantees, letters of credit, capital "
        "commitments), plus the stated total if one is printed. If the company "
        "states it has none, say so with status nil."),
    Task(
        "segments",
        ["segment_note", "mda", "notes"],
        [r"segment revenue", r"operating segments", r"reportable segments",
         r"segment results", r"business segments"],
        "Extract revenue and, where printed, result/profit and assets for each "
        "reportable business segment, plus the total. Use the segment names the "
        "company uses. Include unallocated/eliminations lines as their own items."),
    Task(
        "geography",
        ["segment_note", "mda", "notes"],
        [r"geographical segment", r"revenue by geograph", r"outside india",
         r"within india", r"export", r"revenue from external customers"],
        "Extract revenue (and non-current assets if printed) split by country or "
        "geography. If the company reports only India versus overseas, that is "
        "the split — report it as printed. If no geographic split is disclosed, "
        "return nothing."),
    Task(
        "special_metrics",
        ["mda", "chairman_letter", "board_report"],
        [r"order book", r"capacity utilisation", r"capacity utilization",
         r"installed capacity", r"assets under management", r"same[- ]store",
         r"realisation per", r"per tonne", r"usfda", r"import alert",
         r"gross merchandise", r"subscriber", r"footfall", r"occupancy",
         r"net interest margin", r"gross npa", r"casa ratio", r"claims ratio",
         r"average selling price", r"utilisation rate", r"plant load factor",
         r"distribution network", r"outlets", r"dealer", r"market share"],
        "Extract the OPERATING metrics specific to this company's business — the "
        "kind that never appear in a standard profit-and-loss statement. Order "
        "book, installed and utilised capacity, plant load factor, tonnes or "
        "units sold, realisation per unit, store or outlet counts, subscribers, "
        "occupancy, market share, regulatory site status (e.g. a USFDA "
        "classification for a named plant), AUM, NIM, gross NPA, CASA, claims "
        "ratio. Name each metric as the company names it. A metric with no "
        "number but a material status (\"Halol facility remains under import "
        "alert\") is worth reporting with value_text null."),
    Task(
        "strategy",
        ["mda", "chairman_letter", "board_report"],
        [r"capital expenditure", r"capex", r"expansion", r"greenfield",
         r"brownfield", r"acquisition of", r"divest", r"demerger",
         r"strategic priorit", r"going forward", r"we expect to",
         r"new plant", r"commission(?:ed|ing)", r"restructur"],
        "Extract stated intent and change of direction: planned capital "
        "expenditure, capacity being added, plants commissioned or closed, "
        "acquisitions, divestments, demergers, entry into or exit from a "
        "business, and any explicit forward statement management makes. Put the "
        "commitment in `label`, the amount in value_text when one is given, and "
        "the timeframe in `period`. Prefer specific commitments over "
        "aspirational language; skip pure boilerplate."),
    Task(
        "audit",
        ["key_audit_matters", "auditor_report"],
        [r"key audit matters?", r"basis for (?:qualified|adverse|disclaimer)",
         r"emphasis of matter", r"material uncertainty related to going concern",
         r"qualified opinion"],
        "Extract each Key Audit Matter as its own item — the matter's title in "
        "`label`, why the auditor flagged it in `note`. Also report the opinion "
        "type (unmodified/qualified/adverse/disclaimer), any Emphasis of Matter, "
        "and any going-concern uncertainty. These are auditor judgements, so "
        "most items will have value_text null."),
    Task(
        "related_party",
        ["related_party", "notes"],
        [r"related part(?:y|ies) transaction", r"key management personnel",
         r"transactions with related part"],
        "Extract related-party transaction totals by counterparty type "
        "(subsidiaries, associates, joint ventures, key management personnel, "
        "entities controlled by directors) and by nature (sales, purchases, "
        "loans given, guarantees, remuneration), plus closing balances "
        "outstanding if printed."),
]
TASK_BY_NAME = {t.name: t for t in TASKS}


# ────────────────────────────────────────────────────────────── grounding

_WS = re.compile(r"\s+")
_ALNUM = re.compile(r"[^a-z0-9]+")
_DIGITS = re.compile(r"[^0-9.]+")
UNIT_IN_TEXT = re.compile(rf"({_U})", re.I)
ABS_RUPEE_RE = re.compile(r"absolute|actual|rupees?\b(?!\s*in)|/-", re.I)
# A currency marker carrying NO multiplier word: "Rs.", "INR", "(`)", "₹".
# Deliberately excludes the single-letter stand-ins (C K I H J) from trap #3 —
# every one of them is also an ordinary letter, and reading a stray "K" as a
# rupee sign would silently divide a figure by ten million.
BARE_RUPEE_RE = re.compile(r"[`₹]|\brs\.?\b|\binr\b", re.I)


def _flat(s: str) -> str:
    return _WS.sub(" ", s).strip().lower()


def _alnum(s: str) -> str:
    return _ALNUM.sub("", s.lower())


def _digits(s: str) -> str:
    return _DIGITS.sub("", s)


@dataclass
class LLMFact:
    symbol: str
    task: str
    group: str
    label: str
    kind: str
    rollup: bool
    value_text: str | None
    unit_text: str | None
    period: str | None
    basis: str | None
    note: str | None
    quote: str
    status: str
    # everything below is computed here, never by the model
    value_raw: float | None = None
    unit: str | None = None
    value_crore: float | None = None
    page: int | None = None
    char_pos: int = -1
    grounding: str = "ungrounded"
    unit_source: str = "none"
    unit_agrees: str = "n/a"      # vs the deterministic resolver
    period_ambiguous: bool = False
    partial_table: bool = False
    drop_reason: str | None = None


# A figure often arrives carrying its own unit — "2 Crores", "₹12 Crores",
# "1,012.7 MW", "53,000+", "3.48 per cent". The first parser accepted only bare
# numerals and threw 152 of these away, 96 of them from special_metrics: the
# installed capacity, the order book, the store count — precisely the material
# nobody else publishes and the whole reason for reading the PDFs.
_NUM_TOKEN = re.compile(r"-?\d[\d,]*\.?\d*")
# A unit welded to its number is the most local declaration that can exist, so
# it outranks any header. Same rule as the deterministic stage's inline-after.
_TRAIL_UNIT = re.compile(
    rf"^\s*(?:{_U}|per\s*cent|%|mw|kw|gw|mt|mmt|tpa|tonnes?|kg|units?|sq\.?\s*ft|"
    rf"acres?|km|litres?|kl|bbl|nos\.?|stores?|outlets?|employees?|mmt|mtpa|mnt|"
    rf"lakh tonnes?|msf|mmscmd|boepd)\b", re.I)
# A dash or "nil" in a table cell is a REPORTED ZERO, not a parse failure.
_NIL_CELL = re.compile(r"^[\s\-–—~]*$|^\s*(nil|n\.?a\.?|none)\s*$", re.I)


def parse_value(s: str) -> tuple[float | None, str | None, str]:
    """-> (number, unit welded to the number, status hint).

    Returns status "nil" for a dash/Nil cell so the caller records a reported
    zero rather than dropping the row.
    """
    s = (s or "").strip()
    if not s or _NIL_CELL.match(s):
        return None, None, "nil"
    neg = s.startswith("(") and s.rstrip().endswith(")")
    body = s.strip("()").strip()
    body = re.sub(r"^[^\d\-+.]{0,4}", "", body)          # leading ₹ ` Rs etc.
    m = _NUM_TOKEN.match(body)
    if not m:
        return None, None, "unparseable"
    try:
        v = float(m.group(0).replace(",", ""))
    except ValueError:
        return None, None, "unparseable"
    tail = body[m.end():]
    tail = tail.lstrip("+")                               # "53,000+" ~ 53000
    um = _TRAIL_UNIT.match(tail)
    return (-abs(v) if neg else v), (um.group(0).strip() if um else None), "ok"


def _locate(d: Doc, quote: str) -> int:
    """Char offset of a quote in the full document, or -1.

    Needed for two things that must not be guessed: the page, and the
    deterministic unit that governs this position. An earlier version probed
    with a single fixed-length prefix, failed on almost every quote, and then
    reported the failure as "no unit declaration above" — which made a check
    that never ran look like a check that passed.
    """
    for n in (140, 80, 44):
        probe = quote[:n]
        if len(probe) < 14:
            break
        at = d.text.find(probe)
        if at >= 0:
            return at
    head = _alnum(quote[:70])
    if len(head) >= 14:
        for p in d.pages:
            if head in _alnum(d.text[p.start:p.end]):
                return p.start
    return -1


def ground(d: Doc, window_blob: str, raw: dict, task: str) -> LLMFact:
    """Verify a model-returned item against the exact text the model was sent.

    Three independent checks, each of which a hallucination fails:
      quote in window   — the sentence was really there
      digits in quote   — the number really belongs to that sentence
      unit in window    — the unit was read, not assumed

    The page is then recovered from where the quote sits in the FULL document,
    which is why a passing quote is worth more than a page number the model
    could have invented.
    """
    f = LLMFact(
        symbol=d.symbol, task=task,
        group=(raw.get("group") or "").strip(),
        label=(raw.get("label") or "").strip(),
        kind=(raw.get("kind") or "text"),
        rollup=bool(raw.get("rollup")),
        value_text=(raw.get("value_text") or None),
        unit_text=(raw.get("unit_text") or None),
        period=(raw.get("period") or None),
        basis=(raw.get("basis") or None),
        note=(raw.get("note") or None),
        quote=" ".join((raw.get("quote") or "").split())[:400],
        status=(raw.get("status") or "reported"),
    )
    if not f.quote:
        f.drop_reason = "no quote"
        return f

    win_flat, win_alnum = _flat(window_blob), _alnum(window_blob)
    q_flat, q_alnum = _flat(f.quote), _alnum(f.quote)

    if q_flat and q_flat in win_flat:
        f.grounding = "exact"
    elif len(q_alnum) >= 12 and q_alnum in win_alnum:
        # The model normalised whitespace or dropped a rupee glyph. The words
        # and digits are still the document's, so this is grounded but softer.
        f.grounding = "normalised"
    else:
        # Distinguish the two ways this happens, because they mean opposite
        # things. A quote that is several numbers strung after a label is the
        # model REBUILDING a row out of column-major text (recon trap #4: some
        # PDFs extract by column, some by row). The numbers are real but the
        # label-to-value pairing is the model's guess, and a mis-paired figure
        # is the worst thing this pipeline could emit — so it is refused just
        # as firmly, but it is not the same failure as invention.
        nums = len(re.findall(r"\d[\d,]*\.\d|\d{3,}", f.quote))
        f.drop_reason = ("row reconstructed from column-major text, not present "
                         "verbatim" if nums >= 3 else
                         "quote not found in the text sent to the model")
        return f

    # Page and unit context from where the quote REALLY sits — never the model's
    # page number, never a probe that silently failed.
    at = _locate(d, f.quote)
    f.char_pos = at
    if at >= 0:
        f.page = d.page_of(at)

    if f.status != "reported" or not f.value_text:
        return f

    if _digits(f.value_text) and _digits(f.value_text) not in _digits(f.quote):
        f.drop_reason = f"value {f.value_text!r} is not inside its own quote"
        return f

    f.value_raw, welded, hint = parse_value(f.value_text)
    if hint == "nil":
        # "-" in a table cell is the company reporting zero, which is an answer.
        f.status, f.unit_source = "nil", "dash/nil cell"
        return f
    if f.value_raw is None:
        f.drop_reason = f"value {f.value_text!r} is not a number"
        return f

    # A unit welded to the number beats every header. "1,012.7 MW" is a
    # capacity however the surrounding table is denominated, and "₹12 Crores"
    # in a lakh-denominated page is still twelve crore.
    if welded:
        wk = to_crore(welded)
        if wk is not None:
            f.kind, f.unit = "currency", unit_key(welded)
            f.value_crore = f.value_raw * wk
            f.unit_source = f"welded to the figure: {f.value_text!r}"
        else:
            f.kind = "measure"
            f.unit = re.sub(r"\s+", "", welded).lower()
            f.unit_source = f"physical unit welded to the figure: {f.unit}"
        return f

    # The model calls a capacity "currency" when it sits in a financial table —
    # ULTRACEMCO's MMT/MTPA/MW accounted for 33 of the 44 unresolved figures on
    # the held-out set. A physical unit is not money whatever the model typed,
    # and treating it as an unresolved rupee figure hides a perfectly good fact.
    if f.unit_text and _TRAIL_UNIT.match(f.unit_text.strip()) \
            and to_crore(f.unit_text.strip()) is None:
        f.kind = "measure"
        f.unit = re.sub(r"\s+", "", f.unit_text).lower()[:12]
        f.unit_source = f"physical unit: {f.unit_text.strip()!r}"
        return f

    # Only money is unit-scaled. A count, a percentage and a ratio are already
    # in their own units and carry no multiplier to resolve.
    if f.kind != "currency":
        f.unit, f.unit_source = f.kind, "n/a (not currency)"
        return f

    if not f.unit_text:
        f.unit, f.unit_source = "UNKNOWN", "model gave no unit"
        return f
    if _alnum(f.unit_text) and _alnum(f.unit_text) not in win_alnum:
        f.unit, f.unit_source = "UNKNOWN", f"unit {f.unit_text!r} not in the extract"
        f.drop_reason = f.unit_source
        return f

    um = UNIT_IN_TEXT.search(f.unit_text)
    if um and to_crore(um.group(1)) is not None:
        f.unit = unit_key(um.group(1))
        f.value_crore = f.value_raw * TO_CRORE[f.unit]
        f.unit_source = f"model-quoted: {f.unit_text.strip()!r}"
    elif BARE_RUPEE_RE.search(f.unit_text) or ABS_RUPEE_RE.search(f.unit_text) \
            or INDIAN_GROUP_RE.fullmatch((f.value_text or "").strip("()")):
        # A currency marker with NO multiplier word means absolute rupees —
        # "Rs.", "(`)", "8,49,522/-". This is the weakest inference here, which
        # is exactly why the deterministic cross-check below exists.
        f.unit, f.value_crore = "rupees", f.value_raw * 1e-7
        f.unit_source = f"bare currency marker: {f.unit_text.strip()!r}"
    else:
        f.unit, f.unit_source = "UNKNOWN", f"unrecognised unit {f.unit_text!r}"

    # THE CHECK ON THE MODEL ITSELF. filing_extract resolves units by walking
    # back to the nearest printed declaration — no model involved. Comparing the
    # two is free and independent: agreement means two unrelated methods read
    # the same header, and a disagreement is the one place a 100x error can
    # hide. Disagreements are REPORTED, not silently preferred either way,
    # because the model is sometimes right (a table with its own header
    # overriding a carried-forward one is trap #1 working as intended).
    if f.unit != "UNKNOWN":
        det = d.resolve_unit(f.char_pos) if f.char_pos >= 0 else None
        if f.char_pos < 0:
            f.unit_agrees = "quote-not-locatable"
        elif det is None:
            f.unit_agrees = "no-declaration-above"
        elif det.unit == f.unit:
            f.unit_agrees = "agree"
        else:
            f.unit_agrees = f"DISAGREE model={f.unit} deterministic={det.unit}"
    return f


# ────────────────────────────────────────────────────────────── the call

_USAGE = {"in": 0, "out": 0, "calls": 0, "fail": 0}


def call_model(system: str, user: str) -> tuple[list[dict], str | None]:
    payload = {
        "model": ds.LLM_DEPLOYMENT,
        "input": [{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        # RELIANCE's Key Audit Matters ran past 6000 and came back `incomplete`,
        # i.e. half a JSON object. Truncation costs a whole task, so the ceiling
        # is set well above the longest observed answer.
        "max_output_tokens": 12000,
        "reasoning": {"effort": LLM_EFFORT},
        "service_tier": ds.LLM_SERVICE_TIER,
        "text": {"format": {"type": "json_schema", "name": "facts",
                            "strict": True, "schema": SCHEMA}},
    }
    req = urllib.request.Request(
        f"{ds.AZURE_ENDPOINT}/responses", data=json.dumps(payload).encode(),
        headers={"api-key": ds.AZURE_KEY, "Content-Type": "application/json"},
        method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_ssl_ctx()) as r:
                j = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            body = e.read().decode()[:300]
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            _USAGE["fail"] += 1
            return [], f"HTTP {e.code}: {body}"
        except Exception as exc:  # noqa: BLE001
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
                continue
            _USAGE["fail"] += 1
            return [], f"{type(exc).__name__}: {exc}"
    else:
        return [], "exhausted retries"

    u = j.get("usage") or {}
    _USAGE["in"] += u.get("input_tokens", 0)
    _USAGE["out"] += u.get("output_tokens", 0)
    _USAGE["calls"] += 1

    if j.get("status") == "incomplete":
        # A truncated response yields half a JSON object; say so rather than
        # silently returning the items that happened to fit.
        return [], f"incomplete: {(j.get('incomplete_details') or {}).get('reason')}"

    text = ""
    for o in j.get("output", []):
        if o.get("type") == "message":
            for c in o.get("content", []):
                text += c.get("text") or ""
    if not text.strip():
        return [], "empty response"
    try:
        return (json.loads(text).get("items") or []), None
    except json.JSONDecodeError as exc:
        return [], f"unparseable JSON: {exc}"


def run_task(d: Doc, t: Task) -> tuple[list[LLMFact], str | None]:
    wins = build_windows(d, t.sections, t.keywords)
    if not wins:
        return [], None  # topic genuinely absent — not an error
    blob = "\n\n────────────────\n\n".join(wins)
    user = (f"COMPANY: {d.symbol}\nTASK: {t.instruction}\n\n"
            f"EXTRACT FROM THE ANNUAL REPORT (page markers are physical PDF "
            f"pages):\n\n{blob}")
    items, err = call_model(SYSTEM, user)
    if err:
        return [], err
    return [ground(d, blob, it, t.name) for it in items], None


# ────────────────────────────────────────────────────────────── validation

_STAR = re.compile(r"[*†‡#]+|\s+")


def dedupe(facts: list[LLMFact]) -> tuple[list[LLMFact], int]:
    """One fact per (company, table, row, period, value).

    An annual report prints the same segment table twice — once in the
    standalone financials and once in the consolidated — and the model, reading
    generously across several windows, correctly returns both. Every affected
    sum then came out at a ratio of exactly 2.000, which is the signature of
    double counting rather than of a misread table.

    Matching is on the NUMBER, not on the printed text, because the two copies
    are not printed identically: RELIANCE writes its O2C revenue as "6,25,928"
    on page 157 and "625,928" on page 169 — Indian grouping in one, Western in
    the other, same figure. Footnote daggers are stripped from labels for the
    same reason ("O2C" vs "O2C **").
    """
    seen: dict[tuple, LLMFact] = {}
    dropped = 0
    for f in facts:
        k = (f.symbol, f.task, f.group.lower().strip(),
             _STAR.sub(" ", f.label.lower()).strip(), (f.period or "").lower(),
             f.basis, None if f.value_raw is None else round(f.value_raw, 4))
        if k in seen:
            dropped += 1
            continue
        seen[k] = f
    return list(seen.values()), dropped


def check_sums(facts: list[LLMFact]) -> list[str]:
    """Do the parts add up to the whole the document itself printed?

    This is the only validation that needs no external data and no human: a
    segment table that totals to something other than its own total line has a
    unit resolved wrongly, a row missed, or a row invented. Recon proved the
    check works — FABCLEAN's order-book chart prints values before labels, and
    the pairing was only verifiable because 184.42+135.18+27.17+0.5+7.47
    equalled the printed 354.74.
    """
    msgs = []
    skipped = 0
    for task in ("segments", "geography", "contingent"):
        by: dict[tuple, list[LLMFact]] = {}
        for f in facts:
            if f.task != task or f.value_crore is None:
                continue
            # group AND measure. One printed table routinely carries several
            # measures — CAMEXLTD heads a whole segment P&L "a. Segment Revenue"
            # and puts sales, other income, operating income and tax under it.
            # The model already separates them in the label ("Total Segment
            # Revenue — Fiber Glass"), so the measure is the part before the
            # dash. Grouping without it adds operating income to sales and
            # reports a 1.03 discrepancy against a table read correctly.
            m = re.split(r"\s+[—–-]\s+", f.label, maxsplit=1)
            measure = m[0].lower().strip() if len(m) > 1 else ""
            by.setdefault((f.symbol, f.group.lower(), measure, f.period or "",
                           f.basis or ""), []).append(f)
        for (sym, grp, measure, per, bas), rows in sorted(by.items()):
            # `rollup` is the model's own reading of which rows are derived.
            # The label regex stays only as a backstop for a missed flag.
            tot = [f for f in rows if f.rollup and re.search(
                r"^\s*(grand\s+)?total\b|\btotal\b\s*$", f.label, re.I)]
            # An UNALLOCABLE row is a component, not a memo — GUJTLRM's segment
            # assets are 346.40 across four segments plus 200.28 unallocable,
            # and the printed total is 546.68. Excluding it (as an earlier
            # version did, and as the model's own rollup flag also did) failed
            # six tables that had been read exactly right. It carries its own
            # sign: the same company's unallocable INCOME is -6.88, and
            # 18.50 + (-6.88) = 11.61 = the printed total.
            #
            # Genuine memo lines are still excluded: MARUTI prints "Amount
            # deposited under protest" beneath its claims table, and adding it
            # makes a correct table read 1.10x. "Less:" rows are excluded
            # because the total sits above them, not below.
            # NOTE the spelling: filings write "Unallocable", which does not
            # contain "unallocat". Matching on the longer stem left these rows
            # in `parts` AND re-added them below, counting GUJTLRM's 200.28
            # twice and turning a 0.63 shortfall into a 1.37 excess. Match
            # "unalloca" and add back only the rows the model called rollup.
            memo = (r"eliminat|inter[- ]segment|deposited under protest|"
                    r"^\s*less\s*:")
            parts = [f for f in rows
                     if not f.rollup and not re.search(memo, f.label, re.I)]
            parts += [f for f in rows
                      if f.rollup and re.search(r"unalloca", f.label, re.I)
                      and not re.search(memo, f.label, re.I)]
            # One row label, one period, two different values = the model gave
            # two year-columns the same period string. Only one can be right, and
            # guessing which would be inventing data — so the bucket is declared
            # unreconcilable instead. This is what remained of HDFCBANK's segment
            # results after every structural cause was ruled out: a 5-segment
            # table summing 6 rows.
            per_label: dict[str, set] = {}
            for f in parts:
                per_label.setdefault(f.label.lower(), set()).add(round(f.value_crore, 2))
            if any(len(v) > 1 for v in per_label.values()):
                for f in parts:
                    f.period_ambiguous = True
                skipped += 1
                continue
            if len(parts) < 2 or len(tot) != 1:
                # Zero totals means nothing to check against; more than one means
                # the bucket still holds two tables. Neither is a data error, so
                # neither may be reported as a failure.
                skipped += 1
                continue
            printed = tot[0]
            s = sum(f.value_crore for f in parts)
            r = s / printed.value_crore if printed.value_crore else 0
            # Over and under mean opposite things and must not share a verdict.
            # Parts EXCEEDING the total is double counting or a misread row — a
            # correctness failure. Parts falling well SHORT means rows are
            # missing: LT's note 57 totals 123,080 cr and the model returned six
            # lines of it, because the note runs past the window. Every number
            # it did return is right. Calling that a failure would hide the real
            # ones, so it is reported as INCOMPLETE and the rows are flagged.
            if r > 1.03:
                verdict = "FAIL"
            elif r < 0.97:
                verdict = "PART"
                for f in parts:
                    f.partial_table = True
            else:
                verdict = "PASS"
            label = f"{grp[:24]}/{measure[:16]}" if measure else grp[:41]
            msgs.append(f"  {verdict}  {sym:10s} {label:42s} "
                        f"{per[:9]:9s} parts={s:>12,.2f} vs total="
                        f"{printed.value_crore:>12,.2f}  ratio={r:>6.3f} "
                        f"(n={len(parts)})")
    if skipped:
        msgs.append(f"  ---   {skipped} table(s) not checkable (no single printed "
                    f"total, or a period label used twice) — not failures, but "
                    f"the rows are flagged period_ambiguous")
    return msgs


# ────────────────────────────────────────────────────────────── run

def load_docs(sample_dir: Path) -> list[tuple[str, str, str]]:
    db = sqlite3.connect(sample_dir / "index.db")
    rows = list(db.execute("""SELECT symbol,exchange,text_path FROM docs
                              WHERE fetch_state='ok' AND doc_kind='annual_report'
                                AND text_path IS NOT NULL ORDER BY symbol"""))
    db.close()
    return rows


def main(sample_dir: Path, only: list[str] | None, limit: int | None,
         out_path: Path | None) -> int:
    rows = load_docs(sample_dir)
    if limit:
        rows = rows[:limit]
    tasks = [t for t in TASKS if not only or t.name in only]
    print(f"model stage  dir={sample_dir.name}  model={ds.LLM_DEPLOYMENT} "
          f"effort={LLM_EFFORT}  docs={len(rows)}  tasks={len(tasks)}  "
          f"workers={LLM_WORKERS}")

    docs: list[Doc] = []
    for sym, ex, tp in rows:
        d = parse(Path(tp))
        if not d.pages:
            print(f"  SKIP {sym:10s} unreadable (0 pages)")
            continue
        d.symbol = sym
        docs.append(d)

    t0 = time.time()
    facts: list[LLMFact] = []
    errors: list[tuple[str, str, str]] = []
    jobs = [(d, t) for d in docs for t in tasks]
    with ThreadPoolExecutor(max_workers=LLM_WORKERS) as ex:
        fut = {ex.submit(run_task, d, t): (d, t) for d, t in jobs}
        done = 0
        for f in as_completed(fut):
            d, t = fut[f]
            done += 1
            try:
                got, err = f.result()
            except Exception as exc:  # noqa: BLE001
                got, err = [], f"{type(exc).__name__}: {exc}"
            if err:
                errors.append((d.symbol, t.name, err))
            facts += got
            kept = sum(1 for x in got if not x.drop_reason)
            print(f"  [{done:>3}/{len(jobs)}] {d.symbol:10s}{t.name:15s}"
                  f"{len(got):>3} returned {kept:>3} kept"
                  + (f"  ERR {err[:60]}" if err else ""))
    wall = time.time() - t0

    kept = [f for f in facts if not f.drop_reason]
    dropped = [f for f in facts if f.drop_reason]
    kept, ndup = dedupe(kept)

    print(f"\n=== GROUNDING GATE ===")
    print(f"  returned by model : {len(facts)}")
    print(f"  duplicate rows    : {ndup} (same table printed twice in one report)")
    print(f"  kept (grounded)   : {len(kept)}")
    print(f"  DROPPED           : {len(dropped)}")
    why: dict[str, int] = {}
    for f in dropped:
        key = re.sub(r"'[^']*'", "'…'", f.drop_reason or "")
        why[key] = why.get(key, 0) + 1
    for k, v in sorted(why.items(), key=lambda x: -x[1]):
        print(f"      {v:>4}  {k}")
    gmix: dict[str, int] = {}
    for f in kept:
        gmix[f.grounding] = gmix.get(f.grounding, 0) + 1
    print(f"  grounding quality : {gmix}")

    print(f"\n=== COVERAGE BY TASK (of {len(docs)} readable reports) ===")
    for t in tasks:
        got = {f.symbol for f in kept if f.task == t.name}
        nfacts = sum(1 for f in kept if f.task == t.name)
        nil = sum(1 for f in kept if f.task == t.name and f.status == "nil")
        bar = "#" * int(20 * len(got) / max(len(docs), 1))
        print(f"  {t.name:16s} {len(got):>3}/{len(docs)} companies "
              f"{nfacts:>5} facts  {nil:>3} nil  {bar}")

    print(f"\n=== UNIT RESOLUTION (currency facts only) ===")
    cur = [f for f in kept if f.value_raw is not None and f.kind == "currency"]
    umix: dict[str, int] = {}
    for f in cur:
        umix[f.unit or "?"] = umix.get(f.unit or "?", 0) + 1
    unres = sum(1 for f in cur if f.value_crore is None)
    print(f"  currency facts {len(cur)}   units {umix}")
    print(f"  UNRESOLVED (no usable unit): {unres}  "
          f"({100*unres/max(len(cur),1):.1f}%)")
    nonc = [f for f in kept if f.value_raw is not None and f.kind != "currency"]
    kmix: dict[str, int] = {}
    for f in nonc:
        kmix[f.kind] = kmix.get(f.kind, 0) + 1
    print(f"  non-currency numerics (never scaled): {len(nonc)}  {kmix}")

    print(f"\n=== CHECKING THE MODEL: its unit vs the deterministic resolver ===")
    print("    (two independent readings of the same header; a disagreement is")
    print("     where a 100x error would hide, so they are named individually)")
    amix: dict[str, int] = {}
    for f in cur:
        amix[f.unit_agrees.split()[0]] = amix.get(f.unit_agrees.split()[0], 0) + 1
    print(f"  {amix}")
    dis = [f for f in cur if f.unit_agrees.startswith("DISAGREE")]
    seen_d: set[str] = set()
    for f in dis:
        k = f"{f.symbol}|{f.unit_agrees}"
        if k in seen_d:
            continue
        seen_d.add(k)
        print(f"    {f.symbol:10s}{f.task:14s}{f.unit_agrees:44s}"
              f" p{f.page} {f.label[:34]}")
        print(f"        model read: {str(f.unit_text)[:80]!r}")

    print(f"\n=== VALIDATION: parts sum to the document's own printed total ===")
    v = check_sums(kept)
    print("\n".join(v) if v else "    (no company printed both parts and a total)")
    if v:
        npass = sum(1 for x in v if x.lstrip().startswith("PASS"))
        nfail = sum(1 for x in v if x.lstrip().startswith("FAIL"))
        npart = sum(1 for x in v if x.lstrip().startswith("PART"))
        print(f"\n    {npass} reconcile / {nfail} OVER-COUNT (a real error) / "
              f"{npart} incomplete (rows missing, numbers still right)")

    if errors:
        print(f"\n=== CALL ERRORS ({len(errors)}) ===")
        for sym, tn, err in errors[:25]:
            print(f"  {sym:10s}{tn:15s}{err[:110]}")

    print(f"\n=== COST / TIME ===")
    print(f"  wall {wall:.1f}s   calls {_USAGE['calls']}  failed {_USAGE['fail']}")
    print(f"  tokens in {_USAGE['in']:,}  out {_USAGE['out']:,}  "
          f"per doc in {_USAGE['in']//max(len(docs),1):,}")

    if out_path:
        out_path.write_text(json.dumps(
            [asdict(f) for f in kept], indent=1, ensure_ascii=False), encoding="utf-8")
        # Dropped facts are written too. A gate you cannot inspect is a gate you
        # cannot tell from a bug — the drop file is how "the model hallucinated"
        # gets distinguished from "the grounding check is too strict".
        dpath = out_path.with_name(out_path.stem + "_dropped.json")
        dpath.write_text(json.dumps(
            [asdict(f) for f in dropped], indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"\n  wrote {len(kept)} grounded facts -> {out_path}")
        print(f"  wrote {len(dropped)} dropped     -> {dpath}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="filings_sample")
    ap.add_argument("--task", action="append", help="run only these tasks")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--out", help="write grounded facts as JSON")
    a = ap.parse_args()
    raise SystemExit(main(HERE / a.dir, a.task, a.limit,
                          Path(a.out) if a.out else None))
