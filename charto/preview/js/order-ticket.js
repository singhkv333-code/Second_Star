/* Charto preview — the Buy / Sell ticket behind the two buttons in the chart's
 * legend. Orders go into the paper book (charto/data/paper.py): simulated,
 * nothing reaches a broker.
 *
 * Laid out the way traders already know it. TradingView's order panel: a
 * side, then Market / Limit / Stop / Stop limit, a size, and a stop loss and
 * take profit that ride on a market or limit buy and cancel each other.
 * Kite's price fields: a limit price, a trigger price. An exchange's spot
 * form: size by quantity or by amount, or a share of the cash or holding.
 *
 * The server prices every fill; the summary here is an estimate from the same
 * numbers (/paper/ticket) so the reader sees what the order costs before
 * pressing the button. Crypto is entered in its own currency and settles in
 * rupees at the USDINR rate the ticket shows. */
"use strict";

const OrderTicket = (() => {
  const API = location.port === "5173" ? "http://127.0.0.1:5174" : "";
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const toast = (m) => (window.Layouts && Layouts.toast ? Layouts.toast(m) : null);
  const TYPES = [["MARKET", "Market"], ["LIMIT", "Limit"], ["SL-M", "Stop"], ["SL", "Stop limit"]];
  const inr = (n) => "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 });
  let open_ = null;

  async function get(path) {
    const r = await fetch(API + path, { headers: Auth.headers({}) });
    let d = {};
    try { d = await r.json(); } catch {}
    return { status: r.status, ok: r.ok, d };
  }

  function close() {
    if (!open_) return;
    open_.wrap.remove();
    document.removeEventListener("keydown", open_.onKey);
    open_ = null;
  }

  async function open(symbol, side) {
    close();
    const wrap = document.createElement("div");
    wrap.className = "dlg-wrap ot-wrap open";
    wrap.innerHTML = `<div class="ot" role="dialog" aria-modal="true" aria-label="Order ticket">
      <div class="ot-loading">Loading ${esc(symbol)}…</div></div>`;
    document.body.appendChild(wrap);
    const onKey = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    wrap.addEventListener("mousedown", (e) => { if (e.target === wrap) close(); });
    open_ = { wrap, onKey };

    if (!Auth.token) return message(wrap, "Sign in to trade on paper",
      "Every account gets a paper book with ₹1,50,000 of practice money. Orders fill at live prices; nothing reaches a broker.");
    const r = await get(`/paper/ticket?symbol=${encodeURIComponent(symbol)}`);
    if (!open_ || open_.wrap !== wrap) return;
    if (r.status === 401) return message(wrap, "Sign in to trade on paper", "Your session has ended. Sign in again to place orders.");
    if (!r.ok) return message(wrap, "The ticket could not load", r.d.error || "Try again in a moment.");
    if (!r.d.tradable) return message(wrap, `${symbol} cannot be traded here`, r.d.why);
    if (r.d.ccy !== "INR" && !r.d.fx) return message(wrap, "No exchange rate right now",
      `${symbol} is priced in ${r.d.ccy}, and there is no USDINR rate to convert it to your rupee paper book. Try again shortly.`);
    render(wrap, r.d, side === "SELL" ? "SELL" : "BUY");
  }

  function message(wrap, title, body) {
    wrap.querySelector(".ot").innerHTML = `
      <div class="ot-head"><b>${esc(title)}</b><button class="btn icon ot-x" aria-label="Close">${Icons.svg("x", "sm")}</button></div>
      <p class="ot-msg">${esc(body)}</p>`;
    wrap.querySelector(".ot-x").onclick = close;
  }

  function render(wrap, t, side0) {
    const S = Sym.of(t.symbol);
    const native = (n) => (t.ccy === "INR" ? inr(n) : `${Number(n).toLocaleString("en-US", { maximumFractionDigits: 6 })} ${t.ccy}`);
    const fx = t.ccy === "INR" ? 1 : (t.fx || 0);
    const dp = t.fractional ? 8 : 0;
    const roundQty = (q) => {
      const k = 10 ** dp;
      return Math.max(0, Math.floor(q * k + 1e-9) / k);
    };
    const st = { side: side0, type: "MARKET", by: "qty", sl: false, tp: false, busy: false };
    const box = wrap.querySelector(".ot");
    const ago = t.price_at ? new Date(t.price_at) : null;
    const when = ago ? ago.toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
    box.innerHTML = `
      <div class="ot-head">
        <div class="ot-sym">${Universe.logoHTML ? Universe.logoHTML(t.symbol, "co-logo") : ""}<b>${esc(Universe.shown ? Universe.shown(t.symbol) : t.symbol)}</b><span>${esc(t.venue)}</span></div>
        <button class="btn icon ot-x" aria-label="Close">${Icons.svg("x", "sm")}</button>
      </div>
      <div class="ot-px"><b>${t.price == null ? "No price" : native(t.price)}</b>
        <span>${when ? `last ${esc(when)}` : ""}${t.ccy !== "INR" && t.fx ? ` · ₹${t.fx.toFixed(2)} per ${esc(t.ccy)}` : ""}</span></div>
      <div class="ot-side" role="tablist">
        <button data-side="BUY">Buy</button><button data-side="SELL">Sell</button>
      </div>
      <div class="ot-types" role="tablist">${TYPES.map(([k, l]) => `<button data-type="${k}">${l}</button>`).join("")}</div>
      <div class="ot-fields">
        <label class="ot-f" data-for="limit"><span>Limit price <i>${esc(t.ccy)}</i></span><input class="textfield" id="otLimit" inputmode="decimal"></label>
        <label class="ot-f" data-for="trigger"><span>Trigger price <i>${esc(t.ccy)}</i></span><input class="textfield" id="otTrig" inputmode="decimal"></label>
        <div class="ot-f">
          <span class="ot-by"><button data-by="qty">Quantity</button><button data-by="amt">Amount ₹</button></span>
          <input class="textfield" id="otSize" inputmode="decimal" placeholder="0">
        </div>
        <div class="ot-pcts">${[25, 50, 75, 100].map((p) => `<button data-pct="${p}">${p === 100 ? "Max" : p + "%"}</button>`).join("")}</div>
      </div>
      <div class="ot-exits">
        <label class="ot-ex"><input type="checkbox" id="otSlOn"><span>Stop loss</span>
          <input class="textfield" id="otSl" inputmode="decimal" placeholder="${esc(t.ccy)}"><em id="otSlPct"></em></label>
        <label class="ot-ex"><input type="checkbox" id="otTpOn"><span>Take profit</span>
          <input class="textfield" id="otTp" inputmode="decimal" placeholder="${esc(t.ccy)}"><em id="otTpPct"></em></label>
      </div>
      <dl class="ot-sum" id="otSum"></dl>
      <p class="ot-note" id="otNote"></p>
      <p class="ot-err" id="otErr" role="alert"></p>
      <button class="ot-go" id="otGo"></button>
      <p class="ot-foot">Paper trade. Simulated with practice money; nothing reaches a broker.</p>`;
    const $ = (id) => box.querySelector("#" + id);
    const num = (id) => { const v = parseFloat(String($(id).value).replace(/,/g, "")); return Number.isFinite(v) ? v : null; };
    if (t.price != null) { $("otLimit").value = t.price; $("otTrig").value = t.price; }
    box.querySelector(".ot-x").onclick = close;

    const refPx = () => (st.type === "MARKET" ? t.price : st.type === "SL-M" ? num("otTrig") : num("otLimit"));
    const pct = () => (st.side === "BUY" ? t.charges_pct.buy : t.charges_pct.sell);
    const qty = () => {
      const v = num("otSize");
      if (v == null) return 0;
      if (st.by === "qty") return roundQty(v);
      const px = refPx();
      return px ? roundQty(v / (px * fx * (1 + pct()))) : 0;
    };

    function paint() {
      box.querySelectorAll("[data-side]").forEach((b) => b.classList.toggle("on", b.dataset.side === st.side));
      box.classList.toggle("sell", st.side === "SELL");
      box.querySelectorAll("[data-type]").forEach((b) => b.classList.toggle("on", b.dataset.type === st.type));
      box.querySelectorAll("[data-by]").forEach((b) => b.classList.toggle("on", b.dataset.by === st.by));
      box.querySelector('[data-for="limit"]').hidden = !(st.type === "LIMIT" || st.type === "SL");
      box.querySelector('[data-for="trigger"]').hidden = !(st.type === "SL" || st.type === "SL-M");
      const exits = st.side === "BUY" && (st.type === "MARKET" || st.type === "LIMIT");
      box.querySelector(".ot-exits").hidden = !exits;
      $("otSl").disabled = !$("otSlOn").checked; $("otTp").disabled = !$("otTpOn").checked;

      const q = qty(), px = refPx();
      const value = q && px ? q * px * fx : 0;
      const charges = value * pct();
      const total = st.side === "BUY" ? value + charges : value - charges;
      const held = Number(t.position_qty || 0);
      const qtxt = q ? q.toLocaleString("en-US", { maximumFractionDigits: dp }) : "0";
      const after = st.side === "BUY" ? `${inr(t.cash_available - total)} cash left`
        : `${(held - q).toLocaleString("en-US", { maximumFractionDigits: dp })} left`;
      $("otSum").innerHTML = `
        <div><dt>Quantity</dt><dd>${qtxt}${st.by === "amt" ? " (from amount)" : ""}</dd></div>
        <div><dt>${st.type === "MARKET" ? "Price now" : "At"}</dt><dd>${px ? native(px) : "–"}</dd></div>
        <div><dt>Order value</dt><dd>${inr(value)}</dd></div>
        <div><dt>Charges, est.</dt><dd>${inr(charges)}</dd></div>
        <div class="tot"><dt>${st.side === "BUY" ? "You pay" : "You get"}</dt><dd>${inr(total)}</dd></div>
        <div><dt>${st.side === "BUY" ? "Cash" : "Holding"}</dt><dd>${st.side === "BUY" ? inr(t.cash_available) : `${held.toLocaleString("en-US", { maximumFractionDigits: dp })} held`} · ${after}</dd></div>`;
      for (const [on, id, out, dir] of [["otSlOn", "otSl", "otSlPct", -1], ["otTpOn", "otTp", "otTpPct", 1]]) {
        const v = num(id), base = refPx();
        $(out).textContent = $(on).checked && v && base ? `${((v / base - 1) * 100).toFixed(1)}%` : "";
        $(out).classList.toggle("bad", !!($(on).checked && v && base && (v - base) * dir <= 0));
      }
      const notes = [];
      if (t.stale) notes.push(`${t.symbol} has not traded here since ${when}, so a market order will not fill.`);
      if (t.ccy !== "INR") notes.push(`Priced in ${t.ccy}; your paper book pays in rupees at ₹${(t.fx || 0).toFixed(2)} per ${t.ccy}.`);
      if (st.type === "LIMIT") notes.push(`Fills when the price reaches your limit${st.side === "BUY" ? " or lower" : " or higher"}.`);
      if (st.type === "SL-M") notes.push(`A market order goes in when the price ${st.side === "BUY" ? "rises" : "falls"} to the trigger.`);
      if (st.type === "SL") notes.push(`At the trigger, a limit order goes in at your limit price.`);
      $("otNote").textContent = notes.join(" ");
      const go = $("otGo");
      const verb = st.side === "BUY" ? "Buy" : "Sell";
      go.textContent = st.busy ? "Placing…" : `${verb} ${qtxt} ${Universe.shown ? Universe.shown(t.symbol) : t.symbol}${st.type === "MARKET" ? "" : ` · ${TYPES.find((x) => x[0] === st.type)[1]}`}`;
      go.disabled = st.busy || !q || !px;
    }

    box.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      if (b.dataset.side) { st.side = b.dataset.side; $("otErr").textContent = ""; }
      if (b.dataset.type) st.type = b.dataset.type;
      if (b.dataset.by) { st.by = b.dataset.by; $("otSize").value = ""; }
      if (b.dataset.pct) {
        const p = Number(b.dataset.pct) / 100, px = refPx();
        if (st.side === "BUY") {
          const budget = t.cash_available * p;
          if (st.by === "amt") $("otSize").value = Math.floor(budget);
          else if (px) $("otSize").value = roundQty(budget / (px * fx * (1 + pct())));
        } else {
          const q = roundQty(Number(t.position_qty || 0) * p);
          if (st.by === "qty") $("otSize").value = q;
          else if (px) $("otSize").value = Math.floor(q * px * fx);
        }
      }
      if (b.id === "otGo") return submit();
      paint();
    });
    box.addEventListener("input", paint);
    box.addEventListener("change", paint);
    box.addEventListener("keydown", (e) => { if (e.key === "Enter" && !$("otGo").disabled) submit(); });

    async function submit() {
      const q = qty();
      if (!q || st.busy) return;
      st.busy = true; $("otErr").textContent = ""; paint();
      const body = { symbol: t.symbol, side: st.side, order_type: st.type, quantity: q };
      if (st.type === "LIMIT" || st.type === "SL") body.limit_price = num("otLimit");
      if (st.type === "SL" || st.type === "SL-M") body.trigger_price = num("otTrig");
      if (!box.querySelector(".ot-exits").hidden) {
        if ($("otSlOn").checked && num("otSl")) body.stop_loss = num("otSl");
        if ($("otTpOn").checked && num("otTp")) body.take_profit = num("otTp");
      }
      let res;
      try {
        res = await fetch(API + "/paper/orders", {
          method: "POST", headers: Auth.headers({ "Content-Type": "application/json" }),
          body: JSON.stringify(body) });
      } catch { res = null; }
      let d = {};
      try { d = res ? await res.json() : {}; } catch {}
      st.busy = false;
      if (!res || !res.ok) {
        $("otErr").textContent = d.error || "The order could not be placed. Try again.";
        return paint();
      }
      const name = Universe.shown ? Universe.shown(t.symbol) : t.symbol;
      const qs = Number(d.quantity).toLocaleString("en-US", { maximumFractionDigits: dp });
      if (d.status === "filled") {
        const exits = !d.exits ? "" : d.exits.length === 2 ? " Stop loss and take profit are set."
          : d.exits[0].kind === "stop_loss" ? " Stop loss set." : " Take profit set.";
        toast(`${d.side === "BUY" ? "Bought" : "Sold"} ${qs} ${name} at ${native(d.price_native)} (paper).${exits}`);
      } else {
        toast(`${TYPES.find((x) => x[0] === d.order_type)[1]} ${d.side.toLowerCase()} order placed: ${qs} ${name} (paper).`);
      }
      document.dispatchEvent(new CustomEvent("pivot:paper-changed", { detail: d }));
      close();
    }
    paint();
    setTimeout(() => $("otSize").focus(), 30);
  }

  return { open, close };
})();

window.OrderTicket = OrderTicket;
