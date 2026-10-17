import { getScreenerSparklines } from "@/lib/screenerApi";

/**
 * One sparkline cache for the whole page, outside React.
 *
 * WHY NOT COMPONENT STATE
 *
 * It was component state, and that is exactly why the column kept reloading.
 * The screener re-renders on a metrics-warming poll and the results area
 * swaps to a loading branch while it does, which UNMOUNTS the table — taking
 * its cache and its "already asked" set with it. Every poll therefore
 * re-requested every visible symbol, and the charts flickered back to empty
 * on a cadence the user did not control. A cache that a re-render can destroy
 * is not a cache.
 *
 * WHAT THIS GUARANTEES
 *
 *   * One request per symbol per TTL, however many components ask and however
 *     often they re-render or remount.
 *   * Asks made in the same tick are COALESCED into one batch, so a table
 *     rendering forty rows makes four requests, not forty.
 *   * A symbol already in flight is never requested twice; the second asker
 *     simply waits for the first answer.
 *   * A symbol the source cannot serve is remembered as a MISS for a shorter
 *     window, so a name with no intraday data is not re-asked on every scroll
 *     while still recovering on its own once the session has data.
 */

/** Matches the server's own Redis TTL — a finer line than the source refreshes
 *  is a line that cannot move. */
const TTL_MS = 5 * 60_000;
/** Misses expire sooner: "no data yet" is usually a market that has not opened
 *  or a name that is thinly traded, and both resolve on their own. */
const MISS_TTL_MS = 60_000;
/** The server caps a call at 60. Twelve keeps each response small enough that
 *  the first rows paint while the rest are still in the air. */
const CHUNK = 12;
/** Long enough to collect a whole render pass, short enough to be invisible. */
const COALESCE_MS = 40;

type Entry = { pts: number[] | null; at: number };

const cache = new Map<string, Entry>();
const inflight = new Set<string>();
const queue = new Set<string>();
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setTimeout> | null = null;

function fresh(e: Entry | undefined): boolean {
  if (!e) return false;
  return Date.now() - e.at < (e.pts ? TTL_MS : MISS_TTL_MS);
}

/** The series for a symbol, or undefined while it is unknown or in flight. */
export function getSparkline(symbol: string): number[] | undefined {
  const e = cache.get(symbol);
  return fresh(e) && e?.pts ? e.pts : undefined;
}

/** Re-render on new data. Returns its own unsubscribe. */
export function subscribeSparklines(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

function notify(): void {
  for (const fn of listeners) fn();
}

async function flush(): Promise<void> {
  timer = null;
  const want = [...queue].filter((s) => !inflight.has(s) && !fresh(cache.get(s)));
  queue.clear();
  if (!want.length) return;

  for (let i = 0; i < want.length; i += CHUNK) {
    const batch = want.slice(i, i + CHUNK);
    batch.forEach((s) => inflight.add(s));
    getScreenerSparklines(batch)
      .then((res) => {
        const series = "data" in res && res.data ? res.data.series : {};
        const at = Date.now();
        // Every symbol in the batch gets an entry, including the ones the
        // response did not carry. Without that, a name the source cannot
        // serve is re-requested by the very next render, forever.
        for (const s of batch) cache.set(s, { pts: series[s] ?? null, at });
      })
      .catch(() => {
        // A transport failure is not a fact about the symbol — leave no entry
        // so it can be retried, but let the miss window pace the retry.
        const at = Date.now();
        for (const s of batch) {
          if (!cache.has(s)) cache.set(s, { pts: null, at });
        }
      })
      .finally(() => {
        batch.forEach((s) => inflight.delete(s));
        notify();
      });
  }
}

/** Ask for these symbols. Cheap to call on every render: anything cached,
 *  in flight or already queued is dropped here rather than on the wire. */
export function requestSparklines(symbols: string[]): void {
  let added = false;
  for (const s of symbols) {
    if (inflight.has(s) || fresh(cache.get(s)) || queue.has(s)) continue;
    queue.add(s);
    added = true;
  }
  if (!added || timer) return;
  timer = setTimeout(() => void flush(), COALESCE_MS);
}

/** Test seam — the cache is module state, so it outlives a test's render. */
export function __resetSparklineCache(): void {
  cache.clear();
  inflight.clear();
  queue.clear();
  if (timer) clearTimeout(timer);
  timer = null;
}
