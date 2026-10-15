/* Charto preview — the phone's workspace, one screen at a time.
 *
 * On a portrait phone the desktop workspace (a chart, the conversation and a
 * set of tiled widgets) cannot be shown side by side, and stacking it gave a
 * third of a chart over half a chat. So the phone shows ONE page at a time:
 * the chart, the chat, then each open widget, and a bar along the bottom
 * names the page and moves between them:
 *
 *     ‹   [icon] Watchlist  3 / 5   + Add   ›
 *
 * The arrows (or a swipe along the bar) change the whole screen; Add opens
 * the widget hub, and the widget it adds becomes the page.
 *
 * THE RULE, as in mobile.js: this file owns no workspace state. A widget page
 * is the dock's own phone sheet with that widget active (Dock.reveal), the
 * chart page is the dock with the sheet put away, and the chat page is the
 * real chat panel. The pages are read from Dock.opened() every time, so the
 * bar cannot list a widget the workspace does not have. What this file adds
 * is which of the three is on screen (body.pg-chart / pg-chat / pg-widget),
 * and the stylesheet does the hiding.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined" || !Dock.opened) return;
  const el = (id) => document.getElementById(id);
  const main = document.querySelector(".main");
  const chatPanel = el("chatPanel");
  const chatToggle = el("chatToggle");
  if (!main || !chatPanel) return;

  const mq = matchMedia("(max-width: 560px) and (orientation: portrait)");
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const KEY = "charto:pager:page";
  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const arrow = (d) => `<svg viewBox="0 0 24 24" class="icon" aria-hidden="true"><path d="${d}" fill="none" ` +
    `stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

  let page = (() => { try { return sessionStorage.getItem(KEY) || "chart"; } catch { return "chart"; } })();
  let applying = false;

  const bar = document.createElement("nav");
  bar.className = "pgbar";
  bar.setAttribute("aria-label", "Workspace pages");
  bar.innerHTML =
    `<button type="button" class="pg-arrow" data-dir="-1" aria-label="Previous page">${arrow("M15 5l-7 7 7 7")}</button>` +
    `<div class="pg-name" aria-live="polite"><span class="pg-ico"></span><span class="pg-t"></span><span class="pg-n"></span></div>` +
    `<button type="button" class="pg-add" aria-label="Add a widget">${Icons.svg("plus", "sm")}<span>Add</span></button>` +
    `<button type="button" class="pg-arrow" data-dir="1" aria-label="Next page">${arrow("M9 5l7 7-7 7")}</button>`;
  main.after(bar);

  /** The pages, in order: chart, chat, then the open widgets. */
  function pages() {
    const sym = (() => { try { return Dock.symbolOf("main") || ""; } catch { return ""; } })();
    const ws = Dock.opened().filter((w) => w.id !== "main");
    return [
      { key: "chart", title: sym ? `Chart · ${sym}` : "Chart", icon: "candles" },
      { key: "chat", title: "Chat", icon: "chat" },
      ...ws.map((w) => ({ key: w.id, title: w.title || "Widget", icon: w.icon || "widgets" })),
    ];
  }

  const kind = (key) => key === "chart" || key === "chat" ? key : "widget";
  const chatOpen = () => !chatPanel.classList.contains("hidden");

  function save() { try { sessionStorage.setItem(KEY, page); } catch { /* private mode */ } }

  /** The chart and the widgets measure themselves on resize; a page that was
   *  display:none has to be told it has a size again. */
  function nudge() {
    requestAnimationFrame(() => {
      window.dispatchEvent(new Event("resize"));
      document.dispatchEvent(new CustomEvent("charto:viewport"));
    });
  }

  function slide(dir) {
    if (!dir || reduced.matches) return;
    main.animate(
      [{ transform: `translateX(${dir * 36}px)`, opacity: 0.35 }, { transform: "none", opacity: 1 }],
      { duration: 280, easing: "cubic-bezier(.22,1,.36,1)" },
    );
  }

  /** Put a page on screen. */
  function go(key, dir = 0) {
    const list = pages();
    if (!list.some((p) => p.key === key)) key = "chart";
    page = key;
    save();
    applying = true;
    try {
      if (kind(key) === "widget") Dock.reveal(key);
      else if (Dock.hideSheet) Dock.hideSheet();
      if (key === "chat" && !chatOpen() && chatToggle) chatToggle.click();
    } finally {
      applying = false;
    }
    paint();
    slide(dir);
    nudge();
  }

  function step(dir) {
    Dock.hub(false);
    const list = pages();
    const i = Math.max(0, list.findIndex((p) => p.key === page));
    go(list[(i + dir + list.length) % list.length].key, dir);
  }

  function paint() {
    const on = mq.matches;
    const k = kind(page);
    document.body.classList.toggle("pg-on", on);
    document.body.classList.toggle("pg-chart", on && k === "chart");
    document.body.classList.toggle("pg-chat", on && k === "chat");
    document.body.classList.toggle("pg-widget", on && k === "widget");
    if (!on) return;
    const list = pages();
    const i = Math.max(0, list.findIndex((p) => p.key === page));
    const p = list[i];
    bar.querySelector(".pg-ico").innerHTML = Icons.svg(p.icon, "sm");
    bar.querySelector(".pg-t").textContent = p.title;
    bar.querySelector(".pg-n").textContent = `${i + 1} / ${list.length}`;
    bar.querySelector(".pg-name").setAttribute("aria-label", `${esc(p.title)}, page ${i + 1} of ${list.length}`);
  }

  // The workspace changed under us: a widget was added from the hub or by the
  // chat, or the one on screen was closed.
  document.addEventListener("charto:dock-layout", () => {
    if (applying || !mq.matches) return;
    const vis = Dock.opened().filter((w) => w.id !== "main" && w.visible);
    const k = kind(page);
    // a widget just added from the hub is the page now, and the hub has done its job
    if (k === "chart" && vis.length) { page = vis[0].id; save(); Dock.hub(false); paint(); slide(1); return; }
    // Opening a widget puts the conversation away (dock.js phoneMakeRoom):
    // the chat page follows it to the widget rather than going blank.
    if (k === "chat" && !chatOpen()) { page = vis.length ? vis[0].id : "chart"; save(); paint(); slide(1); nudge(); return; }
    if (k === "widget" && !vis.some((w) => w.id === page)) {
      page = vis.length ? vis[0].id : "chart"; save(); paint(); nudge(); return;
    }
    paint();
  });

  bar.addEventListener("click", (e) => {
    const a = e.target.closest(".pg-arrow");
    if (a) return step(Number(a.dataset.dir));
    if (e.target.closest(".pg-add")) {
      // the hub lives in the workspace canvas, which the chat page hides
      if (kind(page) === "chat") go("chart", -1);
      Dock.hub(true);
    }
  });

  // A sideways swipe along the bar turns the page. Only the bar: the chart and
  // the widgets have their own horizontal gestures.
  let sx = null, sy = null;
  bar.addEventListener("touchstart", (e) => { const t = e.touches[0]; sx = t.clientX; sy = t.clientY; }, { passive: true });
  bar.addEventListener("touchend", (e) => {
    if (sx == null) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - sx, dy = t.clientY - sy;
    sx = sy = null;
    if (Math.abs(dx) > 40 && Math.abs(dx) > Math.abs(dy) * 1.5) step(dx < 0 ? 1 : -1);
  });

  const sync = () => (mq.matches ? go(page) : paint());
  mq.addEventListener("change", sync);
  // after Dock.start() and the chat have settled their first layout
  requestAnimationFrame(() => setTimeout(sync, 0));
})();
