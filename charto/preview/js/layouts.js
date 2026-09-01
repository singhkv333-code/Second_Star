/* Charto preview — saved layouts.
 *
 * TradingView's "Manage layouts", on our terms. A LAYOUT is the whole desk:
 * the pane grid, what each pane is showing, everything drawn on it, the
 * indicator set, the volume profile — and the conversation that was had
 * there. Saved under a name, reopened whenever, optionally shared by link.
 *
 * ── two things called "layout" ───────────────────────────────────────────
 * This file owns the SAVED one. `layoutBtn` in the header owns the pane
 * GRID (42 arrangements, Icons.layoutSvg). TradingView has both under the
 * same word and so do we; the grid is one field of what this saves.
 *
 * ── what a snapshot is ───────────────────────────────────────────────────
 * `workspace` on __charto is the pair Undo already runs on — drawings,
 * scene, indicators, vp — because a saved layout is that same snapshot kept
 * under a name instead of on a stack. Anything undo learns to cover, a
 * layout stores for free. Around it this adds the grid, the per-pane
 * symbol/interval, and the chat id.
 *
 * ── the account is the storage ───────────────────────────────────────────
 * Signed out there is nothing to save TO, and rather than a local
 * almost-version that silently differs, the menu says so and offers sign-in.
 * localStorage still keeps the working session; that has not changed.
 *
 * ── opening across symbols ───────────────────────────────────────────────
 * The primary chart's symbol is fixed at boot from ?symbol= — the whole app
 * treats a symbol change as a new session, deliberately. So opening a layout
 * on a different symbol NAVIGATES, carrying ?layout=<id>, and the restore
 * happens on the other side. Same symbol restores in place.
 */
"use strict";

