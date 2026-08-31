/* Charto — the layers panel: one inventory of everything drawn on the chart.
 *
 * WHAT THIS REPLACES. `PatternDrawer` held a ledger of detector formations and
 * nothing else, while the two layers that actually cover the chart — the
 * annotations the chat draws, and the shapes the user places by hand — had no
 * list at all. Their only control was the global fold: one number, one switch,
 * everything or nothing. So the chart had exactly two states, "all of it" and
 * "none of it", and the thing a reader wants after asking four questions —
 * keep the neckline, put the eight levels away — could not be said.
 *
 * THE MODEL. One row per object, three species, one switch:
 *
 *   pattern   a detector formation. Geometry is fetched from /patterns/draw
 *             the first time it is shown and PARKED after that, so the second
 *             toggle is a flag flip rather than a round trip.
 *   scene     anything a chat tool drew. Keyed by `link || id` so a
 *             divergence's two legs are the one object the user drew, and
 *             grouped by the `owner` stamp dataserver.py puts on every
 *             annotation — which is what lets the panel say "levels" and
 *             "head and shoulders" rather than "zone" and "segment".
 *   drawing   the user's own shape, addressed by the D-ref the chat already
 *             uses, so a row here and a mention in the conversation name the
 *             same thing.
 *
 * ON IS GREEN, OFF IS STILL THERE. The switch sets `hidden` on the item; it
 * does not delete it. That distinction is the whole point of the panel: a
 * chart you have cleaned up is not a chart you have lost work on, and the
 * trash stays a separate, deliberate control.
 *
 * WHERE IT LIVES. Not in the left rail with the watchlist and the journal —
 * those are places you go, and this is a control for the chart you are already
 * looking at. It hangs off the drawing chips at the top-right of the price
 * pane, which is where the annotations already say what they are.
 */
"use strict";

