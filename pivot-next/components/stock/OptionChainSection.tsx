"use client";

/**
 * Options on the stock page: the option chain of an NSE F&O stock or index.
 *
 * The data is Charto's (`/options/chain`, charto/data/options.py) — the same
 * payload the chart's Option chain widget and the chat's card draw — so the
 * page, the workspace and the conversation quote one set of numbers. NSE's
 * end-of-day F&O file, re-quoted live per strike when the Kite session is up;
 * the heading says which, and a close is never labelled live.
 *
 * A symbol with no options renders nothing: the section, and its link in the
 * page's section nav (OptionsNavLink), appear only when there is a chain.
 */

import * as React from "react";
import { SECTION_GAP, PanelSkeleton } from "./chrome";

type Side = {
  ltp: number;
  chg_pct?: number;
  oi: number;
  oi_chg: number;
  volume: number;
  iv?: number;
  delta?: number;
};
type Row = { strike: number; ce?: Side; pe?: Side };
type Level = { strike: number; oi: number; oi_chg: number };
export type OptionChain = {
  available: boolean;
  reason?: string;
  symbol: string;
  index: boolean;
  source: "kite" | "nse_eod";
  as_of_label: string;
  expiry: string;
  dte: number;
  expiries: { date: string; dte: number; monthly: boolean }[];
  lot_size: number;
  spot: number;
  forward: number;
  forward_basis: "future" | "parity" | "spot";
  atm_strike: number;
  atm_iv: number | null;
  expected_move: { abs: number; pct: number } | null;
  one_sd_move: number | null;
  pcr_oi: number | null;
  pcr_volume: number | null;
  max_pain: number | null;
  totals: { call_oi: number; put_oi: number; call_oi_chg: number; put_oi_chg: number };
  walls: { call: Level[]; put: Level[] };
  oi_added: { call: Level[]; put: Level[] };
  rows: Row[];
  strikes_total: number;
};

// One request per symbol+expiry shared by the nav link and the section.
const inflight = new Map<string, Promise<OptionChain | null>>();
function loadChain(symbol: string, expiry = ""): Promise<OptionChain | null> {
  const key = `${symbol}|${expiry}`;
  const hit = inflight.get(key);
  if (hit) return hit;
  const p = fetch(`/options/chain?symbol=${encodeURIComponent(symbol)}&strikes=40${expiry ? `&expiry=${encodeURIComponent(expiry)}` : ""}`)
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null) as Promise<OptionChain | null>;
  inflight.set(key, p);
  setTimeout(() => inflight.delete(key), 60_000);
  return p;
}

const fin = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
const px = (v: unknown) => (fin(v) ? v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "—");
const strike = (v: unknown) => (fin(v) ? v.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "—");
function qty(v: unknown, signed = false): string {
  if (!fin(v)) return "—";
  const a = Math.abs(v), sg = v < 0 ? "−" : signed && v > 0 ? "+" : "";
  if (a >= 1e7) return `${sg}${(a / 1e7).toFixed(2)}Cr`;
  if (a >= 1e5) return `${sg}${(a / 1e5).toFixed(2)}L`;
  if (a >= 1e3) return `${sg}${(a / 1e3).toFixed(1)}K`;
  return `${sg}${Math.round(a)}`;
}
const pct = (v: unknown, d = 1) => (fin(v) ? `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(d)}%` : "");
const tone = (v: unknown) => (fin(v) && v > 0 ? "var(--color-profit)" : fin(v) && v < 0 ? "var(--color-loss)" : "var(--text-tertiary)");
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const day = (iso: string) => { const [, m = "", d = ""] = iso.split("-"); return `${+d} ${MON[+m - 1] ?? ""}`; };

/** "Options" in the section nav, only for a symbol that has a chain. */
export function OptionsNavLink({ symbol }: { symbol: string }): React.ReactElement | null {
  const [ok, setOk] = React.useState(false);
  React.useEffect(() => {
    let dead = false;
    setOk(false);
    loadChain(symbol).then((c) => { if (!dead) setOk(!!c?.available); });
    return () => { dead = true; };
  }, [symbol]);
  return ok ? <a href="#stock-options">Options</a> : null;
}

