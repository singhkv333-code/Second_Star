/* Charto preview — the paywall on the chart.
 *
 * What a person sees when their plan says no, and the plan itself:
 *
 *   show(refusal)    the upgrade prompt for any HTTP 402. Every refusal from
 *                    the dataserver carries {error, code, feature, limit,
 *                    used, plan, upgrade_to, resets_at?} (data/entitlements.py),
 *                    so this needs no second request to explain it.
 *   evicted(detail)  the banner on a tab the parallel-chart limit closed, with
 *                    "Use this tab" to take the slot back.
 *   credits(c)       "N of M credits left" under the composer, from the
 *                    `credits` every chat answer reports.
 *   openBilling()    Plan & billing: plan, status, renewal date, usage, cancel.
 *   lockFor(key, n)  the plan that unlocks n of `key`, for lock markers on the
 *                    layout and indicator menus ("" when allowed).
 *   prompt(key, n)   a client-side refusal (a click Plan.allows said no to),
 *                    shaped exactly like the server's.
 *
 * ONE place sees every refusal: fetch is wrapped below, so a 402 from any
 * route the chart calls (alerts, layouts, chat, tools) raises the prompt
 * without each call site having to know about plans. The caller still gets
 * its response and handles its own error as before.
 *
 * Inside the pivot-next shell (the chart is framed same-origin at
 * /chart-app), the prompt is handed to the shell's paywall instead, so the
 * product has one paywall design, not two. Standalone, it draws its own.
 *
 * Nothing here is a security boundary: the server decides (plan.js header).
 */
