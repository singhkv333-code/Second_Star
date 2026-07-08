"""Known-date macro-event calendar (2026).

The single source of truth for *when* a macro event fires. The verifier
reads the *outcome*; this module only says "around now, an rbi_mpc
decision is due — open the verify window".

Dates are hardcoded for 2026 because there is no machine feed for next
year's central-bank calendars. They are accurate UTC announcement
times. ANNUAL REFRESH CHORE: when RBI / the Fed publish the following
year's schedule, add rows here. Staleness is fail-safe — if a date is
wrong, the verify window simply doesn't open at the right time, so a
trigger fires late or not at all; it never false-fires (the verifier
reads the real release before firing).

Times: RBI MPC outcome ≈ 10:00 IST (04:30 UTC); FOMC decision 14:00 ET
(18:00 UTC in EDT, 19:00 UTC in EST); US CPI 08:30 ET; India CPI
≈ 16:00 IST (10:30 UTC). CPI monthly dates marked APPROX need a refresh
against the official BLS / MOSPI schedule.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


def _utc(y: int, mo: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


@dataclass(frozen=True)
class MacroEventDef:
    kind: str
    fire_at_utc: datetime
    verify_window_minutes: int
    source_of_truth_id: str  # == kind (FK into source_of_truth table)
    label: str

    @property
    def window_end_utc(self) -> datetime:
        return self.fire_at_utc + timedelta(minutes=self.verify_window_minutes)

    def instance_key(self) -> str:
        """Stable per-occurrence id for the fire-once latch
        (e.g. 'rbi_mpc:2026-06-06')."""
        return f"{self.kind}:{self.fire_at_utc.date().isoformat()}"


# ── RBI MPC outcome days (≈10:00 IST = 04:30 UTC) ────────────────────
_RBI_MPC: list[MacroEventDef] = [
    MacroEventDef("rbi_mpc", _utc(2026, 2, 6, 4, 30), 240, "rbi_mpc", "RBI MPC Outcome"),
    MacroEventDef("rbi_mpc", _utc(2026, 4, 8, 4, 30), 240, "rbi_mpc", "RBI MPC Outcome"),
    MacroEventDef("rbi_mpc", _utc(2026, 6, 6, 4, 30), 240, "rbi_mpc", "RBI MPC Outcome"),
    MacroEventDef("rbi_mpc", _utc(2026, 8, 7, 4, 30), 240, "rbi_mpc", "RBI MPC Outcome"),
    MacroEventDef("rbi_mpc", _utc(2026, 10, 1, 4, 30), 240, "rbi_mpc", "RBI MPC Outcome"),
    MacroEventDef("rbi_mpc", _utc(2026, 12, 5, 4, 30), 240, "rbi_mpc", "RBI MPC Outcome"),
]

# ── FOMC decision days (14:00 ET; EST=19:00 UTC, EDT=18:00 UTC) ───────
_US_FOMC: list[MacroEventDef] = [
    MacroEventDef("us_fomc", _utc(2026, 1, 28, 19, 0), 240, "us_fomc", "FOMC Rate Decision"),
    MacroEventDef("us_fomc", _utc(2026, 3, 18, 18, 0), 240, "us_fomc", "FOMC Rate Decision"),
    MacroEventDef("us_fomc", _utc(2026, 4, 29, 18, 0), 240, "us_fomc", "FOMC Rate Decision"),
    MacroEventDef("us_fomc", _utc(2026, 6, 17, 18, 0), 240, "us_fomc", "FOMC Rate Decision"),
    MacroEventDef("us_fomc", _utc(2026, 7, 29, 18, 0), 240, "us_fomc", "FOMC Rate Decision"),
    MacroEventDef("us_fomc", _utc(2026, 9, 16, 18, 0), 240, "us_fomc", "FOMC Rate Decision"),
    MacroEventDef("us_fomc", _utc(2026, 10, 28, 18, 0), 240, "us_fomc", "FOMC Rate Decision"),
    MacroEventDef("us_fomc", _utc(2026, 12, 9, 19, 0), 240, "us_fomc", "FOMC Rate Decision"),
]

# ── US CPI prints (08:30 ET). EST=13:30 UTC, EDT=12:30 UTC. ───────────
# Feb–Jul confirmed from the BLS schedule; Aug–Dec APPROX (2nd week),
# refresh against bls.gov/schedule/news_release/cpi.htm.
_US_CPI: list[MacroEventDef] = [
    MacroEventDef("us_cpi", _utc(2026, 2, 11, 13, 30), 180, "us_cpi", "US CPI Print"),
    MacroEventDef("us_cpi", _utc(2026, 3, 11, 12, 30), 180, "us_cpi", "US CPI Print"),
    MacroEventDef("us_cpi", _utc(2026, 4, 10, 12, 30), 180, "us_cpi", "US CPI Print"),
    MacroEventDef("us_cpi", _utc(2026, 5, 12, 12, 30), 180, "us_cpi", "US CPI Print"),
    MacroEventDef("us_cpi", _utc(2026, 6, 10, 12, 30), 180, "us_cpi", "US CPI Print"),
    MacroEventDef("us_cpi", _utc(2026, 7, 14, 12, 30), 180, "us_cpi", "US CPI Print"),
    MacroEventDef("us_cpi", _utc(2026, 8, 12, 12, 30), 180, "us_cpi", "US CPI Print (APPROX)"),
    MacroEventDef("us_cpi", _utc(2026, 9, 11, 12, 30), 180, "us_cpi", "US CPI Print (APPROX)"),
    MacroEventDef("us_cpi", _utc(2026, 10, 13, 12, 30), 180, "us_cpi", "US CPI Print (APPROX)"),
    MacroEventDef("us_cpi", _utc(2026, 11, 13, 13, 30), 180, "us_cpi", "US CPI Print (APPROX)"),
    MacroEventDef("us_cpi", _utc(2026, 12, 10, 13, 30), 180, "us_cpi", "US CPI Print (APPROX)"),
]

# ── India CPI prints (≈16:00 IST = 10:30 UTC, ~12th). All APPROX. ────
# MOSPI releases on the 12th or next working day; refresh against
# mospi.gov.in's release calendar.
_INDIA_CPI: list[MacroEventDef] = [
    MacroEventDef("india_cpi", _utc(2026, 7, 13, 10, 30), 240, "india_cpi", "India CPI Print (APPROX)"),
    MacroEventDef("india_cpi", _utc(2026, 8, 12, 10, 30), 240, "india_cpi", "India CPI Print (APPROX)"),
    MacroEventDef("india_cpi", _utc(2026, 9, 14, 10, 30), 240, "india_cpi", "India CPI Print (APPROX)"),
    MacroEventDef("india_cpi", _utc(2026, 10, 12, 10, 30), 240, "india_cpi", "India CPI Print (APPROX)"),
    MacroEventDef("india_cpi", _utc(2026, 11, 12, 10, 30), 240, "india_cpi", "India CPI Print (APPROX)"),
    MacroEventDef("india_cpi", _utc(2026, 12, 14, 10, 30), 240, "india_cpi", "India CPI Print (APPROX)"),
]


_REGISTRY: dict[str, list[MacroEventDef]] = {
    "rbi_mpc": _RBI_MPC,
    "us_fomc": _US_FOMC,
    "us_cpi": _US_CPI,
    "india_cpi": _INDIA_CPI,
}


def events_for_kind(kind: str) -> list[MacroEventDef]:
    """All known occurrences for a kind, sorted by fire time."""
    return sorted(_REGISTRY.get(kind, []), key=lambda e: e.fire_at_utc)


def due_event(kind: str, now: datetime) -> MacroEventDef | None:
    """The occurrence whose verify window currently contains ``now``,
    i.e. fire_at <= now <= fire_at + verify_window. None if no event is
    in its window right now. If multiple overlap (they shouldn't), the
    earliest is returned."""
    for ev in events_for_kind(kind):
        if ev.fire_at_utc <= now <= ev.window_end_utc:
            return ev
    return None


def next_event(kind: str, after: datetime) -> MacroEventDef | None:
    """The next upcoming occurrence strictly after ``after`` (used by the
    FE calendar / draft preview)."""
    for ev in events_for_kind(kind):
        if ev.fire_at_utc > after:
            return ev
    return None
