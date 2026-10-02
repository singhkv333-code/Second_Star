/* Charto preview — the Market Depth widget.
 *
 * The order book for the instrument the chart is on (or one pinned to the
 * widget), read from the dataserver's /depth, which reads it off the venue:
 * Kite's five-level book for Indian instruments, Bybit's spot book for
 * *USDT pairs, Coinbase's level-2 book for *-USD pairs. Nothing here makes a
 * level up. When the venue has no book — an index, or a Kite session that is
 * not connected — the widget says why in one line, and for an index it
 * offers the traded instrument that tracks it.
 *
 * It polls once a second, and ONLY while it is on screen and the tab is in
 * front: the server coalesces concurrent asks per symbol, but the cheapest
 * request is the one a hidden widget never sends.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined") return;
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";
  const esc = (s) => String(s).replace(/[&<>"]/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const POLL_MS = 1000;

  // An index has no book; the ETF that tracks it does. The nearest real thing.
  const PROXY = { "NIFTY 50": "NIFTYBEES", "NIFTY BANK": "BANKBEES", "SENSEX": "SENSEXETF",
                  "NIFTY IT": "ITBEES", "NIFTY NEXT 50": "JUNIORBEES" };
  const SOURCE = { kite: "Kite", bybit: "Bybit spot", coinbase: "Coinbase" };

  /** Decimals from the book itself: the most any price on it carries, so a
   *  0.05-tick stock prints two places and a sub-cent coin prints its six. */
  function decimals(levels) {
    let d = 2;
    for (const [p] of levels) {
      const s = String(p);
      const i = s.indexOf(".");
      if (i >= 0) d = Math.max(d, Math.min(8, s.length - i - 1));
    }
    return d;
  }
  const qty = (v) => v >= 1e6 ? (v / 1e6).toFixed(2) + "M" : v >= 1e4 ? (v / 1e3).toFixed(1) + "K"
    : v >= 100 ? Math.round(v).toLocaleString("en-IN") : +v.toPrecision(4) + "";

  function mount(host, ctx) {
    let timer = 0, busy = false, last = null, prev = new Map(), sym = "";
    host.innerHTML =
      `<div class="side-head dp-head">` +
        `<button type="button" class="side-pick" data-d="sym" title="Choose the instrument"></button>` +
        `<div class="spacer"></div><span class="dp-live" aria-hidden="true"></span>` +
      `</div>` +
      `<div class="dp-imb" hidden><span class="b"></span><span class="a"></span>` +
        `<em class="lb"></em><em class="la"></em></div>` +
      `<div class="dp-cols"><span>Price</span><span class="r">Size</span><span class="r">Total</span></div>` +
      `<div class="side-body dp-body"></div>` +
      `<div class="dp-foot"></div>`;
    const $ = (s) => host.querySelector(s);

    function paintHead() {
      sym = ctx.symbol();
      const pinned = !!ctx.cfg.pin;
      $(".side-pick").innerHTML = `${esc(sym)}${Icons.svg("chevronDown")}`;
      $(".side-pick").title = pinned ? `Pinned to ${sym}` : `Following the chart (${sym})`;
      ctx.setTitle(sym);
    }

    async function poll() {
      if (busy || document.visibilityState !== "visible") return;
      busy = true;
      const want = ctx.symbol();
      try {
        const n = ctx.cfg.levels || 20;
        const r = await Net.get(`${API}/depth?symbol=${encodeURIComponent(want)}&levels=${n}`);
        const d = await r.json();
        if (want !== ctx.symbol()) return;
        last = r.ok ? d : { available: false, reason: d.error || `HTTP ${r.status}` };
        paint();
      } catch (e) {
        last = { available: false, reason: "The data server did not answer.", stale: !!(last && last.available) };
        paint();
      } finally {
        busy = false;
      }
    }

    function paint() {
      paintHead();
      const body = $(".dp-body"), foot = $(".dp-foot"), imb = $(".dp-imb");
      host.classList.toggle("dp-off", !last || !last.available);
      if (!last) {
        body.innerHTML = `<div class="dp-skel">${"<i></i>".repeat(12)}</div>`;
        foot.textContent = "";
        return;
      }
      if (!last.available) {
        imb.hidden = true;
        const proxy = PROXY[sym];
        body.innerHTML = `<div class="side-empty">${Icons.svg("depth")}<p>${esc(last.reason || "No order book.")}</p>` +
          (proxy ? `<button type="button" class="btn cta" data-d="proxy" data-sym="${proxy}">Show ${proxy} instead</button>` : "") +
          `</div>`;
        foot.textContent = "";
        return;
      }
      const n = ctx.cfg.levels || 20;
      const bids = last.bids.slice(0, n), asks = last.asks.slice(0, n);
      const dp = decimals([...bids, ...asks]);
      const px = (v) => v.toLocaleString("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp });
      const cum = (side) => { let t = 0; return side.map((l) => (t += l[1])); };
      const cb = cum(bids), ca = cum(asks);
      const tb = cb[cb.length - 1] || 0, ta = ca[ca.length - 1] || 0;
      const mode = ctx.cfg.bars || "total";
      const scale = mode === "total" ? Math.max(tb, ta, 1)
        : Math.max(...bids.map((l) => l[1]), ...asks.map((l) => l[1]), 1);
      const seen = new Map();
      const row = (l, total, side) => {
        const key = side + l[0];
        const was = prev.get(key);
        seen.set(key, l[1]);
        const flash = was == null ? "" : l[1] > was ? " grew" : l[1] < was ? " shrank" : "";
        const w = ((mode === "total" ? total : l[1]) / scale * 100).toFixed(1);
        return `<div class="dp-row ${side}${flash}" style="--w:${w}%" data-px="${l[0]}">` +
          `<span class="p">${px(l[0])}</span><span class="r">${qty(l[1])}</span>` +
          `<span class="r t">${qty(total)}</span></div>`;
      };
      const best = { bid: bids[0] && bids[0][0], ask: asks[0] && asks[0][0] };
      const spread = best.bid && best.ask ? best.ask - best.bid : null;
      const mid = spread != null ? (best.ask + best.bid) / 2 : null;
      const keepScroll = body.querySelector(".dp-mid") ? body.scrollTop : null;
      body.innerHTML =
        `<div class="dp-asks">${asks.map((l, i) => row(l, ca[i], "ask")).reverse().join("")}</div>` +
        `<div class="dp-mid"><b>${mid != null ? px(mid) : "—"}</b>` +
        `<span>Spread ${spread != null ? px(spread) : "—"}` +
        `${spread != null && mid ? ` · ${(spread / mid * 1e4).toFixed(1)} bps` : ""}</span></div>` +
        `<div class="dp-bids">${bids.map((l, i) => row(l, cb[i], "bid")).join("")}</div>`;
      prev = seen;
      // first paint centres the spread; after that the reader's scroll stays
      if (keepScroll == null) {
        const m = body.querySelector(".dp-mid");
        body.scrollTop = m.offsetTop - body.clientHeight / 2 + m.offsetHeight / 2;
      } else body.scrollTop = keepScroll;
      if (tb + ta > 0) {
        const pb = tb / (tb + ta) * 100;
        imb.hidden = false;
        imb.style.setProperty("--b", pb.toFixed(1) + "%");
        imb.querySelector(".lb").textContent = `Bids ${pb.toFixed(0)}%`;
        imb.querySelector(".la").textContent = `${(100 - pb).toFixed(0)}% Asks`;
      }
      const t = new Date((last.ts || Date.now() / 1000) * 1000);
      foot.innerHTML = `<span>${esc(SOURCE[last.source] || last.source)}${last.source === "kite" ? " · 5 levels is the most Kite publishes" : ""}</span>` +
        `<span>${t.toLocaleTimeString("en-IN", { hour12: false })}</span>`;
    }

    host.addEventListener("click", (e) => {
      const b = e.target.closest("[data-d]");
      if (b && b.dataset.d === "sym") {
        e.stopPropagation();
        return ctx.menu(b, [
          { head: "Instrument" },
          { id: "follow", label: `Follow the chart (${ctx.pageSymbol()})`, icon: "link", on: !ctx.cfg.pin },
          { id: "pin", label: "Pin another instrument…", icon: "pin", on: !!ctx.cfg.pin },
        ], (pick) => {
          if (pick === "follow") return ctx.setCfg({ pin: null });
          setTimeout(() => Universe.open({ anchor: b, onPick: (s) => ctx.setCfg({ pin: s }) }), 0);
        });
      }
      if (b && b.dataset.d === "proxy") return ctx.setCfg({ pin: b.dataset.sym });
      // a price on the ladder is a level: hand it to the chat as a question
      const r = e.target.closest(".dp-row");
      if (r && e.detail === 2) {
        ctx.compose(`${sym} has ${r.classList.contains("bid") ? "bids" : "offers"} stacked at ${r.dataset.px}. Does that level matter on the chart?`);
      }
    });

    function start() { stop(); poll(); timer = setInterval(poll, POLL_MS); }
    function stop() { clearInterval(timer); timer = 0; }
    document.addEventListener("visibilitychange", () => {
      if (timer && document.visibilityState === "visible") poll();
    });

    return {
      show: start, hide: stop,
      config(cfg, patch) {
        if ("pin" in patch) { last = null; prev = new Map(); paint(); }
        poll();
      },
      ask: () => last && last.available
        ? `Read the order book for ${sym}: best bid ${last.bids[0] && last.bids[0][0]}, best ask ${last.asks[0] && last.asks[0][0]}. What does the imbalance suggest, and how much does a book like this usually mean?`
        : "",
    };
  }

  Dock.register({
    type: "depth", title: "Market depth", icon: "depth", shortcut: "depth",
    key: "Alt D", desc: "The live order book", zone: "right", minW: 250, hue: "emerald", group: "Market", anim: "pulse", mount,
    settings: [
      { key: "levels", label: "Levels", def: 20, options: [{ v: 10, label: "10" }, { v: 20, label: "20" }, { v: 50, label: "50" }] },
      { key: "bars", label: "Bars show", def: "total", options: [{ v: "total", label: "Cumulative" }, { v: "size", label: "Size" }] },
    ],
  });
})();
