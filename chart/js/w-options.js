/* Pivot preview — the Option chain widget.
 *
 * The chain of an NSE F&O stock or index for one expiry, read from the data
 * server's /options/chain (data/options.py): NSE's end-of-day F&O file, with
 * the strikes on screen re-quoted live when the Kite session is up. The
 * header says which, always — a close is never shown as a live price.
 *
 * Three readings of one payload:
 *   Chain  calls · strike · puts, the money centred, ITM shaded, spot drawn
 *          between the strikes it falls between.
 *   OI     open interest as bars out from the strike, today's change drawn
 *          inside them: where the writers sit and where they moved.
 *   Smile  IV across strikes, calls and puts, the out-of-the-money side bold.
 *
 * The renderer is exported (window.OptionChainView) so the chat's card is the
 * same drawing as the widget, not a second look at the same numbers.
 */
"use strict";

const OptionChainView = (() => {
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const MINUS = "−";
  const fin = (v) => v != null && Number.isFinite(+v);
  const px = (v) => fin(v) ? (+v).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—";
  const strike = (v) => fin(v) ? (+v).toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—";
  /** Contracts the Indian way: 3.72L, 18.4K, 2.1Cr. */
  function qty(v, signed = false) {
    if (!fin(v)) return "—";
    const a = Math.abs(+v), sg = +v < 0 ? MINUS : signed && +v > 0 ? "+" : "";
    if (a >= 1e7) return `${sg}${(a / 1e7).toFixed(2)}Cr`;
    if (a >= 1e5) return `${sg}${(a / 1e5).toFixed(2)}L`;
    if (a >= 1e3) return `${sg}${(a / 1e3).toFixed(1)}K`;
    return `${sg}${Math.round(a)}`;
  }
  const pct = (v, d = 1) => fin(v) ? `${+v > 0 ? "+" : +v < 0 ? MINUS : ""}${Math.abs(+v).toFixed(d)}%` : "";
  const dir = (v) => (+v > 0 ? "up" : +v < 0 ? "dn" : "");
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const day = (iso) => { const [, m, d] = String(iso).split("-"); return `${+d} ${MON[+m - 1] || ""}`; };
  const dte = (n) => (n <= 0 ? "expires today" : n === 1 ? "1 day" : `${n} days`);

  // the columns each side can show, outermost first for the calls
  const COLS = {
    oichg: { h: "OI chg", tip: "Change in open interest since the last close, contracts" },
    oi: { h: "OI", tip: "Open interest, contracts" },
    vol: { h: "Volume", tip: "Contracts traded" },
    iv: { h: "IV", tip: "Implied volatility, %" },
    delta: { h: "Delta", tip: "Black-76 delta on the forward" },
    theta: { h: "Theta", tip: "Premium lost per calendar day, ₹ per unit" },
    ltp: { h: "LTP", tip: "Last price, and its change since the previous close" },
  };
  const SETS = {
    essential: ["oi", "ltp"],
    standard: ["oichg", "oi", "iv", "ltp"],
    detailed: ["oichg", "oi", "vol", "iv", "delta", "ltp"],
    greeks: ["oi", "iv", "delta", "theta", "ltp"],
  };

  function cell(col, q, side, top) {
    if (!q) return `<span class="oc-c ${col}"></span>`;
    switch (col) {
      case "oi": {
        const w = top ? Math.min(100, (q.oi || 0) / top * 100) : 0;
        return `<span class="oc-c oi"><i style="width:${w.toFixed(1)}%"></i><b>${qty(q.oi)}</b></span>`;
      }
      case "oichg": return `<span class="oc-c oichg ${dir(q.oi_chg)}">${q.oi_chg ? qty(q.oi_chg, true) : "0"}</span>`;
      case "vol": return `<span class="oc-c vol">${qty(q.volume)}</span>`;
      case "iv": return `<span class="oc-c iv">${fin(q.iv) ? (+q.iv).toFixed(1) : "—"}</span>`;
      case "delta": return `<span class="oc-c delta">${fin(q.delta) ? (+q.delta).toFixed(2) : "—"}</span>`;
      case "theta": return `<span class="oc-c theta">${fin(q.theta) ? (+q.theta).toFixed(1) : "—"}</span>`;
      case "ltp": return `<span class="oc-c ltp"><b>${px(q.ltp)}</b>${fin(q.chg_pct) ? `<em class="${dir(q.chg_pct)}">${pct(q.chg_pct)}</em>` : ""}</span>`;
    }
    return "";
  }

  /** calls · strike · puts. `cols` is outermost-first for the call side. */
  function table(c, { cols = SETS.standard, sel = null, rows = c.rows || [] } = {}) {
    const top = rows.reduce((m, r) => Math.max(m, r.ce?.oi || 0, r.pe?.oi || 0), 0) || 1;
    const callWall = (c.walls?.call || [])[0]?.strike, putWall = (c.walls?.put || [])[0]?.strike;
    const spot = +c.spot;
    // OI is the column read by its bar, so it gets the room for one
    const fr = (k) => (k === "oi" ? "minmax(0,1.6fr)" : k === "ltp" ? "minmax(0,1.1fr)" : "minmax(0,1fr)");
    const grid = `grid-template-columns:${[...cols.map(fr), "minmax(64px,.9fr)", ...[...cols].reverse().map(fr)].join(" ")}`;
    const head = `<div class="oc-th" style="${grid}">` +
      cols.map((k) => `<span class="oc-c ${k}" title="${esc(COLS[k].tip)}">${COLS[k].h}</span>`).join("") +
      `<span class="oc-k">Strike</span>` +
      [...cols].reverse().map((k) => `<span class="oc-c ${k}" title="${esc(COLS[k].tip)}">${COLS[k].h}</span>`).join("") +
      `</div>`;
    let spotDrawn = false;
    const body = rows.map((r, i) => {
      let pre = "";
      if (!spotDrawn && fin(spot) && r.strike > spot) {
        spotDrawn = true;
        if (i > 0) pre = `<div class="oc-spot"><span>${c.index ? "Spot" : "Price"} ${px(spot)}</span></div>`;
      }
      const tags = (r.strike === c.max_pain ? `<i class="oc-tag" title="Max pain">MP</i>` : "");
      const cls = ["oc-row", r.strike === c.atm_strike ? "atm" : "", sel === r.strike ? "sel" : "",
        r.strike === callWall ? "cwall" : "", r.strike === putWall ? "pwall" : ""].filter(Boolean).join(" ");
      const itmC = fin(spot) && r.strike < spot, itmP = fin(spot) && r.strike > spot;
      return pre + `<div class="${cls}" data-k="${r.strike}" style="${grid}">` +
        `<span class="oc-side ce${itmC ? " itm" : ""}">${cols.map((k) => cell(k, r.ce, "ce", top)).join("")}</span>` +
        `<span class="oc-k">${strike(r.strike)}${tags}</span>` +
        `<span class="oc-side pe${itmP ? " itm" : ""}">${[...cols].reverse().map((k) => cell(k, r.pe, "pe", top)).join("")}</span>` +
        `</div>`;
    }).join("");
    return `<div class="oc-table"><div class="oc-sides"><span>Calls</span><span>Puts</span></div>${head}<div class="oc-rows">${body}</div></div>`;
  }

  /** Open interest as bars out from the strike; today's change inside them. */
  function oiProfile(c, { sel = null, rows = c.rows || [] } = {}) {
    const top = rows.reduce((m, r) => Math.max(m, (r.ce?.oi || 0) + Math.max(0, -(r.ce?.oi_chg || 0)),
      (r.pe?.oi || 0) + Math.max(0, -(r.pe?.oi_chg || 0))), 0) || 1;
    const bar = (q, side) => {
      if (!q) return `<span class="oc-b ${side}"></span>`;
      const w = (q.oi || 0) / top * 100, ch = q.oi_chg || 0;
      const add = ch > 0 ? Math.min(w, ch / top * 100) : 0, cut = ch < 0 ? -ch / top * 100 : 0;
      return `<span class="oc-b ${side}" title="OI ${qty(q.oi)} · ${ch >= 0 ? "added" : "cut"} ${qty(Math.abs(ch))} today">` +
        `<i class="tot" style="width:${w.toFixed(2)}%"></i>` +
        (add ? `<i class="add" style="width:${add.toFixed(2)}%;${side === "ce" ? "right" : "left"}:${(w - add).toFixed(2)}%"></i>` : "") +
        (cut ? `<i class="cut" style="width:${cut.toFixed(2)}%;${side === "ce" ? "right" : "left"}:${w.toFixed(2)}%"></i>` : "") +
        `<em>${qty(q.oi)}</em></span>`;
    };
    let spotDrawn = false;
    const body = rows.map((r, i) => {
      let pre = "";
      if (!spotDrawn && fin(c.spot) && r.strike > c.spot) {
        spotDrawn = true;
        if (i > 0) pre = `<div class="oc-spot"><span>${c.index ? "Spot" : "Price"} ${px(c.spot)}</span></div>`;
      }
      return pre + `<div class="oc-prow${r.strike === c.atm_strike ? " atm" : ""}${sel === r.strike ? " sel" : ""}" data-k="${r.strike}">` +
        bar(r.ce, "ce") + `<span class="oc-k">${strike(r.strike)}${r.strike === c.max_pain ? `<i class="oc-tag">MP</i>` : ""}</span>` + bar(r.pe, "pe") + `</div>`;
    }).join("");
    return `<div class="oc-prof"><div class="oc-sides"><span>Call OI</span><span>Put OI</span></div>` +
      `<div class="oc-legend"><span><i class="tot"></i>Open interest</span><span><i class="add"></i>Added today</span><span><i class="cut"></i>Cut today</span></div>` +
      `<div class="oc-rows">${body}</div></div>`;
  }

  /** IV across strikes. The out-of-the-money side of each strike is the
   *  market's quote for that strike's risk, so it is the bold line. */
  function smile(c, { rows = c.rows || [], width = 560 } = {}) {
    const pts = rows.map((r) => ({ k: r.strike, ce: r.ce?.iv, pe: r.pe?.iv }));
    const ivs = pts.flatMap((p) => [p.ce, p.pe]).filter(fin).map(Number);
    if (ivs.length < 4) return `<div class="oc-none">Too few traded strikes in this expiry to draw the volatility smile.</div>`;
    // drawn at the pixel width it is shown at, so the type is never stretched
    const W = Math.max(300, Math.round(width)), H = 220, L = 38, R = 12, T = 14, B = 26;
    const k0 = pts[0].k, k1 = pts[pts.length - 1].k;
    const v0 = Math.floor(Math.min(...ivs) - 1), v1 = Math.ceil(Math.max(...ivs) + 1);
    const x = (k) => L + (k - k0) / (k1 - k0 || 1) * (W - L - R);
    const y = (v) => T + (1 - (v - v0) / (v1 - v0 || 1)) * (H - T - B);
    const line = (key, keep) => {
      const seg = pts.filter((p) => fin(p[key]) && keep(p));
      return seg.length > 1 ? seg.map((p, i) => `${i ? "L" : "M"}${x(p.k).toFixed(1)} ${y(+p[key]).toFixed(1)}`).join(" ") : "";
    };
    const F = +c.forward || +c.spot;
    const otm = pts.map((p) => ({ k: p.k, v: p.k < F ? p.pe : p.ce })).filter((p) => fin(p.v));
    const otmPath = otm.map((p, i) => `${i ? "L" : "M"}${x(p.k).toFixed(1)} ${y(+p.v).toFixed(1)}`).join(" ");
    const ticks = [];
    for (let i = 0; i <= 4; i++) {
      const v = v0 + (v1 - v0) * i / 4;
      ticks.push(`<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="g"/><text x="${L - 6}" y="${y(v) + 3}" text-anchor="end">${v.toFixed(0)}%</text>`);
    }
    const xt = [0, .25, .5, .75, 1].map((f) => k0 + (k1 - k0) * f);
    const xs = xt.map((k) => `<text x="${x(k)}" y="${H - 8}" text-anchor="middle">${strike(Math.round(k))}</text>`).join("");
    const sx = fin(c.spot) ? x(+c.spot) : null;
    return `<div class="oc-smile"><svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" role="img" aria-label="Implied volatility by strike">` +
      ticks.join("") + xs +
      (sx != null && sx >= L && sx <= W - R ? `<line x1="${sx}" x2="${sx}" y1="${T}" y2="${H - B}" class="s"/><text x="${sx + 4}" y="${T + 8}" class="sl">${c.index ? "Spot" : "Price"}</text>` : "") +
      `<path d="${line("ce", () => true)}" class="ce"/><path d="${line("pe", () => true)}" class="pe"/>` +
      `<path d="${otmPath}" class="otm"/>` +
      otm.map((p) => `<circle cx="${x(p.k).toFixed(1)}" cy="${y(+p.v).toFixed(1)}" r="2.2" class="pt"><title>${strike(p.k)} · IV ${(+p.v).toFixed(1)}%</title></circle>`).join("") +
      `</svg><div class="oc-legend"><span><i class="otm"></i>Out of the money</span><span><i class="ce"></i>Calls</span><span><i class="pe"></i>Puts</span></div></div>`;
  }

  /** The reads, in tiles: what a trader scans before the strikes. */
  function summary(c) {
    const t = c.totals || {};
    const em = c.expected_move;
    const mpGap = fin(c.max_pain) && fin(c.spot) ? (c.max_pain / c.spot - 1) * 100 : null;
    const tile = (label, value, sub = "", cls = "") =>
      `<div class="oc-tile ${cls}"><span>${label}</span><b>${value}</b>${sub ? `<em>${sub}</em>` : ""}</div>`;
    const callShare = (t.call_oi || 0) + (t.put_oi || 0) ? (t.call_oi || 0) / ((t.call_oi || 0) + (t.put_oi || 0)) * 100 : 50;
    return `<div class="oc-tiles">` +
      tile("PCR · OI", fin(c.pcr_oi) ? (+c.pcr_oi).toFixed(2) : "—", fin(c.pcr_volume) ? `volume ${(+c.pcr_volume).toFixed(2)}` : "") +
      tile("Max pain", strike(c.max_pain), fin(mpGap) ? `${pct(mpGap, 1)} from ${c.index ? "spot" : "price"}` : "") +
      tile("ATM IV", fin(c.atm_iv) ? `${(+c.atm_iv).toFixed(1)}%` : "—", fin(c.one_sd_move) ? `1σ ±${strike(Math.round(c.one_sd_move))}` : "") +
      tile("Straddle", em ? `±${strike(Math.round(em.abs))}` : "—", em ? `±${(+em.pct).toFixed(2)}% by ${day(c.expiry)}` : "") +
      `</div>` +
      `<div class="oc-split" title="Total call and put open interest in this expiry">` +
        `<div class="oc-split-bar"><i class="ce" style="width:${callShare.toFixed(1)}%"></i><i class="pe"></i></div>` +
        `<div class="oc-split-lab"><span>Call OI <b>${qty(t.call_oi)}</b> <em class="${dir(t.call_oi_chg)}">${qty(t.call_oi_chg, true)}</em></span>` +
        `<span>Put OI <b>${qty(t.put_oi)}</b> <em class="${dir(t.put_oi_chg)}">${qty(t.put_oi_chg, true)}</em></span></div>` +
      `</div>`;
  }

  /** Where the OI sits, in words a trader would use about the chain. */
  function walls(c) {
    const w = c.walls || {}, add = c.oi_added || {};
    const list = (a) => (a || []).map((x) => strike(x.strike)).join(" · ") || "—";
    return `<div class="oc-walls">` +
      `<span><i class="ce"></i>Most call OI <b>${list(w.call)}</b></span>` +
      `<span><i class="pe"></i>Most put OI <b>${list(w.put)}</b></span>` +
      ((add.call || []).length || (add.put || []).length
        ? `<span class="muted">Most added today: calls ${list(add.call)}, puts ${list(add.put)}</span>` : "") +
      `</div>`;
  }

  /** The source line. A close is labelled a close. */
  function source(c) {
    const live = c.source === "kite";
    return `<span class="oc-src${live ? " live" : ""}" title="${live ? "Live Kite quotes for the strikes shown; OI change is against the last close"
      : "NSE's end-of-day F&O file"}"><i></i>${esc(c.as_of_label || "")}</span>`;
  }

  function foot(c) {
    const fwd = { future: "the same-expiry future", parity: "put-call parity", spot: "spot" }[c.forward_basis] || "spot";
    return `Contracts · lot ${esc(c.lot_size)} · IV and Greeks priced off ${fwd} (${px(c.forward)}) · ` +
      (c.source === "kite" ? "Kite live quotes" : "NSE F&O bhavcopy");
  }

  /** The chain as text, for the chat (a widget's ask(), the card's ask). */
  function brief(c, n = 7) {
    if (!c || !c.available) return "";
    const i = Math.max(0, (c.rows || []).findIndex((r) => r.strike === c.atm_strike));
    const near = (c.rows || []).slice(Math.max(0, i - n), i + n + 1);
    const side = (q) => q ? `LTP ${px(q.ltp)} OI ${qty(q.oi)} (${qty(q.oi_chg, true)}) IV ${fin(q.iv) ? (+q.iv).toFixed(1) : "—"}` : "—";
    const t = c.totals || {};
    return `${c.symbol} options, expiry ${c.expiry} (${dte(c.dte)}), ${c.as_of_label}. ` +
      `${c.index ? "Spot" : "Price"} ${px(c.spot)}, forward ${px(c.forward)} (${c.forward_basis}), lot ${c.lot_size}. ` +
      `PCR (OI) ${c.pcr_oi ?? "—"}, max pain ${strike(c.max_pain)}, ATM ${strike(c.atm_strike)} IV ${c.atm_iv ?? "—"}%, ` +
      `straddle ±${c.expected_move ? px(c.expected_move.abs) : "—"}. Call OI ${qty(t.call_oi)} (${qty(t.call_oi_chg, true)}), ` +
      `put OI ${qty(t.put_oi)} (${qty(t.put_oi_chg, true)}), contracts.\n` +
      `Most call OI: ${(c.walls?.call || []).map((x) => strike(x.strike)).join(", ")}; most put OI: ${(c.walls?.put || []).map((x) => strike(x.strike)).join(", ")}.\n` +
      near.map((r) => `${strike(r.strike)}: CE ${side(r.ce)} | PE ${side(r.pe)}`).join("\n");
  }

  return { table, oiProfile, smile, summary, walls, source, foot, brief, qty, px, strike, day, dte, SETS };
})();
window.OptionChainView = OptionChainView;

