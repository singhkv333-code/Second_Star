/* Charto preview — the phone's chart toolbar.
 *
 * On a portrait phone the 48px header is hidden (see the stylesheet's
 * max-width:560px block) and this one line under the chart takes its place:
 * five slots, each opening a bottom sheet. TradingView's mobile chart is
 * shaped the same way for the same reason — a phone fits ONE row of
 * controls, so the row has to be a set of doors, not a set of controls.
 *
 * THE ONE RULE HERE: this file owns no state and duplicates no control.
 * Every slot either clicks the real header/rail button behind it, or MOVES
 * the real menu into the sheet and moves it back on close. So the interval
 * the bar shows cannot disagree with the interval the chart is on, and the
 * instrument list in the sheet is the same list — same fetch, same handlers,
 * same selection — the header pill opens on a desktop. A phone copy of
 * either would be a second source of truth, which is the bug class this
 * codebase is built to avoid.
 *
 * Nothing here runs above 560px: the bar is display:none there, and its own
 * buttons are the only way in.
 */
"use strict";

(() => {
  const el = (id) => document.getElementById(id);
  const bar = el("mbar");
  if (!bar) return;

  const mq = window.matchMedia("(max-width: 560px) and (orientation: portrait)");

  /* ── the sheet ───────────────────────────────────────────────────────────
   * One instance, reused. It is a modal surface in the CLICK sense too: a
   * tap inside it must never reach the document handler that closes every
   * open dropdown, because a hosted menu is still rendering against its own
   * .open class and would go blank under the finger. */
  const host = document.createElement("div");
  host.className = "sheet-host";
  host.innerHTML =
    '<div class="sheet-scrim" data-close></div>' +
    '<section class="sheet" role="dialog" aria-modal="true" aria-labelledby="sheetTitle">' +
      '<div class="sheet-grab"></div>' +
      '<div class="sheet-head">' +
        '<div class="sheet-title" id="sheetTitle"></div>' +
        '<button type="button" class="sheet-x" data-close aria-label="Close"></button>' +
      '</div>' +
      '<div class="sheet-body" id="sheetBody"></div>' +
    '</section>';
  document.body.appendChild(host);

  const sBody = el("sheetBody"), sTitle = el("sheetTitle");
  host.querySelector(".sheet-x").innerHTML = Icons.svg("x", "sm");

  /* A hosted node is the app's REAL element, borrowed. Where it came from is
   * remembered exactly (parent + next sibling) so putting it back cannot
   * reorder the header. */
  let hosted = [];
  function adopt(node) {
    if (!node) return;
    hosted.push({ node, home: node.parentNode, next: node.nextSibling });
    sBody.appendChild(node);
  }
  function release() {
    for (const h of hosted.reverse()) {
      h.node.classList.remove("open");
      h.home.insertBefore(h.node, h.next);
    }
    hosted = [];
  }

  // id → the sheet. Declared before use only as data, so a slot cannot open
  // a title that belongs to a different body.
  const SHEETS = {};
  let openId = null;

  function closeSheet() {
    if (!openId) return;
    release();
    sBody.innerHTML = "";
    host.classList.remove("open");
    openId = null;
    syncBar();
  }
  /** Tapping the slot that is already open closes it — every other menu in
   *  the app behaves that way. */
  function openSheet(id) {
    const again = openId === id;
    closeSheet();
    if (again) return;
    const s = SHEETS[id];
    if (!s) return;
    openId = id;
    sTitle.textContent = s.title;
    host.classList.add("open");
    s.fill(sBody);
    syncBar();
  }
  /** Rebuild the open sheet in place — for a choice that changes what the
   *  sheet itself should say (the theme tile, the magnet) rather than
   *  something on the chart behind it. */
  function repaint() {
    if (!openId) return;
    release();
    sBody.innerHTML = "";
    SHEETS[openId].fill(sBody);
  }

  host.addEventListener("click", (e) => {
    // stop here, always — see the note above the sheet's construction
    e.stopPropagation();
    // A choice that CHANGES THE CHART dismisses the sheet; a choice that
    // toggles something on it (an indicator, a volume-profile window) does
    // not, because those are multi-select by design. The hosted menus'
    // own handlers have already run by the time this fires.
    if (e.target.closest("[data-close], [data-layout], [data-shot], [data-sym], [data-acct]")) {
      closeSheet();
    }
  });
  addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });
  // rotating the phone, or a window crossing the breakpoint, takes the bar
  // away — the sheet it opened must not outlive it
  mq.addEventListener("change", closeSheet);

  /** Click a real control with the sheet already out of the way: a region
   *  capture, a theme repaint or an armed drawing tool all need the chart
   *  visible, not a panel over it. */
  function act(node) {
    if (!node) return;
    closeSheet();
    requestAnimationFrame(() => node.click());
  }

  const tile = (attr, icon, label, cls = "") =>
    `<button type="button" class="tile ${cls}" ${attr}>${icon}` +
    `<span class="tile-lbl">${label}</span></button>`;
  /** A glyph already drawn somewhere in the app, at tile size. Lifted rather
   *  than re-mapped: the tool→icon map lives in main.js, and a second copy is
   *  a second chance for a tool to wear the wrong picture. */
  const lift = (node, fallback) => {
    const svg = node && node.querySelector("svg");
    return svg ? svg.outerHTML.replace("icon sm", "icon") : Icons.svg(fallback);
  };

  /* ── slot · instrument ────────────────────────────────────────────────
   * The header pill's own menu, moved. Clicking the pill is what renders
   * the list, focuses the box and pulls the 500-company universe — none of
   * which this file may reimplement. */
  SHEETS.symbol = { title: "Instruments", fill() {
    const menu = el("symbolMenu");
    menu.classList.remove("open");
    adopt(menu);
    el("symbolPill").click();
  } };

  /* ── slot · interval ──────────────────────────────────────────────────
   * Read off the header's interval MENU, and every tap is a click ON one of
   * its rows, so main.js's pane-aware routing (a selected secondary chart
   * keeps its own interval) applies here untouched.
   *
   * The menu spells the interval out ("15 minutes") because it has a column
   * of width to do it in; a 74px tile does not, so the tiles wear the short
   * form the header pill wears — carried on the row itself as data-short so
   * there is still only one list of intervals in the app. */
  SHEETS.interval = { title: "Interval", fill(b) {
    b.innerHTML = '<div class="sheet-grid compact">' +
      [...el("intervalMenu").querySelectorAll("button[data-iv]")].map((x) =>
        `<button type="button" class="tile ${x.classList.contains("active") ? "on" : ""}" ` +
        `data-iv="${x.dataset.iv}"><span class="tile-lbl">${x.dataset.short}</span></button>`
      ).join("") + "</div>";
  } };

  /* ── slot · indicators ────────────────────────────────────────────────
   * The menu, borrowed whole — #indBtn's click is what renders it against
   * the SELECTED pane. This sheet only ADDS: what is already on the chart is
   * edited from the legend written on the chart itself, where a phone shows
   * the eye/gear/×/⋯ permanently rather than on a hover it cannot perform. */
  SHEETS.indicators = { title: "Indicators", fill(b) {
    const menu = el("indMenu");
    menu.classList.remove("open");
    adopt(menu);
    el("indBtn").click();
  } };

  /* ── slot · drawings ──────────────────────────────────────────────────
   * The tool catalogue, one category at a time — the same grouping the
   * desktop rail uses, as a grid because a phone has width and no hover.
   * Each tile clicks the rail's own menu item, so arming a tool takes the
   * exact path a mouse takes (including the rail remembering it as that
   * group's last tool). */
  let drawTab = (Tools.GROUPS[0] || {}).id;

  const railTool = (id) => document.querySelector(`.rail .item[data-tool="${id}"]`);

  function paintDrawGrid() {
    const g = Tools.GROUPS.find((x) => x.id === drawTab) || Tools.GROUPS[0];
    const armed = document.querySelector(".rail .item[data-tool].on");
    const grid = sBody.querySelector("#drawGrid");
    if (!grid) return;
    const all = Object.entries(Tools.SPECS).filter(([, s]) => s.group === g.id);
    const tiles = (rows) => rows.map(([id, s]) =>
      tile(`data-tool="${id}"`, lift(railTool(id), g.icon), s.label,
           armed && armed.dataset.tool === id ? "on" : "")).join("");
    // A sectioned group (Lines, seventeen tools) is banded here exactly as
    // it is in the rail's flyout — seventeen unlabelled tiles in one grid is
    // a wall, and the phone has the same three names available to break it.
    // The container stops being the grid in that case and becomes the column
    // the per-section grids stack in.
    grid.className = g.sections ? "" : "sheet-grid";
    grid.innerHTML = g.sections
      ? g.sections.map(([sid, slabel]) => {
          const rows = all.filter(([, s]) => (s.section || g.id) === sid);
          return rows.length
            ? `<div class="sheet-sec">${slabel}</div>`
              + `<div class="sheet-grid">${tiles(rows)}</div>`
            : "";
        }).join("")
      : tiles(all);
  }
  /* The rail's trash opens a menu naming exactly what would go — "Remove 2
   * drawings", "Remove 4 annotations" — instead of clearing on trust. A phone
   * has no room for a menu hanging off a tile, and it does not need one: the
   * sheet IS the menu, so each of those rows becomes its own tile and the
   * choice is made in the same tap. The model behind them is main.js's
   * (window.__charto.objects), so a layer cannot exist here and not there. */
  const objects = () => (window.__charto && window.__charto.objects) || null;

  function paintDrawActs() {
    const acts = sBody.querySelector("#drawActs");
    if (!acts) return;
    const o = objects();
    const live = o ? o.live() : [];
    acts.innerHTML =
      tile('data-tool="cursor"', Icons.svg("crosshair"), "Cursor",
           el("tool-cursor").classList.contains("active") ? "on" : "") +
      tile('data-act="magnet"', Icons.svg("magnet"), "Magnet",
           el("tool-magnet").classList.contains("toggled") ? "on" : "") +
      tile('data-act="export"', Icons.svg("download"), "Export") +
      // Nothing on the chart, no tile: a "Clear all" on an empty chart is a
      // button that can only ever do nothing.
      live.map((x) => tile(`data-clear="${x.key}"`, Icons.svg(x.icon),
                           x.label, "danger")).join("") +
      (live.length > 1
        ? tile(`data-clear="${live.map((x) => x.key).join(" ")}"`,
               Icons.svg("trash"), "Remove all", "danger") : "");
  }

  SHEETS.drawings = { title: "Drawings", fill(b) {
    b.innerHTML =
      '<div class="sheet-tabs">' + Tools.GROUPS.map((g) =>
        `<button type="button" class="stab ${g.id === drawTab ? "on" : ""}" ` +
        `data-tab="${g.id}">${g.label}</button>`).join("") + "</div>" +
      '<div class="sheet-grid" id="drawGrid"></div>' +
      '<div class="sheet-sec">Chart</div>' +
      '<div class="sheet-grid" id="drawActs"></div>';
    paintDrawGrid();
    paintDrawActs();
  } };

  /* ── slot · more ──────────────────────────────────────────────────────
   * Everything the header carried that is not an instrument, an interval,
   * an indicator or a drawing. Each tile is one real button. */
  SHEETS.more = { title: "More", fill(b) {
    const chatOn = !el("chatPanel").classList.contains("hidden");
    const drawn = el("sceneClear").style.display !== "none";
    b.innerHTML =
      '<div class="sheet-sec">Chart</div><div class="sheet-grid">' +
        tile('data-more="settings"', Icons.svg("settings"), "Settings") +
        tile('data-more="layout"', lift(el("layoutBtn"), "panelRight"), "Layout") +
        tile('data-more="shotFull"', Icons.svg("camera"), "Screenshot") +
        tile('data-more="shotRegion"', Icons.svg("rect"), "Select area") +
      "</div>" +
      // No Panels section: the watchlist and alerts are buttons ON the bar
      // now, one tap away. A door in here to the same two would be a second
      // path to a control that is already visible.
      '<div class="sheet-sec">Conversation</div><div class="sheet-grid">' +
        tile('data-more="chat"', Icons.svg("chat"), chatOn ? "Hide chat" : "Show chat",
             chatOn ? "on" : "") +
        (drawn ? tile('data-more="scene"', Icons.svg("eraser"), "Clear chat drawings") : "") +
      "</div>" +
      // The header's avatar is one of the controls this width hides, and it
      // is the only door to signing out — so it gets a tile here rather than
      // being unreachable on a phone.
      '<div class="sheet-sec">Account</div><div class="sheet-grid">' +
        tile('data-more="account"', Icons.svg("user"),
             Auth.user ? (Auth.user.name || Auth.user.email) : "Sign in") +
      "</div>";
  } };

  SHEETS.layout = { title: "Layout", fill() {
    const menu = el("layoutMenu");
    menu.classList.remove("open");
    adopt(menu);
    el("layoutBtn").click();
  } };

  SHEETS.account = { title: "Account", fill() {
    const menu = el("acctMenu");
    menu.classList.remove("open");
    adopt(menu);
    el("acctBtn").click();
  } };

  /* ── one delegated handler for every sheet body ───────────────────────
   * Registered once. Filling a sheet must never add a listener: the body
   * element survives every open, so a per-fill listener would stack one
   * copy per visit and fire a tool five times on the fifth open. */
  sBody.addEventListener("click", (e) => {
    const iv = e.target.closest("[data-iv]");
    if (iv) return act(el("intervalMenu").querySelector(`button[data-iv="${iv.dataset.iv}"]`));

    const tab = e.target.closest("[data-tab]");
    if (tab) {
      drawTab = tab.dataset.tab;
      for (const n of sBody.querySelectorAll(".stab")) {
        n.classList.toggle("on", n.dataset.tab === drawTab);
      }
      return paintDrawGrid();
    }
    // arming a tool leaves the chart visible — the next thing the finger
    // does is place an anchor on it
    const tool = e.target.closest("[data-tool]");
    if (tool) return act(railTool(tool.dataset.tool) || el(`tool-${tool.dataset.tool}`));

    // The sheet stays open and repaints: the tile that was just used is gone
    // (its layer is empty now) and the counts on the others are still true,
    // which is the confirmation — nothing has to be read off the status strip
    // the sheet is covering.
    const rm = e.target.closest("[data-clear]");
    if (rm) {
      const o = objects();
      if (o) o.clear(rm.dataset.clear.split(" "));
      return paintDrawActs();
    }

    const a = e.target.closest("[data-act]");
    if (a) {
      if (a.dataset.act === "magnet") {
        el("tool-magnet").click();
        return paintDrawActs();
      }
      return act(el(`tool-${a.dataset.act}`));
    }

    const m = e.target.closest("[data-more]");
    if (!m) return;
    switch (m.dataset.more) {
      // a modal over the chart, not a sheet: it is the header's own dialog,
      // so the sheet gets out of its way first
      case "settings": return act(el("settingsBtn"));
      case "layout": return openSheet("layout");
      case "account": return openSheet("account");
      case "shotFull": return act(el("shotMenu").querySelector('[data-shot="full"]'));
      case "shotRegion": return act(el("shotMenu").querySelector('[data-shot="region"]'));
      case "chat": return act(el("chatToggle"));
      case "scene": return act(el("sceneClear"));
    }
  });

  /* ── the bar ─────────────────────────────────────────────────────────────
   * Left of the spacer: what the CHART is showing — instrument, interval,
   * indicators, drawings. Right of it: the panels beside the chart, then
   * More. The widget bar those two proxy is display:none at this width, so
   * this is the only way to reach a panel on a phone — and, as everywhere
   * else in this file, each one CLICKS the real button rather than owning a
   * state of its own. Read off #wbar, so a widget added there arrives here
   * with no edit to this file. */
  const widgetBtns = [...document.querySelectorAll("#wbar [data-widget]")];
  bar.innerHTML =
    '<button type="button" class="mbtn" data-slot="symbol" id="mbSymbol"></button>' +
    '<span class="msep"></span>' +
    '<button type="button" class="mbtn" data-slot="interval" id="mbInterval"></button>' +
    '<button type="button" class="mbtn" data-slot="indicators" aria-label="Indicators">' +
      Icons.svg("indicators") + "</button>" +
    '<button type="button" class="mbtn" data-slot="drawings" id="mbDraw" aria-label="Drawings">' +
      Icons.svg("pen") + "</button>" +
    '<span class="mspace"></span>' +
    widgetBtns.map((b) => {
      const label = b.querySelector(".tip").textContent;
      return `<button type="button" class="mbtn" data-widget="${b.dataset.widget}" ` +
        `aria-label="${label}">${lift(b, "list")}</button>`;
    }).join("") +
    '<span class="msep"></span>' +
    '<button type="button" class="mbtn" data-slot="more" aria-label="More">' +
      Icons.svg("more") + "</button>";

  bar.addEventListener("click", (e) => {
    // the app closes every dropdown on a document click; the bar's own taps
    // are not that click
    e.stopPropagation();
    // A panel opens in place, so — unlike a sheet — nothing has to get out
    // of the chart's way first: click the real widget-bar button and let
    // js/panels.js apply its own one-panel rule.
    const w = e.target.closest("[data-widget]");
    if (w) { closeSheet(); el(`wb-${w.dataset.widget}`).click(); return syncBar(); }
    const b = e.target.closest("[data-slot]");
    if (b) openSheet(b.dataset.slot);
  });

  /** The bar says what the chart is showing — read off the controls it
   *  proxies, never off a copy of their state. Written only when a value
   *  actually changed: this runs on every class flip in the interval strip,
   *  and rewriting the instrument's <img> each time would flicker it. */
  let shown = {};
  function syncBar() {
    const sym = (el("symbolName").textContent || "").trim();
    // `Universe` is a top-level const in another classic script: a lexical
    // global, which is NOT a property of window — so it is reached by name.
    const logo = (typeof Universe !== "undefined" && Universe.logo(sym)) || null;
    if (shown.sym !== sym || shown.logo !== logo) {
      shown.sym = sym; shown.logo = logo;
      el("mbSymbol").innerHTML =
        (logo ? `<img class="co-logo" src="${logo}" alt="" onerror="this.remove()"/>`
              : Icons.svg("search")) + `<span>${sym}</span>`;
    }
    // `.on` — the one class every menu in this app marks its current row
    // with. The interval list used to say `.active`; see markInterval().
    const iv = el("intervalMenu").querySelector("button.on");
    const ivl = iv ? iv.dataset.short : "—";
    if (shown.iv !== ivl) {
      shown.iv = ivl;
      el("mbInterval").innerHTML = `<span>${ivl}</span>`;
    }
    // a drawing tool is ARMED when the rail's cursor button is not active —
    // the one place that fact lives
    el("mbDraw").classList.toggle("armed",
      !el("tool-cursor").classList.contains("active"));

    for (const b of bar.querySelectorAll("[data-slot]")) {
      b.classList.toggle("on", !!openId && b.dataset.slot === openId);
    }
    // …and a panel button reads its state off the widget-bar button it
    // proxies, including the bell's "something fired" dot: at this width the
    // bar is hidden, so this row is the only place that mark can show.
    for (const b of bar.querySelectorAll("[data-widget]")) {
      const real = el(`wb-${b.dataset.widget}`);
      if (!real) continue;
      b.classList.toggle("armed", real.classList.contains("active"));
      b.classList.toggle("has-new", real.classList.contains("has-new"));
    }
  }

  // The bar tracks the app rather than being told by it: the interval moves
  // from the chat, a pane selection or the header, and the armed tool from a
  // keyboard Escape or a finished drawing. One observer over the two
  // elements carrying those facts beats four call sites that must remember.
  if (window.MutationObserver) {
    const mo = new MutationObserver(syncBar);
    mo.observe(el("intervalMenu"),
               { subtree: true, attributes: true, attributeFilter: ["class"] });
    mo.observe(el("tool-cursor"), { attributes: true, attributeFilter: ["class"] });
  }
  document.addEventListener("charto:pane-active", syncBar);
  // the instrument's mark lands after its own fetch; repaint once it is known
  if (typeof Universe !== "undefined") Universe.load().then(syncBar);
  syncBar();
})();
