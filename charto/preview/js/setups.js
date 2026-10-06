/* Charto preview — shared setups.
 *
 * A trader publishes the desk they are looking at — panes, drawings, what the
 * chat drew, indicators, and (if they choose) the conversation — as a frozen,
 * read-only SETUP behind an unlisted link. Anyone with the link sees it on a
 * live chart, exactly as it was; a signed-in reader can "Make it mine" and
 * get an editable copy in their own layouts to carry the analysis on.
 *
 * ── three surfaces ────────────────────────────────────────────────────────
 *   publish()  the Share dialog: title, note, what travels, the link.
 *   manage()   the author's list: views, copies, copy link, unpublish.
 *   view mode  ?symbol=X&view=<token>: the setup on the chart, the chat panel
 *              turned into the setup's card and its conversation, and one bar
 *              of actions over the candles.
 *
 * ── why a view session cannot hurt the viewer's own work ──────────────────
 * The chart saves everything it shows into the viewer's storage. In a view
 * session js/store.js holds all of it in memory, drawings.js and auth.js
 * write nothing, layouts.js adopts no layout and autosaves nothing, and
 * chat.js files nothing. The server side is read-only by construction
 * (dataserver /setup is a GET that returns a snapshot). See shares.py.
 *
 * ── what travels ──────────────────────────────────────────────────────
 * The whole workspace, not only the chart: every widget, where it sits and
 * its settings (Dock.exportState), and — if the author leaves it on — what
 * the notes and sheets hold. Documents' files and the author's custom
 * indicators stay with the author. The dialog draws the workspace as it is,
 * tile for tile, so the author sees exactly what a reader will open.
 */
"use strict";

