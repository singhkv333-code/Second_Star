/* Charto preview — the panels a tool prints beside its reply.
 *
 * A reply is prose. Some answers are not: a full pattern sweep comes back as
 * a market-structure read, several formations each with a status and one
 * measurement, and a dozen candlestick bars. Written out as sentences that is
 * a transcript rather than an answer — and left out, the reply has quietly
 * dropped the measurements the question asked for. The card is the third
 * option: the tool prints what it MEASURED, and the paragraph beside it is
 * free to say what that means.
 *
 * ── the rule this module lives under ────────────────────────────────────
 * It renders. It does not decide. Every string and number here came off the
 * `card` object the dataserver built from its own tool result (see
 * `_patterns_card`), so the panel and the prose are reading one source and
 * cannot state two different figures. Nothing is computed on this side —
 * not a count, not a total, not a "so that means". The two things it is
 * allowed to do to a value are FORMAT it (a price into the symbol's
 * currency, a timestamp into the app's short form) and DROP it: a missing
 * measurement takes its whole row with it rather than rendering as "NaN" or
 * "undefined · confirmed", exactly as the provenance card does.
 *
 * ── why the rows point back at the chart ────────────────────────────────
 * Every drawn object already has a scene id, and hovering a mention of one in
 * the thread already lights it up on the candles (main.js `indexChatRefs`).
 * A card row is a mention with better manners, so it carries the same
 * `data-ann` handle and gets that link for free. A row with no handle was
 * never drawn — and says so by being quieter, not by pretending.
 */
"use strict";

