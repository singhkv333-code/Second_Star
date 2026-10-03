/* Charto preview — the Financials widget.
 *
 * The company behind the chart, from the same store the company page reads:
 * the profile and valuation (/company), the quarterly results
 * (/api/stock/<sym>/quarters) and the four filed statements — P&L, balance
 * sheet, cash flow, ratios (/api/financials/<sym>/statement?type=…). Every
 * figure is the filed figure with its period and its unit beside it; a
 * company the store holds no filings for says so, in one line, and an index
 * or a coin says why it has none.
 *
 * Any statement, or the quarterly table, opens in a Sheet with one click.
 */
"use strict";

(() => {
  if (typeof Dock === "undefined") return;
  const { esc, ic, json, num, pct, inr, dir, empty, skel } = WKit;
  const TABS = [["overview", "Overview"], ["quarters", "Quarterly"], ["profit_loss", "P&L"],
                ["balance_sheet", "Balance sheet"], ["cash_flow", "Cash flow"], ["ratios", "Ratios"]];
  const cache = new Map();               // url → { at, d }
  const TTL = 10 * 60_000;
  async function get(url) {
    const hit = cache.get(url);
    if (hit && Date.now() - hit.at < TTL) return hit.d;
    const d = await json(url);
    cache.set(url, { at: Date.now(), d });
    return d;
  }

  function mount(host, ctx) {
    let tab = ctx.cfg.tab || "overview", seq = 0, last = null;
    host.innerHTML =
      `<div class="fin-head">` +
        `<button type="button" class="side-pick" data-fn="sym" title="Choose the company"></button>` +
        `<span class="sh-gap"></span>` +
        `<button type="button" class="sh-btn i" data-fn="sheet" title="Open this table in a sheet">${ic("sheet")}</button>` +
      `</div>` +
      `<div class="fin-tabs" role="tablist">${TABS.map(([k, l]) => `<button type="button" data-tab="${k}">${l}</button>`).join("")}</div>` +
      `<div class="side-body fin-body"></div>`;
    const $ = (s) => host.querySelector(s);
    const body = $(".fin-body");
    const sym = () => ctx.symbol();

    function paintHead() {
      $(".side-pick").innerHTML = `${esc(sym())}${ic("chevronDown", "")}`;
      for (const b of host.querySelectorAll("[data-tab]")) b.classList.toggle("on", b.dataset.tab === tab);
      ctx.setTitle(sym());
      $('[data-fn="sheet"]').hidden = tab === "overview";
    }

    async function paint() {
      paintHead();
      const my = ++seq, s = sym();
      body.innerHTML = skel(9);
      try {
        if (tab === "overview") {
          const [d, q] = await Promise.all([get(`/company?symbol=${encodeURIComponent(s)}`),
            get(`/api/stock/${encodeURIComponent(s)}/quarters`).catch(() => null)]);
          last = { kind: "overview", d, q };
        }
        else if (tab === "quarters") last = { kind: "quarters", d: await get(`/api/stock/${encodeURIComponent(s)}/quarters`) };
        else last = { kind: "statement", d: await get(`/api/financials/${encodeURIComponent(s)}/statement?type=${tab}`) };
        if (my !== seq) return;
        if (last.kind === "overview") qShown = last.q && last.q.available ? (last.q.quarters || []).slice(0, 8).reverse() : [];
        body.innerHTML = last.kind === "overview" ? overview(last.d, last.q) : last.kind === "quarters" ? quarters(last.d) : statement(last.d);
        drawCharts();
      } catch (e) {
        if (my !== seq) return;
        last = null;
        const why = /not in the chart universe|404/.test(e.message) ? `${esc(s)} is not a company the store has filings for.` : esc(e.message);
        body.innerHTML = empty("landmark", why);
      }
    }

    const stat = (k, v, sub) => `<div class="fin-stat"><span>${esc(k)}</span><b>${v}</b>${sub ? `<em>${esc(sub)}</em>` : ""}</div>`;

    /** A labelled bar: the value's share of a scale, coloured by what is
     *  healthy for that measure (`tone` returns good / fair / weak). */
    const gauge = (k, v, max, fmt, tone, note) => {
      if (v == null || !Number.isFinite(+v)) return `<div class="fg"><span>${esc(k)}</span><b>—</b><div class="fg-bar"></div></div>`;
      const w = Math.max(2, Math.min(100, Math.abs(v) / max * 100));
      return `<div class="fg ${tone(v)}"><span>${esc(k)}</span><b>${fmt(v)}</b>` +
        `<div class="fg-bar"><i style="width:${w.toFixed(1)}%"></i></div>${note ? `<em>${esc(note)}</em>` : ""}</div>`;
    };
    const hi = (good, fair) => (v) => v < 0 ? "weak" : v >= good ? "good" : v >= fair ? "fair" : "weak";
    const lo = (good, fair) => (v) => v <= good ? "good" : v <= fair ? "fair" : "weak";

    function overview(d, qd) {
      const p = d.price || {}, r = d.range_52w || {}, v = d.valuation || {};
      const pos = r.high && r.low && p.last != null ? Math.max(0, Math.min(100, (p.last - r.low) / (r.high - r.low) * 100)) : null;
      const offHigh = r.high && p.last != null ? (p.last - r.high) / r.high * 100 : null;
      const q = qd && qd.available ? (qd.quarters || []).slice(0, 8).reverse() : [];
      return `<div class="fin-hero">` +
          `<div><div class="fin-name">${esc(d.long_name || d.name || d.symbol)}</div>` +
          `<div class="fin-sub">${esc([d.sector, d.industry].filter(Boolean).join(" · "))}</div></div>` +
          `<div class="fin-px"><b>${num(p.last)}</b><em class="${dir(p.change_pct)}">${pct(p.change_pct)}</em>` +
          `<span>${esc(p.as_of || "")}</span></div>` +
        `</div>` +
        (pos != null ? `<div class="fin-range"><div class="fr-bar"><i style="left:${pos.toFixed(1)}%"></i></div>` +
          `<div class="fr-row"><span>${num(r.low)}</span><em>52-week range · ${pct(offHigh, 1)} from the high</em><span>${num(r.high)}</span></div></div>` : "") +
        `<div class="fin-grid">` +
          stat("Market cap", inr(d.market_cap)) +
          stat("P/E", v.pe == null ? "—" : num(v.pe, 1), v.basis ? `${v.basis}, ${v.period || ""}` : "") +
          stat("P/B", d.pb == null ? "—" : num(d.pb, 2)) +
          stat("EV / EBITDA", d.ev_ebitda == null ? "—" : num(d.ev_ebitda, 1)) +
          stat("EPS", v.eps == null ? "—" : num(v.eps), v.period || "") +
          stat("Book value / share", d.book_value_ps == null ? "—" : num(d.book_value_ps)) +
        `</div>` +
        (q.length > 1 ? `<h5 class="fin-h">Last ${q.length} quarters<em>${unitName()} · ${esc(qd.basis || "")}</em></h5>` +
          `<div class="fq-wrap" data-q="overview"></div>${key()}` : "") +
        `<h5 class="fin-h">Profitability</h5><div class="fg-list">` +
          gauge("Return on equity", d.roe, 30, (x) => num(x, 1) + "%", hi(15, 8)) +
          gauge("Return on assets", d.roa, 15, (x) => num(x, 1) + "%", hi(6, 2)) +
          gauge("Net margin", d.net_margin, 30, (x) => num(x, 1) + "%", hi(12, 5)) +
        `</div>` +
        `<h5 class="fin-h">Balance sheet</h5><div class="fg-list">` +
          gauge("Debt / equity", d.debt_to_equity, 2, (x) => num(x, 2), lo(0.5, 1), "Lower is less leveraged") +
          gauge("Current ratio", d.current_ratio, 3, (x) => num(x, 2), hi(1.5, 1), "Above 1 covers the next year's bills") +
        `</div>` +
        (d.summary ? `<h5 class="fin-h">About</h5><p class="fin-about">${esc(d.summary.slice(0, 520))}${d.summary.length > 520 ? "…" : ""}</p>` : "") +
        `<div class="fin-foot">${d.website ? `<a href="${esc(d.website)}" target="_blank" rel="noopener">${esc(d.website.replace(/^https?:\/\//, ""))}</a>` : ""}` +
          `${d.ceo ? `<span>CEO ${esc(d.ceo.replace(/\s+/g, " "))}</span>` : ""}` +
          `${d.employees != null ? `<span>${Number(d.employees).toLocaleString("en-IN")} employees</span>` : ""}</div>`;
    }

    // ₹ crore as filed, or ₹ lakh crore for the large companies whose
    // numbers run to seven digits
    const big = () => ctx.cfg.units === "lcr";
    const amt = (v, d) => v == null ? "—" : big() ? num(v / 1e5, 2) : num(v, d);
    const unitName = () => big() ? "₹ lakh crore" : "₹ crore";
    const key = () => `<div class="fq-key"><span><i class="rev"></i>Revenue</span><span><i class="np"></i>Net profit</span>` +
      `<span><i class="mg"></i>Net margin</span></div>`;
    // the quarters the chart is drawn from, oldest first, kept for its tooltip
    let qShown = [];

    /** Revenue and net profit as paired bars, net margin as a line on its own
     *  scale (right). Drawn as SVG at the tile's real width so text stays crisp. */
    function qChart(q, W) {
      const H = 168, L = 6, R = 34, T = 12, B = 22, iw = W - L - R, ih = H - T - B;
      const top = Math.max(...q.map((x) => Math.max(x.revenue || 0, x.net_profit || 0)), 1);
      const bot = Math.min(0, ...q.map((x) => x.net_profit || 0));
      const y = (v) => T + (top - v) / (top - bot) * ih;
      const mg = q.map((x) => x.net_margin_pct).filter((x) => x != null);
      const mTop = Math.max(5, Math.ceil(Math.max(...mg.map(Math.abs), 0) / 5) * 5);
      const ym = (m) => T + (1 - Math.max(0, m) / mTop) * ih;
      const slot = iw / q.length, bw = Math.max(3, Math.min(14, slot * .3));
      const step = Math.ceil(q.length / Math.max(1, Math.floor(iw / 46)));
      let g = "";
      for (const f of [0, .5, 1]) {
        const yy = T + f * ih;
        g += `<line x1="${L}" x2="${L + iw}" y1="${yy}" y2="${yy}" class="fq-grid"/>` +
          `<text x="${W - 2}" y="${yy + 3.5}" class="fq-ax" text-anchor="end">${+(mTop * (1 - f)).toFixed(1)}%</text>`;
      }
      if (bot < 0) g += `<line x1="${L}" x2="${L + iw}" y1="${y(0)}" y2="${y(0)}" class="fq-zero"/>`;
      q.forEach((x, i) => {
        const cx = L + slot * (i + .5), rv = x.revenue || 0, np = x.net_profit || 0;
        g += `<rect x="${cx - bw - 1}" y="${y(Math.max(rv, 0))}" width="${bw}" height="${Math.max(1, Math.abs(y(rv) - y(0)))}" rx="2" class="fq-rev"/>` +
          `<rect x="${cx + 1}" y="${y(Math.max(np, 0))}" width="${bw}" height="${Math.max(1, Math.abs(y(np) - y(0)))}" rx="2" class="fq-np${np < 0 ? " neg" : ""}"/>`;
        if (i % step === (q.length - 1) % step) g += `<text x="${cx}" y="${H - 6}" class="fq-ax" text-anchor="middle">${esc(x.period_label || "")}</text>`;
        g += `<rect x="${cx - slot / 2}" y="${T}" width="${slot}" height="${ih}" class="fq-hit" data-qi="${i}"/>`;
      });
      const pts = q.map((x, i) => x.net_margin_pct == null ? null : [L + slot * (i + .5), ym(x.net_margin_pct)]).filter(Boolean);
      if (pts.length > 1) g += `<polyline points="${pts.map((p_) => p_.map((n) => n.toFixed(1)).join(",")).join(" ")}" class="fq-mg"/>`;
      g += pts.map(([px, py]) => `<circle cx="${px.toFixed(1)}" cy="${py.toFixed(1)}" r="2.6" class="fq-dot"/>`).join("");
      return `<svg class="fq-svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${g}</svg><div class="fq-tip" hidden></div>`;
    }
    function drawCharts() {
      for (const w of body.querySelectorAll(".fq-wrap")) {
        const W = Math.max(220, Math.floor(w.clientWidth));
        w.innerHTML = qShown.length > 1 ? qChart(qShown, W) : "";
      }
    }

    function quarters(d) {
      const q = (d.quarters || []).slice(0, ctx.cfg.quarters || 8).reverse();
      if (!d.available || !q.length) return empty("landmark", `No quarterly results are on file for ${esc(sym())}.`);
      qShown = q;
      const chg = (v) => `<td class="${dir(v)}"><span class="fin-chg">${pct(v, 1)}</span></td>`;
      const rows = [...q].reverse().map((x) => `<tr><td>${esc(x.period_label)}</td><td>${amt(x.revenue, 0)}</td>` +
        `<td>${amt(x.net_profit, 0)}</td><td>${x.net_margin_pct == null ? "—" : num(x.net_margin_pct, 1) + "%"}</td>` +
        chg(x.revenue_yoy_pct) + chg(x.net_profit_yoy_pct) + `<td>${x.eps_basic == null ? "—" : num(x.eps_basic)}</td></tr>`).join("");
      return (ctx.cfg.chart === false ? "" : `<h5 class="fin-h">Revenue, profit and margin<em>${unitName()} · ${esc(d.basis || "")}</em></h5>` +
          `<div class="fq-wrap"></div>${key()}`) +
        `<div class="fin-scroll"><table class="fin-table"><thead><tr><th>Quarter</th><th>Revenue</th><th>Net profit</th><th>Margin</th>` +
        `<th>Revenue YoY</th><th>Profit YoY</th><th>EPS</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    /** A line item's history as a row of small bars, oldest to newest. */
    function spark(vals) {
      const xs = vals.filter((v) => v != null);
      if (xs.length < 2) return "";
      const m = Math.max(...xs.map(Math.abs)) || 1, n = vals.length, w = 58, h = 18, bw = w / n - 1.5;
      const anyNeg = xs.some((v) => v < 0), base = anyNeg ? h / 2 : h;
      return `<svg class="fin-spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" aria-hidden="true">` + vals.map((v, i) => {
        if (v == null) return "";
        const bh = Math.max(1, Math.abs(v) / m * (anyNeg ? h / 2 : h));
        return `<rect x="${(i * (w / n)).toFixed(1)}" y="${(v >= 0 ? base - bh : base).toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="1"` +
          ` class="${v < 0 ? "neg" : i === n - 1 ? "last" : ""}"/>`;
      }).join("") + `</svg>`;
    }
    const KEYROW = /^(total\b|profit\/loss (before|for|after)|net profit|consolidated profit|revenue from operations \[net\]|net cash|basic eps|cash and cash equivalents end)/i;

    function statement(d) {
      if (!d.available || !(d.rows || []).length) return empty("landmark", `No ${esc((TABS.find((t) => t[0] === tab) || [])[1] || "statement")} is on file for ${esc(sym())}.`);
      const periods = (d.periods || []).slice(0, ctx.cfg.years || 5);
      // growth is arithmetic on the two newest filed values — computed here,
      // never estimated — and only where both exist and the base is not zero
      const yoy = ctx.cfg.growth === true && periods.length > 1;
      const trend = ctx.cfg.trend !== false && periods.length > 2;
      const ratios = tab === "ratios";
      const cell = (v) => v == null ? "—" : ratios ? num(v, Math.abs(v) < 100 ? 2 : 0) : amt(v, Math.abs(v) < 100 ? 2 : 0);
      const cols = periods.length + 1 + (yoy ? 1 : 0) + (trend ? 1 : 0);
      let lastSec = null;
      const rows = d.rows.filter((r) => ctx.cfg.hideEmpty !== true || periods.some((p) => r.values && r.values[p] != null)).map((r) => {
        const sec = r.section && r.section !== lastSec ? (lastSec = r.section, `<tr class="sec"><td colspan="${cols}">${esc(r.section)}</td></tr>`) : "";
        const vals = periods.map((p) => r.values && r.values[p]);
        const a = vals[0], b = vals[1];
        const g = yoy && a != null && b != null && b !== 0 ? (a - b) / Math.abs(b) * 100 : null;
        return sec + `<tr${KEYROW.test(r.line_item) ? ` class="key"` : ""}><td>${esc(r.line_item)}</td>` +
          (trend ? `<td class="tr">${spark([...vals].reverse())}</td>` : "") +
          vals.map((v) => `<td class="${v != null && v < 0 ? "neg" : ""}">${cell(v)}</td>`).join("") +
          (yoy ? `<td class="${dir(g)}"><span class="fin-chg">${g == null ? "—" : pct(g, 1)}</span></td>` : "") + `</tr>`;
      }).join("");
      return `<div class="fin-unit">${esc(ratios ? d.unit || "" : big() ? unitName() : d.unit || "")} · ${esc(d.basis || "")}</div>` +
        `<div class="fin-scroll"><table class="fin-table st"><thead><tr><th></th>${trend ? `<th class="tr">Trend</th>` : ""}${periods.map((p) => `<th>${esc(p)}</th>`).join("")}` +
        (yoy ? `<th title="Change from ${esc(periods[1])} to ${esc(periods[0])}">YoY</th>` : "") +
        `</tr></thead><tbody>${rows}</tbody></table></div>`;
    }

    function toSheet() {
      if (!last || last.kind === "overview") return;
      const d = last.d;
      let table;
      if (last.kind === "quarters") {
        const q = d.quarters || [];
        table = { title: `${sym()} quarterly`, columns: ["Quarter", "Revenue (₹ cr)", "Net profit (₹ cr)", "Net margin %", "Revenue YoY %", "Net profit YoY %"],
                  rows: q.map((x) => [x.period_label, x.revenue, x.net_profit, x.net_margin_pct, x.revenue_yoy_pct, x.net_profit_yoy_pct]) };
      } else {
        const periods = d.periods || [];
        table = { title: `${sym()} ${(TABS.find((t) => t[0] === tab) || [])[1]}`, columns: [`Line item (${d.unit || ""})`, ...periods],
                  rows: d.rows.map((r) => [r.line_item, ...periods.map((p) => (r.values || {})[p] ?? "")]) };
      }
      ctx.send("sheet", { table });
    }

    body.addEventListener("pointermove", (e) => {
      const hit = e.target.closest(".fq-hit");
      const wrap = e.target.closest(".fq-wrap");
      const tip = wrap && wrap.querySelector(".fq-tip");
      for (const t of body.querySelectorAll(".fq-tip")) if (t !== tip) t.hidden = true;
      if (!tip) return;
      if (!hit) { tip.hidden = true; return; }
      const x = qShown[+hit.dataset.qi];
      tip.innerHTML = `<b>${esc(x.period_label)}</b>` +
        `<span><i class="rev"></i>Revenue<em>${amt(x.revenue, 0)}</em></span>` +
        `<span><i class="np"></i>Net profit<em>${amt(x.net_profit, 0)}</em></span>` +
        `<span><i class="mg"></i>Net margin<em>${x.net_margin_pct == null ? "—" : num(x.net_margin_pct, 1) + "%"}</em></span>` +
        (x.revenue_yoy_pct != null ? `<span>Revenue YoY<em class="${dir(x.revenue_yoy_pct)}">${pct(x.revenue_yoy_pct, 1)}</em></span>` : "");
      tip.hidden = false;
      const wr = wrap.getBoundingClientRect(), hr = hit.getBoundingClientRect();
      const left = hr.left - wr.left + hr.width / 2, tw = tip.offsetWidth;
      tip.style.left = Math.max(0, Math.min(wr.width - tw, left - tw / 2)) + "px";
    });
    body.addEventListener("pointerleave", () => { for (const t of body.querySelectorAll(".fq-tip")) t.hidden = true; });
    let rzW = 0, rzT = 0;
    new ResizeObserver(() => {
      const w = body.clientWidth;
      if (!w || Math.abs(w - rzW) < 6) return;
      rzW = w; clearTimeout(rzT); rzT = setTimeout(drawCharts, 80);
    }).observe(body);

    host.addEventListener("click", (e) => {
      const t = e.target.closest("[data-tab]");
      if (t) { tab = t.dataset.tab; ctx.setCfg({ tab }); return paint(); }
      const a = e.target.closest("[data-fn]");
      if (!a) return;
      e.stopPropagation();
      if (a.dataset.fn === "sym") {
        return setTimeout(() => Universe.open({ anchor: a, current: sym(), onPick: (s) => ctx.setCfg({ pin: s, link: "pin" }) }), 0);
      }
      if (a.dataset.fn === "sheet") return toSheet();
    });

    return {
      show: paint,
      config(cfg, patch) { if (Object.keys(patch).some((k) => k !== "title")) paint(); },
      ask: () => `Walk me through ${sym()}'s financials: growth, margins, balance sheet strength and valuation, with the numbers. This is analysis, not advice.`,
    };
  }

  Dock.register({
    type: "financials", title: "Financials", icon: "landmark", hue: "copper", group: "Research",
    desc: "Valuation, quarterly results and the filed statements", zone: "right", minW: 300, mount,
    linkable: true,
    settings: [
      { section: "Statements" },
      { key: "years", label: "Years shown", def: 5, options: [{ v: 3, label: "3" }, { v: 5, label: "5" }, { v: 8, label: "8" }, { v: 12, label: "12" }] },
      { key: "growth", label: "Growth column", kind: "toggle", def: false, hint: "Latest year against the one before" },
      { key: "hideEmpty", label: "Hide empty lines", kind: "toggle", def: false },
      { key: "trend", label: "Trend bars beside each line", kind: "toggle", def: true },
      { section: "Quarterly" },
      { key: "quarters", label: "Quarters shown", def: 8, options: [{ v: 4, label: "4" }, { v: 8, label: "8" }, { v: 12, label: "12" }] },
      { key: "chart", label: "Revenue, profit and margin chart", kind: "toggle", def: true },
      { section: "Numbers" },
      { key: "units", label: "Amounts in", def: "cr", options: [{ v: "cr", label: "₹ crore" }, { v: "lcr", label: "₹ lakh crore" }] },
    ],
  });
})();