export function OptionChainSection({ symbol }: { symbol: string }): React.ReactElement | null {
  const [expiry, setExpiry] = React.useState("");
  const [chain, setChain] = React.useState<OptionChain | null | undefined>(undefined);
  const [view, setView] = React.useState<"chain" | "oi">("chain");
  const [all, setAll] = React.useState(false);

  React.useEffect(() => { setExpiry(""); setAll(false); }, [symbol]);
  React.useEffect(() => {
    let dead = false;
    loadChain(symbol, expiry).then((c) => { if (!dead) setChain(c); });
    return () => { dead = true; };
  }, [symbol, expiry]);

  const rows = React.useMemo(() => {
    if (!chain || !chain.available) return [];
    if (all) return chain.rows;
    const i = Math.max(0, chain.rows.findIndex((r) => r.strike === chain.atm_strike));
    return chain.rows.slice(Math.max(0, i - 10), i + 11);
  }, [chain, all]);

  if (chain === null || (chain && !chain.available)) return null;
  const c = chain;

  return (
    <section id="stock-options" aria-label="Options" className="pv-oc" style={{ marginTop: SECTION_GAP, padding: "0 20px", scrollMarginTop: 90 }}>
      <div className="pv-oc-head">
        <div>
          <h2>Options</h2>
          <div className="pv-oc-sub">
            {c ? `${c.as_of_label} · ${c.lot_size} lot` : "Option chain"}
          </div>
        </div>
        <div className="pv-oc-views" role="tablist" aria-label="Options view">
          <button type="button" role="tab" aria-selected={view === "chain"} onClick={() => setView("chain")}>Chain</button>
          <button type="button" role="tab" aria-selected={view === "oi"} onClick={() => setView("oi")}>Open interest</button>
        </div>
      </div>

      {!c ? <PanelSkeleton rows={8} /> : <>
        <div className="pv-oc-exps" role="tablist" aria-label="Expiry">
          <span className="pv-oc-exps-label">Expiry</span>
          {c.expiries.map((e) => (
            <button key={e.date} type="button" role="tab" aria-selected={e.date === c.expiry}
              aria-label={`${day(e.date)}, ${e.dte <= 0 ? "expires today" : `${e.dte} days to expiry`}${e.monthly ? ", monthly expiry" : ""}`}
              className={e.date === c.expiry ? "on" : ""} onClick={() => setExpiry(e.date)}>
              <b>{day(e.date)}</b><span>{e.dte <= 0 ? "today" : `${e.dte}d`}</span>
            </button>
          ))}
        </div>

        <div className="pv-oc-stats">
          <Stat label={c.index ? "Spot" : "Price"} value={`₹${px(c.spot)}`} />
          <Stat label="Put-call ratio" value={fin(c.pcr_oi) ? c.pcr_oi.toFixed(2) : "—"} />
          <Stat label="Max pain" value={strike(c.max_pain)} />
          <Stat label="ATM implied vol" value={fin(c.atm_iv) ? `${c.atm_iv.toFixed(1)}%` : "—"} />
          <Stat label="Expected move" value={c.expected_move ? `±${strike(Math.round(c.expected_move.abs))}` : "—"} />
        </div>

        <OiSplit c={c} />

        <div className="pv-oc-walls">
          <span>Call wall <b>{c.walls.call.map((w) => strike(w.strike)).join(" · ") || "—"}</b></span>
          <span>Put wall <b>{c.walls.put.map((w) => strike(w.strike)).join(" · ") || "—"}</b></span>
        </div>

        {view === "chain" ? <ChainTable c={c} rows={rows} /> : <OiProfile c={c} rows={rows} />}

        <div className="pv-oc-foot">
          {c.strikes_total > rows.length || all ? (
            <button type="button" onClick={() => setAll((x) => !x)}>
              {all ? "Show strikes near the money" : `Show all ${c.strikes_total} strikes`}
            </button>
          ) : <span />}
          <a href={`/chart-app/index.html?symbol=${encodeURIComponent(c.symbol)}&panel=options`}
            onClick={(e) => {
              // inside the app: the Chart tab, with the Option chain widget open
              // beside it; a new-tab click keeps the plain link
              if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
              e.preventDefault();
              window.dispatchEvent(new CustomEvent("pivot:open-chart", { detail: { symbol: c.symbol, panel: "options" } }));
            }}>Open chart ↗</a>
        </div>
      </>}
      <style>{CSS}</style>
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }): React.ReactElement {
  return <div className="pv-oc-stat"><span>{label}</span><b>{value}</b></div>;
}

