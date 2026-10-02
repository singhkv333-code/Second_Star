/* Charto preview — the workspace dock.
 *
 * Widgets live AROUND the chart, never instead of it. There are three docks
 * — a column on the left, a column on the right, a strip along the bottom —
 * and windows that float over the chart. Each dock holds groups; a group is a
 * stack of tabs, one widget per tab. Everything is dragged by its tab: onto
 * another group's tab strip to join it, onto half of a group to split it,
 * onto an edge of the chart to open a new dock there, or over the chart to
 * float. A dock with nothing in it takes no space at all, so the chart only
 * ever gives up the room something is actually using.
 *
 * Why its own engine and not a docking library. The chart is not a panel: it
 * is the fixed centre the workspace is arranged around, and the conversation
 * keeps its own column on the far edge. A general docking manager would make
 * both of them tiles that can be dragged away or closed, and its tab chrome
 * would be a second design language beside this one. The rules here are
 * narrower than a library's on purpose — that is what keeps the chart the
 * subject of the screen and the chat one glance away from it.
 *
 * State is one object, persisted unscoped (a workspace follows the user, not
 * the symbol), and the DOM is DERIVED from it on every change: groups and
 * widget hosts are persistent elements that are only re-parented, never
 * re-rendered, so moving a widget keeps its scroll, its inputs and its
 * timers exactly where they were.
 *
 * A widget is a spec handed to Dock.register():
 *   { type, title, icon, desc, single, zone, linkable, settings[],
 *     host?() — an existing element to adopt (single-instance widgets),
 *     mount(host, ctx) → { show?, hide?, config?, symbol?, ask? } }
 * `ctx` is the widget's handle on the dock: its config, its symbol, the
 * chat, and the chart.
 */
"use strict";

