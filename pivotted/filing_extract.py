"""Segment Indian annual reports, resolve their units, and extract structured facts.

Built from the reconnaissance in `filings_sample/FINDINGS_{A,B,C}.md`. Every rule
here exists because the sample proved it was needed — the comments name the
evidence, because each of these fails SILENTLY if you get it wrong.

THE FOUR TRAPS THIS EXISTS TO SURVIVE

1. UNITS ARE PER-TABLE, NOT PER-DOCUMENT.
   HDFCBANK declares "(` in crore)" in its front-matter highlights and
   "(` in '000)" in its statutory statements — same PDF, 10,000x apart. TCS
   declares both crore and million. CAMEXLTD is crore in 2026 and lakh in 2025.
   MARUTI and SUNPHARMA (large caps) use million; KPGEL (small cap) uses crore,
   so size and exchange predict NOTHING.
   => resolve_unit() walks BACKWARDS to the nearest declaration. Never a
      per-company default, never a guess. No declaration found => unit unknown
      => the number is NOT emitted.

2. THE APOSTROPHE IN "'000" IS U+2018, NOT ASCII.
   An ASCII-only regex returns zero hits and you conclude the trap is absent.
   That is exactly what happened during recon. APOS covers the whole family.

3. THE RUPEE SIGN IS RARELY THE RUPEE SIGN.
   Observed standing in for it: ` C K I H J ~ \x07 (bell) and an en-dash,
   up to three variants inside ONE document. So currency is matched by the
   WORD ("in crore"), never by the glyph.

4. HEADINGS ARE LETTER-SPACED AND MIXED-CASE.
   HDFCBANK renders "I N D E P E N D E N T  A U D I T O R ' S  R E P O R T".
   MARUTI titles Secretarial Audit Report in Title Case where 8 of 10 use caps.
   JYOTI uses four casings of "Board's Report" in one file.
   => normalise() de-spaces and case-folds before any heading match.

Also encoded: page markers are physical PDF pages (printed page numbers drift by
a document-specific offset — measured -9 on RELIANCE, +17 elsewhere), and
standalone-vs-consolidated cannot be inferred from position (RIL orders
standalone first, TCS consolidated first) so it is tracked by heading.

    pivot/.venv/bin/python pivotted/filing_extract.py --sample
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SAMPLE = HERE / "filings_sample"

PAGE_RE = re.compile(r"=====\s*\[PAGE (\d+)\]\s*=====")

# Trap 2: every apostrophe variant seen in the wild.
APOS = "'‘’ʼ`´"

# Trap 1 + 3: match the WORD, never the currency glyph. Canonical unit is crore.
_U = rf"[{APOS}]?000|thousand|lakh?s?|lacs?|crores?|millions?|billions?"
UNIT_DECL_RE = re.compile(
    rf"\(\s*[^()\n]{{0,24}}?\bin\s+({_U})\b[^()\n]{{0,30}}?\)", re.I)
# Some filings write it without brackets: "Amount in Lakhs"
UNIT_BARE_RE = re.compile(rf"\b(?:amount|figures|rs\.?|inr)\s+in\s+({_U})\b", re.I)

TO_CRORE = {
    "crore": 1.0, "crores": 1.0,
    "lakh": 0.01, "lakhs": 0.01, "lac": 0.01, "lacs": 0.01,
    "million": 0.1, "millions": 0.1,
    "billion": 100.0, "billions": 100.0,
    "thousand": 1e-4, "000": 1e-4,
}


def unit_key(raw: str) -> str:
    s = raw.strip().lower().lstrip(APOS)
    return "000" if s == "000" else s


def to_crore(raw: str) -> float | None:
    return TO_CRORE.get(unit_key(raw))


# Trap 4: "I N D E P E N D E N T" -> "independent". Only collapse when a run is
# genuinely letter-spaced, so ordinary prose is untouched.
_SPACED_RUN = re.compile(r"(?:(?<=\s)|^)((?:[A-Za-z][ \t]){3,}[A-Za-z])(?=\s|$)")


def _despace(line: str) -> str:
    return _SPACED_RUN.sub(lambda m: m.group(1).replace(" ", "").replace("\t", ""), line)


def normalise(text: str) -> str:
    """Case-fold + de-letter-space + collapse whitespace. For MATCHING only."""
    return re.sub(r"[ \t]+", " ", _despace(text)).strip().lower()


# Section anchors. Ordered: earlier entries win a tie. Patterns run against the
# normalised line, so they are lowercase and whitespace-tolerant by construction.
SECTIONS: list[tuple[str, str]] = [
    ("agm_notice",       r"^notice\b.{0,40}annual general meeting|^notice is hereby given"),
    ("chairman_letter",  r"^(chairman|chairperson)[''`s]{0,2}\s+(letter|message|statement)"),
    ("mda",              r"^management discussion and analysis"),
    ("board_report",     r"^(board[''`s]{0,2}|directors?[''`s]{0,2})\s+report\b"),
    ("corp_governance",  r"^corporate governance report"),
    ("brsr",             r"^business responsibility (and|&) sustainability"),
    ("secretarial_audit", r"^secretarial audit report"),
    ("auditor_report",   r"^independent auditor[''`s]{0,2}\s+report"),
    ("key_audit_matters", r"^key audit matters?\b"),
    ("balance_sheet",    r"^(standalone |consolidated )?balance sheet\b"),
    ("profit_loss",      r"^(standalone |consolidated )?(statement of )?profit and loss"),
    ("cash_flow",        r"^(standalone |consolidated )?(statement of )?cash flows?\b"),
    ("notes",            r"^notes? (to|forming part of) .{0,30}financial statements"),
    ("segment_note",     r"^segment (information|reporting)\b|^operating segments?\b"),
    ("contingent",       r"^contingent liabilit(y|ies)( and commitments)?\b"),
    ("related_party",    r"^related part(y|ies) (transactions?|disclosures?)\b"),
]
SECTION_RES = [(name, re.compile(pat)) for name, pat in SECTIONS]

# Basis is tracked by heading, never by position (RIL standalone-first vs TCS
# consolidated-first).
BASIS_RE = re.compile(r"^(standalone|consolidated)\b")


@dataclass
class Page:
    no: int
    start: int
    end: int


@dataclass
class UnitDecl:
    pos: int
    page: int
    unit: str
    multiplier: float
    raw: str


@dataclass
class Section:
    name: str
    page_start: int
    char_start: int
    basis: str | None = None
    page_end: int = 0
    char_end: int = 0


@dataclass
class Doc:
    path: str
    symbol: str = ""
    pages: list[Page] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    units: list[UnitDecl] = field(default_factory=list)
    text: str = ""

    def page_of(self, pos: int) -> int:
        lo, hi = 0, len(self.pages) - 1
        best = 1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.pages[mid].start <= pos:
                best = self.pages[mid].no
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def resolve_unit(self, pos: int) -> UnitDecl | None:
        """Nearest declaration ABOVE pos. This is trap #1's entire defence."""
        best = None
        for u in self.units:
            if u.pos <= pos:
                best = u
            else:
                break
        return best

    def section_of(self, pos: int) -> Section | None:
        best = None
        for s in self.sections:
            if s.char_start <= pos:
                best = s
            else:
                break
        return best


