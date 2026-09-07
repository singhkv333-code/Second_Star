/**
 * lib/market-hours.ts — client-side NSE session helpers.
 *
 * Mirrors backend/utils/time_utils.py (is_market_open / next_market_open) so
 * the UI can explain, without a round-trip, *why* an order was queued and
 * *when* it will run. The backend remains the source of truth for whether an
 * order is actually queued (it sets `queued` on the register response); this
 * is purely for the human-readable "executes at next open" copy.
 *
 * NSE regular session: 09:15–15:30 IST, Monday–Friday. Holidays are not
 * modelled here (the backend's is_trading_day carries the same caveat).
 */

/** Current wall-clock parts in Asia/Kolkata, regardless of the viewer's tz. */
function istParts(now: Date = new Date()): {
  weekday: number; // 0=Sun … 6=Sat
  minutes: number; // minutes since IST midnight
} {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Kolkata",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  const parts = fmt.formatToParts(now);
  const get = (t: string): string =>
    parts.find((p) => p.type === t)?.value ?? "";
  const WD: Record<string, number> = {
    Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6,
  };
  const weekday = WD[get("weekday")] ?? 0;
  // "24" can appear for midnight under hour12:false — normalise to 0.
  const hh = Number(get("hour")) % 24;
  const mm = Number(get("minute"));
  return { weekday, minutes: hh * 60 + mm };
}

const OPEN_MIN = 9 * 60 + 15; // 09:15
const CLOSE_MIN = 15 * 60 + 30; // 15:30

/** True when the NSE regular session is currently open (weekday, 09:15–15:30
 *  IST). Ignores holidays. */
export function isMarketOpen(now: Date = new Date()): boolean {
  const { weekday, minutes } = istParts(now);
  if (weekday === 0 || weekday === 6) return false; // weekend
  return minutes >= OPEN_MIN && minutes <= CLOSE_MIN;
}

/**
 * Human label for when a just-queued order will execute, relative to now in
 * IST — "when the market opens tomorrow (9:15 AM IST)", "on Monday", etc.
 * Best-effort copy; the backend's `next_open` string is authoritative when
 * present.
 */
export function nextOpenLabel(now: Date = new Date()): string {
  const { weekday, minutes } = istParts(now);
  const beforeOpenToday =
    weekday >= 1 && weekday <= 5 && minutes < OPEN_MIN;
  if (beforeOpenToday) return "when the market opens today at 9:15 AM IST";

  // Otherwise the next session is a future day. Walk forward to the next
  // weekday (Mon–Fri).
  let d = weekday;
  let hops = 0;
  do {
    d = (d + 1) % 7;
    hops += 1;
  } while (d === 0 || d === 6);

  const when = hops === 1 ? "tomorrow" : ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][d];
  return `when the market opens ${when} at 9:15 AM IST`;
}
