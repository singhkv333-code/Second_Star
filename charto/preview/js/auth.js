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

/* ── the screen ───────────────────────────────────────────────────────────
   Pivot's login/signup, translated: the dark brand panel on the left and one
   focused column on the right. Two MODES rather than two routed pages —
   charto is one page, and the fields that differ are hidden rather than
   rebuilt so switching keeps whatever you have already typed.

   What is NOT carried over from Pivot is the social sign-in block: this
   server issues its own sessions and speaks no OAuth, and a button that
   cannot do what it says is the one thing this codebase will not draw.
   ────────────────────────────────────────────────────────────────────────── */
(() => {
  const el = (id) => document.getElementById(id);
  const screen = el("authScreen");
  if (!screen) return;

  const form = el("authForm"), err = el("authError");
  const title = el("authTitle"), desc = el("authDesc"), submit = el("authSubmit");
  const altText = el("authAltText"), toggle = el("authToggle");
  const nameField = el("authNameField"), nameIn = el("authName");
  const confirmField = el("authConfirmField"), confirmIn = el("authConfirm");
  const emailIn = el("authEmail"), pwIn = el("authPassword");
  const pwAside = el("authPwAside");
  const meter = el("authPwMeter"), meterLevel = el("authPwLevel"),
        meterHint = el("authPwHint"), meterBars = [...meter.querySelectorAll("i")];

  const SKIPPED = "charto:auth:skipped";
  let mode = "login";
  let busy = false;

  /* ── the brand panel's wave field ───────────────────────────────────────
   * Pivot's BrandPanel, path for path: thirty ridge lines whose amplitude
   * swells toward the middle for a 3D feel, masked by a radial fade, over a
   * film grain and a vignette. Deterministic and computed once — it is a
   * still image, not an animation, and geometry does not belong in markup. */
  (function paintWaves() {
    const panel = screen.querySelector(".auth-brandpanel");
    if (!panel) return;
    const W = 600, H = 800, N = 30;
    const wave = (yBase, amp, phase) => {
      const pts = [];
      for (let x = -40; x <= W + 40; x += 8) {
        const t = x / W;
        const y = yBase + Math.sin(t * Math.PI * 2 + phase) * amp
                        + Math.sin(t * Math.PI * 5 + phase * 1.7) * amp * 0.22;
        pts.push(`${x.toFixed(1)},${y.toFixed(1)}`);
      }
      return "M" + pts.join(" L");
    };
    const bell = (i, s) => Math.exp(-((i - N / 2) ** 2) / (2 * s * s));
    const paths = Array.from({ length: N }, (_, i) =>
      `<path d="${wave(60 + i * 23, 6 + 26 * bell(i, 8), i * 0.42)}" `
      + `opacity="${(0.12 + 0.5 * bell(i, 9)).toFixed(3)}"/>`).join("");

    panel.insertAdjacentHTML("afterbegin",
      `<svg aria-hidden="true" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid slice">`
      + `<defs>`
      + `<linearGradient id="bpLine" x1="0" y1="0" x2="1" y2="0">`
      + `<stop offset="0%" stop-color="#fff" stop-opacity="0"/>`
      + `<stop offset="30%" stop-color="#fff" stop-opacity=".55"/>`
      + `<stop offset="70%" stop-color="#fff" stop-opacity=".55"/>`
      + `<stop offset="100%" stop-color="#fff" stop-opacity="0"/></linearGradient>`
      + `<radialGradient id="bpFade" cx="40%" cy="46%" r="70%">`
      + `<stop offset="0%" stop-color="#fff" stop-opacity="1"/>`
      + `<stop offset="100%" stop-color="#fff" stop-opacity="0"/></radialGradient>`
      + `<mask id="bpMask"><rect width="${W}" height="${H}" fill="url(#bpFade)"/></mask>`
      + `</defs>`
      + `<g mask="url(#bpMask)" fill="none" stroke="url(#bpLine)" stroke-width="1" `
      + `stroke-linecap="round">${paths}</g></svg>`
      + `<svg aria-hidden="true" class="grain"><filter id="bpGrain">`
      + `<feTurbulence type="fractalNoise" baseFrequency="0.8" numOctaves="2" `
      + `stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/></filter>`
      + `<rect width="100%" height="100%" filter="url(#bpGrain)"/></svg>`
      + `<div class="vignette"></div>`);
  })();

  const COPY = {
    login: {
      title: "Sign in to Charto", submit: "Sign in", busy: "Signing in…",
      alt: "New to Charto?", toggle: "Create an account", ac: "current-password",
      desc: "Enter your email and password to access your account.",
    },
    signup: {
      title: "Create your account", submit: "Create account",
      busy: "Creating account…", alt: "Already have an account?",
      toggle: "Sign in", ac: "new-password",
      desc: "Keep your layouts, drawings and conversations across devices.",
    },
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
    confirmField.hidden = mode !== "signup";
    // "Forgot?" belongs to a form you are signing IN with; on a form that is
    // creating the password there is nothing yet to have forgotten.
    pwAside.hidden = mode !== "login";
    // Password managers offer to SAVE on new-password and to FILL on
    // current-password; leaving it as current- through a signup is what makes
    // a browser quietly not offer to remember the account just created.
    pwIn.setAttribute("autocomplete", c.ac);
    paintStrength();
    fail("");
  }

  /* ── password strength ──────────────────────────────────────────────────
   * Pivot's three-segment meter, and it REPORTS rather than gates: the
   * server's rule is length alone, so a password it would accept must never
   * be refused here for want of a digit. The advice is still worth giving —
   * it is just advice, and it says so by never blocking the button. */
  const LEVELS = ["", "Weak", "Fair", "Strong"];
  const LEVEL_C = ["var(--border)", "var(--down)", "var(--ann-res)", "var(--up)"];

  function measure(pw) {
    if (!pw) return { score: 0, hint: "" };
    let score = 0;
    const hints = [];
    if (pw.length >= 8) score++; else hints.push("At least 8 characters");
    if (/[a-zA-Z]/.test(pw)) score++; else hints.push("Include a letter");
    if (/[0-9]/.test(pw)) score++; else hints.push("Include a number");
    return { score, hint: hints[0] || "" };
  }

  function paintStrength() {
    const on = mode === "signup" && pwIn.value.length > 0;
    meter.classList.toggle("show", on);
    if (!on) return;
    const { score, hint } = measure(pwIn.value);
    meterBars.forEach((b, i) => {
      b.style.background = score > i ? LEVEL_C[score] : "var(--border)";
    });
    meterLevel.textContent = LEVELS[score];
    meterLevel.style.color = LEVEL_C[score];
    meterHint.textContent = hint;
  }
  pwIn.addEventListener("input", paintStrength);

  /* ── the eyes ───────────────────────────────────────────────────────────
   * One handler for both fields: the button names the input it belongs to,
   * so a third password field would need no code here. */
  for (const b of screen.querySelectorAll(".pw-eye")) {
    const input = el(b.dataset.eye);
    const paint = () => {
      const shown = input.type === "text";
      b.innerHTML = Icons.svg(shown ? "eyeOff" : "eye");
      b.setAttribute("aria-label", shown ? "Hide password" : "Show password");
    };
    b.addEventListener("click", () => {
      input.type = input.type === "password" ? "text" : "password";
      paint();
    });
    paint();
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

  // Pivot submits on Cmd/Ctrl+Enter as well as on the button; a long form is
  // worth not making you reach for the mouse at the end of.
  form.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") form.requestSubmit();
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
    if (mode === "signup" && confirmIn.value !== pw) {
      return fail("Passwords do not match.");
    }

    busy = true;
    submit.disabled = true;
    submit.textContent = COPY[mode].busy;
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

  // Opened on demand from elsewhere in the app (the account menu, a save that
  // needs an account) without that caller knowing anything about the markup.
  window.CHARTO_AUTH_OPEN = (which) => { setMode(which === "signup" ? "signup" : "login"); show(); };
})();
