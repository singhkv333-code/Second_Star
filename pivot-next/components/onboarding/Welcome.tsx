"use client";

/**
 * Welcome — three cards shown once to a new account, before the product tour.
 *
 *   1. A welcome.
 *   2. Their watchlist: popular names to tap, then the rest of the chart's
 *      universe loading as they scroll, and a search. What they pick
 *      replaces watchlist 1 on Home.
 *   3. How Pivot looks: light, dark, or one of the workspace palettes.
 *      Choices apply at once, through the same runtime as Workspace colors.
 *
 * Each card is a large rounded square: a painting on top (scripts/welcome-art.py)
 * in Pivot's dark palette (an oil painting, sand, a dot print) under moving film
 * grain, and the choice below it. "Show me around" hands over to the tour; Skip ends
 * here and keeps the defaults.
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowLeft, Check, Plus, Search, X } from "lucide-react";
import { CompanyLogo } from "@/components/CompanyLogo";
import { getCompanyLogos, getMe } from "@/lib/api";
import { fallback, loadUniverse, searchInstruments, type Instrument, type InstrumentKind } from "@/lib/instruments";
import { getMarketExplore } from "@/lib/markets";
import { isError } from "@/lib/types";
import { WELCOME_ART } from "@/lib/publicAssets";
import { PivotWordLogo } from "@/components/brand/PivotWordLogo";
import { setWatchlist, useWatchlists } from "@/lib/watchlists";
import type { WorkspacePalette } from "@/lib/workspace-theme";
import "./welcome.css";

/* Popular picks by kind. "All" mixes the headline names of each. */
const GROUPS = {
  All: ["NIFTY 50", "SENSEX", "BTCUSDT", "ETHUSDT", "GOLD", "CRUDEOIL", "RELIANCE", "HDFCBANK", "TCS", "INFY", "ICICIBANK", "SBIN"],
  Stocks: ["RELIANCE", "HDFCBANK", "TCS", "INFY", "ICICIBANK", "SBIN", "BHARTIARTL", "ITC", "LT", "BAJFINANCE", "HINDUNILVR", "MARUTI"],
  Indices: ["NIFTY 50", "NIFTY BANK", "SENSEX", "NIFTY NEXT 50", "NIFTY MIDCAP 100", "NIFTY IT", "NIFTY FIN SERVICE", "INDIA VIX"],
  Crypto: ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT"],
  Commodities: ["GOLD", "SILVER", "CRUDEOIL", "NATURALGAS", "COPPER", "ZINC", "ALUMINIUM", "USDINR"],
} as const;
type Group = keyof typeof GROUPS;

/** What each kind tab lists after its popular picks. */
const KINDS: Record<Group, InstrumentKind[] | null> = {
  All: null,
  Stocks: ["stock"],
  Indices: ["index"],
  Crypto: ["crypto"],
  Commodities: ["commodity", "currency"],
};
/** Tiles added each time the list nears its end. */
const PAGE = 24;

const ART = [WELCOME_ART.sunset, WELCOME_ART.orb, WELCOME_ART.moonlight];
const LAST = 2;

type Look = "light" | "dark" | "custom";

function firstName(name?: string | null): string {
  return (name || "").trim().split(/\s+/)[0] || "";
}

function palettes(): WorkspacePalette[] {
  return window.PivotWorkspaceTheme?.presets ?? [];
}

/** A small drawing of the workspace in a palette: frame, panel, candles. */
function Preview({ p }: { p: WorkspacePalette }): React.ReactElement {
  // x, body top, wick top, body bottom, rising
  const candles: [number, number, number, number, boolean][] = [
    [8, 30, 18, 36, true], [18, 22, 12, 30, false], [28, 20, 14, 26, true],
    [38, 16, 8, 24, true], [48, 20, 14, 28, false], [58, 10, 4, 18, true],
  ];
  return (
    <svg viewBox="0 0 72 46" className="welcome-preview" aria-hidden="true">
      <rect width="72" height="46" rx="7" fill={p.chrome} />
      <rect x="9" y="5" width="59" height="37" rx="4" fill={p.surface} stroke={p.border} strokeWidth=".8" />
      <rect x="3" y="7" width="3" height="3" rx="1" fill={p.border} />
      <rect x="3" y="13" width="3" height="3" rx="1" fill={p.border} />
      <rect x="3" y="19" width="3" height="3" rx="1" fill={p.accent} />
      {candles.map(([x, top, high, bottom, up]) => (
        <g key={x} transform="translate(6 2)">
          <line x1={x} x2={x} y1={high} y2={bottom + 5} stroke={up ? p.up : p.down} strokeWidth=".8" />
          <rect x={x - 2} y={top} width="4" height={Math.max(3, bottom - top)} rx=".6" fill={up ? p.up : p.down} />
        </g>
      ))}
    </svg>
  );
}