(() => {
  if (typeof Dock === "undefined") return;
  const V = OptionChainView;
  const { esc, ic, json, empty, skel } = WKit;
  const LIVE_MS = 15_000, EOD_MS = 10 * 60_000;
  const VIEWS = [["chain", "Chain"], ["oi", "OI"], ["smile", "Smile"]];
  const SUGGEST = ["NIFTY 50", "NIFTY BANK", "RELIANCE", "HDFCBANK"];

  function mount(host, ctx) {
    let data = null, seq = 0, timer = 0, sel = null, centred = "", width = 0;
    host.classList.add("oc");
    host.innerHTML =
      `<div class="oc-head"><div class="oc-name"><b></b><span class="oc-px"></span></div>` +
        `<span class="sh-gap"></span><span class="oc-srcslot"></span>` +
        `<button type="button" class="sh-btn i" data-oc="refresh" title="Refresh">${ic("rotateCw")}</button></div>` +
      `<div class="oc-bar"><div class="oc-exps" role="tablist" aria-label="Expiry"></div>` +
        `<div class="dk-seg oc-views">${VIEWS.map(([v, l]) => `<button type="button" data-view="${v}">${l}</button>`).join("")}</div></div>` +
      `<div class="side-body oc-body"></div>` +
      `<div class="oc-pick" hidden></div>` +
      `<div class="oc-foot"></div>`;
    const $ = (s) => host.querySelector(s);
    const body = $(".oc-body");

    const ro = new ResizeObserver(() => {
      const w = host.clientWidth;
      host.classList.toggle("oc-narrow", w < 470);
      if (Math.abs(w - width) > 30) { width = w; if (data && data.available) paintBody(); }
    });
    ro.observe(host);

    function cols() {
      const want = ctx.cfg.columns || "auto";
      const w = host.clientWidth || 600;
      const fit = w < 430 ? "essential" : w < 700 ? "standard" : "detailed";
      if (want === "auto") return V.SETS[fit];
      const order = ["essential", "standard", "detailed"];
      if (want === "greeks") return w < 520 ? V.SETS.essential : V.SETS.greeks;
      return V.SETS[order[Math.min(order.indexOf(want), order.indexOf(fit))]] || V.SETS.standard;
    }

    async function load(force) {
      const my = ++seq;
      const sym = ctx.symbol();
      if (!data || force === "hard") body.innerHTML = skel(12, "oc-skel");
      const n = ctx.cfg.strikes === "all" ? 0 : Number(ctx.cfg.strikes) || 15;
      const ex = ctx.cfg.expiry || "";
      try {
        const d = await json(`/options/chain?symbol=${encodeURIComponent(sym)}&strikes=${n}${ex ? `&expiry=${encodeURIComponent(ex)}` : ""}`);
        if (my !== seq) return;
        const moved = !data || data.symbol !== d.symbol || data.expiry !== d.expiry;
        data = d;
        if (moved) sel = null;
        paint(moved);
      } catch (e) {
        if (my !== seq) return;
        body.innerHTML = empty("layers", `The option chain could not be loaded: ${esc(e.message)}`, "Try again", 'data-oc="refresh"');
      }
      schedule();
    }

    function schedule() {
      clearTimeout(timer);
      if (!ctx.visible()) return;
      const ms = data && data.source === "kite" ? Number(ctx.cfg.refresh) || LIVE_MS : EOD_MS;
      timer = setTimeout(() => { if (document.visibilityState === "visible") load(false); else schedule(); }, ms);
    }

    function paint(moved) {
      for (const b of host.querySelectorAll("[data-view]")) b.classList.toggle("on", b.dataset.view === (ctx.cfg.view || "chain"));
      const d = data;
      if (!d || !d.available) {
        $(".oc-name b").textContent = ctx.symbol();
        $(".oc-px").textContent = "";
        $(".oc-srcslot").innerHTML = "";
        $(".oc-exps").innerHTML = "";
        $(".oc-foot").textContent = "";
        ctx.setTitle(ctx.symbol());
        const sug = SUGGEST.filter((s) => s !== ctx.symbol()).slice(0, 3);
        body.innerHTML = `<div class="side-empty oc-empty">${Icons.svg("layers")}<p>${esc((d && d.reason) || "No chain.")}</p>` +
          `<div class="oc-sug">${sug.map((s) => `<button type="button" class="dk-cta" data-sym="${esc(s)}">${esc(s)}</button>`).join("")}</div></div>`;
        return;
      }
      $(".oc-name b").textContent = d.symbol;
      $(".oc-px").innerHTML = `${d.index ? "Spot" : "Price"} <b>${V.px(d.spot)}</b>` +
        (d.forward_basis === "future" ? ` <span>Fut ${V.px(d.forward)}</span>` : "");
      $(".oc-srcslot").innerHTML = V.source(d);
      $(".oc-exps").innerHTML = (d.expiries || []).map((e) =>
        `<button type="button" role="tab" data-exp="${esc(e.date)}" class="${e.date === d.expiry ? "on" : ""}" aria-selected="${e.date === d.expiry}">` +
        `<b>${esc(V.day(e.date))}</b><span>${e.dte <= 0 ? "today" : `${e.dte}d`}${e.monthly ? " · M" : ""}</span></button>`).join("");
      ctx.setTitle(`${d.symbol} · ${V.day(d.expiry)}`);
      $(".oc-foot").textContent = V.foot(d);
      paintBody();
      if (moved || centred !== `${d.symbol}|${d.expiry}|${ctx.cfg.view || "chain"}`) centre();
      const on = $(".oc-exps .on");
      if (on && moved) on.scrollIntoView({ block: "nearest", inline: "center" });
    }

    function paintBody() {
      const d = data, view = ctx.cfg.view || "chain";
      const keep = body.scrollTop;
      const main = view === "oi" ? V.oiProfile(d, { sel }) : view === "smile" ? V.smile(d, { width: body.clientWidth - 24 }) : V.table(d, { cols: cols(), sel });
      body.innerHTML = V.summary(d) + (view === "smile" ? "" : V.walls(d)) + main;
      body.scrollTop = keep;
      paintPick();
    }

    /** Bring the money to the middle of the rows on first paint. */
    function centre() {
      const d = data;
      centred = `${d.symbol}|${d.expiry}|${ctx.cfg.view || "chain"}`;
      requestAnimationFrame(() => {
        const row = body.querySelector(`[data-k="${d.atm_strike}"]`);
        if (!row) return;
        const target = row.offsetTop - body.clientHeight / 2 + row.offsetHeight / 2;
        body.scrollTop = Math.max(0, target);
      });
    }

    function paintPick() {
      const pick = $(".oc-pick");
      const r = sel != null && data && (data.rows || []).find((x) => x.strike === sel);
      if (!r) { pick.hidden = true; return; }
      const s = (q, l) => q ? `<span><em>${l}</em> ${V.px(q.ltp)} · OI ${V.qty(q.oi)} <i class="${q.oi_chg > 0 ? "up" : q.oi_chg < 0 ? "dn" : ""}">${V.qty(q.oi_chg, true)}</i>${q.iv ? ` · IV ${(+q.iv).toFixed(1)}%` : ""}${q.delta != null ? ` · Δ ${(+q.delta).toFixed(2)}` : ""}</span>` : "";
      pick.hidden = false;
      pick.innerHTML = `<b>${V.strike(r.strike)}</b>${s(r.ce, "CE")}${s(r.pe, "PE")}` +
        `<span class="sh-gap"></span><button type="button" class="dk-cta sm" data-oc="ask-strike">${ic("chat")} Ask</button>` +
        `<button type="button" class="sh-btn i" data-oc="unpick" title="Clear">${ic("x")}</button>`;
    }

    function askStrike() {
      const r = data && (data.rows || []).find((x) => x.strike === sel);
      if (!r) return;
      const side = (q, l) => q ? `${l}: LTP ${V.px(q.ltp)} (${q.chg_pct ?? 0}%), OI ${V.qty(q.oi)} contracts (${V.qty(q.oi_chg, true)} today), volume ${V.qty(q.volume)}, IV ${q.iv ?? "—"}%, delta ${q.delta ?? "—"}, theta ${q.theta ?? "—"}` : `${l}: not listed`;
      ctx.ask({ sub: `${data.symbol} ${V.strike(r.strike)} · ${V.day(data.expiry)}`,
        context: `${V.brief(data, 3)}\n\nThe strike in question, ${V.strike(r.strike)} (${data.expiry}):\n${side(r.ce, "Call")}\n${side(r.pe, "Put")}`,
        question: `What does the open interest and IV at ${V.strike(r.strike)} say?` });
    }

    host.addEventListener("click", (e) => {
      const ex = e.target.closest("[data-exp]");
      if (ex) { ctx.setCfg({ expiry: ex.dataset.exp }); return; }
      const v = e.target.closest("[data-view]");
      if (v) { ctx.setCfg({ view: v.dataset.view }); return; }
      const sg = e.target.closest("[data-sym]");
      if (sg) { ctx.setCfg({ pin: sg.dataset.sym, expiry: "" }); return; }
      const row = e.target.closest("[data-k]");
      if (row && data) {
        const k = +row.dataset.k;
        sel = sel === k ? null : k;
        for (const el of body.querySelectorAll("[data-k]")) el.classList.toggle("sel", +el.dataset.k === sel);
        paintPick();
        return;
      }
      const b = e.target.closest("[data-oc]");
      if (!b) return;
      e.stopPropagation();
      if (b.dataset.oc === "refresh") return load("hard");
      if (b.dataset.oc === "ask-strike") return askStrike();
      if (b.dataset.oc === "unpick") { sel = null; for (const el of body.querySelectorAll(".sel")) el.classList.remove("sel"); paintPick(); }
    });

    // the dock's own header already offers "Ask about this widget" (ask()
    // below) and the chain's head has Refresh: no actions of its own
    const api = {
      show() { load(!data); },
      hide() { clearTimeout(timer); },
      config(cfg, patch) {
        if ("expiry" in patch || "strikes" in patch || "symbol" in patch || "link" in patch || "pin" in patch) {
          if ("symbol" in patch || "pin" in patch || "link" in patch) data = null;
          return load("hard");
        }
        if ("refresh" in patch) schedule();
        if (data && data.available) {
          if ("view" in patch) { paint(false); return; }
          paintBody();
        }
      },
      /** From the chat's card: open on this instrument and expiry. */
      receive(p) {
        if (!p) return;
        const patch = {};
        if (p.symbol && p.symbol !== ctx.symbol()) patch.pin = p.symbol;
        if (p.expiry) patch.expiry = p.expiry;
        if (p.view) patch.view = p.view;
        if (Object.keys(patch).length) ctx.setCfg(patch); else load("hard");
      },
      ask: () => data && data.available
        ? { sub: `${data.symbol} options · ${V.day(data.expiry)} · PCR ${data.pcr_oi ?? "—"} · max pain ${V.strike(data.max_pain)}`,
            context: V.brief(data, 8),
            question: "Read this option chain: where is OI concentrated, what changed today, and what range is it pricing?" }
        : null,
    };
    return api;
  }

  Dock.register({
    type: "options", title: "Option chain", icon: "options", hue: "violet", group: "Market",
    desc: "Calls and puts by strike: OI, OI change, IV, Greeks, PCR and max pain",
    zone: "right", minW: 340, mount, linkable: true,
    settings: [
      { section: "Chain" },
      { key: "expiry", label: "Expiry", kind: "text", def: "", placeholder: "Nearest",
        hint: "Blank follows the nearest expiry; a date such as 2026-10-27 holds one" },
      { key: "strikes", label: "Strikes each side of the money", def: 15,
        options: [{ v: 8, label: "8" }, { v: 15, label: "15" }, { v: 25, label: "25" }, { v: "all", label: "All" }] },
      { key: "view", label: "View", def: "chain",
        options: [{ v: "chain", label: "Chain" }, { v: "oi", label: "OI" }, { v: "smile", label: "Smile" }] },
      { section: "Display" },
      { key: "columns", label: "Columns", def: "auto", hint: "Auto fits the columns to the widget's width",
        options: [{ v: "auto", label: "Auto" }, { v: "essential", label: "Essential" }, { v: "standard", label: "Standard" },
                  { v: "detailed", label: "Detailed" }, { v: "greeks", label: "Greeks" }] },
      { key: "refresh", label: "Live refresh", def: 15000, hint: "Only while Kite quotes are live",
        options: [{ v: 5000, label: "5s" }, { v: 15000, label: "15s" }, { v: 60000, label: "1 min" }] },
    ],
  });
})();