const Cards = (() => {
  // Same origin rule the rest of the app uses: served from the dataserver in
  // production, cross-origin only when the preview folder is on its own port.
  const API = ["localhost", "127.0.0.1"].includes(location.hostname)
    ? "http://127.0.0.1:5174" : "";
  const esc = (s) => String(s == null ? "" : s)
    .replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
                                  '"': "&quot;" }[c]));

  /* The dataserver stamps every time as "24 Jul 2026 15:15" — that format is
   * the contract (`_ist` / `_parse_ist`), so it is parsed rather than guessed
   * at, and never re-derived from an epoch on this side: the bars sit on the
   * exchange's clock and the browser does not. */
  const STAMP = /^(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})(?:\s+(\d{1,2}:\d{2}))?$/;
  const parse = (s) => STAMP.exec(String(s || "").trim());

  /** "24 Jul 2026 15:15" → "24 Jul 15:15". The year is the one field a reader
   *  of a chart they are looking at already knows. */
  function when(s) {
    const m = parse(s);
    if (!m) return String(s || "");
    return `${m[1]} ${m[2]}${m[4] ? " " + m[4] : ""}`;
  }

  /** A formation's window, closed up as far as the two ends allow:
   *  "16–28 Jul" inside one month, "28 Jun – 3 Jul" across two, and
   *  "28 Jul 09:15 → 15:15" when the whole thing lived inside one session. */
  function span(from, to) {
    const a = parse(from), b = parse(to);
    if (!a || !b) return [when(from), when(to)].filter(Boolean).join(" → ");
    if (a[1] === b[1] && a[2] === b[2] && a[3] === b[3]) {
      return a[4] && b[4] ? `${a[1]} ${a[2]} ${a[4]} → ${b[4]}` : `${a[1]} ${a[2]}`;
    }
    if (a[2] === b[2] && a[3] === b[3]) return `${a[1]}–${b[1]} ${a[2]}`;
    return `${a[1]} ${a[2]} – ${b[1]} ${b[2]}`;
  }

  /** Prices in the instrument's own currency and grouping — a card about a
   *  crypto pane must not print ₹, and must not group six figures the Indian
   *  way. Sym.of is the one place in the app that knows which. */
  function money(sym, v) {
    if (v == null || !Number.isFinite(Number(v))) return "";
    return Sym.of(sym).price(Number(v),
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }

  const TONE = { up: "up", down: "down", bullish: "up", bearish: "down" };
  const cap = (s) => String(s || "").charAt(0).toUpperCase() + String(s || "").slice(1);

  /* A status is a BADGE, and this map holds only the two that are SETTLED.
   * "Confirmed" is a level that broke — a fact with a date behind it, in the
   * shape's own favour. "Broken" is the same event read the other way: a
   * trendline price has closed through is not a line any more, and saying so
   * in red is the one caution this panel raises.
   *
   * Everything else is a shape still open, and what that should look like
   * depends on the shape, so each renderer supplies its own fallback: an
   * unresolved formation takes the annotation amber (not the candle red — an
   * unresolved wedge is not a bearish wedge), while a trendline that is
   * simply intact takes the plain grey, because still holding is the
   * unremarkable state of a line rather than a caveat about it. */
  const BADGE = { confirmed: "ok", broken: "bad" };

  /** One row of a list: when it happened, what it was, and — where there is
   *  one — the price it happened at, closed against the card's right edge so
   *  every figure in the panel stacks into one column. */
  function row(cls, ann, left, mid, right) {
    return `<div class="scan-row${cls ? " " + cls : ""}"`
      + (ann ? ` data-ann="${esc(ann)}"` : "")
      + `><span class="when">${esc(left)}</span>`
      + `<span class="what">${mid}</span>`
      + (right ? `<b class="num">${esc(right)}</b>` : "")
      + `</div>`;
  }

  /** A section, or nothing at all. An empty heading over an empty list is a
   *  statement that something is missing; a section that simply isn't there
   *  is the same statement without the furniture. */
  function section(title, note, body) {
    if (!body) return "";
    return `<section class="scan-sec"><h4>${esc(title)}`
      + (note ? `<span class="n">${esc(note)}</span>` : "")
      + `</h4>${body}</section>`;
  }

  /* ── the builder's cards ───────────────────────────────────────────────
   *
   * Ported from Pivot's own chat widgets (WorkflowDraftCard.tsx,
   * IndicatorBacktestCard.tsx) rather than re-imagined, because the shape of
   * those cards is an argument that was already had: the "Agent" chip and
   * Draft dot say what state this is in, the reasoning hides behind "Why
   * this?" so the steps stay the hero, the CTA rail is ONE primary pill with
   * ghost actions beneath it instead of a three-button grid, and warnings sit
   * above the buttons where they are read before a decision rather than
   * after it.
   *
   * Not a transliteration. Pivot's are React with hooks and Tailwind; this
   * folder has no build step and `Cards.render` returns a string. So the
   * structure and the behaviour come across, and the mechanics are Charto's.
   *
   * Neither card computes anything. Every figure printed is the payload's
   * own — a card that recomputed a metric could disagree with the engine that
   * produced it, and the disagreement would be invisible.
   */

  const STEP_ICON = {
    trigger: "bell", fetch: "search", condition: "opCross",
    action: "position", notify: "chat", control: "chevronRight",
  };

  /** Last line of defence against a dev-string leak, mirroring Pivot's
   *  `isLeakedLabel` — the backend backfills friendly labels, but a raw
   *  `action.place_order` reaching the card must never be shown as one. */
  function stepLabel(step) {
    const raw = String(step.label || "").trim();
    const type = String(step.step_type || "");
    if (raw && raw !== type && !/^[a-z]+\.[a-z_]+$/.test(raw)) return raw;
    const tail = type.includes(".") ? type.slice(type.indexOf(".") + 1) : type;
    const words = tail.replace(/[._]+/g, " ").trim();
    return words ? words.charAt(0).toUpperCase() + words.slice(1) : type;
  }

  /** One step. A compound trigger's config is a DSL tree; the backend
   *  travels the English sentence for it alongside (`readback`), because
   *  asking a reader to audit a parse tree to find out what their own
   *  strategy says is not showing them their strategy. Steps without a
   *  sentence show the config fields that carry meaning — a place_order's
   *  side and size — and not the ones that are plumbing. */
  const STEP_NOISE = new Set(["entry", "exchange", "requires_approval",
                              "target_symbol", "timezone"]);
  function draftStep(s, i) {
    const cfg = s.config || {};
    const icon = STEP_ICON[String(s.step_type || "").split(".")[0]] || "info";
    const detail = s.readback
      ? `<p class="wf-readback">${esc(s.readback)}</p>`
      : (() => {
          const rows = Object.keys(cfg)
            .filter((k) => !STEP_NOISE.has(k) && cfg[k] != null && cfg[k] !== "")
            .map((k) => `<span class="wf-kv"><i>${esc(k)}</i>${esc(
              typeof cfg[k] === "object" ? JSON.stringify(cfg[k]) : cfg[k])}</span>`);
          return rows.length ? `<div class="wf-cfg">${rows.join("")}</div>` : "";
        })();
    return `<li class="wf-step" style="animation-delay:${i * 45}ms">`
      + `<span class="wf-ico">${Icons.svg(icon, "sm")}</span>`
      + `<div class="wf-body"><b class="wf-label">${esc(stepLabel(s))}</b>`
      + detail + `</div></li>`;
  }

  /* Pivot shows five and counts the rest. The cap is the same here for the
   * same reason: past five the list stops being a shape you can take in and
   * becomes a thing you scroll, and the card is meant to be glanced at. */
  const MAX_VISIBLE_STEPS = 5;

  function workflowDraft(c) {
    const steps = c.steps || [];
    const shown = steps.slice(0, MAX_VISIBLE_STEPS);
    const hidden = steps.length - shown.length;
    const list = steps.length
      ? `<ol class="wf-steps">${shown.map(draftStep).join("")}`
        + (hidden > 0 ? `<li class="wf-more">+${hidden} more step`
            + `${hidden > 1 ? "s" : ""}</li>` : "")
        + `</ol>`
      : "";
    // Warnings are the honest half of this card — a trailing exit modelled in
    // backtest but registering only its initial stop live is exactly the
    // thing to read BEFORE arming, so it sits above the buttons.
    const warn = (c.warnings || []).concat(c.live_warnings || []);
    const blockers = c.backtestable === false ? (c.backtest_blockers || []) : [];
    const warnBlock = warn.length
      ? `<ul class="wf-warn">${warn.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>`
      : "";
    const why = c.rationale
      ? `<button type="button" class="wf-why" data-wf-why aria-expanded="false">`
        + `${Icons.svg("info", "sm")}<span>Why this?</span></button>`
        + `<p class="wf-why-body" hidden>${esc(c.rationale)}</p>`
      : "";
    const ttl = c.valid_until
      ? `<span class="wf-chip-ttl">until ${esc(c.valid_until)}</span>` : "";
    // The draft travels ON the card so the Backtest button can post the
    // exact steps the user is looking at, rather than the FE rebuilding a
    // request that could drift from what is rendered.
    const payload = attrJSON({ name: c.name, steps: steps });
    return `<div class="wf-card" data-wf data-draft="${payload}">`
      + `<div class="wf-top"><span class="wf-chip">Agent</span>`
      + `<span class="wf-state">${ttl}`
      + `<span class="wf-dot" aria-hidden="true"></span>Draft</span></div>`
      + `<h3 class="wf-title">${esc(c.name || "Strategy draft")}</h3>`
      + (c.description ? `<p class="wf-desc">${esc(c.description)}</p>` : "")
      + why
      + list
      + warnBlock
      + (blockers.length
          ? `<p class="wf-note">${esc(blockers[0])}</p>` : "")
      + `<div class="wf-cta">`
      + `<button type="button" class="wf-primary" data-wf-activate`
      + ` title="Connect a Pivot account to arm agents from Charto">`
      + `Save &amp; activate</button>`
      + `<div class="wf-ghosts">`
      + (blockers.length ? ""
          : `<button type="button" class="wf-ghost" data-wf-backtest>`
            + `${Icons.svg("clock", "sm")}<span>Backtest</span></button>`)
      + `</div></div>`
      + `<div class="wf-slot" data-wf-slot hidden></div></div>`;
  }

  /* ── the strategy layer on the chart ──────────────────────────────
   *
   * A backtest answers in two places. The card answers "was it any good"; the
   * CHART answers "what did it actually do" — and that second question is one
   * no table can take, because its answers are shapes: how long trades ran,
   * whether the wins were the long ones, whether the strategy was in the
   * market at all between them.
   *
   * Everything here is derived from the payload's own trade list. The card
   * computes GEOMETRY — a bar time, a pixel span — and never a statistic.
   */
  const SCENE_OWNER = "backtest", SCENE_PREFIX = "bt:";
  const IST = 19800;

  /** The loaded chart's bars, or an empty list when there is no chart. */
  const chartBars = () =>
    (window.__charto && window.__charto.state && window.__charto.state.bars) || [];

  /* An ISO date onto the bar it belongs to.
   *
   * A trade date is a SESSION, not an instant, so it cannot be handed to the
   * chart as an epoch and hoped for: on a 5-minute chart "2023-09-11" is 75
   * bars, and on a daily chart it is a bar stamped at whatever hour the feed
   * chose. Both resolve here, by searching the bars actually loaded — so the
   * mark lands on a bar that exists rather than at a clock reading that may
   * fall in a gap, a holiday or the middle of the night.
   *
   * Bars are held in CHART time (main.js adds IST); annotations are handed
   * back in real time, because the scene layer adds it again on the way out.
   */
  function barFor(iso) {
    const bars = chartBars();
    if (!bars.length || !iso) return null;
    const day = Date.parse(String(iso).slice(0, 10) + "T00:00:00Z");
    if (!Number.isFinite(day)) return null;
    // Chart-time midnight, and NOT the date plus IST: a daily bar is already
    // keyed at chart-time midnight of its own session (the 27th's bar sits at
    // 2022-09-27T00:00Z in chart time), so offsetting the target as well
    // stepped every search one session late. Intraday resolves off the same
    // line — the first bar after that day's chart-time midnight is 09:15.
    const want = day / 1000;
    if (bars[bars.length - 1].time < want) return null;
    if (bars[0].time >= want) return bars[0];
    let lo = 0, hi = bars.length - 1;
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1;
      if (bars[mid].time < want) lo = mid; else hi = mid;
    }
    return bars[hi];
  }

  /* Bars are held in CHART time (main.js adds IST); annotations are handed
   * back in real time, because the scene layer adds it again on the way out. */
  const sceneT = (bar) => bar.time - IST;

  /* Trades → scene annotations. One per trade, plus the rail.
   *
   * The two price series do NOT agree, and pretending otherwise is the whole
   * hazard here. Pivot back-adjusts for dividends; Charto's own store does
   * not, so the same RELIANCE entry is ₹1087.82 to the engine and ₹1141 to
   * this chart — a ladder that steps at every ex-date and closes to nothing
   * on the most recent trade. Drawn at the engine's price, the object would
   * float five percent clear of the candle it claims to be an entry on, and
   * nothing on screen would tell the user which number was wrong.
   *
   * So each trade takes ONE anchor from the chart and ONE magnitude from the
   * engine: the entry sits on this chart's own bar, and the exit is that
   * anchor carried by the engine's own return. The object then lands on the
   * candles it is about, its height is exactly the return the card prints,
   * and the two can never disagree — because only one of them is measured
   * here. No price is invented: both ends are a real bar or a real result.
   */
  function strategyItems(c) {
    const items = [], spans = [];
    const bars = chartBars();
    const lastBar = bars.length ? bars[bars.length - 1] : null;
    (c.trades || []).forEach((t, i) => {
      const inBar = barFor(t.entry_date);
      // A trade still open at the end of the window closes at the last bar —
      // that is where the money still is, and dropping it would quietly
      // under-report how long the strategy holds.
      const outBar = t.exit_date ? barFor(t.exit_date) : lastBar;
      if (!inBar || !outBar) return;
      const ep = Number(inBar.open);
      const r = Number(t.return_pct);
      if (!Number.isFinite(ep)) return;
      // return_pct is the engine's PRICE return (net_pnl is the one after
      // costs), so carrying the anchor by it lands where the exit bar sits
      // on this chart's scale rather than on the engine's.
      const xp = Number.isFinite(r) ? ep * (1 + r) : ep;
      const t0 = sceneT(inBar), end = Math.max(sceneT(outBar), t0);
      items.push({
        id: `${SCENE_PREFIX}t${i}`, kind: "trade", pane: "price",
        owner: SCENE_OWNER,
        entry: { t: t0, v: ep }, exit: { t: end, v: xp },
        // The engine's own sign, not a re-derivation from the two prices:
        // net_pnl is after costs, and a trade that gained 0.2% and paid 0.3%
        // in brokerage is a LOSS however the prices look.
        win: Number(t.net_pnl) > 0,
        text: Number.isFinite(r)
          ? `${r >= 0 ? "+" : "\u2212"}${Math.abs(r * 100).toFixed(2)}%` : "",
      });
      spans.push([t0, end]);
    });
    if (spans.length) {
      items.push({ id: `${SCENE_PREFIX}rail`, kind: "exposure", pane: "price",
                   owner: SCENE_OWNER, spans });
    }
    return items;
  }

  /** Take the strategy layer off the chart, and nothing else with it. */
  const clearStrategy = () => ({
    kind: "clear", scope: "id_prefix", prefix: SCENE_PREFIX,
    owner: SCENE_OWNER,
  });

  /* Why the button can be inert.
   *
   * Drawing INFY's trades over a RELIANCE chart would be a fabrication the
   * user has no way to catch — the shapes look exactly as convincing on the
   * wrong instrument. So the symbol has to agree, and when it does not the
   * button says which one to switch to rather than going quiet.
   */
  function strategyBlocker(c) {
    if (!window.__charto || !window.__charto.scene) return "No chart loaded.";
    if (!(c.trades || []).length) return "This backtest took no trades.";
    const want = String(c.symbol || "").toUpperCase();
    const have = String(window.__charto.symbol || "").toUpperCase();
    if (want && have && want !== have) return `Open ${want} to see these trades.`;
    if (!chartBars().length) return "No bars loaded.";
    return "";
  }

  /** A backtest read-out.
   *
   * The verdict leads because it is the only line that says whether the
   * return under it means anything — a 40% CAGR on nine trades and a 60% CAGR
   * on nine hundred are not the same claim, and the verdict is the engine's
   * own answer to which one this is.
   *
   * Everything below it is arranged as an argument rather than a dump: the
   * headline figures, then the one comparison that decides whether the
   * strategy was worth running at all (it versus simply holding), then the
   * curve, then whether the result came from the whole window or one lucky
   * stretch, then what the resampling says the drawdown could have been.
   *
   * Every series and every figure is the payload's own. The charts derive
   * GEOMETRY from those series — a pixel path, a bar width — and never a
   * statistic: nothing here prints a number the engine did not compute.
   */
  function strategyBacktest(c) {
    const m = c.metrics || {};
    const v = m.trust_verdict || {};
    const fs = m.forward_stats || {};
    const mc = m.monte_carlo || {};
    const sp = m.sub_periods || {};
    const sym = c.symbol || "";
    const fin = (x) => x != null && Number.isFinite(Number(x));
    const num = (x, d) => (fin(x) ? n2(sym, x) + (d || "") : "—");
    const pct = (x) => (fin(x) ? signed(sym, x, "%") : "—");
    // A drawdown has no direction to report — it is a depth. The engine hands
    // it over as a magnitude, so signing it printed "+1.89%", which reads as
    // a gain in the one field that can only ever be a loss.
    const depth = (x) => (fin(x) ? `−${n2(sym, Math.abs(Number(x)))}%` : "—");

    // `label` is a written sentence fragment; `verdict` is the raw enum. Only
    // the fallback needs capitalising, and the CSS no longer does it for both.
    const verdict = v.label || cap(String(v.verdict || "").replace(/_/g, " "));
    const verdictBlock = verdict
      ? `<div class="wf-verdict" data-verdict="${esc(String(v.verdict || ""))}">`
        + `<b>${esc(verdict)}</b>`
        + (v.rationale ? `<span>${esc(v.rationale)}</span>` : "")
        + `</div>` : "";

    const strip = stat("Return", pct(m.total_return_pct), way(m.total_return_pct))
      + stat("CAGR", fin(m.cagr_pct) ? pct(m.cagr_pct) : "—", way(m.cagr_pct))
      + stat("Max drawdown", depth(m.max_drawdown_pct), "down")
      + stat("Hit rate", fin(m.hit_rate_pct) ? num(m.hit_rate_pct, "%") : "—",
             "", m.n_wins == null ? "" : `${esc(m.n_wins)} won`)
      + stat("Sharpe", num(m.sharpe), "",
             fin(fs.deflated_sharpe) ? `deflated ${num(fs.deflated_sharpe)}` : "")
      + stat("Trades", m.n_trades ?? "—", "");

    // The comparison that decides whether any of this was worth doing. Two
    // figures the payload already carries, drawn against one shared scale so
    // the gap is a length rather than a subtraction the reader performs.
    const bench = m.benchmark_return_pct != null
      ? m.benchmark_return_pct : c.bench_buy_hold_return_pct;
    const versus = (fin(m.total_return_pct) && fin(bench))
      ? section("Versus holding", "", bars([
          { label: "Strategy", value: m.total_return_pct,
            text: pct(m.total_return_pct), tone: way(m.total_return_pct) },
          { label: "Buy & hold", value: bench, text: pct(bench), tone: way(bench) },
        ], { signed: true }))
      : "";

    const curve = equityChart(c.equity_curve || [], c.signals || []);

    // Did the edge show up across the window, or in one stretch? A single
    // total cannot answer that and this list can — it is the engine's own
    // per-period split, drawn as columns off a zero line.
    const periods = (sp.period_returns_pct || []);
    const spread = periods.length > 1
      ? section("Period by period",
                fin(sp.positive_period_frac)
                  ? `${Math.round(sp.positive_period_frac * 100)}% positive` : "",
                columns(periods, sym))
      : "";

    // Resampling says what the equity curve alone cannot: this path was one
    // draw, and these are the drawdowns the same edge produced on others.
    const mcRows = [];
    if (fin(mc.dd_median_pct)) mcRows.push({
      label: "Typical", value: Math.abs(mc.dd_median_pct),
      text: depth(mc.dd_median_pct), tone: "down" });
    if (fin(mc.dd_worst_pct)) mcRows.push({
      label: "Worst", value: Math.abs(mc.dd_worst_pct),
      text: depth(mc.dd_worst_pct), tone: "down" });
    const mcBlock = mcRows.length
      ? section("Drawdown across resamples",
                mc.n_sims ? `${mc.n_sims} runs` : "", bars(mcRows))
      : "";

    // The rigor rows only appear when the engine actually computed them — an
    // empty "Monte-Carlo: —" implies a test that ran and said nothing.
    const rigor = [
      fin(fs.psr) ? ["Probabilistic Sharpe", num(fs.psr)] : null,
      fin(fs.min_trl) ? ["Min track record", `${num(fs.min_trl)} obs`] : null,
      fs.n_obs ? ["Observations", String(fs.n_obs)] : null,
      (fs.num_trials && fs.num_trials > 1)
        ? ["Deflated for trials", String(fs.num_trials)] : null,
      fin(mc.prob_loss) ? ["Chance of a loss", `${Math.round(mc.prob_loss * 100)}%`] : null,
      fin(sp.concentration) ? ["Return concentration", num(sp.concentration)] : null,
    ].filter(Boolean);
    const rigorBlock = rigor.length
      ? section("How much to trust it", "",
          `<dl class="wf-rigor">${rigor.map(([k, val]) =>
            `<div><dt>${esc(k)}</dt><dd>${esc(val)}</dd></div>`).join("")}</dl>`)
      : "";

    const assume = (c.assumptions || []).length
      ? `<ul class="wf-warn">${c.assumptions.map((a) =>
          `<li>${esc(a)}</li>`).join("")}</ul>` : "";

    return `<div class="wf-card">`
      + `<div class="wf-top"><span class="wf-chip wf-chip-sim">Simulation</span>`
      + (c.period_label ? `<span class="wf-state">${esc(c.period_label)}</span>` : "")
      + `</div>`
      + `<h3 class="wf-title">${esc(sym)} backtest</h3>`
      + (c.tree_summary ? `<p class="wf-desc">${esc(c.tree_summary)}</p>` : "")
      + verdictBlock
      + `<div class="scan-stats">${strip}</div>`
      + versus
      + curve
      + spread
      + mcBlock
      + rigorBlock
      + tradeRows(c.trades || [], sym)
      + assume
      + onChartCta(c);
  }

  /* The one control this card carries. A backtest's own numbers are already
   * on it; the only thing left to offer is the reading it cannot print. */
  function onChartCta(c) {
    const blocked = strategyBlocker(c);
    return `<div class="wf-cta wf-cta-solo">`
      + `<button type="button" class="wf-ghost" data-bt-chart`
      + (blocked ? ` disabled title="${esc(blocked)}"` : "")
      + ` aria-pressed="false">${Icons.svg("candles", "sm")}`
      + `<span>Show on chart</span></button>`
      + (blocked ? `<span class="wf-cta-note">${esc(blocked)}</span>` : "")
      + `</div>`;
  }

  /** The equity curve: a shape, with the trades marked on it.
   *
   * Not a chart in the axes-and-ticks sense — where the money ended is a
   * question the figures above already answer. What a curve adds is HOW it
   * got there: smoothly or in one jump, and at which moments the strategy was
   * actually in the market. So it carries a fill (area reads as a level, a
   * bare line reads as a rate), a starting baseline to measure against, and a
   * dot per signal placed at the bar it fired on.
   */
  function equityChart(points, signals) {
    const rows = points.filter((p) => p && Number.isFinite(Number(p.v)));
    if (rows.length < 3) return "";
    const vals = rows.map((p) => Number(p.v));
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const span = (hi - lo) || 1;
    const W = 100, H = 44;
    const xAt = (i) => (i / (rows.length - 1)) * W;
    const yAt = (val) => H - ((val - lo) / span) * H;
    const d = vals.map((val, i) =>
      `${i ? "L" : "M"}${xAt(i).toFixed(2)} ${yAt(val).toFixed(2)}`).join(" ");
    const area = `${d} L${W} ${H} L0 ${H} Z`;
    const up = vals[vals.length - 1] >= vals[0];
    const base = yAt(vals[0]).toFixed(2);
    // Signals carry dates; the curve carries the same dates. Match on the
    // date rather than assuming the two arrays share an index — they do not
    // when the strategy sat out part of the window.
    const at = new Map(rows.map((p, i) => [String(p.t).slice(0, 10), i]));
    // Marks are DOM, not SVG. The plot is stretched with
    // preserveAspectRatio="none" so a viewBox unit is wider than it is tall,
    // and an SVG <circle> inside it is drawn as an ELLIPSE — the fills came
    // out as fat horizontal lozenges. A positioned span is round at every
    // panel width, and it can carry its own hover label.
    const marks = (signals || []).slice(0, 80).map((s) => {
      const key = String(s.t || s.date || "").slice(0, 10);
      const i = at.get(key);
      if (i == null) return "";
      const side = s.side === "sell" ? "s" : "b";
      const label = `${side === "s" ? "Exit" : "Entry"} · ${key}`;
      return `<span class="wf-mark ${side}" data-v="${esc(label)}"`
        + ` style="left:${(xAt(i) / W * 100).toFixed(2)}%;`
        + `top:${(yAt(vals[i]) / H * 100).toFixed(2)}%"></span>`;
    }).join("");
    return `<div class="wf-chart"><div class="wf-plot">`
      + `<svg class="wf-curve${up ? " up" : " down"}"`
      + ` viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">`
      + `<path class="fill" d="${area}"/>`
      + `<line class="base" x1="0" y1="${base}" x2="${W}" y2="${base}"/>`
      + `<path class="line" d="${d}"/></svg>${marks}</div>`
      + `<div class="wf-axis"><span>${esc(String(rows[0].t).slice(0, 10))}</span>`
      + `<span>${esc(String(rows[rows.length - 1].t).slice(0, 10))}</span></div></div>`;
  }

  /** Signed columns off a zero line — the shape for "was it every period, or
   *  one of them". Heights are geometry from the given list; no figure here
   *  is computed or printed. */
  function columns(values, sym) {
    const nums = values.map(Number).filter(Number.isFinite);
    if (!nums.length) return "";
    const top = Math.max(...nums.map(Math.abs)) || 1;
    const cells = nums.map((val) => {
      // Floor at 4%, not 2%: a period that returned -0.4% against a 3%
      // best still HAPPENED, and a 1px sliver reads as a rendering gap
      // rather than as the smallest bar in the set.
      const h = Math.max(4, Math.abs(val) / top * 50);
      const cls = val > 0 ? "up" : val < 0 ? "down" : "";
      const side = val >= 0 ? "bottom" : "top";
      return `<span class="wf-col" data-v="${esc(signed(sym, val, "%"))}">`
        + `<i class="${cls}" style="${side}:50%;height:${h.toFixed(1)}%"></i></span>`;
    }).join("");
    return `<div class="wf-cols">${cells}<i class="wf-zero"></i></div>`;
  }

  /** The trades, capped. The full list belongs in a table the user can sort;
   *  what a chat card owes is enough rows to see whether the wins and losses
   *  are the same size, which is the question a hit rate cannot answer. */
  const MAX_TRADE_ROWS = 6;
  const EXIT_REASON = {
    exit_tree: "Exit rule", stop_loss: "Stop", take_profit: "Target",
    end_of_window: "Window end", hold_to_end: "Held to end",
    max_bars: "Time exit", signal: "Signal",
  };
  function tradeRows(trades, sym) {
    if (!trades.length) return "";
    const rows = trades.slice(0, MAX_TRADE_ROWS).map((t) => {
      const ret = Number(t.return_pct);
      const cls = Number.isFinite(ret) ? (ret > 0 ? "up" : ret < 0 ? "down" : "") : "";
      const why = String(t.exit_reason || "");
      return `<tr><td>${esc(String(t.entry_date || "").slice(0, 10))}</td>`
        + `<td>${esc(String(t.exit_date || "open").slice(0, 10))}</td>`
        + `<td class="wf-num ${cls}">${Number.isFinite(ret)
            ? signed(sym, ret, "%") : "—"}</td>`
        + `<td class="wf-reason">${esc(EXIT_REASON[why]
            || why.replace(/_/g, " "))}</td></tr>`;
    }).join("");
    const more = trades.length - MAX_TRADE_ROWS;
    return section("Trades", `${trades.length}`,
      `<table class="wf-trades"><thead><tr><th>In</th><th>Out</th>`
      + `<th class="wf-num">Return</th><th>Why</th></tr></thead>`
      + `<tbody>${rows}</tbody></table>`
      + (more > 0 ? `<p class="wf-more">+${more} more</p>` : ""));
  }

  /* ── strategy, options, and the relationship tools ────────────────────
   *
   * Same construction as the two above: every figure is the payload's own,
   * the charts turn a given series into geometry and never into a statistic,
   * and a field the engine did not compute takes its row with it rather than
   * rendering as "—" beside five real ones.
   */

  /** A constructed basket. The weights ARE the strategy — which names, how
   *  much of each — so they lead as bars on one scale rather than a column of
   *  percentages the reader has to rank themselves. */
  function strategyBasket(c) {
    const names = c.constituents || [];
    const sym = c.constituents?.[0]?.symbol || "";
    const capital = c.capital_inr;
    const rows = names.map((n) => ({
      label: n.symbol, value: Number(n.weight_pct),
      text: `${n2(sym, n.weight_pct)}%`,
    }));
    // The reason a name is in the basket is the engine's own sentence. It sits
    // under the bar it explains, not in a legend somewhere else.
    const why = names.filter((n) => n.weight_reason).slice(0, 8).map((n) =>
      `<div class="wf-kv-row"><b>${esc(n.symbol)}</b>`
      + `<span>${esc(n.weight_reason)}</span></div>`).join("");
    const sleeves = (c.sleeves || []).length
      ? bars((c.sleeves || []).map((s) => ({
          label: s.name || s.sleeve || s.label || "",
          value: Number(s.weight_pct ?? s.pct),
          text: `${n2(sym, s.weight_pct ?? s.pct)}%`,
        })))
      : "";
    const alts = (c.alternatives || []).map((a) =>
      `<div class="wf-kv-row"><b>${esc(a.title || "")}</b>`
      + `<span>${esc(a.detail || "")}</span></div>`).join("");
    const assume = (c.assumptions || []).length
      ? `<ul class="wf-warn">${c.assumptions.map((a) =>
          `<li>${esc(a)}</li>`).join("")}</ul>` : "";
    return `<div class="wf-card">`
      + `<div class="wf-top"><span class="wf-chip">Basket</span>`
      + (c.weighting_scheme
          ? `<span class="wf-state">${esc(String(c.weighting_scheme)
              .replace(/_/g, " "))}</span>` : "")
      + `</div>`
      + `<h3 class="wf-title">${esc(c.title || "Strategy")}</h3>`
      + (capital != null ? `<p class="wf-desc">${esc(money(sym, capital))} across `
          + `${names.length} name${names.length === 1 ? "" : "s"}</p>` : "")
      + section("Weights", "", bars(rows))
      + section("Sleeves", "", sleeves)
      + section("Why these", "", why)
      + section("Instead you could", "", alts)
      + section("Assumptions", "", assume)
      + (c.rationale ? `<p class="wf-note">${esc(c.rationale)}</p>` : "");
  }

  /** A data-provenance banner, ABOVE the numbers it qualifies.
   *
   *  Without a Kite session the option tools return MOCK strikes and premiums
   *  — structurally perfect, financially fictional. That belongs before the
   *  first figure, not in a footnote under it: a reader who has already priced
   *  a spread off the card has been misled by the time they reach the bottom.
   *  Live data renders nothing at all, which is the correct amount of
   *  furniture for the normal case. */
  function provenance(c) {
    const status = String(c.data_status || "").toLowerCase();
    if (!status || status === "live" || status === "ok") return "";
    const note = c.stale_note || (status === "mock"
      ? "This option data is not live." : `Data status: ${status}.`);
    return `<p class="wf-stale" role="status">${esc(note)}</p>`;
  }

  /** An option structure, led by its payoff.
   *
   *  A spread's numbers — max loss, max profit, breakeven — are three points
   *  ON one curve, and reading them as three separate figures is what makes
   *  options feel like arithmetic homework. Drawn, the shape says the whole
   *  thing at once: where it makes money, where it stops, and how far the
   *  underlying has to travel to get there. */
  function optionStrategy(c) {
    const s = c.summary || {};
    const k = c.computed || {};
    const cr = c.critique || {};
    const und = s.underlying || c.underlying || "";
    const fin = (x) => x != null && Number.isFinite(Number(x));
    const rupee = (x) => (fin(x) ? money(und, Math.abs(Number(x))) : "—");
    const legs = (s.legs || []).map((l) =>
      `<li class="wf-leg"><span class="wf-side ${String(l.side).toLowerCase()}">`
      + `${esc(l.side)}</span>`
      + `<b>${esc(l.strike)} ${esc(l.option_type)}</b>`
      + (fin(l.mid) ? `<span class="wf-num">${esc(money(und, l.mid))}</span>` : "")
      + `</li>`).join("");
    const g = k.net_greeks || {};
    const greeks = ["delta", "gamma", "theta", "vega"]
      .filter((x) => fin(g[x]))
      .map((x) => stat(cap(x), n2(und, g[x]), x === "theta" && g[x] < 0 ? "down" : ""))
      .join("");
    const strip = stat("Max profit", rupee(k.max_profit), "up")
      + stat("Max loss", rupee(k.max_loss), "down")
      + stat("Net premium", rupee(k.net_premium),
             Number(k.net_premium) >= 0 ? "up" : "down",
             Number(k.net_premium) >= 0 ? "credit" : "debit")
      + stat("Chance of profit", fin(k.pop) ? `${Math.round(k.pop * 100)}%` : "—", "")
      + stat("Breakeven", (k.breakevens || []).map((b) => n2(und, b)).join(" / ") || "—", "")
      + stat("Capital", rupee(k.capital_required ?? k.margin_estimate), "");
    // Rule-based, from the engine — flags the user should read before the
    // structure looks clever. Severity drives the tone, not my judgement.
    const flags = (cr.flags || []).map((f) =>
      `<li class="sev-${esc(f.severity || "info")}">${esc(f.text)}</li>`).join("");
    return `<div class="wf-card">`
      + `<div class="wf-top"><span class="wf-chip">Options</span>`
      + (s.expiry ? `<span class="wf-state">${esc(s.expiry)}</span>` : "")
      + `</div>`
      + `<h3 class="wf-title">${esc(und)} `
      + `${esc(String(s.template || "").replace(/_/g, " "))}</h3>`
      + (cr.summary ? `<p class="wf-desc">${esc(cr.summary)}</p>` : "")
      + provenance(c)
      + payoffChart(k.payoff || [], k.breakevens || [], c.spot ?? s.spot)
      + `<div class="scan-stats">${strip}</div>`
      + section("Legs", `${(s.legs || []).length}`,
                legs ? `<ul class="wf-legs">${legs}</ul>` : "")
      + section("Net greeks", "", greeks ? `<div class="scan-stats">${greeks}</div>` : "")
      + section("Worth knowing", "", flags ? `<ul class="wf-flags">${flags}</ul>` : "")
      + (k.margin_note ? `<p class="wf-note">${esc(k.margin_note)}</p>` : "");
  }

  /** The payoff at expiry: profit above the line, loss below it, split at
   *  zero rather than coloured by slope. The zero crossing IS the breakeven,
   *  so it is drawn once as a line and not also listed as a claim. */
  function payoffChart(points, breakevens, spot) {
    const pts = points.filter((p) => p && Number.isFinite(Number(p.pnl))
                                     && Number.isFinite(Number(p.s)));
    if (pts.length < 3) return "";
    const xs = pts.map((p) => Number(p.s)), ys = pts.map((p) => Number(p.pnl));
    const xLo = Math.min(...xs), xHi = Math.max(...xs);
    // Headroom above and below. Without it the flat top of a spread sits
    // exactly on the viewBox edge and the stroke is shaved in half — the
    // capped profit, which is the whole point of the structure, read as a
    // clipping artefact.
    const yRaw = [Math.min(...ys, 0), Math.max(...ys, 0)];
    const pad = ((yRaw[1] - yRaw[0]) || 1) * 0.12;
    const yLo = yRaw[0] - pad, yHi = yRaw[1] + pad;
    const xSpan = (xHi - xLo) || 1, ySpan = (yHi - yLo) || 1;
    const W = 100, H = 56;
    const X = (v) => ((v - xLo) / xSpan) * W;
    const Y = (v) => H - ((v - yLo) / ySpan) * H;
    const zero = Y(0);
    const line = pts.map((p, i) =>
      `${i ? "L" : "M"}${X(p.s).toFixed(2)} ${Y(p.pnl).toFixed(2)}`).join(" ");
    // Two fills, each clipped to its own side of zero by a rectangle — a
    // single path cannot be two colours, and splitting the series by sign
    // would need interpolation this card has no business doing.
    const id = "po" + Math.random().toString(36).slice(2, 8);
    const area = `${line} L${X(xs[xs.length - 1]).toFixed(2)} ${zero.toFixed(2)}`
      + ` L${X(xs[0]).toFixed(2)} ${zero.toFixed(2)} Z`;
    const marks = (breakevens || []).filter(Number.isFinite)
      .map((b) => `<line class="be" x1="${X(b).toFixed(2)}" y1="0" `
        + `x2="${X(b).toFixed(2)}" y2="${H}"/>`).join("");
    const spotMark = Number.isFinite(Number(spot))
      ? `<line class="spot" x1="${X(spot).toFixed(2)}" y1="0" `
        + `x2="${X(spot).toFixed(2)}" y2="${H}"/>` : "";
    return `<div class="wf-chart"><svg class="wf-payoff" viewBox="0 0 ${W} ${H}"`
      + ` preserveAspectRatio="none" aria-hidden="true">`
      + `<defs>`
      + `<clipPath id="${id}u"><rect x="0" y="0" width="${W}" height="${zero.toFixed(2)}"/></clipPath>`
      + `<clipPath id="${id}d"><rect x="0" y="${zero.toFixed(2)}" width="${W}" height="${(H - zero).toFixed(2)}"/></clipPath>`
      + `</defs>`
      + `<path class="up" d="${area}" clip-path="url(#${id}u)"/>`
      + `<path class="down" d="${area}" clip-path="url(#${id}d)"/>`
      + `<line class="zero" x1="0" y1="${zero.toFixed(2)}" x2="${W}" y2="${zero.toFixed(2)}"/>`
      + marks + spotMark
      + `<path class="line" d="${line}"/></svg>`
      + `<div class="wf-axis"><span>${esc(n2("", xLo))}</span>`
      + `<span>${esc(n2("", xHi))}</span></div></div>`;
  }

  /** The chain, centred on the money. Open interest is drawn as a bar behind
   *  the figure because the SHAPE of OI across strikes is the read — where the
   *  writers are — and a column of six-digit numbers hides it completely. */
  function optionChain(c) {
    const rows = c.rows || [];
    const und = c.underlying || "";
    const fin = (x) => x != null && Number.isFinite(Number(x));
    const em = c.expected_move || {};
    const strip = stat("Spot", fin(c.spot) ? money(und, c.spot) : "—", "")
      + stat("ATM", fin(c.atm_strike) ? n2(und, c.atm_strike) : "—", "")
      + stat("Max pain", fin(c.max_pain) ? n2(und, c.max_pain) : "—", "")
      + stat("Expected move", fin(em.pct) ? `±${n2(und, em.pct)}%` : "—", "",
             fin(em.abs) ? `±${n2(und, em.abs)}` : "")
      + stat("PCR (OI)", fin(c.pcr_oi) ? n2(und, c.pcr_oi) : "—", "")
      + stat("Lot", c.lot_size ?? "—", "");
    const topOI = rows.reduce((m, r) => Math.max(
      m, Number(r.ce?.oi) || 0, Number(r.pe?.oi) || 0), 0) || 1;
    const cell = (side, oi, ltp) =>
      `<td class="wf-oi ${side}"><i style="width:${
        Math.min(100, (Number(oi) || 0) / topOI * 100).toFixed(1)}%"></i>`
      + `<span>${fin(ltp) ? esc(n2(und, ltp)) : "—"}</span></td>`;
    const body = rows.map((r) => {
      const atm = fin(c.atm_strike) && Number(r.strike) === Number(c.atm_strike);
      return `<tr${atm ? ' class="atm"' : ""}>`
        + cell("ce", r.ce?.oi, r.ce?.ltp)
        + `<td class="wf-strike">${esc(n2(und, r.strike))}</td>`
        + cell("pe", r.pe?.oi, r.pe?.ltp) + `</tr>`;
    }).join("");
    return `<div class="wf-card">`
      + `<div class="wf-top"><span class="wf-chip">Chain</span>`
      + `<span class="wf-state">${esc(c.expiry || "")}</span></div>`
      + `<h3 class="wf-title">${esc(und)} option chain</h3>`
      + provenance(c)
      + `<div class="scan-stats">${strip}</div>`
      + section("Calls · strike · puts", `${rows.length}`,
          body ? `<table class="wf-chain"><thead><tr><th>CE</th>`
            + `<th class="wf-strike">Strike</th><th>PE</th></tr></thead>`
            + `<tbody>${body}</tbody></table>` : "");
  }

  /** Pairs, cointegration, cross-sectional portfolios — four tools, one
   *  shape: a claim, some numbers, and whether the claim survives them. They
   *  share a renderer because giving each its own would be four cards that
   *  look different for no reason a reader could name. */
  function quantResult(c) {
    const m = c.metrics || {};
    const sym = (c.pair && c.pair[0]) || (c.symbols && c.symbols[0]) || "";
    const fin = (x) => x != null && Number.isFinite(Number(x));
    const title = c.pair ? `${c.pair.join(" / ")}`
      : c.symbols ? `${c.symbols.length} names`
      : "Result";
    const co = c.cointegration || (c.is_cointegrated != null ? c : null);
    const verdict = co && co.is_cointegrated != null
      ? `<div class="wf-verdict" data-verdict="${co.is_cointegrated ? "ok" : "no"}">`
        + `<b>${co.is_cointegrated ? "Cointegrated" : "Not cointegrated"}</b>`
        + (co.note ? `<span>${esc(co.note)}</span>` : "")
        + `</div>` : "";
    const KEYS = [
      ["total_return_pct", "Return", "pct"], ["cagr_pct", "CAGR", "pct"],
      ["sharpe", "Sharpe", "n"], ["max_drawdown_pct", "Max drawdown", "dd"],
      ["hit_rate_pct", "Hit rate", "pct"], ["n_trades", "Trades", "raw"],
    ];
    const strip = KEYS.filter(([k]) => m[k] != null).map(([k, label, kind]) => {
      const v = m[k];
      if (kind === "pct") return stat(label, signed(sym, v, "%"), way(v));
      if (kind === "dd") return stat(label, `−${n2(sym, Math.abs(v))}%`, "down");
      if (kind === "n") return stat(label, n2(sym, v), "");
      return stat(label, v, "");
    }).join("");
    // The Johansen test IS a comparison: at each rank, the trace statistic
    // against its 95% critical value, and cointegration is claimed only where
    // the statistic clears the bar. Printing two columns of numbers hides the
    // one thing that matters — whether it cleared — so the bar carries the
    // threshold as a tick and the figures stay quoted verbatim beside it.
    const trace = c.trace_stats || [], crit = c.crit_95 || [];
    const johansen = (trace.length && trace.length === crit.length)
      ? bars(trace.map((t, i) => ({
          label: `Rank ${i}`,
          value: crit[i] ? Number(t) / Number(crit[i]) : 0,
          text: `${n2(sym, t)} vs ${n2(sym, crit[i])}`,
          tone: Number(t) > Number(crit[i]) ? "up" : "",
        })), { max: 1.5, tick: 1 })
      : "";
    const found = (c.cointegrated || []).slice(0, 8).map((p) =>
      `<div class="wf-kv-row"><b>${esc(
        Array.isArray(p.pair) ? p.pair.join(" / ") : (p.pair || ""))}</b>`
      + `<span>${esc(fin(p.p_value) ? `p ${n2(sym, p.p_value)}` : "")}</span></div>`
    ).join("");
    return `<div class="wf-card">`
      + `<div class="wf-top"><span class="wf-chip wf-chip-sim">Simulation</span>`
      + (c.period ? `<span class="wf-state">${esc(c.period)}</span>` : "")
      + `</div>`
      + `<h3 class="wf-title">${esc(title)}</h3>`
      + (c.summary ? `<p class="wf-desc">${esc(c.summary)}</p>` : "")
      + verdict
      + (strip ? `<div class="scan-stats">${strip}</div>` : "")
      + section("Trace vs 95% critical",
                c.n_obs ? `${c.n_obs} obs` : "", johansen)
      + section("Cointegrated pairs", `${(c.cointegrated || []).length}`, found)
      + (c.note ? `<p class="wf-note">${esc(c.note)}</p>` : "");
  }

  /** JSON into an attribute. The thread's `esc` leaves quotes alone, which
   *  is right for a text node and would break out of an attribute. */
  function attrJSON(value) {
    return JSON.stringify(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  /* The draft card's behaviour. Pivot's version runs the backtest through
   * its own API rather than a chat turn, and renders the RESULT inside the
   * draft card — the strategy and its evidence in one object, so nothing has
   * to be scrolled back to. Same here, against `/execution/backtest`.
   *
   * Save & activate is deliberately inert and says why. Charto keeps its own
   * users in its own SQLite; Pivot's accounts live in Postgres, and nothing
   * maps one to the other yet. A button that posted anyway would arm an
   * agent under somebody else's account, and a button that was hidden would
   * be a capability the user never learns exists. Disabled with the reason
   * on it is the only honest third option. */
  /* Show on chart.
   *
   * A toggle rather than a one-way "draw": the layer is dense by design, and
   * anything you can put on a chart you have to be able to take off it in the
   * same gesture. Only ONE backtest is ever on the chart — pressing this on a
   * second card clears the first, because two strategies' trades interleaved
   * in the same two colours is not a comparison, it is a mess.
   */
  function wireOnChart(box, payload) {
    const btn = box.querySelector("[data-bt-chart]");
    if (!btn || btn.disabled) return;
    const label = btn.querySelector("span");
    btn.addEventListener("click", () => {
      const scene = window.__charto && window.__charto.scene;
      if (!scene) return;
      const on = btn.getAttribute("aria-pressed") === "true";
      if (on) {
        scene.apply([clearStrategy()]);
        btn.setAttribute("aria-pressed", "false");
        label.textContent = "Show on chart";
        return;
      }
      // Any other card showing its own trades stands down first, so the
      // pressed state on screen always matches what is actually drawn.
      document.querySelectorAll('[data-bt-chart][aria-pressed="true"]')
        .forEach((other) => {
          other.setAttribute("aria-pressed", "false");
          const l = other.querySelector("span");
          if (l) l.textContent = "Show on chart";
        });
      const items = strategyItems(payload);
      if (!items.length) {
        label.textContent = "No bars for these dates";
        btn.disabled = true;
        return;
      }
      scene.apply([clearStrategy()].concat(items));
      btn.setAttribute("aria-pressed", "true");
      label.textContent = "Hide from chart";
    });
  }

  function wireDraft(box) {
    const why = box.querySelector("[data-wf-why]");
    if (why) {
      const body = box.querySelector(".wf-why-body");
      why.addEventListener("click", () => {
        const open = body.hidden;
        body.hidden = !open;
        why.setAttribute("aria-expanded", String(open));
        why.querySelector("span").textContent = open ? "Hide reasoning" : "Why this?";
      });
    }
    const activate = box.querySelector("[data-wf-activate]");
    if (activate) activate.disabled = true;

    const run = box.querySelector("[data-wf-backtest]");
    if (!run) return;
    const slot = box.querySelector("[data-wf-slot]");
    const label = run.querySelector("span");
    run.addEventListener("click", async () => {
      if (run.disabled) return;
      run.disabled = true;
      label.textContent = "Running…";
      let draft = {};
      try { draft = JSON.parse(box.querySelector("[data-wf]").dataset.draft); }
      catch (e) { draft = {}; }
      try {
        const res = await fetch(`${API}/execution/backtest`, {
          method: "POST",
          headers: (window.Auth && Auth.headers)
            ? Auth.headers({ "Content-Type": "application/json" })
            : { "Content-Type": "application/json" },
          body: JSON.stringify(draft),
        });
        const data = await res.json();
        if (!res.ok || data.error) throw new Error(data.detail || data.error
          || `HTTP ${res.status}`);
        slot.innerHTML = strategyBacktest(data);
        slot.hidden = false;
        // the nested read-out carries its own on-chart control
        wireOnChart(slot, data);
        label.textContent = "Re-run backtest";
      } catch (e) {
        // The failure goes where the result would have gone. A backtest that
        // silently does nothing reads as a dead button.
        slot.innerHTML = `<p class="wf-note">${esc(String(e.message || e))}</p>`;
        slot.hidden = false;
        label.textContent = "Backtest";
      } finally {
        run.disabled = false;
      }
    });
  }

  /** The stat strip's cell: a label, the figure, and — where a figure needs
   *  one — the QUALIFIER that says how to read it. An ADX of 29 means nothing
   *  to most readers until it is placed against Wilder's bands; a return gap
   *  means nothing until the window it was measured over is named.
   *
   *  Hoisted out of the two panels that had a copy each. Eight cards with
   *  eight private versions of this would drift, and a stat that looked
   *  different from card to card would be telling the reader the cards are
   *  unrelated when they are the same instrument answered differently. */
  function stat(k, v, tone, q) {
    return `<div class="scan-stat"><span class="k">${esc(k)}</span>`
      + `<b class="v${tone ? " " + tone : ""}">${esc(v)}</b>`
      + (q ? `<span class="q">${esc(q)}</span>` : "") + `</div>`;
  }

  const n2 = (sym, v) => Sym.of(sym).num(v,
    { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  /** A signed figure with its sign shown, because the sign IS the reading.
   *  The formatter already prints the minus; only the plus has to be added,
   *  and it is added rather than implied — "+12.25 pp" and "12.25 pp" are the
   *  same number and only one of them says which way. */
  const signed = (sym, v, unit) => {
    if (v == null || !Number.isFinite(Number(v))) return "";
    const s = n2(sym, v);
    return (Number(v) > 0 ? "+" : "") + s + (unit || "");
  };
  const way = (v) => (Number(v) > 0 ? "up" : Number(v) < 0 ? "down" : "");

  /* ── bars: one scale, drawn against each other ─────────────────────────
   *
   * The panel's one chart primitive, and it is horizontal for a reason that
   * is not taste: this column is user-resizable down to 340px, and a vertical
   * grouped chart at that width is four stubs under a legend. A horizontal
   * row keeps its label, its figure and its length legible at any width the
   * splitter allows, and stacks to as many series as the payload has.
   *
   * Everything about a bar's LENGTH is drawing; the figure printed beside it
   * is the payload's own string. Nothing here rounds, totals or rescales a
   * number into the text — a bar that disagreed with the number next to it
   * would be the panel arguing with itself.
   *
   *   · the scale is the largest ABSOLUTE value in the set, never 100. ADX
   *     rarely passes 40 and a 0-100 axis renders the whole comparison as
   *     stubs, which loses the one thing a bar row exists to show;
   *   · `signed` puts zero in the middle, because a set that contains −0.9%
   *     and +0.28% is two directions and not two sizes;
   *   · `tick` marks a threshold that is part of the reading (25 on ADX, 50
   *     on RSI) — it is disclosed in the section note, never a bare line.
   */
  function bars(items, opt) {
    const o = opt || {};
    const top = o.max
      || items.reduce((m, x) => Math.max(m, Math.abs(Number(x.value) || 0)), 0)
      || 1;
    const half = o.signed ? 50 : 100;
    const rows = items.map((x) => {
      const v = Number(x.value);
      if (!Number.isFinite(v)) return "";
      const w = Math.min(half, Math.abs(v) / top * half);
      const left = o.signed ? (v >= 0 ? 50 : 50 - w) : 0;
      const tone = x.tone || "";
      const tick = (o.tick != null && !o.signed && o.tick <= top)
        ? `<i class="tick" style="left:${(o.tick / top * 100).toFixed(1)}%"></i>`
        : "";
      return `<div class="scan-bar">`
        + `<span class="lb">${esc(x.label)}</span>`
        + `<b class="num${tone ? " tone-" + tone : ""}">${esc(x.text)}</b>`
        + `<span class="track${o.signed ? " signed" : ""}">`
        + `<i class="fill${tone ? " " + tone : ""}" `
        + `style="left:${left.toFixed(1)}%;width:${w.toFixed(1)}%"></i>`
        + tick + `</span></div>`;
    }).join("");
    return rows ? `<div class="scan-bars">${rows}</div>` : "";
  }

  /** Several bars that belong to one window, under its name. Used where the
   *  same measurement was taken at more than one interval: the interval is
   *  the thing being compared and it has to label the group, not each bar. */
  function group(label, note, body) {
    if (!body) return "";
    return `<div class="scan-group"><div class="gl">${esc(label)}`
      + (note ? `<span class="gn">${esc(note)}</span>` : "")
      + `</div>${body}</div>`;
  }

  /** +DI against −DI. Shared by the trend read and the studies panel, which
   *  are two tools reading ONE ADX result — two copies of this could quietly
   *  draw the same pair two ways.
   *
   *  Scaled to the larger of the pair rather than to 100 for the same reason
   *  every bar row here is: DI rarely reaches 50. */
  function diLegs(sym, di) {
    if (!di || di.plus == null || di.minus == null) return "";
    return bars([
      { label: "+DI", value: di.plus, text: n2(sym, di.plus), tone: "up" },
      { label: "−DI", value: di.minus, text: n2(sym, di.minus), tone: "down" },
    ]);
  }

  /** A price ladder: the levels around price, with price IN it rather than
   *  beside it. A list of levels above and a list below leaves the reader to
   *  work out which side they are standing on; one column, high to low, with
   *  the current row marked, is the same information already read. */
  function rung(cls, price, sym, label, note) {
    return `<div class="scan-rung${cls ? " " + cls : ""}">`
      + `<b class="px">${esc(price)}</b>`
      + `<span class="lb">${esc(label)}</span>`
      + (note ? `<span class="nt">${esc(note)}</span>` : "")
      + `</div>`;
  }

  /** What was looked for, and whether it was there. Both halves — a chip
   *  saying no deal was printed is a measurement of the same table that
   *  would have shown one, and a panel that listed only the hits would let
   *  the reader assume the misses were never checked. */
  function chips(items) {
    const out = (items || []).map((x) =>
      `<span class="scan-chip${x.found ? " found" : ""}">`
      + `<b>${esc(x.what)}</b>`
      + (x.detail ? `<span>${esc(x.detail)}</span>` : "")
      + `</span>`).join("");
    return out ? `<div class="scan-chips">${out}</div>` : "";
  }

  /** A small table, for the one shape a row cannot carry: the same quantity
   *  for several subjects across several windows. Kept rare on purpose —
   *  this panel is a reading surface, not a spreadsheet.
   *
   *  Cells go in as HTML so a figure can carry emphasis, which means the
   *  CALLER escapes them. Every caller in this file does. */
  function grid(head, rows) {
    if (!rows || !rows.length) return "";
    const th = head.map((h, i) =>
      `<span class="gh${i ? "" : " lead"}">${esc(h)}</span>`).join("");
    const tr = rows.map((r) => `<div class="scan-gr">` + r.map((cell, i) =>
      `<span class="gc${i ? "" : " lead"}">${cell}</span>`).join("") + `</div>`).join("");
    return `<div class="scan-grid" style="--cols:${head.length}">`
      + `<div class="scan-gr head">${th}</div>${tr}</div>`;
  }

  /** One line that qualifies the section above it — the band a tally would
   *  flip in, the timeframe a gap is widest on. It is a sentence the payload
   *  computed, not a conclusion drawn here, and it is boxed rather than
   *  bolded so it never reads as the panel's own verdict. */
  function callout(text) {
    return text ? `<div class="scan-call">${esc(text)}</div>` : "";
  }

  /** What was scanned, on the card's own bottom margin. Every panel in this
   *  app says where its numbers came from. */
  function foot(text) {
    return text ? `<div class="scan-foot">${esc(text)}</div>` : "";
  }

  // ── the pattern sweep ───────────────────────────────────────────
  function patterns(c) {
    const sym = c.symbol;
    const counts = c.counts || {};

    /* The four figures the sweep is ABOUT, before any of the detail. The
     * structure read is the only one with a direction, so it is the only one
     * that takes a colour — a count is not bullish.
     *
     * Both counts are of what the panel SHOWS — tiles and rows — never of
     * what the payload held. `candles_found` counts names and would read 20
     * over eighteen rows, because one bar can qualify under several; a
     * heading that argues with the list under it leaves the reader no way to
     * tell which half is wrong. */
    const stats = [
      c.trend ? stat("Structure", cap(c.trend), TONE[c.trend] || "") : "",
      stat("Bars scanned", Sym.of(sym).num(c.bars_scanned)),
      stat("Chart patterns", counts.chart_found ?? (c.chart_patterns || []).length),
      stat("Candle signals", counts.candle_bars ?? (c.candles || []).length),
    ].filter(Boolean).join("");

    /* Deliberately uncoloured. Every one of these rows begins with the word
     * "Bullish" or "Bearish" — painting the sentence its own colour says the
     * same thing twice, and five red lines in a column is a panel raising its
     * voice about a swing sequence. The one figure that takes a colour in
     * this card is the structure read at the top, because that is the only
     * place direction is the finding rather than the wording. */
    const events = (c.events || []).map((e) =>
      row("", null, when(e.t), esc(e.what), money(sym, e.price))).join("");

    /* A formation gets a tile rather than a row because it is four facts, not
     * one: what it is, when it ran, which way the textbook reads it, and the
     * single number that decides it. Stacked as rows those four would need
     * four columns and the panel would become a spreadsheet. */
    const tiles = (c.chart_patterns || []).map((p) => {
      const badge = p.status
        ? `<span class="scan-badge ${BADGE[p.status] || "open"}">${esc(cap(p.status))}</span>`
        : "";
      const sub = [span(p.from, p.to), p.bias && p.bias !== "neutral"
        ? `${p.bias} bias` : ""].filter(Boolean).join(" · ");
      // The measurement and what became of it are one sentence: a neckline
      // that broke is a different fact from a neckline that hasn't, and
      // printing the level with no verdict beside it invites the reader to
      // supply the optimistic one.
      const fact = p.measure
        ? `<span class="scan-fact">${esc(p.measure.label)} `
          + `<b>${esc(money(sym, p.measure.value))}</b>`
          + (p.broke_at ? `<span class="broke"> broke ${esc(when(p.broke_at))}</span>` : "")
          + `</span>`
        : "";
      return `<div class="scan-tile${p.drawn ? " drawn" : ""}"`
        + (p.drawn && p.id ? ` data-ann="${esc(p.id)}"` : "")
        + `>${badge}<b class="nm">${esc(cap(p.name))}</b>`
        + `<span class="sub">${esc(sub)}</span>${fact}</div>`;
    }).join("");

    /* A candle row closes on its BIAS where a structure row closes on its
     * price — that word is the whole reason to read this list right to left,
     * and it is the shape's textbook reading rather than a forecast, which is
     * why it is a word and not an arrow. */
    const candles = (c.candles || []).map((k) => {
      const t = TONE[k.bias];
      return `<div class="scan-row${k.drawn ? " drawn" : ""}"`
        + (k.ann ? ` data-ann="${esc(k.ann)}"` : "")
        + `><span class="when">${esc(when(k.t))}</span>`
        + `<span class="what">${esc(cap((k.names || []).join(", ")))}</span>`
        + `<span class="bias${t ? " tone-" + t : ""}">`
        + `${esc(cap(k.bias || "neutral"))}</span></div>`;
    }).join("");

    /* Found versus drawn, said out loud. The caps are real — three formations
     * and `mark_limit` bars — and a list of twelve above a chart showing five
     * marks would have the reader hunting for seven that were never put
     * there. The panel is the honest place to say so, once. */
    const drew = (found, shown, noun, verb) =>
      (found && shown < found) ? `${found} ${noun} · ${shown} ${verb}` : "";

    const more = (c.candles || []).length > 8
      ? `<button type="button" class="scan-more" data-more>`
        + `${(c.candles.length - 8)} more</button>` : "";

    return `<div class="scan-stats">${stats}</div>`
      + section("Structure events", "", events)
      + section("Chart patterns",
                drew(counts.chart_found, counts.chart_drawn, "found", "drawn"),
                tiles && `<div class="scan-tiles">${tiles}</div>`)
      + section("Candlestick patterns",
                drew(counts.candle_bars, counts.candles_marked, "bars", "marked"),
                candles && `<div class="scan-rows${more ? " capped" : ""}">${candles}</div>${more}`)
      + `<div class="scan-foot">${esc(c.bars_scanned)} ${esc(c.interval)} bars`
      + (c.window ? ` · ${esc(c.window)}` : "") + `</div>`;
  }

  // ── the trend read ──────────────────────────────────────────────────
  //
  // Four measurements of one chart that are allowed to disagree, and the
  // panel's whole job is to let them. They sit side by side in the stat
  // strip at equal weight — no arrow, no verdict line, nothing that folds
  // "sideways" and "bearish" into a third word — because which of the four
  // matters is what the paragraph beside this is for.
  function trend(c) {
    const sym = c.symbol;

    /* Structure and bias are the two readings with a direction, so they are
     * the two that take a colour. ADX has no side at all — a high reading in
     * a bearish market is a strong DOWNtrend — and painting it would be the
     * panel inventing one. The range is two prices.
     *
     * The third line is a QUALIFIER: the word or figure that says how to read
     * the number above it, and the reason neither has to go in the prose. An
     * ADX of 29 means nothing to most readers until it is placed against
     * Wilder's own bands, and a high and a low do not say where price is
     * sitting between them. Both come off the payload; neither is derived
     * here. */
    const adx = c.adx || {};
    const rng = c.range || {};
    const nx = (v) => Sym.of(sym).num(v, { maximumFractionDigits: 2 });
    const stats = [
      c.structure ? stat("Trend", cap(c.structure), TONE[c.structure] || "") : "",
      c.bias ? stat("Bias", cap(c.bias), TONE[c.bias] || "") : "",
      adx.value != null
        ? stat(adx.period ? `ADX ${adx.period}` : "ADX", n2(sym, adx.value),
               "", adx.strength) : "",
      // One currency symbol, on the pair. Two ends of one range are one
      // quantity — "₹1,249.8–₹1,345.8" prices them as if they were two.
      rng.low != null && rng.high != null
        ? stat("Range", `${Sym.of(sym).cur}${nx(rng.low)}–${nx(rng.high)}`, "",
               rng.position_pct != null
                 ? `${nx(rng.position_pct)}% of range` : "") : "",
    ].filter(Boolean).join("");

    /* +DI against −DI, drawn against each other rather than listed. The
     * comparison IS the reading — 8.59 means nothing except next to 35.88 —
     * and two numbers in a column leave the reader to do the subtraction. */
    const di = diLegs(sym, c.di);

    // Same rows, same derivation, same wording as the pattern sweep's panel —
    // both are reading one market_structure result (`_struct_events`).
    const events = (c.events || []).map((e) =>
      row("", null, when(e.t), esc(e.what), money(sym, e.price))).join("");

    /* A fitted line is four facts — which line, what earned it the name,
     * whether it is still holding, and the one level that says so. `touches`
     * is not decoration: three real swings on the line is the whole reason
     * this counts as a trendline rather than a slope somebody saw, and a
     * panel that hid it would be asking to be trusted. The level's LABEL
     * carries the tense — a line price is respecting projects near the latest
     * bar, one it has closed through was near it. */
    const lines = (c.trendlines || []).map((t) => {
      const cls = BADGE[t.status] || "";
      const badge = t.status
        ? `<span class="scan-badge${cls ? " " + cls : ""}">`
          + `${esc(cap(t.status))}</span>` : "";
      const sub = t.touches
        ? `<span class="sub">${esc(t.touches)} touches</span>` : "";
      const fact = t.level != null
        ? `<span class="scan-fact">${esc(t.level_label || "At")} `
          + `<b>${esc(money(sym, t.level))}</b></span>` : "";
      return `<div class="scan-tile${t.drawn ? " drawn" : ""}"`
        + (t.drawn && t.id ? ` data-ann="${esc(t.id)}"` : "")
        + `>${badge}<b class="nm">${esc(cap(t.name))}</b>${sub}${fact}</div>`;
    }).join("");

    return `<div class="scan-stats">${stats}</div>`
      + section("DI comparison", "", di)
      + section("Structure events", "", events)
      + section("Trendlines", "", lines && `<div class="scan-tiles">${lines}</div>`)
      + foot(`${c.bars_scanned} ${c.interval} bars`
             + (c.window ? ` · ${c.window}` : ""));
  }

  // ── the studies panel ───────────────────────────────────────────────
  //
  // Six studies in three families, and the families are allowed to disagree.
  // The panel is built so that they CAN: the overlays get rows tinted by the
  // side price is on, momentum gets tiles carrying the change behind each
  // reading, and ADX sits between them with no colour at all, because it has
  // no side. Nothing in here folds the three into a fourth word — the tally
  // at the top is a count of the overlays and says so in its own qualifier.
  function indicators(c) {
    const sym = c.symbol;
    const rsi = c.rsi || {}, adx = c.adx || {};
    const stats = [
      c.alignment ? stat("Trend", cap(c.alignment), TONE[c.alignment] || "",
                         (c.overlays || []).length
                           ? `${(c.overlays || []).length} overlays` : "") : "",
      c.momentum ? stat("Momentum", cap(c.momentum), "",
                        rsi.period ? `RSI ${rsi.period} band` : "") : "",
      rsi.value != null
        ? stat(rsi.period ? `RSI ${rsi.period}` : "RSI", n2(sym, rsi.value)) : "",
      adx.value != null
        ? stat(adx.period ? `ADX ${adx.period}` : "ADX", n2(sym, adx.value),
               "", adx.strength) : "",
      c.price != null ? stat("Price", money(sym, c.price)) : "",
    ].filter(Boolean).join("");

    /* One row per overlay, tinted by the side price is on. The tint is the
     * reading — three rows the same colour is the tally the strip reports,
     * seen rather than counted — and the row still says the word, because a
     * colour alone is a claim nobody can check and is invisible to a reader
     * who cannot see it. */
    const rows = (c.overlays || []).map((o) => {
      const t = o.side === "below" ? "down" : o.side === "above" ? "up" : "";
      return `<div class="scan-read${t ? " tone-" + t : ""}">`
        + `<b class="nm">${esc(o.name)}</b>`
        + `<span class="nt">${esc(o.note || "")}</span>`
        + `<b class="num">${esc(money(sym, o.value))}</b></div>`;
    }).join("");

    const zone = c.zone
      ? callout(`Flip band ${money(sym, c.zone.lo)} – ${money(sym, c.zone.hi)}`
                + (c.zone.note ? ` · ${c.zone.note}` : "")) : "";

    /* Each momentum reading is three things and needs all three: the state,
     * the figures it was read off, and the comparison that earned the state.
     * "Easing" on its own is an opinion; "−1.35 · less negative than the
     * prior bar" is the measurement the word is short for. */
    const tiles = (c.readings || []).map((r) => `<div class="scan-tile">`
      + (r.state ? `<span class="scan-badge ${r.tone === "up" ? "ok" : "bad"}">`
                   + `${esc(cap(r.state))}</span>` : "")
      + `<b class="nm">${esc(r.name)}</b>`
      + `<span class="scan-fact"><b>${esc(r.figures)}</b>`
      + (r.why ? ` · ${esc(r.why)}` : "") + `</span></div>`).join("");

    return `<div class="scan-stats">${stats}</div>`
      + section("Price against the overlays", "", rows && rows + zone)
      + section("Directional strength", "+DI against −DI", diLegs(sym, c.di))
      + section("Momentum readings", "",
                tiles && `<div class="scan-tiles">${tiles}</div>`)
      + foot(`${c.bars_scanned} ${c.interval} bars`
             + (c.window ? ` · ${c.window}` : ""));
  }

  // ── the confirmation checklist ──────────────────────────────────────
  //
  // The panel most at risk of being read as a recommendation, so it is built
  // to resist that reading: the stages are numbered rather than scored, the
  // weight beside each says what a break COSTS rather than how likely it is,
  // and every condition carries the reading it is at now — which is what
  // turns a wish list into a measurement. There is deliberately no progress
  // bar and no percentage anywhere in here.
  function confirmation(c) {
    const sym = c.symbol;
    const stats = [
      c.direction ? stat("Confirming", cap(c.direction),
                         TONE[c.direction] || "") : "",
      c.of ? stat("Conditions met", `${c.met} of ${c.of}`, "",
                  "measured, not scored") : "",
      c.price != null ? stat("Price", money(sym, c.price)) : "",
    ].filter(Boolean).join("");

    const steps = (c.stages || []).map((s) => {
      const at = s.lo != null && s.hi != null
        ? `${money(sym, s.lo)} – ${money(sym, s.hi)}`
        : s.price != null ? money(sym, s.price) : "";
      return `<div class="scan-step${s.met ? " met" : ""}">`
        + `<span class="no">${esc(s.step)}</span>`
        + `<b class="nm">${esc(s.action)}${at ? " " + esc(at) : ""}</b>`
        + (s.weight ? `<span class="wt">${esc(s.weight)}</span>` : "")
        + (s.why ? `<span class="nt">${esc(s.why)}</span>` : "")
        + `</div>`;
    }).join("");

    /* "Not yet" rather than a cross. A condition that has not been met is a
     * chart that has not done something yet, which is a neutral fact about
     * today; a failure mark would read as the chart having tried. */
    const checks = (c.conditions || []).map((x) => {
      const now = x.now != null ? x.now
        : x.price != null ? money(sym, x.price) : "";
      return `<div class="scan-check${x.met ? " met" : ""}">`
        + `<span class="what">${esc(x.what)}</span>`
        + (now ? `<b class="num">${esc(now)}</b>` : "")
        + `<span class="st">${x.met ? "Met" : "Not yet"}</span></div>`;
    }).join("");

    return `<div class="scan-stats">${stats}</div>`
      + section("Staged price confirmation", "in order of what a break costs",
                steps && `<div class="scan-steps">${steps}</div>`)
      + section("Indicator conditions", "", checks
                && `<div class="scan-checks">${checks}</div>`)
      + foot(`${c.bars_scanned} ${c.interval} bars`
             + (c.window ? ` · ${c.window}` : ""));
  }

  // ── the timeframe ladder ────────────────────────────────────────────
  //
  // Every rung measured the same four ways, so the rows compare. The stance
  // word takes the row's tint and the four readings stay printed beside it,
  // because a ladder of six coloured words with no numbers is a mood board.
  // ADX and RSI then get their own bar rows ACROSS the rungs, which is the
  // one comparison the per-rung line cannot make: 29 on the 15-minute and 14
  // on the daily is the finding, and it is invisible in six separate rows.
  function timeframes(c) {
    const sym = c.symbol;
    const rungs = c.rungs || [];
    // Counted by the tool, not here — the panel draws, it does not derive.
    const tally = c.tally || {};
    const stats = [
      c.price != null ? stat("Price", money(sym, c.price)) : "",
      stat("Rungs measured", rungs.length,
           "", (c.unavailable || []).length
             ? `${(c.unavailable || []).length} unavailable` : ""),
      tally.leaning
        ? stat("Leaning", cap(tally.leaning), TONE[tally.leaning] || "",
               `${tally.majority} of ${tally.of} rungs`) : "",
    ].filter(Boolean).join("");

    /* The row's own numbers, in one line, in the order the votes were taken.
     * Composed here out of the payload's figures — which is formatting, not
     * derivation: every value printed is one the tool measured, and the row
     * adds no reading of its own beyond putting them in a sentence. */
    const rows = rungs.map((r) => {
      const t = String(r.stance || "").indexOf("bull") >= 0 ? "up"
        : String(r.stance || "").indexOf("bear") >= 0 ? "down" : "";
      const bits = [
        r.rsi != null ? `RSI ${n2(sym, r.rsi)}` : "",
        r.macd_hist != null ? `MACD hist ${n2(sym, r.macd_hist)}` : "",
        r.adx != null ? `ADX ${n2(sym, r.adx)}` : "",
        r.di ? (Number(r.di.plus) > Number(r.di.minus) ? "+DI>−DI" : "−DI>+DI") : "",
        r.ema50 != null ? `${r.ema50_side === "above" ? "above" : "below"} EMA 50` : "",
      ].filter(Boolean).join(" · ");
      return `<div class="scan-read${t ? " tone-" + t : ""}">`
        + `<b class="nm">${esc(r.label)}</b>`
        + `<span class="nt">${esc(bits)}</span>`
        + `<b class="num">${esc(cap(r.stance || ""))}</b></div>`;
    }).join("");

    const adxBars = bars(rungs.filter((r) => r.adx != null).map((r) => ({
      label: r.label, value: r.adx, text: n2(sym, r.adx),
      tone: Number(r.adx) >= 25 ? "ann" : "",
    })), { tick: 25 });
    const rsiBars = bars(rungs.filter((r) => r.rsi != null).map((r) => ({
      label: r.label, value: r.rsi, text: n2(sym, r.rsi),
      tone: Number(r.rsi) >= 50 ? "up" : "down",
    })), { tick: 50, max: 100 });

    /* The pooled levels, with price standing in the column rather than
     * beside it. `intervals` is the whole point of pooling — a zone three
     * timeframes found is better evidenced than one the 5-minute saw alone,
     * and the row names them rather than grading them. */
    const lv = (c.levels || []).slice();
    const withPrice = lv.map((x) => ({
      cls: x.role === "resistance" ? "res" : "sup",
      price: `${Sym.of(sym).cur}${Sym.of(sym).num(x.lo, { maximumFractionDigits: 2 })}`
             + `–${Sym.of(sym).num(x.hi, { maximumFractionDigits: 2 })}`,
      label: cap(x.role || ""),
      note: (x.intervals || []).join(", "),
      at: Number(x.price),
    }));
    if (c.price != null) {
      withPrice.push({ cls: "current", price: money(sym, c.price),
                       label: "Current price", note: "", at: Number(c.price) });
    }
    withPrice.sort((a, b) => b.at - a.at);
    const ladder = withPrice.length > 1
      ? `<div class="scan-ladder">`
        + withPrice.map((x) => rung(x.cls, x.price, sym, x.label, x.note)).join("")
        + `</div>` : "";

    const gone = (c.unavailable || []).length
      ? callout(`Not measured: ${(c.unavailable || []).join(" · ")}`) : "";

    return `<div class="scan-stats">${stats}</div>`
      + section("Every rung, measured the same four ways", "", rows)
      + section("ADX across timeframes", "25 marks a trending phase", adxBars)
      + section("RSI across timeframes", "50 is the midline", rsiBars)
      + section("Levels the timeframes agree on", "", ladder)
      + gone;
  }

  // ── the pair comparison ─────────────────────────────────────────────
  //
  // Every measured interval is its own column, and the columns exist to be
  // read against each other: a gap that triples between the daily window and
  // the weekly one is the finding, and neither window alone contains it. The
  // benchmark rides in the return group rather than in a note, because "both
  // fell" and "both fell while the index fell too" are different answers.
  function compare(c) {
    const syms = c.symbols || [];
    const sym = syms[0];
    const cols = c.intervals || [];
    const pair = syms.length === 2;

    const stats = [];
    for (const g of (c.gaps || [])) {
      stats.push(stat(`${g.label} return gap`, signed(sym, g.gap_pp, " pp"),
                      way(g.gap_pp), g.pair));
    }
    for (const col of cols) {
      const corr = col.correlation && pair
        ? col.correlation[`${syms[0]}~${syms[1]}`] : null;
      if (corr != null) {
        stats.push(stat(`${col.label} correlation`, n2(sym, corr), "",
                        "daily returns"));
      }
    }
    if (!stats.length) {
      for (const col of cols) {
        stats.push(stat(`${col.label} bars`, col.bars ? col.bars[syms[0]] : ""));
      }
    }

    /* One group per interval, one bar per symbol, and the benchmark as the
     * last bar of the group where there is one. Grouped this way because the
     * comparison inside a window is the reading and the comparison BETWEEN
     * windows is the second one — a flat list of six bars would offer
     * neither.
     *
     * Coloured by SYMBOL, not by sign, and the same symbol keeps its colour
     * through every section. Sign colouring is the right choice on a single
     * series and the wrong one here: in a year both names can be down, and
     * two red bars under two labels is a chart the eye cannot follow across
     * three sections. The sign is not lost — it is printed, and zero is
     * drawn down the middle of the track. */
    const series = (pick, opt) => cols.map((col) => {
      const items = syms.map((s, i) => ({
        label: s, value: col[pick] ? col[pick][s] : null,
        text: signed(sym, col[pick] ? col[pick][s] : null, "%"),
        tone: "s" + Math.min(i + 1, 4),
      }));
      if (pick === "ret" && col.benchmark) {
        items.push({ label: col.benchmark.name, value: col.benchmark.ret,
                     text: signed(sym, col.benchmark.ret, "%"), tone: "bench" });
      }
      return group(col.label, col.window || "",
                   bars(items.filter((x) => x.value != null),
                        { signed: !!(opt && opt.signed) }));
    }).join("");

    /* Turnover is the one quantity that is neither signed nor comparable
     * across instruments by size alone, and it comes in two units. A table
     * keeps the unit next to every figure; a bar row would have to pick one
     * scale for rupees crore and millions of dollars at once. */
    const units = { cr: " cr", musd: " M$" };
    const haveTurn = cols.some((col) => col.turnover);
    const turnRows = haveTurn ? syms.map((s) => [
      `<b>${esc(s)}</b>`,
      ...cols.map((col) => {
        const t = col.turnover && col.turnover[s];
        return t ? esc(Sym.of(sym).num(t.value, { maximumFractionDigits: 1 })
                       + (units[t.unit] || "")) : "—";
      }),
    ]) : [];

    return `<div class="scan-stats">${stats.join("")}</div>`
      // Return takes the centred zero because a return set really does hold
      // both signs and which side of nothing a name landed on is the whole
      // reading. Drawdown and ATR never do — a drawdown is a fall and an ATR
      // is a width — so centring them would spend half the track drawing a
      // side no value can ever be on. The figures keep their own signs.
      + section("Return", "per bar window, against the index",
                series("ret", { signed: true }))
      + section("Maximum drawdown", "peak to trough, inside the window",
                series("dd", {}))
      + section("ATR volatility", "average true range as % of price",
                series("atr", {}))
      + section("Average turnover per bar", "",
                haveTurn ? grid(["", ...cols.map((x) => x.label)], turnRows) : "")
      + ((c.unavailable || []).length
         ? callout(`Not measured: ${(c.unavailable || []).join(" · ")}`) : "");
  }

  // ── why it moved ────────────────────────────────────────────────────
  //
  // The size question FIRST, because a move inside its own normal range
  // needs no story and a panel that opened with a cause would have conceded
  // there was one. Then where inside the session it happened, then the
  // ladder price is standing on, and last what was looked for — including
  // what was looked for and not found, which is the half a reader would
  // otherwise assume was never checked.
  function move(c) {
    const sym = c.symbol;
    const s = c.stats || {};
    const win = c.window || {};
    const stats = [
      s.move_pct != null
        ? stat(win.sessions === 1 ? "Session move" : "Window move",
               signed(sym, s.move_pct, "%"), way(s.move_pct),
               win.sessions ? `${win.sessions} session${win.sessions === 1 ? "" : "s"}` : "")
        : "",
      s.typical_abs_move_pct != null
        ? stat("Typical move", `${n2(sym, s.typical_abs_move_pct)}%`, "",
               "this stock's own median") : "",
      s.abs_percentile != null
        ? stat("Size percentile", `${s.abs_percentile}`, "",
               "of its own past moves") : "",
      s.vol_vs_20d_avg != null
        ? stat("Volume vs 20d avg", `${n2(sym, s.vol_vs_20d_avg)}×`,
               Number(s.vol_vs_20d_avg) >= 1.5 ? "ann" : "") : "",
    ].filter(Boolean).join("");

    const segs = bars((c.segments || []).map((x) => ({
      label: x.name, value: x.pct, text: signed(sym, x.pct, "%"), tone: way(x.pct),
    })), { signed: true });

    const ladder = (c.ladder || []).length > 1
      ? `<div class="scan-ladder">` + (c.ladder || []).map((r) => rung(
          r.role === "current" ? "current" : r.role === "crossed" ? "crossed"
            : r.role === "resistance" ? "res" : "sup",
          money(sym, r.price), sym, r.label,
          r.touches != null ? `${r.touches} touches` : "")).join("")
        + `</div>` : "";

    /* The index's share, as rows rather than a chart: three quantities that
     * are one arithmetic sentence — the index moved this much, beta says
     * that much of the move was owed to it, and the residual is the part it
     * does not account for. A bar row would invite them to be compared by
     * size, which is not what they are. */
    const ix = c.index;
    const idx = ix ? [
      row("", null, ix.name, "moved", signed(sym, ix.index_pct, "%")),
      ix.expected_pct != null
        ? row("", null, `Beta ${n2(sym, ix.beta)}`, "would explain",
              signed(sym, ix.expected_pct, "%")) : "",
      ix.residual_pct != null
        ? row("", null, "Residual", "the part it does not explain",
              signed(sym, ix.residual_pct, "%")) : "",
    ].filter(Boolean).join("") : "";

    return `<div class="scan-stats">${stats}</div>`
      + section("Return by session segment",
                c.segments_of ? `on ${c.segments_of}` : "", segs)
      + section("The ladder price is standing on", "", ladder)
      + section("How much of it was the index", "", idx)
      + section("What was looked for", "", chips(c.context))
      + foot(win.from && win.to
             ? (win.from === win.to ? win.from : `${win.from} → ${win.to}`) : "");
  }

  const RENDER = { patterns, trend, indicators, confirmation, timeframes,
                   compare, move, workflow_draft: workflowDraft,
                   strategy_backtest: strategyBacktest,
                   strategy_basket: strategyBasket,
                   option_strategy: optionStrategy,
                   option_chain: optionChain,
                   quant_result: quantResult };

  return {
    /** A card object → an element for the thread, or null when this build has
     *  no renderer for that kind. Null rather than a placeholder: a panel
     *  reading "unsupported card" tells the user about our deploy schedule
     *  and nothing about their chart. */
    render(card) {
      if (!card || !RENDER[card.kind]) return null;
      // The thread card is the backend-grounded pattern inventory. Mirror it
      // into the adjacent drawer so the chart can stay quiet while every
      // detection remains inspectable. This is data handoff only: the drawer
      // never invents, ranks or modifies detector values.
      if (card.kind === "patterns" && window.PatternDrawer) {
        window.PatternDrawer.setPatterns(card);
      }
      let html;
      try {
        html = RENDER[card.kind](card);
      } catch (e) {
        console.warn("[charto] card render failed", card, e);
        return null;      // a broken panel must never cost the reply
      }
      if (!html) return null;
      const box = document.createElement("div");
      box.className = "scan";
      box.dataset.card = card.kind;
      box.innerHTML = html;
      const more = box.querySelector("[data-more]");
      if (more) {
        more.addEventListener("click", () => {
          box.querySelector(".scan-rows.capped").classList.remove("capped");
          more.remove();
        });
      }
      if (box.querySelector("[data-wf]")) wireDraft(box);
      if (box.querySelector("[data-bt-chart]")) wireOnChart(box, card);
      return box;
    },
  };
})();