const Setups = (() => {
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";
  const BASE = (document.currentScript && document.currentScript.src) || location.href;
  const el = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const toast = (m) => Layouts.toast(m);
  const VIEW = Store.viewOnly ? Store.viewToken : "";
  let viewing = null;                 // the setup on screen, in a view session

  /* ══ the visual kit ══════════════════════════════════════════════════ */

  /** The bars ON SCREEN, not the whole history loaded behind them: a
   *  sparkline beside a picture of the desk has to describe that picture. */
  function onScreen(c) {
    const bars = (c && c.state && c.state.bars) || [];
    try {
      const r = c.chart.timeScale().getVisibleLogicalRange();
      if (r) return bars.slice(Math.max(0, Math.floor(r.from)), Math.ceil(r.to) + 1);
    } catch { /* no chart yet: fall back to the tail */ }
    return bars.slice(-120);
  }
  /** Closes as a small SVG line; green or red only for the direction. */
  function spark(bars) {
    const closes = (bars || []).map((b) => b.close).filter((x) => Number.isFinite(x));
    if (closes.length < 2) return null;
    const step = Math.max(1, Math.floor(closes.length / 60));
    const pts = closes.filter((_, i) => i % step === 0 || i === closes.length - 1);
    const lo = Math.min(...pts), hi = Math.max(...pts), span = hi - lo || 1;
    const W = 120, H = 30;
    const xy = pts.map((p, i) => [(i / (pts.length - 1)) * W, H - 2 - ((p - lo) / span) * (H - 4)]);
    const line = xy.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
    const chg = (closes[closes.length - 1] / closes[0] - 1) * 100;
    const dir = chg >= 0 ? "up" : "down";
    const svg = `<svg class="sx-spark ${dir}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">` +
      `<polygon points="0,${H} ${line} ${W},${H}"/><polyline points="${line}"/></svg>`;
    return { svg, chg, dir };
  }

  function indicatorName(id) {
    const c = window.__charto;
    const def = c && c.ind && (c.ind.CATALOG || []).find((d) => d.id === id);
    if (def && (def.short || def.label || def.name)) {
      return String(def.short || def.label || def.name);
    }
    const m = /^([a-z_]+?)(\d+)$/.exec(id || "");
    return m ? `${m[1].toUpperCase()} ${m[2]}` : String(id || "").toUpperCase();
  }

  const typeMeta = (type) => (type === "main" ? { title: "Chart", icon: "candles" }
    : (typeof Dock !== "undefined" && Dock.meta && Dock.meta(type)) || { title: type, icon: "widgets" });

  /** The tiled tree of a workspace state, each group by its active widget. */
  function shapeOf(st) {
    if (!st || !st.tree || !st.groups || !st.inst) return null;
    const g = (n) => {
      if (!n) return null;
      if (n.k === "g") {
        const gr = st.groups[n.g];
        if (!gr) return null;
        const tabs = gr.tabs.map((id) => (st.inst[id] || {}).type).filter(Boolean);
        return tabs.length ? { g: true, tabs, active: (st.inst[gr.active] || {}).type || tabs[0] } : null;
      }
      const kids = (n.c || []).map((c, j) => [g(c), (n.s || [])[j] || 1]).filter(([c]) => c);
      return kids.length ? { k: n.k, c: kids.map(([c]) => c), s: kids.map(([, w]) => w) } : null;
    };
    return { tree: g(st.tree), floats: (st.floats || []).map((f) => g({ k: "g", g: f.gid })).filter(Boolean) };
  }
  const widgetsIn = (st) => (st && st.inst ? Object.values(st.inst).filter((i) => i.type !== "main" && i.type !== "slot").length : 0);

  /** The workspace in miniature: one tile per group at its real share of the
   *  screen, the chart tile showing the chart. */
  function deskMini(shape, thumb, sym) {
    let i = 0;
    const tile = (n) => {
      const chart = n.active === "main";
      const m = chart ? { title: sym || "Chart", icon: "candles" } : typeMeta(n.active);
      const more = n.tabs.length - 1;
      return `<div class="sx-tile${chart ? " chart" : ""}" style="--i:${i++}">` +
        (chart && thumb ? `<img src="${esc(thumb)}" alt="">` : `<i class="sx-glyph">${Icons.svg(m.icon, "sm")}</i>`) +
        `<span class="sx-lab">${Icons.svg(m.icon, "xs")}<b>${esc(m.title)}</b>${more > 0 ? `<em>+${more}</em>` : ""}</span></div>`;
    };
    const node = (n) => {
      if (n.g) return tile(n);
      const tot = n.s.reduce((x, y) => x + y, 0) || 1;
      return `<div class="sx-split ${n.k}">` +
        n.c.map((c, j) => `<div class="sx-cell" style="flex:${(n.s[j] / tot).toFixed(4)} 1 0">${node(c)}</div>`).join("") + `</div>`;
    };
    const body = shape && shape.tree ? node(shape.tree) : tile({ g: true, tabs: ["main"], active: "main" });
    const fl = shape && shape.floats && shape.floats.length
      ? `<div class="sx-floats">${shape.floats.map(tile).join("")}</div>` : "";
    return `<div class="sx-desk">${body}${fl}</div>`;
  }

  /** What a desk carries, as chips. The same words in the Share dialog and
   *  on the setup card, so the author sees what the reader gets. */
  function manifest(spec, chatTurns, dataOn) {
    const ws = (spec && spec.workspace) || {};
    const charts = (spec && spec.charts) || [];
    const st = spec && spec.dock && spec.dock.state;
    const n = (v, one, many) => `${v} ${v === 1 ? one : many}`;
    const inds = (ws.indicators || []).map(indicatorName);
    const chips = [
      ["candles", n(charts.length, "chart", "charts")],
      ["pen", n((ws.drawings || []).length, "drawing", "drawings")],
      ["indicators", n(inds.length, "indicator", "indicators"), inds.slice(0, 6).join(", ")],
    ];
    if ((ws.scene || []).length) chips.push(["sparkles", n(ws.scene.length, "chat annotation", "chat annotations")]);
    if (st || dataOn !== undefined) chips.push(["widgets", n(widgetsIn(st), "widget", "widgets"), "Every widget with its settings"]);
    if (dataOn) chips.push(["note", "Notes and sheets"]);
    if (chatTurns) chips.push(["chat", n(chatTurns, "chat turn", "chat turns")]);
    return chips.map(([ic, t, tip]) => `<span class="sx-chip"${tip ? ` title="${esc(tip)}"` : ""}>${Icons.svg(ic, "xs")}${esc(t)}</span>`).join("");
  }

  /** The instrument strip: logo, name, interval, the move on screen. */
  function idStrip(sym, iv, sp) {
    const logo = typeof Universe !== "undefined" && Universe.logoHTML ? Universe.logoHTML(sym, "sx-logo") : "";
    return `<div class="sx-id">${logo || `<span class="sx-logo sx-mono">${esc(String(sym || "?").charAt(0))}</span>`}` +
      `<div class="sx-idt"><b>${esc(sym)}</b>${iv ? `<span>${esc(iv)}</span>` : ""}</div>` +
      (sp ? `<span class="sx-gap"></span>${sp.svg}<b class="sx-chg ${sp.dir}">${sp.chg >= 0 ? "+" : "−"}${Math.abs(sp.chg).toFixed(2)}%</b>` : "") +
      `</div>`;
  }

  /** The credit chain, nearest first. */
  function lineage(chain) {
    if (!chain || !chain.length) return "";
    return `<div class="sx-lineage"><span>Built on</span>` + chain.slice(0, 5).map((c) =>
      `<span class="sx-credit">${Icons.svg("link", "xs")}“${esc(c.title)}” by ${esc(c.by)}</span>`).join("") + `</div>`;
  }

  /* ── the workspace's own contents ─────────────────────────────────────
   * Notes travel as blocks of plain text (the server strips angle brackets
   * from everything shared, so HTML would not survive, and should not);
   * sheets as their cells. Both are put back by applyDesk. */
  function noteBlocks(html) {
    const doc = new DOMParser().parseFromString(`<div>${html || ""}</div>`, "text/html");
    const out = [];
    const walk = (el) => {
      for (const n of el.children) {
        const t = n.tagName;
        if (/^(UL|OL|DIV|SECTION|ARTICLE)$/.test(t) && n.children.length) { walk(n); continue; }
        const text = n.textContent.replace(/\s+/g, " ").trim();
        if (!text) continue;
        out.push({ t: /^H[1-6]$/.test(t) ? "h" : t === "LI" ? "li" : t === "BLOCKQUOTE" ? "q" : "p", s: text.slice(0, 4000) });
      }
    };
    walk(doc.body.firstChild);
    if (!out.length) { const t = doc.body.textContent.trim(); if (t) out.push({ t: "p", s: t.slice(0, 8000) }); }
    return out.slice(0, 400);
  }
  const blocksHtml = (bl) => (bl || []).map((b) => b.t === "h" ? `<h3>${esc(b.s)}</h3>` : b.t === "li" ? `<ul><li>${esc(b.s)}</li></ul>`
    : b.t === "q" ? `<blockquote>${esc(b.s)}</blockquote>` : `<p>${esc(b.s)}</p>`).join("");

  async function deskOf(withData, symbols) {
    if (typeof Dock === "undefined" || !Dock.exportState) return null;
    const state = Dock.exportState();
    const out = { v: 1, state };
    if (!withData) return out;
    const ids = Object.entries(state.inst);
    const noteSuffix = new Set(ids.filter(([, i]) => i.type === "notes").map(([id]) => (id === "notes" ? "" : id.split(":")[1])));
    const scopes = new Set(["general", ...symbols]);
    out.notes = {};
    try {
      for (const key of Object.keys(localStorage)) {
        const m = /^charto:(note:([^:]+)(?::(.+))?)$/.exec(key);
        if (!m || !scopes.has(m[2]) || !noteSuffix.has(m[3] || "")) continue;
        const v = JSON.parse(localStorage.getItem(key) || "null");
        const bl = v && noteBlocks(v.html);
        if (bl && bl.length) out.notes[m[1]] = bl;
      }
    } catch { /* notes are a courtesy; the workspace still travels */ }
    out.sheets = {};
    for (const [id, i] of ids) {
      if (i.type !== "sheet") continue;
      try {
        const v = await WKit.idb.get(`sheet:${id}`);
        if (v && JSON.stringify(v).length < 400_000) out.sheets[id] = v;
      } catch { /* skip it */ }
    }
    if (JSON.stringify(out).length > 1_100_000) delete out.sheets;
    return out;
  }

  let sharedSheets = {};
  /** A sheet a shared workspace brought, for w-sheet.js in a view session. */
  const sheet = (key) => sharedSheets[key.replace(/^sheet:/, "")] || null;

  /** Put a shared workspace's widgets and contents on screen. In a view
   *  session everything stays in memory (store.js); on a copy the notes join
   *  the reader's own (never replacing them) and a sheet lands only where
   *  the reader has none under that id. */
  async function applyDesk(desk, view) {
    if (!desk || !desk.state || typeof Dock === "undefined" || !Dock.applyState) return;
    for (const [key, bl] of Object.entries(desk.notes || {})) {
      if (!/^note:[^:]+(:[\w-]+)?$/.test(key)) continue;
      const html = blocksHtml(bl);
      const had = Store.get(key, null);
      if (view || !had || !had.html) Store.set(key, { html, at: Date.now() });
      else if (!had.html.includes(html)) Store.set(key, { html: `${had.html}<p><b>From a shared setup</b></p>${html}`, at: Date.now() });
    }
    sharedSheets = view ? (desk.sheets || {}) : {};
    if (!view) {
      for (const [id, v] of Object.entries(desk.sheets || {})) {
        try { if (!(await WKit.idb.get(`sheet:${id}`))) await WKit.idb.set(`sheet:${id}`, v); } catch {}
      }
    }
    Dock.applyState(desk.state);
  }

  function ago(ts) {
    if (!ts) return "";
    const s = Date.now() / 1000 - ts;
    if (s < 90) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    const d = Math.round(s / 86400);
    if (d < 30) return `${d} day${d === 1 ? "" : "s"} ago`;
    return new Date(ts * 1000).toLocaleDateString([], { day: "2-digit", month: "short", year: "numeric" });
  }

  function linkFor(token, symbol) {
    const u = new URL(location.origin + location.pathname);
    u.searchParams.set("symbol", symbol);
    u.searchParams.set("view", token);
    return u.toString();
  }

  async function copyText(text, done) {
    try { await navigator.clipboard.writeText(text); toast(done); }
    catch { toast("Could not reach the clipboard. Select the link and copy it"); }
  }

  /* ══ dialogs ══════════════════════════════════════════════════════════ */

  function dialog(html) {
    const back = document.createElement("div");
    back.className = "ly-back su-back";
    back.innerHTML = `<div class="ly-dlg su-dlg" role="dialog" aria-modal="true">${html}</div>`;
    document.body.appendChild(back);
    const onKey = (e) => { if (e.key === "Escape") close(); };
    const close = () => { back.remove(); document.removeEventListener("keydown", onKey); };
    back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
    document.addEventListener("keydown", onKey);
    return { back, dlg: back.firstElementChild, close };
  }

  const sw = (name, on, label, sub) =>
    `<label class="su-switch" data-sw="${name}">`
    + `<span class="su-switch-text"><span>${esc(label)}</span>`
    + (sub ? `<small>${esc(sub)}</small>` : "") + `</span>`
    + `<span class="switch${on ? " on" : ""}" role="switch" aria-checked="${on}"><i></i></span></label>`;

  /* ── Share ────────────────────────────────────────────────────────────── */

  async function publish() {
    if (Store.viewOnly) return toast("Make it mine first, then share your own version");
    if (!Layouts.signedIn()) {
      toast("Sign in to share setups");
      if (window.CHARTO_AUTH_OPEN) window.CHARTO_AUTH_OPEN();
      return;
    }
    const spec = Layouts.snapshot();
    if (!spec) return toast("The chart is still loading");
    const c = window.__charto;
    const cur = Layouts.current;
    const chat = (window.Chat && Chat.transcript) ? Chat.transcript() : [];
    const thumb = Layouts.thumbnail();
    let existing = null;
    if (cur && cur.id) {
      try { existing = ((await Layouts.call(`/setups?layout=${cur.id}`)).setups || [])[0] || null; }
      catch { existing = null; }
    }
    const sym = c.symbol;
    const iv = String(c.interval || "").toUpperCase();
    const named = cur && cur.name && cur.name !== "Unnamed" ? cur.name : "";
    const state = {
      include_chat: existing ? existing.has_chat : chat.length > 0,
      include_data: true,
      allow_copy: existing ? existing.allow_copy : true,
      mode: existing ? "update" : "new",
    };
    const live = typeof Dock !== "undefined" && Dock.exportState ? Dock.exportState() : null;
    const preview = { ...spec, dock: live ? { state: live } : undefined };
    const sp = spark(onScreen(c));
    const firstAsk = (chat.find((t) => t.role === "user") || {}).content || "";
    const row = (name, icon, on, label, sub, off) =>
      `<label class="sx-opt${off ? " off" : ""}" data-sw="${name}"><span class="sx-opt-ic">${Icons.svg(icon, "xs")}</span>` +
      `<span class="sx-opt-t"><span>${esc(label)}</span>${sub ? `<small>${esc(sub)}</small>` : ""}</span>` +
      `<span class="switch${on ? " on" : ""}" role="switch" aria-checked="${on}"><i></i></span></label>`;

    const { dlg, close } = dialog(`
      <div class="sx">
        <aside class="sx-art">
          <div class="sx-kick"><span class="sx-dot"></span>Your workspace, as it is now</div>
          ${deskMini(shapeOf(live), thumb, sym)}
          ${idStrip(sym, iv, sp)}
          <div class="sx-chips" id="sxChips"></div>
        </aside>
        <section class="sx-form">
          <h3>Share this workspace</h3>
          <p class="sx-lede">A read-only copy of your screen: the chart, every widget and its settings.
             Anyone with the link opens it live, and can copy it to build on.</p>
          <label class="ly-lab" for="suTitle">Title</label>
          <input class="textfield" id="suTitle" maxlength="120" autocomplete="off"
                 value="${esc(existing ? existing.title : named || `${sym} ${iv} workspace`)}">
          <label class="ly-lab su-gap" for="suNote">Your read <span class="su-count" id="suCount"></span></label>
          <textarea class="textfield su-note" id="suNote" maxlength="2000" rows="4"
            placeholder="What are you seeing? The levels that matter, what would confirm it, what would prove it wrong.">${esc(existing ? existing.note : "")}</textarea>
          <div class="sx-opts">
            ${row("include_chat", "chat", state.include_chat, `The conversation${chat.length ? ` · ${chat.length} turns` : ""}`,
                  chat.length ? `Starts with “${firstAsk.slice(0, 56)}${firstAsk.length > 56 ? "…" : ""}”` : "There is no conversation on this desk yet.", !chat.length)}
            ${row("include_data", "note", state.include_data, "What the notes and sheets hold",
                  "Their text and cells. Documents' files stay with you.")}
            ${row("allow_copy", "copy", state.allow_copy, "Let others copy it", "Copies are theirs to edit. Yours never changes.")}
          </div>
          ${existing ? `<div class="su-seg" role="radiogroup" aria-label="Link">
              <button type="button" data-mode="update" class="${state.mode === "update" ? "on" : ""}">Update the existing link</button>
              <button type="button" data-mode="new" class="${state.mode === "new" ? "on" : ""}">Publish a new link</button>
            </div>` : ""}
        </section>
      </div>
      <div class="sx-foot">
        <span class="su-privacy">${Icons.svg("lock", "xs")} Unlisted. Only people with the link can open it; your email is never shown.</span>
        <div class="ly-actions">
          <button class="btn" data-x>Cancel</button>
          <button class="btn cta" data-ok>${Icons.svg("link", "xs")}<span>${existing ? "Update link" : "Publish"}</span></button>
        </div>
      </div>`);
    dlg.classList.add("sx-dlg");

    const paintManifest = () => {
      dlg.querySelector("#sxChips").innerHTML =
        manifest(preview, state.include_chat && chat.length ? chat.length : 0, state.include_data);
    };
    paintManifest();
    const note = dlg.querySelector("#suNote"), count = dlg.querySelector("#suCount");
    const recount = () => { count.textContent = note.value.length ? `${note.value.length}/2000` : ""; };
    note.addEventListener("input", recount); recount();
    dlg.querySelector("#suTitle").select();

    dlg.addEventListener("click", (e) => {
      const t = e.target.closest("[data-sw]");
      if (t) {
        e.preventDefault();
        const k = t.dataset.sw;
        if (k === "include_chat" && !chat.length) return;
        state[k] = !state[k];
        const s2 = t.querySelector(".switch");
        s2.classList.toggle("on", state[k]);
        s2.setAttribute("aria-checked", String(state[k]));
        paintManifest();
        return;
      }
      const m = e.target.closest("[data-mode]");
      if (m) {
        state.mode = m.dataset.mode;
        dlg.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("on", b === m));
        dlg.querySelector("[data-ok] span").textContent = state.mode === "update" ? "Update link" : "Publish";
      }
    });
    dlg.querySelector("[data-x]").onclick = close;
    const ok = dlg.querySelector("[data-ok]");
    ok.onclick = async () => {
      const title = dlg.querySelector("#suTitle").value.trim();
      if (!title) { dlg.querySelector("#suTitle").focus(); return toast("Give the setup a title"); }
      ok.disabled = true;
      ok.querySelector("span").textContent = "Publishing…";
      try {
        const desk = await deskOf(state.include_data, [...new Set((spec.charts || []).map((x) => x.symbol))]);
        const d = await Layouts.call("/setups", {
          ...(state.mode === "update" && existing ? { token: existing.token } : {}),
          layout_id: cur && cur.id ? cur.id : undefined,
          title, note: note.value, spec: desk ? { ...spec, dock: desk } : spec, thumb,
          include_chat: !!(state.include_chat && chat.length), chat,
          allow_copy: state.allow_copy,
        });
        published(dlg, close, d.token, sym, title, !d.created, deskMini(shapeOf(live), thumb, sym));
      } catch (err) {
        ok.disabled = false;
        ok.querySelector("span").textContent = existing ? "Update link" : "Publish";
        toast(err.message);
      }
    };
  }

  // Share targets are plain links the reader's own apps open; nothing is
  // sent from here. Each carries its real brand mark (a filled 24-unit glyph)
  // and its brand colour, shown on the chip on hover.
  const TARGETS = [
    ["WhatsApp", (u, t) => `https://wa.me/?text=${encodeURIComponent(`${t} ${u}`)}`, "#25D366",
      '<path d="M12.04 2C6.58 2 2.13 6.45 2.13 11.91c0 1.75.46 3.45 1.32 4.95L2 22l5.25-1.38a9.9 9.9 0 0 0 4.79 1.22h.01c5.46 0 9.91-4.45 9.91-9.91C21.96 6.45 17.5 2 12.04 2Zm5.8 14.17c-.25.69-1.45 1.32-1.99 1.37-.53.05-1.03.24-3.47-.72-2.93-1.15-4.8-4.14-4.95-4.33-.14-.2-1.18-1.57-1.18-2.99 0-1.42.75-2.12 1.01-2.41.25-.3.55-.37.74-.37l.53.01c.17 0 .4-.06.62.47.25.6.86 2.07.94 2.22.07.15.12.32.02.52-.1.2-.15.32-.3.5-.15.17-.31.39-.45.52-.15.15-.3.31-.13.6.17.3.76 1.25 1.63 2.03 1.12 1 2.06 1.3 2.36 1.45.3.15.47.12.64-.07.17-.2.74-.86.94-1.16.2-.3.4-.25.67-.15.27.1 1.71.81 2 .96.3.15.5.22.57.35.07.12.07.72-.18 1.41Z"/>'],
    ["X", (u, t) => `https://x.com/intent/post?text=${encodeURIComponent(t)}&url=${encodeURIComponent(u)}`, "#000000",
      '<path d="M18.9 2h3.3l-7.2 8.26L23.5 22h-6.6l-5.18-6.78L5.8 22H2.5l7.73-8.84L2 2h6.77l4.68 6.19L18.9 2Zm-1.16 18h1.83L7.34 3.9H5.38L17.74 20Z"/>'],
    ["Telegram", (u, t) => `https://t.me/share/url?url=${encodeURIComponent(u)}&text=${encodeURIComponent(t)}`, "#229ED9",
      '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm4.64 6.8-1.56 7.36c-.12.52-.42.65-.86.4l-2.37-1.75-1.14 1.1c-.13.13-.24.24-.48.24l.17-2.43 4.42-3.99c.19-.17-.04-.27-.3-.1l-5.46 3.44-2.35-.73c-.51-.16-.52-.51.11-.76l9.18-3.54c.42-.15.8.1.66.76Z"/>'],
    ["LinkedIn", (u) => `https://www.linkedin.com/sharing/share-offsite/?url=${encodeURIComponent(u)}`, "#0A66C2",
      '<path d="M20.45 20.45h-3.56v-5.57c0-1.33-.02-3.04-1.85-3.04-1.85 0-2.14 1.45-2.14 2.94v5.67H9.35V9h3.41v1.56h.05c.48-.9 1.64-1.85 3.37-1.85 3.6 0 4.27 2.37 4.27 5.46v6.28ZM5.34 7.43a2.07 2.07 0 1 1 0-4.14 2.07 2.07 0 0 1 0 4.14Zm1.78 13.02H3.56V9h3.56v11.45ZM22.22 0H1.77C.8 0 0 .78 0 1.75v20.5C0 23.22.8 24 1.77 24h20.45c.98 0 1.78-.78 1.78-1.75V1.75C24 .78 23.2 0 22.22 0Z"/>'],
    ["Email", (u, t) => `mailto:?subject=${encodeURIComponent(t)}&body=${encodeURIComponent(u)}`, "",
      '<path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2Zm16 2H4l8 5 8-5Zm0 2.25-7.47 4.67a1 1 0 0 1-1.06 0L4 8.25V18h16V8.25Z"/>'],
  ];
  const mark = (path) => `<svg class="icon icon-xs" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">${path}</svg>`;

  function published(dlg, close, token, sym, title, updated, mini) {
    const url = linkFor(token, sym);
    dlg.classList.add("sx-done");
    dlg.innerHTML = `
      <div class="sx-ok">
        <div class="sx-ok-art">${mini}
          <svg class="sx-check" viewBox="0 0 52 52" aria-hidden="true"><circle cx="26" cy="26" r="23"/><path d="m15 27 7.5 7.5L37.5 19"/></svg>
        </div>
        <h3>${updated ? "The link shows this version now" : "Your workspace is live"}</h3>
        <p class="sx-lede">${updated
          ? "Anyone opening the link sees what is on your screen now. Copies people already took are unchanged."
          : `“${esc(title)}” opens read-only on a live chart, with every widget in place.`}</p>
        <div class="sx-link">${Icons.svg("lock", "xs")}<input readonly value="${esc(url)}" aria-label="Share link">
          <button class="btn cta" data-copy>${Icons.svg("copy", "xs")}<span>Copy</span></button></div>
        <div class="sx-targets">
          ${TARGETS.map(([n, , brand, path], i) => `<button type="button" class="sx-target" data-t="${i}" title="Share on ${n}"${brand ? ` style="--brand:${brand}"` : ""}>${mark(path)}<span>${n}</span></button>`).join("")}
          ${navigator.share ? `<button type="button" class="sx-target" data-t="more" title="More ways to share">${Icons.svg("more", "xs")}<span>More</span></button>` : ""}
        </div>
      </div>
      <div class="ly-actions">
        <button class="btn" data-manage>${Icons.svg("eye", "xs")}<span>My shared setups</span></button>
        <a class="btn" href="${esc(url)}" target="_blank" rel="noopener">${Icons.svg("externalLink", "xs")}<span>Open as a viewer</span></a>
        <button class="btn" data-x>Done</button>
      </div>`;
    const input = dlg.querySelector(".sx-link input");
    input.addEventListener("focus", () => input.select());
    dlg.querySelector("[data-copy]").onclick = () => copyText(url, "Link copied");
    dlg.querySelector("[data-x]").onclick = close;
    dlg.querySelector("[data-manage]").onclick = () => { close(); manage(); };
    dlg.querySelector(".sx-targets").addEventListener("click", (e) => {
      const b = e.target.closest("[data-t]");
      if (!b) return;
      const text = `${title} · ${sym} on Pivot`;
      if (b.dataset.t === "more") { navigator.share({ title: text, url }).catch(() => {}); return; }
      const [, make] = TARGETS[+b.dataset.t];
      open(make(url, text), "_blank", "noopener,noreferrer");
    });
    copyText(url, "Published. The link is on your clipboard");
  }

  /* ── the author's list ────────────────────────────────────────────────── */

  async function manage() {
    if (!Layouts.signedIn()) {
      toast("Sign in to see your shared setups");
      if (window.CHARTO_AUTH_OPEN) window.CHARTO_AUTH_OPEN();
      return;
    }
    const { dlg, close } = dialog(`
      <div class="su-head slim">
        <h3>My shared setups</h3>
        <span class="su-sub" id="suTotals"></span>
      </div>
      <div class="su-list" id="suList"><div class="su-loading">Loading…</div></div>
      <div class="ly-actions"><button class="btn" data-x>Close</button></div>`);
    dlg.classList.add("su-manage");
    dlg.querySelector("[data-x]").onclick = close;
    let list = [];
    const render = () => {
      const box = dlg.querySelector("#suList");
      const views = list.reduce((a, s) => a + s.views, 0);
      const copies = list.reduce((a, s) => a + s.copies, 0);
      dlg.querySelector("#suTotals").textContent = list.length
        ? `${list.length} live · ${views} views · ${copies} copies` : "";
      if (!list.length) {
        box.innerHTML = `<div class="sx-empty">${Icons.svg("link", "sm")}<b>Nothing shared yet</b>` +
          `<span>Share, in the top bar, publishes the workspace you are on as a read-only link.</span></div>`;
        return;
      }
      box.innerHTML = list.map((s) => `
        <div class="su-row" data-token="${esc(s.token)}" data-symbol="${esc(s.symbol)}">
          <div class="su-row-main">
            <div class="su-row-title">${esc(s.title)}
              ${s.has_chat ? '<span class="ly-tag alt">Chat</span>' : ""}
              ${s.allow_copy ? "" : '<span class="ly-tag alt">View only</span>'}</div>
            <div class="su-row-sub"><span class="su-mono">${esc(s.symbols.join(" · "))}${s.interval ? " " + esc(s.interval.toUpperCase()) : ""}</span>
              <span class="ly-dotsep">·</span> updated ${esc(ago(s.updated))}</div>
          </div>
          <div class="su-stats su-mono" title="${s.views} views · ${s.copies} copies">
            <span>${Icons.svg("eye", "xs")}${s.views}</span>
            <span>${Icons.svg("copy", "xs")}${s.copies}</span>
          </div>
          <div class="su-row-acts">
            <button class="btn icon" data-act="copy" title="Copy link">${Icons.svg("link", "xs")}</button>
            <a class="btn icon" data-act="open" title="Open as a viewer" target="_blank" rel="noopener"
               href="${esc(linkFor(s.token, s.symbol))}">${Icons.svg("externalLink", "xs")}</a>
            <button class="btn icon su-danger" data-act="unpublish" title="Unpublish">${Icons.svg("trash", "xs")}</button>
          </div>
        </div>`).join("");
    };
    try { list = (await Layouts.call("/setups")).setups || []; }
    catch (e) { list = []; toast(e.message); }
    render();
    dlg.addEventListener("click", async (e) => {
      const b = e.target.closest("[data-act]");
      if (!b) return;
      const row = b.closest(".su-row");
      const tok = row.dataset.token;
      if (b.dataset.act === "copy") return copyText(linkFor(tok, row.dataset.symbol), "Link copied");
      if (b.dataset.act === "unpublish") {
        if (row.classList.contains("confirm")) {
          try {
            await Layouts.call("/setups", { token: tok, delete: true });
            list = list.filter((s) => s.token !== tok);
            render();
            toast("Unpublished. The link no longer opens; copies people took are theirs");
          } catch (err) { toast(err.message); }
          return;
        }
        row.classList.add("confirm");
        b.title = "Click again to unpublish";
        toast("Click the bin again to unpublish this link");
        setTimeout(() => row.classList.remove("confirm"), 3500);
      }
    });
  }

  /* ══ view mode ════════════════════════════════════════════════════════ */

  function exitUrl(sym) {
    const u = new URL(location.href);
    u.searchParams.delete("view");
    u.searchParams.delete("shared");
    if (sym) u.searchParams.set("symbol", sym);
    return u.toString();
  }

  async function fetchSetup(token) {
    const r = await fetch(`${API}/shared?token=${encodeURIComponent(token)}`,
      { headers: (typeof Auth !== "undefined" && Auth.token) ? Auth.headers({}) : {} });
    let d = {};
    try { d = await r.json(); } catch { /* an HTML fall-through is a miss too */ }
    if (!r.ok || !d.spec) return { error: d.error || "This setup is no longer shared", status: r.status };
    // An older live layout link (`?shared=`) answers in the layout's shape;
    // it is shown the same way, without a conversation or a copy button.
    if (!d.token) {
      return { token, title: d.name || "Shared layout", note: "", by: d.by || "A Pivot trader",
               symbol: (d.symbols || [])[0] || ((d.spec.charts || [])[0] || {}).symbol,
               symbols: d.symbols || [], spec: d.spec, chat: null, allow_copy: false,
               lineage: [], updated: d.updated, created: d.updated, views: null,
               copies: null, legacy: true };
    }
    return d;
  }

  function mountGone(msg) {
    document.body.classList.add("view-only", "su-gone");
    const bar = document.createElement("div");
    bar.className = "su-bar gone";
    bar.innerHTML = `<div class="sx-gone">${Icons.svg("link", "sm")}<b>This link is closed</b><span>${esc(msg)}</span></div>
      <a class="btn cta" href="${esc(exitUrl())}">Open my chart</a>`;
    (el("stage") || document.body).appendChild(bar);
  }

  function mountBar(d) {
    const bar = document.createElement("div");
    bar.className = "su-bar";
    bar.id = "suBar";
    const canCopy = d.allow_copy || d.own;
    bar.innerHTML = `
      <span class="su-badge">${Icons.svg("eye", "xs")}View only</span>
      <span class="su-bar-sep"></span>
      <span class="su-bar-title" title="${esc(d.title)}">${esc(d.title)}</span>
      <span class="su-bar-by">by ${esc(d.by)}</span>
      <span class="su-bar-sep"></span>
      <button class="btn" data-act="link" title="Copy this link">${Icons.svg("link", "xs")}<span>Copy link</span></button>
      <button class="btn cta" data-act="mine" ${canCopy ? "" : "disabled"}
        title="${canCopy ? "Copy this setup into your own layouts and keep going" : "The author has made this setup view-only"}">
        ${Icons.svg("copy", "xs")}<span>${canCopy ? "Make it mine" : "View only"}</span></button>`;
    // In the HEADER, in the room the editing controls leave when they stand
    // down (see body.view-only in the stylesheet). Floated over the candles
    // it sat on the OHLC readout — the one line a reader most needs.
    const header = document.querySelector("body > header");
    const spacer = header && header.querySelector(".spacer");
    if (spacer) {
      bar.classList.add("in-header");
      const after = document.createElement("div");
      after.className = "spacer";
      spacer.after(bar, after);
    } else {
      (el("stage") || document.body).appendChild(bar);
    }
    bar.addEventListener("click", (e) => {
      const b = e.target.closest("[data-act]");
      if (!b) return;
      if (b.dataset.act === "link") copyText(location.href, "Link copied");
      if (b.dataset.act === "mine") makeItMine();
    });
  }

  async function mountCard(d) {
    const thread = el("thread");
    if (!thread) return;
    const c = window.__charto;
    const s = spark(onScreen(c));
    const card = document.createElement("section");
    card.className = "su-card";
    card.id = "suCard";
    const stats = d.legacy ? "" : `
      <div class="su-card-stats su-mono">
        <span title="Views">${Icons.svg("eye", "xs")}${d.views}</span>
        <span title="Copies">${Icons.svg("copy", "xs")}${d.copies}</span>
        <span title="Published">${Icons.svg("clock", "xs")}${esc(ago(d.updated))}</span>
      </div>`;
    const st = d.spec && d.spec.dock && d.spec.dock.state;
    const iv = String(((d.spec && d.spec.charts) || [{}])[0].interval || "").toUpperCase();
    card.innerHTML = `
      ${st ? deskMini(shapeOf(st), d.thumb || "", d.symbol) : ""}
      ${idStrip(d.symbol, iv, s)}
      <h2 class="su-card-title">${esc(d.title)}</h2>
      <div class="su-card-by"><span class="su-avatar">${esc((d.by || "?").trim().charAt(0).toUpperCase())}</span>
        <span>${esc(d.by)}</span></div>
      ${stats}
      ${d.note ? `<div class="su-card-note">${esc(d.note)}</div>` : ""}
      <div class="sx-chips">${manifest(d.spec, d.chat ? d.chat.length : 0, !!(d.spec && d.spec.dock && (d.spec.dock.notes || d.spec.dock.sheets)))}</div>
      ${lineage(d.lineage)}
      ${d.chat && d.chat.length ? `<div class="su-card-rule"><span>the conversation</span></div>` : ""}`;
    thread.insertBefore(card, thread.firstChild);
    // The thread opens at its newest turn, and chat.js keeps pulling it back
    // there as turns lay out. A reader arriving from a link should meet the
    // setup first and scroll down into how it was made — so hold the top
    // while the thread settles, and let go the moment they touch it.
    let touched = false;
    const let_go = () => { touched = true; };
    for (const ev of ["wheel", "touchstart", "keydown", "mousedown"]) {
      thread.addEventListener(ev, let_go, { once: true, passive: true });
    }
    const t0 = performance.now();
    (function hold() {
      if (touched) return;
      thread.scrollTop = 0;
      if (performance.now() - t0 < 2500) requestAnimationFrame(hold);
    })();

    const wrap = document.querySelector(".composer-wrap");
    if (wrap && !el("suReadonly")) {
      const note = document.createElement("div");
      note.className = "su-readonly";
      note.id = "suReadonly";
      const canCopy = d.allow_copy || d.own;
      note.innerHTML = `<span class="su-ro-mark">${Icons.svg("lock", "xs")}</span>
        <span class="su-ro-text">${canCopy
          ? "This conversation is read-only. Make it mine to continue it on your own copy."
          : "This setup is view-only. The author has not allowed copies."}</span>
        ${canCopy ? `<button class="btn cta" data-mine>${Icons.svg("copy", "xs")}<span>Make it mine</span></button>` : ""}`;
      wrap.appendChild(note);
      const m = note.querySelector("[data-mine]");
      if (m) m.onclick = makeItMine;
    }
  }

  /** The reader's OWN working desk on this symbol, kept as a layout before
   *  the copy opens over it. Opening any layout replaces the working desk;
   *  a reader who clicked "Make it mine" on someone else's TCS setup did not
   *  ask to lose the lines they had on their own TCS chart. Read straight
   *  from localStorage: in a view session the store is memory (store.js). */
  async function keepOwnDesk(sym) {
    const rd = (k) => { try { return JSON.parse(localStorage.getItem(k) || "null"); } catch { return null; } };
    const scoped = (key) => (sym === "RELIANCE" ? `charto:${key}` : `charto:${sym}:${key}`);
    const drawings = rd(`charto_drawings_v2_${sym}`) || [];
    const scene = rd(scoped("scene")) || [];
    if (!drawings.length && !scene.length) return null;
    try {
      const d = await Layouts.call("/layouts", {
        name: `${sym} desk (before copy)`, symbols: [sym],
        spec: { v: 1, grid: "1",
                charts: [{ symbol: sym, interval: rd("charto:interval") || "5m" }],
                workspace: { drawings, scene, indicators: rd("charto:indicators") || [],
                             vp: rd(scoped("vp")) } },
      });
      return d.name;
    } catch { return null; }
  }

  async function makeItMine() {
    const d = viewing;
    if (!d || d.legacy) return toast("This older link can be viewed but not copied");
    if (!d.allow_copy && !d.own) return toast("The author has made this setup view-only");
    if (typeof Auth === "undefined" || !Auth.token) {
      // Signing in reloads the page; the intent survives it and finishes on
      // the other side (see enterView).
      try { sessionStorage.setItem("charto:pending-copy", d.token); } catch {}
      toast("Sign in to copy this setup into your layouts");
      if (window.CHARTO_AUTH_OPEN) window.CHARTO_AUTH_OPEN("signup");
      const desc = el("authDesc");
      if (desc) {
        desc.textContent = `Create a free account to copy “${d.title}” into your `
          + "own layouts, with its drawings and conversation, and keep working on it.";
      }
      return;
    }
    const btns = document.querySelectorAll('[data-act="mine"], [data-mine]');
    btns.forEach((b) => { b.disabled = true; const t = b.querySelector("span"); if (t) t.textContent = "Copying…"; });
    try {
      const kept = await keepOwnDesk(d.symbol);
      if (kept) { try { sessionStorage.setItem("charto:kept-desk", kept); } catch {} }
      const r = await Layouts.call("/setups/copy", { token: d.token });
      if (r.chat && r.chat.length && r.chat_id) {
        try {
          sessionStorage.setItem("charto:adopt-chat",
            JSON.stringify({ id: r.chat_id, turns: r.chat, title: d.title }));
        } catch { /* the layout still copies; only its thread stays behind */ }
      }
      const u = new URL(exitUrl(r.symbol || d.symbol));
      u.searchParams.set("layout", String(r.layout_id));
      location.href = u.toString();
    } catch (err) {
      btns.forEach((b) => { b.disabled = false; const t = b.querySelector("span"); if (t) t.textContent = "Make it mine"; });
      toast(err.message);
    }
  }

  async function enterView() {
    const c = window.__charto;
    const d = await fetchSetup(VIEW);
    if (d.error) {
      mountGone(d.status === 404 ? "The author has unpublished it, or the link is mistyped."
                                 : d.error);
      return;
    }
    // The chart's symbol is fixed at boot from ?symbol= (see layouts.js), so
    // a link that names the wrong one — or none — is re-pointed first.
    if (d.symbol && d.symbol !== c.symbol) {
      const u = new URL(location.href);
      u.searchParams.set("symbol", d.symbol);
      location.replace(u.toString());
      return;
    }
    viewing = d;
    document.body.classList.add("view-only");
    document.title = `${d.title} · ${d.symbol} — Pivot`;
    // Locked, so a reader's click selects a shape and reads it but cannot
    // drag the author's level somewhere it was never drawn.
    const spec = JSON.parse(JSON.stringify(d.spec));
    const ws = spec.workspace || (spec.workspace = {});
    ws.drawings = (ws.drawings || []).map((x) => ({ ...x, locked: true }));
    try { await Layouts.restore(spec); }
    catch (e) { console.error("[charto] setup restore", e); }
    mountBar(d);
    if (window.Chat && Chat.showShared) Chat.showShared(d.chat || []);
    await mountCard(d);
    let pending = null;
    try { pending = sessionStorage.getItem("charto:pending-copy"); } catch {}
    if (pending && pending === d.token && Auth.token) {
      try { sessionStorage.removeItem("charto:pending-copy"); } catch {}
      makeItMine();
    }
  }

  /* ── boot ─────────────────────────────────────────────────────────────── */

  const shareBtn = el("shareBtn");
  if (shareBtn) {
    shareBtn.innerHTML = `${Icons.svg("link", "xs")}<span>Share</span>`;
    shareBtn.addEventListener("click", () => publish());
  }

  // An older `?shared=<token>` link is turned into a view session before
  // anything is drawn from it: without ?view= the store is the reader's real
  // one, and restoring someone else's desk into it would overwrite theirs.
  const legacy = new URLSearchParams(location.search).get("shared");
  if (legacy && !VIEW) {
    const u = new URL(location.href);
    u.searchParams.delete("shared");
    u.searchParams.set("view", legacy);
    location.replace(u.toString());
  } else if (VIEW) {
    document.body.classList.add("view-only");
    document.addEventListener("charto:workspace-ready", () => { enterView(); }, { once: true });
  } else {
    // Arrived from "Make it mine" with drawings of your own on this symbol:
    // say where they went, after the copy's own "Opened …" has been read.
    let kept = null;
    try { kept = sessionStorage.getItem("charto:kept-desk"); } catch {}
    if (kept) {
      try { sessionStorage.removeItem("charto:kept-desk"); } catch {}
      document.addEventListener("charto:workspace-ready", () => {
        setTimeout(() => toast(`Your earlier drawings here were saved as “${kept}”`), 3200);
      }, { once: true });
    }
  }

  return { publish, manage, makeItMine, get viewing() { return viewing; },
           // exposed for tests and for the eval harness
           applyDesk, sheet, _kit: { spark, manifest, shapeOf, deskMini } };
})();

window.Setups = Setups;