def parse(path: Path) -> Doc:
    text = path.read_text(encoding="utf-8", errors="replace")
    d = Doc(path=str(path), symbol=path.name.split("_")[0], text=text)

    marks = [(m.start(), int(m.group(1)), m.end()) for m in PAGE_RE.finditer(text)]
    for i, (start, no, endm) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        d.pages.append(Page(no=no, start=endm, end=end))

    for rx in (UNIT_DECL_RE, UNIT_BARE_RE):
        for m in rx.finditer(text):
            mult = to_crore(m.group(1))
            if mult is None:
                continue
            d.units.append(UnitDecl(pos=m.start(), page=d.page_of(m.start()),
                                    unit=unit_key(m.group(1)), multiplier=mult,
                                    raw=m.group(0).strip()))
    d.units.sort(key=lambda u: u.pos)

    # Headings: scan line by line so an anchor must START a line, which keeps
    # running-text mentions ("...as set out in the Board's Report...") out.
    pos = 0
    basis = None
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.strip()
        if line and len(line) < 160:
            norm = normalise(line)
            bm = BASIS_RE.match(norm)
            if bm:
                basis = bm.group(1)
            for name, rx in SECTION_RES:
                if rx.search(norm):
                    d.sections.append(Section(name=name, page_start=d.page_of(pos),
                                              char_start=pos, basis=basis))
                    break
        pos += len(raw_line)
    d.sections.sort(key=lambda s: s.char_start)
    for i, s in enumerate(d.sections):
        s.char_end = d.sections[i + 1].char_start if i + 1 < len(d.sections) else len(text)
        s.page_end = d.page_of(max(s.char_end - 1, s.char_start))
    return d


# ---------------------------------------------------------------- extraction

NUM = rf"\(?-?\d[\d,]*\.?\d*\)?"

