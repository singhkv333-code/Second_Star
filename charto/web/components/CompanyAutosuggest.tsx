"use client";

/**
 * CompanyAutosuggest — debounced company search input with keyboard-navigable
 * dropdown. Self-contained: owns input state + fetch lifecycle.
 *
 * When the input is focused but EMPTY, it surfaces the user's recent searches
 * (persisted in localStorage) instead of nothing, so a click on the search bar
 * is a useful jumping-off point. As soon as they type, it switches to live
 * company results.
 *
 * Props:
 *   placeholder       — input placeholder text
 *   onSelect(sym,name)— called when the user picks a result; clears/closes
 *   className         — forwarded to the wrapper div
 *   autoFocus         — whether the input should auto-focus on mount
 *   inputDataTestId   — data-testid forwarded to the <input> element
 *
 * Keyboard:
 *   ArrowDown / ArrowUp — move highlight
 *   Enter               — select highlighted (or first) result
 *   Escape              — close dropdown
 * Outside click/blur closes the dropdown.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { searchCompanies, type CompanySearchResult } from "@/lib/api";
import { isError } from "@/lib/types";
import { CompanyLogo } from "@/components/CompanyLogo";
import { VoiceInputButton } from "@/components/VoiceInputButton";
import { ChartNoAxesCombined, Search } from "lucide-react";

interface CompanyAutosuggestProps {
  placeholder?: string;
  onSelect: (symbol: string, name: string) => void;
  className?: string;
  autoFocus?: boolean;
  inputDataTestId?: string;
  /** Render a mic that dictates the query (browser recording → English). */
  enableVoice?: boolean;
  onOpenChart?: (symbol: string) => void;
  stockOnly?: boolean;
}

// Debounce interval in ms — short enough to feel live, long enough to
// avoid hammering the API on every keystroke.
const DEBOUNCE_MS = 150;

// Recent searches — persisted so a returning user's last picks are one click
// away. Capped small so the dropdown stays a quick shortlist, not a history log.
const RECENT_KEY = "pivot:recent-stock-searches";
const RECENT_MAX = 6;
type SearchCategory = "All" | "Stocks" | "Crypto" | "Indices" | "Commodities" | "Options";
type SearchInstrument = CompanySearchResult & { searchCategory?: SearchCategory; exchange?: string | null; instrument_type?: string | null; price?: number | null; change_pct?: number | null; currency?: string | null; quote_source?: string | null; isHydrated?: boolean };
const SEARCH_CATEGORIES: SearchCategory[] = ["All", "Stocks", "Crypto", "Indices", "Commodities", "Options"];

function instrumentCategory(symbol: string, exchange?: string | null, kind?: string | null): SearchCategory {
  const type = (kind || "").toLowerCase();
  if (type.includes("crypto") || exchange === "BYBIT" || exchange === "COINBASE" || /(?:USDT|-USD)$/.test(symbol)) return "Crypto";
  if (type.includes("option") || exchange === "NFO") return "Options";
  if (type.includes("index") || type.includes("volatility")) return "Indices";
  if (exchange === "MCX" || type.includes("commodity")) return "Commodities";
  return "Stocks";
}

function loadRecent(): CompanySearchResult[] {
  try {
    const raw = localStorage.getItem(RECENT_KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw) as unknown;
    if (!Array.isArray(arr)) return [];
    return arr
      .filter(
        (x): x is CompanySearchResult =>
          !!x && typeof (x as CompanySearchResult).symbol === "string",
      )
      .slice(0, RECENT_MAX);
  } catch {
    return [];
  }
}

function saveRecent(list: CompanySearchResult[]): void {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_MAX)));
  } catch {
    // localStorage unavailable (private mode / quota) — recents are a
    // nice-to-have, never block the search on a persistence failure.
  }
}

