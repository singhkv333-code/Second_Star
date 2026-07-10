"""F&O instrument master refresh + dynamic universe selection (P0).

Source of record: the Kite instruments dump (regenerated once a day,
~08:30 IST). ``refresh_instrument_master`` pulls the NFO / BFO / MCX
derivative segments and upserts ``instrument_master``;
``select_active_universe`` then derives the day's tradable universe from
liquidity evidence — percentile thresholds over an ATM liquidity sample,
NO hardcoded underlying lists anywhere. New expiries, strike-ladder
extensions and newly-listed F&O underlyings appear automatically as rows
with ``first_seen == today``.

Mock mode (no Kite creds): a deterministic synthetic dump is generated
relative to *today* — NIFTY weekly Tuesdays + monthlies, BANKNIFTY /
RELIANCE monthlies, CRUDEOIL on MCX — so the entire F&O stack runs in
dev and tests without credentials and without hardcoded dates.

Expiry-kind classification is DERIVED (never parsed from symbols, never
a hardcoded weekday): within an underlying's option expiries, an expiry
is "monthly" iff it is the last expiry of its calendar month, else
"weekly". This survives every exchange expiry-day reshuffle (NSE moved
to Tuesday/NIFTY-only weeklies in Sep 2025; BSE differs) because the
dump already reflects whatever the current rule is.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from backend.kite.system import get_system_kite
from backend.models import InstrumentMaster, OptionUniverse

logger = logging.getLogger(__name__)

# Derivative segments we master. Equity cash rows are NOT mirrored here —
# the equity path already has its own instrument handling (kite/ticker.py).
_SEGMENTS = ("NFO", "BFO", "MCX")
_OPT_SEGMENTS = ("NFO-OPT", "BFO-OPT", "MCX-OPT")

# Liquidity-selection knobs (percentile-based, self-adjusting — see plan).
_SELECT_PERCENTILE = 40.0       # keep underlyings ≥ this percentile of score
_MAX_SPREAD_PCT_ATM = 0.05      # reject if ATM spread is wider than 5%
_ATM_SAMPLE_HALF_WIDTH = 5      # strikes each side of ATM for the sample


# ── Mock dump (deterministic, date-relative — NO hardcoded dates) ────


def _next_weekday(from_day: date, weekday: int) -> date:
    """Next date with ``weekday`` (Mon=0) strictly after ``from_day``."""
    days = (weekday - from_day.weekday()) % 7
    return from_day + timedelta(days=days or 7)


def _last_weekday_of_month(year: int, month: int, weekday: int) -> date:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _monthly_expiries(today: date, weekday: int, count: int) -> list[date]:
    out: list[date] = []
    y, m = today.year, today.month
    while len(out) < count:
        exp = _last_weekday_of_month(y, m, weekday)
        if exp >= today:
            out.append(exp)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _mock_strike_ladder(center: float, step: float, half_width: int) -> list[float]:
    atm = round(center / step) * step
    return [atm + i * step for i in range(-half_width, half_width + 1)]


def _mock_instrument_rows(today: date) -> list[dict[str, Any]]:
    """Synthetic Kite-dump rows shaped exactly like kite.instruments()."""
    # (underlying, exchange, opt_segment, spot, step, lot, expiries)
    nifty_weeklies = [_next_weekday(today - timedelta(days=1), 1)]  # Tuesdays
    for _ in range(2):
        nifty_weeklies.append(_next_weekday(nifty_weeklies[-1], 1))
    specs = [
        ("NIFTY", "NFO", "NFO-OPT", 23456.0, 50.0, 65,
         sorted(set(nifty_weeklies) | set(_monthly_expiries(today, 1, 2)))),
        ("BANKNIFTY", "NFO", "NFO-OPT", 51200.0, 100.0, 30,
         _monthly_expiries(today, 1, 2)),
        ("RELIANCE", "NFO", "NFO-OPT", 1320.0, 20.0, 500,
         _monthly_expiries(today, 1, 2)),
        ("SENSEX", "BFO", "BFO-OPT", 77000.0, 100.0, 20,
         _monthly_expiries(today, 3, 2)),     # BSE Thursdays
        ("CRUDEOIL", "MCX", "MCX-OPT", 5800.0, 50.0, 100,
         _monthly_expiries(today, 1, 2)),
    ]
    rows: list[dict[str, Any]] = []
    token = 10_000_000
    for underlying, exch, seg, spot, step, lot, expiries in specs:
        fut_seg = seg.replace("-OPT", "-FUT")
        for expiry in expiries:
            token += 1
            rows.append({
                "instrument_token": token, "exchange_token": token // 256,
                "tradingsymbol": f"{underlying}{expiry:%y%b}FUT".upper(),
                "name": underlying, "last_price": spot,
                "expiry": expiry, "strike": 0.0, "tick_size": 0.05,
                "lot_size": lot, "instrument_type": "FUT",
                "segment": fut_seg, "exchange": exch,
            })
            for strike in _mock_strike_ladder(spot, step, 15):
                for kind in ("CE", "PE"):
                    token += 1
                    rows.append({
                        "instrument_token": token,
                        "exchange_token": token // 256,
                        "tradingsymbol": (
                            f"{underlying}{expiry:%y%b}{int(strike)}{kind}".upper()
                        ),
                        "name": underlying, "last_price": 0.0,
                        "expiry": expiry, "strike": float(strike),
                        "tick_size": 0.05, "lot_size": lot,
                        "instrument_type": kind, "segment": seg,
                        "exchange": exch,
                    })
    return rows


# ── Refresh ──────────────────────────────────────────────────────────


def _coerce_expiry(raw: Any) -> Optional[date]:
    if not raw:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _classify_expiry_kinds(
    expiries_by_underlying: dict[tuple[str, str], set[date]],
) -> dict[tuple[str, str, date], str]:
    """expiry → weekly|monthly, derived from date grouping (see module doc)."""
    kinds: dict[tuple[str, str, date], str] = {}
    for (underlying, segment), expiries in expiries_by_underlying.items():
        by_month: dict[tuple[int, int], list[date]] = defaultdict(list)
        for exp in expiries:
            by_month[(exp.year, exp.month)].append(exp)
        for month_expiries in by_month.values():
            last = max(month_expiries)
            for exp in month_expiries:
                kinds[(underlying, segment, exp)] = (
                    "monthly" if exp == last else "weekly"
                )
    return kinds


def refresh_instrument_master(db: Session, *, today: Optional[date] = None) -> dict:
    """Pull the daily dump and upsert ``instrument_master``. Idempotent —
    safe to re-run any number of times a day. Returns counts."""
    today = today or date.today()

    raw_rows: list[dict[str, Any]] = []
    source = "kite"
    kite = get_system_kite(db)
    if kite is None:
        raw_rows = _mock_instrument_rows(today)
        source = "mock"
    else:
        for seg in _SEGMENTS:
            try:
                raw_rows.extend(kite.instruments(seg) or [])
            except Exception as exc:
                logger.warning("[instrument-master] %s dump failed: %s", seg, exc)
        if not raw_rows:
            logger.warning(
                "[instrument-master] live dump empty; falling back to mock"
            )
            raw_rows = _mock_instrument_rows(today)
            source = "mock_fallback"

    # Derive expiry kinds across the full dump before writing rows.
    expiries_by_underlying: dict[tuple[str, str], set[date]] = defaultdict(set)
    for row in raw_rows:
        if row.get("instrument_type") in ("CE", "PE"):
            exp = _coerce_expiry(row.get("expiry"))
            underlying = (row.get("name") or "").strip().upper()
            if exp and underlying:
                expiries_by_underlying[(underlying, row.get("segment", ""))].add(exp)
    kinds = _classify_expiry_kinds(expiries_by_underlying)

    existing_tokens = {
        t for (t,) in db.query(InstrumentMaster.instrument_token).all()
    }
    inserted = updated = 0
    for row in raw_rows:
        itype = row.get("instrument_type")
        if itype not in ("CE", "PE", "FUT"):
            continue  # derivatives only; equity cash stays in ticker.py
        token = int(row.get("instrument_token") or 0)
        if not token:
            continue
        underlying = (row.get("name") or "").strip().upper()
        if not underlying:
            # Rare junk rows in the dump — skip, never guess.
            continue
        expiry = _coerce_expiry(row.get("expiry"))
        segment = str(row.get("segment") or "")
        values = {
            "exchange_token": int(row.get("exchange_token") or 0) or None,
            "tradingsymbol": str(row.get("tradingsymbol") or "")[:64],
            "name": underlying[:64],
            "underlying": underlying[:40],
            "exchange": str(row.get("exchange") or "")[:8],
            "segment": segment[:16],
            "instrument_type": str(itype)[:4],
            "strike": float(row.get("strike") or 0.0) or None,
            "expiry": expiry,
            "expiry_kind": (
                kinds.get((underlying, segment, expiry))
                if itype in ("CE", "PE") and expiry else None
            ),
            "lot_size": int(row.get("lot_size") or 0) or None,
            "tick_size": float(row.get("tick_size") or 0.0) or None,
            "last_price": float(row.get("last_price") or 0.0) or None,
            "last_seen": today,
            "refreshed_on": today,
        }
        if token in existing_tokens:
            db.query(InstrumentMaster).filter(
                InstrumentMaster.instrument_token == token
            ).update(values, synchronize_session=False)
            updated += 1
        else:
            db.add(InstrumentMaster(
                instrument_token=token, first_seen=today, **values,
            ))
            existing_tokens.add(token)
            inserted += 1
    db.commit()
    # Purge stale rows after a HEALTHY real dump — instruments not present
    # in today's Kite dump (delisted contracts, and crucially the old
    # synthetic mock dump that otherwise lingers and contaminates the
    # option chain with garbage synthetic strikes/IVs). Guarded on a
    # substantial real dump so a thin/partial pull never wipes the master.
    purged = 0
    if source == "kite" and (inserted + updated) > 1000:
        # (a) Delisted contracts: not in today's real dump.
        purged = (
            db.query(InstrumentMaster)
            .filter(InstrumentMaster.last_seen < today)
            .delete(synchronize_session=False)
        )
        # (b) The synthetic mock dump: it uses a deterministic token band
        # starting at 10_000_000 (see _mock_instrument_rows) — real Kite
        # NFO/BFO/MCX tokens never fall in that narrow window. A mock
        # refresh (e.g. before the daily session is armed) re-stamps these
        # with last_seen=today, so (a) won't catch them; they then duplicate
        # every real contract on (strike, expiry) and the chain picks the
        # synthetic leg → garbage IV. Drop the whole synthetic band.
        purged += (
            db.query(InstrumentMaster)
            .filter(
                InstrumentMaster.instrument_token >= 10_000_000,
                InstrumentMaster.instrument_token < 10_002_000,
            )
            .delete(synchronize_session=False)
        )
        db.commit()
    counts = {
        "source": source, "inserted": inserted, "updated": updated,
        "purged": purged, "total_rows": len(raw_rows),
    }
    logger.info("[instrument-master] refresh %s", counts)
    return counts


# ── Read API (every lot/expiry/strike consumer goes through these) ───


def list_option_underlyings(db: Session) -> list[dict[str, str]]:
    rows = (
        db.query(
            InstrumentMaster.underlying,
            InstrumentMaster.segment,
            InstrumentMaster.exchange,
        )
        .filter(
            InstrumentMaster.segment.in_(_OPT_SEGMENTS),
            InstrumentMaster.expiry >= date.today(),
        )
        .distinct()
        .all()
    )
    return [
        {"underlying": u, "segment": s, "exchange": e} for u, s, e in rows
    ]


def list_expiries(
    db: Session, underlying: str, *, today: Optional[date] = None,
) -> list[dict[str, Any]]:
    """Tradable option expiries for an underlying, soonest first."""
    today = today or date.today()
    rows = (
        db.query(
            InstrumentMaster.expiry, InstrumentMaster.expiry_kind,
        )
        .filter(
            InstrumentMaster.underlying == underlying.strip().upper(),
            InstrumentMaster.instrument_type.in_(("CE", "PE")),
            InstrumentMaster.expiry >= today,
        )
        .distinct()
        .order_by(InstrumentMaster.expiry)
        .all()
    )
    return [
        {"expiry": exp.isoformat(), "kind": kind or "monthly"}
        for exp, kind in rows
    ]


def resolve_expiry(
    db: Session, underlying: str, expiry: Optional[str] = None,
) -> Optional[date]:
    """Nearest tradable expiry when ``expiry`` is None, else the parsed
    requested one (validated against the master)."""
    options = list_expiries(db, underlying)
    if not options:
        return None
    if expiry:
        want = str(expiry)[:10]
        for opt in options:
            if opt["expiry"] == want:
                return date.fromisoformat(want)
        return None
    return date.fromisoformat(options[0]["expiry"])


def get_lot_size(
    db: Session, underlying: str, expiry: Optional[date] = None,
) -> Optional[int]:
    """Contract lot size FROM THE MASTER — the only legal source.
    Per-expiry because lot revisions phase in by expiry (Dec'25/Jan'26)."""
    q = db.query(InstrumentMaster.lot_size).filter(
        InstrumentMaster.underlying == underlying.strip().upper(),
        InstrumentMaster.instrument_type.in_(("CE", "PE")),
        InstrumentMaster.lot_size.isnot(None),
    )
    if expiry is not None:
        q = q.filter(InstrumentMaster.expiry == expiry)
    row = q.order_by(InstrumentMaster.expiry).first()
    return int(row[0]) if row and row[0] else None


def chain_instruments(
    db: Session, underlying: str, expiry: date,
) -> list[InstrumentMaster]:
    """CE/PE rows for one (underlying, expiry), strike-ordered."""
    return (
        db.query(InstrumentMaster)
        .filter(
            InstrumentMaster.underlying == underlying.strip().upper(),
            InstrumentMaster.instrument_type.in_(("CE", "PE")),
            InstrumentMaster.expiry == expiry,
        )
        .order_by(InstrumentMaster.strike)
        .all()
    )


def future_instrument(
    db: Session, underlying: str, expiry: date,
) -> Optional[InstrumentMaster]:
    """Same-expiry future if listed, else the nearest future ≥ today."""
    exact = (
        db.query(InstrumentMaster)
        .filter(
            InstrumentMaster.underlying == underlying.strip().upper(),
            InstrumentMaster.instrument_type == "FUT",
            InstrumentMaster.expiry == expiry,
        )
        .first()
    )
    if exact:
        return exact
    return (
        db.query(InstrumentMaster)
        .filter(
            InstrumentMaster.underlying == underlying.strip().upper(),
            InstrumentMaster.instrument_type == "FUT",
            InstrumentMaster.expiry >= date.today(),
        )
        .order_by(InstrumentMaster.expiry)
        .first()
    )


def is_research_only(db: Session, underlying: str) -> bool:
    """True when execution is product-blocked for this underlying.
    Currently always False — MCX commodities are tradeable via
    register-not-execute (2026-06-29); kept for future non-tradeable segments.
    Falls back to the segment when no universe row exists yet."""
    row = (
        db.query(OptionUniverse)
        .filter(OptionUniverse.underlying == underlying.strip().upper())
        .order_by(OptionUniverse.as_of.desc())
        .first()
    )
    if row is not None:
        return bool(row.research_only)
    seg = (
        db.query(InstrumentMaster.segment)
        .filter(
            InstrumentMaster.underlying == underlying.strip().upper(),
            InstrumentMaster.segment.in_(_OPT_SEGMENTS),
        )
        .first()
    )
    return bool(seg and seg[0] == "MCX-OPT")


# ── Dynamic universe selection ───────────────────────────────────────


def _percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(ordered) - 1)
    frac = k - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def select_active_universe(
    db: Session, *, as_of: Optional[date] = None,
) -> list[OptionUniverse]:
    """Score every option underlying on front-expiry ATM liquidity and
    write the day's OptionUniverse rows. Percentile gate — the universe
    grows/shrinks with the market, no constants to maintain."""
    from backend.market.option_chain import get_chain  # lazy: avoids cycle

    as_of = as_of or date.today()
    candidates = list_option_underlyings(db)
    if not candidates:
        logger.warning("[universe] instrument master empty — run refresh first")
        return []

    import math

    sampled: list[dict[str, Any]] = []
    for cand in candidates:
        underlying = cand["underlying"]
        try:
            chain = get_chain(
                db, underlying, width=_ATM_SAMPLE_HALF_WIDTH,
            )
        except Exception as exc:
            logger.info("[universe] %s sample failed: %s", underlying, exc)
            chain = None
        if not chain or not chain.get("rows"):
            sampled.append({
                **cand, "avg_oi": 0.0, "avg_volume": 0.0,
                "spread_pct_atm": None, "score": 0.0,
            })
            continue
        ois: list[float] = []
        vols: list[float] = []
        spreads: list[float] = []
        atm = chain.get("atm_strike")
        for row in chain["rows"]:
            for side in ("ce", "pe"):
                q = row.get(side)
                if not q:
                    continue
                ois.append(float(q.get("oi") or 0.0))
                vols.append(float(q.get("volume") or 0.0))
                bid = float(q.get("bid") or 0.0)
                ask = float(q.get("ask") or 0.0)
                if atm and row["strike"] == atm and bid > 0 and ask >= bid:
                    spreads.append((ask - bid) / ((ask + bid) / 2.0))
        avg_oi = sum(ois) / len(ois) if ois else 0.0
        avg_vol = sum(vols) / len(vols) if vols else 0.0
        spread_atm = sum(spreads) / len(spreads) if spreads else None
        score = (
            math.log10(1.0 + avg_oi)
            + math.log10(1.0 + avg_vol)
            - 10.0 * (spread_atm if spread_atm is not None else _MAX_SPREAD_PCT_ATM)
        )
        sampled.append({
            **cand, "avg_oi": avg_oi, "avg_volume": avg_vol,
            "spread_pct_atm": spread_atm, "score": score,
        })

    cutoff = _percentile([s["score"] for s in sampled], _SELECT_PERCENTILE)
    out: list[OptionUniverse] = []
    for s in sampled:
        research_only = False  # MCX commodities are tradeable (register-not-execute)
        liquid = (
            s["score"] >= cutoff
            and s["avg_oi"] > 0.0
            and (
                s["spread_pct_atm"] is None
                or s["spread_pct_atm"] <= _MAX_SPREAD_PCT_ATM
            )
        )
        reason = (
            "mcx_research_only" if research_only
            else ("liquidity_ok" if liquid else "below_liquidity_percentile")
        )
        row = (
            db.query(OptionUniverse)
            .filter(
                OptionUniverse.underlying == s["underlying"],
                OptionUniverse.as_of == as_of,
            )
            .first()
        )
        if row is None:
            row = OptionUniverse(underlying=s["underlying"], as_of=as_of)
            db.add(row)
        row.segment = s["segment"]
        row.exchange = s["exchange"]
        row.avg_oi = s["avg_oi"]
        row.avg_volume = s["avg_volume"]
        row.spread_pct_atm = s["spread_pct_atm"]
        row.liquidity_score = s["score"]
        row.selected = liquid and not research_only
        row.research_only = research_only
        row.reason = reason
        out.append(row)
    db.commit()
    logger.info(
        "[universe] %s scored, %s selected, %s research-only",
        len(out), sum(1 for r in out if r.selected),
        sum(1 for r in out if r.research_only),
    )
    return out


async def refresh_instrument_master_job() -> None:
    """APScheduler entrypoint: daily dump refresh + universe selection."""
    import asyncio

    def _run() -> None:
        from backend.database import SessionLocal

        db = SessionLocal()
        try:
            refresh_instrument_master(db)
            select_active_universe(db)
        finally:
            db.close()

    try:
        await asyncio.to_thread(_run)
    except Exception:
        logger.exception("[instrument-master] daily refresh failed")