# Indian digit grouping: 8,49,522 / 4,24,76,055 — groups of TWO before a final
# three. Western grouping never does this. Combined with a "/-" suffix it means
# the figure is in ABSOLUTE RUPEES, whatever the section header says.
INDIAN_GROUP_RE = re.compile(r"\d{1,2}(?:,\d{2})+,\d{3}")
RUPEE_SUFFIX_RE = re.compile(r"/-")
INLINE_UNIT_RE = re.compile(rf"^\s*({_U})\b", re.I)


def parse_num(s: str) -> float | None:
    s = s.strip()
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace(",", "")
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


@dataclass
class Fact:
    symbol: str
    field: str
    kind: str
    value_raw: float
    unit: str
    value_crore: float | None
    page: int
    section: str | None
    basis: str | None
    quote: str
    unit_source: str
    confidence: str


@dataclass
class FactSpec:
    name: str
    pattern: str
    kind: str          # currency | count | percent | ratio


# kind matters: a headcount is NOT money and must never be unit-scaled. The
# first pilot converted "75 permanent employees" into 0.75 crore.
FACT_SPECS = [
    FactSpec("employees_on_roll",
             r"(\d[\d,]*)\s+permanent employees on the rolls", "count"),
    FactSpec("median_remuneration_pct_increase",
             rf"percentage increase in the median remuneration of employees"
             rf"[^.\n]{{0,60}}?({NUM})\s*%", "percent"),
    # Both CSR patterns MUST anchor on the colon that separates the label from
    # the value. Without it the regex happily returns the STATUTORY REFERENCE:
    # "as per sub-section (5)" yielded -5.00 and "as per Section 135" yielded
    # 135.00, which is what made the first validation run fail 7/7.
    # [CUR] = the rupee glyph, which is any of ` C K I H J ~ ₹ (see module docs).
    FactSpec("csr_obligation",
             rf"total csr obligation[^:\n]{{0,60}}:\s*(?:rs\.?|inr)?\s*[^\d\n]{{0,4}}({NUM})",
             "currency"),
    FactSpec("csr_avg_net_profit",
             rf"average net profit of the compan(?:y|ies)[^:\n]{{0,60}}:"
             rf"\s*(?:rs\.?|inr)?\s*[^\d\n]{{0,4}}({NUM})", "currency"),
]
_COMPILED = [(s, re.compile(s.pattern, re.I)) for s in FACT_SPECS]


def resolve_for(d: Doc, m: re.Match, num_group: int = 1):
    """Unit for THIS number, most-local-wins.

    Order matters and was set by counter-example:
      inline-after   KPGEL "` 46.80 lakhs"  — a crore declaration elsewhere in
                     the document otherwise wins and inflates it 100x.
      rupee-marker   GTV "Rs. 8,49,522/-"   — absolute rupees; the section's
                     "Rs. In Lacs" header otherwise inflates it ~10^9.
      section        the nearest preceding declaration (the general case).
    """
    tail = d.text[m.end(num_group): m.end(num_group) + 34]
    im = INLINE_UNIT_RE.match(tail)
    if im:
        mult = to_crore(im.group(1))
        if mult is not None:
            return unit_key(im.group(1)), mult, f"inline:{im.group(1)}", "high"

    raw_num = m.group(num_group)
    near = d.text[m.start(num_group): m.end(num_group) + 4]
    if RUPEE_SUFFIX_RE.search(near) or INDIAN_GROUP_RE.fullmatch(raw_num.strip("()")):
        return "rupees", 1e-7, "absolute-rupees(/- or Indian grouping)", "high"

    u = d.resolve_unit(m.start())
    if u:
        return u.unit, u.multiplier, f"section:{u.raw}", "medium"
    return "UNKNOWN", None, "none-found", "unresolved"


def extract(d: Doc) -> list[Fact]:
    out: list[Fact] = []
    for spec, rx in _COMPILED:
        for m in rx.finditer(d.text):
            v = parse_num(m.group(1))
            if v is None:
                continue
            sec = d.section_of(m.start())
            if spec.kind == "currency":
                unit, mult, src, conf = resolve_for(d, m)
                vc = (v * mult) if mult is not None else None
            else:
                # counts and percentages are dimensionless — never scaled
                unit, vc, src, conf = spec.kind, None, "n/a (not currency)", "high"
            out.append(Fact(
                symbol=d.symbol, field=spec.name, kind=spec.kind, value_raw=v,
                unit=unit, value_crore=vc, page=d.page_of(m.start()),
                section=sec.name if sec else None,
                basis=sec.basis if sec else None,
                quote=" ".join(m.group(0).split())[:170],
                unit_source=src, confidence=conf))
    return out


