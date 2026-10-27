"""Deterministic tool-arg repair.

Catches common minor mistakes the LLM emits (numeric strings, channel
typos, time-string crons) BEFORE Pydantic validation, so we don't
burn an LLM hop or surface a canned reject message for inputs that
have a clear deterministic fix.

WHY this is OK even though we explicitly forbid LLM retries on
validation failure: this isn't a retry. It's a single deterministic
transform applied once between "args from LLM" and "Pydantic
validate". No second LLM call, no token cost, sub-millisecond.

Two surfaces:
  - `repair_tool_args(tool_name, args)` for the top-level tool
    dispatcher (chat tools like place_market_order).
  - `repair_step_config(step_type, config)` for workflow step
    configs (action.place_order.quantity, notify.message.channel,
    trigger.schedule.cron).

Repair scope is intentionally small: numbers (textual numerals,
₹/lakh/crore suffixes, "10 shares"), channels (email/sms → push),
and weekday cron from short time strings. Everything else falls
through unchanged so existing valid args aren't disturbed.

Repair notes are logged via the standard logger and returned to the
caller, so we can audit how often repairs save a turn from rejection.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ── Numeric repair ──────────────────────────────────────────────────


_NUMBER_WORDS: dict[str, int] = {
    "zero": 0, "nil": 0, "none": 0,
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "twenty": 20, "twenty-five": 25,
    "thirty": 30, "fifty": 50, "hundred": 100,
    "thousand": 1000, "lakh": 100000, "crore": 10000000,
}


# Strip trailing unit words. "10 shares" / "5 units" / "3 lots"
_TRAIL_UNIT_RE = re.compile(
    r"\s+(?:shares?|units?|lots?|stocks?|qty|quantity|nos\.?|nos)\s*$",
    re.IGNORECASE,
)


def _repair_numeric(value: Any) -> Any:
    """Try to coerce value to a number. Returns the original value
    when it isn't repairable so the validator still surfaces the
    real error.
    """
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if not isinstance(value, str):
        return value

    s = value.strip()
    if not s:
        return value

    s_lc = s.lower()

    # Number-word form: "ten", "fifty", "one hundred".
    if s_lc in _NUMBER_WORDS:
        return _NUMBER_WORDS[s_lc]
    # Compound: "twenty five" → check by removing space/hyphen
    compact = s_lc.replace("-", " ")
    parts = compact.split()
    if len(parts) == 2 and parts[0] in _NUMBER_WORDS and parts[1] in _NUMBER_WORDS:
        a, b = _NUMBER_WORDS[parts[0]], _NUMBER_WORDS[parts[1]]
        # twenty + five = 25; one + hundred = 100; not 5+1=6
        if a >= 20 and b < 10:
            return a + b
        if b in (100, 1000, 100000, 10000000):
            return a * b

    # Strip rupee sign, commas, trailing unit words
    cleaned = s.replace("₹", "").replace(",", "")
    cleaned = _TRAIL_UNIT_RE.sub("", cleaned).strip()

    # Bare decimal/integer — bail out and let it through as-is by
    # converting to int/float
    try:
        if "." in cleaned:
            return float(cleaned)
        return int(cleaned)
    except ValueError:
        pass

    # Suffix multipliers: 1.5L, 2.3cr, 50k, 5m
    m = re.match(
        r"^([+-]?\d+(?:\.\d+)?)\s*"
        r"(k|thousand|m|mn|million|l|lac|lakh|cr|crore|bn|billion)\s*$",
        cleaned, re.IGNORECASE,
    )
    if m:
        try:
            n = float(m.group(1))
        except ValueError:
            return value
        suf = m.group(2).lower()
        mult = {
            "k": 1_000, "thousand": 1_000,
            "m": 1_000_000, "mn": 1_000_000, "million": 1_000_000,
            "l": 100_000, "lac": 100_000, "lakh": 100_000,
            "cr": 10_000_000, "crore": 10_000_000,
            "bn": 1_000_000_000, "billion": 1_000_000_000,
        }.get(suf, 1)
        n *= mult
        return int(n) if n == int(n) else n

    return value


# ── Cron repair ─────────────────────────────────────────────────────


_CLOCK_RE = re.compile(
    r"^\s*(\d{1,2})[:.\s]?(\d{2})?\s*(am|pm|hrs?|hours?)?\s*$",
    re.IGNORECASE,
)


def _time_to_weekday_cron(value: str) -> str | None:
    """Try '9:15' / '9:15 AM' / '15:30' → '15 9 * * 1-5' (weekday cron).

    Single-time strings only — multi-day or interval cron stays the
    LLM's responsibility. Returns None when the string isn't a clean
    HH:MM (case-insensitive AM/PM optional).
    """
    if not isinstance(value, str):
        return None
    # Bail if it already looks like a cron expression (5 space-
    # separated fields).
    if len(value.split()) >= 4:
        return None
    m = _CLOCK_RE.match(value)
    if not m:
        return None
    try:
        h = int(m.group(1))
    except (TypeError, ValueError):
        return None
    mn = int(m.group(2)) if m.group(2) else 0
    ampm = (m.group(3) or "").lower()
    if "pm" in ampm and h < 12:
        h += 12
    elif "am" in ampm and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= mn <= 59):
        return None
    return f"{mn} {h} * * 1-5"


# ── Channel repair ──────────────────────────────────────────────────


# Channels Pivot v1 doesn't deliver yet. Snap them to "push" (in-app)
# rather than reject — the user prefers a working agent over a hard
# error. The user-visible response separately tells them about the
# substitution (see the email-handling system prompt rule).
_NON_PUSH_CHANNELS: frozenset[str] = frozenset({
    "email", "mail", "e-mail",
    "sms", "text", "message",
    "whatsapp", "wa",
    "slack", "telegram", "tg",
    "in_app", "in-app",  # tolerate the "in_app" alias
})


# ── Field name registries ───────────────────────────────────────────


_NUMERIC_FIELDS: frozenset[str] = frozenset({
    "quantity", "qty", "shares", "limit",
    "total_inr", "notional_inr", "amount_inr", "amount",
    "trigger_price", "limit_price", "sl_price",
    "trigger_offset_pct", "sl_offset_pct", "offset_pct",
    "tax_slab", "value", "right",
    "period", "window_years",
})


# ── Public API ──────────────────────────────────────────────────────


def repair_tool_args(
    tool_name: str, args: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Repair top-level tool args. Returns (repaired, notes)."""
    if not args:
        return args or {}, []
    out: dict[str, Any] = dict(args)
    notes: list[str] = []

    # Numeric coercion on known numeric fields.
    for key, val in list(out.items()):
        if key in _NUMERIC_FIELDS:
            repaired = _repair_numeric(val)
            if repaired != val:
                notes.append(f"{key}: {val!r} -> {repaired!r}")
                out[key] = repaired

    # Channel collapse on notify-shaped tool calls.
    if isinstance(out.get("channel"), str):
        ch = out["channel"].strip().lower()
        if ch in _NON_PUSH_CHANNELS and ch != "push":
            notes.append(f"channel: {out['channel']!r} -> 'push' (v1 in-app only)")
            out["channel"] = "push"

    if notes:
        logger.info(
            "arg_repair tool=%s repairs=%s", tool_name, "; ".join(notes),
        )
    return out, notes


def repair_step_config(
    step_type: str, config: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Repair workflow step configs. Same semantics as repair_tool_args
    but covers cron-string repair for trigger.schedule.
    """
    if not config:
        return config or {}, []
    out: dict[str, Any] = dict(config)
    notes: list[str] = []

    for key, val in list(out.items()):
        if key in _NUMERIC_FIELDS:
            repaired = _repair_numeric(val)
            if repaired != val:
                notes.append(f"{key}: {val!r} -> {repaired!r}")
                out[key] = repaired

    if isinstance(out.get("channel"), str):
        ch = out["channel"].strip().lower()
        if ch in _NON_PUSH_CHANNELS and ch != "push":
            notes.append(f"channel: {out['channel']!r} -> 'push'")
            out["channel"] = "push"

    if step_type == "trigger.schedule":
        cron = out.get("cron")
        if isinstance(cron, str):
            repaired = _time_to_weekday_cron(cron)
            if repaired:
                notes.append(f"cron: {cron!r} -> {repaired!r}")
                out["cron"] = repaired

    if notes:
        logger.info(
            "arg_repair step=%s repairs=%s", step_type, "; ".join(notes),
        )
    return out, notes