export function Welcome({ onDone }: { onDone: (tour: boolean) => void }): React.ReactElement | null {
  const [step, setStep] = useState(0);
  const [name, setName] = useState("");
  const titleId = useId();
  const primary = useRef<HTMLButtonElement>(null);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  useEffect(() => {
    getMe().then((r) => { if (!isError(r)) setName(firstName(r.data.full_name)); }).catch(() => {});
  }, []);

  // ── Watchlist ───────────────────────────────────────────
  const wl = useWatchlists();
  const [picked, setPicked] = useState<string[] | null>(null);
  const chosen = useMemo(() => picked ?? wl.lists[0]?.tickers ?? [], [picked, wl]);
  const [known, setKnown] = useState<Map<string, Instrument>>(new Map());
  const [stockLogos, setStockLogos] = useState<Record<string, string | null>>({});
  // Stocks by market cap, largest first, when the Markets data answers.
  const [rank, setRank] = useState<Map<string, number>>(new Map());
  const [group, setGroup] = useState<Group>("All");
  const [count, setCount] = useState(PAGE);
  const scroller = useRef<HTMLDivElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Instrument[]>([]);
  const [searching, setSearching] = useState(false);
  const toggle = useCallback((symbol: string): void => {
    setPicked((cur) => {
      const list = cur ?? wl.lists[0]?.tickers ?? [];
      return list.includes(symbol) ? list.filter((s) => s !== symbol) : [...list, symbol];
    });
  }, [wl]);

  useEffect(() => {
    void loadUniverse().then(setKnown);
    void getCompanyLogos([...GROUPS.Stocks]).then(setStockLogos);
    getMarketExplore().then((r) => {
      if (!isError(r)) setRank(new Map(r.data.stocks.map((x, i) => [x.symbol, i])));
    }).catch(() => {});
  }, []);
  const describe = useCallback((symbol: string): Instrument => {
    const i = known.get(symbol) ?? fallback(symbol);
    return i.logo || !stockLogos[symbol] ? i : { ...i, logo: stockLogos[symbol] ?? null };
  }, [known, stockLogos]);

  // The tab's popular picks, then everything else of its kind: indices,
  // commodities, crypto, then stocks, the largest companies first.
  const catalog = useMemo((): string[] => {
    const kinds = KINDS[group];
    const order: InstrumentKind[] = ["index", "commodity", "currency", "crypto", "stock"];
    const popular = new Set<string>(GROUPS[group]);
    const rest = [...known.values()]
      .filter((i) => !popular.has(i.symbol) && (!kinds || kinds.includes(i.kind)))
      .sort((a, b) =>
        order.indexOf(a.kind) - order.indexOf(b.kind)
        || (rank.get(a.symbol) ?? 1e9) - (rank.get(b.symbol) ?? 1e9)
        || Number(!a.logo) - Number(!b.logo)
        || a.symbol.localeCompare(b.symbol))
      .map((i) => i.symbol);
    return [...GROUPS[group], ...rest];
  }, [group, known, rank]);
  const visible = catalog.slice(0, count);

  useEffect(() => { setCount(PAGE); scroller.current?.scrollTo({ top: 0 }); }, [group]);

  // Load the next page as the end of the list comes into view.
  useEffect(() => {
    const root = scroller.current, end = sentinel.current;
    if (!root || !end || count >= catalog.length) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting)) setCount((n) => n + PAGE);
    }, { root, rootMargin: "0px 0px 240px 0px" });
    io.observe(end);
    return () => io.disconnect();
  }, [count, catalog.length, step, query]);

  // Logos for the stocks on screen that the universe has no art for.
  useEffect(() => {
    const want = visible.filter((x) => !(x in stockLogos) && !known.get(x)?.logo && (known.get(x)?.kind ?? "stock") === "stock");
    if (!want.length) return;
    let alive = true;
    void getCompanyLogos(want).then((m) => {
      if (alive) setStockLogos((cur) => ({ ...cur, ...Object.fromEntries(want.map((x) => [x, m[x] ?? null])) }));
    });
    return () => { alive = false; };
  }, [visible, stockLogos, known]);

  useEffect(() => {
    const q = query.trim();
    if (!q) { setResults([]); setSearching(false); return; }
    setSearching(true);
    // A slower, older search must never replace the answer to a newer one.
    let current = true;
    const t = window.setTimeout(() => {
      searchInstruments(q, 10).then((r) => {
        if (!current) return;
        setResults(r);
        setSearching(false);
      }).catch(() => { if (current) { setResults([]); setSearching(false); } });
    }, 180);
    return () => { current = false; window.clearTimeout(t); };
  }, [query]);

  // ── Look ────────────────────────────────────────────────
  const [presets, setPresets] = useState<WorkspacePalette[]>([]);
  const [look, setLook] = useState<Look>("light");
  const [paletteId, setPaletteId] = useState("ivory");
  useEffect(() => {
    const all = palettes();
    setPresets(all);
    const active = window.PivotWorkspaceTheme?.getTheme();
    if (!active) return;
    setPaletteId(active.id);
    setLook(active.id === "ivory" ? "light" : active.id === "charcoal" ? "dark" : "custom");
  }, []);
  const apply = useCallback((id: string): void => {
    const p = palettes().find((x) => x.id === id);
    if (!p) return;
    window.PivotWorkspaceTheme?.setTheme(p);
    setPaletteId(id);
  }, []);
  const chooseLook = (l: Look): void => {
    setLook(l);
    if (l === "light") apply("ivory");
    else if (l === "dark") apply("charcoal");
    else if (paletteId === "ivory" || paletteId === "charcoal") apply("midnight");
  };
  const byId = (id: string): WorkspacePalette | undefined => presets.find((p) => p.id === id);
  const customPreview = byId(look === "custom" ? paletteId : "midnight");

  // ── Flow ────────────────────────────────────────────────
  const finish = useCallback((tour: boolean): void => {
    if (picked) {
      // What they added goes first: Home shows the first seven, and their own
      // picks should not fall behind the defaults they happened to keep.
      const before = wl.lists[0]?.tickers ?? [];
      setWatchlist([...picked.filter((s) => !before.includes(s)), ...picked.filter((s) => before.includes(s))]);
    }
    onDone(tour);
  }, [picked, wl, onDone]);

  useEffect(() => { primary.current?.focus({ preventScroll: true }); }, [step]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => { if (e.key === "Escape") finish(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [finish]);

  if (!mounted) return null;

  const tile = (i: Instrument, wide = false): React.ReactElement => {
    const on = chosen.includes(i.symbol);
    return (
      <button key={i.symbol} type="button" className={wide ? "welcome-row" : "welcome-tile"} aria-pressed={on}
        onClick={() => toggle(i.symbol)}>
        <CompanyLogo logoUrl={i.logo} name={i.title} symbol={i.symbol} size={wide ? 40 : 46} />
        <span className="welcome-tile-text">
          <strong>{i.title}</strong>
          <span>{i.sub}</span>
        </span>
        <span className="welcome-tick" aria-hidden="true">{on ? <Check size={14} strokeWidth={2.5} /> : <Plus size={14} strokeWidth={2.25} />}</span>
      </button>
    );
  };

  return createPortal(
    <div className="welcome-scrim">
      <section className="welcome-card" role="dialog" aria-modal="true" aria-labelledby={titleId} data-step={step}>
        <div className="welcome-art" aria-hidden="true">
          {/* Plain <img>: bundled assets (lib/publicAssets.ts), not next/image. */}
          {ART.map((src, i) => (
            // eslint-disable-next-line @next/next/no-img-element
            <img key={src} src={src} alt="" className={i === step ? "on" : ""} />
          ))}
          {/* The wordmark alone, as vector in silver: crisp at any density,
              lifted off the painting by its shadow rather than a plate. */}
          <span className="welcome-mark"><PivotWordLogo className="welcome-mark-logo" gradient={["#ffffff", "#c3c7cd"]} /></span>
          <i className="welcome-grain" />
        </div>
        <button type="button" className="welcome-skip" onClick={() => finish(false)}>
          Skip <X size={14} strokeWidth={2} aria-hidden="true" />
        </button>

        <div className="welcome-body">
          {step === 0 && (
            <div className="welcome-pane" key="hello">
              <h2 id={titleId}>{name ? `Welcome to Pivot, ${name}` : "Welcome to Pivot"}</h2>
              <p>We&apos;re happy to have you. Pivot is your trading workspace: live charts, research and a paper portfolio in one place.</p>
              <p>Make two quick choices and we&apos;ll set it up for you.</p>
            </div>
          )}

          {step === 1 && (
            <div className="welcome-pane" key="watch">
              <h2 id={titleId}>Build your watchlist</h2>
              <p>Pick what you follow: stocks, indices, crypto or commodities. They appear on Home, and you can change them any time.</p>
              <label className="welcome-search">
                <Search size={17} strokeWidth={2} aria-hidden="true" />
                <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search any stock, index, crypto or commodity" aria-label="Search symbols"
                  onKeyDown={(e) => { if (e.key === "Enter" && results[0]) { e.preventDefault(); toggle(results[0].symbol); } }} />
                {query && (
                  <button type="button" className="welcome-clear" onClick={() => setQuery("")} aria-label="Clear search">
                    <X size={14} strokeWidth={2} aria-hidden="true" />
                  </button>
                )}
              </label>
              {query.trim() ? (
                <div className="welcome-results" aria-live="polite">
                  {results.map((r) => tile(r, true))}
                  {!searching && results.length === 0 && <p className="welcome-empty">Nothing matches &ldquo;{query.trim()}&rdquo;.</p>}
                </div>
              ) : (
                <>
                  <div className="welcome-groups" role="tablist" aria-label="Kinds">
                    {(Object.keys(GROUPS) as Group[]).map((g) => (
                      <button key={g} type="button" role="tab" aria-selected={group === g} onClick={() => setGroup(g)}>{g}</button>
                    ))}
                  </div>
                  <div className="welcome-scroll" ref={scroller}>
                    <div className="welcome-grid" key={group}>
                      {visible.map((sym) => tile(describe(sym)))}
                    </div>
                    {count < catalog.length && (
                      <div ref={sentinel} className="welcome-more" aria-hidden="true"><i /><i /><i /></div>
                    )}
                  </div>
                </>
              )}
            </div>
          )}

          {step === 2 && (
            <div className="welcome-pane" key="look">
              <h2 id={titleId}>Choose how Pivot looks</h2>
              <p>Pick light, dark or your own colours. You can change this later in Workspace colors.</p>
              <div className="welcome-looks" role="radiogroup" aria-label="Appearance">
                {(["light", "dark", "custom"] as Look[]).map((l) => {
                  const p = l === "light" ? byId("ivory") : l === "dark" ? byId("charcoal") : customPreview;
                  return (
                    <button key={l} type="button" role="radio" aria-checked={look === l} className="welcome-look" onClick={() => chooseLook(l)}>
                      {p && <Preview p={p} />}
                      <span>{l === "light" ? "Light" : l === "dark" ? "Dark" : "Custom"}</span>
                    </button>
                  );
                })}
              </div>
              {look === "custom" && (
                <div className="welcome-palettes" role="radiogroup" aria-label="Palette">
                  {presets.filter((p) => p.id !== "ivory" && p.id !== "charcoal").map((p) => (
                    <button key={p.id} type="button" role="radio" aria-checked={paletteId === p.id} onClick={() => apply(p.id)}
                      title={p.name}>
                      <span className="welcome-swatch" style={{ background: `linear-gradient(135deg, ${p.surface} 50%, ${p.accent} 50%)` }} />
                      <span>{p.name}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          <footer className="welcome-foot">
            {step > 0 ? (
              <button type="button" className="welcome-back" onClick={() => setStep(step - 1)}>
                <ArrowLeft size={16} strokeWidth={2} aria-hidden="true" /> Back
              </button>
            ) : (
              <div className="welcome-dots" aria-label={`Step ${step + 1} of 3`}>
                {[0, 1, 2].map((i) => <i key={i} className={i === step ? "on" : ""} />)}
              </div>
            )}
            {step === 1 && <span className="welcome-count">{chosen.length} selected</span>}
            {step === LAST ? (
              <div className="welcome-actions">
                <button type="button" className="welcome-quiet" onClick={() => finish(false)}>Skip the tour</button>
                <button ref={primary} type="button" className="welcome-next" onClick={() => finish(true)}>Show me around</button>
              </div>
            ) : (
              <button ref={primary} type="button" className="welcome-next" onClick={() => setStep(step + 1)}>
                {step === 0 ? "Get started" : "Continue"}
              </button>
            )}
          </footer>
        </div>
      </section>
    </div>,
    document.body,
  );
}