window.LayersPanel = (() => {
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";

  const symbol = ((window.__charto && window.__charto.symbol)
    || new URLSearchParams(location.search).get("symbol") || "RELIANCE").toUpperCase();

  let query = "";
  let onlyLive = false;              // the header filter: on-chart only
  const expanded = new Set();
  let host = null;                   // the popover body, while open

  /* ── the pattern ledger ────────────────────────────────────────────────
   * Unchanged in shape from the drawer this replaces, so a workspace saved by
   * the old build still opens: `pattern_ledger` stays the storage key and
   * stays the thing Auth syncs. What is new is that it is now ONE of three
   * sources rather than the whole panel. */
  const saved = Store.get("pattern_ledger", null);
  let ledger = saved && !Array.isArray(saved)
    ? { items: Array.isArray(saved.items) ? saved.items : [], deleted: saved.deleted || {} }
    : { items: Array.isArray(saved) ? saved : [], deleted: {} };
  let saveTimer = null;

  const esc = (v) => String(v == null ? "" : v).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const title = (v) => String(v || "").replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
  const sentence = (v) => {
    const s = String(v == null || v === "" ? "—" : v).replace(/_/g, " ");
    return s.charAt(0).toUpperCase() + s.slice(1);
  };
  const shortDate = (v) => String(v || "—").replace(/\s+\d{4}(?=\s|$)/, "");
  /* The formation date, for the column at the right of every pattern row.
   *
   * The detectors emit `_ist`'s format — "08 Jul 2026", or "08 Jul 2026 15:25"
   * on an intraday interval. A column has room for neither in full, and
   * neither is what the eye is asking: scanning a list of formations, the
   * question is WHICH DAY, and the year is the same year for everything on a
   * 300-bar lookback. So the year goes, and so does the clock — the exact
   * minute is still one press away in the row's detail, where the full window
   * (from → to) is spelled out.
   *
   * Falls back to the raw string rather than to a blank: a detector that some
   * day emits a shape this does not recognise should print something the user
   * can read, not lose the date entirely. */
  const dayOf = (v) => {
    const s = String(v == null ? "" : v).trim();
    if (!s) return "";
    const m = /^(\d{1,2}\s+[A-Za-z]{3})(?:\s+\d{4})?(?:\s+\d{1,2}:\d{2})?$/.exec(s);
    return m ? m[1] : shortDate(s);
  };
  /* Icons.svg THROWS on a name it does not have, and half the glyphs here are
   * chosen from DATA — a drawing's tool type, a tool name off the wire. One
   * unknown name must cost that row its picture, not cost the user the panel:
   * the render is a single innerHTML, so an exception midway through leaves an
   * empty popover with no error anywhere the user can see. */
  const icon = (name) => {
    try { return Icons.svg(name, "xs"); }
    catch { return Icons.svg("brush", "xs"); }
  };
  const scene = () => window.__charto && window.__charto.scene;
  const draw = () => window.__charto && window.__charto.draw;

  const refresh = () => document.dispatchEvent(new Event("charto:layers-refresh"));

  /** Repaint the chip on the chart as well as the list.
   *
   *  The chip is painted by scene.js off `foldState()`, so it only notices a
   *  change the SCENE made. Deleting a pattern that was never drawn touches no
   *  scene object at all — the row went, the list re-rendered, and the "3/136"
   *  chip six inches away still said 136. Two counts of one thing, disagreeing
   *  by exactly the row the user just removed. */
  const repaintChip = () => { try { scene() && scene().requestUpdate(); } catch { /* chart not up */ } };

  function persist() {
    Store.set("pattern_ledger", ledger);
    clearTimeout(saveTimer);
    if (typeof Auth === "undefined" || !Auth.user) return;
    saveTimer = setTimeout(() => {
      Auth.saveWorkspace(symbol, { pattern_ledger: ledger }).catch(() => {});
    }, 350);
  }

  /* ── naming what the chat drew ─────────────────────────────────────────
   * A scene row has to say what the OBJECT is, and the raw kind does not:
   * "segment" is a trendline, a neckline and a divergence leg depending on
   * which tool made it. `owner` is the tool, stamped server-side on every
   * annotation, so it is the honest source for the name. Falling back to the
   * kind rather than to the owner string keeps an unrecognised tool readable
   * instead of printing an identifier at the user. */
  /* Glyphs come from `Icons.paths` — the 16px line set the toolbar and the
   * legends are drawn from. NOT `Icons.tiles`, which is the illustration set
   * the empty chat is built out of and which happens to answer to some of the
   * same words ("levels", "patterns"). Icons.svg THROWS on an unknown name, so
   * a tile name here does not degrade to a blank square, it takes the whole
   * panel's render with it. */
  const OWNERS = {
    get_levels: { name: "Level", group: "Levels", icon: "hline" },
    get_trendlines: { name: "Trendline", group: "Trendlines", icon: "trend" },
    get_divergences: { name: "Divergence", group: "Divergences", icon: "trendAngle" },
    get_patterns: { name: "Pattern", group: "Patterns", icon: "triangle" },
    get_gaps: { name: "Gap", group: "Gaps", icon: "rect" },
    get_trend: { name: "Trend", group: "Trend", icon: "trend" },
    get_results: { name: "Results", group: "Events", icon: "clock" },
    volume_profile: { name: "Volume profile", group: "Volume profile", icon: "barChart" },
    plan_position: { name: "Position plan", group: "Plans", icon: "position" },
    draw_shape: { name: "Shape", group: "Marks", icon: "brush" },
    mark: { name: "Mark", group: "Marks", icon: "tag" },
    scene: { name: "Annotation", group: "Marks", icon: "brush" },
  };
  const KINDS = {
    level: "Level", zone: "Zone", segment: "Line", box: "Box", vline: "Time line",
    vband: "Session", point: "Point", poly: "Shape", fib: "Fib", drawing: "Shape",
    markers: "Markers", position: "Plan", vprofile: "Volume profile",
    candle: "Candle mark", label: "Label", trade: "Trade", exposure: "Exposure",
  };
  const ownerOf = (o) => OWNERS[o] || OWNERS.scene;

  /* ══ source 1 — detector formations ═══════════════════════════════════ */

  function fromCard(card) {
    if (!card) return [];
    const interval = card.interval || card.timeframe || "1d";
    const charts = (card.chart_patterns || []).map((p) => ({
      id: `chart:${interval}:${p.id || p.pattern}:${p.from || ""}`,
      name: p.pattern || p.name, kind: "Chart pattern", status: p.status || "unconfirmed",
      sceneId: p.id || "",
      from: p.from, to: p.to, interval,
      lookback: card.bars_scanned || 300,
      area: p.measure ? `${p.measure.label || "Level"} ${p.measure.value ?? ""}` : p.bias || "Structure",
      drawn: !!p.drawn,
      detail: p.broke_at ? `Confirmed at ${p.broke_at}.` : "Detector geometry is available for review.",
    }));
    const candles = (card.candles || card.candlesticks || []).map((p, i) => ({
      id: `candle:${interval}:${p.ann || p.t || i}`,
      name: Array.isArray(p.names) ? p.names.join(", ") : p.pattern || p.name,
      kind: "Candle signal", status: p.bias || "detected", from: p.t, to: p.t, interval,
      sceneId: p.ann || "",
      patternNames: Array.isArray(p.names) ? p.names : [p.pattern || p.name].filter(Boolean),
      lookback: card.bars_scanned || 300,
      area: p.bias || "Bar anatomy", drawn: !!(p.drawn || p.ann),
      detail: "Marked at the qualifying bar; inspect the candle body and wick on the selected interval.",
    }));
    return [...charts, ...candles];
  }

  function merge(items) {
    const byId = new Map(ledger.items.map((p) => [p.id, p]));
    for (const item of items) {
      if (ledger.deleted[item.id]) continue;
      const old = byId.get(item.id) || {};
      byId.set(item.id, {
        ...old, ...item,
        pinned: !!old.pinned,
        drawn: !!(old.drawn || item.drawn),
      });
    }
    ledger.items = [...byId.values()].slice(-500);
    persist();
  }

  /** Pattern rows, reconciled against the chart. A restored layout can put
   *  annotations back before the detector card is replayed, so `live` is read
   *  off the scene by the detector's stable link rather than trusted from a
   *  stale `drawn` flag. */
  function patternRows() {
    const items = scene() ? scene().inventory() : [];
    const byKey = new Map(items.map((i) => [i.key, i]));
    return ledger.items
      .filter((p) => !ledger.deleted[p.id])
      .map((p) => {
        const on = p.sceneId && byKey.get(p.sceneId);
        return {
          species: "pattern",
          rowId: `p:${p.id}`,
          key: p.id,
          sceneKey: p.sceneId || "",
          name: title(p.name) || "Pattern",
          group: p.kind === "Candle signal" ? "Candle signals" : "Patterns",
          icon: p.kind === "Candle signal" ? "candles" : "triangle",
          meta: sentence(p.area),
          status: title(p.status),
          interval: p.interval,
          from: p.from, to: p.to,
          detail: p.detail,
          pinned: !!p.pinned,
          // Present on the chart at all — drawn and not switched off.
          live: !!(on && !on.hidden),
          // Fetched once and parked: the switch is now free.
          parked: !!(on && on.hidden),
          removable: true,
        };
      });
  }

  /* ══ source 2 — what the chat drew ════════════════════════════════════ */

  /** Scene annotations that are NOT a pattern's geometry. A formation already
   *  has a row of its own with the detector's language on it; listing its
   *  segments again underneath would be the same object twice, named worse. */
  function sceneRows() {
    const claimed = new Set(ledger.items.map((p) => p.sceneId).filter(Boolean));
    return (scene() ? scene().inventory() : [])
      .filter((i) => !claimed.has(i.key))
      .map((i) => {
        const o = ownerOf(i.owner);
        return {
          species: "scene",
          rowId: `s:${i.key}`,
          key: i.key,
          sceneKey: i.key,
          name: i.label || o.name,
          group: o.group,
          icon: o.icon,
          meta: KINDS[i.kind] || title(i.kind),
          status: i.legs > 1 ? `${i.legs} parts` : "",
          interval: "",
          detail: `Drawn by ${i.owner === "scene" ? "the chart" : i.owner}.`,
          live: !i.hidden,
          removable: true,
        };
      });
  }

  /* ══ source 3 — the user's own shapes ═════════════════════════════════ */

  function drawingRows() {
    return (draw() ? draw().inventory() : []).map((d) => ({
      species: "drawing",
      rowId: `d:${d.key}`,
      key: d.key,
      name: d.label || title(d.kind),
      group: "Your drawings",
      // The tool's OWN glyph where there is one — a trendline row wearing the
      // trendline icon is the whole reason this list can go without a "type"
      // column. `Icons.paths` is the registry, so this asks it directly.
      icon: (Icons.paths && Icons.paths[d.kind]) ? d.kind : "brush",
      meta: d.ref || "",
      status: d.locked ? "Locked" : "",
      interval: "",
      detail: `Placed by you${d.locked ? ", and locked against edits" : ""}.`,
      live: !d.hidden,
      removable: true,
    }));
  }

  /* ══ the merged list ══════════════════════════════════════════════════ */

  /** Everything, in one order: on-chart first — the panel's own subject is
   *  what the chart is showing — then by group so like sits with like. */
  function rows() {
    const all = [...patternRows(), ...sceneRows(), ...drawingRows()];
    const q = query.trim().toLowerCase();
    return all
      .filter((r) => {
        if (onlyLive && !r.live) return false;
        if (!q) return true;
        return `${r.name} ${r.group} ${r.meta} ${r.status}`.toLowerCase().includes(q);
      })
      /* GROUP FIRST, and that ordering is load-bearing rather than taste.
       * Sorting by live before group splits a section: switch one of four
       * levels off and the list grows a second "Levels" heading further down,
       * so the headings stop being a map of the chart and start being an
       * artefact of which rows happen to be on. Within a group, what is on the
       * candles rises — the same signal, where it cannot break the sections. */
      .sort((a, b) => a.group.localeCompare(b.group)
        || Number(!!b.pinned) - Number(!!a.pinned)
        || Number(!!b.live) - Number(!!a.live)
        || a.name.localeCompare(b.name));
  }

  /* The badge on the chart button reads THIS, so it is on the repaint path —
   * scene.js asks for it through `foldState` on every pan, zoom and tick. The
   * three row builders each walk the scene inventory and the ledger, which is
   * nothing once and pointless sixty times a second while a chart is being
   * dragged. So it is computed on demand and cached until something that could
   * change it fires: the same three events this module already re-renders on.
   *
   * The count has to come from here rather than from `scene.count()` — which
   * is what the button used to read — because the panel and its own button
   * must not disagree. A pattern that has been detected but never drawn is a
   * row in this list and is not in the scene at all, so the button said "24"
   * over a panel headed "24 / 29" and neither number explained the other. */
  let countCache = null;
  const counts = () => {
    if (countCache) return countCache;
    const all = [...patternRows(), ...sceneRows(), ...drawingRows()];
    countCache = { total: all.length, live: all.filter((r) => r.live).length };
    return countCache;
  };
  const invalidate = () => { countCache = null; };

  /* ══ the switch ═══════════════════════════════════════════════════════ */

  const notify = (message) => {
    if (typeof Layouts !== "undefined" && Layouts.toast) Layouts.toast(message);
    else console.warn(`[charto] ${message}`);
  };

  /** Turn one row on or off. Three species, one gesture — and only the
   *  pattern's first ON costs a request. */
  async function toggle(row, button) {
    if (row.species === "drawing") {
      draw().setHiddenFor([row.key], row.live);
      document.dispatchEvent(new Event("charto:drawings-changed"));
      return refresh();
    }
    if (row.species === "scene" || (row.species === "pattern" && (row.live || row.parked))) {
      scene().setHiddenFor([row.sceneKey], row.live);
      if (row.species === "pattern") {
        const item = ledger.items.find((p) => p.id === row.key);
        if (item) { item.drawn = true; persist(); }
      }
      return refresh();
    }
    // A pattern being shown for the first time: fetch the geometry.
    return drawPattern(row, button);
  }

  async function drawPattern(row, button) {
    if (!scene()) return notify("The chart is still loading");
    const p = ledger.items.find((x) => x.id === row.key);
    if (!p) return;
    const original = button ? button.innerHTML : "";
    if (button) {
      button.disabled = true;
      button.classList.add("is-loading");
      button.innerHTML = icon("loader");
    }
    try {
      const params = new URLSearchParams({
        symbol,
        interval: p.interval || window.__charto.interval || "1d",
        lookback_bars: String(p.lookback || 300),
      });
      if (p.kind === "Candle signal") {
        params.set("family", "candlestick");
        params.set("at", p.from || "");
        params.set("kinds", (p.patternNames || []).join(","));
      } else {
        params.set("family", "chart");
        params.set("id", p.sceneId || "");
      }
      const response = await fetch(`${API}/patterns/draw?${params}`);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        const err = new Error(payload.error || "Could not draw that pattern");
        err.unavailable = !!payload.unavailable;
        throw err;
      }
      const patch = Array.isArray(payload.scene) ? payload.scene : [];
      if (!patch.length) throw new Error("No pattern geometry was returned");
      scene().apply(patch);
      p.drawn = true;
      p.sceneId = p.sceneId
        || patch.find((item) => item.link || item.id)?.link
        || patch.find((item) => item.id)?.id || "";
      persist();
      // Showing must not navigate. The user's interval, zoom and scroll stay
      // exactly where they left them; the hover is the only pointer we move.
      scene().setHover(p.sceneId || null);
      refresh();
      notify(`${title(p.name)} shown on ${p.interval}`);
    } catch (error) {
      // A formation the server cannot reach is GONE, not broken — the bar it
      // was found on has aged out of the furthest window this interval can
      // scan, and no later click will change that. So the row goes with it
      // rather than sitting in the list advertising a button that can only
      // fail. Only on `unavailable`: a timeout or a dropped connection is a
      // transient failure and must never delete anything.
      if (error && error.unavailable) {
        ledger.deleted[p.id] = Date.now();
        ledger.items = ledger.items.filter((x) => x.id !== p.id);
        expanded.delete(row.rowId);
        persist();
        notify(`${title(p.name)} has aged out of this chart's history — removed`);
        repaintChip();
        refresh();
        return;
      }
      notify(error.message || "Could not draw that pattern");
    } finally {
      if (button && button.isConnected) {
        button.disabled = false;
        button.classList.remove("is-loading");
        button.innerHTML = original;
      }
    }
  }

  function remove(row) {
    if (row.species === "drawing") {
      draw().remove(row.key);
      document.dispatchEvent(new Event("charto:drawings-changed"));
    } else if (row.species === "pattern") {
      ledger.deleted[row.key] = Date.now();
      ledger.items = ledger.items.filter((p) => p.id !== row.key);
      if (row.sceneKey) scene().removeFor([row.sceneKey]);
      persist();
    } else {
      scene().removeFor([row.key]);
    }
    expanded.delete(row.rowId);
    repaintChip();
    refresh();
  }

  /** Delete every row the list is CURRENTLY showing.
   *
   *  Scoped to the visible list on purpose, which is what makes one button
   *  enough: the search box and the on-chart filter already narrow this panel,
   *  so "spinning top" then sweep is a targeted bulk delete, and an empty
   *  search is "clear the lot". A separate multi-select mode would be a second
   *  way to express a selection this panel can already express.
   *
   *  Armed rather than confirmed in a dialog — one click asks, the next does
   *  it, and anything else disarms. A modal over a popover that closes on
   *  outside-click is a fight, and 226 rows is exactly when a user wants this
   *  to take two clicks rather than five. */
  function removeListed() {
    const list = rows();
    if (!list.length) return;
    const sceneKeys = [];
    for (const r of list) {
      if (r.species === "drawing") {
        draw().remove(r.key);
      } else if (r.species === "pattern") {
        ledger.deleted[r.key] = Date.now();
        if (r.sceneKey) sceneKeys.push(r.sceneKey);
      } else if (r.key) {
        sceneKeys.push(r.key);
      }
      expanded.delete(r.rowId);
    }
    const gone = new Set(list.filter((r) => r.species === "pattern").map((r) => r.key));
    if (gone.size) ledger.items = ledger.items.filter((p) => !gone.has(p.id));
    if (sceneKeys.length && scene()) scene().removeFor(sceneKeys);
    if (list.some((r) => r.species === "drawing")) {
      document.dispatchEvent(new Event("charto:drawings-changed"));
    }
    persist();
    armedSweep = false;
    notify(`Deleted ${list.length} layer${list.length === 1 ? "" : "s"}`);
    repaintChip();
    refresh();
  }

  /** Every row at once — the global fold, kept as a control on the list it
   *  acts on rather than as a second button on the chart. */
  function setAll(on) {
    const all = rows();
    const sceneKeys = all.filter((r) => r.species !== "drawing" && r.sceneKey)
      .map((r) => r.sceneKey);
    const drawIds = all.filter((r) => r.species === "drawing").map((r) => r.key);
    if (sceneKeys.length && scene()) scene().setHiddenFor(sceneKeys, !on);
    if (drawIds.length && draw()) {
      draw().setHiddenFor(drawIds, !on);
      document.dispatchEvent(new Event("charto:drawings-changed"));
    }
    refresh();
  }

  /* ══ rendering ════════════════════════════════════════════════════════ */

  function rowHtml(r) {
    const open = expanded.has(r.rowId);
    const date = r.from
      ? `${shortDate(r.from)}${r.to && r.to !== r.from ? ` → ${shortDate(r.to)}` : ""}`
      : "";
    const state = r.live ? "On the chart" : r.parked ? "Ready to show" : "Not shown";
    return `<article class="lyr-row${r.live ? " is-live" : ""}${open ? " expanded" : ""}" data-row="${esc(r.rowId)}">
      <button class="lyr-eye" type="button" data-lyr-toggle="${esc(r.rowId)}"
        aria-pressed="${r.live}" aria-label="${r.live ? "Hide" : "Show"} ${esc(r.name)}"
        title="${r.live ? "Hide" : "Show"}">${icon(r.live ? "eye" : "eyeOff")}</button>
      <span class="lyr-glyph" aria-hidden="true">${icon(r.icon)}</span>
      <span class="lyr-name" title="${esc(r.name)}">${esc(r.name)}</span>
      <span class="lyr-meta">${esc(r.meta)}</span>
      <span class="lyr-date"${date ? ` title="${esc(date)}"` : ""}>${esc(dayOf(r.to || r.from))}</span>
      <span class="lyr-actions">
        ${r.species === "pattern" ? `<button class="lyr-act${r.pinned ? " active" : ""}" type="button" data-lyr-pin="${esc(r.rowId)}" aria-label="${r.pinned ? "Unpin" : "Pin"}" title="${r.pinned ? "Unpin" : "Pin"}">${icon("pin")}</button>` : ""}
        <button class="lyr-act" type="button" data-lyr-detail="${esc(r.rowId)}" aria-expanded="${open}" aria-label="${open ? "Close details" : "Details"}" title="${open ? "Close" : "Details"}">${icon("chevronDown")}</button>
        <button class="lyr-act danger" type="button" data-lyr-del="${esc(r.rowId)}" aria-label="Delete ${esc(r.name)}" title="Delete">${icon("trash")}</button>
      </span>
      <div class="lyr-detail">
        <div class="lyr-detail-grid">
          <span>State</span><strong>${esc(state)}</strong>
          ${r.status ? `<span>Reading</span><strong>${esc(r.status)}</strong>` : ""}
          ${r.interval ? `<span>Interval</span><strong>${esc(r.interval)}</strong>` : ""}
          ${date ? `<span>Window</span><strong>${esc(date)}</strong>` : ""}
        </div>
        <p class="lyr-note">${esc(r.detail)}</p>
      </div>
    </article>`;
  }

  /** Rows carry their group as a heading the first time it changes, so the
   *  list reads as sections without a second nesting level to keep in sync. */
  function listHtml(list) {
    if (!list.length) {
      return `<div class="lyr-empty">${query || onlyLive
        ? "Nothing matches" : "Nothing is drawn yet"}</div>`;
    }
    let group = null;
    return list.map((r) => {
      const head = r.group !== group
        ? `<div class="lyr-group">${esc(r.group)}</div>` : "";
      group = r.group;
      return head + rowHtml(r);
    }).join("");
  }

  function render(target) {
    host = target || host;
    if (!host || !host.isConnected) return;
    /* KEEP THE READER WHERE THEY WERE.
     *
     * Every action in this panel ends in render(), and render() replaces the
     * whole host — so showing a pattern, opening its details or deleting a row
     * rebuilt `.lyr-body` and reset its scrollTop to zero. With 226 rows that
     * is not a small jump: click anything below the fold and the list throws
     * you back to the top, having lost the row you were working on.
     *
     * The search box had the same wound in a worse form: its `input` handler
     * calls render(), which destroys the input mid-keystroke, so focus and
     * caret were gone after every character. Both are saved across the swap. */
    const oldBody = host.querySelector(".lyr-body");
    const keepTop = oldBody ? oldBody.scrollTop : 0;
    const focused = document.activeElement;
    const typing = focused && focused.id === "layerSearch";
    const caret = typing ? focused.selectionStart : null;
    const list = rows();
    const c = counts();
    const allOn = c.total > 0 && c.live === c.total;
    host.innerHTML = `
      <div class="lyr-head">
        <div class="lyr-title">Layers<span class="lyr-count">${c.live}/${c.total}</span></div>
        <div class="lyr-head-tools">
          <label class="lyr-search" title="Search"><span>${icon("search")}</span><input id="layerSearch" type="search" aria-label="Search layers" placeholder="Search" value="${esc(query)}"></label>
          <button class="lyr-tool${onlyLive ? " active" : ""}" type="button" data-lyr-filter aria-pressed="${onlyLive}" aria-label="${onlyLive ? "Show everything" : "Show only what is on the chart"}" title="${onlyLive ? "Show all" : "On chart only"}">${icon("layers")}</button>
          <button class="lyr-tool" type="button" data-lyr-all="${allOn ? "off" : "on"}" aria-label="${allOn ? "Hide every layer" : "Show every layer"}" title="${allOn ? "Hide all" : "Show all"}">${icon(allOn ? "eyeOff" : "eye")}</button>
          <button class="lyr-tool danger${armedSweep ? " armed" : ""}" type="button" data-lyr-sweep aria-label="Delete the ${list.length} layers listed" title="${armedSweep ? `Delete ${list.length}?` : `Delete all ${list.length} listed`}">${icon(armedSweep ? "check" : "trash")}</button>
        </div>
      </div>
      <div class="lyr-body">${listHtml(list)}</div>`;
    const body = host.querySelector(".lyr-body");
    if (body && keepTop) body.scrollTop = keepTop;
    const search = host.querySelector("#layerSearch");
    if (search) {
      search.addEventListener("input", (e) => { query = e.target.value; render(); });
      if (typing) {
        search.focus();
        try { search.setSelectionRange(caret, caret); } catch { /* not text */ }
      }
    }
    if (!host.dataset.lyrBound) {
      host.addEventListener("click", onClick);
      // Pointing at a row lights the annotation up on the chart, which is the
      // cheapest possible answer to "which one is that?" — and the reason the
      // list can stay this terse.
      host.addEventListener("pointerover", onHover);
      host.addEventListener("pointerleave", () => scene() && scene().setHover(null));
      host.dataset.lyrBound = "true";
    }
  }

  let armedSweep = false;

  function find(rowId) { return rows().find((r) => r.rowId === rowId); }

  function onHover(e) {
    const el = e.target.closest("[data-row]");
    if (!el || !scene()) return;
    const r = find(el.dataset.row);
    scene().setHover(r && r.live && r.sceneKey ? r.sceneKey : null);
  }

  function onClick(e) {
    const t = e.target.closest("[data-lyr-toggle],[data-lyr-pin],[data-lyr-detail],"
      + "[data-lyr-del],[data-lyr-filter],[data-lyr-all],[data-lyr-sweep]");
    if (!t) return;
    e.stopPropagation();
    // Anything that is not the sweep itself disarms it. An armed destructive
    // button that survives a change of mind is the whole reason to arm one.
    if (armedSweep && !t.hasAttribute("data-lyr-sweep")) {
      armedSweep = false;
      render();
    }
    if (t.hasAttribute("data-lyr-sweep")) {
      if (!armedSweep) { armedSweep = true; return render(); }
      return removeListed();
    }
    if (t.hasAttribute("data-lyr-filter")) {
      onlyLive = !onlyLive; return render();
    }
    if (t.hasAttribute("data-lyr-all")) {
      return setAll(t.dataset.lyrAll === "on");
    }
    const id = t.dataset.lyrToggle || t.dataset.lyrPin || t.dataset.lyrDetail
      || t.dataset.lyrDel;
    const r = find(id);
    if (!r) return;
    if (t.hasAttribute("data-lyr-toggle")) return toggle(r, t);
    if (t.hasAttribute("data-lyr-del")) return remove(r);
    if (t.hasAttribute("data-lyr-pin")) {
      const item = ledger.items.find((p) => p.id === r.key);
      if (item) { item.pinned = !item.pinned; persist(); refresh(); }
      return;
    }
    expanded.has(id) ? expanded.delete(id) : expanded.add(id);
    render();
  }

  /* ══ keeping the list current ═════════════════════════════════════════ */

  async function loadRemote() {
    if (typeof Auth === "undefined" || !Auth.user) return;
    try {
      const remote = await Auth.loadWorkspace(symbol);
      const state = remote && remote.state && remote.state.pattern_ledger;
      if (!state) return;
      const localDeleted = ledger.deleted;
      ledger.deleted = { ...(state.deleted || {}), ...localDeleted };
      const local = ledger.items;
      ledger.items = Array.isArray(state.items) ? state.items : [];
      merge(local);
      refresh();
    } catch { /* the local ledger remains usable offline */ }
  }

  if (typeof Auth !== "undefined") Auth.onChange((user) => { if (user) loadRemote(); });
  if (typeof Universe !== "undefined") Universe.load().then(refresh);
  document.addEventListener("charto:scene-changed", () => { invalidate(); refresh(); });
  document.addEventListener("charto:drawings-changed", () => { invalidate(); refresh(); });
  document.addEventListener("charto:layers-refresh", () => { invalidate(); render(); });

  return {
    /** Kept under the old name so js/cards.js does not have to change: a
     *  detector card still hands its formations straight to the ledger. */
    setPatterns(card) {
      merge(fromCard(card));
      expanded.clear();
      refresh();
    },
    render,
    counts,
    setAll,
  };
})();

/* The drawer this file replaces. js/cards.js calls PatternDrawer.setPatterns
 * on every detector card, and a build that loaded a stale cards.js against a
 * fresh index.html would silently stop recording formations. Aliasing costs
 * one line and makes that impossible. */
window.PatternDrawer = window.LayersPanel;