const Dock = (() => {
  const el = (id) => document.getElementById(id);
  const main = document.querySelector(".main");
  const charts = document.querySelector(".charts") || el("stage");
  if (!main || !charts) return { register() {}, start() {} };

  const KEY = "dock";
  const ZONES = ["left", "right", "bottom"];
  const MIN = { left: 230, right: 250, bottom: 150 };
  const DEF = { left: 300, right: 320, bottom: 250 };
  const CENTER_MIN = 380;        // the chart never gets narrower than this
  const FLOAT_DEF = { w: 340, h: 380 };
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const compactMq = matchMedia("(max-width: 820px) and (orientation: portrait), " +
                               "(orientation: landscape) and (max-height: 520px)");

  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const icon = (n, cls = "") => Icons.svg(n, cls);
  const uid = (p) => p + Math.random().toString(36).slice(2, 8);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  /* ══ the page symbol, and opening another one ═══════════════════════════
   * The primary chart IS the page: its symbol is ?symbol=, and every surface
   * on the page — drawings, scene, chat context — is keyed off it. So opening
   * an instrument on the primary chart is a navigation, made to feel instant
   * by three things: the bars are already cached (`warm`, on hover), the
   * new page paints them before it fetches (js/main.js), and the swap is a
   * cross-document view transition rather than a white flash. A SECONDARY
   * pane is a real object with its own symbol, so when one is selected the
   * instrument lands there in place, with no navigation at all. */
  const pageSymbol = () =>
    (new URLSearchParams(location.search).get("symbol") || "RELIANCE").toUpperCase();

  function openSymbol(sym, how) {
    const s = String(sym || "").trim().toUpperCase();
    if (!s) return;
    if (typeof Panes !== "undefined" && Panes.openChart) {
      if (how === "beside") return Panes.openChart(s, (Panes.activeSub() || {}).interval, false);
      const sub = Panes.activeSub && Panes.activeSub();
      if (sub) return Panes.openChart(s, sub.interval, true);
    }
    if (s === pageSymbol()) return;
    persistNow();
    const q = new URLSearchParams(location.search);
    q.set("symbol", s);
    q.delete("panel");
    location.search = q.toString();
  }

  const warmed = new Map();          // symbol → when its bars were asked for
  function warm(sym) {
    const s = String(sym || "").toUpperCase();
    if (!s || s === pageSymbol() || typeof window.__chartoWarm !== "function") return;
    const at = warmed.get(s) || 0;
    if (Date.now() - at < 60_000) return;
    warmed.set(s, Date.now());
    window.__chartoWarm(s);
  }

  /* ══ state ═══════════════════════════════════════════════════════════════ */

  const TYPES = new Map();
  const blank = () => ({
    v: 1, inst: {}, groups: {}, floats: [], closed: [], focus: false,
    zones: { left: { size: DEF.left, groups: [] }, right: { size: DEF.right, groups: [] },
             bottom: { size: DEF.bottom, groups: [] } },
  });
  let S = blank();

  function load() {
    const raw = Store.get(KEY, null);
    if (!raw || raw.v !== 1 || typeof raw !== "object") return blank();
    const s = blank();
    s.focus = !!raw.focus;
    for (const [id, i] of Object.entries(raw.inst || {})) {
      if (i && typeof i.type === "string") s.inst[id] = { type: i.type, cfg: i.cfg || {} };
    }
    for (const [gid, g] of Object.entries(raw.groups || {})) {
      if (!g || !Array.isArray(g.tabs)) continue;
      s.groups[gid] = { tabs: g.tabs.filter((t) => typeof t === "string"),
                        active: g.active, size: Number(g.size) || 1 };
    }
    for (const z of ZONES) {
      const rz = (raw.zones || {})[z] || {};
      s.zones[z].size = Number(rz.size) || DEF[z];
      s.zones[z].groups = (rz.groups || []).filter((g) => s.groups[g]);
    }
    s.floats = (raw.floats || []).filter((f) => f && s.groups[f.gid])
      .map((f) => ({ gid: f.gid, x: +f.x || 40, y: +f.y || 40,
                     w: +f.w || FLOAT_DEF.w, h: +f.h || FLOAT_DEF.h }));
    s.closed = (raw.closed || []).filter((c) => c && s.inst[c.id]).slice(-24);
    return s;
  }

  /** Drop whatever the stored layout names that this build cannot draw — a
   *  widget type that no longer exists, a tab pointing at no instance — so a
   *  stale blob degrades to fewer widgets, never to a broken workspace. */
  function sanitize() {
    for (const [id, i] of Object.entries(S.inst)) if (!TYPES.has(i.type)) delete S.inst[id];
    const placed = new Set();
    for (const [gid, g] of Object.entries(S.groups)) {
      g.tabs = g.tabs.filter((t) => S.inst[t] && !placed.has(t));
      g.tabs.forEach((t) => placed.add(t));
      if (!g.tabs.includes(g.active)) g.active = g.tabs[0];
      if (!g.tabs.length) delete S.groups[gid];
    }
    for (const z of ZONES) S.zones[z].groups = S.zones[z].groups.filter((g) => S.groups[g]);
    S.floats = S.floats.filter((f) => S.groups[f.gid]);
    const inZone = new Set([...ZONES.flatMap((z) => S.zones[z].groups),
                            ...S.floats.map((f) => f.gid)]);
    for (const gid of Object.keys(S.groups)) if (!inZone.has(gid)) delete S.groups[gid];
    S.closed = S.closed.filter((c) => S.inst[c.id] && !placed.has(c.id));
    // an instance that is neither placed nor remembered as closed is garbage
    const kept = new Set([...placed, ...S.closed.map((c) => c.id)]);
    for (const id of Object.keys(S.inst)) if (!kept.has(id)) delete S.inst[id];
  }

  let saveT = 0;
  const persist = () => { clearTimeout(saveT); saveT = setTimeout(persistNow, 160); };
  function persistNow() { clearTimeout(saveT); Store.set(KEY, S); }
  addEventListener("pagehide", persistNow);

  /* ── where things are ──────────────────────────────────────────────────── */

  function whereGroup(gid) {
    for (const z of ZONES) if (S.zones[z].groups.includes(gid)) return { zone: z };
    const f = S.floats.find((x) => x.gid === gid);
    return f ? { zone: "float", float: f } : null;
  }
  const groupOf = (id) => Object.keys(S.groups).find((g) => S.groups[g].tabs.includes(id));
  const instancesOf = (type) => Object.keys(S.inst).filter((id) => S.inst[id].type === type);
  const placedOf = (type) => instancesOf(type).filter((id) => groupOf(id));

  /** Remove an instance from wherever it sits; empty groups and docks fold. */
  function detach(id) {
    const gid = groupOf(id);
    if (!gid) return null;
    const g = S.groups[gid];
    const at = g.tabs.indexOf(id);
    g.tabs.splice(at, 1);
    if (g.active === id) g.active = g.tabs[Math.max(0, at - 1)];
    const where = whereGroup(gid);
    const place = { zone: where && where.zone, gid,
                    rect: where && where.float ? { ...where.float } : null };
    if (!g.tabs.length) dropGroup(gid);
    return place;
  }

  function dropGroup(gid) {
    for (const z of ZONES) S.zones[z].groups = S.zones[z].groups.filter((g) => g !== gid);
    S.floats = S.floats.filter((f) => f.gid !== gid);
    delete S.groups[gid];
    const node = groupEls.get(gid);
    if (node) { node.remove(); groupEls.delete(gid); }
  }

  function newGroup(tabs) {
    const gid = uid("g");
    S.groups[gid] = { tabs: [...tabs], active: tabs[0], size: 1 };
    return gid;
  }

  /** Put instance `id` where a drop (or a menu) said — the one mutation every
   *  route into the layout goes through. */
  function place(id, t) {
    if (t.kind === "tab") {
      const g = S.groups[t.gid];
      if (!g) return place(id, { kind: "zone", zone: "right" });
      const i = clamp(t.index == null ? g.tabs.length : t.index, 0, g.tabs.length);
      g.tabs.splice(i, 0, id);
      g.active = id;
      return;
    }
    if (t.kind === "split") {
      const w = whereGroup(t.gid);
      if (!w || w.zone === "float") return place(id, { kind: "tab", gid: t.gid });
      const list = S.zones[w.zone].groups;
      const gid = newGroup([id]);
      list.splice(list.indexOf(t.gid) + (t.after ? 1 : 0), 0, gid);
      return;
    }
    if (t.kind === "float") {
      const gid = newGroup([id]);
      S.floats.push({ gid, x: t.x, y: t.y, w: t.w || FLOAT_DEF.w, h: t.h || FLOAT_DEF.h });
      return;
    }
    // a dock: join its last group as a tab when it has one (a workspace of
    // tabs reads as one quiet column, not a wall of boxes), else open it
    const z = S.zones[t.zone] ? t.zone : "right";
    const list = S.zones[z].groups;
    if (t.newGroup || !list.length) {
      const gid = newGroup([id]);
      if (t.first) list.unshift(gid); else list.push(gid);
    } else {
      place(id, { kind: "tab", gid: list[list.length - 1] });
    }
  }

  /* ══ DOM: docks, groups, hosts ══════════════════════════════════════════ */

  const zoneEl = {}, handleEl = {};
  const center = document.createElement("div");
  center.className = "dk-center";
  charts.parentNode.insertBefore(center, charts);
  center.appendChild(charts);

  for (const z of ZONES) {
    const n = document.createElement("section");
    n.className = `dk-zone dk-${z}`;
    n.dataset.zone = z;
    n.setAttribute("aria-label", `${z[0].toUpperCase() + z.slice(1)} widgets`);
    zoneEl[z] = n;
    const h = document.createElement("div");
    h.className = `dk-handle ${z === "bottom" ? "h" : "v"}`;
    h.dataset.zone = z;
    h.setAttribute("role", "separator");
    h.tabIndex = 0;
    h.title = "Drag to resize · double-click to reset";
    handleEl[z] = h;
  }
  center.after(handleEl.right, zoneEl.right);
  center.before(zoneEl.left, handleEl.left);
  center.append(handleEl.bottom, zoneEl.bottom);

  const floatLayer = document.createElement("div");
  floatLayer.className = "dk-floats";
  center.appendChild(floatLayer);

  const groupEls = new Map();        // gid → <section>
  const hosts = new Map();           // inst id → host element
  const live = new Map();            // inst id → what mount() returned
  const shown = new Set();           // inst ids currently on screen

  function hostFor(id) {
    let h = hosts.get(id);
    if (h) return h;
    const spec = TYPES.get(S.inst[id].type);
    h = (spec.host && spec.host()) || document.createElement("div");
    h.classList.remove("sidepanel", "hidden");
    h.classList.add("dk-host", `dk-w-${spec.type}`);
    h.dataset.inst = id;
    hosts.set(id, h);
    return h;
  }

  function ensureMounted(id) {
    if (live.has(id)) return live.get(id);
    const spec = TYPES.get(S.inst[id].type);
    const api = (spec.mount && spec.mount(hostFor(id), ctxFor(id))) || {};
    live.set(id, api);
    return api;
  }

  function groupEl(gid) {
    let g = groupEls.get(gid);
    if (g) return g;
    g = document.createElement("section");
    g.className = "dk-group";
    g.dataset.gid = gid;
    g.innerHTML =
      `<div class="dk-head">` +
        `<button type="button" class="dk-grip" data-act="grip" title="Drag to move" ` +
          `aria-label="Move group">${icon("grip")}</button>` +
        `<div class="dk-tabs" role="tablist"></div>` +
        `<div class="dk-acts"></div>` +
      `</div><div class="dk-body"></div>` +
      `<div class="dk-rz" data-act="resize" aria-hidden="true"></div>`;
    groupEls.set(gid, g);
    return g;
  }

  const label = (id) => {
    const spec = TYPES.get(S.inst[id].type);
    const sub = titles.get(id);
    return { title: spec.title, sub: sub || "" };
  };
  const titles = new Map();          // inst id → short suffix (a symbol, a preset)

  function paintHead(gid) {
    const g = S.groups[gid], node = groupEl(gid);
    const tabs = node.querySelector(".dk-tabs");
    const act = S.inst[g.active];
    const spec = act && TYPES.get(act.type);
    tabs.innerHTML = g.tabs.map((id) => {
      const s = TYPES.get(S.inst[id].type), l = label(id), on = id === g.active;
      return `<button type="button" class="dk-tab${on ? " on" : ""}" role="tab" ` +
        `aria-selected="${on}" data-inst="${id}" title="${esc(l.title + (l.sub ? " · " + l.sub : ""))}">` +
        `${icon(s.icon)}<span class="t">${esc(l.title)}</span>` +
        (l.sub ? `<span class="s">${esc(l.sub)}</span>` : "") +
        (g.tabs.length > 1 ? `<span class="x" data-act="close-tab" role="button" ` +
          `aria-label="Close ${esc(l.title)}">${icon("x")}</span>` : "") +
        `</button>`;
    }).join("") + `<span class="dk-ink" aria-hidden="true"></span>`;
    const where = whereGroup(gid);
    const btn = (a, ic, t, extra = "") =>
      `<button type="button" class="dk-act" data-act="${a}" title="${t}" aria-label="${t}" ${extra}>${icon(ic)}</button>`;
    const linked = spec && spec.linkable;
    const cfg = act ? act.cfg : {};
    node.querySelector(".dk-acts").innerHTML =
      (linked ? btn("link", "link", cfg.pin ? `Pinned to ${esc(cfg.pin)} — click to change`
                                            : "Following the chart — click to change",
                    cfg.pin ? 'data-pinned="1"' : "") : "") +
      (spec && spec.settings && spec.settings.length ? btn("settings", "settings", "Widget settings") : "") +
      btn("more", "more", "More") +
      (where && where.zone !== "float" ? btn("max", maxed === gid ? "shrink" : "expand",
                                             maxed === gid ? "Restore" : "Maximize") : "") +
      btn("close", "x", "Close");
    node.classList.toggle("is-float", where && where.zone === "float");
    requestAnimationFrame(() => paintInk(node));
  }

  /** The active tab's underline is ONE element that slides between tabs, so
   *  switching reads as motion from where you were, not a flash elsewhere. */
  function paintInk(node) {
    const on = node.querySelector(".dk-tab.on"), ink = node.querySelector(".dk-ink");
    if (!on || !ink) return;
    ink.style.width = on.offsetWidth - 16 + "px";
    ink.style.transform = `translateX(${on.offsetLeft + 8}px)`;
  }

  /* ══ layout ══════════════════════════════════════════════════════════════ */

  let compact = compactMq.matches;
  let maxed = null;                  // gid of the maximised group, if any

  function viewModel() {
    // On a phone there is room for the chart and ONE widget, so every open
    // widget becomes a tab of a single sheet. The stored layout is untouched:
    // turning the phone back, or the next desktop visit, gets it all back.
    if (!compact) return { zones: S.zones, floats: S.floats, groups: S.groups };
    const all = [...ZONES.flatMap((z) => S.zones[z].groups), ...S.floats.map((f) => f.gid)]
      .flatMap((gid) => S.groups[gid].tabs);
    // ...and the sheet is out only when it was asked for this visit: a phone
    // opens on the chart and the conversation, never on yesterday's widgets
    if (!all.length || !compactSheet.shown) return { zones: { left: { groups: [] }, right: { groups: [] }, bottom: { groups: [] } },
                              floats: [], groups: {} };
    const act = Object.values(S.groups).map((g) => g.active).find((a) => all.includes(a)) || all[0];
    const sheet = compactSheet.active && all.includes(compactSheet.active) ? compactSheet.active : act;
    compactSheet.active = sheet;
    return { zones: { left: { groups: [] }, bottom: { groups: [] },
                      right: { size: S.zones.right.size, groups: ["sheet"] } },
             floats: [], groups: { sheet: { tabs: all, active: sheet, size: 1 } } };
  }
  const compactSheet = { active: null, shown: false };

  function layout(animate) {
    sanitize();
    const before = animate ? snapshot() : null;
    const vm = viewModel();
    const groupsBefore = S.groups;
    if (compact) S.groups = { ...S.groups, ...vm.groups };   // paintHead reads S.groups
    const visibleNow = new Set();

    for (const z of ZONES) {
      const zone = zoneEl[z], list = (vm.zones[z] || {}).groups || [];
      const kids = [];
      list.forEach((gid, i) => {
        if (i) kids.push(splitEl(z, list[i - 1], gid));
        const node = groupEl(gid);
        node.style.flex = `${S.groups[gid].size || 1} 1 0`;
        node.style.left = node.style.top = node.style.width = node.style.height = "";
        kids.push(node);
      });
      // re-parent without churn: only move what is not already in order
      kids.forEach((k, i) => { if (zone.children[i] !== k) zone.insertBefore(k, zone.children[i] || null); });
      while (zone.children.length > kids.length) zone.lastElementChild.remove();
      const on = list.length > 0 && !S.focus;
      zone.classList.toggle("on", on);
      handleEl[z].classList.toggle("on", on && !compact);
    }

    // floats, in z-order (the array order is the stacking order)
    const fl = vm.floats;
    fl.forEach((f) => {
      const node = groupEl(f.gid);
      if (node.parentNode !== floatLayer) floatLayer.appendChild(node);
      node.style.flex = "";
    });
    fl.forEach((f) => floatLayer.appendChild(groupEl(f.gid)));
    [...floatLayer.children].forEach((n) => { if (!fl.some((f) => f.gid === n.dataset.gid)) n.remove(); });
    floatLayer.classList.toggle("on", fl.length > 0 && !S.focus);
    placeFloats();

    // bodies: the active tab's host is in its group; the others are parked
    for (const gid of Object.keys(vm.groups)) {
      const g = S.groups[gid], node = groupEl(gid), body = node.querySelector(".dk-body");
      for (const id of g.tabs) {
        const h = hostFor(id);
        if (h.parentNode !== body) body.appendChild(h);
        const on = id === g.active;
        h.hidden = !on;
        if (on && !S.focus) visibleNow.add(id);
      }
      paintHead(gid);
    }
    // a host whose instance was closed leaves the page (kept, not destroyed)
    for (const [id, h] of hosts) {
      if (!Object.keys(vm.groups).some((gid) => S.groups[gid].tabs.includes(id))) {
        if (h.parentNode) h.parentNode.removeChild(h);
      }
    }
    if (compact) S.groups = groupsBefore;

    document.body.classList.toggle("dk-focus", !!S.focus);
    document.body.classList.toggle("dk-compact", compact);
    syncVisibility(visibleNow);
    applySizes();
    syncRail();
    if (before) play(before);
    persist();
  }

  /** Mount on first sight, then tell each widget whether it is on screen —
   *  a widget that is not showing must not poll or animate. */
  function syncVisibility(now) {
    const hidden = document.visibilityState !== "visible";
    for (const id of now) {
      ensureMounted(id);
      if (!shown.has(id) && !hidden) {
        shown.add(id);
        const api = live.get(id);
        if (api.show) try { api.show(); } catch (e) { console.error("[dock] show", id, e); }
      }
    }
    for (const id of [...shown]) {
      if (!now.has(id) || hidden) {
        shown.delete(id);
        const api = live.get(id);
        if (api && api.hide) try { api.hide(); } catch (e) { console.error("[dock] hide", id, e); }
      }
    }
    lastVisible = now;
  }
  let lastVisible = new Set();
  document.addEventListener("visibilitychange", () => syncVisibility(lastVisible));

  function splitEl(zone, a, b) {
    const n = document.createElement("div");
    n.className = `dk-split ${zone === "bottom" ? "v" : "h"}`;
    n.dataset.a = a; n.dataset.b = b;
    n.setAttribute("role", "separator");
    return n;
  }

  /** Widths are stored as the user set them; what is APPLIED is clamped so
   *  the chart keeps CENTER_MIN — a narrower window shrinks the docks for now
   *  without forgetting the size they had. */
  function applySizes() {
    const W = main.clientWidth - (el("rail") ? el("rail").offsetWidth : 0)
      - (el("chatPanel") && !el("chatPanel").classList.contains("hidden") ? el("chatPanel").offsetWidth : 0);
    const L = zoneEl.left.classList.contains("on") ? S.zones.left.size : 0;
    const R = zoneEl.right.classList.contains("on") ? S.zones.right.size : 0;
    const room = Math.max(0, W - CENTER_MIN);
    const k = L + R > room && L + R > 0 ? room / (L + R) : 1;
    if (!compact) {
      zoneEl.left.style.width = L ? Math.max(MIN.left * Math.min(1, k), Math.round(L * k)) + "px" : "";
      zoneEl.right.style.width = R ? Math.max(MIN.right * Math.min(1, k), Math.round(R * k)) + "px" : "";
      const H = center.clientHeight;
      zoneEl.bottom.style.height = zoneEl.bottom.classList.contains("on")
        ? clamp(S.zones.bottom.size, MIN.bottom, Math.max(MIN.bottom, H * .62)) + "px" : "";
    } else {
      zoneEl.left.style.width = zoneEl.right.style.width = zoneEl.bottom.style.height = "";
    }
    // The OHLC figures stand down when the chart is too narrow to hold them
    // on one line (see .two-columns in the stylesheet).
    requestAnimationFrame(() => document.body.classList.toggle("two-columns",
      !compact && charts.clientWidth > 0 && charts.clientWidth < 640));
  }
  addEventListener("resize", () => { unmax(); applySizes(); placeFloats(); });
  // the chat column opening or closing changes the room the docks have
  if (window.ResizeObserver && el("chatPanel")) {
    new ResizeObserver(() => applySizes()).observe(el("chatPanel"));
  }

  function placeFloats() {
    const W = center.clientWidth, H = center.clientHeight;
    for (const f of S.floats) {
      const node = groupEls.get(f.gid);
      if (!node) continue;
      f.w = clamp(f.w, 240, Math.max(240, W - 16));
      f.h = clamp(f.h, 160, Math.max(160, H - 16));
      f.x = clamp(f.x, 8, Math.max(8, W - f.w - 8));
      f.y = clamp(f.y, 8, Math.max(8, H - f.h - 8));
      Object.assign(node.style, { left: f.x + "px", top: f.y + "px",
                                  width: f.w + "px", height: f.h + "px" });
    }
  }

  /* ── FLIP: things that moved glide from where they were ────────────────── */
  function snapshot() {
    const m = new Map();
    for (const [gid, n] of groupEls) if (n.isConnected) m.set(gid, n.getBoundingClientRect());
    return m;
  }
  function play(before) {
    if (reduced.matches) return;
    for (const [gid, n] of groupEls) {
      if (!n.isConnected) continue;
      const a = before.get(gid), b = n.getBoundingClientRect();
      if (!b.width) continue;
      if (!a) {
        n.animate([{ opacity: 0, transform: "scale(.975)" }, { opacity: 1, transform: "none" }],
                  { duration: 180, easing: "cubic-bezier(.22,1,.36,1)" });
        continue;
      }
      const dx = a.left - b.left, dy = a.top - b.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
      n.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
                { duration: 220, easing: "cubic-bezier(.22,1,.36,1)" });
    }
  }

  /* ══ opening and closing ═════════════════════════════════════════════════ */

  /** Open a widget type. A single-instance widget comes back where it was; a
   *  multi-instance one reopens its most recently closed copy (so a note you
   *  closed is still that note) unless `fresh` asks for a new one. */
  function open(type, where, opts = {}) {
    const spec = TYPES.get(type);
    if (!spec) return null;
    if (S.focus) S.focus = false;
    let id = !opts.fresh ? placedOf(type)[0] : null;
    if (id && !where) {
      const gid = groupOf(id);
      S.groups[gid].active = id;
      compactSheet.active = id;
      if (compact && !compactSheet.shown) { compactSheet.shown = true; phoneMakeRoom(); }
      layout(true);
      flash(id);
      return id;
    }
    let remembered = null;
    if (!id) {
      const ci = !opts.fresh ? S.closed.map((c) => c.id).reverse()
        .find((cid) => S.inst[cid] && S.inst[cid].type === type) : null;
      if (ci) {
        remembered = S.closed.find((c) => c.id === ci);
        S.closed = S.closed.filter((c) => c.id !== ci);
        id = ci;
      } else {
        id = spec.single ? type : uid(type + ":");
        if (spec.single && S.inst[id]) { /* reuse */ } else {
          S.inst[id] = { type, cfg: { ...(spec.defaults || {}), ...(opts.cfg || {}) } };
        }
      }
    } else {
      detach(id);
    }
    let target = where;
    if (!target && remembered) {
      target = remembered.zone === "float" && remembered.rect
        ? { kind: "float", ...remembered.rect }
        : S.groups[remembered.gid] ? { kind: "tab", gid: remembered.gid }
        : { kind: "zone", zone: remembered.zone || spec.zone || "right" };
    }
    place(id, target || { kind: "zone", zone: spec.zone || "right" });
    compactSheet.active = id;
    if (compact) { compactSheet.shown = true; phoneMakeRoom(); }
    if (typeof Journal !== "undefined" && Journal.toggleQuick) Journal.toggleQuick(false);
    layout(true);
    flash(id);
    return id;
  }

  function close(id) {
    if (!S.inst[id]) return;
    const placeAt = detach(id);
    if (placeAt) S.closed.push({ id, zone: placeAt.zone, gid: placeAt.gid, rect: placeAt.rect });
    S.closed = S.closed.slice(-24);
    if (maxed && !S.groups[maxed]) unmax();
    layout(true);
    if (compact) phoneRestore();
  }

  /** On a phone, putting the sheet away is not closing anything: the widgets
   *  stay where the desktop layout has them. */
  function hideSheet() {
    compactSheet.shown = false;
    layout(false);
    phoneRestore();
  }

  function toggle(type) {
    const ids = placedOf(type);
    if (compact) {
      const on = compactSheet.shown && ids.includes(compactSheet.active);
      return on ? hideSheet() : open(type);
    }
    const vis = ids.find((id) => lastVisible.has(id));
    if (vis) return close(vis);
    return open(type);
  }

  /** The widget you just summoned says where it is: one soft pulse on its
   *  group, so a tab that landed in an existing stack is found, not hunted. */
  function flash(id) {
    const gid = compact ? "sheet" : groupOf(id);
    const n = gid && groupEls.get(gid);
    if (!n || reduced.matches) return;
    n.classList.remove("dk-flash");
    void n.offsetWidth;
    n.classList.add("dk-flash");
  }

  function duplicate(id) {
    const i = S.inst[id], spec = TYPES.get(i.type);
    if (spec.single) return;
    const copy = uid(i.type + ":");
    S.inst[copy] = { type: i.type, cfg: JSON.parse(JSON.stringify(i.cfg || {})) };
    const api = live.get(id);
    if (api && api.duplicate) try { api.duplicate(copy); } catch { }
    place(copy, { kind: "tab", gid: groupOf(id), index: S.groups[groupOf(id)].tabs.indexOf(id) + 1 });
    layout(true);
  }

  function moveTo(id, zone) {
    const from = groupOf(id);
    const rectHint = from && groupEls.get(from) ? groupEls.get(from).getBoundingClientRect() : null;
    detach(id);
    if (zone === "float") {
      const c = center.getBoundingClientRect();
      const x = rectHint ? rectHint.left - c.left : c.width / 2 - FLOAT_DEF.w / 2;
      place(id, { kind: "float", x: clamp(x, 16, c.width - FLOAT_DEF.w - 16), y: 24 });
    } else {
      place(id, { kind: "zone", zone, newGroup: true });
    }
    layout(true);
  }

  /* ── the phone: a widget takes the conversation's slot ─────────────────── */
  let restoreChat = false;
  const chatOpen = () => el("chatPanel") && !el("chatPanel").classList.contains("hidden");
  function phoneMakeRoom() {
    if (!compactMq.matches || !window.matchMedia("(orientation: portrait)").matches) return;
    if (chatOpen() && el("chatToggle")) { restoreChat = true; el("chatToggle").click(); }
  }
  function phoneRestore() {
    const any = compactSheet.shown && Object.values(S.groups).some((g) => g.tabs.length);
    if (!any && restoreChat && !chatOpen() && el("chatToggle")) {
      restoreChat = false; el("chatToggle").click();
    }
  }
  if (el("chatToggle")) {
    el("chatToggle").addEventListener("click", () => {
      // opening the chat on a phone puts the widget sheet away
      setTimeout(() => {
        if (compact && compactMq.matches && matchMedia("(orientation: portrait)").matches
            && chatOpen() && lastVisible.size) {
          restoreChat = false;
          compactSheet.shown = false;
          layout(false);
        }
        applySizes();
      }, 0);
    });
  }
  compactMq.addEventListener("change", () => { compact = compactMq.matches; unmax(); layout(false); });

  /* ══ focus mode and fullscreen ══════════════════════════════════════════ */

  function setFocus(on) {
    S.focus = on == null ? !S.focus : !!on;
    unmax();
    layout(true);
    toast(S.focus ? "Widgets hidden. Press Alt Z to bring them back." : "");
  }

  function fullscreen() {
    const d = document;
    try {
      if (d.fullscreenElement) return d.exitFullscreen();
      const p = d.documentElement.requestFullscreen({ navigationUI: "hide" });
      if (p && p.catch) p.catch(() => toast("Fullscreen is not available in this window."));
    } catch { toast("Fullscreen is not available in this window."); }
  }
  document.addEventListener("fullscreenchange", syncHeader);

  /* ══ maximise one group over the workspace ══════════════════════════════ */

  function max(gid) {
    if (maxed === gid) return unmax();
    unmax();
    const node = groupEls.get(gid);
    if (!node) return;
    const from = node.getBoundingClientRect();
    maxed = gid;
    // over the whole workspace — both docks and the chart — but never over
    // the rail or the conversation, which stay where the hand expects them
    const a = (zoneEl.left.classList.contains("on") ? zoneEl.left : center).getBoundingClientRect();
    const b = (zoneEl.right.classList.contains("on") ? zoneEl.right : center).getBoundingClientRect();
    const c = center.getBoundingClientRect();
    Object.assign(node.style, { left: a.left + "px", top: c.top + "px",
                                width: b.right - a.left + "px", height: c.height + "px" });
    node.classList.add("dk-maxed");
    main.classList.add("dk-has-max");
    paintHead(gid);
    if (!reduced.matches) {
      const to = node.getBoundingClientRect();
      node.animate([{ transform: `translate(${from.left - to.left}px, ${from.top - to.top}px) ` +
                                 `scale(${from.width / to.width}, ${from.height / to.height})`,
                      transformOrigin: "0 0" },
                    { transform: "none", transformOrigin: "0 0" }],
                   { duration: 220, easing: "cubic-bezier(.22,1,.36,1)" });
    }
  }
  function unmax() {
    if (!maxed) return;
    const n = groupEls.get(maxed);
    maxed = null;
    main.classList.remove("dk-has-max");
    if (n) {
      n.classList.remove("dk-maxed");
      if (!n.classList.contains("is-float")) n.style.left = n.style.top = n.style.width = n.style.height = "";
      else placeFloats();
    }
    for (const gid of Object.keys(S.groups)) if (groupEls.has(gid)) paintHead(gid);
  }

  /* ══ menus, settings, toast ══════════════════════════════════════════════ */

  let pop = null, popOff = null;
  function closePop() {
    if (!pop) return;
    pop.remove(); pop = null;
    document.removeEventListener("pointerdown", popOff, true);
    popOff = null;
  }
  /** One floating sheet at a time, hung off an anchor or a point. Items are
   *  { id, label, icon, on, off, sep, head, hint } or raw HTML for bespoke
   *  sheets (the settings form, the catalogue). */
  function menu(anchor, items, onPick, cls = "") {
    const again = pop && pop.__anchor === anchor;
    closePop();
    if (again) return null;
    if (window.__chartoCloseMenus) window.__chartoCloseMenus(null);
    const p = document.createElement("div");
    p.className = `dropdown floating open dk-menu ${cls}`;
    p.__anchor = anchor;
    p.innerHTML = typeof items === "string" ? items : items.map((it) =>
      it.sep ? `<div class="sep"></div>`
      : it.head ? `<div class="head">${esc(it.head)}</div>`
      : `<div class="item${it.on ? " on" : ""}${it.off ? " off" : ""}${it.danger ? " danger" : ""}" data-pick="${esc(it.id)}">` +
        `<span class="lead">${it.icon ? icon(it.icon, "xs") : ""}${esc(it.label)}</span>` +
        (it.hint ? `<span class="sc">${esc(it.hint)}</span>` : it.on ? icon("check", "xs") : "") +
        `</div>`).join("");
    document.body.appendChild(p);
    pop = p;
    const r = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect()
      : { left: anchor.x, right: anchor.x, top: anchor.y, bottom: anchor.y };
    const w = p.offsetWidth, h = p.offsetHeight;
    p.style.left = clamp(r.right - w, 8, innerWidth - w - 8) + "px";
    p.style.top = (r.bottom + h + 8 > innerHeight && r.top - h - 6 > 8
      ? r.top - h - 6 : clamp(r.bottom + 6, 8, innerHeight - h - 8)) + "px";
    p.addEventListener("click", (e) => {
      const it = e.target.closest("[data-pick]");
      if (!it || it.classList.contains("off")) return;
      const keep = onPick && onPick(it.dataset.pick, it, e);
      if (keep !== true) closePop();
    });
    popOff = (e) => { if (!p.contains(e.target) && !(anchor.contains && anchor.contains(e.target))) closePop(); };
    setTimeout(() => document.addEventListener("pointerdown", popOff, true), 0);
    return p;
  }
  addEventListener("resize", closePop);

  function moreMenu(anchor, id) {
    const spec = TYPES.get(S.inst[id].type);
    const z = whereGroup(groupOf(id));
    const api = live.get(id) || {};
    menu(anchor, [
      { head: spec.title },
      ...(api.ask ? [{ id: "ask", label: "Ask in chat", icon: "chat" }] : []),
      ...(!spec.single ? [{ id: "dup", label: "Duplicate", icon: "copy" }] : []),
      ...(spec.settings && spec.settings.length ? [{ id: "settings", label: "Settings", icon: "settings" }] : []),
      ...(compact ? [] : [{ sep: true }, { head: "Move to" },
        { id: "mv:left", label: "Left dock", icon: "panelLeft", on: z && z.zone === "left" },
        { id: "mv:right", label: "Right dock", icon: "panelRight", on: z && z.zone === "right" },
        { id: "mv:bottom", label: "Bottom dock", icon: "panelBottom", on: z && z.zone === "bottom" },
        { id: "mv:float", label: "Floating window", icon: "float", on: z && z.zone === "float" }]),
      { sep: true },
      { id: "close", label: "Close", icon: "x", hint: "" },
    ], (pick) => {
      if (pick === "ask") return askFrom(id);
      if (pick === "dup") return duplicate(id);
      if (pick === "settings") return setTimeout(() => settingsSheet(anchor, id), 0);
      if (pick === "close") return close(id);
      if (pick.startsWith("mv:")) return moveTo(id, pick.slice(3));
    });
  }

  function askFrom(id) {
    const api = live.get(id) || {};
    const text = api.ask && api.ask();
    if (text && window.Chat && window.Chat.compose) window.Chat.compose(text);
  }

  /** The settings a widget declared, drawn as a short form. Every change is
   *  live — there is no Apply, because every one of these is reversible by
   *  the same control that made it. */
  function settingsSheet(anchor, id) {
    const spec = TYPES.get(S.inst[id].type), cfg = S.inst[id].cfg;
    const row = (s) => {
      const v = cfg[s.key] !== undefined ? cfg[s.key] : s.def;
      if (s.kind === "toggle") {
        return `<label class="dk-set"><span>${esc(s.label)}</span>` +
          `<input type="checkbox" class="dk-switch" data-key="${s.key}" ${v ? "checked" : ""}></label>`;
      }
      return `<div class="dk-set"><span>${esc(s.label)}</span><div class="dk-seg" data-key="${s.key}">` +
        s.options.map((o) => `<button type="button" class="${String(o.v) === String(v) ? "on" : ""}" ` +
          `data-v="${esc(o.v)}">${esc(o.label)}</button>`).join("") + `</div></div>`;
    };
    const p = menu(anchor, `<div class="head">${esc(spec.title)} settings</div>` +
      spec.settings.map(row).join(""), null, "dk-settings");
    if (!p) return;
    p.addEventListener("change", (e) => {
      const k = e.target.dataset.key;
      if (k) setCfg(id, { [k]: e.target.checked });
    });
    p.addEventListener("click", (e) => {
      const b = e.target.closest(".dk-seg button");
      if (!b) return;
      const seg = b.parentNode;
      seg.querySelectorAll("button").forEach((x) => x.classList.toggle("on", x === b));
      const s = spec.settings.find((x) => x.key === seg.dataset.key);
      const raw = b.dataset.v;
      setCfg(id, { [seg.dataset.key]: typeof s.def === "number" ? Number(raw) : raw });
    });
  }

  function setCfg(id, patch) {
    const i = S.inst[id];
    if (!i) return;
    Object.assign(i.cfg, patch);
    persist();
    const api = live.get(id);
    if (api && api.config) try { api.config(i.cfg, patch); } catch (e) { console.error(e); }
    const gid = groupOf(id);
    if (gid && groupEls.has(gid)) paintHead(gid);
  }

  function linkMenu(anchor, id) {
    const cfg = S.inst[id].cfg;
    menu(anchor, [
      { head: "Symbol" },
      { id: "follow", label: `Follow the chart (${pageSymbol()})`, icon: "link", on: !cfg.pin },
      { id: "pin", label: cfg.pin ? `Pinned to ${cfg.pin} — change…` : "Pin another symbol…",
        icon: "pin", on: !!cfg.pin },
    ], (pick) => {
      if (pick === "follow") return setCfg(id, { pin: null });
      if (typeof Universe !== "undefined") {
        setTimeout(() => Universe.open({ anchor, current: cfg.pin || pageSymbol(),
                                         onPick: (s) => setCfg(id, { pin: s }) }), 0);
      }
    });
  }

  let toastT = 0;
  const toastEl = document.createElement("div");
  toastEl.className = "dk-toast";
  toastEl.setAttribute("role", "status");
  document.body.appendChild(toastEl);
  function toast(text) {
    clearTimeout(toastT);
    if (!text) { toastEl.classList.remove("on"); return; }
    toastEl.textContent = text;
    toastEl.classList.add("on");
    toastT = setTimeout(() => toastEl.classList.remove("on"), 2600);
  }

  /* ══ the widget's handle on the dock ════════════════════════════════════ */

  function ctxFor(id) {
    return {
      id,
      get cfg() { return S.inst[id] ? S.inst[id].cfg : {}; },
      setCfg: (patch) => setCfg(id, patch),
      /** The instrument this widget is about: the chart's, unless pinned. */
      symbol: () => (S.inst[id] && S.inst[id].cfg.pin) || pageSymbol(),
      pageSymbol,
      setTitle: (sub) => {
        if (titles.get(id) === sub) return;
        titles.set(id, sub);
        const gid = compact ? "sheet" : groupOf(id);
        if (gid && groupEls.has(gid)) {
          if (compact) { layout(false); } else paintHead(gid);
        }
      },
      visible: () => shown.has(id),
      compose: (text) => window.Chat && window.Chat.compose && window.Chat.compose(text),
      openSymbol, warm, menu, toast,
    };
  }

  /* ══ dragging ════════════════════════════════════════════════════════════
   * One gesture, four outcomes, decided by where the pointer is when it
   * lets go — and SHOWN before it lets go: a single preview plate glides
   * between candidate spots, so the drop is never a surprise. Escape, or
   * letting go anywhere that is not a target, puts everything back. */

  const ghost = document.createElement("div");
  ghost.className = "dk-ghost";
  const preview = document.createElement("div");
  preview.className = "dk-drop";
  document.body.append(preview, ghost);

  let drag = null;

  function beginDrag(e, source) {
    // source: { kind: "inst", id } | { kind: "group", gid } | { kind: "new", type }
    const x0 = e.clientX, y0 = e.clientY;
    const pid = e.pointerId;
    const move = (ev) => {
      if (ev.pointerId !== pid) return;
      if (!drag) {
        if (Math.hypot(ev.clientX - x0, ev.clientY - y0) < 5) return;
        startDrag(source, ev);
      }
      drag.x = ev.clientX; drag.y = ev.clientY;
      if (!drag.raf) drag.raf = requestAnimationFrame(frame);
    };
    const up = (ev) => {
      if (ev.pointerId !== pid) return;
      removeEventListener("pointermove", move);
      removeEventListener("pointerup", up);
      removeEventListener("pointercancel", up);
      if (drag) finishDrag(ev.type === "pointerup");
    };
    addEventListener("pointermove", move);
    addEventListener("pointerup", up);
    addEventListener("pointercancel", up);
  }

  function startDrag(source, ev) {
    closePop();
    unmax();
    let title, ic;
    if (source.kind === "new") {
      const s = TYPES.get(source.type); title = s.title; ic = s.icon;
    } else {
      const id = source.kind === "inst" ? source.id : S.groups[source.gid].active;
      const s = TYPES.get(S.inst[id].type); title = label(id).title; ic = s.icon;
      if (source.kind === "group" && S.groups[source.gid].tabs.length > 1) {
        title += ` +${S.groups[source.gid].tabs.length - 1}`;
      }
    }
    ghost.innerHTML = `${icon(ic)}<span>${esc(title)}</span>`;
    drag = { source, x: ev.clientX, y: ev.clientY, target: null, raf: 0 };
    document.body.classList.add("dk-dragging");
    ghost.classList.add("on");
    const srcGid = source.kind === "inst" ? groupOf(source.id) : source.gid;
    if (srcGid && groupEls.get(srcGid)) {
      const n = groupEls.get(srcGid);
      if (source.kind === "group" || S.groups[srcGid].tabs.length === 1) n.classList.add("dk-src");
      else { const t = n.querySelector(`.dk-tab[data-inst="${source.id}"]`); if (t) t.classList.add("dk-src"); }
    }
    addEventListener("keydown", escDrag, true);
    frame();
  }

  function escDrag(e) {
    if (e.key !== "Escape" || !drag) return;
    e.preventDefault(); e.stopPropagation();
    finishDrag(false);
  }

  function frame() {
    if (!drag) return;
    drag.raf = 0;
    ghost.style.transform = `translate3d(${drag.x + 14}px, ${drag.y + 12}px, 0)`;
    const t = hit(drag.x, drag.y);
    drag.target = t;
    if (!t) { preview.classList.remove("on"); return; }
    const r = t.rect;
    preview.classList.toggle("caret", t.kind === "tab");
    Object.assign(preview.style, { left: r.left + "px", top: r.top + "px",
                                   width: r.width + "px", height: r.height + "px" });
    preview.classList.add("on");
  }

  const R = (a) => ({ left: a.left, top: a.top, width: a.width, height: a.height,
                      right: a.right, bottom: a.bottom });

  /** What dropping at (x, y) would do, and the rectangle that shows it. */
  function hit(x, y) {
    const src = drag.source;
    const srcGid = src.kind === "group" ? src.gid : src.kind === "inst" ? groupOf(src.id) : null;
    const lonely = srcGid && (src.kind === "group" || S.groups[srcGid].tabs.length === 1);
    // 1 · a tab strip: join that group, at the slot under the pointer
    for (const [gid, n] of groupEls) {
      if (!n.isConnected || gid === "sheet") continue;
      const head = n.querySelector(".dk-head").getBoundingClientRect();
      if (x < head.left || x > head.right || y < head.top || y > head.bottom) continue;
      if (lonely && gid === srcGid) return null;
      const tabs = [...n.querySelectorAll(".dk-tab")];
      let index = tabs.length, cx = tabs.length
        ? tabs[tabs.length - 1].getBoundingClientRect().right : head.left + 34;
      for (let i = 0; i < tabs.length; i++) {
        const tr = tabs[i].getBoundingClientRect();
        if (x < tr.left + tr.width / 2) { index = i; cx = tr.left; break; }
      }
      return { kind: "tab", gid, index, rect: { left: cx - 1.5, top: head.top + 6, width: 3, height: head.height - 12 } };
    }
    // 2 · a group's body: its own half (split), or its middle (join)
    for (const [gid, n] of groupEls) {
      if (!n.isConnected || gid === "sheet") continue;
      const b = n.getBoundingClientRect();
      if (x < b.left || x > b.right || y < b.top || y > b.bottom) continue;
      if (lonely && gid === srcGid) return null;
      const w = whereGroup(gid);
      if (w && w.zone !== "float") {
        const horiz = w.zone === "bottom";
        const f = horiz ? (x - b.left) / b.width : (y - b.top) / b.height;
        if (f < .33 || f > .67) {
          const after = f > .5;
          const rect = horiz
            ? { left: after ? b.left + b.width / 2 : b.left, top: b.top, width: b.width / 2, height: b.height }
            : { left: b.left, top: after ? b.top + b.height / 2 : b.top, width: b.width, height: b.height / 2 };
          return { kind: "split", gid, after, rect };
        }
      }
      return { kind: "tab", gid, index: null, rect: R(b), merge: true };
    }
    // 3 · the chart: an edge opens a dock there, the middle floats it
    const c = center.getBoundingClientRect();
    const bz = zoneEl.bottom.classList.contains("on") ? zoneEl.bottom.getBoundingClientRect() : null;
    const top = c.top, bottom = bz ? bz.top : c.bottom;
    if (x < c.left || x > c.right || y < top || y > bottom) {
      // over an open dock's empty space (below its last group)
      for (const z of ["left", "right"]) {
        const zr = zoneEl[z].getBoundingClientRect();
        if (zoneEl[z].classList.contains("on") && x >= zr.left && x <= zr.right && y >= zr.top && y <= zr.bottom) {
          return { kind: "zone", zone: z, newGroup: true, rect: { left: zr.left, top: zr.bottom - zr.height / 3, width: zr.width, height: zr.height / 3 } };
        }
      }
      return null;
    }
    const band = Math.min(120, c.width * .18);
    const vband = Math.min(140, (bottom - top) * .26);
    const zoneRect = (z) => {
      const on = zoneEl[z].classList.contains("on");
      if (z === "left") {
        if (on) { const r = zoneEl.left.getBoundingClientRect(); return { left: r.left, top: r.bottom - r.height / 3, width: r.width, height: r.height / 3 }; }
        return { left: c.left, top, width: Math.min(S.zones.left.size, c.width * .42), height: bottom - top };
      }
      if (z === "right") {
        if (on) { const r = zoneEl.right.getBoundingClientRect(); return { left: r.left, top: r.bottom - r.height / 3, width: r.width, height: r.height / 3 }; }
        const w = Math.min(S.zones.right.size, c.width * .42);
        return { left: c.right - w, top, width: w, height: bottom - top };
      }
      if (on) { const r = bz; return { left: r.right - r.width / 3, top: r.top, width: r.width / 3, height: r.height }; }
      const h = Math.min(S.zones.bottom.size, (bottom - top) * .5);
      return { left: c.left, top: bottom - h, width: c.width, height: h };
    };
    if (x - c.left < band) return { kind: "zone", zone: "left", newGroup: true, rect: zoneRect("left") };
    if (c.right - x < band) return { kind: "zone", zone: "right", newGroup: true, rect: zoneRect("right") };
    if (bottom - y < vband) return { kind: "zone", zone: "bottom", newGroup: true, rect: zoneRect("bottom") };
    const fw = FLOAT_DEF.w, fh = FLOAT_DEF.h;
    const fx = clamp(x - fw / 2, c.left + 8, c.right - fw - 8);
    const fy = clamp(y - 18, top + 8, bottom - fh - 8);
    return { kind: "float", x: fx - c.left, y: fy - c.top, rect: { left: fx, top: fy, width: fw, height: fh } };
  }

  function finishDrag(commit) {
    const d = drag;
    drag = null;
    cancelAnimationFrame(d.raf);
    removeEventListener("keydown", escDrag, true);
    document.body.classList.remove("dk-dragging");
    document.querySelectorAll(".dk-src").forEach((n) => n.classList.remove("dk-src"));
    preview.classList.remove("on", "caret");
    const t = commit ? d.target : null;
    if (!t) { ghost.classList.remove("on"); return; }
    const src = d.source;
    const before = snapshot();
    if (src.kind === "new") {
      open(src.type, t);
    } else if (src.kind === "inst") {
      if (t.kind === "tab" && groupOf(src.id) === t.gid) {
        // reorder inside the same strip
        const g = S.groups[t.gid], from = g.tabs.indexOf(src.id);
        let to = t.index == null ? g.tabs.length : t.index;
        if (to > from) to -= 1;
        g.tabs.splice(from, 1); g.tabs.splice(to, 0, src.id); g.active = src.id;
      } else {
        detach(src.id);
        place(src.id, t);
      }
      layout(false); play(before);
    } else if (src.kind === "group") {
      const g = S.groups[src.gid];
      if (!g) { ghost.classList.remove("on"); return; }
      const tabs = [...g.tabs], active = g.active;
      if (t.kind === "float" && whereGroup(src.gid).zone === "float") {
        // dragging a float by its grip is handled live; nothing to do
      } else {
        tabs.forEach((id) => detach(id));
        place(tabs[0], t);
        const gid = groupOf(tabs[0]);
        tabs.slice(1).forEach((id, i) => S.groups[gid].tabs.splice(S.groups[gid].tabs.indexOf(tabs[0]) + 1 + i, 0, id));
        S.groups[gid].active = active;
      }
      layout(false); play(before);
    }
    landGhost(t);
  }

  /** The ghost flies into the spot it was dropped on and dissolves there. */
  function landGhost(t) {
    if (reduced.matches) { ghost.classList.remove("on"); return; }
    const r = t.rect;
    const a = ghost.animate([{ opacity: 1 }, { transform: `translate3d(${r.left + 10}px, ${r.top + 8}px, 0) scale(.9)`, opacity: 0 }],
                            { duration: 170, easing: "cubic-bezier(.22,1,.36,1)", fill: "forwards" });
    a.onfinish = () => { a.cancel(); ghost.classList.remove("on"); };
  }

  /* ── pointer wiring for groups (delegated on the docks and the floats) ── */

  function onGroupPointerDown(e) {
    if (e.button !== 0) return;
    const node = e.target.closest(".dk-group");
    if (!node) return;
    const gid = node.dataset.gid;
    const float = S.floats.find((f) => f.gid === gid);
    if (float) {
      // bring to front
      S.floats = [...S.floats.filter((f) => f !== float), float];
      if (floatLayer.lastElementChild !== node) floatLayer.appendChild(node);
    }
    if (compact) return;
    if (e.target.closest("[data-act='resize']") && float) return resizeFloat(e, float, node);
    const tab = e.target.closest(".dk-tab");
    if (tab && !e.target.closest("[data-act='close-tab']")) {
      return beginDrag(e, { kind: "inst", id: tab.dataset.inst });
    }
    if (e.target.closest("[data-act='grip']")) {
      e.preventDefault();
      if (float) return moveFloat(e, float, node);
      return beginDrag(e, { kind: "group", gid });
    }
    // the empty part of a float's head moves the window
    if (float && e.target.closest(".dk-head") && !e.target.closest("button")) {
      return moveFloat(e, float, node);
    }
  }

  function moveFloat(e, f, node) {
    const x0 = e.clientX, y0 = e.clientY, fx = f.x, fy = f.y;
    let dragged = false, redock = false;
    node.classList.add("dk-moving");
    const mv = (ev) => {
      const dx = ev.clientX - x0, dy = ev.clientY - y0;
      if (!dragged && Math.hypot(dx, dy) < 3) return;
      dragged = true;
      const W = center.clientWidth, H = center.clientHeight;
      let nx = fx + dx, ny = fy + dy;
      // snap to the chart's edges within 10px — windows line up without effort
      if (Math.abs(nx - 8) < 10) nx = 8;
      if (Math.abs(W - (nx + f.w) - 8) < 10) nx = W - f.w - 8;
      if (Math.abs(ny - 8) < 10) ny = 8;
      if (Math.abs(H - (ny + f.h) - 8) < 10) ny = H - f.h - 8;
      f.x = clamp(nx, 8, W - f.w - 8); f.y = clamp(ny, 8, H - f.h - 8);
      node.style.left = f.x + "px"; node.style.top = f.y + "px";
      // pulled against a side of the CHART, the window offers to dock there
      const c = center.getBoundingClientRect();
      redock = ev.clientX < c.left + 6 ? "left" : ev.clientX > c.right - 6 ? "right"
        : ev.clientY > c.bottom - 6 ? "bottom" : false;
      node.classList.toggle("dk-redock", !!redock);
    };
    const up = () => {
      removeEventListener("pointermove", mv); removeEventListener("pointerup", up);
      node.classList.remove("dk-moving", "dk-redock");
      if (redock) {
        const tabs = [...S.groups[f.gid].tabs], active = S.groups[f.gid].active;
        tabs.forEach((id) => detach(id));
        place(tabs[0], { kind: "zone", zone: redock, newGroup: true });
        const gid = groupOf(tabs[0]);
        S.groups[gid].tabs = tabs; S.groups[gid].active = active;
        layout(true);
      } else persist();
    };
    addEventListener("pointermove", mv); addEventListener("pointerup", up);
  }

  function resizeFloat(e, f, node) {
    e.preventDefault();
    const x0 = e.clientX, y0 = e.clientY, w0 = f.w, h0 = f.h;
    document.body.classList.add("dk-resizing");
    const mv = (ev) => {
      f.w = clamp(w0 + ev.clientX - x0, 240, center.clientWidth - f.x - 8);
      f.h = clamp(h0 + ev.clientY - y0, 160, center.clientHeight - f.y - 8);
      node.style.width = f.w + "px"; node.style.height = f.h + "px";
    };
    const up = () => {
      removeEventListener("pointermove", mv); removeEventListener("pointerup", up);
      document.body.classList.remove("dk-resizing");
      persist();
    };
    addEventListener("pointermove", mv); addEventListener("pointerup", up);
  }

  function onGroupClick(e) {
    const node = e.target.closest(".dk-group");
    if (!node) return;
    const gid = node.dataset.gid;
    const g = compact ? { tabs: [...lastVisible], active: compactSheet.active } : S.groups[gid];
    const closeTab = e.target.closest("[data-act='close-tab']");
    if (closeTab) { e.stopPropagation(); return close(closeTab.closest(".dk-tab").dataset.inst); }
    const tab = e.target.closest(".dk-tab");
    if (tab) {
      const id = tab.dataset.inst;
      if (compact) { compactSheet.active = id; return layout(false); }
      if (g.active !== id) { g.active = id; layout(false); }
      return;
    }
    const b = e.target.closest("[data-act]");
    if (!b || !g) return;
    const active = compact ? compactSheet.active : g.active;
    const a = b.dataset.act;
    if (a === "close") { e.stopPropagation(); return compact ? hideSheet() : close(active); }
    if (a === "max") return max(gid);
    if (a === "more") { e.stopPropagation(); return moreMenu(b, active); }
    if (a === "settings") { e.stopPropagation(); return settingsSheet(b, active); }
    if (a === "link") { e.stopPropagation(); return linkMenu(b, active); }
  }

  for (const host of [zoneEl.left, zoneEl.right, zoneEl.bottom, floatLayer]) {
    host.addEventListener("pointerdown", onGroupPointerDown);
    host.addEventListener("click", onGroupClick);
    host.addEventListener("dblclick", (e) => {
      const tab = e.target.closest(".dk-tab");
      if (tab && !compact) max(tab.closest(".dk-group").dataset.gid);
    });
    host.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const tab = e.target.closest(".dk-tab");
      if (!tab) return;
      const sib = e.key === "ArrowLeft" ? tab.previousElementSibling : tab.nextElementSibling;
      if (sib && sib.classList.contains("dk-tab")) { sib.click(); sib.focus(); }
    });
  }
  // a click anywhere outside a maximised group's head leaves it maximised;
  // Escape is the way back (and the button in its head)
  addEventListener("keydown", (e) => { if (e.key === "Escape" && maxed && !drag) unmax(); });

  /* ── resizing the docks and the splits inside them ─────────────────────── */

  function onHandleDown(e) {
    const h = e.target.closest(".dk-handle, .dk-split");
    if (!h || e.button !== 0) return;
    e.preventDefault();
    const x0 = e.clientX, y0 = e.clientY;
    document.body.classList.add("dk-resizing", h.classList.contains("v") ? "dk-col-resize" : "dk-row-resize");
    h.classList.add("dragging");
    let mv;
    if (h.classList.contains("dk-handle")) {
      const z = h.dataset.zone;
      const start = z === "bottom" ? zoneEl.bottom.offsetHeight : zoneEl[z].offsetWidth;
      mv = (ev) => {
        const d = z === "left" ? ev.clientX - x0 : z === "right" ? x0 - ev.clientX : y0 - ev.clientY;
        S.zones[z].size = Math.round(clamp(start + d, MIN[z], z === "bottom"
          ? center.clientHeight * .62 : (main.clientWidth - CENTER_MIN) * .7));
        applySizes();
      };
    } else {
      const a = S.groups[h.dataset.a], b = S.groups[h.dataset.b];
      const na = groupEls.get(h.dataset.a), nb = groupEls.get(h.dataset.b);
      const horiz = h.classList.contains("v");
      const sa = horiz ? na.offsetWidth : na.offsetHeight, sb = horiz ? nb.offsetWidth : nb.offsetHeight;
      const wsum = (a.size || 1) + (b.size || 1);
      mv = (ev) => {
        const d = horiz ? ev.clientX - x0 : ev.clientY - y0;
        const pa = clamp(sa + d, 90, sa + sb - 90);
        a.size = wsum * pa / (sa + sb); b.size = wsum - a.size;
        na.style.flex = `${a.size} 1 0`; nb.style.flex = `${b.size} 1 0`;
      };
    }
    const up = () => {
      removeEventListener("pointermove", mv); removeEventListener("pointerup", up);
      document.body.classList.remove("dk-resizing", "dk-col-resize", "dk-row-resize");
      h.classList.remove("dragging");
      for (const n of groupEls.values()) if (n.isConnected) paintInk(n);
      persist();
    };
    addEventListener("pointermove", mv); addEventListener("pointerup", up);
  }
  main.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".dk-handle, .dk-split")) onHandleDown(e);
  });
  main.addEventListener("dblclick", (e) => {
    const h = e.target.closest(".dk-handle");
    if (h) { S.zones[h.dataset.zone].size = DEF[h.dataset.zone]; applySizes(); persist(); }
    const sp = e.target.closest(".dk-split");
    if (sp) { S.groups[sp.dataset.a].size = S.groups[sp.dataset.b].size = 1; layout(false); }
  });
  for (const z of ZONES) {
    handleEl[z].addEventListener("keydown", (e) => {
      const k = { ArrowLeft: z === "left" ? -16 : 16, ArrowRight: z === "left" ? 16 : -16,
                  ArrowUp: 16, ArrowDown: -16 }[e.key];
      if (!k || (z === "bottom") !== (e.key === "ArrowUp" || e.key === "ArrowDown")) return;
      e.preventDefault();
      S.zones[z].size = Math.max(MIN[z], S.zones[z].size + k);
      applySizes(); persist();
    });
  }

  /* ══ the rail and the header ═════════════════════════════════════════════ */

  const railBtns = new Map();

  function buildRail() {
    const spacer = document.querySelector("#rail .rail-spacer");
    if (!spacer) return;
    const specs = [...TYPES.values()].filter((s) => s.rail !== false);
    spacer.insertAdjacentHTML("afterend",
      `<div class="rail-sep rail-widget-sep"></div>` +
      specs.map((s) => `<button type="button" class="tool dk-rail" id="wb-${s.type}" ` +
        `data-widget="${s.type}" aria-expanded="false">${icon(s.icon)}` +
        `<span class="tip">${esc(s.title)}${s.key ? ` <kbd>${esc(s.key)}</kbd>` : ""}</span></button>`).join("") +
      `<div class="rail-sep rail-export-sep"></div>`);
    for (const s of specs) railBtns.set(s.type, el(`wb-${s.type}`));
    const rail = el("rail");
    rail.addEventListener("pointerdown", (e) => {
      const b = e.target.closest(".dk-rail");
      if (!b || e.button !== 0 || compact) return;
      const spec = TYPES.get(b.dataset.widget);
      if (spec.onRail) return;            // bespoke widgets do not drag out
      beginDrag(e, { kind: "new", type: b.dataset.widget });
    });
    rail.addEventListener("click", (e) => {
      const b = e.target.closest(".dk-rail");
      if (!b) return;
      if (dragJustEnded) return;
      const spec = TYPES.get(b.dataset.widget);
      if (spec.onRail && spec.onRail({ compact })) return;
      toggle(b.dataset.widget);
    });
    rail.addEventListener("contextmenu", (e) => {
      const b = e.target.closest(".dk-rail");
      if (!b || compact) return;
      e.preventDefault();
      const spec = TYPES.get(b.dataset.widget);
      if (spec.onRail) return;
      menu(b, [
        { head: `Open ${spec.title}` },
        { id: "left", label: "In the left dock", icon: "panelLeft" },
        { id: "right", label: "In the right dock", icon: "panelRight" },
        { id: "bottom", label: "In the bottom dock", icon: "panelBottom" },
        { id: "float", label: "As a floating window", icon: "float" },
        ...(spec.single ? [] : [{ sep: true }, { id: "new", label: `New ${spec.title.toLowerCase()}`, icon: "plus" }]),
      ], (pick) => {
        if (pick === "new") return open(spec.type, null, { fresh: true });
        const c = center.getBoundingClientRect();
        open(spec.type, pick === "float"
          ? { kind: "float", x: c.width / 2 - FLOAT_DEF.w / 2, y: 40 }
          : { kind: "zone", zone: pick, newGroup: true });
      });
    });
  }
  let dragJustEnded = false;
  addEventListener("pointerup", () => {
    if (document.body.classList.contains("dk-dragging")) {
      dragJustEnded = true; setTimeout(() => { dragJustEnded = false; }, 0);
    }
  }, true);

  function syncRail() {
    for (const [type, b] of railBtns) {
      const on = placedOf(type).some((id) => lastVisible.has(id));
      b.classList.toggle("active", on);
      b.setAttribute("aria-expanded", String(on));
    }
    syncHeader();
  }

  /* The header gains two controls: the workspace sheet and fullscreen. */
  let wsBtn = null, fsBtn = null;
  function buildHeader() {
    const anchor = el("chatToggle");
    if (!anchor) return;
    anchor.insertAdjacentHTML("beforebegin",
      `<button class="btn icon dk-hbtn" id="dockBtn" type="button" title="Widgets" ` +
      `aria-label="Widgets" aria-haspopup="menu">${icon("widgets")}</button>` +
      `<button class="btn icon dk-hbtn" id="fullBtn" type="button" title="Fullscreen (Alt Shift F)" ` +
      `aria-label="Fullscreen">${icon("fullscreen")}</button>`);
    wsBtn = el("dockBtn"); fsBtn = el("fullBtn");
    wsBtn.addEventListener("click", (e) => { e.stopPropagation(); catalogue(wsBtn); });
    fsBtn.addEventListener("click", fullscreen);
  }

  function syncHeader() {
    if (fsBtn) {
      const on = !!document.fullscreenElement;
      fsBtn.innerHTML = icon(on ? "fullscreenExit" : "fullscreen");
      fsBtn.title = on ? "Exit fullscreen (Alt Shift F)" : "Fullscreen (Alt Shift F)";
      fsBtn.classList.toggle("on", on);
    }
    if (wsBtn) wsBtn.classList.toggle("on", !!S.focus);
  }

  /** The workspace sheet: every widget as a card you click to open or drag
   *  into place, and the two whole-workspace switches under them. */
  function catalogue(anchor) {
    const specs = [...TYPES.values()].filter((s) => s.catalog !== false);
    const p = menu(anchor,
      `<div class="head">Widgets</div><div class="dk-cards">` +
      specs.map((s) => {
        const on = placedOf(s.type).some((id) => lastVisible.has(id));
        return `<button type="button" class="dk-card${on ? " on" : ""}" data-pick="w:${s.type}">` +
          `<span class="dk-tile">${icon(s.icon)}</span><span class="txt"><b>${esc(s.title)}</b>` +
          `<span>${esc(s.desc || "")}</span></span></button>`;
      }).join("") + `</div><div class="sep"></div>` +
      `<div class="item${S.focus ? " on" : ""}" data-pick="focus"><span class="lead">${icon("focus", "xs")}` +
        `Focus on the chart</span><span class="sc">Alt Z</span></div>` +
      `<div class="item" data-pick="full"><span class="lead">${icon("fullscreen", "xs")}` +
        `${document.fullscreenElement ? "Exit fullscreen" : "Fullscreen"}</span><span class="sc">Alt Shift F</span></div>` +
      `<div class="item" data-pick="reset"><span class="lead">${icon("rotateCw", "xs")}` +
        `Reset the workspace</span></div>`,
      (pick) => {
        if (pick.startsWith("w:")) return toggle(pick.slice(2));
        if (pick === "focus") return setFocus();
        if (pick === "full") return fullscreen();
        if (pick === "reset") {
          for (const id of Object.keys(S.inst)) { const p2 = detach(id); if (p2) S.closed.push({ id, ...p2 }); }
          S.zones.left.size = DEF.left; S.zones.right.size = DEF.right; S.zones.bottom.size = DEF.bottom;
          S.focus = false; unmax(); layout(true);
        }
      }, "dk-catalog");
    if (!p) return;
    // a card can be dragged straight into the workspace
    p.addEventListener("pointerdown", (e) => {
      const card = e.target.closest(".dk-card");
      if (!card || compact) return;
      const type = card.dataset.pick.slice(2);
      const x0 = e.clientX, y0 = e.clientY;
      const mv = (ev) => {
        if (Math.hypot(ev.clientX - x0, ev.clientY - y0) < 6) return;
        removeEventListener("pointermove", mv);
        closePop();
        beginDrag(e, { kind: "new", type });
        startDrag({ kind: "new", type }, ev);
      };
      addEventListener("pointermove", mv);
      addEventListener("pointerup", () => removeEventListener("pointermove", mv), { once: true });
    });
  }

  /* ══ registration and start ══════════════════════════════════════════════ */

  let started = false;
  function register(spec) {
    TYPES.set(spec.type, spec);
    if (started) { buildRailOne(spec); layout(false); }
  }
  function buildRailOne() { /* late registration: the rail is built once */ }

  function start() {
    if (started) return;
    started = true;
    S = load();
    // Back-compat: ?panel=watch (the old way a reload kept a panel open)
    const legacy = new URLSearchParams(location.search).get("panel");
    if (legacy && TYPES.has(legacy) && !placedOf(legacy).length) {
      const id = legacy;
      if (!S.inst[id]) S.inst[id] = { type: legacy, cfg: { ...(TYPES.get(legacy).defaults || {}) } };
      place(id, { kind: "zone", zone: TYPES.get(legacy).zone || "right" });
    }
    buildRail();
    buildHeader();
    layout(false);
    if (typeof Shortcuts !== "undefined" && Shortcuts.on) {
      Shortcuts.on("focus-chart", () => setFocus());
      Shortcuts.on("fullscreen", fullscreen);
      for (const s of TYPES.values()) if (s.shortcut) Shortcuts.on(s.shortcut, () => toggle(s.type));
    }
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
  else queueMicrotask(start);

  return {
    register, start, open, close, toggle, openSymbol, warm, setFocus, fullscreen,
    /** Is any instance of this type on screen right now? */
    visible: (type) => placedOf(type).some((id) => lastVisible.has(id)),
    instances: (type) => placedOf(type),
    setCfg, toast, menu, closeMenu: closePop,
  };
})();
