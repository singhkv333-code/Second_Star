/* Charto preview — the Portfolio widget.
 *
 * The user's PAPER book (data/paper.py): what they hold, what it cost, what it
 * is worth now, and how it is split. A ring, not a filled pie — the hole holds
 * the total, and a ring's arcs are compared by length, which the eye does far
 * better than wedge angles. Every number is the server's (/paper/summary and
 * /paper/holdings, live-marked on read); this file only arranges them.
 *
 * Simulated money, and it says so in its footer. Nothing here places an order.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined") return;
  const { esc, ic, num, pct, dir, empty, skel } = WKit;
  // whole rupees: a holdings total to the paisa reads as noise
  const inr = (v) => WKit.inr(v == null || !Number.isFinite(+v) ? v : Math.round(+v));
  const POLL_MS = 30_000;
  // Calm, distinguishable, and none of them the up/down colours, which keep
  // their one meaning in the table beside the ring.
  const PALETTE = ["#4f6bed", "#14a3b8", "#d4a72c", "#8b5cf6", "#e07a3f", "#3fa877",
                   "#d6517d", "#5c7186", "#a3b33a", "#2f8fd8"];
  const OTHER = "#9aa3ad";
  const TAU = Math.PI * 2;

  function mount(host, ctx) {
    let sum = null, rows = null, err = "", timer = 0, seq = 0, hot = null, open = null, at = 0;
    host.innerHTML =
      `<div class="nw-head pf-head">` +
        `<div class="dk-seg pf-by"><button type="button" data-by="stock">Holdings</button><button type="button" data-by="sector">Sectors</button></div>` +
        `<span class="sh-gap"></span>` +
        `<button type="button" class="sh-btn i" data-pf="refresh" title="Refresh">${ic("rotateCw")}</button>` +
      `</div>` +
      `<div class="side-body pf-body"></div>` +
      `<div class="nw-foot pf-foot"></div>`;
    const body = host.querySelector(".pf-body"), foot = host.querySelector(".pf-foot");

    const signedIn = () => typeof Auth !== "undefined" && !!Auth.user;
    // The user's portfolio is the paper book in Pivot's API — the account the
    // Home, Portfolio and Paper pages show — not charto's own book, which
    // this widget used to read and which is empty for anyone who trades from
    // those pages. Same-origin under /pv (nginx and the shell both route it);
    // Pivot accepts the charto session as its bearer.
    const PIVOT = location.port === "5173" ? "http://127.0.0.1:8000" : "/pv";
    async function get(path) {
      const r = await fetch(PIVOT + path, { headers: typeof Auth !== "undefined" ? Auth.headers() : {} });
      let d = null;
      try { d = await r.json(); } catch {}
      if (!r.ok) throw new Error((d && d.error) || `HTTP ${r.status}`);
      return d;
    }

    async function load(force) {
      const my = ++seq;
      if (!signedIn()) { sum = rows = null; return paint(); }
      if (!rows || force) body.innerHTML = skel(8, "news");
      try {
        const [s, h] = await Promise.all([get("/paper/summary"), get("/paper/holdings")]);
        if (my !== seq) return;
        sum = s; rows = Array.isArray(h) ? h : []; err = ""; at = Date.now();
      } catch (e) {
        if (my !== seq) return;
        err = e.message || String(e);
      }
      paint();
    }

    /** The ring's slices: one per holding (or sector), the smallest folded
     *  into "Others" past `max` so a 40-stock book stays readable. */
    function slices() {
      const by = ctx.cfg.by === "sector" ? "sector" : "stock";
      const total = rows.reduce((a, r) => a + Math.max(0, r.market_value), 0);
      let list;
      if (by === "sector") {
        const m = new Map();
        for (const r of rows) {
          const k = r.sector || "Unclassified";
          const o = m.get(k) || { key: k, label: k, value: 0, pnl: 0, n: 0 };
          o.value += Math.max(0, r.market_value); o.pnl += r.unrealized_pnl; o.n++;
          m.set(k, o);
        }
        list = [...m.values()].sort((a, b) => b.value - a.value);
      } else {
        list = rows.map((r) => ({ key: r.symbol, label: r.symbol, value: Math.max(0, r.market_value), pnl: r.unrealized_pnl }));
      }
      const max = 8;
      if (list.length > max) {
        const rest = list.slice(max - 1);
        list = list.slice(0, max - 1).concat([{ key: "__other", label: `${rest.length} others`, other: true,
          value: rest.reduce((a, x) => a + x.value, 0), pnl: rest.reduce((a, x) => a + x.pnl, 0) }]);
      }
      list.forEach((x, i) => { x.color = x.other ? OTHER : PALETTE[i % PALETTE.length]; x.w = total ? x.value / total : 0; });
      return { list, total, by };
    }

    function ring(list, total) {
      const R = 52, SW = 13, C = TAU * R;
      const gap = list.length > 1 ? 2.2 : 0;
      let off = 0;
      const arcs = list.map((x) => {
        const len = Math.max(0, x.w * C - gap);
        const a = `<circle class="pf-arc${hot === x.key ? " hot" : ""}" data-k="${esc(x.key)}" r="${R}" cx="70" cy="70" ` +
          `stroke="${x.color}" stroke-width="${SW}" stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" ` +
          `stroke-dashoffset="${(-off).toFixed(2)}"><title>${esc(x.label)} · ${(x.w * 100).toFixed(1)}%</title></circle>`;
        off += x.w * C;
        return a;
      }).join("");
      const h = hot && list.find((x) => x.key === hot);
      const day = sum && sum.day_pnl;
      const centre = h
        ? `<span class="pf-k">${esc(h.label)}</span><b>${(h.w * 100).toFixed(1)}%</b><span class="pf-s">${esc(inr(h.value))}</span>`
        : `<span class="pf-k">Holdings value</span><b>${esc(inr(total))}</b>` +
          (day != null ? `<span class="pf-s ${dir(day)}">${day >= 0 ? "+" : ""}${esc(inr(day))} today</span>` : "");
      return `<div class="pf-ring"><svg viewBox="0 0 140 140" aria-hidden="true">` +
        `<circle r="${R}" cx="70" cy="70" class="pf-track" stroke-width="${SW}"/>` +
        `<g transform="rotate(-90 70 70)">${arcs}</g></svg>` +
        `<div class="pf-centre">${centre}</div></div>`;
    }

    function stats() {
      const s = sum;
      const cell = (k, v, cls = "", sub = "") => `<div class="pf-stat"><span>${k}</span><b class="${cls}">${v}</b>${sub ? `<small class="${cls}">${sub}</small>` : ""}</div>`;
      return `<div class="pf-stats">` +
        cell("Invested", esc(inr(s.invested))) +
        cell("Current", esc(inr(s.positions_mv))) +
        cell("Unrealised P&amp;L", `${s.unrealized_pnl >= 0 ? "+" : ""}${esc(inr(s.unrealized_pnl))}`, dir(s.unrealized_pnl), esc(pct(s.unrealized_pct))) +
        cell("Today", `${s.day_pnl >= 0 ? "+" : ""}${esc(inr(s.day_pnl))}`, dir(s.day_pnl)) +
        cell("Cash", esc(inr(s.cash_available))) +
        cell("Total return", `${s.total_pnl >= 0 ? "+" : ""}${esc(inr(s.total_pnl))}`, dir(s.total_pnl), esc(pct(s.total_pnl_pct))) +
      `</div>`;
    }

    function table(colorOf, total) {
      const logo = (sym) => (typeof Universe !== "undefined" && Universe.logoHTML ? Universe.logoHTML(sym, "pf-logo") : "");
      const name = (sym) => (typeof Universe !== "undefined" && Universe.label ? Universe.label(sym) : "");
      const tr = rows.map((r) => {
        const w = total ? (Math.max(0, r.market_value) / total) * 100 : 0;
        const c = colorOf(r);
        const nm = name(r.symbol);
        const isOpen = open === r.symbol;
        return `<tr class="pf-row${isOpen ? " open" : ""}${hot && (hot === r.symbol || hot === r.sector) ? " hot" : ""}" data-sym="${esc(r.symbol)}">` +
          `<td class="pf-co"><i class="pf-dot" style="background:${c}"></i>${logo(r.symbol)}<span class="pf-nm"><b>${esc(r.symbol)}</b>` +
            `${nm && nm !== r.symbol ? `<small>${esc(nm)}</small>` : ""}</span></td>` +
          `<td class="n">${esc(num(r.quantity, Number.isInteger(+r.quantity) ? 0 : 2))}<small>@ ${esc(num(r.avg_cost))}</small></td>` +
          `<td class="n">${esc(num(r.last_price))}${r.stale ? `<small title="Never priced: shown at cost">at cost</small>` : ""}</td>` +
          `<td class="n">${esc(inr(r.market_value))}<small>${w.toFixed(1)}%</small></td>` +
          `<td class="n ${dir(r.unrealized_pnl)}">${r.unrealized_pnl >= 0 ? "+" : ""}${esc(inr(r.unrealized_pnl))}<small>${esc(pct(r.unrealized_pct))}</small></td>` +
        `</tr>` +
        (isOpen ? `<tr class="pf-more"><td colspan="5"><div class="pf-det">` +
          `<span><small>Invested</small>${esc(inr(r.invested))}</span>` +
          `<span><small>Today</small><em class="${dir(r.day_pnl)}">${r.day_pnl >= 0 ? "+" : ""}${esc(inr(r.day_pnl))}</em></span>` +
          `<span><small>Booked P&amp;L</small><em class="${dir(r.realized_pnl)}">${r.realized_pnl >= 0 ? "+" : ""}${esc(inr(r.realized_pnl))}</em></span>` +
          `<span><small>Sector</small>${esc(r.sector || "Unclassified")}</span>` +
          `<span><small>Weight</small>${w.toFixed(2)}%</span>` +
          `<span><small>Priced</small>${r.last_mark_at ? esc(new Date(r.last_mark_at).toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })) : "at cost"}</span>` +
          `</div><div class="pf-acts">` +
            `<button type="button" class="btn" data-pf="chart" data-s="${esc(r.symbol)}">${ic("candles")}<span>Open chart</span></button>` +
            `<button type="button" class="btn" data-pf="ask" data-s="${esc(r.symbol)}">${ic("chat")}<span>Ask about it</span></button>` +
          `</div></td></tr>` : "");
      }).join("");
      return `<table class="pf-table"><thead><tr><th>Stock</th><th class="n">Qty</th><th class="n">LTP</th><th class="n">Value</th><th class="n">P&amp;L</th></tr></thead><tbody>${tr}</tbody></table>`;
    }

    function paint() {
      for (const b of host.querySelectorAll("[data-by]")) b.classList.toggle("on", b.dataset.by === (ctx.cfg.by || "stock"));
      if (!signedIn()) {
        body.innerHTML = empty("pie", "Sign in to see your paper portfolio.", "Sign in", 'data-pf="signin"');
        foot.innerHTML = "";
        return;
      }
      if (err && !rows) {
        body.innerHTML = empty("pie", `The portfolio could not be loaded: ${esc(err)}`, "Try again", 'data-pf="refresh"');
        return;
      }
      if (!rows) return;
      if (!rows.length) {
        body.innerHTML = (sum && sum.exists ? stats() : "") +
          empty("pie", "No holdings yet. Paper orders you place fill here.");
      } else {
        const { list, total, by } = slices();
        const colorOf = (r) => {
          const k = by === "sector" ? (r.sector || "Unclassified") : r.symbol;
          const x = list.find((s) => s.key === k);
          return x ? x.color : OTHER;
        };
        const legend = `<ul class="pf-legend">${list.map((x) =>
          `<li data-k="${esc(x.key)}" class="${hot === x.key ? "hot" : ""}"><i style="background:${x.color}"></i>` +
          `<span>${esc(x.label)}</span><b>${(x.w * 100).toFixed(1)}%</b></li>`).join("")}</ul>`;
        body.innerHTML = `<div class="pf-top">${ring(list, total)}${legend}</div>` + stats() + table(colorOf, total);
      }
      foot.innerHTML = `<span title="The paper book: simulated money, no broker">Paper · simulated</span>` +
        `<span>${rows.length} holding${rows.length === 1 ? "" : "s"}${at ? ` · ${new Date(at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}` : ""}${err ? " · refresh failed" : ""}</span>`;
    }

    // hover a slice, a legend row or a table row: the same thing lights up in all three
    host.addEventListener("pointerover", (e) => {
      const k = e.target.closest("[data-k]");
      const row = e.target.closest(".pf-row");
      let next = k ? k.dataset.k : null;
      if (!next && row && rows) {
        const r = rows.find((x) => x.symbol === row.dataset.sym);
        next = r ? (ctx.cfg.by === "sector" ? (r.sector || "Unclassified") : r.symbol) : null;
      }
      if (next !== hot) { hot = next; paintHot(); }
    });
    host.addEventListener("pointerleave", () => { if (hot) { hot = null; paintHot(); } });
    function paintHot() {
      if (!rows || !rows.length) return;
      const { list, total } = slices();
      const ringEl = body.querySelector(".pf-ring");
      if (ringEl) ringEl.outerHTML = ring(list, total);
      for (const li of body.querySelectorAll(".pf-legend li")) li.classList.toggle("hot", li.dataset.k === hot);
      for (const tr of body.querySelectorAll(".pf-row")) {
        const r = rows.find((x) => x.symbol === tr.dataset.sym);
        tr.classList.toggle("hot", !!hot && !!r && (r.symbol === hot || (r.sector || "Unclassified") === hot));
      }
    }

    host.addEventListener("click", (e) => {
      const by = e.target.closest("[data-by]");
      if (by) { ctx.setCfg({ by: by.dataset.by }); hot = null; return paint(); }
      const b = e.target.closest("[data-pf]");
      if (b) {
        e.stopPropagation();
        const a = b.dataset.pf;
        if (a === "refresh") return load(true);
        if (a === "signin") return window.CHARTO_AUTH_OPEN && window.CHARTO_AUTH_OPEN();
        if (a === "chart") return ctx.openSymbol ? ctx.openSymbol(b.dataset.s) : null;
        if (a === "ask") {
          const r = rows.find((x) => x.symbol === b.dataset.s);
          return ctx.ask({ sub: `${r.symbol} · ${num(r.quantity, 0)} @ ${num(r.avg_cost)} · ${pct(r.unrealized_pct)}`,
            context: `Paper portfolio (simulated) position: ${num(r.quantity, 0)} ${r.symbol} at an average of ${num(r.avg_cost)}; ` +
              `last traded ${num(r.last_price)} (${pct(r.unrealized_pct)} on cost).`,
            question: "What is the chart saying, and what would change the picture?" });
        }
        return;
      }
      const row = e.target.closest(".pf-row");
      if (row) { open = open === row.dataset.sym ? null : row.dataset.sym; paint(); }
    });

    if (typeof Auth !== "undefined" && Auth.onChange) Auth.onChange(() => load(true));

    return {
      actions: [{ icon: "rotateCw", label: "Refresh holdings", run: () => load(true) }],
      show() {
        load(!rows);
        clearInterval(timer);
        timer = setInterval(() => { if (document.visibilityState === "visible") load(false); }, Number(ctx.cfg.refresh) || POLL_MS);
      },
      hide() { clearInterval(timer); },
      config(cfg, patch) {
        if ("refresh" in patch) { clearInterval(timer); timer = setInterval(() => load(false), Number(ctx.cfg.refresh) || POLL_MS); }
        paint();
      },
      ask: () => rows && rows.length
        ? { sub: `${rows.length} holding${rows.length === 1 ? "" : "s"} · ${inr(sum.positions_mv)} · ${pct(sum.unrealized_pct)}`,
            context: `My paper portfolio (simulated): holdings value ${inr(sum.positions_mv)}, unrealised ${inr(sum.unrealized_pnl)} (${pct(sum.unrealized_pct)}), cash ${inr(sum.cash_available)}.\n` +
              rows.map((r) => `- ${r.symbol}: ${num(r.quantity, 0)} @ ${num(r.avg_cost)}, now ${num(r.last_price)}, ${pct(r.unrealized_pct)}, sector ${r.sector || "n/a"}`).join("\n"),
            question: "How concentrated is this, what is the biggest risk, and which positions need a closer look?" }
        : null,
    };
  }

  Dock.register({
    type: "portfolio", title: "Portfolio", icon: "pie", hue: "azure", group: "Market",
    desc: "Your paper holdings: allocation ring, P&L and each position", zone: "right", minW: 320, mount,
    settings: [
      { section: "Ring" },
      { key: "by", label: "Split the ring by", def: "stock", options: [{ v: "stock", label: "Holding" }, { v: "sector", label: "Sector" }] },
      { section: "Updates" },
      { key: "refresh", label: "Re-price holdings", def: 30000,
        options: [{ v: 15000, label: "15s" }, { v: 30000, label: "30s" }, { v: 60000, label: "1m" }] },
      { kind: "note", label: "Paper money: the book that orders and armed strategies fill into. No broker is touched." },
    ],
  });
})();
