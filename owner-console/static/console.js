// Pivot owner console. Vanilla JS, no dependencies, no innerHTML with data:
// every value reaches the page as a text node.
"use strict";

(() => {
  const CSRF = document.querySelector('meta[name="csrf"]')?.content || "";
  const APP = "https://app.pivotnow.in";
  const $app = document.getElementById("app");

  // ── tiny DOM ──────────────────────────────────────────────────────────
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "on") for (const [ev, fn] of Object.entries(v)) el.addEventListener(ev, fn);
      else if (k === "class") el.className = v;
      // Through the CSSOM: the CSP forbids style attributes, not el.style.
      else if (k === "style") for (const decl of String(v).split(";")) {
        const i = decl.indexOf(":");
        if (i > 0) el.style.setProperty(decl.slice(0, i).trim(), decl.slice(i + 1).trim());
      }
      else if (k in el && typeof v !== "string") el[k] = v;
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid == null || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };

  // ── format ────────────────────────────────────────────────────────────
  const inr = (p) => p == null ? "—" : (p < 0 ? "−₹" : "₹") + (Math.abs(p) / 100).toLocaleString("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: p % 100 ? 2 : 0 });
  const when = (t) => t ? new Date(t * 1000).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
  const day = (t) => t ? new Date(t * 1000).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", year: "numeric" }) : "—";
  const n = (x) => x == null ? "—" : Number(x).toLocaleString("en-IN");
  const PLAN = { anonymous: "Signed out", free: "Free", pro: "Pro", pro_plus: "Pro+" };
  const planName = (p) => PLAN[p] || p || "—";

  // ── the wire ──────────────────────────────────────────────────────────
  async function api(method, path, body) {
    let r;
    try {
      r = await fetch("/api" + path, {
        method, credentials: "same-origin", cache: "no-store",
        headers: method === "POST" ? { "Content-Type": "application/json", "X-CSRF": CSRF } : {},
        body: method === "POST" ? JSON.stringify(body || {}) : undefined,
      });
    } catch {
      return { ok: false, status: 0, error: "Could not reach the console." };
    }
    let data = {};
    try { data = await r.json(); } catch { /* empty */ }
    if (r.status === 401) { signedOut(); }
    return r.ok ? { ok: true, status: r.status, data } : { ok: false, status: r.status, error: data.error || `HTTP ${r.status}`, data };
  }
  function signedOut() {
    clear($app).append(h("main", { class: "door" }, h("div", { class: "door-card" },
      h("div", { class: "brand" }, "Signed out"),
      h("p", { class: "muted" }, "The session ended after 30 minutes idle, 8 hours, or a restart."),
      h("button", { on: { click: () => location.assign("/") } }, "Sign in again"))));
  }
  let toastTimer;
  function toast(msg) {
    document.querySelector(".toast")?.remove();
    const t = h("div", { class: "toast", role: "status" }, msg);
    document.body.append(t);
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.remove(), 3200);
  }
  async function copy(text, label) {
    try { await navigator.clipboard.writeText(text); toast(`${label || "Copied"}`); }
    catch { toast("Copy failed. Select the text and copy it."); }
  }
  // A destructive button asks twice: the first click arms it for 4 seconds.
  function confirmBtn(label, armed, fn, cls = "btn sm danger") {
    const b = h("button", { class: cls, type: "button" }, label);
    let t = null;
    b.addEventListener("click", async () => {
      if (!t) {
        b.textContent = armed;
        t = setTimeout(() => { t = null; b.textContent = label; }, 4000);
        return;
      }
      clearTimeout(t); t = null;
      b.disabled = true;
      await fn();
      b.disabled = false;
      b.textContent = label;
    });
    return b;
  }
  const note = (msg, tone) => h("div", { class: `note ${tone || ""}`, role: tone === "bad" ? "alert" : "status" }, msg);
  const table = (cols, rows, onRow) => {
    if (!rows.length) return h("div", { class: "table-wrap" }, h("div", { class: "empty" }, "Nothing here yet."));
    return h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, cols.map((c) => h("th", { class: c.num ? "num" : "" }, c.label)))),
      h("tbody", {}, rows.map((r) => h("tr", { class: onRow ? "click" : "", on: onRow ? { click: () => onRow(r) } : {} },
        cols.map((c) => h("td", { class: c.num ? "num" : "" }, c.get(r))))))));
  };
  const field = (label, input, hint) => h("label", { class: "field" }, h("span", {}, label), input, hint ? h("small", {}, hint) : null);
  const stat = (k, v, d) => h("div", { class: "card stat" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v), d ? h("div", { class: "d" }, d) : null);
  const tag = (txt, cls) => h("span", { class: `tag ${cls || ""}` }, txt);

  // ── drawer ────────────────────────────────────────────────────────────
  function drawer(build) {
    closeDrawer();
    const scrim = h("div", { class: "scrim", on: { click: closeDrawer } });
    const panel = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true" },
      h("button", { class: "btn sm close", type: "button", "aria-label": "Close", on: { click: closeDrawer } }, "Close"));
    document.body.append(scrim, panel);
    const body = h("div", { class: "stack", style: "gap:18px" });
    panel.append(body);
    build(body);
    return body;
  }
  function closeDrawer() { document.querySelectorAll(".scrim,.drawer").forEach((e) => e.remove()); }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

  // ── frame ─────────────────────────────────────────────────────────────
  const VIEWS = [
    ["overview", "Overview"], ["trading", "Trading"], ["codes", "Codes"], ["accounts", "Accounts"], ["grants", "Grants"],
    ["subscriptions", "Subscriptions"], ["events", "Webhooks"], ["audit", "Audit log"], ["system", "System"],
  ];
  let current = (location.hash.slice(1) || "overview");
  const $view = h("main", { class: "view" });
  const $tabs = h("nav", { class: "tabs", "aria-label": "Sections" });
  function renderTabs() {
    clear($tabs).append(...VIEWS.map(([id, label]) => h("button", {
      type: "button", "aria-current": id === current ? "page" : null,
      on: { click: () => go(id) },
    }, label)));
  }
  function go(id) {
    current = VIEWS.some(([v]) => v === id) ? id : "overview";
    history.replaceState(null, "", "#" + current);
    renderTabs();
    closeDrawer();
    clear($view).append(h("p", { class: "muted" }, "Loading"));
    ({ overview, trading, codes, accounts, grants, subscriptions, events, audit, system })[current]();
  }
  const logout = h("form", { method: "post", action: "/logout", on: { submit: async (e) => {
    e.preventDefault();
    await fetch("/logout", { method: "POST", headers: { "X-CSRF": CSRF }, credentials: "same-origin" });
    location.assign("/");
  } } }, h("button", { class: "btn sm", type: "submit" }, "Sign out"));
  $app.append(
    h("header", { class: "top" },
      h("div", { class: "brand" }, h("img", { src: "/static/icon.svg", alt: "", width: 24, height: 24 }), "Owner console"),
      $tabs, h("div", { class: "who" }, logout)),
    $view);

  let catalog = null;
  async function getCatalog() {
    if (!catalog) { const r = await api("GET", "/catalog"); if (r.ok) catalog = r.data; }
    return catalog;
  }

  // ── overview ──────────────────────────────────────────────────────────
  async function overview() {
    const r = await api("GET", "/overview");
    if (!r.ok) return clear($view).append(note(r.error, "bad"));
    const o = r.data;
    const max = Math.max(1, ...o.signups.map((s) => s.n));
    const plans = Object.entries(o.plans).sort();
    const b = o.billing;
    clear($view).append(
      h("div", { class: "head" }, h("div", {}, h("h1", {}, "Overview"), h("p", { class: "sub" }, `As of ${when(o.now)} IST`))),
      h("div", { class: "grid" },
        stat("Accounts", n(o.users.total), `+${n(o.users.day)} today, +${n(o.users.week)} this week, +${n(o.users.month)} in 30 days`),
        stat("Active this week", n(o.users.active_week), "accounts with a session used in 7 days"),
        stat("Paying subscribers", n(o.paid.active + o.paid.past_due + o.paid.cancelling),
          `${n(o.paid.active)} active, ${n(o.paid.past_due)} retrying payment, ${n(o.paid.cancelling)} cancelling`),
        stat("Monthly recurring", inr(o.mrr), "active renewals, yearly counted as a twelfth"),
        stat("Collected, 30 days", inr(o.money_30d.collected), o.money_30d.refunded ? `${inr(o.money_30d.refunded)} refunded` : "no refunds"),
        stat("Free-time grants", n(o.grants.active), `${n(o.grants.accounts)} accounts, comps and codes`),
        stat("Codes", `${n(o.codes.active)} active`, `${n(o.codes.redeemed_week)} used this week, ${n(o.codes.redeemed_month)} in 30 days`),
        stat("Webhooks to review", n(b.events_needing_review_7d), "retries, unmatched or failed, 7 days"),
      ),
      h("div", { class: "card" },
        h("div", { class: "row spread" }, h("h2", {}, "Sign-ups, last 30 days"), h("span", { class: "muted" }, `${n(o.signups.reduce((a, s) => a + s.n, 0))} total`)),
        h("div", { class: "bars", role: "img", "aria-label": "Daily sign-ups for the last 30 days" },
          o.signups.map((s) => h("div", { class: s.n ? "has" : "", title: `${s.day}: ${s.n}`, style: `height:${Math.round((s.n / max) * 100)}%` }))),
        h("div", { class: "axis" }, h("span", {}, o.signups[0].day), h("span", {}, o.signups[o.signups.length - 1].day))),
      h("div", { class: "grid", style: "grid-template-columns:repeat(auto-fill,minmax(280px,1fr))" },
        h("div", { class: "card stack" }, h("h2", {}, "Plans in force"),
          h("dl", { class: "kv" }, plans.flatMap(([p, c]) => [h("dt", {}, planName(p)), h("dd", {}, n(c))]))),
        h("div", { class: "card stack" }, h("h2", {}, "Billing"),
          h("dl", { class: "kv" },
            h("dt", {}, "Payments"), h("dd", {}, b.configured ? (b.test_mode ? tag("test keys", "bad") : tag("live", "on")) : tag("not configured", "bad")),
            h("dt", {}, "Paywall"), h("dd", {}, b.paywall_enabled ? tag("enforced", "on") : tag("off", "off")),
          ))),
    );
  }

  // ── trading: plays' trades and what live plays paid ─────────────────
  let tradeDays = 30;
  async function trading() {
    const r = await api("GET", `/trading?days=${tradeDays}`);
    if (!r.ok) return clear($view).append(note(r.error, "bad"));
    const t = r.data;
    const head = h("div", { class: "head" },
      h("div", {}, h("h1", {}, "Trading"), h("p", { class: "sub" }, t.available ? `Plays' trades and what live plays paid, as of ${when(t.now)} IST` : "")),
      h("div", { class: "seg", role: "group", "aria-label": "Window" }, [7, 30, 90].map((d) => h("button", {
        type: "button", "aria-pressed": String(d === tradeDays), on: { click: () => { tradeDays = d; trading(); } } }, `${d} days`))));
    if (!t.available) return clear($view).append(head, note(`No trading data: ${t.reason}.`, ""));
    const tr = t.trades, rv = t.revenue.month, w = t.wallet, p = t.plays;
    const vol = (b) => `${n(b.trades)} trades, ${inr(b.volume_paise)}`;
    const max = Math.max(1, ...t.series.map((s) => s.trades));
    clear($view).append(head,
      h("div", { class: "grid" },
        stat("Trades", n(tr.total), `${n(tr.entries)} entries, ${n(tr.exits)} exits, ${t.days} days`),
        stat("Trade volume", inr(tr.volume_paise), `average ${inr(tr.avg_paise)} a trade`),
        stat("Traders", n(tr.traders), `${tr.per_trader} trades each`),
        stat("Live trades", n(tr.by_venue.live.trades), `${inr(tr.by_venue.live.volume_paise)} traded live`),
        stat("Pivot revenue, this month", inr(rv.charged_paise), `${inr(rv.base_paise)} plus ${inr(rv.gst_paise)} GST`),
        stat("Waived by the ₹499 cap", inr(rv.waived_paise), `${n(rv.at_cap)} accounts at the cap`),
        stat("Live traders, this month", n(rv.live_traders), `${inr(rv.per_live_trader_paise)} and ${rv.trades_per_live_trader} trades each`),
        stat("Wallets", inr(w.balance_paise), `${n(w.funded)} funded, ${n(w.negative)} below zero, ${n(w.restricted)} paused`),
        stat("Top-ups", n(w.topups), `${inr(w.topped_up_paise)} from ${n(w.topup_accounts)} accounts, ${t.days} days`),
        stat("Entries skipped for money", n(tr.skipped_for_money), "a live entry the wallet could not cover"),
        stat("Plays armed", n(p.armed), `${n(p.armed_live)} live, ${n(p.paused)} paused, ${n(p.created)} new in ${t.days} days`),
        stat("Builds this month", n(p.builds_used_month), `${n(p.unlocked_month)} accounts unlocked by a paper arm`),
      ),
      h("div", { class: "card" },
        h("div", { class: "row spread" }, h("h2", {}, `Trades a day, last ${t.days} days`), h("span", { class: "muted" }, `${n(tr.total)} total`)),
        h("div", { class: "bars", role: "img", "aria-label": "Trades placed by plays each day" },
          t.series.map((s) => h("div", { class: s.trades ? "has" : "", title: `${s.day}: ${s.trades} trades, ${inr(s.volume_paise)}, ${inr(s.revenue_paise)} charged`, style: `height:${Math.round((s.trades / max) * 100)}%` }))),
        h("div", { class: "axis" }, h("span", {}, t.series[0].day), h("span", {}, t.series[t.series.length - 1].day))),
      h("div", { class: "grid", style: "grid-template-columns:repeat(auto-fill,minmax(280px,1fr))" },
        h("div", { class: "card stack" }, h("h2", {}, "By venue"),
          h("dl", { class: "kv" }, h("dt", {}, "Paper"), h("dd", {}, vol(tr.by_venue.paper)), h("dt", {}, "Live"), h("dd", {}, vol(tr.by_venue.live)))),
        h("div", { class: "card stack" }, h("h2", {}, "By segment"),
          h("dl", { class: "kv" }, h("dt", {}, "Intraday, F&O, crypto"), h("dd", {}, vol(tr.by_segment.FNO)), h("dt", {}, "Delivery"), h("dd", {}, vol(tr.by_segment.DELIVERY)))),
        h("div", { class: "card stack" }, h("h2", {}, "By action"),
          h("dl", { class: "kv" }, Object.entries(tr.by_kind).flatMap(([k, c]) => [h("dt", {}, k), h("dd", {}, n(c))]))),
        h("div", { class: "card stack" }, h("h2", {}, "Pricing in force"),
          h("dl", { class: "kv" },
            h("dt", {}, "Per trade"), h("dd", {}, `${inr(t.pricing.fno_paise)}, delivery up to ${inr(t.pricing.delivery_max_paise)}`),
            h("dt", {}, "Monthly cap"), h("dd", {}, inr(t.pricing.cap_paise)),
            h("dt", {}, "Live venue"), h("dd", {}, t.pricing.live_open ? tag("open (paper fills)", "on") : tag("closed", "off")),
            h("dt", {}, "Build limits"), h("dd", {}, t.pricing.enforced ? tag("enforced", "on") : tag("off", "off"))))),
      h("h2", {}, "Most active traders"),
      tr.traders ? table([
        { label: "Account", get: (u) => u.email || `#${u.user_id}` },
        { label: "Trades", get: (u) => n(u.trades) },
        { label: "Volume", get: (u) => inr(u.volume_paise) },
        { label: "Charged", get: (u) => inr(u.charged_paise) },
        { label: "Plays", get: (u) => n(u.plays) },
      ], t.top_traders, (u) => accountDetail(u.user_id)) : note("No play has traded in this window.", ""),
    );
  }

  // ── codes ─────────────────────────────────────────────────────────────
  let codeFilter = { status: "", q: "" };
  async function codes() {
    const qs = new URLSearchParams(Object.entries(codeFilter).filter(([, v]) => v)).toString();
    const r = await api("GET", "/coupons" + (qs ? `?${qs}` : ""));
    if (!r.ok) return clear($view).append(note(r.error, "bad"));
    const list = r.data.coupons;
    const search = h("input", { class: "in", type: "search", placeholder: "Search code, campaign, note", value: codeFilter.q, style: "width:260px" });
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") { codeFilter.q = search.value.trim(); codes(); } });
    const status = h("select", { class: "in", on: { change: (e) => { codeFilter.status = e.target.value; codes(); } } },
      [["", "Every status"], ["active", "Active"], ["disabled", "Disabled"], ["archived", "Archived"]].map(([v, l]) =>
        h("option", { value: v, selected: v === codeFilter.status }, l)));
    const exportHref = "/api/coupons/export.csv" + (qs ? `?${qs}` : "");
    clear($view).append(
      h("div", { class: "head" },
        h("div", {}, h("h1", {}, "Codes"), h("p", { class: "sub" }, "Free time on a paid plan, or a lower price at checkout. Every code has its own limits.")),
        h("div", { class: "row" }, h("a", { class: "btn", href: exportHref, style: "display:inline-flex;align-items:center;text-decoration:none" }, "Export CSV"),
          h("button", { class: "btn primary", type: "button", on: { click: () => newCode() } }, "New code"))),
      h("div", { class: "row" }, search, status, h("span", { class: "muted" }, `${n(list.length)} shown`)),
      table([
        { label: "Code", get: (c) => h("span", { class: "mono" }, c.code) },
        { label: "What it does", get: (c) => c.summary },
        { label: "Status", get: (c) => tag(c.status, c.status === "active" ? "on" : "off") },
        { label: "Used", num: true, get: (c) => `${n(c.redeemed)}${c.reserved ? ` (+${c.reserved} at checkout)` : ""} / ${c.max_redemptions == null ? "no cap" : n(c.max_redemptions)}` },
        { label: "Per account", num: true, get: (c) => n(c.per_user) },
        { label: "Who", get: (c) => c.emails_count ? `${c.emails_count} emails` : c.domain ? `@${c.domain}` : c.new_only ? "new subscribers" : "anyone" },
        { label: "Ends", get: (c) => c.valid_until ? day(c.valid_until) : "never" },
        { label: "Campaign", get: (c) => c.campaign || "" },
      ], list, (c) => codeDetail(c.id)),
    );
  }

  const shareLink = (c) => {
    const plan = c.plan === "any" ? "pro" : c.plan;
    const cycle = c.cycles === "monthly" || c.cycles === "annual" ? `&cycle=${c.cycles}` : "";
    return `${APP}/checkout?plan=${plan}${cycle}&code=${encodeURIComponent(c.code)}`;
  };

  async function codeDetail(id) {
    const r = await api("GET", `/coupons/${id}`);
    if (!r.ok) return toast(r.error);
    const c = r.data.coupon;
    drawer((d) => {
      const dateIn = (t) => h("input", { class: "in", type: "date", value: t ? new Date(t * 1000).toISOString().slice(0, 10) : "" });
      const max = h("input", { class: "in", type: "number", min: 1, value: c.max_redemptions ?? "", placeholder: "no cap" });
      const per = h("input", { class: "in", type: "number", min: 1, value: c.per_user });
      const from = dateIn(c.valid_from), until = dateIn(c.valid_until);
      const emails = h("textarea", { class: "in", placeholder: "one per line or comma separated" }, c.emails.split(",").filter(Boolean).join("\n"));
      const domain = h("input", { class: "in", value: c.domain, placeholder: "e.g. iitb.ac.in" });
      const newOnly = h("input", { type: "checkbox", checked: !!c.new_only });
      const campaign = h("input", { class: "in", value: c.campaign, maxLength: 80 });
      const noteIn = h("textarea", { class: "in" }, c.note);
      const msg = h("div");
      const save = async () => {
        const body = {
          max_redemptions: max.value === "" ? null : Number(max.value), per_user: Number(per.value || 1),
          valid_from: toEpoch(from.value), valid_until: toEpoch(until.value, true),
          emails: emails.value, domain: domain.value, new_only: newOnly.checked,
          campaign: campaign.value, note: noteIn.value,
        };
        const s = await api("POST", `/coupons/${id}`, body);
        clear(msg).append(s.ok ? note("Saved.", "good") : note(s.error, "bad"));
        if (s.ok) codes();
      };
      const setStatus = async (st) => {
        const s = await api("POST", `/coupons/${id}`, { status: st });
        if (!s.ok) return toast(s.error);
        toast(`${c.code} is ${st}`); codes(); codeDetail(id);
      };
      const link = shareLink(c);
      d.append(
        h("div", {}, h("h2", { class: "mono", style: "font-family:var(--mono)" }, c.code), h("p", { class: "sub" }, c.summary)),
        h("div", { class: "row" },
          h("button", { class: "btn sm", type: "button", on: { click: () => copy(c.code, "Code copied") } }, "Copy code"),
          h("button", { class: "btn sm", type: "button", on: { click: () => copy(link, "Link copied") } }, "Copy checkout link"),
          c.status !== "active" ? h("button", { class: "btn sm", type: "button", on: { click: () => setStatus("active") } }, "Enable") : null,
          c.status === "active" ? confirmBtn("Disable", "Click again to disable", () => setStatus("disabled")) : null,
          c.status !== "archived" ? confirmBtn("Archive", "Click again to archive", () => setStatus("archived")) : null,
          c.batch ? confirmBtn("Disable the whole batch", "Click again: every code in this batch", async () => {
            const s = await api("POST", "/coupons/batch", { batch: c.batch, status: "disabled" });
            toast(s.ok ? `${s.data.updated} codes disabled` : s.error); codes();
          }) : null),
        h("p", { class: "muted mono", style: "font-size:12px;word-break:break-all" }, link),
        h("dl", { class: "kv" },
          h("dt", {}, "Status"), h("dd", {}, tag(c.status, c.status === "active" ? "on" : "off")),
          h("dt", {}, "Kind"), h("dd", {}, c.kind === "access" ? `Free time: ${planName(c.plan)} for ${c.access_days} days` : "Discount at checkout"),
          c.kind === "discount" ? [h("dt", {}, "Prices"), h("dd", {}, c.offers.map((o) => h("div", {}, `${o.plan_name} ${o.cycle}: ${inr(o.was)} → ${inr(o.amount)}`)))] : null,
          c.kind === "discount" ? [h("dt", {}, "Lasts"), h("dd", {}, c.discount_cycles ? `${c.discount_cycles} billing cycles, then the subscription ends` : "every renewal")] : null,
          h("dt", {}, "Used"), h("dd", {}, Object.entries(c.uses).map(([k, v]) => `${v} ${k}`).join(", ") || "not yet"),
          h("dt", {}, "Created"), h("dd", {}, `${when(c.created)} by ${c.created_by}`),
          c.batch ? [h("dt", {}, "Batch"), h("dd", { class: "mono" }, c.batch)] : null),
        h("h3", {}, "Limits and audience"),
        h("div", { class: "form" },
          field("Total uses", max, "across everyone; empty means no cap"),
          field("Uses per account", per),
          field("Starts", from), field("Ends", until, "the code stops working at the end of this day, IST"),
          h("div", { class: "wide" }, field("Only these emails", emails, "leave empty for anyone")),
          field("Only this email domain", domain),
          h("label", { class: "check" }, newOnly, "Only accounts that have never subscribed"),
          field("Campaign", campaign),
          h("div", { class: "wide" }, field("Note", noteIn))),
        h("div", { class: "row" }, h("button", { class: "btn primary", type: "button", on: { click: save } }, "Save changes"), msg),
        h("p", { class: "fine" }, "The plan, days and discount of a code cannot change once it exists, because a payment plan may already carry them. Disable it and make a new one."),
        h("h3", {}, "Who used it"),
        table([
          { label: "Account", get: (x) => x.email || `#${x.user_id}` },
          { label: "State", get: (x) => tag(x.state, x.state === "redeemed" ? "on" : x.state === "revoked" ? "bad" : "off") },
          { label: "Paid", num: true, get: (x) => x.amount ? inr(x.amount) : "free" },
          { label: "When", get: (x) => when(x.created) },
          { label: "", get: (x) => (x.state === "redeemed" && !x.provider_sub_id) ? confirmBtn("Revoke", "Click again", async () => {
            const s = await api("POST", `/redemptions/${x.id}/revoke`, {});
            toast(s.ok ? "Free time revoked" : s.error); if (s.ok) codeDetail(id);
          }) : "" },
        ], r.data.redemptions),
      );
    });
  }

  function toEpoch(v, endOfDay) {
    if (!v) return null;
    // A date is an IST calendar day: starts at 00:00 IST, ends at 23:59:59 IST.
    const t = Date.parse(`${v}T${endOfDay ? "23:59:59" : "00:00:00"}+05:30`);
    return Number.isFinite(t) ? Math.floor(t / 1000) : null;
  }

  async function newCode() {
    const cat = await getCatalog();
    const paid = (cat?.plans || []).filter((p) => p.id !== "free");
    const price = (plan, cycle) => paid.find((p) => p.id === plan)?.prices?.[cycle]?.amount;
    drawer((d) => {
      let kind = "access";
      const seg = h("div", { class: "seg", role: "group", "aria-label": "Kind" });
      const kindBtn = (k, label) => h("button", { type: "button", "aria-pressed": String(k === kind), on: { click: () => { kind = k; paint(); } } }, label);
      const plan = h("select", { class: "in" });
      const days = h("input", { class: "in", type: "number", min: 1, max: 3660, value: 30 });
      const offKind = h("select", { class: "in" }, h("option", { value: "percent" }, "Percent off"), h("option", { value: "rupees" }, "Rupees off"));
      const off = h("input", { class: "in", type: "number", min: 1, value: 40 });
      const cycles = h("select", { class: "in" }, h("option", { value: "any" }, "Monthly and yearly"), h("option", { value: "monthly" }, "Monthly only"), h("option", { value: "annual" }, "Yearly only"));
      const dcycles = h("input", { class: "in", type: "number", min: 1, max: 120, placeholder: "every renewal" });
      const mode = h("select", { class: "in" }, h("option", { value: "one" }, "One code"), h("option", { value: "batch" }, "A batch of unique codes"));
      const custom = h("input", { class: "in mono", placeholder: "leave empty to generate", maxLength: 40, style: "text-transform:uppercase" });
      const count = h("input", { class: "in", type: "number", min: 2, max: 1000, value: 25 });
      const prefix = h("input", { class: "in mono", value: "PIVOT", maxLength: 16, style: "text-transform:uppercase" });
      const max = h("input", { class: "in", type: "number", min: 1, placeholder: "no cap" });
      const per = h("input", { class: "in", type: "number", min: 1, value: 1 });
      const from = h("input", { class: "in", type: "date" });
      const until = h("input", { class: "in", type: "date" });
      const emails = h("textarea", { class: "in", placeholder: "one per line or comma separated" });
      const domain = h("input", { class: "in", placeholder: "e.g. iitb.ac.in" });
      const newOnly = h("input", { type: "checkbox" });
      const campaign = h("input", { class: "in", maxLength: 80, placeholder: "e.g. diwali-2026, creator-ravi" });
      const noteIn = h("textarea", { class: "in", placeholder: "for you: who asked, why" });
      const preview = h("div", { class: "note" });
      const msg = h("div");
      const out = h("div", { class: "stack" });
      const kindRows = h("div", { class: "form" });
      const modeRows = h("div", { class: "form" });

      function paint() {
        clear(seg).append(kindBtn("access", "Free time"), kindBtn("discount", "Discount"));
        const keep = plan.value;
        clear(plan).append(...paid.map((p) => h("option", { value: p.id }, p.name)), kind === "discount" ? h("option", { value: "any" }, "Any paid plan") : null);
        if ([...plan.options].some((o) => o.value === keep)) plan.value = keep;
        clear(kindRows).append(...(kind === "access"
          ? [field("Plan it unlocks", plan), field("Free days", days, "the plan ends on its own after this")]
          : [field("Applies to", plan), field("Billing", cycles), field("Discount", offKind), field(offKind.value === "percent" ? "Percent (1 to 95)" : "Rupees off", off),
             field("Billing cycles at this price", dcycles, "empty: every renewal. Set: the subscription ends after that many, then renews at full price")]));
        clear(modeRows).append(field("How many", mode), ...(mode.value === "one" ? [field("Code", custom, "4 to 40 letters, digits or dashes")] : [field("Number of codes", count), field("Prefix", prefix)]));
        summarize();
      }
      function summarize() {
        let s;
        if (kind === "access") s = `${plan.selectedOptions[0]?.textContent || ""} free for ${days.value || "?"} days, no payment.`;
        else {
          const plans = plan.value === "any" ? paid.map((p) => p.id) : [plan.value];
          const cys = cycles.value === "any" ? ["monthly", "annual"] : [cycles.value];
          const lines = [];
          for (const p of plans) for (const cy of cys) {
            const was = price(p, cy); if (!was) continue;
            const v = Number(off.value || 0);
            const now = offKind.value === "percent" ? Math.floor(was * (100 - v) / 100 / 100) * 100 : Math.floor((was - v * 100) / 100) * 100;
            lines.push(`${paid.find((x) => x.id === p)?.name} ${cy === "annual" ? "yearly" : "monthly"}: ${inr(was)} → ${inr(Math.max(0, now))}`);
          }
          s = lines.join(" · ") + (dcycles.value ? ` for ${dcycles.value} billing cycles` : ", every renewal");
        }
        const lim = [max.value ? `${max.value} uses in total` : "no total cap", `${per.value || 1} per account`];
        if (until.value) lim.push(`ends ${until.value}`);
        if (emails.value.trim()) lim.push(`${emails.value.split(/[\n,]/).filter((x) => x.trim()).length} named accounts`);
        if (domain.value.trim()) lim.push(`@${domain.value.trim()} only`);
        if (newOnly.checked) lim.push("new subscribers only");
        clear(preview).append(h("strong", {}, s), h("div", { class: "muted" }, lim.join(", ")));
      }
      [plan, days, offKind, off, cycles, dcycles, max, per, until, emails, domain, newOnly].forEach((el) => el.addEventListener("input", () => { if (el === offKind) paint(); else summarize(); }));
      mode.addEventListener("change", paint);

      async function submit(e) {
        e.preventDefault();
        const body = {
          kind, plan: plan.value,
          max_redemptions: max.value ? Number(max.value) : null, per_user: Number(per.value || 1),
          valid_from: toEpoch(from.value), valid_until: toEpoch(until.value, true),
          emails: emails.value, domain: domain.value, new_only: newOnly.checked,
          campaign: campaign.value, note: noteIn.value,
        };
        if (kind === "access") body.access_days = Number(days.value);
        else {
          body.cycles = cycles.value;
          if (offKind.value === "percent") body.percent_off = Number(off.value); else body.amount_off = Math.round(Number(off.value) * 100);
          if (dcycles.value) body.discount_cycles = Number(dcycles.value);
        }
        if (mode.value === "one") { if (custom.value.trim()) body.code = custom.value.trim(); }
        else { body.count = Number(count.value); body.prefix = prefix.value; }
        btn.disabled = true;
        const r = await api("POST", "/coupons", body);
        btn.disabled = false;
        clear(msg);
        if (!r.ok) return msg.append(note(r.error, "bad"));
        const made = r.data.coupons;
        const text = made.map((c) => c.code).join("\n");
        clear(out).append(
          note(`${made.length} code${made.length === 1 ? "" : "s"} made: ${made[0].summary}.`, "good"),
          h("div", { class: "codes-out" }, text),
          h("div", { class: "row" },
            h("button", { class: "btn sm", type: "button", on: { click: () => copy(text, "Codes copied") } }, made.length > 1 ? "Copy all" : "Copy code"),
            made.length === 1 ? h("button", { class: "btn sm", type: "button", on: { click: () => copy(shareLink(made[0]), "Link copied") } }, "Copy checkout link") : null,
            r.data.batch ? h("a", { class: "btn sm", href: `/api/coupons/export.csv?batch=${r.data.batch}`, style: "display:inline-flex;align-items:center;text-decoration:none" }, "Download CSV") : null));
        codes();
      }
      const btn = h("button", { class: "btn primary", type: "submit" }, "Make code");
      d.append(
        h("div", {}, h("h2", {}, "New code"), h("p", { class: "sub" }, "Free time is redeemed in the app with no payment. A discount lowers the price at checkout through Razorpay.")),
        h("form", { class: "stack", style: "gap:18px", on: { submit } },
          seg, kindRows,
          h("h3", {}, "The code"), modeRows,
          h("h3", {}, "Limits"),
          h("div", { class: "form" },
            field("Total uses", max, "across everyone; empty means no cap"), field("Uses per account", per),
            field("Starts", from, "empty: now"), field("Ends", until, "empty: never")),
          h("h3", {}, "Who can use it"),
          h("div", { class: "form" },
            h("div", { class: "wide" }, field("Only these emails", emails, "for specific people; leave empty for anyone with the code")),
            field("Only this email domain", domain),
            h("label", { class: "check" }, newOnly, "Only accounts that have never subscribed")),
          h("h3", {}, "For your records"),
          h("div", { class: "form" }, field("Campaign", campaign), h("div", { class: "wide" }, field("Note", noteIn))),
          preview, h("div", { class: "row" }, btn, msg)),
        out);
      paint();
    });
  }

  // ── accounts ──────────────────────────────────────────────────────────
  let userQ = "";
  async function accounts() {
    const r = await api("GET", "/users" + (userQ ? `?q=${encodeURIComponent(userQ)}` : ""));
    if (!r.ok) return clear($view).append(note(r.error, "bad"));
    const search = h("input", { class: "in", type: "search", placeholder: "Email, name or account number", value: userQ, style: "width:300px" });
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") { userQ = search.value.trim(); accounts(); } });
    clear($view).append(
      h("div", { class: "head" }, h("div", {}, h("h1", {}, "Accounts"), h("p", { class: "sub" }, "The newest 100, or what you search for. Open one to grant a plan or end its sessions."))),
      h("div", { class: "row" }, search, h("button", { class: "btn", type: "button", on: { click: () => { userQ = search.value.trim(); accounts(); } } }, "Search")),
      table([
        { label: "#", num: true, get: (u) => u.id },
        { label: "Email", get: (u) => u.email },
        { label: "Plan now", get: (u) => tag(planName(u.plan), u.plan === "free" ? "off" : "on") },
        { label: "Subscription", get: (u) => u.subscription ? `${planName(u.subscription.plan)} · ${u.subscription.status} · ${u.subscription.provider || "—"}` : "" },
        { label: "Joined", get: (u) => day(u.created) },
        { label: "Last seen", get: (u) => u.last_seen ? when(u.last_seen) : "—" },
        { label: "Verified", get: (u) => u.verified == null ? "" : u.verified ? "yes" : "no" },
      ], r.data.users, (u) => accountDetail(u.id)));
  }

  async function accountDetail(id) {
    const [r, cat] = await Promise.all([api("GET", `/users/${id}`), getCatalog()]);
    if (!r.ok) return toast(r.error);
    const u = r.data.user;
    const sub = u.billing.subscription;
    drawer((d) => {
      const gPlan = h("select", { class: "in" }, (cat?.plans || []).filter((p) => p.id !== "free").map((p) => h("option", { value: p.id }, p.name)));
      const gDays = h("input", { class: "in", type: "number", min: 1, placeholder: "empty: no end" });
      const gReason = h("input", { class: "in", placeholder: "why (kept in the audit log)" });
      const fKey = h("select", { class: "in" }, Object.entries(cat?.features || {}).filter(([, f]) => f.kind !== "flag").map(([k, f]) => h("option", { value: k }, `${f.label} (${k})`)));
      const fVal = h("input", { class: "in", placeholder: "a number, or unlimited" });
      const fDays = h("input", { class: "in", type: "number", min: 1, placeholder: "empty: no end" });
      const fReason = h("input", { class: "in", placeholder: "why" });
      const msg = h("div");
      const grant = async (body) => {
        const s = await api("POST", `/users/${id}/grant`, body);
        clear(msg).append(s.ok ? note(`Granted. Plan now: ${planName(s.data.plan)}.`, "good") : note(s.error, "bad"));
        if (s.ok) setTimeout(() => accountDetail(id), 600);
      };
      const now = Date.now() / 1000;
      d.append(
        h("div", {}, h("h2", {}, u.email), h("p", { class: "sub" }, `Account #${u.id}${u.name ? ` · ${u.name}` : ""}`)),
        h("dl", { class: "kv" },
          h("dt", {}, "Plan now"), h("dd", {}, tag(planName(u.plan), u.plan === "free" ? "off" : "on")),
          h("dt", {}, "Subscription"), h("dd", {}, sub ? `${planName(sub.plan)} ${sub.cycle}, ${sub.status}${sub.cancel_at_period_end ? ", cancelling" : ""} (${sub.provider || "none"})` : "none"),
          sub?.period_end ? [h("dt", {}, "Paid through"), h("dd", {}, when(sub.period_end))] : null,
          h("dt", {}, "Joined"), h("dd", {}, when(u.created)),
          h("dt", {}, "Sessions"), h("dd", {}, `${u.sessions} open${u.last_seen ? `, last used ${when(u.last_seen)}` : ""}`),
          h("dt", {}, "Email verified"), h("dd", {}, u.verified == null ? "—" : u.verified ? "yes" : "no")),
        h("div", { class: "row" }, confirmBtn("Sign out everywhere", "Click again to end every session", async () => {
          const s = await api("POST", `/users/${id}/signout`, {});
          toast(s.ok ? `${s.data.sessions_ended} sessions ended` : s.error); if (s.ok) accountDetail(id);
        })),
        h("h3", {}, "Grant a plan"),
        h("div", { class: "form" }, field("Plan", gPlan), field("Days", gDays), h("div", { class: "wide" }, field("Reason", gReason))),
        h("div", { class: "row" }, h("button", { class: "btn primary", type: "button", on: { click: () => grant({ plan: gPlan.value, days: gDays.value || null, reason: gReason.value }) } }, "Grant plan")),
        h("h3", {}, "Set one limit"),
        h("div", { class: "form" }, field("Feature", fKey), field("Value", fVal, "e.g. 500 AI credits, or unlimited"), field("Days", fDays), h("div", { class: "wide" }, field("Reason", fReason))),
        h("div", { class: "row" }, h("button", { class: "btn", type: "button", on: { click: () => grant({ feature: fKey.value, value: fVal.value, days: fDays.value || null, reason: fReason.value }) } }, "Set limit"), msg),
        h("h3", {}, "Grants"),
        table([
          { label: "What", get: (g) => g.plan ? planName(g.plan) : `${g.feature} = ${g.value}` },
          { label: "Ends", get: (g) => g.expires ? when(g.expires) : "never" },
          { label: "Reason", get: (g) => g.reason },
          { label: "By", get: (g) => g.granted_by },
          { label: "", get: (g) => (!g.expires || g.expires > now) ? confirmBtn("Revoke", "Click again", async () => {
            const s = await api("POST", `/grants/${g.id}/revoke`, {});
            toast(s.ok ? "Grant revoked" : s.error); if (s.ok) accountDetail(id);
          }) : tag("ended", "off") },
        ], u.grants),
        h("h3", {}, "Codes used"),
        table([
          { label: "Code", get: (x) => h("span", { class: "mono" }, x.code) },
          { label: "State", get: (x) => x.state },
          { label: "Paid", num: true, get: (x) => x.amount ? inr(x.amount) : "free" },
          { label: "When", get: (x) => when(x.created) },
        ], u.redemptions),
        h("h3", {}, "Usage this period"),
        h("dl", { class: "kv" }, Object.entries(u.billing.features).filter(([, f]) => f.kind === "quota").flatMap(([k, f]) =>
          [h("dt", {}, f.label), h("dd", {}, `${n(f.used)} of ${f.limit == null ? "unlimited" : n(f.limit)}`)])),
      );
    });
  }

  // ── grants, subscriptions, webhooks, audit, system ────────────────────
  let allGrants = false;
  async function grants() {
    const r = await api("GET", "/grants" + (allGrants ? "?all=1" : ""));
    if (!r.ok) return clear($view).append(note(r.error, "bad"));
    const now = Date.now() / 1000;
    clear($view).append(
      h("div", { class: "head" }, h("div", {}, h("h1", {}, "Grants"), h("p", { class: "sub" }, "Plans and limits given without payment: comps, codes, deals.")),
        h("label", { class: "check" }, h("input", { type: "checkbox", checked: allGrants, on: { change: (e) => { allGrants = e.target.checked; grants(); } } }), "Include ended")),
      table([
        { label: "Account", get: (g) => g.email || `#${g.user_id}` },
        { label: "What", get: (g) => g.plan ? planName(g.plan) : `${g.feature} = ${g.value}` },
        { label: "Ends", get: (g) => g.expires ? when(g.expires) : "never" },
        { label: "Reason", get: (g) => g.reason },
        { label: "By", get: (g) => g.granted_by },
        { label: "Given", get: (g) => day(g.created) },
        { label: "", get: (g) => (!g.expires || g.expires > now) ? confirmBtn("Revoke", "Click again", async () => {
          const s = await api("POST", `/grants/${g.id}/revoke`, {});
          toast(s.ok ? "Grant revoked" : s.error); if (s.ok) grants();
        }) : tag("ended", "off") },
      ], r.data.grants));
  }

  async function subscriptions() {
    const r = await api("GET", "/subscriptions");
    if (!r.ok) return clear($view).append(note(r.error, "bad"));
    clear($view).append(
      h("div", { class: "head" }, h("div", {}, h("h1", {}, "Subscriptions"), h("p", { class: "sub" }, "Every subscription row, newest change first. Refunds and cancellations are made in the Razorpay dashboard."))),
      table([
        { label: "Account", get: (s) => s.email || `#${s.user_id}` },
        { label: "Plan", get: (s) => `${planName(s.plan)} ${s.cycle}` },
        { label: "Status", get: (s) => tag(s.status + (s.cancel_at_period_end ? ", cancelling" : ""), s.status === "active" ? "on" : s.status === "past_due" ? "bad" : "off") },
        { label: "Provider", get: (s) => s.provider || "—" },
        { label: "Period ends", get: (s) => when(s.period_end) },
        { label: "Updated", get: (s) => when(s.updated) },
      ], r.data.subscriptions, (s) => accountDetail(s.user_id)));
  }

  async function events() {
    const r = await api("GET", "/events");
    if (!r.ok) return clear($view).append(note(r.error, "bad"));
    const bad = (x) => /^retry|^no user|exception/.test(x.result || "");
    clear($view).append(
      h("div", { class: "head" }, h("div", {}, h("h1", {}, "Webhooks"), h("p", { class: "sub" }, "The latest 200 events Razorpay sent, and what billing did with each."))),
      table([
        { label: "Received", get: (e) => when(e.received) },
        { label: "Event", get: (e) => e.type },
        { label: "Result", get: (e) => h("span", { class: bad(e) ? "down" : "" }, (e.result || "").slice(0, 160)) },
        { label: "Event id", get: (e) => h("span", { class: "mono muted", style: "font-size:12px" }, e.event_id) },
      ], r.data.events));
  }

  async function audit() {
    const r = await api("GET", "/audit");
    if (!r.ok) return clear($view).append(note(r.error, "bad"));
    clear($view).append(
      h("div", { class: "head" }, h("div", {}, h("h1", {}, "Audit log"), h("p", { class: "sub" }, "Every change made from this console, with the address it came from."))),
      table([
        { label: "When", get: (a) => when(a.ts) },
        { label: "Action", get: (a) => h("span", { class: "mono" }, a.action) },
        { label: "By", get: (a) => a.actor },
        { label: "From", get: (a) => h("span", { class: "mono" }, a.ip) },
        { label: "Detail", get: (a) => h("span", { class: "muted", style: "font-size:12px;word-break:break-word" }, (a.detail || "").slice(0, 300)) },
      ], r.data.audit));
  }

  async function system() {
    const [hr, ov] = await Promise.all([api("GET", "/health"), api("GET", "/overview")]);
    clear($view).append(h("div", { class: "head" }, h("div", {}, h("h1", {}, "System"), h("p", { class: "sub" }, "The server's own health report, read through the signed API."))));
    if (!hr.ok) return $view.append(note(hr.error, "bad"));
    const rep = hr.data;
    const rows = Object.entries(rep).filter(([k]) => !["status"].includes(k));
    $view.append(
      h("div", { class: "grid" },
        stat("Health", rep.status === 200 ? "Healthy" : `Degraded (${rep.status})`, rep.ok === false ? "a check failed" : ""),
        ov.ok ? stat("Payments", ov.data.billing.configured ? (ov.data.billing.test_mode ? "Test keys" : "Live") : "Not configured", "Razorpay") : null,
        ov.ok ? stat("Paywall", ov.data.billing.paywall_enabled ? "Enforced" : "Off", "PAYWALL_ENABLED") : null),
      h("div", { class: "card" }, h("dl", { class: "kv" }, rows.flatMap(([k, v]) =>
        [h("dt", {}, k), h("dd", { class: typeof v === "object" ? "mono" : "", style: "font-size:12px;white-space:pre-wrap" }, typeof v === "object" ? JSON.stringify(v, null, 1) : String(v))]))));
  }

  renderTabs();
  go(current);
})();