function OiSplit({ c }: { c: OptionChain }): React.ReactElement {
  const t = c.totals, total = (t.call_oi || 0) + (t.put_oi || 0);
  const share = total ? (t.call_oi / total) * 100 : 50;
  return (
    <div className="pv-oc-split" title="Total call and put open interest in this expiry">
      <div className="bar"><i style={{ width: `${share.toFixed(1)}%` }} /></div>
      <div className="lab">
        <span>Call OI <b>{qty(t.call_oi)}</b> <em style={{ color: tone(t.call_oi_chg) }}>{qty(t.call_oi_chg, true)}</em></span>
        <span>Put OI <b>{qty(t.put_oi)}</b> <em style={{ color: tone(t.put_oi_chg) }}>{qty(t.put_oi_chg, true)}</em></span>
      </div>
    </div>
  );
}

function ChainTable({ c, rows }: { c: OptionChain; rows: Row[] }): React.ReactElement {
  const top = rows.reduce((m, r) => Math.max(m, r.ce?.oi ?? 0, r.pe?.oi ?? 0), 0) || 1;
  const callWall = c.walls.call[0]?.strike, putWall = c.walls.put[0]?.strike;
  const out: React.ReactNode[] = [];
  let spotDrawn = false;
  rows.forEach((r, i) => {
    if (!spotDrawn && r.strike > c.spot) {
      spotDrawn = true;
      if (i > 0) out.push(<div key="spot" className="pv-oc-spot"><span>{c.index ? "Spot" : "Price"} ₹{px(c.spot)}</span></div>);
    }
    const itmC = r.strike < c.spot, itmP = r.strike > c.spot;
    out.push(
      <div key={r.strike} className={`pv-oc-row${r.strike === c.atm_strike ? " atm" : ""}`}>
        <Cells q={r.ce} side="ce" top={top} itm={itmC} wall={r.strike === callWall} />
        <span className="k">{strike(r.strike)}{r.strike === c.max_pain ? <i title="Max pain">MP</i> : null}</span>
        <Cells q={r.pe} side="pe" top={top} itm={itmP} wall={r.strike === putWall} />
      </div>,
    );
  });
  return (
    <div className="pv-oc-table">
      <div className="pv-oc-sides"><span>Calls</span><span>Puts</span></div>
      <div className="pv-oc-row th">
        <span className="c chg">OI chg</span><span className="c oi">OI</span><span className="c iv">IV</span><span className="c ltp">LTP</span>
        <span className="k">Strike</span>
        <span className="c ltp">LTP</span><span className="c iv">IV</span><span className="c oi">OI</span><span className="c chg">OI chg</span>
      </div>
      {out}
    </div>
  );
}

function Cells({ q, side, top, itm, wall }: { q?: Side; side: "ce" | "pe"; top: number; itm: boolean; wall: boolean }): React.ReactElement {
  const cls = `${side}${itm ? " itm" : ""}${wall ? " wall" : ""}`;
  const cells = [
    <span key="chg" className={`c chg ${cls}`} style={{ color: tone(q?.oi_chg) }}>{q ? (q.oi_chg ? qty(q.oi_chg, true) : "0") : ""}</span>,
    <span key="oi" className={`c oi ${cls}`}>{q ? <><i style={{ width: `${Math.min(100, (q.oi / top) * 100).toFixed(1)}%` }} /><b>{qty(q.oi)}</b></> : null}</span>,
    <span key="iv" className={`c iv ${cls}`}>{q && fin(q.iv) ? q.iv.toFixed(1) : q ? "—" : ""}</span>,
    <span key="ltp" className={`c ltp ${cls}`}>{q ? <><b>{px(q.ltp)}</b>{fin(q.chg_pct) ? <em style={{ color: tone(q.chg_pct) }}>{pct(q.chg_pct)}</em> : null}</> : null}</span>,
  ];
  return <>{side === "ce" ? cells : cells.reverse()}</>;
}

