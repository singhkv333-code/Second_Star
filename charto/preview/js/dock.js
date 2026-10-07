/* Charto preview — the workspace.
 *
 * Everything between the tool rail and the conversation is one CANVAS, laid
 * out as a tree of splits: a row or a column of tiles, each of which is
 * either a group of tabbed widgets or another row or column. The chart is a
 * widget like any other — it has the same header and the same six-dot
 * handle, and it can be dragged anywhere. Drag a widget by its tab (or a
 * whole group by its handle):
 *
 *   · onto another group's tab strip, or the middle of it → join it as a tab
 *   · onto the top, bottom, left or right of any tile     → split it there
 *   · against an outer edge of the canvas                 → a new full-height
 *                                                           or full-width tile
 *   · onto open space, or the middle of the chart         → a floating window
 *
 * The drop is SHOWN before it happens: a plate slides to where the tile will
 * land. The seams between tiles drag to resize. Only the conversation stays
 * where it is — it is the product's other half, not a tile.
 *
 * The chart can be LOCKED in place from its menu or the widget hub; locked,
 * it loses its header and cannot be moved, though widgets can still be split
 * against it.
 *
 * State is one object, persisted unscoped (a workspace follows the user, not
 * the symbol); the DOM is derived from it. Groups and widget hosts are
 * persistent elements that are only MOVED (with Element.moveBefore where the
 * browser has it, so a framed page or a playing video survives the move),
 * never re-rendered.
 *
 * A widget is a spec handed to Dock.register():
 *   { type, title, icon, hue, group, desc, single, zone, linkable, settings[],
 *     host?() — an existing element to adopt (single-instance widgets),
 *     mount(host, ctx) → { show?, hide?, config?, ask?, receive?, duplicate? } }
 * `hue` names the palette of the widget's picture in the hub; the interface
 * itself is monochrome.
 */
"use strict";

