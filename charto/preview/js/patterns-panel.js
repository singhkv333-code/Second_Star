/* Chart-pattern review drawer. It displays detector payloads verbatim. */
"use strict";

window.PatternDrawer = (() => {
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";
  let latest = null;
  let query = "";
  let filter = "all"; // all → on chart → inventory
  const expanded = new Set();
  const symbol = ((window.__charto && window.__charto.symbol)
    || new URLSearchParams(location.search).get("symbol") || "RELIANCE").toUpperCase();
  const saved = Store.get("pattern_ledger", null);
  let ledger = saved && !Array.isArray(saved)
    ? { items: Array.isArray(saved.items) ? saved.items : [], deleted: saved.deleted || {} }
    : { items: Array.isArray(saved) ? saved : [], deleted: {} };
  let saveTimer = null;
  const esc = (v) => String(v == null ? "" : v).replace(/[&<>\"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const title = (v) => String(v || "Pattern").replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
  const sentence = (v) => {
    const s = String(v == null || v === "" ? "—" : v).replace(/_/g, " ");
    return s.charAt(0).toUpperCase() + s.slice(1);
  };
  const shortDate = (v) => String(v || "—").replace(/\s+\d{4}(?=\s|$)/, "");
  const icon = (name) => Icons.svg(name, "xs");

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

  function records() {
    const scene = window.__charto?.scene?.state?.items || [];
    const sceneKeys = new Set(scene.flatMap((item) => [item.id, item.link]).filter(Boolean));
    return ledger.items
      .filter((p) => !ledger.deleted[p.id])
      // A restored layout can put annotations back on the scene before the
      // detector card is replayed. Reconcile by the detector's stable link so
      // the drawer reflects the chart instead of a stale `drawn: false`.
      .map((p) => ({ ...p, drawn: !!(p.drawn || (p.sceneId && sceneKeys.has(p.sceneId))) }))
      .sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned)
        || Number(!!b.wishlist) - Number(!!a.wishlist)
        || Number(!!b.drawn) - Number(!!a.drawn));
  }

  function persist() {
    Store.set("pattern_ledger", ledger);
    clearTimeout(saveTimer);
    if (typeof Auth === "undefined" || !Auth.user) return;
    saveTimer = setTimeout(() => {
      Auth.saveWorkspace(symbol, { pattern_ledger: ledger }).catch(() => {});
    }, 350);
  }

  function merge(items) {
    const byId = new Map(ledger.items.map((p) => [p.id, p]));
    for (const item of items) {
      if (ledger.deleted[item.id]) continue;
      const old = byId.get(item.id) || {};
      byId.set(item.id, {
        ...old, ...item,
        pinned: !!old.pinned,
        wishlist: !!old.wishlist,
        drawn: !!(old.drawn || item.drawn),
      });
    }
    ledger.items = [...byId.values()].slice(-500);
    persist();
  }

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
      document.dispatchEvent(new Event("charto:patterns-panel-refresh"));
    } catch { /* local ledger remains usable offline */ }
  }

  if (typeof Auth !== "undefined") Auth.onChange((user) => { if (user) loadRemote(); });
  if (typeof Universe !== "undefined") {
    Universe.load().then(() => document.dispatchEvent(
      new Event("charto:patterns-panel-refresh")));
  }
  document.addEventListener("charto:scene-changed", () => {
    const keys = new Set((window.__charto?.scene?.state?.items || [])
      .flatMap((item) => [item.id, item.link]).filter(Boolean));
    let changed = false;
    for (const item of ledger.items) {
      if (!item.sceneId) continue;
      const drawn = keys.has(item.sceneId);
      if (item.drawn !== drawn) { item.drawn = drawn; changed = true; }
    }
    if (changed) persist();
    document.dispatchEvent(new Event("charto:patterns-panel-refresh"));
  });
  document.addEventListener("charto:drawings-changed", () => document.dispatchEvent(
    new Event("charto:patterns-panel-refresh")));

  function visible() {
    const q = query.trim().toLowerCase();
    return records().filter((p) => {
      const matches = !q || `${p.name} ${p.status} ${p.kind}`.toLowerCase().includes(q);
      const inView = filter === "all"
        || (filter === "drawn" ? p.drawn : !p.drawn);
      return matches && inView;
    });
  }

  function row(p) {
    const open = expanded.has(p.id);
    const date = `${shortDate(p.from)}${p.to && p.to !== p.from ? ` → ${shortDate(p.to)}` : ""}`;
    const chartState = p.drawn ? "Visible on chart" : "Available to draw";
    const chartAction = p.drawn ? "Remove from chart" : "Draw on chart";
    return `<article class="pat-row${p.drawn ? " on-chart" : ""}${open ? " expanded" : ""}" data-pattern="${esc(p.id)}">
      <div class="pat-line">
        <div class="pat-primary"><strong class="pat-name">${esc(title(p.name))}</strong>${p.pinned ? `<span class="pat-pin-mark" aria-label="Pinned" data-tip="Pinned">${icon("pin")}</span>` : ""}</div>
        <span class="pat-cell pat-area">${esc(sentence(p.area))}</span>
        <span class="pat-cell pat-confirm">${esc(title(p.status))}</span>
        <span class="pat-cell pat-date">${esc(date)}</span>
        <span class="pat-cell pat-interval">${esc(p.interval)}</span>
        <span class="pat-hover-actions">
          <button class="pat-icon-action${p.pinned ? " active" : ""}" type="button" data-pattern-pin="${esc(p.id)}" aria-label="${p.pinned ? "Unpin pattern" : "Pin pattern"}" aria-pressed="${!!p.pinned}" data-tip="${p.pinned ? "Unpin" : "Pin"}">${icon("pin")}</button>
          <button class="pat-icon-action${p.wishlist ? " active" : ""}" type="button" data-pattern-wish="${esc(p.id)}" aria-label="${p.wishlist ? "Remove from wishlist" : "Add to wishlist"}" aria-pressed="${!!p.wishlist}" data-tip="${p.wishlist ? "Remove from wishlist" : "Wishlist"}">${icon("star")}</button>
          <button class="pat-icon-action${p.drawn ? " is-drawn" : ""}" type="button" data-pattern-draw="${esc(p.id)}" aria-label="${chartAction}" aria-pressed="${!!p.drawn}" data-tip="${chartAction}">${icon("eye")}</button>
          <button class="pat-icon-action pat-detail" type="button" data-pattern-detail="${esc(p.id)}" aria-label="${open ? "Close details" : "Open details"}" aria-expanded="${open}" data-tip="${open ? "Close details" : "Details"}">${icon("chevronDown")}</button>
          <button class="pat-icon-action danger" type="button" data-pattern-delete="${esc(p.id)}" aria-label="Delete pattern" data-tip="Delete">${icon("trash")}</button>
        </span>
      </div>
      <div class="pat-expanded">
        <div class="pat-detail-layout">
          <div class="pat-detail-focus"><span>Key area</span><strong>${esc(sentence(p.area))}</strong><small>${esc(title(p.status))} · ${esc(p.interval)}</small></div>
          <div class="pat-window" aria-label="Formation from ${esc(shortDate(p.from))} to ${esc(shortDate(p.to || p.from))}">
            <span class="pat-window-title">Formation window</span>
            <div class="pat-window-track"><i></i></div>
            <div class="pat-window-labels"><span>${esc(shortDate(p.from))}</span><span>${esc(shortDate(p.to || p.from))}</span></div>
          </div>
        </div>
        <div class="pat-detail-meta"><span>${esc(p.kind)}</span><span>${esc(chartState)}</span></div>
        <p class="pat-detail-note">${esc(p.detail)}</p>
      </div>
    </article>`;
  }

  function render(host) {
    if (!host) return;
    const all = records(); const list = visible();
    const filterTitle = filter === "all" ? "Show charted only" : filter === "drawn" ? "Show undrawn only" : "Show all patterns";
    const company = (latest && latest.symbol) || symbol;
    const companyLabel = (typeof Universe !== "undefined" && Universe.label(company)) || company;
    host.style.width = `${Math.max(280, Math.min(620, Number(Store.get("patterns_width", 344)) || 344))}px`;
    host.innerHTML = `<div class="pat-resize" data-pattern-resize aria-hidden="true"></div>
      <div class="side-head"><button class="pat-company" type="button" data-pattern-company aria-label="Change company" data-tip="${esc(companyLabel)}"><span>${esc(companyLabel)}</span>${icon("chevronDown")}</button>
      <div class="pat-head-tools">
        <label class="pat-search" data-tip="Search"><span>${icon("search")}</span><input id="patternSearch" type="search" aria-label="Search patterns" placeholder="Search" value="${esc(query)}"></label>
        <button class="pat-tool${filter !== "all" ? " active" : ""}" type="button" data-pattern-filter aria-label="${filterTitle}" data-tip="${filterTitle}">${icon("layers")}</button>
      </div></div>
      <div class="side-body pat-scroll"><div class="pat-table">
        <div class="pat-table-head"><span>Formation</span><span>Key area</span><span>Confirmation</span><span>Dates</span><span>Interval</span></div>
        <div class="pat-list">${list.length ? list.map(row).join("") : `<div class="pat-empty">No matching formations</div>`}</div>
      </div></div>`;
    host.querySelector("#patternSearch")?.addEventListener("input", (e) => { query = e.target.value; render(host); });
    if (!host.dataset.patternActionsBound) {
      host.addEventListener("click", onClick);
      host.dataset.patternActionsBound = "true";
    }
    bindResize(host);
  }

  function bindResize(host) {
    if (host.dataset.patternResizeBound) return;
    host.dataset.patternResizeBound = "true";
    host.addEventListener("pointerdown", (e) => {
      if (!e.target.closest("[data-pattern-resize]")) return;
      const startX = e.clientX, startW = host.getBoundingClientRect().width;
      const move = (ev) => {
        const width = Math.max(280, Math.min(620, startW + startX - ev.clientX));
        host.style.width = `${width}px`;
      };
      const up = () => {
        removeEventListener("pointermove", move);
        removeEventListener("pointerup", up);
        Store.set("patterns_width", Math.round(host.getBoundingClientRect().width));
      };
      addEventListener("pointermove", move);
      addEventListener("pointerup", up, { once: true });
      e.preventDefault();
    });
  }

  const notify = (message) => {
    if (typeof Layouts !== "undefined" && Layouts.toast) Layouts.toast(message);
    else console.warn(`[charto] ${message}`);
  };

  async function drawNow(p, button) {
    if (!window.__charto?.scene) return notify("The chart is still loading");
    const original = button.innerHTML;
    button.disabled = true;
    button.classList.add("is-loading");
    button.innerHTML = icon("loader");
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
      if (!response.ok) throw new Error(payload.error || "Could not draw that pattern");
      const patch = Array.isArray(payload.scene) ? payload.scene : [];
      if (!patch.length) throw new Error("No pattern geometry was returned");
      window.__charto.scene.apply(patch);

      const savedItem = ledger.items.find((item) => item.id === p.id);
      if (savedItem) {
        savedItem.drawn = true;
        savedItem.sceneId = savedItem.sceneId
          || patch.find((item) => item.link || item.id)?.link
          || patch.find((item) => item.id)?.id || "";
      }
      persist();
      document.dispatchEvent(new Event("charto:patterns-panel-refresh"));
      const key = savedItem?.sceneId || p.sceneId;
      // Drawing must not navigate the chart. Keep the user's interval window,
      // zoom and scroll position exactly where they left them.
      window.__charto.scene.setHover(key || null);
      notify(`${title(p.name)} drawn on ${p.interval}`);
    } catch (error) {
      notify(error.message || "Could not draw that pattern");
    } finally {
      if (button.isConnected) {
        button.disabled = false;
        button.classList.remove("is-loading");
        button.innerHTML = original;
      }
    }
  }

  function onClick(e) {
    const company = e.target.closest("[data-pattern-company]");
    if (company && typeof Universe !== "undefined") {
      e.stopPropagation();
      Universe.open({
        anchor: company,
        current: symbol,
        note: "Patterns are kept separately for each company.",
        onPick(next) {
          if (!next || next === symbol) return;
          const url = new URL(location.href);
          url.searchParams.set("symbol", next);
          location.href = url.toString();
        },
      });
      return;
    }
    const pin = e.target.closest("[data-pattern-pin]");
    const wish = e.target.closest("[data-pattern-wish]");
    const del = e.target.closest("[data-pattern-delete]");
    if (pin || wish || del) {
      const id = (pin && pin.dataset.patternPin)
        || (wish && wish.dataset.patternWish) || del.dataset.patternDelete;
      const item = ledger.items.find((p) => p.id === id);
      if (!item) return;
      if (pin) item.pinned = !item.pinned;
      if (wish) item.wishlist = !item.wishlist;
      if (del) {
        ledger.deleted[id] = Date.now();
        ledger.items = ledger.items.filter((p) => p.id !== id);
        expanded.delete(id);
      }
      persist();
      document.dispatchEvent(new Event("charto:patterns-panel-refresh"));
      return;
    }
    const detail = e.target.closest("[data-pattern-detail]");
    if (detail) {
      const id = detail.dataset.patternDetail;
      expanded.has(id) ? expanded.delete(id) : expanded.add(id);
      document.dispatchEvent(new Event("charto:patterns-panel-refresh"));
      return;
    }
    const draw = e.target.closest("[data-pattern-draw]");
    if (draw) {
      const p = records().find((x) => x.id === draw.dataset.patternDraw);
      if (!p) return;
      if (p.drawn && window.__charto?.scene) {
        window.__charto.scene.setItems(window.__charto.scene.state.items.filter(
          (item) => item.link !== p.sceneId && item.id !== p.sceneId));
        const savedItem = ledger.items.find((item) => item.id === p.id);
        if (savedItem) savedItem.drawn = false;
        persist();
        document.dispatchEvent(new Event("charto:patterns-panel-refresh"));
        notify(`${title(p.name)} removed from chart`);
        return;
      }
      drawNow(p, draw);
      return;
    }
    if (e.target.closest("[data-pattern-filter]")) {
      filter = filter === "all" ? "drawn" : filter === "drawn" ? "open" : "all";
      document.dispatchEvent(new Event("charto:patterns-panel-refresh"));
    }
  }

  return {
    setPatterns(card) {
      latest = card;
      merge(fromCard(card));
      expanded.clear();
      document.dispatchEvent(new Event("charto:patterns-panel-refresh"));
    },
    render,
  };
})();