const Layouts = (() => {
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";
  const el = (id) => document.getElementById(id);
  const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  const UNTITLED = "Unnamed";
  const AUTOSAVE_MS = 2500;

  let list = [];                  // the index, newest-touched first
  let cur = null;                 // {id, name, autosave, shared, chat_id}
  let dirty = false;
  let saveTimer = null;
  let booted = false;

  /* ── what gets saved ──────────────────────────────────────────────────── */

  function snapshot() {
    const c = window.__charto;
    if (!c || !c.workspace) return null;
    const subs = c.panes.subsInfo ? c.panes.subsInfo() : [];
    return {
      v: 1,
      grid: c.panes.layout,
      // the primary first, then the secondaries in screen order — the same
      // order panes.js hands them back, so a restore can walk it straight
      charts: [{ symbol: c.symbol, interval: c.interval },
               ...subs.map((s) => ({ symbol: s.symbol, interval: s.interval }))],
      workspace: c.workspace.read(),
    };
  }

  /* ── the picture ──────────────────────────────────────────────────────
   *
   * A name tells you which layout you saved; a picture tells you which desk
   * it was. So the Open dialog leads with one, and it has to be the WHOLE
   * desk — a split screen whose thumbnail showed only the primary pane would
   * be a picture of a layout the user does not have.
   *
   * Every pane is its own lightweight-charts instance with its own
   * takeScreenshot(), so this composites them by their real screen rectangles
   * into one canvas the shape of the grid. 480px wide at JPEG 0.6 lands
   * around 30 KB, which is what makes it affordable to send forty of them.
   */
  const THUMB_W = 480;

  function thumbnail() {
    const c = window.__charto;
    if (!c) return "";
    try {
      const shots = [];
      const primary = document.getElementById("chart");
      if (primary && c.chart) shots.push([c.chart.takeScreenshot(),
                                          primary.getBoundingClientRect()]);
      for (let i = 1; ; i++) {
        const s = c.panes.paneAt(i);
        if (!s) break;
        if (s.chart && s.root) shots.push([s.chart.takeScreenshot(),
                                           s.root.getBoundingClientRect()]);
      }
      if (!shots.length) return "";
      const x0 = Math.min(...shots.map(([, r]) => r.left));
      const y0 = Math.min(...shots.map(([, r]) => r.top));
      const x1 = Math.max(...shots.map(([, r]) => r.right));
      const y1 = Math.max(...shots.map(([, r]) => r.bottom));
      const w = Math.max(1, x1 - x0), h = Math.max(1, y1 - y0);
      const k = THUMB_W / w;
      const out = document.createElement("canvas");
      out.width = THUMB_W;
      out.height = Math.max(1, Math.round(h * k));
      const ctx = out.getContext("2d");
      // the pane background, so a gap between panes is not transparent-black
      ctx.fillStyle = getComputedStyle(document.body)
        .getPropertyValue("--chart-bg").trim() || "#fff";
      ctx.fillRect(0, 0, out.width, out.height);
      for (const [cv, r] of shots) {
        ctx.drawImage(cv, (r.left - x0) * k, (r.top - y0) * k,
                      r.width * k, r.height * k);
      }
      return out.toDataURL("image/jpeg", 0.6);
    } catch (e) {
      // A layout still saves without its picture — the capture is the
      // decoration, never the record.
      console.warn("[charto] thumbnail failed", e);
      return "";
    }
  }

  function symbolsOf(spec) {
    return [...new Set((spec.charts || []).map((x) => x.symbol).filter(Boolean))];
  }

  /** Put a snapshot back on the chart. Same-symbol only — see the header. */
  async function restore(spec) {
    const c = window.__charto;
    if (!c || !c.workspace || !spec) return;
    if (spec.grid && spec.grid !== c.panes.layout) c.panes.apply(spec.grid);
    const charts = spec.charts || [];
    if (charts[0] && charts[0].interval && charts[0].interval !== c.interval) {
      await Promise.resolve(c.loadInterval(charts[0].interval)).catch(() => {});
    }
    // Secondaries are opened by the same call the chat uses to put a chart on
    // screen, so there is one path that creates a pane and it is already
    // proven. `replace` targets the pane by index rather than appending.
    for (let i = 1; i < charts.length; i++) {
      try { c.panes.openChart(charts[i].symbol, charts[i].interval, i); } catch {}
    }
    await c.workspace.write(spec.workspace || {});
    dirty = false;
    paint();
  }

  /* ── the account round trips ──────────────────────────────────────────── */

  const signedIn = () => typeof Auth !== "undefined" && !!Auth.token;

  async function call(path, body, method) {
    const r = await fetch(API + path, {
      method: method || (body === undefined ? "GET" : "POST"),
      headers: Auth.headers(body === undefined ? {}
        : { "Content-Type": "application/json" }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    let d = {};
    try { d = await r.json(); } catch {}
    if (!r.ok) throw new Error(d.error || `request failed (${r.status})`);
    return d;
  }

  async function refresh(withThumbs) {
    if (!signedIn()) { list = []; return list; }
    try {
      // thumbs only for the picker — see _layouts_list; a dropdown must not
      // pay a megabyte for pictures it does not show
      list = (await call(`/layouts${withThumbs ? "?thumbs=1" : ""}`)).layouts || [];
    } catch { list = []; }
    return list;
  }

  /* ── the operations behind the menu ───────────────────────────────────── */

  async function save({ name, asNew } = {}) {
    if (!signedIn()) return toast("Sign in to save layouts");
    const spec = snapshot();
    if (!spec) return toast("The chart is still loading");
    const body = {
      name: name || (cur && cur.name) || UNTITLED,
      spec, symbols: symbolsOf(spec), thumb: thumbnail(),
      chat_id: (window.Chat && window.Chat.activeId && window.Chat.activeId()) || "",
    };
    // Belt and braces on the hazard the boot adoption also guards: never
    // overwrite a layout that was saved on a DIFFERENT instrument. `cur`
    // carries the symbol it was opened on, so if the desk has since moved,
    // this becomes a NEW layout instead of silently replacing one that was
    // about something else.
    const here = symbolsOf(spec)[0];
    const sameSymbol = cur && (!cur.symbol || cur.symbol === here);
    if (!asNew && cur && cur.id && sameSymbol) body.id = cur.id;
    try {
      const d = await call("/layouts", body);
      cur = { ...(cur || {}), id: d.id, name: d.name, symbol: here,
              autosave: asNew ? false : !!(cur && cur.autosave) };
      dirty = false;
      await refresh();
      paint();
      toast(`Saved “${d.name}”`);
      return d;
    } catch (e) { toast(e.message); }
  }

  async function open(id) {
    if (!signedIn()) return;
    let d;
    try { d = await call(`/layouts?id=${id}`); }
    catch (e) { return toast(e.message); }
    const want = (d.spec.charts || [])[0];
    // A different instrument is a different session — the app has always
    // said so, and re-pointing every pane in place would be a second, worse
    // implementation of the reload that already does it correctly.
    if (want && want.symbol && want.symbol !== window.__charto.symbol) {
      const u = new URL(location.href);
      u.searchParams.set("symbol", want.symbol);
      u.searchParams.set("layout", String(id));
      location.href = u.toString();
      return;
    }
    cur = { id: d.id, name: d.name, autosave: d.autosave,
            shared: d.shared, chat_id: d.chat_id,
            symbol: (want && want.symbol) || null };
    await restore(d.spec);
    if (d.chat_id && window.Chat && window.Chat.openChat) {
      window.Chat.openChat(d.chat_id);
    }
    toast(`Opened “${d.name}”`);
    // Layouts saved before thumbnails existed have none, and there is no
    // moment better than this one to make a picture of them: the desk is on
    // screen because the user just asked for it. Backfilled once, quietly,
    // through a route that cannot touch the saved workspace — a picture is
    // never worth the risk of rewriting the thing it is a picture of.
    if (!d.has_thumb) {
      setTimeout(() => {
        const pic = thumbnail();
        if (!pic || !cur || cur.id !== d.id) return;
        call("/layouts", { id: d.id, thumb: pic }).catch(() => {});
      }, 1400);   // after the restore has settled and the chart has painted
    }
  }

  async function createNew() {
    const name = await prompt_("Create new layout", "Name", UNTITLED);
    if (name === null) return;
    const c = window.__charto;
    // A NEW layout starts from a clean desk, not from whatever is on screen —
    // otherwise "create new" would be "make a copy" wearing another name.
    if (c && c.workspace) {
      await c.workspace.write({ drawings: [], scene: [], indicators: [], vp: null });
    }
    if (window.Chat && window.Chat.newChat) window.Chat.newChat();
    cur = null;
    await save({ name: name || UNTITLED, asNew: true });
  }

  async function copy() {
    if (!cur || !cur.id) return toast("Save this layout first");
    try {
      const d = await call("/layouts", { id: cur.id, copy: true });
      cur = { id: d.id, name: d.name, autosave: false, shared: false,
              symbol: cur.symbol };
      await refresh(); paint();
      toast(`Copied to “${d.name}”`);
    } catch (e) { toast(e.message); }
  }

  async function rename() {
    if (!cur || !cur.id) return toast("Save this layout first");
    const name = await prompt_("Rename layout", "Name", cur.name);
    if (name === null || !name.trim()) return;
    await save({ name: name.trim() });
  }

  async function remove(id, name) {
    if (!await confirm_("Delete layout",
                        `“${name}” and everything saved in it will be removed.`,
                        "Delete")) return;
    try {
      await call("/layouts", { id, delete: true });
      if (cur && cur.id === id) cur = null;
      await refresh(); paint(); renderOpen();
      toast(`Deleted “${name}”`);
    } catch (e) { toast(e.message); }
  }

  async function setAutosave(on) {
    if (!cur || !cur.id) {
      toast("Save this layout first");
      return false;
    }
    try {
      await call("/layouts", { id: cur.id, autosave: !!on });
      cur.autosave = !!on;
      paint();
      return true;
    } catch (e) { toast(e.message); return false; }
  }

  async function setShared(on) {
    if (!cur || !cur.id) { toast("Save this layout first"); return false; }
    try {
      const d = await call("/layouts", { id: cur.id, share: !!on });
      cur.shared = d.shared;
      cur.share_token = d.token || null;
      if (d.token) {
        toast("Sharing on — anyone with the link can view this layout");
      } else {
        toast("Sharing off — the old link no longer works");
      }
      paint();
      return true;
    } catch (e) { toast(e.message); return false; }
  }

  async function copyShareLink() {
    if (!cur || !cur.id) return toast("Save this layout first");
    try {
      const d = cur.share_token ? { token: cur.share_token }
        : await call("/layouts", { id: cur.id, share: true });
      if (!d.token) throw new Error("Could not create a share link");
      cur.shared = true;
      cur.share_token = d.token;
      const link = `${location.origin}${location.pathname}?shared=${d.token}`;
      await navigator.clipboard.writeText(link);
      toast("Link copied — anyone with it can view this layout");
      paint(); renderMenu();
    } catch (e) { toast(e.message || "Could not copy the link"); }
  }

  /** The bars on screen, as CSV. The chart's own series, not a re-fetch:
   *  what you download is what you were looking at, including the interval
   *  and any forming bar. */
  function downloadData() {
    const c = window.__charto;
    const bars = (c && c.state && c.state.bars) || [];
    if (!bars.length) return toast("No bars loaded");
    const iv = c.interval;
    // `state.bars[].time` is ALREADY in display space — main.js's fetchBars
    // adds the instrument's offset so the axis reads local. Adding it again
    // here stamped the last 15m bar of an NSE session at 20:30, five and a
    // half hours after the exchange shut. So format as UTC and the clock
    // comes out as the chart shows it.
    //
    // A DAILY bar's column is a DATE. Its stamp is the session anchor, and
    // rendering that as a clock gave "2015-02-02T05:30" — the offset itself,
    // not a moment anything traded.
    const daily = ["1d", "1w", "1mo"].includes(iv);
    const stamp = (t) => {
      const s = new Date(t * 1000).toISOString();
      return daily ? s.slice(0, 10) : s.slice(0, 16).replace("T", " ");
    };
    const rows = [["time", "open", "high", "low", "close", "volume"].join(",")];
    for (const b of bars) {
      rows.push([stamp(b.time), b.open, b.high, b.low, b.close,
                 b.volume ?? ""].join(","));
    }
    const blob = new Blob([rows.join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `${c.symbol}_${iv}_${bars.length}bars.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    toast(`${bars.length.toLocaleString()} bars downloaded`);
  }

  /* ── autosave ─────────────────────────────────────────────────────────── */

  /** Something changed. Debounced, and only ever for a layout that has been
   *  saved once AND has autosave armed — silently writing to a layout the
   *  user never named is how work gets overwritten. */
  function touch() {
    dirty = true;
    paint();
    if (!booted || !cur || !cur.id || !cur.autosave) return;
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      const spec = snapshot();
      if (!spec) return;
      call("/layouts", { id: cur.id, name: cur.name, spec,
                         symbols: symbolsOf(spec), thumb: thumbnail() })
        .then(() => { dirty = false; paint(); })
        .catch(() => {});     // a failed autosave must never interrupt work
    }, AUTOSAVE_MS);
  }

  /* ── chrome ───────────────────────────────────────────────────────────── */

  function toast(msg) {
    let t = el("layoutToast");
    if (!t) {
      t = document.createElement("div");
      t.id = "layoutToast";
      t.className = "layout-toast";
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("show"), 2600);
  }

  const nameBtn = () => el("lySaveName");
  const menu = () => el("lyMenu");

  function paint() {
    const b = nameBtn();
    if (!b) return;
    const label = (cur && cur.name) || UNTITLED;
    b.querySelector(".ly-name").textContent = label;
    // NO DOT. It marked unsaved changes, and it sat in the top bar beside the
    // layout name as a live blue pip — a persistent alert for a state that is
    // both normal and not urgent, since every layout is dirty the moment you
    // move anything. Removed on request: the same fact still reaches anyone
    // who wants it through the button's title, which costs nothing until it
    // is asked for. The markup and its rule stay put so the element can come
    // back without hunting for it.
    b.querySelector(".ly-dot").hidden = true;
    b.title = dirty ? `${label} — unsaved changes` : label;
    const m = menu();
    if (!m || !m.classList.contains("open")) return;
    renderMenu();
  }

  /* One menu row, in the shape every other menu in this app uses: a `.lead`
   * holding the glyph and the label, and one trailing slot for whatever the
   * row has to say about itself — a shortcut, a switch, a symbol. The rows
   * here used to be a flat icon+span with their own flex rules, which is why
   * the shortcut needed a <kbd> of its own instead of the `.sc` the toolbar's
   * flyout already prints its Alt-chords in. */
  function row(icon, label, extra = "", trail = "") {
    return `<div class="item" ${extra}><span class="lead">`
      + `${Icons.svg(icon, "xs")}${esc(label)}</span>${trail}</div>`;
  }

  /* A switch row. `note` is the caveat that does not fit in the label — it
   * hangs off a real ⓘ from the icon set, and clicking it is NOT clicking the
   * row: `data-note` is how the menu's handler tells "explain this" from
   * "flip this", so reaching for the explanation cannot arm the thing being
   * explained. */
  function toggleRow(id, label, on, note) {
    return `<div class="item ly-toggle" data-act="${id}">`
      + `<span class="lead">${esc(label)}`
      + (note ? `<span class="ly-note" data-note title="${esc(note)}">`
                + `${Icons.svg("info", "xs")}</span>` : "")
      + `</span>`
      + `<span class="switch${on ? " on" : ""}"><i></i></span></div>`;
  }

  /* A layout you were recently in, on ONE line: the name, and the instruments
   * it is about at the row's end. It was two stacked lines — a name over a
   * subtitle, in a card twice the height of every other row in the menu — and
   * the section read as a panel that had been pasted into a list.
   *
   * The one you are sitting in is marked the way this app marks a current row
   * everywhere else: the name in the accent, no fill (see .dropdown .item.on
   * in the stylesheet). */
  const recentRow = (L) =>
    `<div class="item ly-recent${cur && cur.id === L.id ? " on" : ""}" `
    + `data-open="${L.id}" title="${esc(L.name)}">`
    + `<span class="lead">${esc(L.name)}</span>`
    + `<span class="ly-rsym">${esc(L.symbols.join(" · ") || "—")}</span></div>`;

  function renderMenu() {
    const m = menu();
    if (!m) return;
    if (!signedIn()) {
      m.innerHTML = `<div class="head">Layouts</div>`
        + `<div class="ly-empty">Sign in to save layouts, reopen them on any `
        + `device, and share a link.</div>`
        + row("user", "Sign in…", 'data-act="signin"');
      return;
    }
    const recent = list.slice(0, 5);
    // The chord comes from the shortcuts catalogue, not from a string typed
    // here — this menu used to print ⌘S to a Windows reader whose keyboard
    // has no such key.
    const key = Shortcuts.chord("save-layout");
    m.innerHTML =
      row("download", "Save layout", 'data-act="save"',
          key ? `<span class="sc">${esc(key)}</span>` : "")
      + toggleRow("autosave", "Autosave", !!(cur && cur.autosave))
      + toggleRow("share", "Share layout", !!(cur && cur.shared),
                  "Anyone with the link can view this layout, read-only.")
      + (cur && cur.shared
        ? row("link", "Copy link…", 'data-act="copylink"') : "")
      + `<div class="sep"></div>`
      + row("copy", "Make a copy…", 'data-act="copy"')
      + row("pen", "Rename…", 'data-act="rename"')
      + row("download", "Download chart data…", 'data-act="csv"')
      + `<div class="sep"></div>`
      + row("plus", "Create new layout…", 'data-act="new"')
      + (recent.length
        ? `<div class="sep"></div><div class="head">Recently used</div>`
          + recent.map(recentRow).join("")
        : "")
      + `<div class="sep"></div>`
      + row("list", "Open layout…", 'data-act="openall"');
  }

  /* ── dialogs ──────────────────────────────────────────────────────────── */

  function dlg(html, cls) {
    const back = document.createElement("div");
    back.className = "ly-back";
    back.innerHTML = `<div class="ly-dlg${cls ? " " + cls : ""}">${html}</div>`;
    document.body.appendChild(back);
    const close = () => back.remove();
    back.addEventListener("mousedown", (e) => { if (e.target === back) close(); });
    document.addEventListener("keydown", function esc2(e) {
      if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc2); }
    });
    return { back, close };
  }

  function prompt_(title, label, value) {
    return new Promise((res) => {
      const { back, close } = dlg(
        `<h3>${esc(title)}</h3>`
        + `<label class="ly-lab" for="lyIn">${esc(label)}</label>`
        // .textfield, not .dlg-input: the latter is the settings dialog's
        // 34px numeric cell — tabular figures, a hairline border, sized for
        // a period or an offset. Stretched to the full width of a dialog to
        // hold a layout's NAME it read as a form control borrowed from
        // somewhere else. This is the filled, borderless box the app's other
        // text entries wear.
        + `<input class="textfield" id="lyIn" value="${esc(value || "")}" `
        + `placeholder="${esc(UNTITLED)}" autocomplete="off" spellcheck="false">`
        + `<div class="ly-actions"><button class="btn" data-x>Cancel</button>`
        + `<button class="btn primary" data-ok>Save</button></div>`);
      const inp = back.querySelector("#lyIn");
      inp.focus(); inp.select();
      const ok = () => { const v = inp.value; close(); res(v); };
      back.querySelector("[data-ok]").onclick = ok;
      back.querySelector("[data-x]").onclick = () => { close(); res(null); };
      inp.onkeydown = (e) => { if (e.key === "Enter") ok(); };
    });
  }

  function confirm_(title, body, okLabel) {
    return new Promise((res) => {
      const { back, close } = dlg(
        `<h3>${esc(title)}</h3><p class="ly-body">${esc(body)}</p>`
        + `<div class="ly-actions"><button class="btn" data-x>Cancel</button>`
        + `<button class="btn danger" data-ok>${esc(okLabel)}</button></div>`);
      back.querySelector("[data-ok]").onclick = () => { close(); res(true); };
      back.querySelector("[data-x]").onclick = () => { close(); res(false); };
    });
  }

  let openDlg = null;
  async function renderOpen() {
    if (!openDlg) return;
    const q = (openDlg.back.querySelector("#lyq").value || "").toLowerCase();
    const rows = list.filter((L) => !q || L.name.toLowerCase().includes(q)
      || L.symbols.join(" ").toLowerCase().includes(q));
    // A CARD, led by the picture. The name tells you which one you saved;
    // the chart tells you which desk it was, and that is what a person
    // actually recognises. Details sit under it, quiet and in one line.
    openDlg.back.querySelector("#lylist").innerHTML = rows.length
      ? `<div class="ly-grid">` + rows.map((L) => `
          <div class="ly-card${cur && cur.id === L.id ? " on" : ""}" data-id="${L.id}">
            <div class="ly-shot">
              ${L.thumb ? `<img src="${esc(L.thumb)}" alt="" loading="lazy">`
                        : `<div class="ly-noshot">${Icons.svg("candles", "sm")}
                             <span>No preview</span></div>`}
              <button class="ly-del" data-del="${L.id}" title="Delete">
                ${Icons.svg("trash", "xs")}</button>
            </div>
            <div class="ly-meta">
              <div class="ly-item-name">${esc(L.name)}
                ${L.shared ? '<span class="ly-tag">Shared</span>' : ""}
                ${L.autosave ? '<span class="ly-tag alt">Auto</span>' : ""}</div>
              <div class="ly-item-sub">${esc(L.symbols.join(" · ") || "—")}
                <span class="ly-dotsep">·</span> ${when(L.updated)}</div>
            </div>
          </div>`).join("") + `</div>`
      : `<div class="ly-empty">${q ? "Nothing matches that." : "No saved layouts yet."}</div>`;
  }

  function when(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    const days = Math.floor((Date.now() / 1000 - ts) / 86400);
    if (days === 0) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (days === 1) return "yesterday";
    if (days < 7) return `${days} days ago`;
    return d.toLocaleDateString([], { day: "2-digit", month: "short" });
  }

  async function openPicker() {
    await refresh(true);
    openDlg = dlg(
      `<h3>Open layout</h3>`
      // The app's search box, from the one place that draws it: a filled
      // field with the magnifier that goes from grey to ink when the field
      // is live. It was a bare .dlg-input — a bordered numeric cell with a
      // placeholder in it, and the only "type to filter this list" box in
      // charto that did not look like the others or say it was focused.
      + Icons.field(`<input id="lyq" type="search" autocomplete="off"`
                    + ` placeholder="Search layouts or symbols">`)
      + `<div class="ly-list" id="lylist"></div>`
      // `data-ly-new`, NOT `data-new` — see the click handler below.
      + `<div class="ly-actions"><button class="btn" data-x>Close</button>`
      + `<button class="btn primary" data-ly-new>Create new…</button></div>`,
      "wide");
    renderOpen();
    openDlg.back.querySelector("#lyq").addEventListener("input", renderOpen);
    openDlg.back.querySelector("[data-x]").onclick = () => { openDlg.close(); openDlg = null; };
    /* This button was `data-new`, and js/journal.js listens for that attribute
     * on the DOCUMENT — its "Add trade" button wears the same name. So "Create
     * new layout" opened the New trade drawer on top of the layout prompt: two
     * dialogs, one click, and the one in front was about a trade.
     *
     * Scoping journal's listener to its own roots is the real fix and is done
     * there. This name is prefixed as well, because `data-new` is exactly the
     * kind of generic hook a third module will reach for next. */
    openDlg.back.querySelector("[data-ly-new]").onclick = () => {
      openDlg.close(); openDlg = null; createNew();
    };
    openDlg.back.addEventListener("click", (e) => {
      const del = e.target.closest("[data-del]");
      if (del) {
        e.stopPropagation();
        const L = list.find((x) => String(x.id) === del.dataset.del);
        if (L) remove(L.id, L.name);
        return;
      }
      const it = e.target.closest(".ly-card");
      if (!it) return;
      openDlg.close(); openDlg = null;
      open(Number(it.dataset.id));
    });
  }

  /* ── wiring ───────────────────────────────────────────────────────────── */

  function bind() {
    const b = nameBtn(), m = menu();
    if (!b || !m) return;
    b.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (window.__chartoCloseMenus) window.__chartoCloseMenus(m);
      const opening = !m.classList.contains("open");
      if (opening) { await refresh(); renderMenu(); }
      m.classList.toggle("open", opening);
    });
    m.addEventListener("click", async (e) => {
      /* THIS MENU CLOSES ITSELF, and nothing else may close it.
       *
       * js/main.js dismisses every open .dropdown on any document click, and
       * that listener is what was shutting the menu the instant a switch was
       * flipped: the toggle branch below deliberately does not close, and
       * then the click bubbled up and closed it anyway. Reading the switch's
       * new position meant reopening the menu to look.
       *
       * Stopping here covers the dead clicks too — a press on a heading or a
       * rule is not a command, and a menu that vanishes when you click its
       * own chrome feels like it was dismissed by accident. */
      e.stopPropagation();
      const openRow = e.target.closest("[data-open]");
      if (openRow) { m.classList.remove("open"); return open(Number(openRow.dataset.open)); }
      // The ⓘ explains its row; it does not fire it. Its tooltip is the whole
      // interaction, and flipping sharing ON because someone reached for the
      // reason not to would be the worst possible reading of that gesture.
      if (e.target.closest("[data-note]")) return;
      const it = e.target.closest("[data-act]");
      if (!it) return;
      const act = it.dataset.act;
      // The two switches stay put and flip in place; everything else is a
      // command and closes the menu, which is TradingView's own split.
      if (act === "autosave") {
        const on = !(cur && cur.autosave);
        if (await setAutosave(on)) renderMenu();
        return;
      }
      if (act === "share") {
        const on = !(cur && cur.shared);
        if (await setShared(on)) renderMenu();
        return;
      }
      m.classList.remove("open");
      if (act === "save") save();
      else if (act === "copylink") copyShareLink();
      else if (act === "copy") copy();
      else if (act === "rename") rename();
      else if (act === "csv") downloadData();
      else if (act === "new") createNew();
      else if (act === "openall") openPicker();
      else if (act === "signin" && window.CHARTO_AUTH_OPEN) window.CHARTO_AUTH_OPEN();
    });

    // ⌘S / Ctrl+S — TradingView's shortcut, and the browser's Save Page is
    // not a thing anyone wants on a chart. Registered as a VERB against the
    // shortcuts catalogue rather than bound here: that is what puts the row
    // on the sheet, prints the right cap per platform in the menu above, and
    // applies the guard every other chord gets — this used to fire mid-word
    // in the chat composer, because a hand-rolled keydown does not know what
    // the reader is typing into.
    Shortcuts.on("save-layout", () => save());

    // Everything that edits the workspace already announces itself; autosave
    // and the unsaved dot both ride on those rather than on a poll.
    for (const ev of ["charto:indicators-changed", "charto:drawings-changed",
                      "charto:scene-changed"]) {
      document.addEventListener(ev, touch);
    }
  }

  async function boot() {
    bind();
    paint();
    if (!signedIn()) return;
    await refresh();
    const want = new URLSearchParams(location.search).get("layout");
    if (want && list.some((L) => String(L.id) === want)) {
      await open(Number(want));
      const u = new URL(location.href);      // don't re-open on every reload
      u.searchParams.delete("layout");
      history.replaceState(null, "", u.toString());
    } else if (list.length && !cur) {
      /* Adopt the most recently opened layout as the one you are sitting in —
       * but ONLY if it is on this symbol.
       *
       * Without that test, opening AARTIIND showed the name of a layout saved
       * on RELIANCE, and the header was not merely wrong: ⌘S would then have
       * written the AARTIIND desk over the RELIANCE layout, under its name,
       * destroying it. A label that lies about identity is a save-over
       * waiting to happen.
       *
       * Its contents are still not applied either way — a reload restores the
       * live session from localStorage, and re-applying a snapshot over that
       * would discard whatever has been done since.
       */
      const here = window.__charto && window.__charto.symbol;
      const top = [...list]
        .filter((L) => (L.symbols || [])[0] === here)
        .sort((a, b) => (b.opened || 0) - (a.opened || 0))[0];
      if (top) {
        cur = { id: top.id, name: top.name, autosave: top.autosave,
                shared: top.shared, chat_id: top.chat_id, symbol: here };
      }
    }
    booted = true;
    paint();
  }

  document.addEventListener("charto:workspace-ready", () => { boot(); }, { once: true });

  return { save, open, openPicker, createNew, downloadData, touch,
           /* This file owns the app's one toast element and its one timer, so
            * it owns the app's one toast. js/alerts.js already re-declared an
            * identical local copy against the same #layoutToast; exporting it
            * is what stops a third from appearing. It matters more than it
            * looks: the chart's status strip (#statusLine, #drawStatus) is
            * long gone from the markup, so every status() and setStatus()
            * call in main.js and drawings.js is a silent no-op, and anything
            * that genuinely has to be SEEN — a copy that failed, a symbol
            * filed onto a list you cannot see — has to come through here. */
           toast,
           get current() { return cur; }, refresh };
})();