const Dock = (() => {
  const el = (id) => document.getElementById(id);
  const main = document.querySelector(".main");
  const charts = document.querySelector(".charts") || el("stage");
  if (!main || !charts) return { register() {}, start() {} };

  const KEY = "dock";
  const CHART = "main";              // the chart's instance id
  const SLOT = "slot";               // an empty tile: room made beside the chart
  const LINK_IDS = ["1", "2", "3", "4"];   // the coloured link groups
  const FLOAT_DEF = { w: 380, h: 420 };
  const MIN_TILE = 150;
  const EDGE_SHARE = .27;            // a new edge tile takes this much of the canvas
  const reduced = matchMedia("(prefers-reduced-motion: reduce)");
  const compactMq = matchMedia("(max-width: 820px) and (orientation: portrait), " +
                               "(orientation: landscape) and (max-height: 520px)");
  const portraitMq = matchMedia("(orientation: portrait)");

  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const icon = (n, cls = "") => Icons.svg(n, cls);
  const uid = (p) => p + Math.random().toString(36).slice(2, 8);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const sum = (a) => a.reduce((x, y) => x + y, 0);
  const move = (parent, node, before) => {
    if (node.parentNode === parent && (before ? node.nextSibling === before : !node.nextSibling)) return;
    try {
      if (parent.moveBefore && node.isConnected && parent.isConnected) return parent.moveBefore(node, before || null);
    } catch { /* fall through */ }
    parent.insertBefore(node, before || null);
  };

  /* ══ the page symbol, and opening another one ═══════════════════════════
   * The primary chart IS the page: its symbol is ?symbol=. Opening an
   * instrument there is a navigation, made to feel instant by three things:
   * the bars are already cached (warm, on hover), the new page paints them
   * before it fetches (js/main.js), and the swap is a cross-document view
   * transition. A selected SECONDARY pane takes the instrument in place. */
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

  const warmed = new Map();
  function warm(sym) {
    const s = String(sym || "").toUpperCase();
    if (!s || s === pageSymbol() || typeof window.__chartoWarm !== "function") return;
    if (Date.now() - (warmed.get(s) || 0) < 60_000) return;
    warmed.set(s, Date.now());
    window.__chartoWarm(s);
  }

  /* ══ state: instances, groups, and the tree ═════════════════════════════
   * tree node: { k: "row" | "col", c: [node…], s: [weight…], id }  or
   *            { k: "g", g: groupId, id } */

  const TYPES = new Map();
  const nid = () => uid("n");
  const leaf = (g) => ({ k: "g", g, id: nid() });
  const box = (k, c, s) => ({ k, c, s: s || c.map(() => 1), id: nid() });
  const blank = () => ({ v: 2, inst: {}, groups: {}, tree: null, floats: [], closed: [],
                         focus: false, lock: false, links: {} });
  let S = blank();

  function load() { return parse(Store.get(KEY, null)); }
  function parse(raw) {
    if (!raw || typeof raw !== "object") return blank();
    const s = blank();
    s.focus = !!raw.focus; s.lock = !!raw.lock;
    for (const [id, i] of Object.entries(raw.inst || {})) {
      if (i && typeof i.type === "string") s.inst[id] = { type: i.type, cfg: i.cfg || {} };
    }
    for (const [gid, g] of Object.entries(raw.groups || {})) {
      if (!g || !Array.isArray(g.tabs)) continue;
      s.groups[gid] = { tabs: g.tabs.filter((t) => typeof t === "string"), active: g.active };
    }
    if (raw.v === 2) s.tree = validTree(raw.tree);
    else if (raw.v === 1 && raw.zones) {
      // the first workspace kept docks: left · (centre over bottom) · right
      const col = (gs) => gs && gs.length ? box("col", gs.map(leaf)) : null;
      const z = raw.zones;
      const mid = [z.main && z.main.groups.length ? box("row", z.main.groups.map(leaf)) : null,
                   z.bottom && z.bottom.groups.length ? box("row", z.bottom.groups.map(leaf)) : null];
      const parts = [[col(z.left && z.left.groups), .27],
                     [mid.some(Boolean) ? box("col", mid.filter(Boolean), mid[0] && mid[1] ? [1, .4] : [1]) : null, 1],
                     [col(z.right && z.right.groups), .29]].filter((p) => p[0]);
      s.tree = parts.length ? box("row", parts.map((p) => p[0]), parts.map((p) => p[1])) : null;
    }
    s.floats = (raw.floats || []).filter((f) => f && s.groups[f.gid])
      .map((f) => ({ gid: f.gid, x: +f.x || 40, y: +f.y || 40,
                     w: +f.w || FLOAT_DEF.w, h: +f.h || FLOAT_DEF.h }));
    s.closed = (raw.closed || []).filter((c) => c && s.inst[c.id]).slice(-24);
    for (const g of LINK_IDS) {
      const l = raw.links && raw.links[g];
      if (l && typeof l === "object") s.links[g] = { sym: typeof l.sym === "string" ? l.sym : null, iv: typeof l.iv === "string" ? l.iv : null };
    }
    return s;
  }

  function validTree(n) {
    if (!n || typeof n !== "object") return null;
    if (n.k === "g") return typeof n.g === "string" ? { k: "g", g: n.g, id: n.id || nid() } : null;
    if ((n.k === "row" || n.k === "col") && Array.isArray(n.c)) {
      const c = n.c.map(validTree);
      const s = c.map((_, i) => Number((n.s || [])[i]) || 1);
      return { k: n.k, c, s, id: n.id || nid() };
    }
    return null;
  }

  function walk(n, fn, p = null, i = -1) {
    if (!n) return;
    fn(n, p, i);
    if (n.k !== "g") n.c.forEach((ch, j) => walk(ch, fn, n, j));
  }
  function locate(gid) {
    let out = null;
    walk(S.tree, (n, p, i) => { if (n.k === "g" && n.g === gid) out = { n, p, i }; });
    return out;
  }
  const treeGroups = (t = S.tree) => { const o = []; walk(t, (n) => { if (n.k === "g") o.push(n.g); }); return o; };

  /** Drop dead leaves, flatten a row inside a row, unwrap a box of one. */
  function normalize(n) {
    if (!n) return null;
    if (n.k === "g") return S.groups[n.g] ? n : null;
    const kids = [], sizes = [];
    n.c.forEach((ch, j) => {
      const m = normalize(ch);
      if (!m) return;
      const w = n.s[j] || 1;
      if (m.k === n.k) {
        const tot = sum(m.s) || 1;
        m.c.forEach((cc, q) => { kids.push(cc); sizes.push(w * m.s[q] / tot); });
      } else { kids.push(m); sizes.push(w); }
    });
    if (!kids.length) return null;
    if (kids.length === 1) return kids[0];
    n.c = kids; n.s = sizes;
    return n;
  }

  function replaceNode(loc, node) {
    if (!loc.p) S.tree = node;
    else loc.p.c[loc.i] = node;
  }

  /** Make the stored layout drawable whatever it says: dead tabs and groups
   *  drop out, every tiled group is in the tree exactly once, and the chart
   *  is always somewhere. */
  function sanitize() {
    for (const [id, i] of Object.entries(S.inst)) if (!TYPES.has(i.type)) delete S.inst[id];
    // an empty slot exists only to be filled: once something joins it, it goes
    for (const g of Object.values(S.groups)) {
      const full = g.tabs.filter((t) => S.inst[t] && S.inst[t].type !== SLOT);
      if (full.length && full.length < g.tabs.length) {
        g.tabs.filter((t) => S.inst[t] && S.inst[t].type === SLOT).forEach((t) => delete S.inst[t]);
        g.tabs = full;
        if (!full.includes(g.active)) g.active = full[0];
      }
    }
    S.inst[CHART] = S.inst[CHART] || { type: CHART, cfg: {} };
    const placed = new Set();
    for (const [gid, g] of Object.entries(S.groups)) {
      g.tabs = g.tabs.filter((t) => S.inst[t] && !placed.has(t));
      g.tabs.forEach((t) => placed.add(t));
      if (!g.tabs.includes(g.active)) g.active = g.tabs[0];
      if (!g.tabs.length) delete S.groups[gid];
    }
    S.floats = S.floats.filter((f) => S.groups[f.gid]);
    // a group in the tree twice, or in the tree AND floating, keeps one place
    const seen = new Set(S.floats.map((f) => f.gid));
    walk(S.tree, (n) => { if (n.k === "g") { if (seen.has(n.g)) n.g = "\u0000dead"; else seen.add(n.g); } });
    S.tree = normalize(S.tree);
    for (const gid of Object.keys(S.groups)) {
      if (!seen.has(gid)) { S.groups[gid].tabs.forEach((t) => placed.delete(t)); delete S.groups[gid]; }
    }
    if (!placed.has(CHART)) {
      const gid = newGroup([CHART]);
      S.tree = S.tree ? box("row", [leaf(gid), S.tree], [1, .4]) : leaf(gid);
      S.tree = normalize(S.tree);
      placed.add(CHART);
    }
    S.closed = S.closed.filter((c) => S.inst[c.id] && !placed.has(c.id));
    const kept = new Set([...placed, ...S.closed.map((c) => c.id)]);
    for (const id of Object.keys(S.inst)) if (!kept.has(id)) delete S.inst[id];
  }

  /** The workspace as data, for a shared setup: every widget, where it sits
   *  and its settings. What a widget keeps outside its settings (a note's
   *  text, a sheet's cells) is gathered by setups.js. */
  function exportState() {
    const s = JSON.parse(JSON.stringify(S));
    s.closed = []; s.focus = false; s.lock = false;
    for (const [id, i] of Object.entries(s.inst)) if (i.type === SLOT) delete s.inst[id];
    return s;
  }
  /** Put a shared workspace on screen. Every widget remounts, so none keeps
   *  showing what it held before. */
  function applyState(raw) {
    if (!raw || typeof raw !== "object" || !raw.inst) return false;
    closeSettings();
    for (const [id, api] of [...live]) {
      if (id === CHART) continue;
      try { if (api.hide) api.hide(); } catch {}
      live.delete(id); shown.delete(id); lastVisible.delete(id);
      const h = hosts.get(id);
      if (h) { h.remove(); hosts.delete(id); }
    }
    S = parse(raw);
    layout(false);
    return true;
  }

  let saveT = 0;
  const persist = () => { clearTimeout(saveT); saveT = setTimeout(persistNow, 160); };
  function persistNow() { clearTimeout(saveT); Store.set(KEY, S); }
  addEventListener("pagehide", persistNow);

  /* ── where things are ──────────────────────────────────────────────────── */

  const groupOf = (id) => Object.keys(S.groups).find((g) => S.groups[g].tabs.includes(id));
  const instancesOf = (type) => Object.keys(S.inst).filter((id) => S.inst[id].type === type);
  const placedOf = (type) => instancesOf(type).filter((id) => groupOf(id));
  const floatOf = (gid) => S.floats.find((f) => f.gid === gid);
  const isSlot = (gid) => { const g = S.groups[gid]; return !!g && g.tabs.length === 1 && S.inst[g.tabs[0]] && S.inst[g.tabs[0]].type === SLOT; };
  const isBare = (g) => g && g.tabs.length === 1 && (g.tabs[0] === CHART && S.lock
    || (S.inst[g.tabs[0]] && S.inst[g.tabs[0]].type === SLOT));
  const hasChart = (gid) => S.groups[gid] && S.groups[gid].tabs.includes(CHART);

  function detach(id) {
    const gid = groupOf(id);
    if (!gid) return null;
    const g = S.groups[gid];
    const at = g.tabs.indexOf(id);
    g.tabs.splice(at, 1);
    if (g.active === id) g.active = g.tabs[Math.max(0, at - 1)];
    const f = floatOf(gid);
    const out = { gid, rect: f ? { ...f } : null, float: !!f };
    if (!g.tabs.length) dropGroup(gid);
    return out;
  }

  function dropGroup(gid) {
    S.floats = S.floats.filter((f) => f.gid !== gid);
    const loc = locate(gid);
    if (loc) {
      if (!loc.p) S.tree = null;
      else { loc.p.c.splice(loc.i, 1); loc.p.s.splice(loc.i, 1); }
      S.tree = normalize(S.tree);
    }
    delete S.groups[gid];
    const node = groupEls.get(gid);
    if (node) { node.remove(); groupEls.delete(gid); }
  }

  function newGroup(tabs) {
    const gid = uid("g");
    S.groups[gid] = { tabs: [...tabs], active: tabs[0] };
    return gid;
  }

  const AXIS = { left: "row", right: "row", top: "col", bottom: "col" };
  const BEFORE = { left: true, top: true, right: false, bottom: false };

  /** Split tile `target` and put group `gid` on its `side`, taking `share`. */
  function splitAt(target, side, gid, share = .5) {
    const loc = locate(target);
    if (!loc) return addEdge(side, gid);
    const k = AXIS[side], before = BEFORE[side];
    if (loc.p && loc.p.k === k) {
      const w = loc.p.s[loc.i];
      loc.p.s[loc.i] = w * (1 - share);
      loc.p.c.splice(loc.i + (before ? 0 : 1), 0, leaf(gid));
      loc.p.s.splice(loc.i + (before ? 0 : 1), 0, w * share);
    } else {
      const pair = before ? [leaf(gid), loc.n] : [loc.n, leaf(gid)];
      replaceNode(loc, box(k, pair, before ? [share, 1 - share] : [1 - share, share]));
    }
  }

  /** A full-height (left/right) or full-width (top/bottom) tile at an edge. */
  function addEdge(side, gid, share = EDGE_SHARE) {
    const k = AXIS[side], before = BEFORE[side];
    if (!S.tree) { S.tree = leaf(gid); return; }
    if (S.tree.k === k) {
      const add = sum(S.tree.s) * share / (1 - share);
      if (before) { S.tree.c.unshift(leaf(gid)); S.tree.s.unshift(add); }
      else { S.tree.c.push(leaf(gid)); S.tree.s.push(add); }
    } else {
      S.tree = box(k, before ? [leaf(gid), S.tree] : [S.tree, leaf(gid)],
                   before ? [share, 1 - share] : [1 - share, share]);
    }
  }

  /** The group already sitting against a canvas edge, if one is (not the
   *  chart's): a widget opened "on the right" joins it rather than adding a
   *  fourth sliver beside three. */
  function edgeGroup(side) {
    const cv = canvas.getBoundingClientRect();
    let best = null;
    for (const gid of treeGroups()) {
      if (hasChart(gid)) continue;
      const n = groupEls.get(gid);
      if (!n || !n.isConnected) continue;
      const r = n.getBoundingClientRect();
      const touches = side === "left" ? r.left - cv.left < 3 : side === "right" ? cv.right - r.right < 3
        : side === "top" ? r.top - cv.top < 3 : cv.bottom - r.bottom < 3;
      const full = side === "left" || side === "right" ? r.height > cv.height * .45 : r.width > cv.width * .45;
      if (touches && full && (!best || r.height * r.width > best.a)) best = { gid, a: r.height * r.width };
    }
    return best && best.gid;
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
    const gid = newGroup([id]);
    if (t.kind === "split") {
      if (floatOf(t.gid)) { dropGroup(gid); return place(id, { kind: "tab", gid: t.gid }); }
      return splitAt(t.gid, t.side, gid, t.share || (hasChart(t.gid) ? .34 : .5));
    }
    // the chart moved to an edge takes most of the canvas, not a sidebar's share
    if (t.kind === "edge") return addEdge(t.side, gid, t.share || (id === CHART ? .62 : EDGE_SHARE));
    if (t.kind === "float") {
      S.floats.push({ gid, x: t.x, y: t.y, w: t.w || FLOAT_DEF.w, h: t.h || FLOAT_DEF.h });
      return;
    }
    // "zone": a side of the canvas — join what is there, else open a tile
    const side = t.zone === "left" || t.zone === "bottom" || t.zone === "top" ? t.zone : "right";
    const there = !t.newGroup && edgeGroup(side);
    if (there) { dropGroup(gid); return place(id, { kind: "tab", gid: there }); }
    addEdge(side, gid);
  }

  /* ══ DOM ═════════════════════════════════════════════════════════════════ */

  const canvas = document.createElement("div");
  canvas.className = "dk-canvas";
  charts.parentNode.insertBefore(canvas, charts);
  const floatLayer = document.createElement("div");
  floatLayer.className = "dk-floats";
  canvas.appendChild(floatLayer);

  const groupEls = new Map();        // gid → <section>
  const boxEls = new Map();          // tree node id → row/col <div>
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
          `aria-label="Move">${icon("grip")}</button>` +
        `<div class="dk-tabs" role="tablist"></div>` +
        `<div class="dk-tools"></div>` +
        `<div class="dk-acts"></div>` +
      `</div><div class="dk-body"></div>` +
      `<div class="dk-rz" data-act="resize" aria-hidden="true"></div>`;
    groupEls.set(gid, g);
    return g;
  }

  const titles = new Map();
  /** "Notes 2" when there are two of them. */
  function label(id) {
    const spec = TYPES.get(S.inst[id].type);
    const named = S.inst[id].cfg && S.inst[id].cfg.title;
    if (named) return { title: String(named).slice(0, 40), sub: titles.get(id) || "" };
    let title = spec.title;
    if (!spec.single) {
      const n = instancesOf(spec.type).indexOf(id);
      if (placedOf(spec.type).length > 1 && n > 0) title += ` ${n + 1}`;
    }
    return { title, sub: titles.get(id) || "" };
  }

  function paintHead(gid) {
    const g = S.groups[gid], node = groupEl(gid);
    const act = S.inst[g.active];
    const spec = act && TYPES.get(act.type);
    node.querySelector(".dk-tabs").innerHTML = g.tabs.map((id) => {
      const s = TYPES.get(S.inst[id].type), l = label(id), on = id === g.active;
      const dot = badges.has(S.inst[id].type) ? `<i class="dk-dot" aria-label="New"></i>` : "";
      return `<button type="button" class="dk-tab${on ? " on" : ""}" role="tab" ` +
        `aria-selected="${on}" data-inst="${id}" title="${esc(l.title + (l.sub ? " · " + l.sub : ""))}">` +
        `${icon(s.icon)}<span class="t">${esc(l.title)}</span>${dot}` +
        (l.sub ? `<span class="s">${esc(l.sub)}</span>` : "") +
        (g.tabs.length > 1 && id !== CHART ? `<span class="x" data-act="close-tab" role="button" ` +
          `aria-label="Close ${esc(l.title)}">${icon("x")}</span>` : "") +
        `</button>`;
    }).join("") + `<span class="dk-ink" aria-hidden="true"></span>`;
    const btn = (a, ic, t, extra = "") =>
      `<button type="button" class="dk-act" data-act="${a}" title="${t}" aria-label="${t}" ${extra}>${icon(ic)}</button>`;
    const cfg = act ? act.cfg : {};
    const fl = !!floatOf(gid);
    node.querySelector(".dk-acts").innerHTML =
      (spec && spec.linkable ? linkChip(g.active) : "") +
      (spec && g.active !== CHART && act.type !== SLOT ? btn("settings", "settings", "Settings") : "") +
      btn("more", "more", "More") +
      (!fl ? btn("max", maxed === gid ? "shrink" : "expand", maxed === gid ? "Restore" : "Maximize") : "") +
      (g.active !== CHART ? btn("close", "x", "Close") : "");
    node.classList.toggle("is-float", fl);
    node.classList.toggle("has-chart", g.tabs.includes(CHART));
    requestAnimationFrame(() => paintInk(node));
  }

  /** The chart's controls — interval, indicators, undo/redo, panes, settings —
   *  ride in the chart tile's tab bar, in the room beside its tab. When that
   *  bar is gone (the chart locked, focus, a phone) or another tab is in
   *  front, the same nodes go back to the page header: one set, moved, so
   *  every listener on them keeps working. */
  // held, not looked up: a layout can detach the tile they ride in, and a
  // detached node is no longer found by id
  let chartTools = null;
  function syncChartTools(vm) {
    const tools = chartTools || (chartTools = document.getElementById("chartTools"));
    const home = document.getElementById("chartToolsHome");
    if (!tools || !home) return;
    // the group the chart is drawn in THIS layout — in focus that is the
    // stage, not the group the chart is stored in
    const groups = (vm && vm.groups) || S.groups;
    const gid = Object.keys(groups).find((k) => groups[k].tabs.includes(CHART));
    const node = gid && groupEls.get(gid);
    const slot = node && canvas.contains(node) && !compact && !node.classList.contains("dk-bare")
      && groups[gid].active === CHART ? node.querySelector(".dk-tools") : null;
    if (slot) { if (tools.parentNode !== slot) slot.appendChild(tools); }
    else if (home.nextElementSibling !== tools) home.after(tools);
    for (const n of groupEls.values()) n.classList.toggle("has-tools", !!slot && n === node);
    document.body.classList.toggle("ct-docked", !!slot);
  }

  /** The active tab's underline is ONE element that slides between tabs. */
  function paintInk(node) {
    const on = node.querySelector(".dk-tab.on"), ink = node.querySelector(".dk-ink");
    if (!on || !ink) return;
    ink.style.width = on.offsetWidth - 16 + "px";
    ink.style.transform = `translateX(${on.offsetLeft + 8}px)`;
  }

  /* ══ layout ══════════════════════════════════════════════════════════════ */

  let compact = compactMq.matches;
  let maxed = null;
  const compactSheet = { active: null, shown: false };

  /** What is drawn: the stored layout, except in FOCUS (the chart alone) and
   *  on a PHONE (the chart, and under it one sheet holding every open widget
   *  as a tab — out only when asked for this visit). Neither touches the
   *  stored layout. */
  function viewModel() {
    if (!compact && !S.focus) return { tree: S.tree, floats: S.floats, groups: S.groups };
    const groups = { stage: { tabs: [CHART], active: CHART } };
    const stage = { k: "g", g: "stage", id: "vstage" };
    if (S.focus || !compact) return { tree: stage, floats: [], groups };
    const all = [...treeGroups(), ...S.floats.map((f) => f.gid)]
      .flatMap((gid) => S.groups[gid].tabs).filter((t) => t !== CHART);
    if (!all.length || !compactSheet.shown) return { tree: stage, floats: [], groups };
    const act = Object.values(S.groups).map((g) => g.active).find((a) => all.includes(a)) || all[0];
    compactSheet.active = compactSheet.active && all.includes(compactSheet.active) ? compactSheet.active : act;
    groups.sheet = { tabs: all, active: compactSheet.active };
    const portrait = portraitMq.matches;
    return { tree: { k: portrait ? "col" : "row", id: "vcompact", s: portrait ? [1.15, 1] : [1.5, 1],
                     c: [stage, { k: "g", g: "sheet", id: "vsheet" }] },
             floats: [], groups };
  }

  function renderNode(n) {
    if (n.k === "g") return groupEl(n.g);
    let b = boxEls.get(n.id);
    if (!b) {
      b = document.createElement("div");
      boxEls.set(n.id, b);
    }
    b.className = `dk-box dk-${n.k}`;
    const want = [];
    n.c.forEach((ch, j) => {
      if (j) {
        const gut = b.querySelector(`:scope > .dk-gut[data-j="${j}"]`) || document.createElement("div");
        gut.className = `dk-gut ${n.k === "row" ? "v" : "h"}`;
        gut.dataset.node = n.id; gut.dataset.j = j;
        gut.setAttribute("role", "separator");
        want.push(gut);
      }
      const e = renderNode(ch);
      e.style.flex = `${n.s[j]} 1 0`;
      want.push(e);
    });
    if (n.k === "row") rowMins(n, want.filter((e) => !e.classList.contains("dk-gut")));
    want.forEach((w, i) => { if (b.children[i] !== w) move(b, w, b.children[i] || null); });
    while (b.children.length > want.length) b.lastElementChild.remove();
    return b;
  }

  /* How narrow each child of a row may get. A widget asks for the width it
   * can be read at (spec.minW); the chart asks for more. But every ask is
   * capped at a fair share of the row — the chart's side may claim half, the
   * rest split what is left — so the asks can never add up to more than the
   * row and push the chart off the screen on a small display. */
  const CHART_MIN = 420, TILE_MIN = 220;
  function minOf(n) {
    if (n.k === "g") {
      const g = S.groups[n.g];
      if (!g) return TILE_MIN;
      if (g.tabs.includes(CHART)) return CHART_MIN;
      return Math.max(TILE_MIN, ...g.tabs.map((t) => (TYPES.get(S.inst[t] ? S.inst[t].type : "") || {}).minW || 0));
    }
    const m = n.c.map(minOf);
    return n.k === "row" ? m.reduce((a, b) => a + b, 0) : Math.max(...m);
  }
  function rowMins(n, els) {
    const withChart = n.c.map((ch) => { let f = false; walk(ch, (x) => { if (x.k === "g" && S.groups[x.g] && S.groups[x.g].tabs.includes(CHART)) f = true; }); return f; });
    const others = withChart.filter((f) => !f).length;
    const hasChart = withChart.some(Boolean);
    const chartAsk = `min(${CHART_MIN}px, ${others ? 50 : 100}%)`;
    const rest = `calc((100% - ${hasChart ? chartAsk : "0px"}) / ${Math.max(1, others)})`;
    n.c.forEach((ch, j) => {
      els[j].style.minWidth = compact ? "" : withChart[j] ? chartAsk : `min(${minOf(ch)}px, ${rest})`;
    });
  }

  function layout(animate) {
    sanitize();
    const before = animate ? snapshot() : null;
    const vm = viewModel();
    const stored = S.groups;
    if (vm.groups !== S.groups) S.groups = { ...S.groups, ...vm.groups };
    const visibleNow = new Set();

    const root = vm.tree ? renderNode(vm.tree) : null;
    if (root) {
      root.style.flex = "1 1 0";
      if (canvas.firstElementChild !== root) move(canvas, root, canvas.firstElementChild);
    }
    for (const c of [...canvas.children]) if (c !== root && c !== floatLayer && !c.classList.contains("dk-hub") && !c.classList.contains("dk-pull")) c.remove();
    // forget boxes no longer in the tree
    const live_ = new Set(); walk(vm.tree, (n) => live_.add(n.id));
    for (const id of [...boxEls.keys()]) if (!live_.has(id)) boxEls.delete(id);

    const fl = vm.floats;
    fl.forEach((f) => { const n = groupEl(f.gid); n.style.flex = ""; if (n.parentNode !== floatLayer) move(floatLayer, n, null); });
    [...floatLayer.children].forEach((n) => { if (!fl.some((f) => f.gid === n.dataset.gid)) n.remove(); });
    floatLayer.classList.toggle("on", fl.length > 0);

    for (const gid of Object.keys(vm.groups)) {
      const g = S.groups[gid], node = groupEl(gid), body = node.querySelector(".dk-body");
      for (const id of g.tabs) {
        const h = hostFor(id);
        if (h.parentNode !== body) move(body, h, null);
        const on = id === g.active;
        h.hidden = !on;
        if (on) visibleNow.add(id);
      }
      node.classList.toggle("dk-bare", isBare(g) || gid === "stage" && (S.lock || compact || S.focus));
      paintHead(gid);
    }
    for (const [id, h] of hosts) {
      if (!Object.keys(vm.groups).some((gid) => S.groups[gid].tabs.includes(id)) && h.parentNode) h.remove();
    }
    if (vm.groups !== stored) S.groups = stored;

    // settings belong to a widget on screen: closed, moved away or hidden, they go
    if (setsOpen && (!S.inst[setsOpen.id] || !visibleNow.has(setsOpen.id) || !setsOpen.el.isConnected
        || setsOpen.el.parentNode !== groupEls.get(groupOf(setsOpen.id)))) closeSettings();
    document.body.classList.toggle("dk-focus", !!S.focus);
    document.body.classList.toggle("dk-compact", compact);
    document.body.classList.toggle("dk-chart-locked", !!S.lock);
    syncVisibility(visibleNow);
    syncChartTools(vm);
    placeFloats();
    requestAnimationFrame(syncPulls);
    syncDense();
    syncRail();
    if (before) play(before);
    persist();
  }

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

  /** The OHLC figures stand down when the chart is too narrow for one line. */
  function syncDense() {
    requestAnimationFrame(() => document.body.classList.toggle("two-columns",
      !compact && charts.clientWidth > 0 && charts.clientWidth < 640));
  }
  addEventListener("resize", () => { unmax(); placeFloats(); syncDense(); });
  if (window.ResizeObserver) new ResizeObserver(() => { placeFloats(); syncDense(); }).observe(canvas);

  function placeFloats() {
    const W = floatLayer.clientWidth, H = floatLayer.clientHeight;
    if (!W || !H) return;
    for (const f of S.floats) {
      const node = groupEls.get(f.gid);
      if (!node || maxed === f.gid) continue;
      f.w = clamp(f.w, 240, Math.max(240, W - 16));
      f.h = clamp(f.h, 160, Math.max(160, H - 16));
      f.x = clamp(f.x, 8, Math.max(8, W - f.w - 8));
      f.y = clamp(f.y, 8, Math.max(8, H - f.h - 8));
      Object.assign(node.style, { left: f.x + "px", top: f.y + "px",
                                  width: f.w + "px", height: f.h + "px" });
    }
  }

  /* ── FLIP: tiles that moved glide from where they were ─────────────────── */
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
        n.animate([{ opacity: 0, transform: "scale(.98)" }, { opacity: 1, transform: "none" }],
                  { duration: 200, easing: "cubic-bezier(.22,1,.36,1)" });
        continue;
      }
      const dx = a.left - b.left, dy = a.top - b.top;
      const sx = a.width / b.width, sy = a.height / b.height;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1 && Math.abs(sx - 1) < .01 && Math.abs(sy - 1) < .01) continue;
      n.animate([{ transform: `translate(${dx}px, ${dy}px) scale(${sx}, ${sy})`, transformOrigin: "0 0" },
                 { transform: "none", transformOrigin: "0 0" }],
                { duration: 240, easing: "cubic-bezier(.22,1,.36,1)" });
    }
  }

  /* ══ opening and closing ═════════════════════════════════════════════════ */

  /** Open a widget type. A single-instance widget comes back where it was; a
   *  multi-instance one reopens its most recently closed copy unless `fresh`
   *  asks for a new one, which lands beside the copy already open. */
  function open(type, where, opts = {}) {
    const spec = TYPES.get(type);
    if (!spec || type === CHART) return null;
    if (S.focus) S.focus = false;
    let id = !opts.fresh ? placedOf(type)[0] : null;
    if (id && !where) {
      S.groups[groupOf(id)].active = id;
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
        if (!(spec.single && S.inst[id])) {
          S.inst[id] = { type, cfg: { ...(spec.defaults || {}), ...(opts.cfg || {}) } };
        }
      }
    } else {
      detach(id);
    }
    let target = where;
    if (!target && opts.fresh) {
      const sib = placedOf(type).find((x) => x !== id);
      const sg = sib && groupOf(sib);
      if (sg && !floatOf(sg)) target = { kind: "split", gid: sg, side: "bottom" };
    }
    // A widget that always opens in its own zone (`zoneOnly`) does not go
    // back to wherever it was last docked; a floating window is still kept.
    if (remembered && spec.zoneOnly && !remembered.float) remembered = null;
    if (!target && remembered) {
      target = remembered.float && remembered.rect ? { kind: "float", ...remembered.rect }
        : S.groups[remembered.gid] ? { kind: "tab", gid: remembered.gid } : null;
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
    if (!S.inst[id] || id === CHART) return;
    if (S.inst[id].type === SLOT) { detach(id); delete S.inst[id]; layout(true); return; }
    const at = detach(id);
    if (at) S.closed.push({ id, ...at });
    S.closed = S.closed.slice(-24);
    if (maxed && !S.groups[maxed]) unmax();
    layout(true);
    if (compact) phoneRestore();
  }

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
    const gid = groupOf(id);
    place(copy, floatOf(gid) ? { kind: "tab", gid } : { kind: "split", gid, side: "bottom" });
    layout(true);
  }

  /** Menu moves: a whole tile to an edge of the canvas, or into a window. */
  function moveTo(id, where) {
    const gid = groupOf(id);
    const rect = gid && groupEls.get(gid) ? groupEls.get(gid).getBoundingClientRect() : null;
    const g = gid && S.groups[gid];
    // the chart travels with its whole tile; a widget travels alone
    const tabs = id === CHART && g ? [...g.tabs] : [id];
    const active = g ? g.active : id;
    tabs.forEach((t) => detach(t));
    if (where === "float") {
      const c = floatLayer.getBoundingClientRect();
      const x = rect ? rect.left - c.left : c.width / 2 - FLOAT_DEF.w / 2;
      place(tabs[0], { kind: "float", x: clamp(x, 16, c.width - FLOAT_DEF.w - 16), y: 24 });
    } else {
      place(tabs[0], { kind: "edge", side: where });
    }
    const ng = groupOf(tabs[0]);
    tabs.slice(1).forEach((t) => S.groups[ng].tabs.push(t));
    S.groups[ng].active = active;
    layout(true);
  }

  function setLock(on) {
    S.lock = on == null ? !S.lock : !!on;
    if (S.lock) {
      // locked, the chart is a tile of its own
      const gid = groupOf(CHART);
      if (!gid || S.groups[gid].tabs.length > 1 || floatOf(gid)) {
        detach(CHART);
        const g = newGroup([CHART]);
        S.tree = S.tree ? box("row", [leaf(g), S.tree], [1, .5]) : leaf(g);
      }
    }
    layout(true);
    toast(S.lock ? "The chart is locked in place." : "The chart can be moved by its handle, like any widget.");
  }

  /* ── the phone: a widget takes the conversation's slot ─────────────────── */
  let restoreChat = false;
  const chatOpen = () => el("chatPanel") && !el("chatPanel").classList.contains("hidden");
  function phoneMakeRoom() {
    if (!compactMq.matches || !portraitMq.matches) return;
    if (chatOpen() && el("chatToggle")) { restoreChat = true; el("chatToggle").click(); }
  }
  function phoneRestore() {
    if (compactSheet.shown && lastVisible.size > 1) return;
    if (restoreChat && !chatOpen() && el("chatToggle")) { restoreChat = false; el("chatToggle").click(); }
  }
  if (el("chatToggle")) {
    el("chatToggle").addEventListener("click", () => {
      setTimeout(() => {
        if (compact && compactMq.matches && portraitMq.matches && chatOpen() && compactSheet.shown) {
          restoreChat = false;
          compactSheet.shown = false;
          layout(false);
        }
        placeFloats(); syncDense();
      }, 0);
    });
  }
  compactMq.addEventListener("change", () => { compact = compactMq.matches; unmax(); layout(false); });

  /* ══ focus, fullscreen, maximise ════════════════════════════════════════ */

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
  document.addEventListener("fullscreenchange", () => { syncHeader(); setTimeout(placeFloats, 60); });

  function max(gid) {
    if (maxed === gid) return unmax();
    unmax();
    const node = groupEls.get(gid);
    if (!node) return;
    const from = node.getBoundingClientRect();
    maxed = gid;
    const ws = canvas.getBoundingClientRect();
    Object.assign(node.style, { left: ws.left + "px", top: ws.top + "px",
                                width: ws.width + "px", height: ws.height + "px" });
    node.classList.add("dk-maxed");
    paintHead(gid);
    if (!reduced.matches) {
      const to = node.getBoundingClientRect();
      node.animate([{ transform: `translate(${from.left - to.left}px, ${from.top - to.top}px) ` +
                                 `scale(${from.width / to.width}, ${from.height / to.height})`, transformOrigin: "0 0" },
                    { transform: "none", transformOrigin: "0 0" }],
                   { duration: 240, easing: "cubic-bezier(.22,1,.36,1)" });
    }
  }
  function unmax() {
    if (!maxed) return;
    const n = groupEls.get(maxed);
    maxed = null;
    if (n) {
      n.classList.remove("dk-maxed");
      n.style.left = n.style.top = n.style.width = n.style.height = "";
      if (n.classList.contains("is-float")) placeFloats();
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
    const api = live.get(id) || {};
    const chart = id === CHART;
    const fl = !!floatOf(groupOf(id));
    menu(anchor, [
      { head: label(id).title },
      ...(api.ask ? [{ id: "ask", label: "Ask in chat", icon: "chat" }] : []),
      ...(!spec.single && !chart ? [{ id: "new", label: `New ${spec.title.toLowerCase()}`, icon: "plus" },
                                   { id: "dup", label: "Duplicate", icon: "copy" }] : []),
      { id: "settings", label: chart ? "Chart settings" : "Settings", icon: "settings" },
      // a narrow tile hides its maximize button, so the menu always offers it
      ...(compact || fl ? [] : [{ id: "max", label: maxed === groupOf(id) ? "Restore" : "Maximize",
                                  icon: maxed === groupOf(id) ? "shrink" : "expand", hint: "Double-click tab" }]),
      ...(compact || (chart && S.lock) ? [] : [{ sep: true }, { head: "Move to" },
        { id: "mv:left", label: "Left side", icon: "panelLeft" },
        { id: "mv:right", label: "Right side", icon: "panelRight" },
        { id: "mv:top", label: "Top", icon: "panelTop" },
        { id: "mv:bottom", label: "Bottom", icon: "panelBottom" },
        { id: "mv:float", label: "Floating window", icon: "float", on: fl }]),
      ...(chart ? [{ sep: true }, { id: "lock", label: S.lock ? "Unlock chart" : "Lock chart in place",
                                    icon: S.lock ? "unlock" : "lock" }]
                : [{ sep: true }, { id: "close", label: "Close", icon: "x" }]),
    ], (pick) => {
      if (pick === "ask") return askFrom(id);
      if (pick === "new") return open(spec.type, null, { fresh: true });
      if (pick === "dup") return duplicate(id);
      if (pick === "settings") return setTimeout(() => settingsPanel(id), 0);
      if (pick === "close") return close(id);
      if (pick === "max") return max(groupOf(id));
      if (pick === "lock") return setLock();
      if (pick.startsWith("mv:")) return moveTo(id, pick.slice(3));
    });
  }

  function askFrom(id) {
    const api = live.get(id) || {};
    askWith(id, api.ask && api.ask());
  }
  /** A widget's "Ask in chat". A widget hands back { sub, context, question }
   *  and the chat wears it as a tag — the widget's icon and name, one line of
   *  what it holds — with the snapshot riding on the message rather than
   *  pasted into the box. A plain string still drafts it as text. */
  function askWith(id, got) {
    if (!got) return toast("There is nothing in this widget to ask about yet.");
    if (typeof got === "string") return window.Chat && window.Chat.compose && window.Chat.compose(got);
    const spec = TYPES.get(S.inst[id].type);
    if (window.Chat && window.Chat.attach) {
      window.Chat.attach({ type: spec.type, icon: spec.icon, title: label(id).title, ...got });
    }
  }

  /* ══ widget settings ═════════════════════════════════════════════════════
   * A panel that slides over the widget's own body — frosted, so the change
   * shows behind it as it is made. Every widget gets a NAME and, where it
   * follows a symbol, its LINK; the rest is the widget's own, declared in
   * spec.settings:
   *
   *   { section: "Display" }                          a heading
   *   { key, label, kind, def, options, hint, when,   a control
   *     min, max, step, unit, placeholder, get, set, run }
   *
   * kind: seg (default when there are options) · toggle · select · chips
   * (several of the options) · range · text · action (a button) · note.
   * `options` may be a function, `when(cfg)` hides a row that does not
   * apply, and `get`/`set` bind a row to state the widget keeps itself (the
   * watchlist's columns live with its lists, not in the dock). Every change
   * is applied as it is made; Reset puts back the defaults, and Apply to all
   * copies them to every other widget of the same kind. */
  let setsOpen = null;
  function closeSettings() {
    if (!setsOpen) return;
    const el_ = setsOpen.el;
    setsOpen = null;
    el_.classList.remove("on");
    setTimeout(() => el_.remove(), 200);
  }

  function settingsPanel(id) {
    if (id === CHART) { const b = el("settingsBtn"); return b && b.click(); }
    if (setsOpen && setsOpen.id === id) return closeSettings();
    closeSettings();
    closePop();
    const gid = groupOf(id);
    if (!gid) return;
    if (S.groups[gid].active !== id) { S.groups[gid].active = id; layout(false); }
    const node = groupEls.get(gid);
    if (!node) return;
    const p = document.createElement("div");
    p.className = "dk-sets";
    p.setAttribute("role", "dialog");
    p.setAttribute("aria-label", "Widget settings");
    node.appendChild(p);
    setsOpen = { id, el: p };
    paintSettings();
    p.addEventListener("click", onSetsClick);
    p.addEventListener("change", onSetsChange);
    p.addEventListener("input", onSetsInput);
    p.addEventListener("keydown", (e) => { if (e.key !== "Escape") e.stopPropagation(); });
    requestAnimationFrame(() => p.classList.add("on"));
  }

  const optsOf = (s, cfg) => (typeof s.options === "function" ? s.options(cfg) : s.options) || [];
  const valOf = (s, cfg) => s.get ? s.get(cfg) : cfg[s.key] !== undefined ? cfg[s.key] : s.def;

  function paintSettings() {
    if (!setsOpen) return;
    const { id, el: p } = setsOpen;
    if (!S.inst[id]) return closeSettings();
    const spec = TYPES.get(S.inst[id].type), cfg = S.inst[id].cfg;
    const keep = p.querySelector(".ds-body") ? p.querySelector(".ds-body").scrollTop : 0;
    const row = (s, i) => {
      if (s.section) return `<div class="ds-sec">${esc(s.section)}</div>`;
      if (s.when && !s.when(cfg)) return "";
      const v = valOf(s, cfg);
      const kind = s.kind || (s.options ? "seg" : "toggle");
      const lab = `<span class="ds-l">${esc(s.label || "")}${s.hint ? `<em>${esc(s.hint)}</em>` : ""}</span>`;
      const at = `data-i="${i}"`;
      if (kind === "note") return `<p class="ds-note">${esc(s.label)}</p>`;
      if (kind === "toggle") {
        return `<label class="ds-row">${lab}<input type="checkbox" class="dk-switch" ${at} ${v ? "checked" : ""}></label>`;
      }
      if (kind === "action") {
        return `<div class="ds-row">${lab}<button type="button" class="ds-btn" ${at}>${esc(s.button || "Do it")}</button></div>`;
      }
      if (kind === "select") {
        return `<label class="ds-row">${lab}<select class="ds-select" ${at}>` +
          optsOf(s, cfg).map((o) => `<option value="${esc(o.v)}" ${String(o.v) === String(v) ? "selected" : ""}>${esc(o.label)}</option>`).join("") +
          `</select></label>`;
      }
      if (kind === "range") {
        return `<label class="ds-row ds-col">${lab}<span class="ds-range"><input type="range" ${at} min="${s.min}" max="${s.max}" step="${s.step || 1}" value="${esc(v)}">` +
          `<output>${esc(v)}${esc(s.unit || "")}</output></span></label>`;
      }
      if (kind === "text") {
        return `<label class="ds-row ds-col">${lab}<input type="text" class="ds-text" ${at} value="${esc(v || "")}" ` +
          `placeholder="${esc(s.placeholder || "")}" spellcheck="false" autocomplete="off"></label>`;
      }
      if (kind === "chips") {
        const on = new Set((v || []).map(String));
        return `<div class="ds-row ds-col">${lab}<div class="ds-chips" ${at}>` +
          optsOf(s, cfg).map((o) => `<button type="button" class="${on.has(String(o.v)) ? "on" : ""}" data-v="${esc(o.v)}">${esc(o.label)}</button>`).join("") +
          `</div></div>`;
      }
      const opts = optsOf(s, cfg);
      return `<div class="ds-row${opts.length > 3 ? " ds-col" : ""}">${lab}<div class="ds-seg" ${at}>` +
        opts.map((o) => `<button type="button" class="${String(o.v) === String(v) ? "on" : ""}" data-v="${esc(o.v)}">${esc(o.label)}</button>`).join("") +
        `</div></div>`;
    };
    const l = linkOf(id);
    const linkRow = spec.linkable
      ? `<div class="ds-sec">Link</div><div class="ds-links">` +
        `<button type="button" class="${l === "chart" ? "on" : ""}" data-link="chart" title="Follow the chart (${esc(pageSymbol())})">${icon("link")}<span>Chart</span></button>` +
        LINK_IDS.map((g) => `<button type="button" class="g${g}${l === g ? " on" : ""}" data-link="${g}" ` +
          `title="Group ${g}${S.links[g] && S.links[g].sym ? " · " + esc(S.links[g].sym) : ""}"><i>${g}</i></button>`).join("") +
        `<button type="button" class="${l === "pin" ? "on" : ""}" data-link="pin" title="Pin a symbol">${icon("pin")}<span>${l === "pin" ? esc(symbolOf(id)) : "Pin"}</span></button>` +
        `</div><p class="ds-note">${l === "chart" ? `Follows the chart: ${esc(pageSymbol())}.`
          : l === "pin" ? `Stays on ${esc(symbolOf(id))} whatever the chart shows.`
          : `Shares a symbol${spec.type === "chart" ? " and an interval" : ""} with every group-${l} widget · now ${esc(symbolOf(id))}.`}</p>`
      : "";
    const others = placedOf(spec.type).filter((x) => x !== id).length;
    p.innerHTML =
      `<div class="ds-head"><span class="ds-ic">${icon(spec.icon)}</span><b>${esc(label(id).title)}</b>` +
        `<button type="button" class="ds-x" data-ds="close" title="Close (Esc)" aria-label="Close settings">${icon("x")}</button></div>` +
      `<div class="ds-body">` +
        linkRow +
        (spec.settings || []).map(row).join("") +
        `<div class="ds-sec">Widget</div>` +
        `<label class="ds-row ds-col"><span class="ds-l">Name</span><input type="text" class="ds-text" data-name="1" ` +
          `value="${esc(cfg.title || "")}" placeholder="${esc(spec.title)}" maxlength="40" spellcheck="false" autocomplete="off"></label>` +
      `</div>` +
      `<div class="ds-foot">` +
        `<button type="button" class="ds-btn ghost" data-ds="reset">${icon("rotateCw", "xs")}Reset</button>` +
        (others ? `<button type="button" class="ds-btn ghost" data-ds="all">Apply to all ${others + 1}</button>` : "") +
        `<button type="button" class="ds-btn" data-ds="close">Done</button>` +
      `</div>`;
    p.querySelector(".ds-body").scrollTop = keep;
  }

  function setOne(i, v) {
    const { id } = setsOpen;
    const spec = TYPES.get(S.inst[id].type), s = spec.settings[i];
    if (s.set) { s.set(v, S.inst[id].cfg); setCfg(id, {}); }
    else setCfg(id, { [s.key]: v });
  }
  function onSetsClick(e) {
    const { id } = setsOpen || {};
    if (!id) return;
    e.stopPropagation();
    const spec = TYPES.get(S.inst[id].type), cfg = S.inst[id].cfg;
    const ds = e.target.closest("[data-ds]");
    if (ds) {
      const a = ds.dataset.ds;
      if (a === "close") return closeSettings();
      if (a === "reset") {
        const patch = { title: null };
        for (const s of spec.settings || []) {
          if (!s.key || s.def === undefined || s.kind === "action") continue;
          if (s.set) s.set(Array.isArray(s.def) ? [...s.def] : s.def, cfg);
          else patch[s.key] = Array.isArray(s.def) ? [...s.def] : s.def;
        }
        setCfg(id, patch);
        layout(false);
        toast(`${spec.title} is back to its defaults.`);
        return paintSettings();
      }
      if (a === "all") {
        const patch = {};
        for (const s of spec.settings || []) if (s.key && !s.set && cfg[s.key] !== undefined) patch[s.key] = JSON.parse(JSON.stringify(cfg[s.key]));
        const others = placedOf(spec.type).filter((x) => x !== id);
        others.forEach((x) => setCfg(x, patch));
        return toast(`Applied to ${others.length} other ${spec.title.toLowerCase()} widget${others.length === 1 ? "" : "s"}.`);
      }
    }
    const lk = e.target.closest("[data-link]");
    if (lk) {
      const v = lk.dataset.link;
      if (v !== "pin") { setLinkOf(id, v); return paintSettings(); }
      if (typeof Universe !== "undefined") {
        Universe.open({ anchor: lk, current: symbolOf(id), onPick: (sym) => { setLinkOf(id, "pin", sym); paintSettings(); } });
      }
      return;
    }
    const seg = e.target.closest(".ds-seg button, .ds-chips button");
    if (seg) {
      const box_ = seg.parentNode, i = +box_.dataset.i, s = spec.settings[i];
      const num = (x) => typeof (Array.isArray(s.def) ? s.def[0] : s.def) === "number" ? Number(x) : x;
      if (box_.classList.contains("ds-chips")) {
        const cur = new Set((valOf(s, cfg) || []).map(String));
        cur.has(seg.dataset.v) ? cur.delete(seg.dataset.v) : cur.add(seg.dataset.v);
        if (cur.size < (s.min || 0)) return toast(`Keep at least ${s.min} on.`);
        // keep the options' own order, not the order they were clicked in
        setOne(i, optsOf(s, cfg).map((o) => String(o.v)).filter((v) => cur.has(v)).map(num));
      } else setOne(i, num(seg.dataset.v));
      return paintSettings();
    }
    const act = e.target.closest(".ds-btn[data-i]");
    if (act) { const s = spec.settings[+act.dataset.i]; if (s.run) Promise.resolve(s.run(cfg)).then(paintSettings); }
  }
  function onSetsChange(e) {
    const t = e.target;
    if (!setsOpen) return;
    if (t.matches('input[type="checkbox"][data-i]')) { setOne(+t.dataset.i, t.checked); return paintSettings(); }
    if (t.matches("select[data-i]")) {
      const s = TYPES.get(S.inst[setsOpen.id].type).settings[+t.dataset.i];
      setOne(+t.dataset.i, typeof s.def === "number" ? Number(t.value) : t.value);
      return paintSettings();
    }
    if (t.matches('input[type="range"][data-i]')) return setOne(+t.dataset.i, Number(t.value));
  }
  let textT = 0;
  function onSetsInput(e) {
    const t = e.target;
    if (!setsOpen) return;
    if (t.matches('input[type="range"][data-i]')) {
      const s = TYPES.get(S.inst[setsOpen.id].type).settings[+t.dataset.i];
      t.nextElementSibling.textContent = t.value + (s.unit || "");
      return;
    }
    if (!t.matches(".ds-text")) return;
    clearTimeout(textT);
    const id = setsOpen.id;
    textT = setTimeout(() => {
      if (t.dataset.name) { setCfg(id, { title: t.value.trim() || null }); const g = groupOf(id); if (g) paintHead(g); }
      else setOne(+t.dataset.i, t.value);
    }, 300);
  }

  function setCfg(id, patch) {
    const i = S.inst[id];
    if (!i) return;
    Object.assign(i.cfg, patch);
    // a widget's own "pin this instrument" menu is a link change too
    if ("pin" in patch && !("link" in patch)) i.cfg.link = patch.pin ? "pin" : "chart";
    persist();
    const api = live.get(id);
    if (api && api.config) try { api.config(i.cfg, patch); } catch (e) { console.error(e); }
    const gid = groupOf(id);
    if (gid && groupEls.has(gid)) paintHead(gid);
  }

  /* ══ link groups ═════════════════════════════════════════════════════════
   * TakeProfit's best idea, kept small: a widget follows the CHART (the
   * page's symbol), joins one of four coloured GROUPS, or is PINNED to a
   * symbol of its own. Widgets in a group share a symbol and an interval:
   * click a row in a watchlist in group 2 and every group-2 widget — an
   * extra chart, the order book, the financials — moves to it, without the
   * page navigating. The colour is the only meaning colour carries here. */
  const linkOf = (id) => {
    const c = (S.inst[id] && S.inst[id].cfg) || {};
    return c.link && (c.link === "chart" || c.link === "pin" || LINK_IDS.includes(c.link)) ? c.link : c.pin ? "pin" : "chart";
  };
  function symbolOf(id) {
    const c = (S.inst[id] && S.inst[id].cfg) || {}, l = linkOf(id);
    if (l === "pin") return c.pin || pageSymbol();
    if (LINK_IDS.includes(l)) return (S.links[l] && S.links[l].sym) || pageSymbol();
    return pageSymbol();
  }
  const members = (g) => Object.keys(S.inst).filter((iid) => linkOf(iid) === g && groupOf(iid));

  /** Tell a widget its symbol (or interval) changed under it. */
  function notify(iid, patch) {
    const api = live.get(iid);
    if (api && api.config) try { api.config(S.inst[iid].cfg, patch); } catch (e) { console.error("[dock] config", e); }
    const gid = groupOf(iid);
    if (gid && groupEls.has(gid)) paintHead(gid);
  }
  function setLink(g, patch, from) {
    S.links[g] = { ...(S.links[g] || {}), ...patch };
    persist();
    const out = {};
    if ("sym" in patch) out.symbol = patch.sym;
    if ("iv" in patch) out.iv = patch.iv;
    for (const iid of members(g)) if (iid !== from) notify(iid, out);
  }
  /** A row clicked in a list: the group's symbol if the list is in one,
   *  else the chart's (a navigation, or a selected pane). */
  function pickSymbol(id, sym, how) {
    const s = String(sym || "").trim().toUpperCase();
    const l = linkOf(id);
    if (s && LINK_IDS.includes(l) && how !== "beside") {
      setLink(l, { sym: s });
      for (const iid of members(l)) flash(iid);
      return;
    }
    openSymbol(s, how);
  }
  /** Move a widget to a link: the chart, a group (adopting the group's
   *  symbol, or giving the group its own if the group is empty), or a pin. */
  function setLinkOf(id, l, pin) {
    const before = symbolOf(id);
    if (LINK_IDS.includes(l) && !(S.links[l] && S.links[l].sym)) S.links[l] = { ...(S.links[l] || {}), sym: before };
    Object.assign(S.inst[id].cfg, { link: l, pin: l === "pin" ? (pin || before) : null });
    persist();
    notify(id, { symbol: symbolOf(id), link: l, pin: S.inst[id].cfg.pin });
  }

  function linkChip(id) {
    const l = linkOf(id), sym = symbolOf(id);
    if (LINK_IDS.includes(l)) {
      return `<button type="button" class="dk-act dk-link g${l}" data-act="link" title="Colour group ${l} · showing ${esc(sym)}. Widgets in this colour share a symbol." ` +
        `aria-label="Link group ${l}">${l}</button>`;
    }
    return `<button type="button" class="dk-act dk-link" data-act="link" data-pinned="${l === "pin" ? 1 : ""}" ` +
      `title="${l === "pin" ? `Pinned to ${esc(sym)}` : `Following the chart (${esc(sym)})`} — click to link" ` +
      `aria-label="Link">${icon(l === "pin" ? "pin" : "link")}</button>`;
  }

  function linkMenu(anchor, id) {
    const l = linkOf(id);
    const html = `<div class="head">Which symbol this shows</div>` +
      `<p class="dk-linkhelp">Widgets with the same colour share a symbol: pick a stock in one and the others in that colour switch to it.</p>` +
      `<div class="item${l === "chart" ? " on" : ""}" data-pick="chart"><span class="lead">${icon("link", "xs")}Follow the main chart</span>` +
        `<span class="sc">${esc(pageSymbol())}</span></div>` +
      LINK_IDS.map((g) => {
        const n = members(g).filter((x) => x !== id).length, sym = S.links[g] && S.links[g].sym;
        return `<div class="item${l === g ? " on" : ""}" data-pick="${g}"><span class="lead"><i class="dk-swatch g${g}">${g}</i>Group ${g}</span>` +
          `<span class="sc">${sym ? esc(sym) : ""}${n ? ` · ${n} more` : ""}</span></div>`;
      }).join("") +
      `<div class="sep"></div>` +
      `<div class="item${l === "pin" ? " on" : ""}" data-pick="pin"><span class="lead">${icon("pin", "xs")}${l === "pin" ? "Pinned — change…" : "Pin a symbol…"}</span>` +
        `<span class="sc">${l === "pin" ? esc(symbolOf(id)) : ""}</span></div>`;
    menu(anchor, html, (pick) => {
      if (pick !== "pin") return setLinkOf(id, pick);
      if (typeof Universe !== "undefined") {
        setTimeout(() => Universe.open({ anchor, current: symbolOf(id), onPick: (s) => setLinkOf(id, "pin", s) }), 0);
      }
    }, "dk-linkmenu");
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
    toastT = setTimeout(() => toastEl.classList.remove("on"), 2800);
  }

  /* ══ the widget's handle on the dock ════════════════════════════════════ */

  function ctxFor(id) {
    return {
      id,
      get cfg() { return S.inst[id] ? S.inst[id].cfg : {}; },
      setCfg: (patch) => setCfg(id, patch),
      symbol: () => symbolOf(id),
      link: () => linkOf(id),
      /** A list's row was chosen: the group's symbol, or the chart's. */
      pick: (sym, how) => pickSymbol(id, sym, how),
      /** The interval a group shares (null outside a group). */
      linkedIv: () => { const l = linkOf(id); return LINK_IDS.includes(l) && S.links[l] ? S.links[l].iv || null : null; },
      setLinkedIv: (iv) => { const l = linkOf(id); if (LINK_IDS.includes(l)) setLink(l, { iv }, id); },
      pageSymbol,
      setTitle: (sub) => {
        if (titles.get(id) === sub) return;
        titles.set(id, sub);
        const gid = compact ? "sheet" : groupOf(id);
        if (gid && groupEls.has(gid) && S.groups[gid]) paintHead(gid);
        else if (compact) layout(false);
      },
      visible: () => shown.has(id),
      compose: (text) => window.Chat && window.Chat.compose && window.Chat.compose(text),
      /** Attach this widget to the chat: { sub, context, question }. */
      ask: (got) => askWith(id, got),
      /** Hand something to another widget, opening one if none is open. */
      send: (type, payload) => sendTo(type, payload, id),
      close: () => close(id),
      openSymbol, warm, menu, toast,
    };
  }

  function sendTo(type, payload, from) {
    let id = placedOf(type).find((x) => lastVisible.has(x)) || placedOf(type)[0];
    // a quiet send (a "save to …") files into a widget already on the
    // workspace without bringing it forward over the one you are using
    if (id && payload && payload.quiet) {
      const api = ensureMounted(id);
      if (api && api.receive) { try { api.receive(payload); } catch (e) { console.error("[dock] receive", e); } }
      return id;
    }
    id = id ? (open(type), id) : open(type);
    // the first quiet save opens the widget it saves into, but never in
    // front of the widget the save came from
    if (payload && payload.quiet && from && id) {
      const gf = groupOf(from), gt = groupOf(id);
      if (gf && gf === gt && S.groups[gf].active !== from) { S.groups[gf].active = from; layout(false); }
    }
    const api = id && ensureMounted(id);
    if (api && api.receive) {
      try { api.receive(payload); } catch (e) { console.error("[dock] receive", e); }
    }
    return id;
  }

  /* ══ dragging ════════════════════════════════════════════════════════════ */

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
    const mv = (ev) => {
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
      removeEventListener("pointermove", mv);
      removeEventListener("pointerup", up);
      removeEventListener("pointercancel", up);
      if (drag) finishDrag(ev.type === "pointerup");
    };
    addEventListener("pointermove", mv);
    addEventListener("pointerup", up);
    addEventListener("pointercancel", up);
  }

  function startDrag(source, ev) {
    closePop();
    unmax();
    let title, spec;
    if (source.kind === "new") {
      spec = TYPES.get(source.type); title = spec.title;
    } else {
      const id = source.kind === "inst" ? source.id : S.groups[source.gid].active;
      spec = TYPES.get(S.inst[id].type); title = label(id).title;
      if (source.kind === "group" && S.groups[source.gid].tabs.length > 1) {
        title += ` +${S.groups[source.gid].tabs.length - 1}`;
      }
    }
    ghost.innerHTML = `${icon(spec.icon)}<span>${esc(title)}</span>`;
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
    preview.classList.toggle("caret", t.kind === "tab" && !t.merge);
    Object.assign(preview.style, { left: r.left + "px", top: r.top + "px",
                                   width: r.width + "px", height: r.height + "px" });
    preview.classList.add("on");
  }

  const inside = (x, y, r) => x >= r.left && x <= r.right && y >= r.top && y <= r.bottom;

  /** What dropping at (x, y) would do, and the rectangle that shows it. */
  function hit(x, y) {
    const src = drag.source;
    const srcGid = src.kind === "group" ? src.gid : src.kind === "inst" ? groupOf(src.id) : null;
    const lonely = srcGid && (src.kind === "group" || S.groups[srcGid].tabs.length === 1);
    const movingChart = (src.kind === "inst" && src.id === CHART)
      || (src.kind === "group" && hasChart(src.gid));
    const ws = canvas.getBoundingClientRect();
    if (!inside(x, y, ws) || (hubOpen && hubEl && inside(x, y, hubEl.getBoundingClientRect()) && !hubEl.classList.contains("away"))) return null;

    // 0 · the canvas edges: a new full-height or full-width tile
    const EDGE = 18;
    const sideRect = (side) => {
      const w = ws.width * EDGE_SHARE, h = ws.height * EDGE_SHARE;
      return side === "left" ? { left: ws.left, top: ws.top, width: w, height: ws.height }
        : side === "right" ? { left: ws.right - w, top: ws.top, width: w, height: ws.height }
        : side === "top" ? { left: ws.left, top: ws.top, width: ws.width, height: h }
        : { left: ws.left, top: ws.bottom - h, width: ws.width, height: h };
    };
    for (const [side, d] of [["left", x - ws.left], ["right", ws.right - x], ["top", y - ws.top], ["bottom", ws.bottom - y]]) {
      if (d < EDGE) return { kind: "edge", side, rect: sideRect(side) };
    }

    // windows first (they are on top), then the tiles
    const order = [...[...floatLayer.children].reverse(),
                   ...[...groupEls.values()].filter((n) => n.parentNode !== floatLayer)]
      .filter((n) => n.isConnected && S.groups[n.dataset.gid] && n.dataset.gid !== "stage" && n.dataset.gid !== "sheet");
    for (const n of order) {
      const gid = n.dataset.gid;
      const b = n.getBoundingClientRect();
      if (!inside(x, y, b)) continue;
      if (lonely && gid === srcGid) return null;
      const g = S.groups[gid];
      const fl = !!floatOf(gid);
      // 1 · a tab strip: join, at the slot under the pointer
      if (!isBare(g) && !movingChart) {
        const head = n.querySelector(".dk-head").getBoundingClientRect();
        if (inside(x, y, head)) {
          const tabs = [...n.querySelectorAll(".dk-tab")];
          let index = tabs.length, cx = tabs.length ? tabs[tabs.length - 1].getBoundingClientRect().right : head.left + 34;
          for (let i = 0; i < tabs.length; i++) {
            const tr = tabs[i].getBoundingClientRect();
            if (x < tr.left + tr.width / 2) { index = i; cx = tr.left; break; }
          }
          return { kind: "tab", gid, index, rect: { left: cx - 1.5, top: head.top + 6, width: 3, height: head.height - 12 } };
        }
      }
      // an empty slot is a target as a whole: whatever lands in it fills it
      if (isSlot(gid) && !movingChart) {
        return { kind: "tab", gid, index: null, merge: true, rect: { left: b.left, top: b.top, width: b.width, height: b.height } };
      }
      // 2 · a side of the tile: split it there
      if (!fl) {
        const fx = (x - b.left) / b.width, fy = (y - b.top) / b.height;
        const d = [["left", fx], ["right", 1 - fx], ["top", fy], ["bottom", 1 - fy]].sort((a, c) => a[1] - c[1])[0];
        if (d[1] < .3) {
          const share = hasChart(gid) && !movingChart ? .34 : .5;
          const side = d[0];
          const rect = side === "left" ? { left: b.left, top: b.top, width: b.width * share, height: b.height }
            : side === "right" ? { left: b.right - b.width * share, top: b.top, width: b.width * share, height: b.height }
            : side === "top" ? { left: b.left, top: b.top, width: b.width, height: b.height * share }
            : { left: b.left, top: b.bottom - b.height * share, width: b.width, height: b.height * share };
          return { kind: "split", gid, side, share, rect };
        }
      }
      // 3 · the middle: join as a tab — but the chart is never covered by a
      // tab, and is never put into another widget's stack; there it floats
      if (hasChart(gid) || movingChart) break;
      return { kind: "tab", gid, index: null, merge: true,
               rect: { left: b.left, top: b.top, width: b.width, height: b.height } };
    }
    const fw = Math.min(FLOAT_DEF.w, ws.width - 16), fh = Math.min(FLOAT_DEF.h, ws.height - 16);
    const fx = clamp(x - fw / 2, ws.left + 8, ws.right - fw - 8);
    const fy = clamp(y - 18, ws.top + 8, ws.bottom - fh - 8);
    return { kind: "float", x: fx - ws.left, y: fy - ws.top, w: fw, h: fh,
             rect: { left: fx, top: fy, width: fw, height: fh } };
  }

  function finishDrag(commit) {
    const d = drag;
    drag = null;
    cancelAnimationFrame(d.raf);
    removeEventListener("keydown", escDrag, true);
    document.body.classList.remove("dk-dragging");
    document.querySelectorAll(".dk-src").forEach((n) => n.classList.remove("dk-src"));
    preview.classList.remove("on", "caret");
    if (hubEl) hubEl.classList.remove("away");
    const t = commit ? d.target : null;
    if (!t) { ghost.classList.remove("on"); return; }
    const src = d.source;
    const before = snapshot();
    if (src.kind === "new") {
      open(src.type, t, { fresh: !TYPES.get(src.type).single && !!placedOf(src.type).length });
    } else if (src.kind === "inst") {
      if (t.kind === "tab" && groupOf(src.id) === t.gid) {
        const g = S.groups[t.gid], from = g.tabs.indexOf(src.id);
        let to = t.index == null ? g.tabs.length : t.index;
        if (to > from) to -= 1;
        g.tabs.splice(from, 1); g.tabs.splice(to, 0, src.id); g.active = src.id;
      } else if (t.kind === "split" && t.gid === groupOf(src.id) && S.groups[t.gid].tabs.length === 1) {
        // splitting a tile with itself is not a move
      } else {
        detach(src.id);
        place(src.id, t);
      }
      layout(false); play(before);
    } else if (src.kind === "group") {
      const g = S.groups[src.gid];
      if (!g || (t.gid === src.gid)) { ghost.classList.remove("on"); return; }
      const tabs = [...g.tabs], active = g.active;
      tabs.forEach((id) => detach(id));
      place(tabs[0], t);
      const gid = groupOf(tabs[0]);
      const at = S.groups[gid].tabs.indexOf(tabs[0]);
      S.groups[gid].tabs.splice(at + 1, 0, ...tabs.slice(1));
      S.groups[gid].active = active;
      layout(false); play(before);
    }
    landGhost(t);
  }

  function landGhost(t) {
    if (reduced.matches) { ghost.classList.remove("on"); return; }
    const r = t.rect;
    const a = ghost.animate([{ opacity: 1 }, { transform: `translate3d(${r.left + 10}px, ${r.top + 8}px, 0) scale(.9)`, opacity: 0 }],
                            { duration: 180, easing: "cubic-bezier(.22,1,.36,1)", fill: "forwards" });
    a.onfinish = () => { a.cancel(); ghost.classList.remove("on"); };
  }

  /* ── pointer wiring for groups ────────────────────────────────────────── */

  function onGroupPointerDown(e) {
    if (e.button !== 0) return;
    const node = e.target.closest(".dk-group");
    if (!node) return;
    const gid = node.dataset.gid;
    const float = floatOf(gid);
    if (float) {
      S.floats = [...S.floats.filter((f) => f !== float), float];
      if (floatLayer.lastElementChild !== node) move(floatLayer, node, null);
    }
    if (compact || !S.groups[gid]) return;
    if (e.target.closest("[data-act='resize']") && float) return resizeFloat(e, float, node);
    const tab = e.target.closest(".dk-tab");
    if (tab && !e.target.closest("[data-act='close-tab']")) {
      if (tab.dataset.inst === CHART && S.lock) return;
      return beginDrag(e, { kind: "inst", id: tab.dataset.inst });
    }
    if (e.target.closest("[data-act='grip']")) {
      if (S.lock && hasChart(gid)) return;
      e.preventDefault();
      if (float) return moveFloat(e, float, node);
      return beginDrag(e, { kind: "group", gid });
    }
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
      const W = floatLayer.clientWidth, H = floatLayer.clientHeight;
      let nx = fx + dx, ny = fy + dy;
      const snapX = [8, W - f.w - 8], snapY = [8, H - f.h - 8];
      for (const o of S.floats) {
        if (o === f) continue;
        snapX.push(o.x, o.x + o.w + 8, o.x - f.w - 8);
        snapY.push(o.y, o.y + o.h + 8, o.y - f.h - 8);
      }
      for (const s of snapX) if (Math.abs(nx - s) < 10) { nx = s; break; }
      for (const s of snapY) if (Math.abs(ny - s) < 10) { ny = s; break; }
      f.x = clamp(nx, 8, W - f.w - 8); f.y = clamp(ny, 8, H - f.h - 8);
      node.style.left = f.x + "px"; node.style.top = f.y + "px";
      const c = floatLayer.getBoundingClientRect();
      redock = ev.clientX < c.left + 6 ? "left" : ev.clientX > c.right - 6 ? "right"
        : ev.clientY > c.bottom - 6 ? "bottom" : ev.clientY < c.top + 6 ? "top" : false;
      node.classList.toggle("dk-redock", !!redock);
    };
    const up = () => {
      removeEventListener("pointermove", mv); removeEventListener("pointerup", up);
      node.classList.remove("dk-moving", "dk-redock");
      if (redock) {
        const tabs = [...S.groups[f.gid].tabs], active = S.groups[f.gid].active;
        tabs.forEach((id) => detach(id));
        place(tabs[0], { kind: "edge", side: redock });
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
      f.w = clamp(w0 + ev.clientX - x0, 240, floatLayer.clientWidth - f.x - 8);
      f.h = clamp(h0 + ev.clientY - y0, 160, floatLayer.clientHeight - f.y - 8);
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
    const sheet = gid === "sheet";
    const g = sheet ? { active: compactSheet.active } : S.groups[gid] || { active: CHART };
    const closeTab = e.target.closest("[data-act='close-tab']");
    if (closeTab) { e.stopPropagation(); return close(closeTab.closest(".dk-tab").dataset.inst); }
    const tab = e.target.closest(".dk-tab");
    if (tab) {
      const id = tab.dataset.inst;
      if (sheet) { compactSheet.active = id; return layout(false); }
      if (g.active !== id) { g.active = id; layout(false); }
      return;
    }
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const a = b.dataset.act;
    if (a === "close") { e.stopPropagation(); return sheet ? hideSheet() : close(g.active); }
    if (a === "max") return max(gid);
    if (a === "more") { e.stopPropagation(); return moreMenu(b, g.active); }
    if (a === "settings") { e.stopPropagation(); return settingsPanel(g.active); }
    if (a === "link") { e.stopPropagation(); return linkMenu(b, g.active); }
  }

  canvas.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".chart-tools")) return;   // the chart's own controls, not the tile's
    if (e.target.closest(".dk-gut")) return onGutterDown(e);
    if (!e.target.closest(".dk-hub")) onGroupPointerDown(e);
  });
  canvas.addEventListener("click", (e) => { if (!e.target.closest(".dk-hub, .chart-tools")) onGroupClick(e); });
  canvas.addEventListener("dblclick", (e) => {
    const tab = e.target.closest(".dk-tab");
    if (tab && !compact) return max(tab.closest(".dk-group").dataset.gid);
    const gut = e.target.closest(".dk-gut");
    if (gut) {
      // a double-click on a seam evens out the two tiles beside it
      let node = null; walk(S.tree, (n) => { if (n.id === gut.dataset.node) node = n; });
      if (node) { const j = +gut.dataset.j; node.s[j] = node.s[j - 1] = (node.s[j] + node.s[j - 1]) / 2; layout(false); }
    }
  });
  canvas.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    const tab = e.target.closest(".dk-tab");
    if (!tab) return;
    const sib = e.key === "ArrowLeft" ? tab.previousElementSibling : tab.nextElementSibling;
    if (sib && sib.classList.contains("dk-tab")) { sib.click(); sib.focus(); }
  });
  // Escape steps back one layer at a time: a menu, then the drawer, then a
  // maximized tile
  addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || drag) return;
    if (pop) { e.preventDefault(); return closePop(); }
    if (setsOpen) { e.preventDefault(); return closeSettings(); }
    if (hubOpen) return hub(false);
    if (maxed) unmax();
  });

  /* ── resizing: the seams between tiles ──────────────────────────────────── */

  function onGutterDown(e) {
    const gut = e.target.closest(".dk-gut");
    if (!gut || e.button !== 0) return;
    let node = null; walk(S.tree, (n) => { if (n.id === gut.dataset.node) node = n; });
    if (!node) return;
    e.preventDefault();
    const j = +gut.dataset.j;
    const horiz = node.k === "row";
    const a = gut.previousElementSibling, b = gut.nextElementSibling;
    const sa = horiz ? a.offsetWidth : a.offsetHeight, sb = horiz ? b.offsetWidth : b.offsetHeight;
    const wsum = node.s[j - 1] + node.s[j];
    const x0 = e.clientX, y0 = e.clientY;
    document.body.classList.add("dk-resizing", horiz ? "dk-col-resize" : "dk-row-resize");
    gut.classList.add("dragging");
    const mv = (ev) => {
      const d = horiz ? ev.clientX - x0 : ev.clientY - y0;
      const pa = clamp(sa + d, MIN_TILE, sa + sb - MIN_TILE);
      node.s[j - 1] = wsum * pa / (sa + sb); node.s[j] = wsum - node.s[j - 1];
      a.style.flex = `${node.s[j - 1]} 1 0`; b.style.flex = `${node.s[j]} 1 0`;
    };
    const up = () => {
      removeEventListener("pointermove", mv); removeEventListener("pointerup", up);
      document.body.classList.remove("dk-resizing", "dk-col-resize", "dk-row-resize");
      gut.classList.remove("dragging");
      for (const n of groupEls.values()) if (n.isConnected) paintInk(n);
      syncDense();
      persist();
    };
    addEventListener("pointermove", mv); addEventListener("pointerup", up);
  }

  /* ══ the rail and the header ═════════════════════════════════════════════ */

  /* The drawing rail is for drawing. Widgets used to sit on it as a second
   * column of icons, which made the chart's own tools hard to find; they are
   * reached from the header (Widgets, and Go to — Ctrl K) instead. */

  /** Where to put a widget: the menu a right-click on its hub card opens. */
  function placeMenu(anchor, type) {
    const spec = TYPES.get(type);
    const fresh = !spec.single && !!placedOf(type).length;
    menu(anchor, [
      { head: `Open ${spec.title}` },
      { id: "left", label: "Left side", icon: "panelLeft" },
      { id: "right", label: "Right side", icon: "panelRight" },
      { id: "bottom", label: "Bottom", icon: "panelBottom" },
      { id: "float", label: "Floating window", icon: "float" },
      ...(spec.single ? [] : [{ sep: true }, { id: "new", label: `New ${spec.title.toLowerCase()}`, icon: "plus" }]),
    ], (pick) => {
      if (pick === "new") return open(type, null, { fresh: true });
      const c = floatLayer.getBoundingClientRect();
      open(type, pick === "float" ? { kind: "float", x: c.width / 2 - FLOAT_DEF.w / 2, y: 40 }
                                  : { kind: "edge", side: pick }, { fresh });
    });
  }
  let dragJustEnded = false;
  addEventListener("pointerup", () => {
    if (document.body.classList.contains("dk-dragging")) {
      dragJustEnded = true; setTimeout(() => { dragJustEnded = false; }, 0);
    }
  }, true);

  /** After every layout: the header, the drawer's badges, and one event for
   *  anyone who mirrors the workspace (the phone bar, Go to). */
  function syncRail() {
    syncHeader();
    if (hubOpen) paintHubState();
    document.dispatchEvent(new CustomEvent("charto:dock"));
  }

  /* A widget can ask for attention — the alerts bell's "something fired".
   * The mark is shown on the widget's tabs, its hub card and the Widgets
   * button, so it is visible whether the widget is open or not. */
  const badges = new Set();
  function badge(type, on) {
    if (on === badges.has(type)) return;
    on ? badges.add(type) : badges.delete(type);
    for (const gid of Object.keys(S.groups)) if (groupEls.has(gid)) paintHead(gid);
    syncRail();
  }

  /** Bring an open widget forward: its tab, its tile, a flash. */
  function reveal(id) {
    const gid = groupOf(id);
    if (!gid) return;
    if (S.focus) S.focus = false;
    if (maxed && maxed !== gid) unmax();
    S.groups[gid].active = id;
    compactSheet.active = id;
    if (compact && !compactSheet.shown) { compactSheet.shown = true; phoneMakeRoom(); }
    layout(true);
    flash(id);
  }

  let wsBtn = null, fsBtn = null;
  function buildHeader() {
    const anchor = el("chatToggle");
    if (!anchor) return;
    anchor.insertAdjacentHTML("beforebegin",
      `<button class="btn dk-hbtn dk-addw" id="dockBtn" type="button" title="Add widgets" ` +
      `aria-label="Add widgets">${icon("plus")}<span>Widgets</span></button>` +
      `<button class="btn icon dk-hbtn" id="fullBtn" type="button" title="Fullscreen (Alt Shift F)" ` +
      `aria-label="Fullscreen">${icon("fullscreen")}</button>`);
    wsBtn = el("dockBtn"); fsBtn = el("fullBtn");
    wsBtn.addEventListener("click", (e) => { e.stopPropagation(); hub(); });
    fsBtn.addEventListener("click", fullscreen);
  }

  function syncHeader() {
    if (fsBtn) {
      const on = !!document.fullscreenElement;
      fsBtn.innerHTML = icon(on ? "fullscreenExit" : "fullscreen");
      fsBtn.title = on ? "Exit fullscreen (Alt Shift F)" : "Fullscreen (Alt Shift F)";
      fsBtn.classList.toggle("on", on);
    }
    if (wsBtn) {
      wsBtn.classList.toggle("on", hubOpen);
      wsBtn.classList.toggle("has-new", badges.size > 0);
    }
  }

  /* ══ the widget hub ══════════════════════════════════════════════════════
   * A drawer over the right of the canvas: every widget as a card with a
   * picture of itself. Click a card (or its +) to open it, or drag the card
   * onto the canvas — the drawer steps aside while you do. The switches for
   * the whole workspace sit at its top. Escape closes it. */
  const SECTIONS = ["Market", "Research", "Tools", "Media"];
  let hubEl = null, hubOpen = false;

  let hubTarget = null;               // an empty slot the drawer was opened to fill
  /** A card was chosen: fill the slot the drawer was opened from, or open it
   *  where it usually goes (a fresh copy when its + was pressed). */
  function pickCard(type, add) {
    const spec = TYPES.get(type);
    const slotGid = hubTarget && S.inst[hubTarget] && groupOf(hubTarget);
    if (slotGid) {
      open(type, { kind: "tab", gid: slotGid }, { fresh: !spec.single && placedOf(type).length > 0 });
      hubTarget = null;
      return hub(false);
    }
    open(type, null, { fresh: !spec.single && placedOf(type).length > 0 && add });
  }

  function hub(on, target) {
    on = on == null ? !hubOpen : on;
    if (on) hubTarget = target || null;
    else hubTarget = null;
    if (on === hubOpen) return;
    hubOpen = on;
    closePop();
    if (!hubEl) buildHub();
    if (on) {
      if (hubEl.parentNode !== canvas) canvas.appendChild(hubEl);
      paintHubState();
      hubEl.hidden = false;
      requestAnimationFrame(() => hubEl.classList.add("open"));
      setTimeout(() => document.addEventListener("pointerdown", hubOff, true), 0);
      const f = hubEl.querySelector(".hub-find input");
      if (f && f.value) { f.value = ""; f.dispatchEvent(new Event("input")); }
      if (f && matchMedia("(pointer: fine)").matches) setTimeout(() => f.focus({ preventScroll: true }), 60);
    } else {
      document.removeEventListener("pointerdown", hubOff, true);
      hubEl.classList.remove("open");
      setTimeout(() => { if (!hubOpen) hubEl.hidden = true; }, 220);
    }
    syncHeader();
  }

  // a press anywhere outside the drawer closes it — except on the button
  // that toggles it, and inside a menu the drawer itself opened
  function hubOff(e) {
    if (!hubOpen || drag) return;
    const t = e.target;
    if (hubEl.contains(t) || (wsBtn && wsBtn.contains(t)) || (pop && pop.contains(t))) return;
    hub(false);
  }

  function buildHub() {
    hubEl = document.createElement("aside");
    hubEl.className = "dk-hub";
    hubEl.hidden = true;
    hubEl.setAttribute("aria-label", "Widgets");
    const specs = [...TYPES.values()].filter((s) => s.catalog !== false && s.type !== CHART);
    const order = (s) => SECTIONS.indexOf(s.group || "Tools");
    specs.sort((x, y) => order(x) - order(y));
    // a picture and a name; the one-line description is the tooltip
    const card = (s) =>
      `<div class="hub-card" data-type="${s.type}" data-group="${esc(s.group || "Tools")}" role="button" tabindex="0" ` +
        `title="${esc(s.desc || s.title)}" aria-label="Add ${esc(s.title)}">` +
        `<div class="hub-pic">${typeof HubArt !== "undefined" ? HubArt.svg(s.type) : ""}` +
          `<button type="button" class="hub-add" data-add="${s.type}" title="Add another" ` +
            `aria-label="Add ${esc(s.title.toLowerCase())}">${icon("plus")}</button></div>` +
        `<b class="hub-t">${icon(s.icon, "xs")}<span>${esc(s.title)}</span><i class="hub-n" hidden></i></b>` +
      `</div>`;
    hubEl.innerHTML =
      `<div class="hub-top hub-glass">` +
        `<div class="hub-find">${Icons.field('<input type="search" placeholder="Search widgets" autocomplete="off" spellcheck="false" aria-label="Search widgets">')}</div>` +
        `<button type="button" class="hub-ico" data-hub="close" title="Close (Esc)" aria-label="Close">${icon("x")}</button>` +
      `</div>` +
      `<div class="hub-tabs hub-glass">` +
        `<button type="button" class="hub-tscroll l" data-tscroll="-1" aria-label="Earlier tabs" tabindex="-1">${icon("chevronLeft")}</button>` +
        `<div class="hub-track" role="tablist">` +
          ["All", ...SECTIONS].map((g, i) => `<button type="button" role="tab" data-hubg="${g}" class="${i ? "" : "on"}">${g}</button>`).join("") +
        `</div>` +
        `<button type="button" class="hub-tscroll r" data-tscroll="1" aria-label="More tabs" tabindex="-1">${icon("chevronRight")}</button>` +
      `</div>` +
      `<div class="hub-rail">` +
        `<button type="button" class="hub-arrow l" data-scroll="-1" aria-label="Scroll up" tabindex="-1">${icon("chevronUp")}</button>` +
        `<div class="hub-row">${specs.map(card).join("")}<p class="hub-none" hidden>No widget by that name.</p></div>` +
        `<button type="button" class="hub-arrow r" data-scroll="1" aria-label="Scroll down" tabindex="-1">${icon("chevronDown")}</button>` +
      `</div>` +
      `<div class="hub-tools hub-glass">` +
        `<label class="hub-switch" title="Let the chart be dragged and resized like any widget">` +
          `<input type="checkbox" class="dk-switch" data-hub="lock"><span>Movable chart</span></label>` +
        `<span class="hub-gap"></span>` +
        `<button type="button" class="hub-ico" data-hub="focus" title="Focus on the chart (Alt Z)" aria-label="Focus">${icon("focus")}</button>` +
        `<button type="button" class="hub-ico" data-hub="full" title="Fullscreen (Alt Shift F)" aria-label="Fullscreen">${icon("fullscreen")}</button>` +
        `<button type="button" class="hub-ico" data-hub="reset" title="Reset the workspace — close every widget" aria-label="Reset">${icon("rotateCw")}</button>` +
      `</div>`;
    const row = hubEl.querySelector(".hub-row");
    const find = hubEl.querySelector(".hub-find input");
    let group = "All";
    const filter = () => {
      const q = find.value.trim().toLowerCase();
      let any = false;
      for (const c of row.querySelectorAll(".hub-card")) {
        const on = (group === "All" || c.dataset.group === group)
          && (!q || (c.textContent + " " + c.title + " " + c.dataset.type).toLowerCase().includes(q));
        c.hidden = !on;
        any = any || on;
      }
      hubEl.querySelector(".hub-none").hidden = any;
      row.scrollTop = 0;
      arrows();
    };
    const arrows = () => {
      const max = row.scrollHeight - row.clientHeight;
      hubEl.querySelector(".hub-arrow.l").disabled = row.scrollTop < 4;
      hubEl.querySelector(".hub-arrow.r").disabled = row.scrollTop > max - 4;
    };
    hubEl.__arrows = arrows;
    find.addEventListener("input", filter);
    row.addEventListener("scroll", arrows, { passive: true });
    // the one-line tab strip: a track that scrolls sideways between the two
    // end chevrons, which light up only while there is more to see that way
    const track = hubEl.querySelector(".hub-track");
    const tabsBar = hubEl.querySelector(".hub-tabs");
    const tabArrows = () => {
      const max = track.scrollWidth - track.clientWidth;
      tabsBar.classList.toggle("has-l", track.scrollLeft > 4);
      tabsBar.classList.toggle("has-r", max > 4 && track.scrollLeft < max - 4);
    };
    hubEl.__tabArrows = tabArrows;
    track.addEventListener("scroll", tabArrows, { passive: true });
    tabsBar.addEventListener("click", (e) => {
      const s = e.target.closest("[data-tscroll]");
      if (s) { track.scrollBy({ left: +s.dataset.tscroll * track.clientWidth * .6, behavior: reduced.matches ? "auto" : "smooth" }); return; }
      const b = e.target.closest("[data-hubg]");
      if (!b) return;
      group = b.dataset.hubg;
      for (const x of hubEl.querySelectorAll("[data-hubg]")) x.classList.toggle("on", x === b);
      // keep the chosen tab in view within the strip
      b.scrollIntoView({ inline: "nearest", block: "nearest", behavior: reduced.matches ? "auto" : "smooth" });
      filter();
    });
    hubEl.querySelector(".hub-rail").addEventListener("click", (e) => {
      const a = e.target.closest("[data-scroll]");
      if (a) row.scrollBy({ top: +a.dataset.scroll * row.clientHeight * .8, behavior: reduced.matches ? "auto" : "smooth" });
    });
    find.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      const c = [...hubEl.querySelectorAll(".hub-card")].find((x) => !x.hidden);
      if (c) { e.preventDefault(); pickCard(c.dataset.type, false); }
    });
    hubEl.addEventListener("contextmenu", (e) => {
      const c = e.target.closest(".hub-card");
      if (!c || compact) return;
      e.preventDefault();
      placeMenu(c, c.dataset.type);
    });
    hubEl.addEventListener("click", (e) => {
      const h = e.target.closest("[data-hub]");
      if (h) {
        const a = h.dataset.hub;
        if (a === "close") return hub(false);
        if (a === "lock") return setLock(!h.checked);
        if (a === "focus") { hub(false); return setFocus(); }
        if (a === "full") return fullscreen();
        if (a === "reset") return resetWorkspace();
      }
      const add = e.target.closest("[data-add]");
      const c = e.target.closest(".hub-card");
      if (!add && !c) return;
      if (hubDragged) return;
      pickCard((add || c).dataset.add || c.dataset.type, !!add);
    });
    hubEl.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("hub-card")) {
        e.preventDefault(); pickCard(e.target.dataset.type, false);
      }
    });
    let hubDragged = false;
    hubEl.addEventListener("pointerdown", (e) => {
      const c = e.target.closest(".hub-card");
      if (!c || e.button !== 0 || compact || e.target.closest("[data-add]")) return;
      hubDragged = false;
      const type = c.dataset.type, x0 = e.clientX, y0 = e.clientY;
      const mv = (ev) => {
        if (Math.hypot(ev.clientX - x0, ev.clientY - y0) < 6) return;
        removeEventListener("pointermove", mv);
        hubDragged = true;
        hubEl.classList.add("away");      // step aside so the canvas is a target
        beginDrag(e, { kind: "new", type });
        startDrag({ kind: "new", type }, ev);
      };
      addEventListener("pointermove", mv);
      addEventListener("pointerup", () => {
        removeEventListener("pointermove", mv);
        setTimeout(() => { hubDragged = false; }, 0);
      }, { once: true });
    });
  }

  function paintHubState() {
    if (!hubEl) return;
    const lock = hubEl.querySelector('[data-hub="lock"]');
    if (lock) lock.checked = !S.lock;
    const f = hubEl.querySelector('[data-hub="focus"]');
    if (f) f.classList.toggle("on", !!S.focus);
    if (hubEl.__arrows) requestAnimationFrame(hubEl.__arrows);
    if (hubEl.__tabArrows) requestAnimationFrame(hubEl.__tabArrows);
    for (const c of hubEl.querySelectorAll(".hub-card")) {
      const n = placedOf(c.dataset.type).length;
      c.classList.toggle("on", n > 0);
      const i = c.querySelector(".hub-n");
      i.hidden = n < 1; i.textContent = n > 1 ? String(n) : "";
      i.title = n ? `${n} open` : "";
      c.classList.toggle("has-new", badges.has(c.dataset.type));
    }
  }

  function resetWorkspace() {
    for (const id of Object.keys(S.inst)) {
      if (id === CHART) continue;
      const at = detach(id); if (at) S.closed.push({ id, ...at });
    }
    S.focus = false; S.lock = false;
    unmax(); layout(true);
  }

  /* ══ the chart itself, as a widget ══════════════════════════════════════ */

  TYPES.set(CHART, {
    type: CHART, title: "Chart", icon: "candles", single: true, rail: false, catalog: false,
    host() {
      const h = document.createElement("div");
      h.appendChild(charts);
      return h;
    },
    mount(host, ctx) {
      return { show() { ctx.setTitle(pageSymbol()); } };
    },
  });

  /* ══ the empty slot ══════════════════════════════════════════════════════
   * Pull the chart in from an edge of the workspace and the room it gives up
   * is a slot: a dashed place that says what it is for. Fill it from the
   * drawer, drag any widget into it, or close it and the chart takes the
   * room back. */
  TYPES.set(SLOT, {
    type: SLOT, title: "Empty", icon: "plus", catalog: false, rail: false,
    mount(host, ctx) {
      host.innerHTML = `<div class="dk-slot">` +
        `<button type="button" class="dk-slot-add" data-slot="add">${icon("plus")}<span>Add a widget</span></button>` +
        `<em>or drag one here</em>` +
        `<button type="button" class="dk-slot-x" data-slot="close" title="Close this space; the widgets beside it take the room back" aria-label="Close">${icon("x")}</button>` +
      `</div>`;
      host.addEventListener("click", (e) => {
        const b = e.target.closest("[data-slot]");
        if (!b) return;
        e.stopPropagation();
        if (b.dataset.slot === "add") hub(true, ctx.id);
        else ctx.close();
      });
      return {};
    },
  });

  /* Edge handles: every tile can be resized from every side. Between two
   * tiles that is the gutter; where a tile meets the edge of the workspace
   * there is no gutter, so a thin handle sits along THAT tile's edge. Drag it
   * in and the tile shrinks on that side, leaving an empty slot sized live by
   * the pointer — the space a widget can be dropped or picked into. Let go
   * near where you started and nothing is made. */
  const pullEls = [];
  const SIDES = ["left", "right", "top", "bottom"];
  function pullEl(k) {
    let n = pullEls[k];
    if (!n) {
      n = pullEls[k] = document.createElement("div");
      n.className = "dk-pull";
      n.setAttribute("aria-hidden", "true");
      n.addEventListener("pointerdown", (e) => pullStart(e, n.dataset.gid, n.dataset.side));
      canvas.appendChild(n);
    }
    return n;
  }
  function syncPulls() {
    const off = compact || S.focus || maxed || drag;
    const cv = canvas.getBoundingClientRect();
    let k = 0;
    if (!off) {
      for (const gid of treeGroups()) {
        if (isSlot(gid) || floatOf(gid)) continue;
        const node = groupEls.get(gid);
        if (!node || !node.isConnected) continue;
        const r = node.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) continue;
        for (const side of SIDES) {
          const touch = side === "left" ? r.left - cv.left < 2 : side === "right" ? cv.right - r.right < 2
            : side === "top" ? r.top - cv.top < 2 : cv.bottom - r.bottom < 2;
          if (!touch) continue;
          const n = pullEl(k++);
          if (n.parentNode !== canvas) canvas.appendChild(n);
          n.hidden = false;
          n.dataset.gid = gid; n.dataset.side = side;
          n.title = `Drag to resize ${label(S.groups[gid].active).title} from this side`;
          const v = side === "left" || side === "right";
          Object.assign(n.style, v
            ? { top: r.top - cv.top + 8 + "px", height: Math.max(0, r.height - 16) + "px", left: side === "left" ? r.left - cv.left + "px" : "",
                right: side === "right" ? cv.right - r.right + "px" : "", width: "", bottom: "" }
            : { left: r.left - cv.left + 8 + "px", width: Math.max(0, r.width - 16) + "px", top: side === "top" ? r.top - cv.top + "px" : "",
                bottom: side === "bottom" ? cv.bottom - r.bottom + "px" : "", height: "", right: "" });
        }
      }
    }
    for (; k < pullEls.length; k++) pullEls[k].hidden = true;
  }
  addEventListener("resize", () => requestAnimationFrame(syncPulls));
  // the chat opening, a sidebar folding: the workspace changes size with no
  // window resize, and the handles must follow the tiles
  if (typeof ResizeObserver !== "undefined") {
    let pr = 0;
    new ResizeObserver(() => { cancelAnimationFrame(pr); pr = requestAnimationFrame(syncPulls); }).observe(canvas);
  }

  function pullStart(e, target, side) {
    if (e.button !== 0 || !S.groups[target]) return;
    e.preventDefault();
    e.stopPropagation();
    const node = groupEls.get(target);
    const r0 = node.getBoundingClientRect();
    const v = side === "left" || side === "right";
    const size = v ? r0.width : r0.height;
    const before = BEFORE[side];
    let id = null, gid = null, share = 0, raf = 0;
    document.body.classList.add("dk-resizing", v ? "dk-col-resize" : "dk-row-resize");
    const dist = (ev) => side === "left" ? ev.clientX - r0.left : side === "right" ? r0.right - ev.clientX
      : side === "top" ? ev.clientY - r0.top : r0.bottom - ev.clientY;
    // the tile keeps at least what it needs to be read
    const spec = TYPES.get((S.inst[S.groups[target].active] || {}).type) || {};
    const keep = hasChart(target) ? (v ? 320 : 180) : (v ? Math.min(spec.minW || 220, 260) : 120);
    const apply = () => {
      raf = 0;
      const loc = locate(gid);
      if (!loc || !loc.p) return;
      const ti = before ? loc.i + 1 : loc.i - 1;
      if (ti < 0 || ti >= loc.p.s.length) return;
      const pair = loc.p.s[loc.i] + loc.p.s[ti];
      loc.p.s[loc.i] = pair * share;
      loc.p.s[ti] = pair * (1 - share);
      layout(false);
    };
    const mv = (ev) => {
      const d = dist(ev);
      share = clamp(d / size, .03, Math.max(.05, 1 - keep / size));
      if (!id) {
        if (d < 12) return;
        id = uid("slot:");
        S.inst[id] = { type: SLOT, cfg: {} };
        gid = newGroup([id]);
        splitAt(target, side, gid, share);
        layout(false);
        return;
      }
      if (!raf) raf = requestAnimationFrame(apply);
    };
    const up = () => {
      removeEventListener("pointermove", mv);
      removeEventListener("pointerup", up);
      removeEventListener("pointercancel", up);
      cancelAnimationFrame(raf);
      document.body.classList.remove("dk-resizing", "dk-col-resize", "dk-row-resize");
      if (!id) return;
      if (share * size < 40) { detach(id); delete S.inst[id]; layout(true); return; }
      apply();
      flash(id);
    };
    addEventListener("pointermove", mv);
    addEventListener("pointerup", up);
    addEventListener("pointercancel", up);
  }

  /* ══ registration and start ══════════════════════════════════════════════ */

  let started = false;
  function register(spec) {
    TYPES.set(spec.type, spec);
    if (started) layout(false);
  }

  function start() {
    if (started) return;
    started = true;
    S = load();
    const legacy = new URLSearchParams(location.search).get("panel");
    if (legacy && TYPES.has(legacy) && !placedOf(legacy).length) {
      if (!S.inst[legacy]) S.inst[legacy] = { type: legacy, cfg: { ...(TYPES.get(legacy).defaults || {}) } };
      sanitize();
      place(legacy, { kind: "zone", zone: TYPES.get(legacy).zone || "right" });
    }
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
    register, start, open, close, toggle, openSymbol, warm, setFocus, fullscreen, setLock, hub,
    visible: (type) => placedOf(type).some((id) => lastVisible.has(id)),
    instances: (type) => placedOf(type),
    /** Every widget the hub offers: what Go to and the phone bar list. */
    catalog: () => [...TYPES.values()].filter((t) => t.catalog !== false && t.type !== CHART)
      .map((t) => ({ type: t.type, title: t.title, icon: t.icon, desc: t.desc || "", group: t.group || "",
                     key: t.key || "", anim: t.anim || "", single: !!t.single, open: placedOf(t.type).length })),
    /** What is on the workspace now, in reading order. */
    opened: () => [...treeGroups(), ...S.floats.map((f) => f.gid)].flatMap((gid) => S.groups[gid].tabs)
      .filter((id) => S.inst[id].type !== SLOT)
      .map((id) => ({ id, type: S.inst[id].type, icon: (TYPES.get(S.inst[id].type) || {}).icon,
                      ...label(id), visible: lastVisible.has(id) })),
    reveal, badge, badged: (type) => badges.has(type),
    /** A list widget's row was chosen (by instance id, or by type for a
     *  single widget): the group's symbol, or the chart's. */
    pick: (idOrType, sym, how) => {
      const id = S.inst[idOrType] ? idOrType : placedOf(idOrType)[0];
      return id ? pickSymbol(id, sym, how) : openSymbol(sym, how);
    },
    symbolOf: (idOrType) => { const id = S.inst[idOrType] ? idOrType : placedOf(idOrType)[0]; return id ? symbolOf(id) : pageSymbol(); },
    cfgOf: (idOrType) => { const id = S.inst[idOrType] ? idOrType : placedOf(idOrType)[0] || idOrType; return S.inst[id] ? S.inst[id].cfg : {}; },
    settings: (idOrType) => { const id = S.inst[idOrType] ? idOrType : placedOf(idOrType)[0]; if (id) settingsPanel(id); },
    reset: () => resetWorkspace(),
    exportState, applyState,
    /** The tiled tree with each group's active widget: what the Share dialog draws. */
    shape: () => {
      const g = (n) => n.k === "g" ? { g: n.g, tabs: S.groups[n.g].tabs.map((id) => S.inst[id].type), active: S.inst[S.groups[n.g].active].type }
        : { k: n.k, s: n.s.slice(), c: n.c.map(g) };
      return { tree: S.tree ? g(S.tree) : null, floats: S.floats.map((f) => g({ k: "g", g: f.gid })) };
    },
    meta: (type) => { const t = TYPES.get(type); return t ? { title: t.title, icon: t.icon, hue: t.hue || "" } : null; },
    state: () => ({ focus: !!S.focus, lock: !!S.lock, hub: hubOpen, fullscreen: !!document.fullscreenElement }),
    send: sendTo,
    setCfg, toast, menu, closeMenu: closePop,
  };
})();
