/* Charto preview — the Strategy widget.
 *
 * Pick a rule, test it, read the evidence, and arm it into the paper book.
 * A rule is one of the lab's templates (data/strategy_lab.py) with its
 * parameters, or one of the user's saved strategies, or a draft another
 * surface hands over. Every run goes to POST /execution/lab, which resolves
 * the rule to the same steps[] the chat's Backtest button sends and runs it
 * through the same engine; the response carries the engine's metrics and the
 * lab's report (profit factor, streaks, drawdown series, months).
 *
 * Nothing here computes a statistic. The charts turn the server's series into
 * paths and the panels print the server's figures; where a figure is missing
 * the cell says so rather than deriving one.
 *
 * The verdict leads, as it does on the chat's card: a return without the
 * trust ladder's answer is a number without a meaning. Arming a rule the
 * ladder calls "no edge" takes a second, explicit press and is written into
 * the strategy's note.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined") return;
  const { API, esc, ic, num, pct, inr, dir, empty } = WKit;
  const MINUS = "−";

  const CHART_TABS = [["equity", "Equity"], ["dd", "Drawdown"], ["vol", "Volatility"], ["mc", "Simulated"], ["months", "Months"]];
  const SECTIONS = [["overview", "Overview"], ["trades", "Trades"], ["method", "Method"]];
  const VERDICT = {
    promising: { cls: "good", short: "Promising" },
    unproven: { cls: "mid", short: "Unproven" },
    no_edge: { cls: "bad", short: "No edge" },
    insufficient_data: { cls: "none", short: "Too little data" },
  };
  const FLAG = { drawdown_risk: "Deep drawdowns", loss_likely: "Loss likely", few_trades: "Few trades",
    concentrated: "One period carries it", overfit: "Overfit risk" };

  // one catalogue per page: the templates do not change between widgets
  let catalogP = null;
  const catalog = () => (catalogP ||= WKit.json("/execution/templates").catch((e) => { catalogP = null; throw e; }));

  const authHeaders = () => (typeof Auth !== "undefined" && Auth.headers ? Auth.headers() : {});
  const signedIn = () => typeof Auth !== "undefined" && !!Auth.user;

  async function call(path, body) {
    const r = await fetch(API + path, {
      method: body ? "POST" : "GET",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: body ? JSON.stringify(body) : undefined,
    });
    let d = null;
    try { d = await r.json(); } catch { /* not json */ }
    if (!r.ok) throw new Error((d && d.error) || `HTTP ${r.status}`);
    return d;
  }

  // ── formats ──
  const fin = (x) => x != null && Number.isFinite(Number(x));
  const depth = (x) => (fin(x) ? `${MINUS}${Math.abs(+x).toFixed(1)}%` : "—");
  const signedInr = (x) => (fin(x) ? `${x >= 0 ? "+" : ""}${inr(Math.round(+x))}` : "—");
  const fixed = (x, d = 2) => (fin(x) ? `${x < 0 ? MINUS : ""}${Math.abs(+x).toFixed(d)}` : "—");
  function when(t, iv) {
    if (!t) return "";
    const d = new Date(String(t).length <= 10 ? `${t}T00:00:00` : String(t).replace(" ", "T"));
    if (Number.isNaN(+d)) return String(t);
    const day = d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "2-digit" });
    return iv && iv !== "1d" ? `${day} ${d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false })}` : day;
  }

  function mount(host, ctx) {
    let cat = null, catErr = "";
    let saved = null;                  // the user's strategies, once asked for
    let res = null, err = "", busy = false, seq = 0, lastKey = "";
    let pinned = null;                 // a run kept to compare against
    let popOpen = false, armOpen = false, armBusy = false, armMsg = "";
    let hoverI = -1;
    const cache = new Map();           // request → result, for this session

    host.innerHTML =
      `<div class="nw-head st-head">` +
        `<button type="button" class="st-pick" data-st="pick"><span class="st-pick-ic">${ic("sigma")}</span>` +
          `<span class="st-pick-t"><b></b><small></small></span>${ic("chevronDown")}</button>` +
        `<span class="sh-gap"></span>` +
        `<button type="button" class="sh-btn i" data-st="inputs" title="Inputs">${ic("settings")}</button>` +
        `<button type="button" class="sh-btn i" data-st="run" title="Run again">${ic("rotateCw")}</button>` +
      `</div>` +
      `<div class="st-ctl">` +
        `<button type="button" class="st-sym" data-st="sym"></button>` +
        `<div class="dk-seg st-iv"></div>` +
        `<div class="dk-seg st-per"></div>` +
      `</div>` +
      `<div class="st-busy"><i></i></div>` +
      `<div class="st-pop" hidden></div>` +
      `<div class="side-body st-body"></div>` +
      `<div class="nw-foot st-foot"></div>`;
    const $ = (s) => host.querySelector(s);
    const body = $(".st-body"), foot = $(".st-foot"), pop = $(".st-pop");

    // ── what is being tested ──
    const cfg = () => ctx.cfg;
    const src = () => cfg().src || "tpl:ma_cross";
    const isSaved = () => src().startsWith("saved:");
    const tplId = () => (isSaved() ? null : src().slice(4));
    const tpl = () => cat && cat.templates.find((t) => t.id === tplId());
    const savedItem = () => saved && saved.find((s) => `saved:${s.id}` === src());
    const iv = () => {
      if (isSaved()) { const s = savedItem(); return s && cat && cat.intervals.includes(s.interval) ? s.interval : "1d"; }
      return cat && cat.intervals.includes(cfg().iv) ? cfg().iv : "1d";
    };
    const period = () => {
      const opts = (cat && cat.periods[iv()]) || ["5y"];
      return opts.includes(cfg().period) ? cfg().period : opts[Math.min(2, opts.length - 1)];
    };
    const symbol = () => (isSaved() ? (savedItem() || {}).symbol || "" : ctx.symbol());
    const params = () => {
      const t = tpl(), mine = (cfg().params || {})[tplId()] || {};
      if (!t) return {};
      const out = {};
      for (const p of t.params) out[p.key] = mine[p.key] ?? p.def;
      return out;
    };
    const capital = () => Number(cfg().capital) || 100000;

    function request() {
      const base = { interval: iv(), period: period(), capital: capital() };
      if (isSaved()) return { ...base, strategy_id: Number(src().slice(6)) };
      return { ...base, template: tplId(), symbol: symbol(), params: params() };
    }

    // ── running ──
    async function run(force) {
      if (!cat) return paint();
      if (isSaved() && !savedItem()) return paint();
      const req = request();
      const key = JSON.stringify(req);
      if (!force && key === lastKey && res) return paint();
      lastKey = key;
      if (!force && cache.has(key)) { res = cache.get(key); err = ""; armOpen = false; armMsg = ""; return paint(); }
      const my = ++seq;
      busy = true; err = ""; paint();
      try {
        const out = await call("/execution/lab", req);
        if (my !== seq) return;
        cache.set(key, out);
        res = out; armOpen = false; armMsg = "";
      } catch (e) {
        if (my !== seq) return;
        err = e.message || String(e);
      }
      busy = false;
      paint();
    }
    let runT = 0;
    const runSoon = () => { clearTimeout(runT); runT = setTimeout(() => run(false), 220); };

    async function loadSaved() {
      if (!signedIn()) { saved = []; return; }
      try { saved = (await call("/strategies")).strategies || []; } catch { saved = saved || []; }
    }

    // ── header ──
    function paintHead() {
      const t = tpl(), s = savedItem();
      $(".st-pick-t b").textContent = isSaved() ? (s ? s.name : "Saved strategy") : t ? t.name : "Strategy";
      $(".st-pick-t small").textContent = isSaved() ? (s ? `Yours · ${s.state}` : "") : t ? t.family : "";
      const sb = $(".st-sym");
      sb.innerHTML = `${esc(symbol() || "Symbol")}${isSaved() ? "" : ic("chevronDown")}`;
      sb.disabled = isSaved();
      sb.title = isSaved() ? "A saved strategy trades its own symbol" : "Symbol to test on";
      $(".st-iv").innerHTML = (cat ? cat.intervals : ["1d"]).map((v) =>
        `<button type="button" data-iv="${v}" class="${v === iv() ? "on" : ""}"${isSaved() ? " disabled" : ""}>${v === "1d" ? "1D" : v === "1h" ? "1H" : v}</button>`).join("");
      $(".st-per").innerHTML = ((cat && cat.periods[iv()]) || []).map((v) =>
        `<button type="button" data-per="${v}" class="${v === period() ? "on" : ""}">${v.toUpperCase()}</button>`).join("");
      $('[data-st="inputs"]').hidden = isSaved();
      $('[data-st="inputs"]').classList.toggle("on", popOpen);
      host.classList.toggle("st-loading", busy);
    }

    // ── inputs popover ──
    function paintPop() {
      pop.hidden = !popOpen || isSaved();
      if (pop.hidden) return;
      const t = tpl(), p = params();
      if (!t) { pop.hidden = true; return; }
      const field = (q) => q.options
        ? `<label class="st-f"><span>${esc(q.label)}</span><select data-k="${q.key}">${q.options.map((o) =>
            `<option value="${esc(o)}"${String(p[q.key]) === o ? " selected" : ""}>${esc(o.toUpperCase())}</option>`).join("")}</select></label>`
        : `<label class="st-f"><span>${esc(q.label)}</span><input type="number" data-k="${q.key}" value="${esc(p[q.key])}" ` +
          `min="${q.min}" max="${q.max}" step="${q.step}" inputmode="decimal"></label>`;
      pop.innerHTML =
        `<p class="st-pop-blurb">${esc(t.blurb)}</p>` +
        `<div class="st-pop-grid">${t.params.map(field).join("")}` +
          `<label class="st-f"><span>Capital ₹</span><input type="number" data-k="__capital" value="${esc(capital())}" min="1000" step="10000" inputmode="numeric"></label>` +
        `</div>` +
        `<div class="st-pop-acts"><button type="button" class="btn outline" data-st="reset">Defaults</button>` +
        `<button type="button" class="btn cta" data-st="apply">Run</button></div>`;
    }
    function applyPop() {
      const t = tpl();
      if (!t) return;
      const next = { ...(cfg().params || {}) };
      const mine = {};
      for (const inp of pop.querySelectorAll("[data-k]")) {
        if (inp.dataset.k === "__capital") continue;
        const q = t.params.find((x) => x.key === inp.dataset.k);
        mine[q.key] = q.options ? inp.value : Number(inp.value);
      }
      next[t.id] = mine;
      const cap = Number(pop.querySelector('[data-k="__capital"]').value);
      popOpen = false;
      ctx.setCfg({ params: next, capital: Number.isFinite(cap) && cap >= 1000 ? cap : capital() });
      run(false);
    }

    // ── the body ──
    function kpis() {
      const m = res.metrics || {}, r = res.report || {};
      const cmp = pinned && pinned !== res ? pinned : null;
      const pm = cmp && cmp.metrics, pr = cmp && cmp.report;
      const delta = (a, b, d = 1, unit = "") => (cmp && fin(a) && fin(b)
        ? `<em class="${dir(a - b)}">${a - b >= 0 ? "+" : MINUS}${Math.abs(a - b).toFixed(d)}${unit}</em>` : "");
      const k = (label, val, cls, sub) => `<div class="st-k"><span>${label}</span><b class="${cls || ""}">${val}</b>${sub || ""}</div>`;
      return `<div class="st-kpis">` +
        k("Return", pct(m.total_return_pct, 1), dir(m.total_return_pct), delta(m.total_return_pct, pm && pm.total_return_pct, 1, "%") ||
          `<small>${esc(signedInr(m.pnl_inr))}</small>`) +
        k("Max drawdown", depth(m.max_drawdown_pct), "down", delta(-m.max_drawdown_pct, pm && -pm.max_drawdown_pct, 1, "%")) +
        k("Win rate", fin(m.hit_rate_pct) && r.trades ? `${(+m.hit_rate_pct).toFixed(0)}%` : "—", "", `<small>${r.wins ?? 0}/${r.trades ?? 0}</small>`) +
        k("Profit factor", fixed(r.profit_factor), r.profit_factor >= 1 ? "up" : fin(r.profit_factor) ? "down" : "", delta(r.profit_factor, pr && pr.profit_factor, 2)) +
        k("Sharpe", fixed(m.sharpe), "", delta(m.sharpe, pm && pm.sharpe, 2)) +
        k("Trades", r.trades ?? m.n_trades ?? "—", "", fin(m.capital_utilization_pct) ? `<small>${Math.round(m.capital_utilization_pct)}% in</small>` : "") +
      `</div>`;
    }

    function verdict() {
      const v = (res.metrics || {}).trust_verdict || {};
      const meta = VERDICT[v.verdict] || { cls: "none", short: v.label || "No verdict" };
      const flags = (v.flags || []).map((f) => `<i>${esc(FLAG[f] || f.replace(/_/g, " "))}</i>`).join("");
      return `<div class="st-verdict ${meta.cls}" title="${esc(v.rationale || "")}">` +
        `<span class="st-dot"></span><b>${esc(v.label || meta.short)}</b>` +
        (fin(v.confidence) ? `<span class="st-conf">${Math.round(v.confidence)}% confidence</span>` : "") +
        `<span class="st-flags">${flags}</span></div>`;
    }

    function chartCard() {
      const view = cfg().view || "equity";
      const tabs = CHART_TABS.map(([k, l]) => `<button type="button" data-view="${k}" class="${k === view ? "on" : ""}">${l}</button>`).join("");
      return `<div class="st-card">` +
        `<div class="st-card-h"><div class="st-tabs">${tabs}</div>` +
          `<button type="button" class="sh-btn i st-pin${pinned ? " on" : ""}" data-st="pin" title="${pinned ? "Stop comparing" : "Keep this run to compare"}">${ic("pin")}</button></div>` +
        `<div class="st-read"></div>` +
        `<div class="st-plot" data-plot="${view}"></div></div>`;
    }

    function sectionBody() {
      const sec = cfg().sec || "overview";
      const tabs = SECTIONS.map(([k, l]) => `<button type="button" data-sec="${k}" class="${k === sec ? "on" : ""}">${l}</button>`).join("");
      const inner = sec === "trades" ? tradesTable() : sec === "method" ? method() : overview();
      return `<div class="dk-seg st-sec">${tabs}</div><div class="st-sec-body">${inner}</div>`;
    }

    // ── overview: small charts, each with the one or two numbers it is about ──
    const tile = (title, figure, inner, wide) =>
      `<section class="st-tile${wide ? " wide" : ""}"><header><h4>${title}</h4>${figure ? `<b>${figure}</b>` : ""}</header>${inner}</section>`;

    function overview() {
      const m = res.metrics || {}, r = res.report || {}, vo = r.volatility || {}, sp = m.sub_periods || {};
      return `<div class="st-tiles">` +
        tile("Payoff", fin(r.payoff) ? `${fixed(r.payoff)}×` : "", payoffViz(r)) +
        tile("Win rate", fin(m.hit_rate_pct) && r.trades ? `${(+m.hit_rate_pct).toFixed(0)}%` : "", winViz(r)) +
        tile("Trade returns", r.trades ? `${r.trades}` : "", histViz(r.distribution), true) +
        tile("Trade by trade", "", seqViz(r.trade_returns || []), true) +
        tile("Volatility", fin(vo.annual_pct) ? `${(+vo.annual_pct).toFixed(1)}%` : "", vsBars([
          ["Strategy", vo.annual_pct, "eq"], ["Hold", vo.hold_annual_pct, "hold"]], (x) => `${x.toFixed(1)}%`)) +
        tile("Max drawdown", depth(m.max_drawdown_pct), vsBars([
          ["Strategy", fin(m.max_drawdown_pct) ? Math.abs(m.max_drawdown_pct) : null, "dn"],
          ["Hold", fin(m.benchmark_max_drawdown_pct) ? Math.abs(m.benchmark_max_drawdown_pct) : null, "hold"]], (x) => `${MINUS}${x.toFixed(1)}%`)) +
        tile("Consistency", fin(sp.positive_period_frac) ? `${Math.round(sp.positive_period_frac * 100)}% of slices up` : "", sliceViz(sp.period_returns_pct || []), true) +
      `</div>` + allFigures();
    }

    function payoffViz(r) {
      if (!fin(r.avg_win) && !fin(r.avg_loss)) return `<p class="st-nil">No closed trades</p>`;
      const w = Math.abs(+r.avg_win || 0), l = Math.abs(+r.avg_loss || 0), mx = Math.max(w, l, 1);
      const bar = (cls, v, label) => `<div class="st-hb"><span>${label}</span><i class="${cls}" style="--w:${(v / mx * 100).toFixed(1)}%"></i><em>${v ? inr(Math.round(v)) : "—"}</em></div>`;
      return bar("up", w, "Avg win") + bar("down", l, "Avg loss");
    }

    function winViz(r) {
      if (!r.trades) return `<p class="st-nil">No trades</p>`;
      const w = r.wins || 0, l = r.losses || 0;
      return `<div class="st-split"><i class="up" style="flex:${w || 0.0001}"></i><i class="down" style="flex:${l || 0.0001}"></i></div>` +
        `<div class="st-split-l"><span>${w} won</span><span>${l} lost</span></div>` +
        `<div class="st-split-l"><span>Best ${pct(r.best_trade_pct, 1)}</span><span>Worst ${pct(r.worst_trade_pct, 1)}</span></div>`;
    }

    function histViz(d) {
      if (!d || !d.bins || !d.bins.length) return `<p class="st-nil">No trades to spread</p>`;
      const W = 300, H = 74, n = d.bins.length, mx = Math.max(...d.bins.map((b) => b.n), 1);
      const bw = W / n;
      const zero = d.bins.findIndex((b) => b.lo < 0 && b.hi > 0) >= 0 || d.bins.some((b) => b.lo === 0)
        ? ((0 - d.bins[0].lo) / (d.bins[n - 1].hi - d.bins[0].lo)) * W : null;
      let svg = d.bins.map((b, i) => {
        const h = b.n ? Math.max(2, (b.n / mx) * (H - 14)) : 0;
        const cls = b.hi <= 0 ? "down" : b.lo >= 0 ? "up" : "mid";
        return `<rect class="${cls}" x="${(i * bw + 1).toFixed(1)}" y="${(H - 12 - h).toFixed(1)}" width="${Math.max(1, bw - 2).toFixed(1)}" height="${h.toFixed(1)}" rx="1.5"><title>${b.lo}% to ${b.hi}%: ${b.n}</title></rect>`;
      }).join("");
      if (zero != null) svg += `<line class="zero" x1="${zero}" x2="${zero}" y1="0" y2="${H - 12}"/>`;
      svg += `<text class="ax" x="0" y="${H - 1}">${d.bins[0].lo}%</text><text class="ax" x="${W}" y="${H - 1}" text-anchor="end">${d.bins[n - 1].hi}%</text>`;
      return `<svg class="st-mini" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${svg}</svg>`;
    }

    function seqViz(xs) {
      if (!xs.length) return `<p class="st-nil">No trades</p>`;
      const W = 300, H = 64, mid = H / 2, mx = Math.max(...xs.map((x) => Math.abs(x)), 0.01);
      const bw = W / xs.length;
      const svg = xs.map((x, i) => {
        const h = Math.max(1, (Math.abs(x) / mx) * (mid - 2));
        return `<rect class="${x > 0 ? "up" : "down"}" x="${(i * bw + bw * 0.15).toFixed(1)}" y="${(x > 0 ? mid - h : mid).toFixed(1)}" width="${Math.max(0.8, bw * 0.7).toFixed(1)}" height="${h.toFixed(1)}"><title>Trade ${i + 1}: ${pct(x, 2)}</title></rect>`;
      }).join("") + `<line class="zero" x1="0" x2="${W}" y1="${mid}" y2="${mid}"/>`;
      return `<svg class="st-mini" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${svg}</svg>`;
    }

    function vsBars(rows, fmt) {
      const ok = rows.filter(([, v]) => fin(v));
      if (!ok.length) return `<p class="st-nil">Unavailable</p>`;
      const mx = Math.max(...ok.map(([, v]) => +v), 0.01);
      return rows.map(([label, v, cls]) => `<div class="st-hb"><span>${label}</span>` +
        (fin(v) ? `<i class="${cls}" style="--w:${(v / mx * 100).toFixed(1)}%"></i><em>${fmt(+v)}</em>` : `<i></i><em>—</em>`) + `</div>`).join("");
    }

    function sliceViz(xs) {
      if (!xs.length) return `<p class="st-nil">Too short to slice</p>`;
      const W = 300, H = 56, mid = H / 2, mx = Math.max(...xs.map((x) => Math.abs(x)), 0.01), bw = W / xs.length;
      const svg = xs.map((x, i) => {
        const h = Math.max(1.5, (Math.abs(x) / mx) * (mid - 4));
        return `<rect class="${x > 0 ? "up" : "down"}" x="${(i * bw + bw * 0.2).toFixed(1)}" y="${(x > 0 ? mid - h : mid).toFixed(1)}" width="${(bw * 0.6).toFixed(1)}" height="${h.toFixed(1)}" rx="2"><title>Slice ${i + 1}: ${pct(x, 1)}</title></rect>`;
      }).join("") + `<line class="zero" x1="0" x2="${W}" y1="${mid}" y2="${mid}"/>`;
      return `<svg class="st-mini" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${svg}</svg>`;
    }

    function allFigures() {
      const m = res.metrics || {}, r = res.report || {}, mc = m.monte_carlo || {}, fs = m.forward_stats || {}, tr = r.trend || {};
      const row = (k, v, cls = "") => `<div class="st-row"><span>${k}</span><b class="${cls}">${v}</b></div>`;
      return `<details class="st-all"${cfg().allOpen ? " open" : ""}><summary>All figures</summary>` +
        row("Net P&amp;L", esc(signedInr(m.pnl_inr)), dir(m.pnl_inr)) +
        row("CAGR", pct(m.cagr_pct, 1), dir(m.cagr_pct)) +
        row("Buy &amp; hold", pct(m.benchmark_return_pct, 1), dir(m.benchmark_return_pct)) +
        row("Trend pace", fin(tr.pace_pct) ? `${pct(tr.pace_pct, 1)} a year` : "—", dir(tr.pace_pct)) +
        row("Trend fit (R²)", fixed(tr.r2)) +
        row("Sortino", fixed(m.sortino)) +
        row("Deflated Sharpe", fixed(fs.deflated_sharpe)) +
        row("Expectancy", esc(signedInr(r.expectancy)), dir(r.expectancy)) +
        row("Streaks", `${r.max_win_streak ?? "—"} won · ${r.max_loss_streak ?? "—"} lost`) +
        row("Avg hold", fin(r.avg_hold_days) ? `${r.avg_hold_days} days` : "—") +
        row("Longest underwater", fin(r.longest_underwater_bars) ? `${r.longest_underwater_bars} bars` : "—") +
        row("Simulated worst-5% drawdown", fin(mc.dd_p95_severity_pct) ? depth(mc.dd_p95_severity_pct) : "—") +
        row("Chance of a loss", fin(mc.prob_loss) ? `${Math.round(mc.prob_loss * 100)}%` : "—") +
        `</details>`;
    }

    function tradesTable() {
      const ts = res.trades || [];
      if (!ts.length) return `<p class="st-none">No trades in this window.</p>`;
      const ivv = res.interval || iv();
      const rows = ts.slice().reverse().map((t) => {
        const r = Number(t.return_pct) * 100;
        const open = t.exit_reason === "force_close";
        return `<tr><td class="st-id">${t.trade_id}</td>` +
          `<td>${esc(when(t.entry_date, ivv))}<small>${esc(num(t.entry_price))}</small></td>` +
          `<td>${open ? `<span class="st-open">Open</span>` : esc(when(t.exit_date, ivv))}<small>${esc(num(t.exit_price))}</small></td>` +
          `<td class="n ${dir(r)}">${pct(r, 2)}<small>${esc(signedInr(t.net_pnl))}</small></td></tr>`;
      }).join("");
      return `<table class="st-trades"><thead><tr><th>#</th><th>Entry</th><th>Exit</th><th class="n">Return</th></tr></thead><tbody>${rows}</tbody></table>`;
    }

    function method() {
      const md = res.methodology || {}, dg = res.diagnostics || {};
      const t = tpl();
      const line = (k, v) => (v ? `<div class="st-m"><span>${k}</span><p>${esc(v)}</p></div>` : "");
      const notes = [...(res.interval_notes || []), ...(res.assumptions || [])];
      const p = res.source && res.source.params;
      return line("Rule", res.tree_summary ? `Enter when ${res.tree_summary}.` + (t ? ` ${t.blurb}` : "") : t ? t.blurb : "") +
        (p ? line("Inputs", Object.entries(p).map(([k, v]) => `${k} ${v}`).join(" · ")) : "") +
        line("Window", md.window || res.period_label) +
        line("Bars", dg.bars_evaluated ? `${dg.bars_evaluated} ${res.interval || iv()} bars · fired on ${dg.fire_bars}` : "") +
        line("Costs", md.costs) + line("Data", md.basis) + line("Caveat", md.caveat) +
        notes.map((n) => line("Note", n)).join("");
    }

    function armPanel() {
      if (!armOpen) return "";
      const v = ((res.metrics || {}).trust_verdict || {});
      const weak = v.verdict === "no_edge" || v.verdict === "insufficient_data";
      if (!signedIn()) {
        return `<div class="st-arm"><p>Sign in to arm strategies into your paper book.</p>` +
          `<div class="st-arm-acts"><button type="button" class="btn cta" data-st="signin">Sign in</button></div></div>`;
      }
      return `<div class="st-arm">` +
        `<p><b>Arm into the paper book</b><span>Simulated fills on the live tick. No broker is touched.</span></p>` +
        `<label class="st-f"><span>Shares per entry</span><input type="number" data-arm="qty" value="${esc(cfg().qty || 1)}" min="1" step="1" inputmode="numeric"></label>` +
        (weak ? `<label class="st-ack"><input type="checkbox" data-arm="ack"> <span>The verdict is <b>${esc(v.label || v.verdict)}</b>. Arm it anyway; the override is recorded on the strategy.</span></label>` : "") +
        (armMsg ? `<p class="st-arm-msg">${esc(armMsg)}</p>` : "") +
        `<div class="st-arm-acts"><button type="button" class="btn outline" data-st="arm-cancel">Cancel</button>` +
        `<button type="button" class="btn cta" data-st="arm-go"${armBusy ? " disabled" : ""}>${armBusy ? "Arming…" : "Arm"}</button></div></div>`;
    }

    function paint() {
      paintHead();
      paintPop();
      if (catErr && !cat) {
        body.innerHTML = empty("sigma", `Strategies are unavailable: ${esc(catErr)}`, "Try again", 'data-st="retry"');
        foot.innerHTML = "";
        return;
      }
      if (!cat) { body.innerHTML = WKit.skel(6, "news"); return; }
      if (isSaved() && saved && !savedItem()) {
        body.innerHTML = empty("sigma", "That saved strategy is gone. Pick another.", "Choose", 'data-st="pick"');
        return;
      }
      if (!res) {
        body.innerHTML = err ? empty("sigma", esc(err), "Try again", 'data-st="run"') : WKit.skel(6, "news");
        foot.innerHTML = "";
        return;
      }
      body.innerHTML =
        (err ? `<div class="st-err">${esc(err)}</div>` : "") +
        verdict() + kpis() + chartCard() + armPanel() + sectionBody();
      drawPlot();
      const m = res.metrics || {};
      const onChart = typeof Cards !== "undefined" && Cards.strategyLayer;
      const want = String(res.symbol || "").toUpperCase();
      const here = String((window.__charto && window.__charto.symbol) || "").toUpperCase();
      const chartLabel = !onChart ? "" : want && here && want !== here ? `Open ${want}` : chartShown ? "Hide on chart" : "Show on chart";
      const isMine = res.source && res.source.kind === "saved";
      foot.innerHTML =
        `<span title="${esc((res.methodology || {}).costs || "")}">Paper · after costs · ${esc(res.period_label || "")}</span>` +
        `<span class="st-acts">` +
          (chartLabel ? `<button type="button" class="sh-btn" data-st="chart">${ic("candles")}<span>${chartLabel}</span></button>` : "") +
          `<button type="button" class="sh-btn" data-st="ask">${ic("chat")}<span>Ask</span></button>` +
          (isMine ? "" : `<button type="button" class="sh-btn st-arm-btn" data-st="arm">${ic("zap")}<span>Arm</span></button>`) +
        `</span>`;
      if (m.n_trades === 0 && !err) { /* the verdict already says why: nothing more to add */ }
    }

    // ── charts: geometry from the server's series, no statistics ──
    function scaleY(vals, h, padT = 10, padB = 16) {
      let lo = Infinity, hi = -Infinity;
      for (const v of vals) if (fin(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
      if (!Number.isFinite(lo)) { lo = 0; hi = 1; }
      if (hi === lo) { hi += 1; lo -= 1; }
      const pad = (hi - lo) * 0.06;
      lo -= pad; hi += pad;
      return { lo, hi, y: (v) => padT + (1 - (v - lo) / (hi - lo)) * (h - padT - padB) };
    }
    const path = (vals, x, y) => {
      let d = "", pen = false;
      vals.forEach((v, i) => {
        if (!fin(v)) { pen = false; return; }
        d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
        pen = true;
      });
      return d;
    };
    function ticks(lo, hi, n = 3) {
      const span = hi - lo, raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
      const step = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => span / s <= n + 1) || mag * 10;
      const out = [];
      for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) out.push(+v.toPrecision(12));
      return out;
    }
    const short = (v) => {
      const a = Math.abs(v);
      return a >= 1e7 ? `${(v / 1e7).toFixed(1)}Cr` : a >= 1e5 ? `${(v / 1e5).toFixed(1)}L` : a >= 1e3 ? `${(v / 1e3).toFixed(0)}k` : `${Math.round(v)}`;
    };

    function drawPlot() {
      const plot = body.querySelector(".st-plot");
      if (!plot) return;
      const W = Math.max(240, plot.clientWidth || 320), H = 168;
      const view = plot.dataset.plot;
      const c = (res.report || {}).curve;
      const read = body.querySelector(".st-read");
      if (view === "months") { plot.innerHTML = months(); read.innerHTML = `<span>Monthly return, %</span>`; return; }
      if (view === "mc") { plot.innerHTML = monteCarlo(W, H); read.innerHTML = mcRead(); return; }
      if (!c || !c.t.length) { plot.innerHTML = `<p class="st-none">No equity series in this result.</p>`; return; }
      const n = c.t.length, R = 44;
      const x = (i) => (n === 1 ? 0 : (i / (n - 1)) * (W - R));
      let svg = "";
      if (view === "vol") {
        const vol = c.vol || [], hv = c.hold_vol || [];
        if (!vol.some(fin)) { plot.innerHTML = `<p class="st-none">Too few bars for a rolling volatility.</p>`; read.textContent = ""; return; }
        const s = scaleY([0, ...vol, ...hv], H);
        svg += ticks(s.lo, s.hi).map((v) => `<line class="g" x1="0" x2="${W - R}" y1="${s.y(v)}" y2="${s.y(v)}"/><text class="ax" x="${W - R + 6}" y="${s.y(v) + 3}">${v.toFixed(0)}%</text>`).join("");
        svg += `<path class="hold" d="${path(hv, x, s.y)}"/><path class="vol" d="${path(vol, x, s.y)}"/>`;
        plot.__geo = { x, n, kind: "vol", y: s.y };
      } else if (view === "dd") {
        const s = scaleY([0, ...c.drawdown], H);
        const line = path(c.drawdown, x, s.y);
        svg += ticks(s.lo, s.hi).map((v) => `<line class="g" x1="0" x2="${W - R}" y1="${s.y(v)}" y2="${s.y(v)}"/><text class="ax" x="${W - R + 6}" y="${s.y(v) + 3}">${v.toFixed(0)}%</text>`).join("");
        svg += `<path class="dd-a" d="${line}L${x(n - 1)},${s.y(0)}L0,${s.y(0)}Z"/><path class="dd-l" d="${line}"/>`;
        plot.__geo = { x, n, kind: "dd", y: s.y };
      } else {
        const ghost = ghostCurve();
        const s = scaleY([...c.equity, ...c.hold, ...(c.trend || []), ...(ghost || [])], H);
        svg += ticks(s.lo, s.hi).map((v) => `<line class="g" x1="0" x2="${W - R}" y1="${s.y(v)}" y2="${s.y(v)}"/><text class="ax" x="${W - R + 6}" y="${s.y(v) + 3}">${short(v)}</text>`).join("");
        const eq = path(c.equity, x, s.y);
        svg += `<defs><linearGradient id="stg-${ctx.id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="currentColor" stop-opacity=".16"/><stop offset="1" stop-color="currentColor" stop-opacity="0"/></linearGradient></defs>`;
        svg += `<path class="hold" d="${path(c.hold, x, s.y)}"/>`;
        if (c.trend) svg += `<path class="trend" d="${path(c.trend, x, s.y)}"/>`;
        if (ghost) {
          const gx = (i) => (ghost.length === 1 ? 0 : (i / (ghost.length - 1)) * (W - R));
          svg += `<path class="ghost" d="${path(ghost, gx, s.y)}"/>`;
        }
        svg += `<path class="eq-a" fill="url(#stg-${ctx.id})" d="${eq}L${x(n - 1)},${H - 16}L0,${H - 16}Z"/><path class="eq" d="${eq}"/>`;
        // the trade rug: one tick per entry along the floor, coloured by its result
        const ts = res.trades || [];
        const at = (d) => { let lo = 0, hi = n - 1; const key = String(d).slice(0, 19); while (lo < hi) { const mid = (lo + hi) >> 1; if (String(c.t[mid]) < key) lo = mid + 1; else hi = mid; } return lo; };
        svg += ts.map((t) => `<line class="rug ${Number(t.net_pnl) > 0 ? "up" : "down"}" x1="${x(at(t.entry_date)).toFixed(1)}" x2="${x(at(t.entry_date)).toFixed(1)}" y1="${H - 23}" y2="${H - 17}"/>`).join("");
        plot.__geo = { x, n, kind: "eq", y: s.y };
      }
      // dates along the bottom
      const marks = [0, Math.round((n - 1) / 2), n - 1];
      svg += marks.map((i, k) => `<text class="ax" x="${x(i)}" y="${H - 1}" text-anchor="${k === 0 ? "start" : k === 2 ? "end" : "middle"}">${esc(when(c.t[i]))}</text>`).join("");
      svg += `<line class="cross" x1="0" x2="0" y1="6" y2="${H - 16}" visibility="hidden"/><circle class="dot" r="3" visibility="hidden"/>`;
      plot.innerHTML = `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${svg}</svg>`;
      read.innerHTML = defaultRead();
      if (hoverI >= 0) hover(hoverI);
    }

    /** The pinned run's equity, when it is drawable against this one (same bars, same window). */
    function ghostCurve() {
      return pinned && pinned !== res && pinned.report && pinned.report.curve &&
        pinned.interval === res.interval && pinned.period === res.period ? pinned.report.curve.equity : null;
    }

    function defaultRead() {
      const m = res.metrics || {};
      const r = res.report || {}, view = cfg().view || "equity", vo = r.volatility || {}, tr = r.trend || {}, c = r.curve || {};
      const key = (cls, label, v) => `<span class="st-key ${cls}"><i></i>${label} <b>${v}</b></span>`;
      if (view === "dd") return key("dn", "Deepest", depth(m.max_drawdown_pct));
      if (view === "vol") return fin(vo.annual_pct)
        ? key("eq", `Strategy · ${vo.window_bars}-bar`, `${(+vo.annual_pct).toFixed(1)}%/yr`) +
          (fin(vo.hold_annual_pct) ? key("hold", "Hold", `${(+vo.hold_annual_pct).toFixed(1)}%/yr`) : "") : "";
      return key("eq", "Strategy", pct(m.total_return_pct, 1)) +
        ((c.hold || []).some(fin) ? key("hold", "Hold", pct(m.benchmark_return_pct, 1)) : "") +
        (fin(tr.pace_pct) ? key("trend", "Trend", `${pct(tr.pace_pct, 1)}/yr`) : "") +
        (ghostCurve() ? key("ghost", "Pinned", pct((pinned.metrics || {}).total_return_pct, 1)) : "");
    }

    function hover(i) {
      const plot = body.querySelector(".st-plot");
      const g = plot && plot.__geo, c = res && res.report && res.report.curve;
      if (!g || !c) return;
      const cross = plot.querySelector(".cross"), dot = plot.querySelector(".dot"), read = body.querySelector(".st-read");
      if (i < 0) { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); read.innerHTML = defaultRead(); return; }
      const xi = g.x(i), v = g.kind === "dd" ? c.drawdown[i] : g.kind === "vol" ? c.vol[i] : c.equity[i];
      if (!fin(v)) { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); return; }
      cross.setAttribute("x1", xi); cross.setAttribute("x2", xi); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", xi); dot.setAttribute("cy", g.y(v)); dot.setAttribute("visibility", "visible");
      read.textContent = g.kind === "dd" ? `${when(c.t[i], res.interval)} · ${v.toFixed(1)}%`
        : g.kind === "vol" ? `${when(c.t[i], res.interval)} · ${v.toFixed(1)}%${fin(c.hold_vol[i]) ? ` · hold ${c.hold_vol[i].toFixed(1)}%` : ""}`
        : `${when(c.t[i], res.interval)} · ${inr(Math.round(v))}${fin(c.hold[i]) ? ` · hold ${inr(Math.round(c.hold[i]))}` : ""}`;
    }

    function monteCarlo(W, H) {
      const mc = ((res.metrics || {}).monte_carlo) || {};
      const p = mc.paths || {};
      // `points` is the MEDIAN of the simulated paths (monte_carlo.py), not the realised run
      const sample = p.sample || [], real = p.points || [];
      if (!sample.length) return `<p class="st-none">Too few trades to resample.</p>`;
      const all = sample.flat().concat(real);
      const R = 44, s = scaleY(all, H, 10, 8);
      const n = Math.max(real.length, ...sample.map((a) => a.length));
      const x = (i) => (n === 1 ? 0 : (i / (n - 1)) * (W - R));
      let svg = ticks(s.lo, s.hi).map((v) => `<line class="g" x1="0" x2="${W - R}" y1="${s.y(v)}" y2="${s.y(v)}"/><text class="ax" x="${W - R + 6}" y="${s.y(v) + 3}">${v.toFixed(0)}%</text>`).join("");
      svg += `<line class="zero" x1="0" x2="${W - R}" y1="${s.y(0)}" y2="${s.y(0)}"/>`;
      svg += sample.map((a) => `<path class="mc-s ${a[a.length - 1] >= 0 ? "up" : "down"}" d="${path(a, x, s.y)}"/>`).join("");
      if (real.length) svg += `<path class="eq" d="${path(real, x, s.y)}"/>`;
      return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${svg}</svg>`;
    }
    function mcRead() {
      const mc = ((res.metrics || {}).monte_carlo) || {};
      if (!fin(mc.terminal_median_pct)) return "";
      const key = (cls, label, v) => `<span class="st-key ${cls}"><i></i>${label} <b>${v}</b></span>`;
      return key("eq", "Median of " + (mc.n_sims || "") + " resampled", pct(mc.terminal_median_pct, 0)) +
        key("dn", "5th percentile", pct(mc.terminal_p05_pct, 0)) +
        (fin(mc.prob_loss) ? key("hold", "Ends in a loss", `${Math.round(mc.prob_loss * 100)}%`) : "");
    }

    function months() {
      const rows = (res.report || {}).monthly || [];
      if (!rows.length) return `<p class="st-none">Not enough history for a monthly view.</p>`;
      const vals = rows.flatMap((r) => r.months).filter(fin).map(Math.abs);
      const cap = Math.max(2, Math.min(15, vals.length ? Math.max(...vals) : 5));
      const cell = (v) => {
        if (!fin(v)) return `<td class="nil"></td>`;
        const a = Math.min(1, Math.abs(v) / cap) * 0.85 + 0.08;
        const col = v >= 0 ? "var(--up)" : "var(--down)";
        return `<td style="--a:${a.toFixed(2)};--c:${col}" title="${pct(v, 1)}"><span>${Math.abs(v) >= 10 ? v.toFixed(0) : v.toFixed(1)}</span></td>`;
      };
      const head = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"].map((m) => `<th>${m}</th>`).join("");
      return `<table class="st-months"><thead><tr><th></th>${head}<th>Year</th></tr></thead><tbody>${rows.map((r) =>
        `<tr><th>${esc(r.year.slice(2))}</th>${r.months.map(cell).join("")}<td class="yr ${dir(r.total)}">${pct(r.total, 1)}</td></tr>`).join("")}</tbody></table>`;
    }

    // ── the chart layer ──
    let chartShown = false;
    function toggleChart() {
      const layer = typeof Cards !== "undefined" && Cards.strategyLayer;
      const scene = window.__charto && window.__charto.scene;
      if (!layer || !scene || !res) return;
      const want = String(res.symbol || "").toUpperCase();
      const here = String((window.__charto && window.__charto.symbol) || "").toUpperCase();
      if (want && here && want !== here) { ctx.openSymbol(want); return; }
      if (chartShown) { scene.apply([layer.clear()]); chartShown = false; return paint(); }
      const why = layer.blocker(res);
      if (why) { ctx.toast(why); return; }
      const items = layer.items(res);
      if (!items.length) { ctx.toast("No bars for these dates on the chart."); return; }
      scene.apply([layer.clear()].concat(items));
      chartShown = true;
      paint();
    }

    // ── arming ──
    async function arm() {
      const qty = Math.floor(Number((body.querySelector('[data-arm="qty"]') || {}).value));
      if (!(qty >= 1)) { armMsg = "Shares per entry has to be a whole number, 1 or more."; return paint(); }
      const v = ((res.metrics || {}).trust_verdict || {});
      const weak = v.verdict === "no_edge" || v.verdict === "insufficient_data";
      const ack = body.querySelector('[data-arm="ack"]');
      if (weak && !(ack && ack.checked)) { armMsg = "Tick the box to arm over this verdict."; return paint(); }
      const draft = JSON.parse(JSON.stringify(res.draft || {}));
      for (const st of draft.steps || []) if (st.step_type === "action.place_order") st.config.quantity = qty;
      const note = `From the Strategy widget: ${res.period_label || ""} ${res.interval || ""} backtest, ${pct((res.metrics || {}).total_return_pct, 1)}, verdict ${v.label || v.verdict || "none"}` +
        (weak ? ". Armed over the verdict by the user's explicit override." : ".");
      armBusy = true; armMsg = ""; ctx.setCfg({ qty }); paint();
      try {
        const out = await call("/strategies", { draft, note, arm: true });
        armOpen = false;
        ctx.toast(out.already_armed ? `Already armed as strategy ${out.id}` : `Armed · strategy ${out.id} · paper`);
        saved = null;
      } catch (e) {
        armMsg = e.message || String(e);
      }
      armBusy = false;
      paint();
    }

    // ── the picker ──
    async function pick(anchor) {
      if (saved == null) await loadSaved();
      const items = [{ head: "Templates" }].concat((cat ? cat.templates : []).map((t) =>
        ({ id: `tpl:${t.id}`, label: t.name, hint: t.family, on: src() === `tpl:${t.id}` })));
      items.push({ sep: true }, { head: "Yours" });
      if (!signedIn()) items.push({ id: "signin", label: "Sign in to see your strategies" });
      else if (!saved.length) items.push({ id: "none", label: "No saved strategies yet", off: true });
      else items.push(...saved.map((s) => ({ id: `saved:${s.id}`, label: s.name, hint: `${s.symbol} · ${s.state}`, on: src() === `saved:${s.id}` })));
      ctx.menu(anchor, items, (id) => {
        if (id === "signin") return window.CHARTO_AUTH_OPEN && window.CHARTO_AUTH_OPEN();
        if (id === src()) return;
        popOpen = false; chartShown = false;
        ctx.setCfg({ src: id });
        run(false);
      });
    }

    // ── events ──
    host.addEventListener("click", (e) => {
      const ivb = e.target.closest("[data-iv]");
      if (ivb && !ivb.disabled) { ctx.setCfg({ iv: ivb.dataset.iv }); return run(false); }
      const pb = e.target.closest("[data-per]");
      if (pb) { ctx.setCfg({ period: pb.dataset.per }); return run(false); }
      const vb = e.target.closest("[data-view]");
      if (vb) { ctx.setCfg({ view: vb.dataset.view }); hoverI = -1; return paint(); }
      const sb = e.target.closest("[data-sec]");
      if (sb) { ctx.setCfg({ sec: sb.dataset.sec }); return paint(); }
      const b = e.target.closest("[data-st]");
      if (!b) return;
      e.stopPropagation();
      const a = b.dataset.st;
      if (a === "pick") return pick(b);
      if (a === "run") return run(true);
      if (a === "retry") { catErr = ""; return start(); }
      if (a === "inputs") { popOpen = !popOpen; return paint(); }
      if (a === "reset") {
        const next = { ...(cfg().params || {}) }; delete next[tplId()];
        popOpen = false; ctx.setCfg({ params: next }); return run(false);
      }
      if (a === "apply") return applyPop();
      if (a === "sym") return setTimeout(() => Universe.open({ anchor: b, current: symbol(), onPick: (s) => ctx.setCfg({ pin: s, link: "pin" }) }), 0);
      if (a === "pin") { pinned = pinned ? null : res; return paint(); }
      if (a === "chart") return toggleChart();
      if (a === "arm") { armOpen = !armOpen; armMsg = ""; return paint(); }
      if (a === "arm-cancel") { armOpen = false; return paint(); }
      if (a === "arm-go") return arm();
      if (a === "signin") return window.CHARTO_AUTH_OPEN && window.CHARTO_AUTH_OPEN();
      if (a === "ask") { const got = askGot(); if (got) ctx.ask(got); }
    });
    pop.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); applyPop(); }
      if (e.key === "Escape") { popOpen = false; paint(); }
    });
    body.addEventListener("pointermove", (e) => {
      const plot = e.target.closest(".st-plot");
      const g = plot && plot.__geo;
      if (!g) return;
      const r = plot.getBoundingClientRect();
      const span = g.x(g.n - 1) || 1;
      const i = Math.max(0, Math.min(g.n - 1, Math.round(((e.clientX - r.left) / span) * (g.n - 1))));
      if (i !== hoverI) { hoverI = i; hover(i); }
    });
    body.addEventListener("toggle", (e) => { if (e.target.matches && e.target.matches(".st-all")) ctx.setCfg({ allOpen: e.target.open }); }, true);
    body.addEventListener("pointerleave", () => { if (hoverI >= 0) { hoverI = -1; hover(-1); } });
    new ResizeObserver(() => { if (res) drawPlot(); }).observe(body);
    if (typeof Auth !== "undefined" && Auth.onChange) Auth.onChange(() => { saved = null; if (isSaved()) loadSaved().then(() => run(false)); else paint(); });

    function askGot() {
      if (!res) return null;
      const m = res.metrics || {}, r = res.report || {}, v = m.trust_verdict || {};
      const name = isSaved() ? (savedItem() || {}).name : (tpl() || {}).name;
      const p = res.source && res.source.params;
      return {
        sub: `${name || "Strategy"} · ${res.symbol} · ${pct(m.total_return_pct, 1)} · ${v.label || ""}`,
        context: `Backtest from the Strategy widget (paper, after costs): ${name} on ${res.symbol}, ${res.interval} bars, ${res.period_label}.` +
          (p ? ` Inputs: ${Object.entries(p).map(([k, x]) => `${k} ${x}`).join(", ")}.` : "") +
          ` Rule: enter when ${res.tree_summary || "—"}.\n` +
          `Return ${pct(m.total_return_pct, 1)} vs buy & hold ${pct(m.benchmark_return_pct, 1)}; CAGR ${pct(m.cagr_pct, 1)}; max drawdown ${depth(m.max_drawdown_pct)}; ` +
          `Sharpe ${fixed(m.sharpe)}; ${r.trades} trades, win rate ${fixed(m.hit_rate_pct, 0)}%, profit factor ${fixed(r.profit_factor)}, expectancy ${signedInr(r.expectancy)}.\n` +
          `Trust verdict: ${v.label} (${Math.round(v.confidence || 0)}%). ${v.rationale || ""}`,
        question: "Is this edge real, and what would make the rule more robust?",
      };
    }

    async function start() {
      try { cat = await catalog(); catErr = ""; }
      catch (e) { catErr = e.message || String(e); return paint(); }
      if (isSaved()) await loadSaved();
      run(false);
    }

    paint();

    return {
      show() { if (!cat) start(); else if (!res) run(false); else paint(); },
      config(c, patch) {
        // a new symbol under a template re-runs; a saved strategy keeps its own
        if (("pin" in patch || "link" in patch || "symbol" in patch) && !isSaved()) runSoon();
        else paintHead();
      },
      /** Another surface hands over a draft: { name, steps } runs as-is. */
      receive(payload) {
        if (!payload || !Array.isArray(payload.steps)) return;
        call("/execution/lab", { steps: payload.steps, name: payload.name, interval: iv(), period: period(), capital: capital() })
          .then((out) => { res = out; err = ""; paint(); })
          .catch((e) => { err = e.message || String(e); paint(); });
      },
      ask: askGot,
    };
  }

  Dock.register({
    type: "strategy", title: "Strategy", icon: "sigma", hue: "violet", group: "Trade",
    desc: "Test a rule on real bars, read the verdict, arm it into paper", zone: "right", minW: 340, mount,
    linkable: true,
    settings: [
      { section: "Defaults" },
      { key: "capital", label: "Starting capital", def: 100000,
        options: [{ v: 50000, label: "₹50k" }, { v: 100000, label: "₹1L" }, { v: 500000, label: "₹5L" }, { v: 1000000, label: "₹10L" }] },
      { kind: "note", label: "Backtests run after costs on Pivot's bars. Arming fills into the paper book only; no broker is touched." },
    ],
  });
})();
