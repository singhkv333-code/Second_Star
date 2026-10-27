"""Source credibility tiers for news-driven triggers.

Hardcoded for the demo per the implementation guideline. The keys must
match NewsAPI's ``source.id`` values (lowercase, kebab-case) so the
``score_source`` helper can look them up directly.
"""
from __future__ import annotations


SOURCE_TIERS: dict[str, float] = {
    "reuters": 0.95,
    "bloomberg": 0.95,
    "financial-times": 0.93,
    "the-wall-street-journal": 0.93,
    "associated-press": 0.92,
    "the-hindu": 0.85,
    "the-economist": 0.88,
    "business-standard": 0.83,
    "the-times-of-india": 0.82,
    "cnbc": 0.84,
    "moneycontrol": 0.80,
    "mint": 0.82,
    "google-news": 0.65,
    "yahoo-news": 0.62,
}


DEFAULT_CREDIBILITY: float = 0.55


# Map NewsAPI source ids to the canonical brand domain. The frontend
# uses this domain to render a publisher logo (e.g. via clearbit's
# logo CDN); we just expose the domain string and leave URL templating
# to the caller. Keys are deliberately the same kebab-case slugs as
# ``SOURCE_TIERS`` so a single lookup covers both score and logo.
SOURCE_BRAND_DOMAIN: dict[str, str] = {
    "reuters": "reuters.com",
    "bloomberg": "bloomberg.com",
    "financial-times": "ft.com",
    "the-wall-street-journal": "wsj.com",
    "associated-press": "apnews.com",
    "the-hindu": "thehindu.com",
    "the-economist": "economist.com",
    "business-standard": "business-standard.com",
    "the-times-of-india": "timesofindia.indiatimes.com",
    "cnbc": "cnbc.com",
    "moneycontrol": "moneycontrol.com",
    "mint": "livemint.com",
    "google-news": "news.google.com",
    "yahoo-news": "news.yahoo.com",
}


def _normalise(value: str) -> str:
    """Lower-case and kebab-case a free-form source name.

    NewsAPI gives us both ``source.id`` (slug) and ``source.name``
    (display). If the caller hands us a display name like
    "The Wall Street Journal", lower-casing and replacing spaces yields
    "the-wall-street-journal", which hits our table. The same
    transformation is a no-op for already-slug ids.
    """
    s = (value or "").strip().lower()
    if not s:
        return ""
    # Replace any run of whitespace / underscores with a single dash.
    out_chars: list[str] = []
    last_dash = True
    for ch in s:
        if ch.isalnum():
            out_chars.append(ch)
            last_dash = False
        elif ch in (" ", "\t", "_", "-", "."):
            if not last_dash:
                out_chars.append("-")
                last_dash = True
        # Drop everything else
    out = "".join(out_chars).strip("-")
    return out


def score_source(source_id_or_name: str) -> float:
    """Return the credibility score for a source.

    Accepts either a NewsAPI source id (e.g. ``"reuters"``) or a
    display name (e.g. ``"Reuters"``, ``"The Wall Street Journal"``).
    Unknown sources fall back to ``DEFAULT_CREDIBILITY`` (0.55).
    """
    key = _normalise(source_id_or_name)
    if not key:
        return DEFAULT_CREDIBILITY
    if key in SOURCE_TIERS:
        return SOURCE_TIERS[key]
    # Try a couple of common abbreviations / aliases.
    aliases = {
        "wsj": "the-wall-street-journal",
        "ft": "financial-times",
        "ap": "associated-press",
        "toi": "the-times-of-india",
        "bs": "business-standard",
    }
    aliased = aliases.get(key)
    if aliased and aliased in SOURCE_TIERS:
        return SOURCE_TIERS[aliased]
    return DEFAULT_CREDIBILITY


def source_brand_domain(source_id: str) -> str | None:
    """Return the canonical brand domain for a NewsAPI source.

    Accepts either a slug id (``"reuters"``) or a display name
    (``"Reuters"``, ``"The Wall Street Journal"``) — same normalisation
    as ``score_source``. Returns ``None`` for unknown sources so the
    caller can omit the logo entirely rather than render a broken one.
    """
    key = _normalise(source_id)
    if not key:
        return None
    if key in SOURCE_BRAND_DOMAIN:
        return SOURCE_BRAND_DOMAIN[key]
    aliases = {
        "wsj": "the-wall-street-journal",
        "ft": "financial-times",
        "ap": "associated-press",
        "toi": "the-times-of-india",
        "bs": "business-standard",
    }
    aliased = aliases.get(key)
    if aliased and aliased in SOURCE_BRAND_DOMAIN:
        return SOURCE_BRAND_DOMAIN[aliased]
    return None
