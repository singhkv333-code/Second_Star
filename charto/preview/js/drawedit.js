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
  let pinned = false;     // true once the user has dragged it — stops auto-follow

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

  /* The gear: a per-DRAWING settings panel, the way Groww and TradingView open
   * one. Not the chart-settings dialog (that styles the candles, not the shape
   * you have selected) — it collects this shape's own colour, width and line
   * style in one card, plus "Edit text" for a note. Every control writes
   * through the same setStyle the inline buttons do, so the two never disagree. */
  function settingsPop(trigger, info) {
    const curW = info.style.width || 2;
    const curDash = (info.style.dash || []).join(",");
    const swatches = `<div class="de-set-sec"><div class="de-set-lab">Colour</div>`
      + `<div class="de-swatches">` + SWATCHES.map((s, i) =>
        `<button type="button" class="de-swatch" data-color="${i}" title="${s.name}"`
        + ` style="--sw:${s.css || s.val}"></button>`).join("") + `</div></div>`;
    const widths = `<div class="de-set-sec"><div class="de-set-lab">Thickness</div>`
      + `<div class="de-set-row">` + WIDTHS.map((w) =>
        `<button type="button" class="de-chip${w === curW ? " on" : ""}" data-width="${w}">`
        + `<span class="de-wline" style="height:${w}px;width:20px"></span></button>`).join("")
      + `</div></div>`;
    const styles = `<div class="de-set-sec"><div class="de-set-lab">Line</div>`
      + `<div class="de-set-row">` + STYLES.map((s, i) => {
        const on = s.dash.join(",") === curDash;
        const stroke = s.dash.length ? `stroke-dasharray:${s.dash.join(" ")}` : "";
        return `<button type="button" class="de-chip${on ? " on" : ""}" data-style="${i}">`
          + `<svg viewBox="0 0 30 8" class="de-sline" aria-hidden="true">`
          + `<line x1="1" y1="4" x2="29" y2="4" style="${stroke}"/></svg></button>`;
      }).join("") + `</div></div>`;
    const textRow = info.isText
      ? `<div class="de-set-sec"><button type="button" class="de-mrow" data-edit-text>`
        + `${svg("pen", "sm")} Edit text</button></div>` : "";
    const el = openPopover(trigger, swatches + widths + styles + textRow, "de-setpop");
    if (!el) return;
    el.addEventListener("click", (e) => {
      const sw = e.target.closest("[data-color]");
      if (sw) { draw().setStyle(curId, { color: SWATCHES[Number(sw.dataset.color)].val }); return refresh(); }
      const w = e.target.closest("[data-width]");
      if (w) { draw().setStyle(curId, { width: Number(w.dataset.width) });
               markOn(el, "[data-width]", w); return refresh(); }
      const st = e.target.closest("[data-style]");
      if (st) { draw().setStyle(curId, { dash: STYLES[Number(st.dataset.style)].dash });
                markOn(el, "[data-style]", st); return refresh(); }
      if (e.target.closest("[data-edit-text]")) { closePop(); draw().editText(curId); }
    });
  }
  /** Move the `.on` mark to the clicked chip within a group, so the panel
   *  reflects the choice without a full rebuild. */
  function markOn(root, sel, chosen) {
    for (const n of root.querySelectorAll(sel)) n.classList.toggle("on", n === chosen);
  }

  /* ── the strip ───────────────────────────────────────────────────────────
   * A floating pill, mounted on <body> and positioned fixed, the way
   * TradingView and Groww draw theirs — it hovers just above the selected
   * drawing and can be dragged anywhere by the grip at its head. Mounting on
   * the body (not inside a pane) is what lets it float over a secondary pane in
   * a split and be dragged off the chart's own box without being clipped. */
  function build() {
    bar = document.createElement("div");
    bar.className = "draw-edit";
    bar.setAttribute("role", "toolbar");
    bar.hidden = true;
    // Buttons are wired by data-act, delegated — one listener, and a row added
    // later cannot forget to bind itself.
    bar.innerHTML =
      `<span class="de-grip" data-grip title="Drag to move">${svg("grip", "sm")}</span>`
      + `<button type="button" class="de-btn de-color" data-act="color" title="Colour">`
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
    document.body.appendChild(bar);
    bar.addEventListener("pointerdown", (e) => {
      // a press anywhere on the strip is the strip's — never a chart pan
      e.stopPropagation();
      if (e.target.closest("[data-grip]")) beginDrag(e);
    });
    bar.addEventListener("click", onClick);
    return bar;
  }

  /* ── drag to reposition ──────────────────────────────────────────────────
   * Grab the grip and the pill follows the pointer. Once moved by hand it
   * stays where it was put (pinned=true) rather than snapping back over the
   * shape on the next chart move — exactly TradingView's behaviour. */
  function beginDrag(e) {
    if (!bar) return;
    e.preventDefault();
    closePop();
    const r = bar.getBoundingClientRect();
    const offX = e.clientX - r.left, offY = e.clientY - r.top;
    pinned = true;
    bar.classList.add("dragging");
    const move = (ev) => {
      const x = Math.max(6, Math.min(ev.clientX - offX, innerWidth - bar.offsetWidth - 6));
      const y = Math.max(6, Math.min(ev.clientY - offY, innerHeight - bar.offsetHeight - 6));
      bar.style.left = x + "px";
      bar.style.top = y + "px";
      bar.style.transform = "none";
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      bar.classList.remove("dragging");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
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
      case "settings": return settingsPop(b, info);
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

  /** Float the strip just above the selected shape, the way TradingView does —
   *  centred on the shape's width, a margin above its top, clamped to the
   *  viewport. Falls back to the top-centre of the chart the shape is on when
   *  the shape has no on-screen point (scrolled out of range). Does nothing
   *  once the user has dragged the strip by hand (pinned). */
  const GAP = 12;          // px above the shape's top edge
  function place() {
    if (!bar || bar.hidden || pinned) return;
    const d = draw();
    const box = d && d.screenBox && d.screenBox(curId);
    const w = bar.offsetWidth || 300, h = bar.offsetHeight || 40;
    let cx, top;
    if (box) {
      cx = box.cx;
      top = box.top - GAP - h;
      // no room above the shape → sit just below it instead
      if (top < 6) top = Math.min(box.bottom + GAP, innerHeight - h - 6);
    } else {
      // fall back to the top-centre of the chart the shape lives on
      const host = (d && d.hostEl) ? (d.hostEl.closest(".subchart") || stageEl()) : stageEl();
      const r = host ? host.getBoundingClientRect() : { left: 0, width: innerWidth, top: 0 };
      cx = r.left + r.width / 2;
      top = r.top + 14;
    }
    let left = cx - w / 2;
    left = Math.max(6, Math.min(left, innerWidth - w - 6));
    top = Math.max(6, Math.min(top, innerHeight - h - 6));
    bar.style.left = Math.round(left) + "px";
    bar.style.top = Math.round(top) + "px";
    bar.style.transform = "none";
  }

  function show(id, paneId) {
    if (!bar) build();
    curId = id;
    curPaneId = paneId || null;
    pinned = false;              // a fresh selection re-anchors to its shape
    bar.hidden = false;
    refresh();
    place();
    startFollow();
  }
  function hide() {
    closePop();
    stopFollow();
    curId = null;
    curPaneId = null;
    pinned = false;
    if (bar) bar.hidden = true;
  }

  /* The shape moves under the toolbar on every pan, zoom and drag, so the
   * toolbar has to re-place itself as often. A rAF loop while a shape is
   * selected is cheap (one getBoundingClientRect + a couple of coordinate
   * projections) and needs no hook into the chart library's own redraw. */
  let followRaf = 0;
  function tick() {
    if (!curId) return;
    place();
    followRaf = requestAnimationFrame(tick);
  }
  function startFollow() { if (!followRaf) followRaf = requestAnimationFrame(tick); }
  function stopFollow() { if (followRaf) { cancelAnimationFrame(followRaf); followRaf = 0; } }

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
