/* Charto preview — the drawing edit toolbar.
 *
 * TradingView's floating strip: select a line, a box, a fib or a text note and
 * a small toolbar appears with the handful of edits you reach for constantly —
 * colour, line width, line style — beside the actions the right-click menu
 * already owns (duplicate, lock, settings, delete, layer order). The menu is
 * still there for the long tail; this is the fast path for the three or four
 * things you change on almost every shape.
 *
 * ── why a toolbar as well as the menu ──────────────────────────────────────
 * A right-click menu is a MODE: it opens, you pick one thing, it closes, and
 * changing a colour then a width is two full trips. Restyling a drawing is
 * iterative — nudge the width, try a dash, pick a different red — and a strip
 * that stays up while you do all three is the difference between it feeling
 * like editing and feeling like a form. So the two coexist, addressing the
 * same drawings API (js/drawings.js): setStyle for colour/width/dash, and the
 * same clone / setLocked / moveLayer / remove / editText the menu calls, so
 * there is never a second definition of what "duplicate" or "lock" does.
 *
 * ── how it knows what is selected ──────────────────────────────────────────
 * It listens for `charto:draw-select`, the one event drawings.js fires on
 * every selection change (and clears on deselect). No polling, no second hit
 * test — the chart has already decided what "this" is.
 *
 * ── where it sits ──────────────────────────────────────────────────────────
 * Pinned to the top-centre of the chart stage, the way openmarket and (near
 * enough) TradingView place it. Anchoring it to the shape's own bounding box
 * would mean projecting the drawing's data-space points to screen on every pan
 * and zoom; the top strip is always on screen, never under the pointer that is
 * dragging the shape, and never off the edge for a drawing near the axis.
 */
"use strict";

