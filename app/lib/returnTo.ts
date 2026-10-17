/**
 * returnTo — where a sign-in hands the user back to.
 *
 * The auth gate used to send everyone to /login and every sign-in back to
 * `/`, which lost whatever the user had arrived for. A reader who chose
 * "Make it mine" on a shared chart signed in and landed on Home with no
 * chart and no copy. So the gate carries the page in `?next=`, and the auth
 * pages send the user back there.
 *
 * Only a path on THIS origin is honoured: it must start with one "/" and
 * not "//" or "/\" (both of which a browser reads as another host). Anything
 * else falls back to `/`, so `?next=` can never be turned into a redirect
 * off the site.
 */

function safe(raw: string | null | undefined): string {
  if (!raw) return "/";
  if (!raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return "/";
  return raw;
}

/** The `next` this page was opened with, made safe; `/` when there is none. */
export function returnPath(): string {
  if (typeof window === "undefined") return "/";
  try {
    return safe(new URLSearchParams(window.location.search).get("next"));
  } catch {
    return "/";
  }
}

/** An auth page's URL, carrying `next` along so switching between sign-in
 *  and sign-up does not drop where the user was going. */
export function authHref(page: "/login" | "/signup", next: string = returnPath()): string {
  return next && next !== "/" ? `${page}?next=${encodeURIComponent(next)}` : page;
}

/** /login for the page the user is on now, so signing in comes back here. */
export function loginForHere(): string {
  if (typeof window === "undefined") return "/login";
  const here = `${window.location.pathname}${window.location.search}${window.location.hash}`;
  return authHref("/login", safe(here));
}