def validate(facts: list[Fact]) -> list[str]:
    """Internal consistency: the filing carries its own proof.

    Companies Act s.135 — CSR obligation is 2% of average net profit, and BOTH
    figures are printed in the same annexure. If our two extractions disagree,
    at least one unit was resolved wrongly. This needs no external data.
    """
    msgs = []
    by_sym: dict[str, dict[str, Fact]] = {}
    for f in facts:
        if f.kind == "currency" and f.value_crore is not None:
            # keep the LARGEST candidate per field: a filing repeats the CSR
            # annexure for each year, and the duplicates are identical anyway
            cur = by_sym.setdefault(f.symbol, {}).get(f.field)
            if cur is None or abs(f.value_crore) > abs(cur.value_crore or 0):
                by_sym[f.symbol][f.field] = f
    for sym, d in by_sym.items():
        ob, avg = d.get("csr_obligation"), d.get("csr_avg_net_profit")
        if not ob or not avg or not avg.value_crore:
            continue
        implied = 0.02 * avg.value_crore
        ratio = ob.value_crore / implied if implied else 0
        ok = 0.90 <= ratio <= 1.10
        msgs.append(
            f"  {'PASS' if ok else 'FAIL'}  {sym:10s} csr={ob.value_crore:>12,.4f}cr "
            f"vs 2%*avgPAT={implied:>12,.4f}cr  ratio={ratio:>7.3f}"
            f"  [{ob.unit}/{avg.unit}]")
    return msgs


# ---------------------------------------------------------------- report

def run_sample() -> int:
    db = sqlite3.connect(SAMPLE / "index.db")
    rows = list(db.execute("""SELECT symbol,exchange,text_path,pages FROM docs
                              WHERE fetch_state='ok' AND doc_kind='annual_report'
                                AND text_path IS NOT NULL ORDER BY symbol"""))
    db.close()
    print(f"segmenting {len(rows)} annual reports\n")
    print(f"{'company':10s}{'ex':4s}{'pg':>5s}{'sects':>6s}{'kinds':>6s}"
          f"{'units':>6s}  unit mix")
    all_facts: list[Fact] = []
    section_hits: dict[str, int] = {}
    multi_unit = 0
    for sym, ex, tp, pages in rows:
        d = parse(Path(tp))
        kinds = {s.name for s in d.sections}
        for k in kinds:
            section_hits[k] = section_hits.get(k, 0) + 1
        mix: dict[str, int] = {}
        for u in d.units:
            mix[u.unit] = mix.get(u.unit, 0) + 1
        if len(mix) > 1:
            multi_unit += 1
        all_facts += extract(d)
        top = sorted(mix.items(), key=lambda x: -x[1])[:4]
        print(f"  {sym:8s}{ex:4s}{pages:>5}{len(d.sections):>6}{len(kinds):>6}"
              f"{len(d.units):>6}  {top}")

    print(f"\n=== SECTION COVERAGE (of {len(rows)} reports) ===")
    for k, v in sorted(section_hits.items(), key=lambda x: -x[1]):
        bar = "#" * int(20 * v / len(rows))
        print(f"  {k:20s} {v:>3}/{len(rows)}  {bar}")
    print(f"\n  documents declaring MORE THAN ONE unit: {multi_unit}/{len(rows)}"
          "   <- this is why unit must be resolved per-position")

    print(f"\n=== FACTS EXTRACTED: {len(all_facts)} ===")
    for f in sorted(all_facts, key=lambda x: (x.symbol, x.field)):
        vc = (f"{f.value_crore:,.4f} cr" if f.value_crore is not None
              else ("—" if f.kind != "currency" else "UNRESOLVED"))
        print(f"  {f.symbol:10s}{f.field:32s}{f.value_raw:>14,.2f} {f.unit:8s}"
              f"-> {vc:>15s}  {f.confidence:<10s} p{f.page}")
        print(f"      {f.unit_source} | {f.quote[:88]}")

    print("\n=== VALIDATION: CSR obligation == 2% of average net profit ===")
    print("    (both figures are printed in the same annexure — the filing")
    print("     proves itself, so a mismatch means a unit was resolved wrongly)")
    v = validate(all_facts)
    print("\n".join(v) if v else "    (no company had both figures extracted)")
    npass = sum(1 for x in v if "PASS" in x)
    print(f"\n    {npass}/{len(v)} passed")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true")
    a = ap.parse_args()
    raise SystemExit(run_sample() if a.sample else run_sample())
