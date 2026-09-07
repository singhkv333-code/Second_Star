import type { Step } from "@/lib/types";

/**
 * One-line, type-aware summary of a step's config — what we render under
 * the step label in each card. Per-type formatters keep the UI legible
 * without dumping raw JSON. Unknown step types fall back to a generic
 * key=value pluck so even backend additions stay readable.
 */
export function previewStepConfig(step: Step): string {
  const c = step.config as Record<string, unknown>;
  switch (step.step_type) {
    case "trigger.schedule":
      return formatSchedule(c);
    case "trigger.price":
      return `${str(c.symbol, "?")} ${str(c.operator, "?")} ${num(c.value)} on ${str(c.exchange, "NSE")}`;
    case "trigger.indicator":
      return `${str(c.indicator, "?").toUpperCase()}(${num(c.period)}) of ${str(c.symbol, "?")} ${str(c.operator, "?")} ${num(c.value)}`;
    case "trigger.event":
      return `Event: ${str(c.event_type, "any")}`;
    case "trigger.manual":
      return "Manual run only — no automatic firing";
    case "trigger.webhook":
      return "Fires when webhook URL is hit";
    case "fetch.quote":
      return `Quote for ${str(c.symbol, "?")} on ${str(c.exchange, "NSE")}`;
    case "fetch.indicator":
      return `${str(c.indicator, "?").toUpperCase()}(${num(c.period)}) of ${str(c.symbol, "?")}`;
    case "fetch.fundamental":
      return `${str(c.metric, "?").toUpperCase()} of ${str(c.symbol, "?")}`;
    case "fetch.portfolio":
      return "Holdings, buying power, and total value";
    case "fetch.news":
      return `News for ${str(c.symbol_or_query, "?")} (limit ${num(c.limit, 10)})`;
    case "condition.numeric":
      return `${rep(c.left)} ${str(c.operator, "?")} ${rep(c.right)}`;
    case "condition.market_status":
      return `Market must be ${str(c.require, "open")}`;
    case "condition.position":
      return `${str(c.symbol, "?")} must be ${str(c.require, "held")}`;
    case "condition.time_window":
      return `Between ${str(c.start_time, "00:00")} and ${str(c.end_time, "23:59")} ${str(c.timezone, "Asia/Kolkata")}`;
    case "action.place_order": {
      const price =
        c.order_type === "limit" && c.limit_price !== undefined
          ? ` @ ${num(c.limit_price)}`
          : " @ market";
      const approval = c.requires_approval ? " (requires approval)" : "";
      return `${str(c.side, "buy").toUpperCase()} ${num(c.quantity)} ${str(c.symbol, "?")}${price}${approval}`;
    }
    case "action.cancel_orders":
      return [
        c.symbol_filter ? `symbol=${str(c.symbol_filter)}` : null,
        c.side_filter ? `side=${str(c.side_filter)}` : null,
      ]
        .filter(Boolean)
        .join(", ") || "Cancel all pending orders";
    case "action.set_stoploss":
      return `Stop ${str(c.symbol, "?")} at ${num(c.trigger_price)}${
        c.quantity !== undefined ? ` (qty ${num(c.quantity)})` : ""
      }`;
    case "action.update_watchlist":
      return `${str(c.action, "add")} ${str(c.symbol, "?")}`;
    case "notify.message":
      return `${str(c.channel, "email").toUpperCase()}: ${str(c.template, "(template)")}`;
    case "notify.log":
      return str(c.message, "(log message)");
    case "wait.approval":
      return `${str(c.summary, "Awaiting approval")} (expires in ${num(c.expires_in_minutes, 15)} min)`;
    case "wait.delay":
      if (c.duration_seconds !== undefined) return `Wait ${num(c.duration_seconds)}s`;
      if (c.until_time !== undefined)
        return `Wait until ${str(c.until_time)} ${str(c.timezone, "Asia/Kolkata")}`;
      return "Wait";
    case "control.skip_if":
      return "Skip next step when condition holds";
    default:
      return genericPreview(c);
  }
}

function str(value: unknown, fallback = ""): string {
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return fallback;
  return String(value);
}

function num(value: unknown, fallback = 0): number {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() && !Number.isNaN(Number(value))) {
    return Number(value);
  }
  return fallback;
}

function rep(value: unknown): string {
  // Refs come through as `{{ context.X.path }}` strings — keep them as-is.
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  return JSON.stringify(value ?? null);
}

function formatSchedule(c: Record<string, unknown>): string {
  const cron = str(c.cron);
  if (!cron) return "(no schedule set)";
  return humanizeCron(cron) ?? cron;
}

/**
 * Convert the small set of cron patterns the chat actually emits into a
 * short human-readable phrase. Returns null for anything we don't
 * recognise so the caller can fall back to the raw expression.
 *
 *   "30 9 1 * *"     → "1st of every month at 9:30 AM"
 *   "55 15 * * 1-5"  → "Weekdays at 3:55 PM"
 *   "0 9 * * 1"      → "Mondays at 9:00 AM"
 *   "0 9 * * *"      → "Every day at 9:00 AM"
 */
function humanizeCron(cron: string): string | null {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [pm, ph, pdom, pmonth, pdow] = parts as [string, string, string, string, string];
  if (pmonth !== "*") return null;
  if (!/^\d+$/.test(pm) || !/^\d+$/.test(ph)) return null;

  const minute = parseInt(pm, 10);
  const hour24 = parseInt(ph, 10);
  if (minute < 0 || minute > 59 || hour24 < 0 || hour24 > 23) return null;
  const time = formatTime12h(hour24, minute);

  if (pdom !== "*" && pdow === "*") {
    if (!/^\d+$/.test(pdom)) return null;
    return `${ordinal(parseInt(pdom, 10))} of every month at ${time}`;
  }
  if (pdom === "*" && pdow !== "*") {
    if (pdow === "1-5") return `Weekdays at ${time}`;
    if (pdow === "0,6" || pdow === "6,0") return `Weekends at ${time}`;
    const dayNames = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
    const days = pdow
      .split(",")
      .map((t) => (/^\d+$/.test(t) ? dayNames[parseInt(t, 10)] : null))
      .filter((s): s is string => !!s);
    if (days.length === 0) return null;
    return `${days.join(", ")} at ${time}`;
  }
  if (pdom === "*" && pdow === "*") {
    return `Every day at ${time}`;
  }
  return null;
}

function formatTime12h(hour24: number, minute: number): string {
  const period = hour24 >= 12 ? "PM" : "AM";
  const hour12 = hour24 % 12 === 0 ? 12 : hour24 % 12;
  return `${hour12}:${String(minute).padStart(2, "0")} ${period}`;
}

function ordinal(n: number): string {
  const v = n % 100;
  if (v >= 11 && v <= 13) return `${n}th`;
  switch (n % 10) {
    case 1: return `${n}st`;
    case 2: return `${n}nd`;
    case 3: return `${n}rd`;
    default: return `${n}th`;
  }
}

function genericPreview(c: Record<string, unknown>): string {
  const entries = Object.entries(c).slice(0, 3);
  if (entries.length === 0) return "No configuration";
  return entries.map(([k, v]) => `${k}=${typeof v === "object" ? "…" : String(v)}`).join(", ");
}
