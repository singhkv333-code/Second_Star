"use client";

/**
 * Welcome — three cards shown once to a new account, before the product tour.
 *
 *   1. A welcome.
 *   2. Their watchlist: popular names to tap, and a search for the rest.
 *      What they pick replaces watchlist 1 on Home.
 *   3. How Pivot looks: light, dark, or one of the workspace palettes.
 *      Choices apply at once, through the same runtime as Workspace colors.
 *
 * Each card is a large rounded square: a painting on top (scripts/welcome-art.py)
 * and the choice below it. "Show me around" hands over to the tour; Skip ends
 * here and keeps the defaults.
 */

import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { ArrowLeft, Check, Plus, Search, X } from "lucide-react";
import { CompanyLogo } from "@/components/CompanyLogo";
import { getCompanyLogos, getMe, searchCompanies, type CompanySearchResult } from "@/lib/api";
import { isError } from "@/lib/types";
import { PIVOT_ICON, WELCOME_ART } from "@/lib/publicAssets";
import { setWatchlist, useWatchlists } from "@/lib/watchlists";
import type { WorkspacePalette } from "@/lib/workspace-theme";
import "./welcome.css";

const POPULAR: { symbol: string; name: string }[] = [
  { symbol: "RELIANCE", name: "Reliance Industries" },
  { symbol: "HDFCBANK", name: "HDFC Bank" },
  { symbol: "TCS", name: "Tata Consultancy" },
  { symbol: "INFY", name: "Infosys" },
  { symbol: "ICICIBANK", name: "ICICI Bank" },
  { symbol: "SBIN", name: "State Bank of India" },
  { symbol: "BHARTIARTL", name: "Bharti Airtel" },
  { symbol: "ITC", name: "ITC" },
  { symbol: "LT", name: "Larsen & Toubro" },
  { symbol: "BAJFINANCE", name: "Bajaj Finance" },
  { symbol: "HINDUNILVR", name: "Hindustan Unilever" },
  { symbol: "MARUTI", name: "Maruti Suzuki" },
];

const ART = [WELCOME_ART.dune, WELCOME_ART.tide, WELCOME_ART.dusk];
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
  const [logos, setLogos] = useState<Record<string, string | null>>({});
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CompanySearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const toggle = useCallback((symbol: string): void => {
    setPicked((cur) => {
      const list = cur ?? wl.lists[0]?.tickers ?? [];
      return list.includes(symbol) ? list.filter((s) => s !== symbol) : [...list, symbol];
    });
  }, [wl]);

  useEffect(() => { void getCompanyLogos(POPULAR.map((p) => p.symbol)).then(setLogos); }, []);

  useEffect(() => {
    const q = query.trim();
    if (!q) { setResults([]); setSearching(false); return; }
    setSearching(true);
    const t = window.setTimeout(() => {
      searchCompanies(q, 8).then((r) => {
        setResults(isError(r) ? [] : r.data.results);
        setSearching(false);
      }).catch(() => { setResults([]); setSearching(false); });
    }, 180);
    return () => window.clearTimeout(t);
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
    if (picked) setWatchlist(picked);
    onDone(tour);
  }, [picked, onDone]);

  useEffect(() => { primary.current?.focus({ preventScroll: true }); }, [step]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => { if (e.key === "Escape") finish(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [finish]);

  if (!mounted) return null;

  const tile = (symbol: string, label: string, logo: string | null | undefined, wide = false): React.ReactElement => {
    const on = chosen.includes(symbol);
    return (
      <button key={symbol} type="button" className={wide ? "welcome-row" : "welcome-tile"} aria-pressed={on}
        onClick={() => toggle(symbol)}>
        <CompanyLogo logoUrl={logo} name={label} symbol={symbol} size={wide ? 40 : 46} />
        <span className="welcome-tile-text">
          <strong>{symbol}</strong>
          <span>{label}</span>
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
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={PIVOT_ICON} alt="" className="welcome-mark" />
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
              <p>Pick the stocks you follow. They appear on Home, and you can change them any time.</p>
              <label className="welcome-search">
                <Search size={17} strokeWidth={2} aria-hidden="true" />
                <input type="search" value={query} onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search any NSE or BSE stock" aria-label="Search stocks"
                  onKeyDown={(e) => { if (e.key === "Enter" && results[0]) { e.preventDefault(); toggle(results[0].symbol); } }} />
                {query && (
                  <button type="button" className="welcome-clear" onClick={() => setQuery("")} aria-label="Clear search">
                    <X size={14} strokeWidth={2} aria-hidden="true" />
                  </button>
                )}
              </label>
              {query.trim() ? (
                <div className="welcome-results" aria-live="polite">
                  {results.map((r) => tile(r.symbol, r.name, r.logo_url, true))}
                  {!searching && results.length === 0 && <p className="welcome-empty">No stock matches &ldquo;{query.trim()}&rdquo;.</p>}
                </div>
              ) : (
                <div className="welcome-grid">
                  {POPULAR.map((p) => tile(p.symbol, p.name, logos[p.symbol]))}
                </div>
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
