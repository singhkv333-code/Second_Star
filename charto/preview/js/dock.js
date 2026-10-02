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
                         focus: false, lock: false });
  let S = blank();

  function load() {
    const raw = Store.get(KEY, null);
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

  let saveT = 0;
  const persist = () => { clearTimeout(saveT); saveT = setTimeout(persistNow, 160); };
  function persistNow() { clearTimeout(saveT); Store.set(KEY, S); }
  addEventListener("pagehide", persistNow);

  /* ── where things are ──────────────────────────────────────────────────── */

  const groupOf = (id) => Object.keys(S.groups).find((g) => S.groups[g].tabs.includes(id));
  const instancesOf = (type) => Object.keys(S.inst).filter((id) => S.inst[id].type === type);
  const placedOf = (type) => instancesOf(type).filter((id) => groupOf(id));
  const floatOf = (gid) => S.floats.find((f) => f.gid === gid);
  const isBare = (g) => g && g.tabs.length === 1 && g.tabs[0] === CHART && S.lock;
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
      return `<button type="button" class="dk-tab${on ? " on" : ""}" role="tab" ` +
        `aria-selected="${on}" data-inst="${id}" title="${esc(l.title + (l.sub ? " · " + l.sub : ""))}">` +
        `${icon(s.icon)}<span class="t">${esc(l.title)}</span>` +
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
      (spec && spec.linkable ? btn("link", "link", cfg.pin ? `Pinned to ${esc(cfg.pin)} — click to change`
                                            : "Following the chart — click to change",
                    cfg.pin ? 'data-pinned="1"' : "") : "") +
      (spec && spec.settings && spec.settings.length ? btn("settings", "settings", "Widget settings") : "") +
      btn("more", "more", "More") +
      (!fl ? btn("max", maxed === gid ? "shrink" : "expand", maxed === gid ? "Restore" : "Maximize") : "") +
      (g.active !== CHART ? btn("close", "x", "Close") : "");
    node.classList.toggle("is-float", fl);
    node.classList.toggle("has-chart", g.tabs.includes(CHART));
    requestAnimationFrame(() => paintInk(node));
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
    for (const c of [...canvas.children]) if (c !== root && c !== floatLayer && !c.classList.contains("dk-hub")) c.remove();
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

    document.body.classList.toggle("dk-focus", !!S.focus);
    document.body.classList.toggle("dk-compact", compact);
    document.body.classList.toggle("dk-chart-locked", !!S.lock);
    syncVisibility(visibleNow);
    placeFloats();
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
      ...(spec.settings && spec.settings.length ? [{ id: "settings", label: "Settings", icon: "settings" }] : []),
      ...(compact || (chart && S.lock) ? [] : [{ sep: true }, { head: "Move to" },
        { id: "mv:left", label: "Left edge", icon: "panelLeft" },
        { id: "mv:right", label: "Right edge", icon: "panelRight" },
        { id: "mv:top", label: "Top edge", icon: "panelBottom" },
        { id: "mv:bottom", label: "Bottom edge", icon: "panelBottom" },
        { id: "mv:float", label: "Floating window", icon: "float", on: fl }]),
      ...(chart ? [{ sep: true }, { id: "lock", label: S.lock ? "Unlock the chart" : "Lock the chart in place",
                                    icon: S.lock ? "unlock" : "lock" }]
                : [{ sep: true }, { id: "close", label: "Close", icon: "x" }]),
    ], (pick) => {
      if (pick === "ask") return askFrom(id);
      if (pick === "new") return open(spec.type, null, { fresh: true });
      if (pick === "dup") return duplicate(id);
      if (pick === "settings") return setTimeout(() => settingsSheet(anchor, id), 0);
      if (pick === "close") return close(id);
      if (pick === "lock") return setLock();
      if (pick.startsWith("mv:")) return moveTo(id, pick.slice(3));
    });
  }

  function askFrom(id) {
    const api = live.get(id) || {};
    const text = api.ask && api.ask();
    if (text && window.Chat && window.Chat.compose) window.Chat.compose(text);
    else if (!text) toast("There is nothing in this widget to ask about yet.");
  }

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
      setCfg(id, { [seg.dataset.key]: typeof s.def === "number" ? Number(b.dataset.v) : b.dataset.v });
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
    toastT = setTimeout(() => toastEl.classList.remove("on"), 2800);
  }

  /* ══ the widget's handle on the dock ════════════════════════════════════ */

  function ctxFor(id) {
    return {
      id,
      get cfg() { return S.inst[id] ? S.inst[id].cfg : {}; },
      setCfg: (patch) => setCfg(id, patch),
      symbol: () => (S.inst[id] && S.inst[id].cfg.pin) || pageSymbol(),
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
      /** Hand something to another widget, opening one if none is open. */
      send: (type, payload) => sendTo(type, payload),
      close: () => close(id),
      openSymbol, warm, menu, toast,
    };
  }

  function sendTo(type, payload) {
    let id = placedOf(type).find((x) => lastVisible.has(x)) || placedOf(type)[0];
    id = id ? (open(type), id) : open(type);
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
    if (a === "settings") { e.stopPropagation(); return settingsSheet(b, g.active); }
    if (a === "link") { e.stopPropagation(); return linkMenu(b, g.active); }
  }

  canvas.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".dk-gut")) return onGutterDown(e);
    if (!e.target.closest(".dk-hub")) onGroupPointerDown(e);
  });
  canvas.addEventListener("click", (e) => { if (!e.target.closest(".dk-hub")) onGroupClick(e); });
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
  addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || drag) return;
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

  const railBtns = new Map();

  function buildRail() {
    const spacer = document.querySelector("#rail .rail-spacer");
    if (!spacer) return;
    const specs = [...TYPES.values()].filter((s) => s.rail !== false && s.type !== CHART);
    spacer.insertAdjacentHTML("afterend",
      `<div class="rail-sep rail-widget-sep"></div>` +
      specs.map((s) => `<button type="button" class="tool dk-rail" id="wb-${s.type}" ` +
        `data-widget="${s.type}" data-anim="${s.anim || ""}" aria-expanded="false">${icon(s.icon)}` +
        `<span class="tip">${esc(s.title)}${s.key ? ` <kbd>${esc(s.key)}</kbd>` : ""}</span></button>`).join("") +
      `<button type="button" class="tool dk-rail dk-rail-more" id="wb-more" aria-label="All widgets">` +
        `${icon("widgets")}<span class="tip">All widgets</span></button>` +
      `<div class="rail-sep rail-export-sep"></div>`);
    for (const s of specs) railBtns.set(s.type, el(`wb-${s.type}`));
    const rail = el("rail");
    rail.addEventListener("pointerdown", (e) => {
      const b = e.target.closest(".dk-rail[data-widget]");
      if (!b || e.button !== 0 || compact) return;
      beginDrag(e, { kind: "new", type: b.dataset.widget });
    });
    rail.addEventListener("click", (e) => {
      if (e.target.closest("#wb-more")) { e.stopPropagation(); return hub(); }
      const b = e.target.closest(".dk-rail[data-widget]");
      if (!b || dragJustEnded) return;
      toggle(b.dataset.widget);
    });
    rail.addEventListener("contextmenu", (e) => {
      const b = e.target.closest(".dk-rail[data-widget]");
      if (!b || compact) return;
      e.preventDefault();
      const spec = TYPES.get(b.dataset.widget);
      const fresh = !spec.single && !!placedOf(spec.type).length;
      menu(b, [
        { head: `Open ${spec.title}` },
        { id: "left", label: "On the left", icon: "panelLeft" },
        { id: "right", label: "On the right", icon: "panelRight" },
        { id: "bottom", label: "Along the bottom", icon: "panelBottom" },
        { id: "float", label: "As a floating window", icon: "float" },
        ...(spec.single ? [] : [{ sep: true }, { id: "new", label: `New ${spec.title.toLowerCase()}`, icon: "plus" }]),
      ], (pick) => {
        if (pick === "new") return open(spec.type, null, { fresh: true });
        const c = floatLayer.getBoundingClientRect();
        open(spec.type, pick === "float" ? { kind: "float", x: c.width / 2 - FLOAT_DEF.w / 2, y: 40 }
                                         : { kind: "edge", side: pick }, { fresh });
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
    if (hubOpen) paintHubState();
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
    if (wsBtn) wsBtn.classList.toggle("on", hubOpen);
  }

  /* ══ the widget hub ══════════════════════════════════════════════════════
   * A drawer over the right of the canvas: every widget as a card with a
   * picture of itself. Click a card (or its +) to open it, or drag the card
   * onto the canvas — the drawer steps aside while you do. The switches for
   * the whole workspace sit at its top. Escape closes it. */
  const SECTIONS = ["Market", "Research", "Tools", "Media"];
  let hubEl = null, hubOpen = false;

  function hub(on) {
    on = on == null ? !hubOpen : on;
    if (on === hubOpen) return;
    hubOpen = on;
    closePop();
    if (!hubEl) buildHub();
    if (on) {
      if (hubEl.parentNode !== canvas) canvas.appendChild(hubEl);
      paintHubState();
      hubEl.hidden = false;
      requestAnimationFrame(() => hubEl.classList.add("open"));
    } else {
      hubEl.classList.remove("open");
      setTimeout(() => { if (!hubOpen) hubEl.hidden = true; }, 220);
    }
    syncHeader();
  }

  function buildHub() {
    hubEl = document.createElement("aside");
    hubEl.className = "dk-hub";
    hubEl.hidden = true;
    hubEl.setAttribute("aria-label", "Widgets");
    const specs = [...TYPES.values()].filter((s) => s.catalog !== false && s.type !== CHART);
    const card = (s) =>
      `<div class="hub-card" data-type="${s.type}" role="button" tabindex="0" aria-label="Add ${esc(s.title)}">` +
        `<div class="hub-pic">${typeof HubArt !== "undefined" ? HubArt.svg(s.type) : ""}</div>` +
        `<div class="hub-meta"><b>${icon(s.icon, "xs")}${esc(s.title)}<i class="hub-n" hidden></i></b>` +
          `<span>${esc(s.desc || "")}</span></div>` +
        `<button type="button" class="hub-add" data-add="${s.type}" title="Add ${esc(s.title.toLowerCase())}" ` +
          `aria-label="Add ${esc(s.title.toLowerCase())}">${icon("plus")}</button>` +
      `</div>`;
    hubEl.innerHTML =
      `<div class="hub-head">` +
        `<div><b>Widgets</b><span>Click to add, or drag a card onto the canvas.</span></div>` +
        `<button type="button" class="hub-close" data-hub="close" aria-label="Close">${icon("x")}<kbd>Esc</kbd></button>` +
      `</div>` +
      `<div class="hub-bar">` +
        `<label class="hub-switch"><input type="checkbox" class="dk-switch" data-hub="lock">` +
          `<span>Movable chart</span></label>` +
        `<button type="button" class="dk-pill" data-hub="focus">${icon("focus", "xs")}Focus<kbd>Alt Z</kbd></button>` +
        `<button type="button" class="dk-pill" data-hub="full">${icon("fullscreen", "xs")}Fullscreen</button>` +
        `<button type="button" class="dk-pill" data-hub="reset">${icon("rotateCw", "xs")}Reset</button>` +
      `</div>` +
      `<div class="hub-scroll">` +
        SECTIONS.map((sec) => {
          const list = specs.filter((s) => (s.group || "Tools") === sec);
          return list.length ? `<div class="hub-sec">${sec}</div><div class="hub-grid">${list.map(card).join("")}</div>` : "";
        }).join("") +
      `</div>`;
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
      const type = (add || c).dataset.add || c.dataset.type;
      const spec = TYPES.get(type);
      const fresh = !spec.single && placedOf(type).length > 0 && !!add;
      open(type, null, { fresh });
    });
    hubEl.addEventListener("keydown", (e) => {
      if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("hub-card")) {
        e.preventDefault(); open(e.target.dataset.type);
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
    for (const c of hubEl.querySelectorAll(".hub-card")) {
      const n = placedOf(c.dataset.type).length;
      c.classList.toggle("on", n > 0);
      const i = c.querySelector(".hub-n");
      i.hidden = n < 1; i.textContent = n > 1 ? `${n} open` : "Open";
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
    register, start, open, close, toggle, openSymbol, warm, setFocus, fullscreen, setLock, hub,
    visible: (type) => placedOf(type).some((id) => lastVisible.has(id)),
    instances: (type) => placedOf(type),
    send: sendTo,
    setCfg, toast, menu, closeMenu: closePop,
  };
})();
