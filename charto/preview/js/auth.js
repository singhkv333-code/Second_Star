/* ══════════════════════════════════════════════════════════════════════════
   AUTH — the Pivot account, and the token every other fetch rides on
   ──────────────────────────────────────────────────────────────────────────
   Signing in happens in one place, the app's /login; this file reads the
   session it leaves (the same token the app sends) and says whose work this
   is. The workspace still works signed out: the chart, the tools and the
   chat run against localStorage, and an account changes only WHERE the work
   is kept — this browser, or your account. So nothing here blocks boot, and a
   dead or unreachable auth endpoint must degrade to "signed out", never to a
   spinner over a chart that would otherwise have loaded.

   The token lives in localStorage rather than an HttpOnly cookie because the
   chart is served from :5173 and the API answers on :5174 — cross-origin,
   where a cookie needs SameSite=None + Secure + an echoed origin + credentials
   mode, and still differs by browser over plain http. The trade is written
   down in dataserver.py next to the session table: a readable token is XSS-
   exposed in a way a cookie is not.
   ══════════════════════════════════════════════════════════════════════════ */
const Auth = (() => {
  // The one session's storage key (pivot-next lib/session.ts SESSION_KEY).
  // Its text is older than the one account system; renaming it would sign
  // every open session out.
  const KEY = "charto:auth:token";
  // Same derivation as chat.js and main.js: same-origin behind the VM's nginx,
  // explicit port in local dev. Spelled out rather than shared because these
  // files load as plain scripts with no module graph between them.
  const API = location.port === "5173"
    ? "http://127.0.0.1:5174" : "";
  let user = null;
  let token = null;
  // true when the last "who is this?" never got an answer (offline, a 5xx,
  // a slow server). The token may be perfectly good; the boot screen must
  // not ask for a sign-in on the strength of a request that failed.
  let unsure = false;
  try { token = localStorage.getItem(KEY); } catch { token = null; }

  const listeners = [];
  const emit = () => listeners.forEach((f) => { try { f(user); } catch {} });

  /** Every authenticated request goes through here so the header is spelled
   *  in exactly one place. Returns plain headers when signed out — callers
   *  must not have to branch on it. */
  function headers(extra) {
    const h = Object.assign({}, extra || {});
    if (token) h.Authorization = `Bearer ${token}`;
    return h;
  }

  async function call(path, body, method) {
    const res = await fetch(API + path, {
      method: method || (body === undefined ? "GET" : "POST"),
      headers: headers(body === undefined ? {} : { "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await res.json(); } catch {}
    if (!res.ok) throw new Error(data.error || `request failed (${res.status})`);
    return data;
  }

  function setSession(tok, u) {
    token = tok; user = u;
    try { tok ? localStorage.setItem(KEY, tok) : localStorage.removeItem(KEY); } catch {}
    emit();
  }

  return {
    get user() { return user; },
    get token() { return token; },
    headers,
    onChange(fn) { listeners.push(fn); if (user !== null) fn(user); },

    /** Boot: who is this? A failure here is answered as "nobody", because the
     *  app is fully usable signed out and a network blip must not gate it. */
    async resume() {
      if (!token) return null;
      try {
        const d = await call("/auth/me");
        unsure = false;
        user = d.user || null;
        if (!user) setSession(null, null);   // token expired or was revoked
        else emit();
      } catch {
        user = null;
        unsure = true;
      }
      return user;
    },

    /** The last resume() got no answer, so a token is held but unconfirmed. */
    get unsure() { return unsure && !!token; },

    async logout() {
      try { await call("/auth/logout", {}); } catch {}   // local sign-out regardless
      setSession(null, null);
    },

    /* ── saved work ──────────────────────────────────────────────────────
       Thin wrappers, deliberately: Store owns WHAT is kept and this owns
       only whose it is. Signed out these are never called. */
    // A view session (?view=) must neither read the viewer's own saved desk
    // into someone else's setup nor write that setup over it. See store.js.
    loadWorkspace(symbol) {
      if (Store.viewOnly) return Promise.resolve({ symbol, state: {} });
      return call(`/workspace?symbol=${encodeURIComponent(symbol)}`);
    },
    saveWorkspace(symbol, state) {
      if (Store.viewOnly) return Promise.resolve({ saved: 0 });
      return call("/workspace", { symbol, state });
    },
    listLayouts() { return call("/layouts"); },
    getLayout(name) { return call(`/layouts?name=${encodeURIComponent(name)}`); },
    saveLayout(name, spec) { return call("/layouts", { name, spec }); },
    deleteLayout(name) { return call("/layouts", { name, delete: true }); },
  };
})();

/* `const Auth` at the top level of a classic script is a global LEXICAL
 * binding, not a property of `window` — so `Auth.headers()` resolves and
 * `window.Auth` is undefined, at the same moment, in the same page.
 *
 * Three handlers in cards.js guard their fetch with `(window.Auth &&
 * Auth.headers) ? Auth.headers({...}) : {...}`, meaning "send the token if the
 * auth module is loaded". That test was never true, so every one of them took
 * the fallback and posted with no Authorization header at all: a signed-in
 * user pressing Activate on a plan, or Save & activate on a draft, was told to
 * sign in. The guard is the right idea; it was reading the wrong shelf.
 *
 * Published here rather than rewritten at the three call sites so the name
 * means the same thing from every script in the page, whatever loads first. */
window.Auth = Auth;

/* ── signing in ───────────────────────────────────────────────────────────
   Pivot has one sign-in: the app's /login (and /signup). The workspace has
   no form of its own. Whatever here needs an account (a save, the account
   menu, a paywall) sends the whole window there, with where the person was
   in ?next=, and signing in brings them back with the session this file
   reads. The workspace runs inside the app's frame, so "the whole window" is
   the top one.
   ────────────────────────────────────────────────────────────────────────── */
(() => {
  function here() {
    try { const t = window.top.location; return t.pathname + t.search + t.hash; }
    catch { return location.pathname + location.search + location.hash; }
  }
  function open(which) {
    const page = which === "signup" ? "/signup" : "/login";
    const url = `${page}?next=${encodeURIComponent(here())}`;
    try { window.top.location.href = url; } catch { location.href = url; }
  }
  window.CHARTO_AUTH_OPEN = open;

  // Who is this? A held token whose check got no answer is asked again
  // (1 s, 3 s, 7 s) rather than treated as signed out.
  window.addEventListener("DOMContentLoaded", async () => {
    let me = await Auth.resume();
    for (const wait of [1000, 3000, 7000]) {
      if (me || !Auth.unsure) break;
      await new Promise((r) => setTimeout(r, wait));
      me = await Auth.resume();
    }
  });
})();