export function CompanyAutosuggest({
  placeholder,
  onSelect,
  className,
  autoFocus,
  inputDataTestId,
  enableVoice,
  onOpenChart,
  stockOnly = false,
}: CompanyAutosuggestProps): React.ReactElement {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CompanySearchResult[]>([]);
  const [recent, setRecent] = useState<CompanySearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const [loading, setLoading] = useState(false);
  const [category, setCategory] = useState<SearchCategory>("All");
  const [universe, setUniverse] = useState<SearchInstrument[]>([]);
  const [universeLoading, setUniverseLoading] = useState(false);
  const [visibleCount, setVisibleCount] = useState(80);
  const modalInputRef = useRef<HTMLInputElement>(null);
  const universeAttempted = useRef(false);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Abort flag for stale requests — avoids race conditions when the user
  // types quickly and an earlier slow response arrives after a later one.
  const cancelledRef = useRef(false);

  // Empty query shows the chart universe; typed query ranks live company results first.
  const trimmed = query.trim();
  const showingRecent = trimmed.length === 0;
  const source: SearchInstrument[] = showingRecent
    ? (universe.length ? universe : recent)
    : [...results, ...universe];
  const seen = new Set<string>();
  const filtered = source.filter((item) => {
    const group = item.searchCategory || instrumentCategory(item.symbol, item.exchange, item.instrument_type);
    if ((stockOnly || category === "Stocks") && group !== "Stocks") return false;
    if (!stockOnly && category !== "All" && category !== "Stocks" && group !== category) return false;
    if (!showingRecent && !`${item.symbol} ${item.name}`.toUpperCase().includes(trimmed.toUpperCase())) return false;
    if (seen.has(item.symbol)) return false;
    seen.add(item.symbol);
    return true;
  });
  const list = filtered.slice(0, visibleCount);
  const dropdownOpen = open;
  useEffect(() => { setVisibleCount(80); }, [query, category]);

  useEffect(() => {
    if (!open || stockOnly || universeAttempted.current) return;
    universeAttempted.current = true;
    let active = true;
    setUniverseLoading(true);
    const host = ["localhost", "127.0.0.1"].includes(location.hostname) ? "http://127.0.0.1:5174" : "";
    fetch(`${host}/symbols`).then((r) => r.json()).then((data: {
      symbols?: string[]; names?: Record<string, string>; long?: Record<string, string>;
      logos?: Record<string, string>; meta?: Record<string, [string, string, number?]>; hydrated?: string[];
    }) => {
      if (!active) return;
      const hydrated = new Set(data.hydrated || []);
      setUniverse((data.symbols || []).map((symbol): SearchInstrument => {
        const [exchange, kind] = data.meta?.[symbol] || ["NSE", "equity"];
        return { symbol, name: data.long?.[symbol] || data.names?.[symbol] || symbol,
          sector: null, has_fundamentals: false, logo_url: data.logos?.[symbol] || null,
          exchange, instrument_type: kind, searchCategory: instrumentCategory(symbol, exchange, kind), isHydrated: hydrated.has(symbol) };
      }));
    }).catch(() => { /* Stock search remains available. */ }).finally(() => { if (active) setUniverseLoading(false); });
    return () => { active = false; };
  }, [open, stockOnly]);

  useEffect(() => { if (open) modalInputRef.current?.focus(); }, [open]);

  // ── Load persisted recent searches once on mount ─────────────────────
  useEffect(() => {
    setRecent(loadRecent());
  }, []);

  // ── Fetch results whenever query changes (debounced) ─────────────────
  useEffect(() => {
    const q = query.trim();
    if (q.length < 1) {
      setResults([]);
      setLoading(false);
      // Don't force-close here: an empty focused input should still be able
      // to show recent searches (handled by onFocus / dropdownOpen).
      return;
    }

    setLoading(true);
    cancelledRef.current = false;

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await searchCompanies(q);
        if (cancelledRef.current) return;
        if (!isError(res)) {
          setResults(res.data.results);
          setOpen(true);
          setHighlighted(0);
        } else {
          setResults([]);
          setOpen(true);
        }
      } catch {
        if (!cancelledRef.current) {
          setResults([]);
          setOpen(true);
        }
      } finally {
        if (!cancelledRef.current) setLoading(false);
      }
    }, DEBOUNCE_MS);

    return () => {
      cancelledRef.current = true;
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  // ── Outside-click closes dropdown ─────────────────────────────────────
  useEffect(() => {
    if (!open) return;
    const handle = (e: MouseEvent): void => {
      if (
        wrapperRef.current &&
        !wrapperRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handle);
    return () => document.removeEventListener("mousedown", handle);
  }, [open]);

  const handleSelect = useCallback(
    (result: SearchInstrument): void => {
      const group = result.searchCategory || instrumentCategory(result.symbol, result.exchange, result.instrument_type);
      if (group === "Stocks") onSelect(result.symbol, result.name);
      else if (onOpenChart) onOpenChart(result.symbol);
      else window.location.assign(`/?symbol=${encodeURIComponent(result.symbol)}`);
      // Remember this pick at the front of the recent list (dedup by symbol).
      setRecent((prev) => {
        const next = [
          result,
          ...prev.filter((x) => x.symbol !== result.symbol),
        ].slice(0, RECENT_MAX);
        saveRecent(next);
        return next;
      });
      setQuery("");
      setResults([]);
      setOpen(false);
      setHighlighted(0);
    },
    [onSelect, onOpenChart],
  );

  const handleOpenChart = useCallback(
    (result: CompanySearchResult): void => {
      if (!onOpenChart) return;
      onOpenChart(result.symbol);
      setRecent((prev) => {
        const next = [result, ...prev.filter((x) => x.symbol !== result.symbol)].slice(0, RECENT_MAX);
        saveRecent(next);
        return next;
      });
      setQuery("");
      setResults([]);
      setOpen(false);
      setHighlighted(0);
    },
    [onOpenChart],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === "Escape") { e.preventDefault(); setOpen(false); return; }
    if (!open || list.length === 0) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlighted((h) => Math.min(h + 1, list.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlighted((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const pick = list[highlighted] ?? list[0];
      if (pick) handleSelect(pick);
    }
  };

  return (
    <div
      ref={wrapperRef}
      className={className}
      style={{
        position: "relative",
        flex: 1,
        minWidth: 0,
        display: "flex",
        alignItems: "center",
        gap: 4,
      }}
    >
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={handleKeyDown}
        onFocus={() => {
          // Open whichever list has content: recent (empty query) or results.
          setOpen(true);
        }}
        placeholder={placeholder}
        autoFocus={autoFocus}
        autoComplete="off"
        spellCheck={false}
        data-testid={inputDataTestId}
        aria-label={placeholder ?? "Search companies"}
        aria-autocomplete="list"
        aria-controls={dropdownOpen ? "company-autosuggest-list" : undefined}
        aria-activedescendant={
          dropdownOpen && list[highlighted]
            ? `cas-option-${highlighted}`
            : undefined
        }
        className="flex-1 outline-none"
        style={{
          width: "100%",
          background: "transparent",
          border: "none",
          color: "var(--text-primary)",
          fontFamily: "var(--font-ui)",
          fontSize: 13,
          letterSpacing: "-0.005em",
        }}
      />

      {enableVoice && (
        <VoiceInputButton
          size={14}
          data-testid="search-voice-btn"
          onTranscript={(text) => {
            // Spoken queries end with dictation punctuation ("Reliance.")
            // that would poison the prefix search — strip it.
            setQuery(text.replace(/[.,!?…]+$/u, "").trim());
            inputRef.current?.focus();
          }}
        />
      )}

      {dropdownOpen && (
        <div
          className="cas-scroll"
          role="dialog"
          aria-label="Symbol search"
        >
          <div className="cas-modal-head">
            <strong>Symbol search</strong>
            <button type="button" aria-label="Close symbol search" onMouseDown={(e) => { e.preventDefault(); setOpen(false); }}>×</button>
          </div>
          <div className="cas-modal-query">
            <Search size={17} aria-hidden="true" />
            <input ref={modalInputRef} value={query} onChange={(e) => { setQuery(e.target.value); setHighlighted(0); }} onKeyDown={handleKeyDown} placeholder="Search symbol or company" aria-label="Search symbols" autoComplete="off" />
          </div>
          {!stockOnly && <div className="cas-category-tabs" role="tablist" aria-label="Instrument category">
            {SEARCH_CATEGORIES.map((item) => <button type="button" role="tab" key={item} aria-selected={category === item} className={category === item ? "active" : ""} onMouseDown={(e) => e.preventDefault()} onClick={() => { setCategory(item); setHighlighted(0); }}>{item}</button>)}
          </div>}
          <div className="cas-results-caption">INSTRUMENTS</div>

          <ul id="company-autosuggest-list" role="listbox" className="cas-result-list" aria-label="Instrument suggestions" onScroll={(e) => {
            const el = e.currentTarget;
            if (el.scrollTop + el.clientHeight >= el.scrollHeight - 120) {
              setVisibleCount((count) => Math.min(filtered.length, count + 80));
            }
          }}>

          {list.map((r, i) => (
            <DropdownRow
              key={r.symbol}
              result={r}
              index={i}
              highlighted={highlighted === i}
              onMouseEnter={() => setHighlighted(i)}
              onSelect={handleSelect}
              onOpenChart={onOpenChart ? handleOpenChart : undefined}
            />
          ))}

          {list.length === 0 && <li role="presentation" className="cas-empty">{loading || universeLoading ? "Loading instruments…" : "No matching instruments in this category"}</li>}
          </ul>
          {list.some((item) => item.logo_url?.includes("img.logo.dev")) && <div className="cas-attribution">Logos by <a href="https://logo.dev" target="_blank" rel="noopener noreferrer">Logo.dev</a></div>}
        </div>
      )}

      {/* Subtle loading indicator — tiny spinner-free dots beneath input */}
      {loading && query.trim().length >= 1 && (
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            bottom: -2,
            left: 0,
            right: 0,
            height: 1,
            background: "var(--glass-border)",
            overflow: "hidden",
          }}
        />
      )}
    </div>
  );
}

// ── Dropdown row ─────────────────────────────────────────────────────────────

function DropdownRow({
  result,
  index,
  highlighted,
  onMouseEnter,
  onSelect,
  onOpenChart,
}: {
  result: SearchInstrument;
  index: number;
  highlighted: boolean;
  onMouseEnter: () => void;
  onSelect: (r: SearchInstrument) => void;
  onOpenChart?: (result: SearchInstrument) => void;
}): React.ReactElement {
  return (
    <li
      id={`cas-option-${index}`}
      role="option"
      aria-selected={highlighted}
      onMouseDown={(e) => {
        // Use mousedown instead of click so the input blur fires AFTER
        // the select (preventing the dropdown from closing too early).
        e.preventDefault();
        onSelect(result);
      }}
      onMouseEnter={onMouseEnter}
      className={`cas-instrument-row${highlighted ? " active" : ""}`}
    >
      <span className="cas-instrument-lead">
        <CompanyLogo logoUrl={result.logo_url} name={result.name} symbol={result.symbol} size={34} />
        <span className="cas-instrument-copy">
          <span className="cas-instrument-symbol">{result.symbol}<small>{result.exchange || result.searchCategory || "Stocks"}</small></span>
          {result.name !== result.symbol && <span className="cas-instrument-name">{result.name}</span>}
        </span>
      </span>
      <span className="cas-instrument-quote">
        {result.price != null ? <>
          <span>{result.currency === "USD" ? "$" : "₹"}{result.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
          <small>{result.change_pct != null ? `${result.change_pct > 0 ? "+" : ""}${result.change_pct.toFixed(2)}%` : ""}{result.quote_source === "charto_relay" ? " · delayed" : ""}</small>
        </> : <small>{result.isHydrated === false ? "~6s to load" : "—"}</small>}
      </span>
      {onOpenChart && (
        <button
          type="button"
          aria-label={`Open ${result.symbol} chart`}
          title="Open chart"
          onMouseDown={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onOpenChart(result);
          }}
          className="inline-flex shrink-0 items-center justify-center"
          style={{
            width: 26,
            height: 26,
            padding: 0,
            border: 0,
            borderRadius: "var(--radius-sm)",
            background: "transparent",
            color: "var(--text-tertiary)",
            cursor: "pointer",
          }}
        >
          <ChartNoAxesCombined size={15} strokeWidth={1.9} aria-hidden="true" />
        </button>
      )}
    </li>
  );
}
