/* ══════════════════════════════════════════════════════════════════════════
   AUTH — the account, and the token every other fetch rides on
   ──────────────────────────────────────────────────────────────────────────
   Charto works signed out. That is the design, not a gap: the chart, the
   tools and the chat all function against localStorage exactly as they did
   before this file existed, and an account changes only WHERE the work is
   kept — this browser, or your account. So nothing here blocks boot, and a
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
  const KEY = "charto:auth:token";
  // Same derivation as chat.js and main.js: same-origin behind the VM's nginx,
  // explicit port in local dev. Spelled out rather than shared because these
  // files load as plain scripts with no module graph between them.
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";
  let user = null;
  let token = null;
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
        user = d.user || null;
        if (!user) setSession(null, null);   // token expired or was revoked
        else emit();
      } catch {
        user = null;
      }
      return user;
    },

    async login(email, password) {
      const d = await call("/auth/login", { email, password });
      setSession(d.token, d.user);
      return d.user;
    },

    async signup(email, password, name) {
      const d = await call("/auth/signup", { email, password, name });
      setSession(d.token, d.user);
      return d.user;
    },

    async logout() {
      try { await call("/auth/logout", {}); } catch {}   // local sign-out regardless
      setSession(null, null);
    },

    /* ── saved work ──────────────────────────────────────────────────────
       Thin wrappers, deliberately: Store owns WHAT is kept and this owns
       only whose it is. Signed out these are never called. */
    loadWorkspace(symbol) {
      return call(`/workspace?symbol=${encodeURIComponent(symbol)}`);
    },
    saveWorkspace(symbol, state) {
      return call("/workspace", { symbol, state });
    },
    listLayouts() { return call("/layouts"); },
    getLayout(name) { return call(`/layouts?name=${encodeURIComponent(name)}`); },
    saveLayout(name, spec) { return call("/layouts", { name, spec }); },
    deleteLayout(name) { return call("/layouts", { name, delete: true }); },
  };
})();

/* ── the screen ─────────────────────────────────────────────────────────── */
(() => {
  const el = (id) => document.getElementById(id);
  const screen = el("authScreen");
  if (!screen) return;

  const form = el("authForm"), err = el("authError");
  const title = el("authTitle"), desc = el("authDesc"), submit = el("authSubmit");
  const altText = el("authAltText"), toggle = el("authToggle");
  const nameField = el("authNameField"), nameIn = el("authName");
  const emailIn = el("authEmail"), pwIn = el("authPassword");

  const SKIPPED = "charto:auth:skipped";
  let mode = "login";
  let busy = false;

  const COPY = {
    login: { title: "Sign in", submit: "Sign in", alt: "New to Charto?",
             toggle: "Create an account", ac: "current-password",
             desc: "Your charts, drawings and conversations, on every device." },
    signup: { title: "Create an account", submit: "Create account",
              alt: "Already have an account?", toggle: "Sign in",
              ac: "new-password",
              desc: "Keep your layouts, drawings and chats across devices." },
  };

  function setMode(next) {
    mode = next;
    const c = COPY[mode];
    title.textContent = c.title;
    desc.textContent = c.desc;
    submit.textContent = c.submit;
    altText.textContent = c.alt;
    toggle.textContent = c.toggle;
    nameField.hidden = mode !== "signup";
    // Password managers offer to SAVE on new-password and to FILL on
    // current-password; leaving it as current- through a signup is what makes
    // a browser quietly not offer to remember the account just created.
    pwIn.setAttribute("autocomplete", c.ac);
    fail("");
  }

  function fail(msg) {
    err.textContent = msg || "";
    err.classList.toggle("show", !!msg);
  }

  function show() { screen.classList.add("open"); setTimeout(() => emailIn.focus(), 40); }
  function hide() { screen.classList.remove("open"); }

  toggle.addEventListener("click", () => setMode(mode === "login" ? "signup" : "login"));

  el("authSkip").addEventListener("click", () => {
    try { localStorage.setItem(SKIPPED, "1"); } catch {}
    hide();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (busy) return;
    const email = emailIn.value.trim();
    const pw = pwIn.value;
    // Checked here as well as on the server so the common mistakes answer
    // instantly; the server still enforces both, since this is only the UI.
    if (!email || !email.includes("@")) return fail("Enter a valid email address.");
    if (pw.length < 8) return fail("Password must be at least 8 characters.");

    busy = true;
    submit.disabled = true;
    submit.textContent = mode === "login" ? "Signing in…" : "Creating account…";
    try {
      if (mode === "login") await Auth.login(email, pw);
      else await Auth.signup(email, pw, nameIn.value.trim());
      try { localStorage.removeItem(SKIPPED); } catch {}
      hide();
      // A sign-in changes whose work this is, and the modules that hold that
      // work are already running. Reloading is the honest way to re-read it
      // all from the account rather than merging two sources in place.
      location.reload();
    } catch (ex) {
      fail(String(ex.message || ex));
      submit.textContent = COPY[mode].submit;
      submit.disabled = false;
      busy = false;
    }
  });

  window.addEventListener("DOMContentLoaded", async () => {
    const me = await Auth.resume();
    let skipped = false;
    try { skipped = localStorage.getItem(SKIPPED) === "1"; } catch {}
    if (!me && !skipped) { setMode("login"); show(); }
  });

  // Opened on demand from elsewhere in the app (an account menu, a save that
  // needs an account) without that caller knowing anything about the markup.
  window.CHARTO_AUTH_OPEN = (which) => { setMode(which === "signup" ? "signup" : "login"); show(); };
})();
