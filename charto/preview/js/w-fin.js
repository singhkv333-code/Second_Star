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
        if (tab === "overview") last = { kind: "overview", d: await get(`/company?symbol=${encodeURIComponent(s)}`) };
        else if (tab === "quarters") last = { kind: "quarters", d: await get(`/api/stock/${encodeURIComponent(s)}/quarters`) };
        else last = { kind: "statement", d: await get(`/api/financials/${encodeURIComponent(s)}/statement?type=${tab}`) };
        if (my !== seq) return;
        body.innerHTML = last.kind === "overview" ? overview(last.d) : last.kind === "quarters" ? quarters(last.d) : statement(last.d);
      } catch (e) {
        if (my !== seq) return;
        last = null;
        const why = /not in the chart universe|404/.test(e.message) ? `${esc(s)} is not a company the store has filings for.` : esc(e.message);
        body.innerHTML = empty("landmark", why);
      }
    }

    const stat = (k, v, sub) => `<div class="fin-stat"><span>${esc(k)}</span><b>${v}</b>${sub ? `<em>${esc(sub)}</em>` : ""}</div>`;

    function overview(d) {
      const p = d.price || {}, r = d.range_52w || {}, v = d.valuation || {};
      const pos = r.high && r.low && p.last != null ? (p.last - r.low) / (r.high - r.low) * 100 : null;
      return `<div class="fin-hero">` +
          `<div><div class="fin-name">${esc(d.long_name || d.name || d.symbol)}</div>` +
          `<div class="fin-sub">${esc([d.sector, d.industry].filter(Boolean).join(" · "))}</div></div>` +
          `<div class="fin-px"><b>${num(p.last)}</b><em class="${dir(p.change_pct)}">${pct(p.change_pct)}</em>` +
          `<span>${esc(p.as_of || "")}</span></div>` +
        `</div>` +
        (pos != null ? `<div class="fin-range"><span>${num(r.low)}</span><div class="bar"><i style="left:${pos.toFixed(1)}%"></i></div>` +
          `<span>${num(r.high)}</span><em>52-week range</em></div>` : "") +
        `<div class="fin-grid">` +
          stat("Market cap", inr(d.market_cap)) +
          stat("P/E", v.pe == null ? "—" : num(v.pe, 1), v.basis ? `${v.basis}, ${v.period || ""}` : "") +
          stat("EPS", v.eps == null ? "—" : num(v.eps), v.period || "") +
          stat("P/B", d.pb == null ? "—" : num(d.pb, 2)) +
          stat("ROE", d.roe == null ? "—" : num(d.roe, 1) + "%") +
          stat("ROA", d.roa == null ? "—" : num(d.roa, 1) + "%") +
          stat("Net margin", d.net_margin == null ? "—" : num(d.net_margin, 1) + "%") +
          stat("Debt / equity", d.debt_to_equity == null ? "—" : num(d.debt_to_equity, 2)) +
          stat("Current ratio", d.current_ratio == null ? "—" : num(d.current_ratio, 2)) +
          stat("EV / EBITDA", d.ev_ebitda == null ? "—" : num(d.ev_ebitda, 1)) +
          stat("Book value / share", d.book_value_ps == null ? "—" : num(d.book_value_ps)) +
          stat("Employees", d.employees == null ? "—" : Number(d.employees).toLocaleString("en-IN")) +
        `</div>` +
        (d.summary ? `<p class="fin-about">${esc(d.summary.slice(0, 520))}${d.summary.length > 520 ? "…" : ""}</p>` : "") +
        `<div class="fin-foot">${d.website ? `<a href="${esc(d.website)}" target="_blank" rel="noopener">${esc(d.website.replace(/^https?:\/\//, ""))}</a>` : ""}` +
          `${d.ceo ? `<span>CEO ${esc(d.ceo.replace(/\s+/g, " "))}</span>` : ""}</div>`;
    }

    function quarters(d) {
      const q = (d.quarters || []).slice(0, 8).reverse();
      if (!d.available || !q.length) return empty("landmark", `No quarterly results are on file for ${esc(sym())}.`);
      const max = Math.max(...q.map((x) => Math.abs(x.revenue || 0)), 1);
      const bars = q.map((x) => {
        const h = Math.abs(x.revenue || 0) / max * 100, hp = Math.abs(x.net_profit || 0) / max * 100;
        return `<div class="fq"><div class="fq-bars"><i style="height:${h.toFixed(1)}%"></i>` +
          `<i class="np${(x.net_profit || 0) < 0 ? " neg" : ""}" style="height:${hp.toFixed(1)}%"></i></div>` +
          `<span>${esc(x.period_label || "")}</span></div>`;
      }).join("");
      const rows = [...q].reverse().map((x) => `<tr><td>${esc(x.period_label)}</td><td>${num(x.revenue, 0)}</td>` +
        `<td>${num(x.net_profit, 0)}</td><td>${x.net_margin_pct == null ? "—" : num(x.net_margin_pct, 1) + "%"}</td>` +
        `<td class="${dir(x.revenue_yoy_pct)}">${pct(x.revenue_yoy_pct, 1)}</td><td class="${dir(x.net_profit_yoy_pct)}">${pct(x.net_profit_yoy_pct, 1)}</td></tr>`).join("");
      return `<div class="fq-chart">${bars}</div>` +
        `<div class="fq-key"><span><i></i>Revenue</span><span><i class="np"></i>Net profit</span><em>₹ crore · ${esc(d.basis || "")}</em></div>` +
        `<table class="fin-table"><thead><tr><th>Quarter</th><th>Revenue</th><th>Net profit</th><th>Margin</th><th>Rev YoY</th><th>NP YoY</th></tr></thead>` +
        `<tbody>${rows}</tbody></table>`;
    }

    function statement(d) {
      if (!d.available || !(d.rows || []).length) return empty("landmark", `No ${esc((TABS.find((t) => t[0] === tab) || [])[1] || "statement")} is on file for ${esc(sym())}.`);
      const periods = (d.periods || []).slice(0, ctx.cfg.years || 5);
      let lastSec = null;
      const rows = d.rows.map((r) => {
        const sec = r.section && r.section !== lastSec ? (lastSec = r.section, `<tr class="sec"><td colspan="${periods.length + 1}">${esc(r.section)}</td></tr>`) : "";
        return sec + `<tr><td>${esc(r.line_item)}</td>${periods.map((p) => `<td>${r.values && r.values[p] != null ? num(r.values[p], Math.abs(r.values[p]) < 100 ? 2 : 0) : "—"}</td>`).join("")}</tr>`;
      }).join("");
      return `<div class="fin-unit">${esc(d.unit || "")} · ${esc(d.basis || "")}</div>` +
        `<table class="fin-table st"><thead><tr><th></th>${periods.map((p) => `<th>${esc(p)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table>`;
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

    host.addEventListener("click", (e) => {
      const t = e.target.closest("[data-tab]");
      if (t) { tab = t.dataset.tab; ctx.setCfg({ tab }); return paint(); }
      const a = e.target.closest("[data-fn]");
      if (!a) return;
      e.stopPropagation();
      if (a.dataset.fn === "sym") {
        return ctx.menu(a, [{ head: "Company" },
          { id: "follow", label: `Follow the chart (${ctx.pageSymbol()})`, icon: "link", on: !ctx.cfg.pin },
          { id: "pin", label: "Pin another company…", icon: "pin", on: !!ctx.cfg.pin }],
          (p) => p === "follow" ? ctx.setCfg({ pin: null })
            : setTimeout(() => Universe.open({ anchor: a, onPick: (s) => ctx.setCfg({ pin: s }) }), 0));
      }
      if (a.dataset.fn === "sheet") return toSheet();
    });

    return {
      show: paint,
      config(cfg, patch) { if ("pin" in patch || "years" in patch) paint(); },
      ask: () => `Walk me through ${sym()}'s financials: growth, margins, balance sheet strength and valuation, with the numbers. This is analysis, not advice.`,
    };
  }

  Dock.register({
    type: "financials", title: "Financials", icon: "landmark", hue: "copper", group: "Research",
    desc: "Valuation, quarterly results and the filed statements", zone: "right", minW: 300, mount,
    linkable: false,
    settings: [{ key: "years", label: "Years shown", def: 5, options: [{ v: 5, label: "5" }, { v: 8, label: "8" }, { v: 12, label: "12" }] }],
  });
})();