function OiProfile({ c, rows }: { c: OptionChain; rows: Row[] }): React.ReactElement {
  const top = rows.reduce((m, r) => Math.max(m, (r.ce?.oi ?? 0) + Math.max(0, -(r.ce?.oi_chg ?? 0)), (r.pe?.oi ?? 0) + Math.max(0, -(r.pe?.oi_chg ?? 0))), 0) || 1;
  const bar = (q: Side | undefined, side: "ce" | "pe") => {
    if (!q) return <span className={`b ${side}`} />;
    const w = (q.oi / top) * 100, ch = q.oi_chg;
    const add = ch > 0 ? Math.min(w, (ch / top) * 100) : 0, cut = ch < 0 ? (-ch / top) * 100 : 0;
    const at = side === "ce" ? "right" : "left";
    return (
      <span className={`b ${side}`} title={`OI ${qty(q.oi)} · ${ch >= 0 ? "added" : "cut"} ${qty(Math.abs(ch))} today`}>
        <i className="tot" style={{ width: `${w.toFixed(2)}%` }} />
        {add ? <i className="add" style={{ width: `${add.toFixed(2)}%`, [at]: `${(w - add).toFixed(2)}%` }} /> : null}
        {cut ? <i className="cut" style={{ width: `${cut.toFixed(2)}%`, [at]: `${w.toFixed(2)}%` }} /> : null}
        <em>{qty(q.oi)}</em>
      </span>
    );
  };
  const out: React.ReactNode[] = [];
  let spotDrawn = false;
  rows.forEach((r, i) => {
    if (!spotDrawn && r.strike > c.spot) {
      spotDrawn = true;
      if (i > 0) out.push(<div key="spot" className="pv-oc-spot"><span>{c.index ? "Spot" : "Price"} ₹{px(c.spot)}</span></div>);
    }
    out.push(
      <div key={r.strike} className={`pv-oc-prow${r.strike === c.atm_strike ? " atm" : ""}`}>
        {bar(r.ce, "ce")}
        <span className="k">{strike(r.strike)}{r.strike === c.max_pain ? <i>MP</i> : null}</span>
        {bar(r.pe, "pe")}
      </div>,
    );
  });
  return (
    <div className="pv-oc-prof">
      <div className="pv-oc-sides"><span>Call OI</span><span>Put OI</span></div>
      <div className="pv-oc-legend"><span><i className="tot" />Open interest</span><span><i className="add" />Added today</span><span><i className="cut" />Cut today</span></div>
      {out}
    </div>
  );
}

