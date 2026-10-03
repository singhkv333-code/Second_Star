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
 * ── the ASCII ─────────────────────────────────────────────────────────────
 * Headers are set with figlet.js (MIT, vendor/figlet/) in "Calvin S", a
 * box-drawing face, patched with matching digits so NIFTY 50 sets as well as
 * TCS. Frames, the sparkline and the credit tree are drawn from the same
 * box-drawing and block characters, in JetBrains Mono, so the whole card
 * reads as one typeset object rather than decoration pasted on a panel.
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

  /* ══ the ASCII kit ════════════════════════════════════════════════════ */

  let figP = null;
  function figlet() {
    if (!figP) {
      figP = (async () => {
        const mod = await import(new URL("../vendor/figlet/figlet.js", BASE).href);
        const fig = mod.default;
        const font = await (await fetch(new URL("../vendor/figlet/calvin-pivot.flf", BASE))).text();
        fig.parseFont("CalvinPivot", font);
        return fig;
      })().catch((e) => { console.warn("[charto] figlet unavailable", e); return null; });
    }
    return figP;
  }

  /** The ticker set in box-drawing type. Falls back to the plain word, so a
   *  missing vendor file costs a flourish and never the header. */
  async function banner(text) {
    const f = await figlet();
    const t = String(text || "").toUpperCase().slice(0, 14);
    if (!f) return t;
    try { return f.textSync(t, { font: "CalvinPivot" }).replace(/\s+$/gm, ""); }
    catch { return t; }
  }

  const len = (s) => Array.from(s).length;
  const pad = (s, n) => s + " ".repeat(Math.max(0, n - len(s)));

  /** A titled box: ┌─ title ──┐ │ rows │ └────┘. Mono, so widths are exact. */
  function frame(rows, title, width) {
    const inner = Math.max(width || 0, len(title || "") + 4, ...rows.map((r) => len(r) + 2));
    const top = title ? `┌─ ${title} ${"─".repeat(inner - len(title) - 3)}┐`
                      : `┌${"─".repeat(inner)}┐`;
    return [top, ...rows.map((r) => `│ ${pad(r, inner - 2)} │`),
            `└${"─".repeat(inner)}┘`].join("\n");
  }

  const BLOCKS = "▁▂▃▄▅▆▇█";
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
  /** Closes as block characters, bucketed to `width` cells. */
  function spark(bars, width = 34) {
    const closes = (bars || []).map((b) => b.close).filter((x) => Number.isFinite(x));
    if (closes.length < 2) return null;
    const step = Math.max(1, closes.length / width);
    const pts = [];
    for (let i = 0; i < width && Math.floor(i * step) < closes.length; i++) {
      pts.push(closes[Math.min(closes.length - 1, Math.floor((i + 1) * step) - 1)]);
    }
    const lo = Math.min(...pts), hi = Math.max(...pts), span = hi - lo || 1;
    const line = pts.map((p) => BLOCKS[Math.round(((p - lo) / span) * 7)]).join("");
    const chg = (closes[closes.length - 1] / closes[0] - 1) * 100;
    return { line, chg, first: closes[0], last: closes[closes.length - 1] };
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

  /** What a desk carries, as rows for a frame. The same words in the Share
   *  dialog and on the setup card, so the author sees what the reader gets.
   *  Plain ASCII inside the box: its right edge is only straight if every
   *  glyph is exactly one cell wide, and a separator like a middle dot or a
   *  guillemet, drawn from a fallback face, is not. */
  function manifest(spec, chatTurns) {
    const ws = (spec && spec.workspace) || {};
    const charts = (spec && spec.charts) || [];
    const syms = [...new Set(charts.map((c) => c.symbol).filter(Boolean))];
    const iv = charts[0] && charts[0].interval ? String(charts[0].interval).toUpperCase() : "";
    const inds = (ws.indicators || []).map(indicatorName);
    const rows = [
      `> ${charts.length} chart${charts.length === 1 ? " " : "s"}   ${syms.join(" / ")}${iv ? "  " + iv : ""}`,
      `> ${(ws.drawings || []).length} drawing${(ws.drawings || []).length === 1 ? "" : "s"}`,
      `> ${(ws.scene || []).length} annotation${(ws.scene || []).length === 1 ? "" : "s"} from the chat`,
      `> ${inds.length} indicator${inds.length === 1 ? "" : "s"}${inds.length ? "   " + inds.slice(0, 4).join(" / ") + (inds.length > 4 ? ` +${inds.length - 4}` : "") : ""}`,
    ];
    if (ws.vp) rows.push("> volume profile");
    rows.push(chatTurns === null ? "> conversation   not shared"
      : `> conversation   ${chatTurns} turn${chatTurns === 1 ? "" : "s"}`);
    return rows;
  }

  /** The credit chain as a tree, nearest first. */
  function lineageTree(chain) {
    if (!chain || !chain.length) return "";
    const lines = ["built on"];
    chain.slice(0, 5).forEach((c, i) => {
      lines.push(`${"   ".repeat(i)}└─ "${c.title}" by ${c.by}`);
    });
    return lines.join("\n");
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
      allow_copy: existing ? existing.allow_copy : true,
      mode: existing ? "update" : "new",
    };
    const s = spark(onScreen(c));
    const firstAsk = (chat.find((t) => t.role === "user") || {}).content || "";

    const { dlg, close } = dialog(`
      <div class="su-head">
        <pre class="su-fig" aria-hidden="true">${esc(sym)}</pre>
        <div class="su-head-copy">
          <h3>Share this setup</h3>
          <p>A read-only snapshot of this desk. People with the link see it on a
             live chart and can copy it to build on.</p>
        </div>
      </div>
      <div class="su-grid">
        <div class="su-left">
          <div class="su-shot">${thumb ? `<img src="${esc(thumb)}" alt="Preview of this desk">`
            : `<div class="ly-noshot">${Icons.svg("candles", "sm")}<span>No preview</span></div>`}
            <span class="su-corner tl">┌</span><span class="su-corner tr">┐</span>
            <span class="su-corner bl">└</span><span class="su-corner br">┘</span></div>
          ${s ? `<pre class="su-spark" title="Closes on screen">${esc(s.line)}  <b class="${s.chg >= 0 ? "up" : "down"}">${s.chg >= 0 ? "+" : ""}${s.chg.toFixed(2)}%</b></pre>` : ""}
          <pre class="su-manifest" id="suManifest"></pre>
        </div>
        <div class="su-right">
          <label class="ly-lab" for="suTitle">Title</label>
          <input class="textfield" id="suTitle" maxlength="120" autocomplete="off"
                 value="${esc(existing ? existing.title : named || `${sym} ${iv} setup`)}">
          <label class="ly-lab su-gap" for="suNote">Your read <span class="su-count" id="suCount"></span></label>
          <textarea class="textfield su-note" id="suNote" maxlength="2000" rows="5"
            placeholder="What are you seeing? The levels that matter, what would confirm it, what would prove it wrong.">${esc(existing ? existing.note : "")}</textarea>
          <div class="su-switches">
            ${sw("include_chat", state.include_chat, `Include the conversation${chat.length ? ` · ${chat.length} turns` : ""}`,
                 chat.length ? `Starts with “${firstAsk.slice(0, 60)}${firstAsk.length > 60 ? "…" : ""}”. Text only; screenshots stay private.`
                             : "There is no conversation on this desk yet.")}
            ${sw("allow_copy", state.allow_copy, "Let others copy it as a template",
                 "Copies are theirs to edit. Your setup never changes.")}
          </div>
          ${existing ? `<div class="su-seg" role="radiogroup" aria-label="Link">
              <button type="button" data-mode="update" class="${state.mode === "update" ? "on" : ""}">Update the existing link</button>
              <button type="button" data-mode="new" class="${state.mode === "new" ? "on" : ""}">Publish a new link</button>
            </div>` : ""}
        </div>
      </div>
      <div class="su-foot">
        <span class="su-privacy">${Icons.svg("lock", "xs")} Unlisted. Only people with the link can open it; your email is never shown.</span>
        <div class="ly-actions">
          <button class="btn" data-x>Cancel</button>
          <button class="btn cta" data-ok>${Icons.svg("link", "xs")}<span>${existing ? "Update link" : "Publish"}</span></button>
        </div>
      </div>`);
    dlg.classList.add("su-publish");
    banner(sym).then((b) => { const f = dlg.querySelector(".su-fig"); if (f) f.textContent = b; });

    const paintManifest = () => {
      dlg.querySelector("#suManifest").textContent =
        frame(manifest(spec, state.include_chat && chat.length ? chat.length : null), "what travels");
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
        const d = await Layouts.call("/setups", {
          ...(state.mode === "update" && existing ? { token: existing.token } : {}),
          layout_id: cur && cur.id ? cur.id : undefined,
          title, note: note.value, spec, thumb,
          include_chat: !!(state.include_chat && chat.length), chat,
          allow_copy: state.allow_copy,
        });
        published(dlg, close, d.token, sym, title, !d.created);
      } catch (err) {
        ok.disabled = false;
        ok.querySelector("span").textContent = existing ? "Update link" : "Publish";
        toast(err.message);
      }
    };
  }

  function published(dlg, close, token, sym, title, updated) {
    const url = linkFor(token, sym);
    dlg.classList.add("su-done");
    dlg.innerHTML = `
      <pre class="su-okart" aria-hidden="true">${esc(frame([
        `${updated ? "UPDATED" : "PUBLISHED"}  /  read-only  /  ${sym}`,
        `"${title.slice(0, 40)}${title.length > 40 ? "..." : ""}"`,
      ], "setup"))}</pre>
      <p class="su-done-copy">${updated
        ? "The same link now shows this version. Copies people already took are unchanged."
        : "Anyone with this link can open the setup on a live chart and copy it to build on."}</p>
      <div class="su-linkrow">
        <input class="textfield su-link" readonly value="${esc(url)}" aria-label="Share link">
        <button class="btn cta" data-copy>${Icons.svg("copy", "xs")}<span>Copy link</span></button>
      </div>
      <div class="ly-actions">
        <button class="btn" data-manage>${Icons.svg("eye", "xs")}<span>My shared setups</span></button>
        <a class="btn" href="${esc(url)}" target="_blank" rel="noopener">${Icons.svg("externalLink", "xs")}<span>Open as a viewer</span></a>
        <button class="btn" data-x>Done</button>
      </div>`;
    const input = dlg.querySelector(".su-link");
    input.addEventListener("focus", () => input.select());
    dlg.querySelector("[data-copy]").onclick = () => copyText(url, "Link copied");
    dlg.querySelector("[data-x]").onclick = close;
    dlg.querySelector("[data-manage]").onclick = () => { close(); manage(); };
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
        box.innerHTML = `<pre class="su-empty">${esc(frame([
          "Nothing shared yet.", "",
          "Share  >  publishes the desk",
          "you are on, read-only.",
        ], "shared setups", 34))}</pre>`;
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
    bar.innerHTML = `<pre class="su-404" aria-hidden="true">${esc(frame([
      "404 / link closed", "", msg,
    ], "shared setup"))}</pre>
      <a class="btn cta" href="${esc(exitUrl())}">Open my chart</a>`;
    (el("stage") || document.body).appendChild(bar);
  }

  function mountBar(d) {
    const bar = document.createElement("div");
    bar.className = "su-bar";
    bar.id = "suBar";
    const canCopy = d.allow_copy || d.own;
    bar.innerHTML = `
      <span class="su-badge su-mono"><i></i>VIEW ONLY</span>
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
    card.innerHTML = `
      <pre class="su-fig" aria-hidden="true">${esc(d.symbol)}</pre>
      <h2 class="su-card-title">${esc(d.title)}</h2>
      <div class="su-card-by"><span class="su-avatar">${esc((d.by || "?").trim().charAt(0).toUpperCase())}</span>
        <span>${esc(d.by)}</span></div>
      ${stats}
      ${d.note ? `<div class="su-card-note">${esc(d.note)}</div>` : ""}
      ${s ? `<pre class="su-spark">${esc(s.line)}  <b class="${s.chg >= 0 ? "up" : "down"}">${s.chg >= 0 ? "+" : ""}${s.chg.toFixed(2)}%</b></pre>` : ""}
      <pre class="su-manifest">${esc(frame(manifest(d.spec, d.chat ? d.chat.length : null), "in this setup"))}</pre>
      ${d.lineage && d.lineage.length ? `<pre class="su-tree">${esc(lineageTree(d.lineage))}</pre>` : ""}
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
    banner(d.symbol).then((b) => { const f = card.querySelector(".su-fig"); if (f) f.textContent = b; });

    const wrap = document.querySelector(".composer-wrap");
    if (wrap && !el("suReadonly")) {
      const note = document.createElement("div");
      note.className = "su-readonly";
      note.id = "suReadonly";
      const canCopy = d.allow_copy || d.own;
      note.innerHTML = `<span class="su-mono su-ro-mark">┆</span>
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
           _ascii: { frame, spark, manifest, lineageTree, banner } };
})();

window.Setups = Setups;