const DrawEdit = (() => {
  // A compact, chart-legible palette — TradingView's spread, not the OS picker.
  // The first is "chart accent"; the rest are the colours people actually
  // reach for on candles, each readable in both themes.
  const SWATCHES = [
    { name: "Accent", css: "var(--primary)", val: null },   // null → the theme default
    { name: "Red", val: "#f23645" },
    { name: "Orange", val: "#ff9800" },
    { name: "Yellow", val: "#ffd60a" },
    { name: "Green", val: "#22ab94" },
    { name: "Teal", val: "#089981" },
    { name: "Blue", val: "#2962ff" },
    { name: "Purple", val: "#9c27b0" },
    { name: "Pink", val: "#e040fb" },
    { name: "White", val: "#ffffff" },
    { name: "Grey", val: "#787b86" },
    { name: "Black", val: "#131722" },
  ];
  const WIDTHS = [1, 2, 3, 4];
  const STYLES = [
    { name: "Solid", dash: [] },
    { name: "Dashed", dash: [6, 4] },
    { name: "Dotted", dash: [2, 3] },
  ];

  let bar = null;         // the toolbar element, built once
  let curId = null;       // the drawing it is currently editing
  let curPaneId = null;   // which runtime owns it: null = primary, "sub-n" = a pane
  let openPop = null;     // an open sub-popover (colour / width / style), or null

  // The runtime that owns the selected drawing. The primary is on __charto;
  // a secondary pane's is resolved from the paneId the select event carried,
  // through Panes' registry — so a toolbar edit lands on the right chart.
  function draw() {
    const c = window.__charto;
    if (!c) return null;
    if (curPaneId && c.panes && c.panes.drawByPaneId) {
      return c.panes.drawByPaneId(curPaneId) || c.draw;
    }
    return c.draw;
  }
  const stageEl = () => document.getElementById("stage");

  const svg = (n, c) => Icons.svg(n, c);

  /* ── the shape being edited ──────────────────────────────────────────────
   * The select event carries an id; everything else (its style, whether it is
   * locked, whether its words can be edited) is asked of the drawings module,
   * so the toolbar and the chart can never disagree about the same shape. */
  function shapeInfo(id) {
    const d = draw();
    if (!d) return null;
    const rec = d.state.drawings.find((q) => q.id === id);
    if (!rec) return null;
    const style = d.styleOf(id) || { color: null, width: null, dash: [] };
    // `val` is the raw hex the shape carries, or null when it is on the theme
    // accent (rec.color unset). The swatch popover marks the current one from
    // this, so it must be the stored value, not the resolved theme colour.
    style.val = rec.color || null;
    return { id, style, locked: !!rec.locked, isText: d.isText(id) };
  }

  /* ── the three sub-popovers ──────────────────────────────────────────────
   * Each opens under its trigger, replaces any other, and closes on the next
   * outside press. They are tiny — a row of swatches, four widths, three
   * styles — so they are rebuilt on open rather than kept around. */
  function closePop() {
    if (openPop) { openPop.el.remove(); openPop = null; }
    document.removeEventListener("pointerdown", onPopOutside, true);
  }
  function onPopOutside(e) {
    if (openPop && !openPop.el.contains(e.target) && !openPop.trigger.contains(e.target)) {
      closePop();
    }
  }
  function openPopover(trigger, html, cls) {
    const same = openPop && openPop.trigger === trigger;
    closePop();
    if (same) return null;
    const el = document.createElement("div");
    el.className = `de-pop dropdown open${cls ? " " + cls : ""}`;
    el.innerHTML = html;
    document.body.appendChild(el);
    const r = trigger.getBoundingClientRect();
    el.style.position = "fixed";
    el.style.top = Math.round(r.bottom + 6) + "px";
    // keep it on screen: clamp the left edge to the viewport
    const w = el.offsetWidth;
    el.style.left = Math.round(Math.max(8, Math.min(r.left, innerWidth - w - 8))) + "px";
    if (Ctx && Ctx.glass) try { Ctx.glass(el); } catch {}
    openPop = { el, trigger };
    document.addEventListener("pointerdown", onPopOutside, true);
    return el;
  }

  function colorPop(trigger, info) {
    const cur = info.style.val;
    const html = `<div class="de-swatches">` + SWATCHES.map((s, i) =>
      `<button type="button" class="de-swatch" data-i="${i}" title="${s.name}"
        style="--sw:${s.css || s.val}"></button>`).join("") + `</div>`;
    const el = openPopover(trigger, html, "de-colorpop");
    if (!el) return;
    el.addEventListener("click", (e) => {
      const b = e.target.closest(".de-swatch");
      if (!b) return;
      const s = SWATCHES[Number(b.dataset.i)];
      draw().setStyle(curId, { color: s.val });
      closePop();
      refresh();
    });
  }

  function widthPop(trigger, info) {
    const cur = info.style.width || 2;
    const html = WIDTHS.map((w) =>
      `<button type="button" class="de-wrow${w === cur ? " on" : ""}" data-w="${w}">`
      + `<span class="de-wline" style="height:${w}px"></span>`
      + `<span class="de-wlabel">${w}px</span></button>`).join("");
    const el = openPopover(trigger, html, "de-widthpop");
    if (!el) return;
    el.addEventListener("click", (e) => {
      const b = e.target.closest("[data-w]");
      if (!b) return;
      draw().setStyle(curId, { width: Number(b.dataset.w) });
      closePop();
      refresh();
    });
  }

  function stylePop(trigger, info) {
    const curDash = (info.style.dash || []).join(",");
    const html = STYLES.map((s, i) => {
      const on = s.dash.join(",") === curDash;
      const stroke = s.dash.length ? `stroke-dasharray:${s.dash.join(" ")}` : "";
      return `<button type="button" class="de-srow${on ? " on" : ""}" data-i="${i}">`
        + `<svg viewBox="0 0 40 8" class="de-sline" aria-hidden="true">`
        + `<line x1="1" y1="4" x2="39" y2="4" style="${stroke}"/></svg>`
        + `<span class="de-slabel">${s.name}</span></button>`;
    }).join("");
    const el = openPopover(trigger, html, "de-stylepop");
    if (!el) return;
    el.addEventListener("click", (e) => {
      const b = e.target.closest("[data-i]");
      if (!b) return;
      draw().setStyle(curId, { dash: STYLES[Number(b.dataset.i)].dash });
      closePop();
      refresh();
    });
  }

  /* ── the strip ───────────────────────────────────────────────────────── */
  function build() {
    bar = document.createElement("div");
    bar.className = "draw-edit";
    bar.setAttribute("role", "toolbar");
    bar.hidden = true;
    // Buttons are wired by data-act, delegated — one listener, and a row added
    // later cannot forget to bind itself.
    bar.innerHTML =
      `<button type="button" class="de-btn de-color" data-act="color" title="Colour">`
      + `<span class="de-dot"></span></button>`
      + `<button type="button" class="de-btn de-width" data-act="width" title="Line width">`
      + `<span class="de-wpreview"></span>${svg("chevronDown", "xs")}</button>`
      + `<button type="button" class="de-btn de-style" data-act="style" title="Line style">`
      + `<svg viewBox="0 0 24 8" class="de-spreview" aria-hidden="true"><line x1="2" y1="4" x2="22" y2="4"/></svg>`
      + `${svg("chevronDown", "xs")}</button>`
      + `<span class="de-sep"></span>`
      + `<button type="button" class="de-btn de-text" data-act="text" title="Edit text" hidden>${svg("pen", "sm")}</button>`
      + `<button type="button" class="de-btn" data-act="clone" title="Duplicate">${svg("copy", "sm")}</button>`
      + `<button type="button" class="de-btn de-lock" data-act="lock" title="Lock">${svg("lock", "sm")}</button>`
      + `<button type="button" class="de-btn" data-act="settings" title="Settings">${svg("settings", "sm")}</button>`
      + `<button type="button" class="de-btn" data-act="more" title="More">${svg("more", "sm")}</button>`
      + `<span class="de-sep"></span>`
      + `<button type="button" class="de-btn de-danger" data-act="del" title="Delete">${svg("trash", "sm")}</button>`;
    stageEl().appendChild(bar);
    bar.addEventListener("pointerdown", (e) => e.stopPropagation());   // never a chart pan
    bar.addEventListener("click", onClick);
    return bar;
  }

  function onClick(e) {
    const b = e.target.closest("[data-act]");
    if (!b || !curId) return;
    e.stopPropagation();
    const d = draw();
    const info = shapeInfo(curId);
    if (!info) return hide();
    switch (b.dataset.act) {
      case "color":  return colorPop(b, info);
      case "width":  return widthPop(b, info);
      case "style":  return stylePop(b, info);
      case "text":   closePop(); return d.editText(curId);
      case "clone":  closePop(); return void d.clone(curId);
      case "lock":   closePop(); d.setLocked(curId, !info.locked); return refresh();
      case "settings": closePop(); return ChartSettings.open();
      case "more":   return morePop(b, info);
      case "del":    closePop(); return void d.remove(curId);
    }
  }

  /* Layer order lives behind "More" — the same four moves the right-click menu
   * offers, calling the same moveLayer, so a shape's z-order has one meaning. */
  function morePop(trigger, info) {
    const d = draw();
    const rows = [
      { label: "Bring to front", where: "front" },
      { label: "Bring forward", where: "forward" },
      { label: "Send backward", where: "backward" },
      { label: "Send to back", where: "back" },
    ];
    const html = rows.map((r) =>
      `<button type="button" class="de-mrow" data-where="${r.where}">${r.label}</button>`).join("");
    const el = openPopover(trigger, html, "de-morepop");
    if (!el) return;
    el.addEventListener("click", (e) => {
      const b = e.target.closest("[data-where]");
      if (!b) return;
      d.moveLayer(curId, b.dataset.where);
      closePop();
    });
  }

  /* Repaint the strip's own state — the colour dot, the width preview, the
   * lock. Called on select and after any edit, so the strip always shows what
   * the shape currently is. */
  function refresh() {
    if (!bar || !curId) return;
    const info = shapeInfo(curId);
    if (!info) return hide();
    const dot = bar.querySelector(".de-dot");
    if (dot) dot.style.background = info.style.color || "var(--primary)";
    const wp = bar.querySelector(".de-wpreview");
    if (wp) wp.style.height = (info.style.width || 2) + "px";
    const sp = bar.querySelector(".de-spreview line");
    if (sp) {
      const dash = info.style.dash || [];
      sp.style.strokeDasharray = dash.length ? dash.join(" ") : "";
    }
    const lock = bar.querySelector(".de-lock");
    if (lock) {
      lock.classList.toggle("on", info.locked);
      lock.title = info.locked ? "Unlock" : "Lock";
    }
    const textBtn = bar.querySelector(".de-text");
    if (textBtn) textBtn.hidden = !info.isText;
  }

  /** Mount the strip over the pane that owns the selection — #stage for the
   *  primary, the .subchart root for a secondary pane — so it always sits at
   *  the top of the chart being edited, not floating over pane 1. */
  function place() {
    if (!bar) return;
    let host = stageEl();
    const d = draw();
    if (d && d.hostEl) {
      // a sub's stage is the .sub-canvas; its positioned parent is the
      // .subchart root, which is what we want to sit inside
      host = d.hostEl.closest(".subchart") || d.hostEl.parentElement || host;
    }
    if (host && bar.parentElement !== host) host.appendChild(bar);
    bar.style.left = "50%";
    bar.style.transform = "translateX(-50%)";
    bar.style.top = "12px";
  }

  function show(id, paneId) {
    if (!bar) build();
    curId = id;
    curPaneId = paneId || null;
    place();
    refresh();
    bar.hidden = false;
  }
  function hide() {
    closePop();
    curId = null;
    curPaneId = null;
    if (bar) bar.hidden = true;
  }

  document.addEventListener("charto:draw-select", (e) => {
    const id = e.detail && e.detail.id;
    if (id) show(id, e.detail.paneId || null);
    else hide();
  });
  // A layout change tears down and rebuilds the stage's charts; a stale strip
  // pointing at a shape that may be gone should stand down.
  document.addEventListener("charto:panes-changed", hide);

  return { show, hide, refresh };
})();
