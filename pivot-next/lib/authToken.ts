/**
 * authToken — one place that hands out a *valid* bearer access token.
 *
 * The backend issues a short-lived access token (12h) plus a long-lived
 * refresh token (7d) and exposes `POST /auth/refresh` to trade a refresh
 * token for a fresh access+refresh pair (with rotation). Login/signup store
 * BOTH tokens in localStorage (`pivot_jwt`, `pivot_refresh`).
 *
 * The bug this fixes: several direct-fetch data modules (chat stream, live
 * quotes, portfolio, screener) read the raw `pivot_jwt` and never refresh —
 * so when the 12h access token expires (typically overnight) every one of
 * them 401s at once and the app looks totally broken, even though a valid
 * 7-day refresh token is sitting right there. Users then had to log out and
 * back in daily.
 *
 * `getAccessToken()` closes that gap: it returns the current access token,
 * transparently refreshing it first when it's expired or about to expire.
 * All refreshers share ONE in-flight promise — critical because `/auth/refresh`
 * ROTATES the refresh token, so two concurrent refreshes would race and
 * invalidate each other. `scheduleAutoRefresh()` keeps the token warm while
 * the app is open (timer + on tab focus) so even sync readers stay valid.
 */

const TOKEN_KEY = "pivot_jwt";
const REFRESH_KEY = "pivot_refresh";

// Refresh when the access token has this many seconds (or fewer) of life
// left — covers clock skew and requests in flight at the boundary.
const EXP_SKEW_SECONDS = 120;
// Background auto-refresh cadence while the app is open.
const AUTO_REFRESH_POLL_MS = 5 * 60 * 1000; // 5 min

function readLS(key: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeLS(key: string, val: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, val);
  } catch {
    /* localStorage may be denied in some embeds */
  }
}

/** Legacy (non-`/api`) base for `/auth/*` routes — mirrors ChatDemo/api.ts. */
function legacyBase(): string {
  const base =
    (typeof process !== "undefined" &&
      process.env.NEXT_PUBLIC_PIVOT_API_BASE) ||
    "/api";
  return base.replace(/\/api\/?$/, "");
}

/**
 * Seconds until the JWT's `exp` claim. Returns `null` when there's no token
 * or the payload can't be decoded — callers treat null as "unknown, use it
 * as-is" rather than forcing a refresh (a genuinely bad token still 401s and
 * the caller's own 401 path handles it).
 */
export function secondsUntilExpiry(token: string | null): number | null {
  if (!token) return null;
  const parts = token.split(".");
  const payload = parts[1];
  if (!payload) return null;
  try {
    const b64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const json = JSON.parse(
      decodeURIComponent(
        atob(b64)
          .split("")
          .map((c) => "%" + c.charCodeAt(0).toString(16).padStart(2, "0"))
          .join(""),
      ),
    );
    if (typeof json.exp !== "number") return null;
    return json.exp - Math.floor(Date.now() / 1000);
  } catch {
    return null;
  }
}

// Shared in-flight refresh. All callers await the SAME promise so a burst of
// simultaneous data loads triggers exactly one `/auth/refresh` (rotation-safe).
let _refreshInFlight: Promise<string | null> | null = null;

async function performRefresh(): Promise<string | null> {
  const refreshToken = readLS(REFRESH_KEY);
  if (!refreshToken) return null;
  try {
    const res = await fetch(`${legacyBase()}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    });
    if (!res.ok) return null;
    const data = (await res.json()) as {
      access_token?: string;
      refresh_token?: string;
    };
    if (data?.access_token) {
      writeLS(TOKEN_KEY, data.access_token);
      if (data.refresh_token) writeLS(REFRESH_KEY, data.refresh_token);
      return data.access_token;
    }
    return null;
  } catch {
    return null;
  }
}

/** Deduped silent refresh. Returns the new access token, or null on failure
 *  (no/expired refresh token → caller falls back to its 401/login path). */
export function refreshAccessToken(): Promise<string | null> {
  if (_refreshInFlight) return _refreshInFlight;
  _refreshInFlight = performRefresh().finally(() => {
    _refreshInFlight = null;
  });
  return _refreshInFlight;
}

/**
 * Return a valid bearer access token, refreshing silently first when the
 * current one is expired or within EXP_SKEW_SECONDS of expiry. This is what
 * every direct-fetch data module should call instead of reading `pivot_jwt`
 * straight from localStorage.
 */
export async function getAccessToken(): Promise<string | null> {
  const current = readLS(TOKEN_KEY);
  const ttl = secondsUntilExpiry(current);
  // Fresh enough (or un-decodable → assume usable): return as-is.
  if (current && (ttl === null || ttl > EXP_SKEW_SECONDS)) return current;
  // Missing / expired / near-expiry but a refresh token exists → refresh.
  const refreshed = await refreshAccessToken();
  return refreshed ?? current;
}

/** Best-effort synchronous read (no refresh). For call sites that can't be
 *  async; pair with scheduleAutoRefresh() so the value stays fresh. */
export function getAccessTokenSync(): string | null {
  return readLS(TOKEN_KEY);
}

let _autoRefreshTimer: ReturnType<typeof setInterval> | null = null;
let _visibilityHandler: (() => void) | null = null;

/** Refresh now if the token is expired/near-expiry — used by the poller and
 *  the tab-focus handler. No-op when comfortably fresh. */
async function refreshIfStale(): Promise<void> {
  const ttl = secondsUntilExpiry(readLS(TOKEN_KEY));
  if (readLS(REFRESH_KEY) && (ttl === null || ttl <= EXP_SKEW_SECONDS)) {
    await refreshAccessToken();
  }
}

/**
 * Keep the access token warm while the app is open: poll on an interval and
 * refresh immediately whenever the tab regains focus (covers a laptop that
 * slept past the token's expiry). Idempotent — safe to call once on mount.
 */
export function scheduleAutoRefresh(): void {
  if (typeof window === "undefined") return;
  if (_autoRefreshTimer !== null) return; // already running
  // Kick once now so a cold load with an already-expired token refreshes
  // before the first sync reader (WS) needs it.
  void refreshIfStale();
  _autoRefreshTimer = setInterval(() => {
    void refreshIfStale();
  }, AUTO_REFRESH_POLL_MS);
  _visibilityHandler = () => {
    if (document.visibilityState === "visible") void refreshIfStale();
  };
  document.addEventListener("visibilitychange", _visibilityHandler);
  window.addEventListener("focus", _visibilityHandler);
}

/** Tear down the auto-refresh timer + listeners (on logout / unmount). */
export function stopAutoRefresh(): void {
  if (_autoRefreshTimer !== null) {
    clearInterval(_autoRefreshTimer);
    _autoRefreshTimer = null;
  }
  if (_visibilityHandler !== null && typeof window !== "undefined") {
    document.removeEventListener("visibilitychange", _visibilityHandler);
    window.removeEventListener("focus", _visibilityHandler);
    _visibilityHandler = null;
  }
}