const Paywall = (() => {
  "use strict";
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";
  const PREVIEW = /[?&]paywall=preview\b/.test(location.search);

  let catalog = null;          // GET /billing/plans, for "Pro gives 200"
  let modal = null;
  let banner = null;

  // ── where pricing lives ────────────────────────────────────────────────
  function inShell() {
    try {
      return window.parent !== window
        && window.parent.location.origin === location.origin;
    } catch { return false; }
  }
  // pivot-next serves /pricing at its root in development (the chart is
  // framed at /chart-app) and under /pv beside the chart in production.
  const SHELL = window.CHARTO_SHELL_BASE
    ?? (inShell() || location.pathname.startsWith("/chart-app") ? "" : "/pv");
  function go(path) {
    const url = SHELL + path;
    if (inShell()) window.parent.location.assign(url);
    else window.location.assign(url);
  }
  const pricing = (feature) =>
    go("/pricing" + (feature ? `?feature=${encodeURIComponent(feature)}&from=chart` : "?from=chart"));

  // ── data ───────────────────────────────────────────────────────────────
  const headers = (extra) => (typeof Auth !== "undefined"
    ? Auth.headers(extra) : Object.assign({}, extra || {}));

  async function loadCatalog() {
    if (catalog) return catalog;
    try {
      const r = await _fetch(`${API}/billing/plans`);
      if (r.ok) catalog = await r.json();
    } catch { /* the prompt still has the server's own sentence */ }
    return catalog;
  }

  const planName = (id) => {
    if (id === "anonymous") return "Signed out";
    const p = catalog && catalog.plans.find((x) => x.id === id);
    return p ? p.name : ({ free: "Free", pro: "Pro", pro_plus: "Pro+" }[id] || id);
  };
  const featureOf = (key) => (catalog && catalog.features[key]) || { label: key, kind: "limit" };
  const lower = (s) => (/^.[A-Z]/.test(s) ? s : s.charAt(0).toLowerCase() + s.slice(1));
  const num = (v) => Number(v).toLocaleString("en-IN");
  const inr = (paise) => "₹" + (paise / 100).toLocaleString("en-IN");
  const date = (unix) => new Date(unix * 1000).toLocaleDateString("en-IN",
    { day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /** "200 AI credits a month", "Unlimited indicators per chart". */
  function line(key, v) {
    const f = featureOf(key);
    if (f.kind === "flag") return f.label;
    if (v === null || v === undefined) return `Unlimited ${lower(f.label)}`;
    if (key === "alerts.expiry_days") return `Alerts last ${v} days`;
    if (f.kind === "quota") return `${num(v)} ${lower(f.label)} a ${f.window || "month"}`;
    return `${num(v)} ${lower(f.label)}`;
  }

  // ── is the paywall on ──────────────────────────────────────────────────
  /** The server's switch (PAYWALL_ENABLED), or ?paywall=preview to review
   *  the lock markers while it is off. */
  function enforced() {
    return PREVIEW || !!(typeof Plan !== "undefined" && Plan.me && Plan.me.paywall_enabled);
  }

  /** The plan that would allow n of `key`, or "" when nothing is locked. */
  function lockFor(key, n) {
    if (!enforced() || typeof Plan === "undefined") return "";
    const v = Plan.value(key);
    if (v === undefined || v === null) return "";
    if (typeof v === "boolean" ? v : n <= v) return "";
    if (!catalog) return "Upgrade";
    const cur = Plan.plan || "free";
    const ranks = catalog.plans.map((p) => p.id);
    for (const p of catalog.plans) {
      if (ranks.indexOf(p.id) <= ranks.indexOf(cur)) continue;
      const pv = p.features[key];
      if (pv === null || pv === true || (typeof pv === "number" && pv >= n)) return p.name;
    }
    return "";                 // no plan reaches n: there is nothing to sell
  }

  // ── the prompt ─────────────────────────────────────────────────────────
  function close() {
    if (!modal) return;
    modal.remove();
    modal = null;
    document.removeEventListener("keydown", onKey, true);
  }
  function onKey(e) { if (e.key === "Escape") { e.stopPropagation(); close(); } }

  /** Free ways forward, per feature. Only real actions. */
  const FREE_WAY = {
    "ai.credits": "Your saved chats, charts and alerts keep working while you wait.",
    "alerts.price": "Pause or delete a price alert you no longer need to free a slot.",
    "alerts.technical": "Pause or delete a technical alert you no longer need to free a slot.",
    "chart.indicators": "Remove an indicator from this chart to make room.",
    "chart.panes": "Pick a layout with fewer charts.",
    "chart.parallel": "Close a chart tab you are not using.",
  };

  async function show(body) {
    if (!body || !body.code) return;
    if (body.code === "evicted") return evicted(body);
    if (inShell()) {
      try {
        window.parent.dispatchEvent(new window.parent.CustomEvent("pivot:paywall",
          { detail: { ...body, surface: "chart" } }));
        return;
      } catch { /* a shell without the provider: draw our own */ }
    }
    if (modal) return;                 // one prompt at a time
    await loadCatalog();
    const signin = body.plan === "anonymous";
    const f = featureOf(body.feature);
    const up = body.upgrade_to && body.upgrade_to !== "free" && body.upgrade_to !== "anonymous"
      ? catalog && catalog.plans.find((p) => p.id === body.upgrade_to) : null;
    const quota = body.code === "quota_exhausted";
    const meter = body.limit != null && body.used != null;
    const used = quota ? Math.max(body.used, body.limit) : body.used;
    const pct = meter && body.limit ? Math.min(100, Math.round(used / body.limit * 100)) : 100;
    const kicker = signin ? "Free account" : quota ? "Monthly allowance used"
      : body.code === "feature_locked" ? `${up ? up.name : "Paid"} feature` : "Plan limit reached";
    const title = signin ? "Sign in to keep going"
      : quota ? `You have used this ${body.feature === "ai.credits" ? "month's AI credits" : "period's " + lower(f.label)}`
      : body.code === "feature_locked" ? f.label
      : body.limit != null ? `You are at ${num(body.limit)} ${lower(f.label)}` : "You are at your plan's limit";

    const opts = [];
    if (up) opts.push(`<li><b>Upgrade to ${esc(up.name)}</b> for ${esc(lower(line(body.feature, up.features[body.feature])))}${up.prices.annual ? `, from ${inr(up.prices.annual.per_month)}/mo` : ""}.</li>`);
    if (body.resets_at) opts.push(`<li><b>Wait for the reset</b> on ${date(body.resets_at)}, when your allowance refills at no cost.</li>`);
    if (FREE_WAY[body.feature]) opts.push(`<li>${FREE_WAY[body.feature]}</li>`);

    modal = document.createElement("div");
    modal.className = "pw-scrim";
    modal.innerHTML = `
      <div class="pw-modal" role="dialog" aria-modal="true" aria-labelledby="pwTitle">
        <button type="button" class="pw-x" aria-label="Close" data-pw="close">${Icons.svg("x", "sm")}</button>
        <span class="pw-kicker">${esc(kicker)}</span>
        <h2 class="pw-title" id="pwTitle">${esc(title)}</h2>
        <p class="pw-msg">${esc(body.error || "")}</p>
        ${meter && !signin ? `
          <div class="pw-meter ${pct >= 100 ? "out" : ""}" role="progressbar" aria-valuemin="0"
               aria-valuemax="${body.limit}" aria-valuenow="${used}">
            <div class="pw-meter-head"><span>${esc(f.label)}</span><span><b>${num(used)}</b> of ${num(body.limit)}</span></div>
            <div class="pw-track"><div class="pw-fill" style="width:${pct}%"></div></div>
          </div>` : ""}
        ${signin ? `<ul class="pw-opts"><li>Your own alerts, layouts and conversations, saved</li><li>More AI questions every month, free</li><li>No card required</li></ul>`
          : opts.length ? `<ul class="pw-opts">${opts.join("")}</ul>` : ""}
        <div class="pw-actions">
          ${signin
            ? `<button type="button" class="btn cta pw-cta" data-pw="signup">Create a free account</button>
               <button type="button" class="btn" data-pw="login">Sign in</button>`
            : up ? `<button type="button" class="btn cta pw-cta" data-pw="plans">See ${esc(up.name)} and other plans</button>`
            : ""}
          <button type="button" class="btn pw-ghost" data-pw="close">Not now</button>
        </div>
      </div>`;
    modal.addEventListener("click", (e) => {
      const b = e.target.closest("[data-pw]");
      if (e.target === modal || (b && b.dataset.pw === "close")) return close();
      if (!b) return;
      close();
      if (b.dataset.pw === "plans") pricing(body.feature);
      if (b.dataset.pw === "signup" || b.dataset.pw === "login") {
        if (window.CHARTO_AUTH_OPEN) window.CHARTO_AUTH_OPEN(b.dataset.pw);
      }
    });
    document.body.appendChild(modal);
    document.addEventListener("keydown", onKey, true);
    const first = modal.querySelector(".pw-cta") || modal.querySelector("[data-pw]");
    if (first) first.focus();
  }

  /** A click the client already knows the plan refuses: say it the way the
   *  server would have, with the same shape. */
  async function prompt(key, n) {
    await loadCatalog();
    const plan = (typeof Plan !== "undefined" && Plan.plan) || "free";
    const v = typeof Plan !== "undefined" ? Plan.value(key) : undefined;
    const f = featureOf(key);
    const flag = typeof v === "boolean";
    let upgrade = null;
    if (catalog) {
      const ranks = catalog.plans.map((p) => p.id);
      upgrade = (catalog.plans.find((p) => ranks.indexOf(p.id) > ranks.indexOf(plan)
        && (p.features[key] === null || p.features[key] === true
            || (typeof p.features[key] === "number" && p.features[key] >= n))) || {}).id || null;
    }
    const msg = typeof Plan !== "undefined" ? Plan.refusal(key) : `${f.label} is limited on your plan.`;
    return show({
      error: msg, code: flag ? "feature_locked" : "plan_limit", feature: key, plan,
      limit: flag ? null : v, used: flag ? null : (typeof v === "number" ? v : null),
      upgrade_to: upgrade,
    });
  }

  // ── the evicted-tab banner ─────────────────────────────────────────────
  function evicted(detail) {
    if (banner) return;
    banner = document.createElement("div");
    banner.className = "pw-banner";
    banner.setAttribute("role", "alert");
    banner.innerHTML = `
      <span class="pw-banner-icon">${Icons.svg("pause", "sm")}</span>
      <div class="pw-banner-body"><b>This chart was paused</b>
        <span>${esc(detail.error || "You opened more charts than your plan runs at once.")}
        Live updates stopped here; your other tabs carry on.</span></div>
      <div class="pw-banner-acts">
        <button type="button" class="btn cta" data-pwb="reclaim">Use this tab</button>
        ${detail.upgrade_to ? `<button type="button" class="btn" data-pwb="plans">See plans</button>` : ""}
      </div>`;
    banner.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-pwb]");
      if (!b) return;
      if (b.dataset.pwb === "plans") return pricing("chart.parallel");
      b.disabled = true;
      b.textContent = "Taking it back…";
      await Plan.reclaim();
      if (!Plan.evicted) { banner.remove(); banner = null; }
      else { b.disabled = false; b.textContent = "Use this tab"; }
    });
    (document.querySelector(".stage") || document.body).prepend(banner);
  }

  // ── credits remaining ──────────────────────────────────────────────────
  function credits(c) {
    let row = document.getElementById("pwCredits");
    // No limit = unmetered (or the paywall is off): nothing honest to count.
    if (!c || c.limit == null) { if (row) row.remove(); return; }
    if (!row) {
      const shell = document.getElementById("askShell");
      if (!shell) return;
      row = document.createElement("div");
      row.id = "pwCredits";
      row.className = "pw-credits";
      shell.parentNode.insertBefore(row, shell.nextSibling);
    }
    const left = Math.max(0, c.left ?? (c.limit - (c.used || 0)));
    const tone = left === 0 ? "out" : left / c.limit <= 0.2 ? "low" : "";
    row.className = "pw-credits " + tone;
    row.innerHTML = `<span>${left === 0 ? "No AI credits left" : `${num(left)} of ${num(c.limit)} AI credits left`}`
      + `${c.resets_at ? ` · resets ${date(c.resets_at)}` : ""}</span>`
      + (tone ? ` <button type="button" class="pw-link" data-pwc="plans">Upgrade</button>` : "");
    row.onclick = (e) => { if (e.target.closest("[data-pwc]")) pricing("ai.credits"); };
  }

  // ── Plan & billing ─────────────────────────────────────────────────────
  const STATUS = {
    active: ["Active", "good"], trialing: ["Trial", "good"], past_due: ["Payment failed", "bad"],
    cancelled: ["Cancelled", ""], expired: ["Expired", ""], created: ["Checkout not completed", "warn"],
  };

  async function openBilling() {
    close();
    await loadCatalog();
    modal = document.createElement("div");
    modal.className = "pw-scrim";
    modal.innerHTML = `<div class="pw-modal pw-wide" role="dialog" aria-modal="true" aria-label="Plan and billing">
      <button type="button" class="pw-x" aria-label="Close" data-pw="close">${Icons.svg("x", "sm")}</button>
      <span class="pw-kicker">Plan &amp; billing</span><div class="pw-body"><div class="pw-skel"></div><div class="pw-skel"></div></div></div>`;
    document.body.appendChild(modal);
    document.addEventListener("keydown", onKey, true);
    modal.addEventListener("click", onBillingClick);
    await paintBilling();
  }

  async function paintBilling() {
    const host = modal && modal.querySelector(".pw-body");
    if (!host) return;
    let me = null;
    try {
      const r = await _fetch(`${API}/billing/me`, { headers: headers() });
      if (r.ok) me = await r.json();
    } catch { /* below */ }
    if (!me) {
      host.innerHTML = `<p class="pw-msg">Your plan could not be loaded. Check your connection and try again.</p>
        <div class="pw-actions"><button type="button" class="btn" data-pw="retry">Try again</button></div>`;
      return;
    }
    if (me.plan === "anonymous") {
      host.innerHTML = `<h2 class="pw-title">Signed out</h2><p class="pw-msg">Plans, usage and billing belong to your account.</p>
        <div class="pw-actions"><button type="button" class="btn cta" data-pw="login">Sign in</button>
        <button type="button" class="btn" data-pw="plans">See plans</button></div>`;
      return;
    }
    const s = me.subscription;
    const now = Date.now() / 1000;
    const paidLive = s && s.provider === "razorpay" && me.plan !== "free";
    const ending = s && (s.cancel_at_period_end || s.status === "cancelled") && s.period_end > now;
    const st = !s || s.plan === "free" ? ["Free", ""]
      : ending ? ["Cancelled", ""] : (STATUS[s.status] || [s.status, ""]);
    const price = s && catalog ? ((catalog.plans.find((p) => p.id === s.plan) || {}).prices || {})[s.cycle] : null;
    const rows = [];
    if (paidLive) {
      rows.push(["Billing", s.cycle === "annual" ? "Yearly" : "Monthly"]);
      if (price) rows.push(["Price", `${inr(price.amount)} / ${s.cycle === "annual" ? "year" : "month"}, GST included`]);
      if (s.status === "past_due" && s.grace_until) rows.push(["Plan held until", date(s.grace_until)]);
      else if (s.period_end) rows.push([ending ? "Active until" : "Next renewal", date(s.period_end)]);
      if (s.pending_change) rows.push(["Scheduled", `${planName(s.pending_change.plan)}, ${s.pending_change.cycle === "annual" ? "yearly" : "monthly"}, from ${date(s.pending_change.at)}`]);
    } else if (me.plan !== "free") {
      rows.push(["Billing", "Granted by Pivot, nothing is charged"]);
    }
    const meters = [];
    const cr = me.features["ai.credits"];
    if (cr && cr.limit != null) meters.push([`AI credits`, cr.used || 0, cr.limit, cr.resets_at ? `Resets ${date(cr.resets_at)}` : ""]);
    for (const k of ["alerts.price", "alerts.technical"]) {
      const f = me.features[k];
      if (f && typeof f.used === "number" && f.value != null) meters.push([f.label, f.used, f.value, ""]);
    }
    const fixed = ["chart.indicators", "chart.panes", "chart.parallel"]
      .map((k) => me.features[k]).filter(Boolean)
      .map((f) => `<div class="pw-kv"><span>${esc(f.label)}</span><b>${f.value == null ? "Unlimited" : num(f.value)}</b></div>`).join("");

    host.innerHTML = `
      ${s && s.status === "past_due" ? `<div class="pw-note bad"><b>Your last payment did not go through.</b> Update your payment method from the full billing page before ${date(s.grace_until)} to keep ${esc(planName(s.plan))}.</div>` : ""}
      ${ending ? `<div class="pw-note"><b>${esc(planName(s.plan))} ends on ${date(s.period_end)}.</b> It will not renew and you will not be charged again.</div>` : ""}
      <div class="pw-plan"><h2 class="pw-title">${esc(me.plan_name)}</h2><span class="pw-badge ${st[1]}">${esc(st[0])}</span></div>
      ${rows.length ? `<div class="pw-rows">${rows.map(([k, v]) => `<div class="pw-kv"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")}</div>` : ""}
      <div class="pw-section">Usage${me.paywall_enabled ? "" : ` <span class="pw-fine">· limits are not enforced yet</span>`}</div>
      ${meters.map(([l, u, lim, foot]) => {
        const pct = lim ? Math.min(100, Math.round(u / lim * 100)) : 0;
        return `<div class="pw-meter ${pct >= 100 ? "out" : pct >= 80 ? "low" : ""}" role="progressbar" aria-valuemin="0" aria-valuemax="${lim}" aria-valuenow="${u}">
          <div class="pw-meter-head"><span>${esc(l)}</span><span><b>${num(u)}</b> of ${num(lim)}</span></div>
          <div class="pw-track"><div class="pw-fill" style="width:${pct}%"></div></div>
          ${foot ? `<div class="pw-fine">${esc(foot)}</div>` : ""}</div>`;
      }).join("")}
      <div class="pw-rows">${fixed}</div>
      <div class="pw-actions">
        <button type="button" class="btn cta" data-pw="plans">${me.plan === "free" ? "Compare plans" : "Change plan"}</button>
        <button type="button" class="btn" data-pw="full">Invoices &amp; payment method</button>
        ${paidLive && !ending && s.status !== "created" ? `<button type="button" class="btn pw-ghost" data-pw="cancel">Cancel subscription</button>` : ""}
      </div>
      <p class="pw-fine">Prices include GST. Payments are processed by Razorpay.</p>`;
    host.dataset.end = s && s.period_end ? date(s.period_end) : "";
    host.dataset.plan = s ? planName(s.plan) : "";
  }

  async function onBillingClick(e) {
    const b = e.target.closest("[data-pw]");
    if (e.target === modal || (b && b.dataset.pw === "close")) return close();
    if (!b) return;
    const host = modal.querySelector(".pw-body");
    const a = b.dataset.pw;
    if (a === "retry") return paintBilling();
    if (a === "plans") { close(); return pricing(); }
    if (a === "full") { close(); return go("/settings/billing"); }
    if (a === "login") { close(); return window.CHARTO_AUTH_OPEN && window.CHARTO_AUTH_OPEN("login"); }
    if (a === "cancel") {
      // A confirmation, not a gauntlet: what happens, one button each way.
      host.innerHTML = `<h2 class="pw-title">Cancel ${esc(host.dataset.plan)}?</h2>
        <p class="pw-msg">Your plan stays active until ${esc(host.dataset.end)} and will not renew. You will not be charged again.
        After that your account moves to Free. Nothing you have made is deleted; alerts above the Free limit pause and can be re-armed.</p>
        <div class="pw-actions pw-split"><button type="button" class="btn" data-pw="keep">Keep my plan</button>
        <button type="button" class="btn pw-danger" data-pw="confirm">Cancel subscription</button></div>`;
      return;
    }
    if (a === "keep") return paintBilling();
    if (a === "confirm") {
      b.disabled = true;
      b.textContent = "Cancelling…";
      let r = null, d = {};
      try {
        r = await _fetch(`${API}/billing/cancel`, { method: "POST", headers: headers({ "Content-Type": "application/json" }), body: "{}" });
        d = await r.json().catch(() => ({}));
      } catch { /* below */ }
      if (r && r.ok) {
        host.innerHTML = `<h2 class="pw-title">Your subscription is cancelled</h2>
          <p class="pw-msg">${esc(host.dataset.plan)} stays active until ${d.active_until ? date(d.active_until) : esc(host.dataset.end)}. You will not be charged again, and you can subscribe again at any time.</p>
          <div class="pw-actions"><button type="button" class="btn cta" data-pw="close">Done</button></div>`;
        if (typeof Plan !== "undefined") Plan.refresh();
      } else {
        host.insertAdjacentHTML("afterbegin", `<div class="pw-note bad"><b>The cancellation did not go through.</b> ${esc(d.error || "Could not reach the server.")} Your subscription was not changed.</div>`);
        b.disabled = false;
        b.textContent = "Cancel subscription";
      }
    }
  }

  // ── every 402 the chart receives ──────────────────────────────────────
  const _fetch = window.fetch.bind(window);
  window.fetch = async function (input, init) {
    const res = await _fetch(input, init);
    if (res.status === 402) {
      const url = String((input && input.url) || input || "");
      // our own beat answers 402 "evicted" every minute; plan.js owns that
      if (!/\/charts\/lease\b/.test(url)) {
        res.clone().json().then((b) => { if (b && b.code && b.feature) show(b); }).catch(() => {});
      }
    }
    return res;
  };

  document.addEventListener("charto:evicted", (e) => evicted(e.detail || {}));
  document.addEventListener("charto:credits", (e) => credits(e.detail));
  // The shell's paywall offers "Use this tab" too; it reaches us here.
  if (inShell()) {
    try { window.parent.addEventListener("pivot:reclaim-tab", () => Plan.reclaim()); } catch { /* not framed by the shell */ }
  }
  loadCatalog().then(() => document.dispatchEvent(new CustomEvent("charto:plan-catalog")));

  return { show, prompt, evicted, credits, openBilling, lockFor, enforced, close };
})();