const CSS = `
.pv-oc { --oc-ce: var(--color-loss); --oc-pe: var(--color-profit); font-variant-numeric: tabular-nums;
  --oc-ce-soft: color-mix(in srgb, var(--color-loss) 14%, transparent); --oc-pe-soft: color-mix(in srgb, var(--color-profit) 14%, transparent);
  --oc-itm: color-mix(in srgb, #f5a524 7%, transparent); }
.pv-oc-head { display: flex; align-items: flex-end; justify-content: space-between; gap: 14px; flex-wrap: wrap; margin-bottom: 18px; }
.pv-oc-head h2 { margin: 0; font-family: var(--font-ui); font-size: 21px; font-weight: 600; letter-spacing: -0.022em; color: var(--text-primary); }
.pv-oc-sub { margin-top: 3px; font-size: 11.5px; color: var(--text-primary); }
.pv-oc-views { display: inline-flex; align-items: center; gap: 22px; }
.pv-oc-views button { padding: 0 0 5px; border: 0; border-bottom: 1.5px solid transparent; background: transparent; color: var(--text-primary); cursor: pointer; font: inherit; font-size: 12.5px; }
.pv-oc-views button[aria-selected="true"] { border-bottom-color: var(--text-primary); font-weight: 650; }
.pv-oc-exps { display: flex; align-items: baseline; gap: 18px; overflow-x: auto; scrollbar-width: none; padding-bottom: 2px; }
.pv-oc-exps::-webkit-scrollbar { display: none; }
.pv-oc-exps-label { flex: none; font-size: 11px; font-weight: 600; color: var(--text-primary); }
.pv-oc-exps button { flex: none; display: inline-flex; align-items: baseline; gap: 5px; padding: 0 0 4px; border: 0; border-bottom: 1.5px solid transparent; background: transparent; color: var(--text-primary); cursor: pointer; font: inherit; }
.pv-oc-exps button b { font-size: 12.5px; font-weight: 500; }
.pv-oc-exps button span { font-size: 10.5px; color: var(--text-primary); }
.pv-oc-exps button.on { border-bottom-color: var(--text-primary); }
.pv-oc-exps button.on b { font-weight: 700; }
.pv-oc-stats { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 20px; margin-top: 22px; }
.pv-oc-stat { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.pv-oc-stat span { font-size: 10.5px; font-weight: 600; letter-spacing: .045em; text-transform: uppercase; color: var(--text-primary); }
.pv-oc-stat b { font-size: 18px; font-weight: 650; letter-spacing: -0.02em; color: var(--text-primary); white-space: nowrap; }
.pv-oc-split { margin-top: 20px; }
.pv-oc-split .bar { display: flex; height: 3px; overflow: hidden; background: var(--oc-pe); }
.pv-oc-split .bar i { background: var(--oc-ce); }
.pv-oc-split .lab { display: flex; justify-content: space-between; margin-top: 7px; font-size: 11.5px; color: var(--text-primary); }
.pv-oc-split b { color: var(--text-primary); font-weight: 600; } .pv-oc-split em { font-style: normal; }
.pv-oc-walls { display: flex; flex-wrap: wrap; gap: 4px 24px; margin-top: 10px; font-size: 11.5px; color: var(--text-primary); }
.pv-oc-walls b { color: var(--text-primary); font-weight: 500; }
.pv-oc-table, .pv-oc-prof { margin-top: 16px; font-size: 12.5px; }
.pv-oc-sides { display: flex; justify-content: space-between; font-size: 10.5px; font-weight: 650; letter-spacing: .07em; text-transform: uppercase; padding-bottom: 4px; }
.pv-oc-sides span:first-child { color: var(--oc-ce); } .pv-oc-sides span:last-child { color: var(--oc-pe); }
.pv-oc-row { display: grid; grid-template-columns: minmax(0,.9fr) minmax(0,1.5fr) minmax(0,.7fr) minmax(0,1fr) 92px minmax(0,1fr) minmax(0,.7fr) minmax(0,1.5fr) minmax(0,.9fr);
  align-items: stretch; min-height: 34px; border-bottom: 1px solid var(--glass-border); }
.pv-oc-table > .pv-oc-row:last-child { border-bottom: 0; }
.pv-oc-row.th { min-height: 28px; font-size: 10.5px; color: var(--text-tertiary); border-top: 1px solid var(--glass-border); }
.pv-oc-row.th > span { display: flex; align-items: center; }
.pv-oc-row .c { position: relative; display: flex; align-items: center; justify-content: flex-end; gap: 6px; padding: 0 10px; white-space: nowrap; min-width: 0; }
.pv-oc-row .c.pe, .pv-oc-row.th .c:nth-child(n+6) { justify-content: flex-start; }
.pv-oc-row .c.itm { background: var(--oc-itm); }
.pv-oc-row .c.iv { color: var(--text-secondary); }
.pv-oc-row .c.oi i { position: absolute; top: 7px; bottom: 7px; border-radius: 3px; }
.pv-oc-row .c.oi.ce i { right: 0; background: var(--oc-ce-soft); } .pv-oc-row .c.oi.pe i { left: 0; background: var(--oc-pe-soft); }
.pv-oc-row .c.oi.ce.wall i { background: color-mix(in srgb, var(--oc-ce) 32%, transparent); }
.pv-oc-row .c.oi.pe.wall i { background: color-mix(in srgb, var(--oc-pe) 32%, transparent); }
.pv-oc-row .c.oi b { position: relative; font-weight: 500; color: var(--text-primary); }
.pv-oc-row .c.ltp { flex-direction: column; align-items: flex-end; justify-content: center; gap: 0; line-height: 1.2; }
.pv-oc-row .c.ltp.pe { align-items: flex-start; }
.pv-oc-row.th .c.ltp { flex-direction: row; align-items: center; justify-content: flex-end; }
.pv-oc-row.th .c.ltp:nth-child(n+6) { justify-content: flex-start; }
.pv-oc-row .c.ltp b { font-weight: 600; color: var(--text-primary); } .pv-oc-row .c.ltp em { font-style: normal; font-size: 10.5px; }
.pv-oc-row .k { display: flex; align-items: center; justify-content: center; gap: 5px; font-weight: 600; color: var(--text-primary);
  background: color-mix(in srgb, var(--text-primary) 5%, transparent); }
.pv-oc-row.th .k { background: transparent; font-weight: 500; color: var(--text-tertiary); }
.pv-oc-row.atm .k { background: var(--text-primary); color: var(--bg-primary); }
.pv-oc-row .k i, .pv-oc-prow .k i { font-style: normal; font-size: 9px; font-weight: 700; padding: 0 4px; border-radius: 4px; line-height: 14px; background: #f5a524; color: #000; }
.pv-oc-spot { position: relative; display: flex; justify-content: center; align-items: center; height: 20px; }
.pv-oc-spot::before { content: ""; position: absolute; left: 0; right: 0; top: 50%; border-top: 1.5px dashed var(--text-primary); opacity: .6; }
.pv-oc-spot span { position: relative; padding: 0 10px; border-radius: 999px; font-size: 10.5px; font-weight: 600; line-height: 16px; background: var(--text-primary); color: var(--bg-primary); }
.pv-oc-prow { display: grid; grid-template-columns: 1fr 92px 1fr; align-items: center; height: 26px; }
.pv-oc-prow .k { text-align: center; font-weight: 600; font-size: 12px; color: var(--text-primary); display: flex; justify-content: center; align-items: center; gap: 4px; }
.pv-oc-prow.atm .k { background: var(--text-primary); color: var(--bg-primary); border-radius: 6px; height: 20px; }
.pv-oc-prof .b { position: relative; height: 15px; }
.pv-oc-prof .b i { position: absolute; top: 0; bottom: 0; border-radius: 3px; }
.pv-oc-prof .b.ce i.tot { right: 0; background: var(--oc-ce-soft); } .pv-oc-prof .b.pe i.tot { left: 0; background: var(--oc-pe-soft); }
.pv-oc-prof .b.ce i.add { background: var(--oc-ce); } .pv-oc-prof .b.pe i.add { background: var(--oc-pe); }
.pv-oc-prof .b i.cut, .pv-oc-legend i.cut { background: repeating-linear-gradient(135deg, transparent 0 3px, color-mix(in srgb, var(--text-tertiary) 55%, transparent) 3px 4px);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--text-tertiary) 40%, transparent); }
.pv-oc-prof .b em { position: absolute; top: 0; line-height: 15px; font-style: normal; font-size: 10.5px; color: var(--text-tertiary); }
.pv-oc-prof .b.ce em { left: 0; } .pv-oc-prof .b.pe em { right: 0; }
.pv-oc-legend { display: flex; flex-wrap: wrap; gap: 4px 16px; padding-bottom: 8px; font-size: 11px; color: var(--text-tertiary); }
.pv-oc-legend i.tot { background: color-mix(in srgb, var(--text-tertiary) 35%, transparent); } .pv-oc-legend i.add { background: var(--text-tertiary); }
.pv-oc-foot { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-top: 14px; font-size: 12.5px; }
.pv-oc-foot button { border: 0; background: transparent; padding: 0; cursor: pointer; font: inherit; color: var(--text-primary); font-weight: 500; text-decoration: underline; text-underline-offset: 3px; text-decoration-color: var(--glass-border); }
.pv-oc-foot a { color: var(--text-primary); font-weight: 500; text-decoration: none; }
.pv-oc-foot a:hover, .pv-oc-foot button:hover { opacity: .75; }
@media (max-width: 900px) {
  .pv-oc-stats { grid-template-columns: repeat(3, minmax(0, 1fr)); row-gap: 18px; }
}
@media (max-width: 720px) {
  .pv-oc-row { grid-template-columns: minmax(0,1.4fr) minmax(0,1fr) 76px minmax(0,1fr) minmax(0,1.4fr); }
  .pv-oc-row .c.chg, .pv-oc-row .c.iv { display: none; }
  .pv-oc-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); row-gap: 16px; }
  .pv-oc-prow { grid-template-columns: 1fr 70px 1fr; }
  .pv-oc-foot { align-items: flex-start; flex-direction: column; }
}
`;
