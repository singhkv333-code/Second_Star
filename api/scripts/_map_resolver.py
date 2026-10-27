"""Name -> yfinance symbol resolver for no-ticker companies. Shared by the
test harness and the batch mapper. Pure-ish: only depends on yfinance.

Strategy:
  - Reconstruct a clean, spaced full company name by aligning the (often
    truncated) Moneycontrol display name against the full `company_slug`.
  - yf.Search() the reconstructed name; keep Indian-exchange EQUITY quotes
    (.NS preferred, .BO fallback).
  - Validate by fuzzy-matching the Yahoo result name against the slug; accept
    only above a threshold so wrong matches don't pollute the data.
"""
from __future__ import annotations
import re
import difflib


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def spaced_name(display: str, slug: str) -> str:
    """Reconstruct a spaced full name by aligning display words to the slug.

    'Indo Rama Text' + 'indoramatextiles' -> 'indo rama textiles'.
    Falls back to the bare display words if alignment breaks.
    """
    words = re.findall(r"[a-z0-9]+", (display or "").lower())
    sl = norm(slug)
    if not words:
        return " ".join(re.findall(r"[a-z0-9]+", (slug or "").lower())) or (display or "")
    if not sl:
        return " ".join(words)
    out: list[str] = []
    i = 0
    for j, w in enumerate(words):
        rest = sl[i:]
        if j == len(words) - 1:
            # last display word is the one most likely truncated: take the
            # whole slug tail when it starts with this word's leading chars.
            if rest.startswith(w[: min(len(w), 3)]):
                out.append(rest)
                i = len(sl)
            else:
                out.append(w)
        else:
            if rest.startswith(w):
                out.append(w)
                i += len(w)
            else:
                # misaligned (abbreviation like 'Mfg'); give up on reconstruction
                return " ".join(words)
    if i < len(sl):
        out.append(sl[i:])
    return " ".join(out)


def score(slug: str, display: str, yahoo_name: str) -> float:
    """Similarity of a Yahoo result name to the source company (0..1)."""
    a = norm(slug) or norm(display)
    b = norm(yahoo_name)
    if not a or not b:
        return 0.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    # Truncation case: source is a prefix of the (fuller) Yahoo name.
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 6 and longer.startswith(shorter):
        r = max(r, 0.85)
    return r


_GENERIC_TAIL = (
    "limited", "ltd", "india", "industries", "services", "company",
    "corporation", "enterprises", "private", "pvt",
)


def candidate_queries(display: str, slug: str) -> list[str]:
    """A few query variants, most-specific first, de-duplicated."""
    qs: list[str] = []
    recon = spaced_name(display, slug)
    if recon:
        qs.append(recon)
    # plain display words (avoids a glued slug tail like 'bioconindia')
    dwords = " ".join(re.findall(r"[a-z0-9]+", (display or "").lower()))
    if dwords:
        qs.append(dwords)
    # reconstruction with a glued generic suffix peeled off the last token
    toks = recon.split()
    if toks:
        last = toks[-1]
        for g in _GENERIC_TAIL:
            if last.endswith(g) and len(last) > len(g) + 2:
                qs.append(" ".join(toks[:-1] + [last[: -len(g)]]))
                break
    # de-dupe preserving order
    seen, out = set(), []
    for q in qs:
        if q and q not in seen:
            seen.add(q)
            out.append(q)
    return out


def resolve(display: str, slug: str, yf, *, threshold: float = 0.62,
            max_results: int = 8) -> dict | None:
    """Return best validated match dict or None. Does NOT fetch .info."""
    best = None
    for query in candidate_queries(display, slug):
        try:
            quotes = yf.Search(query, max_results=max_results).quotes or []
        except Exception:
            continue
        for q in quotes:
            sym = q.get("symbol") or ""
            if not sym.endswith((".NS", ".BO")) or q.get("quoteType") != "EQUITY":
                continue
            yname = q.get("longname") or q.get("shortname") or ""
            sc = score(slug, display, yname)
            rank = sc + (0.03 if sym.endswith(".NS") else 0.0)
            if best is None or rank > best["_rank"]:
                best = {"symbol": sym, "yahoo_name": yname, "score": round(sc, 3),
                        "query": query, "_rank": rank}
        # early exit on a strong .NS hit to save calls
        if best and best["score"] >= 0.9:
            break
    if best and best["score"] >= threshold:
        best.pop("_rank", None)
        return best
    return None
