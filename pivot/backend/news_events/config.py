"""Source registry for the news_events subsystem (Phase 1).

A single dict is the source of truth for which feeds we poll, at what
cadence, and which tier they count as. New sources land here. Polling
intervals are intentionally generous — Phase 1 is about proving the
firehose works, not minimising latency.

The five sources below are the Appendix-A subset that was verified
live from the research-time fetcher (RBI ×3, BBC World, Google News
RSS search). The Business Standard / ET / Mint / SEBI feeds will be
added in a follow-up once they've been verified from prod egress with
a browser User-Agent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SourceTier = Literal["tier1", "tier2", "tier3"]
SourceKind = Literal["rss", "telegram", "miniflux_webhook"]


@dataclass(frozen=True)
class SourceDef:
    """Static definition of a source. Runtime health lives in the
    ``news_source_health`` table; this dataclass is pure config."""

    source_id: str
    display_name: str
    feed_url: str
    tier: SourceTier
    # How often the poller fetches. RBI publishes on business hours
    # only, so 300s is plenty; Google News keyword feeds tick faster
    # and we keep them at 180s. Ignored for non-RSS sources (Telegram
    # is push-driven; Miniflux owns its own polling interval).
    poll_interval_seconds: int
    enabled: bool = True
    # Some feeds carry a long base description that's useful as
    # context for Tier-2/3 classification. Phase 1 stores it but
    # doesn't consume it.
    notes: str | None = None
    # Phase 7 — transport family. ``rss`` is the Phase-1 default
    # (in-process poller). ``telegram`` uses a long-lived Telethon
    # client. ``miniflux_webhook`` is a placeholder source-id for
    # entries that come in via the Miniflux HMAC webhook — the
    # actual feed name is set per-Miniflux-feed in Miniflux's UI.
    kind: SourceKind = "rss"
    # For ``kind='telegram'``, ``feed_url`` is interpreted as the
    # channel username or t.me link. We persist it under the same
    # field so the admin /sources view doesn't need a new column.


# ── Phase 1 source registry ──────────────────────────────────────────
#
# Edits to this dict take effect on the next backend restart (the
# poller reads it at job-registration time). A future phase will move
# it to the DB so per-source toggles are possible without a restart.

_REGISTRY: dict[str, SourceDef] = {
    "rbi_press_releases": SourceDef(
        source_id="rbi_press_releases",
        display_name="RBI Press Releases",
        feed_url="https://www.rbi.org.in/pressreleases_rss.xml",
        tier="tier1",
        poll_interval_seconds=300,
        notes="Primary source for RBI policy actions, repo-rate moves, "
        "monetary policy committee outcomes.",
    ),
    "rbi_notifications": SourceDef(
        source_id="rbi_notifications",
        display_name="RBI Notifications",
        feed_url="https://www.rbi.org.in/notifications_rss.xml",
        tier="tier1",
        poll_interval_seconds=600,
        notes="Lower volume than press releases — circulars and regulator "
        "directions.",
    ),
    "rbi_speeches": SourceDef(
        source_id="rbi_speeches",
        display_name="RBI Speeches",
        feed_url="https://www.rbi.org.in/speeches_rss.xml",
        tier="tier1",
        poll_interval_seconds=900,
        notes="Governor / DG speeches. Early signal on policy direction.",
    ),
    "bbc_world": SourceDef(
        source_id="bbc_world",
        display_name="BBC World News",
        feed_url="https://feeds.bbci.co.uk/news/world/rss.xml",
        tier="tier3",
        poll_interval_seconds=180,
        notes="Global news cross-check for Tier-3 geopolitical events.",
    ),
    "google_news_search_india_markets": SourceDef(
        source_id="google_news_search_india_markets",
        display_name="Google News — India Markets keyword",
        feed_url=(
            "https://news.google.com/rss/search?"
            "q=India+stock+market+OR+sensex+OR+nifty&hl=en-IN&gl=IN&ceid=IN:en"
        ),
        tier="tier2",
        poll_interval_seconds=300,
        notes="Aggregator-built feed. Useful for keyword diversity during "
        "Tier-2 event watches; lower per-source credibility than the "
        "primary publishers it cites.",
    ),

    # ── Macro-event verification sources (consumed on-demand) ─────
    #
    # These back ``trigger.scheduled_macro`` outcome verification
    # (backend/macro_events/). They are read on demand by the verifier
    # via ``get_source()`` + ``RSSAdapter`` — NOT polled by the firehose
    # poller, so they carry ``enabled=False`` (the poller filters on
    # ``enabled_sources()``; ``get_source()`` returns them regardless).
    "fed_press_monetary": SourceDef(
        source_id="fed_press_monetary",
        display_name="US Federal Reserve — Monetary Press Releases",
        feed_url="https://www.federalreserve.gov/feeds/press_monetary.xml",
        tier="tier1",
        poll_interval_seconds=900,
        enabled=False,
        notes="Official Fed monetary press-release feed. FOMC rate-"
        "decision statements land here; read on-demand by the macro "
        "verifier, not the firehose.",
    ),
    "google_news_india_cpi": SourceDef(
        source_id="google_news_india_cpi",
        display_name="Google News — India CPI / retail inflation",
        feed_url=(
            "https://news.google.com/rss/search?"
            "q=India+CPI+OR+%22retail+inflation%22+when:7d"
            "&hl=en-IN&gl=IN&ceid=IN:en"
        ),
        tier="tier2",
        poll_interval_seconds=900,
        enabled=False,
        notes="MOSPI has no machine feed; the macro verifier reads this "
        "keyword RSS to extract the India CPI figure. Verification-only.",
    ),
    "google_news_us_cpi": SourceDef(
        source_id="google_news_us_cpi",
        display_name="Google News — US CPI / inflation",
        feed_url=(
            "https://news.google.com/rss/search?"
            "q=US+CPI+OR+%22consumer+price+index%22+inflation+when:7d"
            "&hl=en-US&gl=US&ceid=US:en"
        ),
        tier="tier2",
        poll_interval_seconds=900,
        enabled=False,
        notes="No clean BLS feed; the macro verifier reads this keyword "
        "RSS to extract the US CPI figure. Verification-only.",
    ),

    # ── Phase 7 — Tier-A Telegram channels ────────────────────────
    #
    # Public Indian financial-press channels. Pushed in 1-3 s via
    # Telethon (see backend/news_events/sources/telegram_source.py
    # + backend/news_events/workers/telegram_worker.py). Only
    # activated when both ``news_events_enabled`` AND
    # ``telegram_enabled`` are true and a valid .session file exists.
    #
    # Username verification (2026-05-21): the diagnostic in Phase 7
    # confirmed `@livemint` and `@NDTVProfit` are live + active on
    # Telegram. The four channels marked enabled=False below either
    # don't exist under those usernames or are dormant; left in the
    # registry as a record so future contributors don't re-add them
    # without verifying. Anyone replacing them must search Telegram
    # directly, then update both ``feed_url`` AND flip ``enabled=True``.
    "tg_livemint": SourceDef(
        source_id="tg_livemint",
        display_name="Mint — Business News (Telegram)",
        feed_url="https://t.me/livemint",
        tier="tier2",
        poll_interval_seconds=0,
        kind="telegram",
        notes="Official Mint channel. High-cadence breaking news. "
        "Verified live 2026-05-21.",
    ),
    "tg_ndtv_profit": SourceDef(
        source_id="tg_ndtv_profit",
        display_name="NDTV Profit (Telegram)",
        feed_url="https://t.me/NDTVProfit",
        tier="tier2",
        poll_interval_seconds=0,
        kind="telegram",
        notes="Indian business news, formerly Bloomberg Quint / BQ Prime. "
        "Verified live 2026-05-21.",
    ),
    "tg_etmarkets": SourceDef(
        source_id="tg_etmarkets",
        display_name="ET Markets (Telegram) — UNVERIFIED",
        feed_url="https://t.me/ETMarkets",
        tier="tier2",
        poll_interval_seconds=0,
        kind="telegram",
        enabled=False,
        notes="Original username dormant (latest msg ~3 years old). "
        "Real Economic Times Markets channel may live under a "
        "different username; verify on Telegram before flipping enabled.",
    ),
    "tg_reuters_india": SourceDef(
        source_id="tg_reuters_india",
        display_name="Reuters India (Telegram) — USERNAME NOT FOUND",
        feed_url="https://t.me/ReutersIndia",
        tier="tier3",
        poll_interval_seconds=0,
        kind="telegram",
        enabled=False,
        notes="`@ReutersIndia` returned UsernameInvalidError on lookup. "
        "Reuters does not appear to run an official Indian Telegram "
        "channel under this slug. Search the wider Reuters channel set "
        "before re-enabling.",
    ),
    "tg_pib_india": SourceDef(
        source_id="tg_pib_india",
        display_name="PIB India (Telegram) — USERNAME NOT FOUND",
        feed_url="https://t.me/PIB_India",
        tier="tier1",
        poll_interval_seconds=0,
        kind="telegram",
        enabled=False,
        notes="`@PIB_India` returned UsernameInvalidError. Try "
        "`@pibhq` or `@PIB_India_Official` (must verify on Telegram first).",
    ),
    "tg_ani_news": SourceDef(
        source_id="tg_ani_news",
        display_name="ANI News (Telegram) — USERNAME NOT FOUND",
        feed_url="https://t.me/ANI_news",
        tier="tier3",
        poll_interval_seconds=0,
        kind="telegram",
        enabled=False,
        notes="`@ANI_news` returned UsernameInvalidError. May exist under "
        "`@ANI_News` (case-sensitive) or another slug; verify first.",
    ),
}


def list_sources() -> list[SourceDef]:
    """Return all registered sources in deterministic order."""
    return sorted(_REGISTRY.values(), key=lambda s: s.source_id)


def get_source(source_id: str) -> SourceDef | None:
    """Lookup by source_id. Returns None if not registered."""
    return _REGISTRY.get(source_id)


def enabled_sources() -> list[SourceDef]:
    """Enabled subset of the registry. The poller filters on this."""
    return [s for s in list_sources() if s.enabled]
