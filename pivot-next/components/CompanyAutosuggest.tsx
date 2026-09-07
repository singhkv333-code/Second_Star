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

interface CompanyAutosuggestProps {
  placeholder?: string;
  onSelect: (symbol: string, name: string) => void;
  className?: string;
  autoFocus?: boolean;
  inputDataTestId?: string;
  /** Render a mic that dictates the query (browser recording → English). */
  enableVoice?: boolean;
  /** Align the floating panel to the containing search pill, including its icon/padding. */
  alignPanelToShell?: boolean;
}

// Debounce interval in ms — short enough to feel live, long enough to
// avoid hammering the API on every keystroke.
const DEBOUNCE_MS = 150;

// Recent searches — persisted so a returning user's last picks are one click
// away. Capped small so the dropdown stays a quick shortlist, not a history log.
const RECENT_KEY = "pivot:recent-stock-searches";
const RECENT_MAX = 6;

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
  alignPanelToShell,
}: CompanyAutosuggestProps): React.ReactElement {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CompanySearchResult[]>([]);
  const [recent, setRecent] = useState<CompanySearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const [loading, setLoading] = useState(false);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Empty query → show recent searches; typed query → show live results.
  const trimmed = query.trim();
  const showingRecent = trimmed.length === 0;
  const list = showingRecent ? recent : results;
  // A typed query keeps the panel open while loading and when there is no
  // match. Closing the surface during a request makes the search feel broken.
  const dropdownOpen = open && (!showingRecent || list.length > 0);

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
    let cancelled = false;

    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      try {
        const res = await searchCompanies(q);
        if (cancelled) return;
        if (!isError(res)) {
          setResults(res.data.results);
          setOpen(true);
          setHighlighted(0);
        } else {
          setResults([]);
          setOpen(true);
        }
      } catch {
        if (!cancelled) {
          setResults([]);
          setOpen(true);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, DEBOUNCE_MS);

    return () => {
      cancelled = true;
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  // Keep keyboard navigation visible without jumping the entire page. The
  // browser only scrolls the compact list by the minimum amount required.
  useEffect(() => {
    if (!dropdownOpen) return;
    const active = listRef.current?.querySelector<HTMLElement>(
      `[data-option-index="${highlighted}"]`,
    );
    active?.scrollIntoView({ block: "nearest" });
  }, [dropdownOpen, highlighted]);

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
    (result: CompanySearchResult): void => {
      onSelect(result.symbol, result.name);
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
    [onSelect],
  );

  const clearRecent = useCallback((): void => {
    setRecent([]);
    saveRecent([]);
    setOpen(false);
    inputRef.current?.focus();
  }, []);

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === "Escape" && open) {
      e.preventDefault();
      setOpen(false);
      return;
    }
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
          if (query.trim().length > 0 || list.length > 0) setOpen(true);
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
        <ul
          ref={listRef}
          id="company-autosuggest-list"
          role="listbox"
          className="company-autosuggest-panel"
          aria-label={showingRecent ? "Recent searches" : "Company suggestions"}
          style={{
            position: "absolute",
            top: "calc(100% + 12px)",
            left: alignPanelToShell ? -38 : -14,
            width: "min(360px, calc(100vw - 32px))",
            zIndex: 200,
            margin: 0,
            padding: 8,
            listStyle: "none",
            background: "color-mix(in srgb, var(--bg-primary) 94%, transparent)",
            border: "1px solid color-mix(in srgb, var(--text-primary) 13%, transparent)",
            borderRadius: 16,
            boxShadow: "0 18px 48px rgba(0,0,0,0.18), 0 3px 10px rgba(0,0,0,0.08)",
            backdropFilter: "blur(28px) saturate(150%)",
            maxHeight: 430,
            overflowY: "auto",
          }}
        >
          {showingRecent && (
            <li
              role="presentation"
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                padding: "5px 10px 7px",
                fontSize: 10.5,
                letterSpacing: "0.08em",
                textTransform: "uppercase",
                color: "var(--text-tertiary)",
              }}
            >
              <span>Recent</span>
              <button
                type="button"
                onMouseDown={(e) => {
                  e.preventDefault();
                  clearRecent();
                }}
                style={{
                  background: "none",
                  border: "none",
                  padding: 0,
                  cursor: "pointer",
                  fontSize: 10.5,
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                  color: "var(--text-tertiary)",
                }}
              >
                Clear
              </button>
            </li>
          )}

          {list.map((r, i) => (
            <DropdownRow
              key={r.symbol}
              result={r}
              index={i}
              highlighted={highlighted === i}
              onMouseEnter={() => setHighlighted(i)}
              onSelect={handleSelect}
            />
          ))}

          {!showingRecent && loading && list.length === 0 && (
            <li
              role="status"
              style={{ padding: "22px 10px", fontSize: 12, color: "var(--text-tertiary)" }}
            >
              Searching instruments…
            </li>
          )}

          {!showingRecent && !loading && list.length === 0 && (
            <li
              role="status"
              style={{ padding: "22px 10px", fontSize: 12, color: "var(--text-tertiary)" }}
            >
              No matching instrument
            </li>
          )}

          {!showingRecent && list.some((r) => Boolean(r.logo_url)) && (
            <li
              role="presentation"
              style={{ padding: "7px 10px 2px", textAlign: "right", fontSize: 9.5, color: "var(--text-tertiary)" }}
            >
              Logos by{" "}
              <a
                href="https://logo.dev"
                target="_blank"
                rel="noreferrer"
                style={{ color: "inherit", textDecoration: "underline", textUnderlineOffset: 2 }}
              >
                Logo.dev
              </a>
            </li>
          )}

        </ul>
      )}

      {/* Subtle loading indicator — tiny spinner-free dots beneath input */}
      {loading && query.trim().length >= 1 && (
        <div
          className="company-autosuggest-loading"
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
}: {
  result: CompanySearchResult;
  index: number;
  highlighted: boolean;
  onMouseEnter: () => void;
  onSelect: (r: CompanySearchResult) => void;
}): React.ReactElement {
  const type = result.instrument_type ?? (
    result.sector?.startsWith("ETF") ? "ETF" :
    result.sector?.startsWith("Commodity") ? "Commodity" :
    result.sector === "Index" ? "Index" : "Equity"
  );
  const exchange = result.exchange ?? (type === "Commodity" ? "MCX" : "NSE");
  const hasPrice = typeof result.price === "number" && Number.isFinite(result.price);
  const hasChange = typeof result.change_pct === "number" && Number.isFinite(result.change_pct);
  const change = hasChange ? result.change_pct! : null;

  const formatPrice = (value: number): string => {
    const currency = result.currency ?? "INR";
    if (currency === "INR") {
      return new Intl.NumberFormat("en-IN", {
        style: "currency",
        currency: "INR",
        maximumFractionDigits: 2,
      }).format(value);
    }
    if (currency === "USD") return `$${value.toLocaleString("en-US", { maximumFractionDigits: 4 })}`;
    return `${value.toLocaleString("en-IN", { maximumFractionDigits: 2 })} ${currency}`;
  };

  return (
    <li
      id={`cas-option-${index}`}
      data-option-index={index}
      role="option"
      className="company-autosuggest-option"
      aria-selected={highlighted}
      onMouseDown={(e) => {
        // Use mousedown instead of click so the input blur fires AFTER
        // the select (preventing the dropdown from closing too early).
        e.preventDefault();
        onSelect(result);
      }}
      onMouseEnter={onMouseEnter}
      style={{
        display: "grid",
        gridTemplateColumns: "minmax(0, 1fr) auto",
        alignItems: "center",
        gap: 12,
        minHeight: 58,
        padding: "7px 10px",
        borderRadius: 12,
        cursor: "pointer",
        background: highlighted ? "var(--surface-hover)" : "transparent",
        transform: highlighted ? "translateX(2px)" : "translateX(0)",
        transition: "background 120ms ease, transform 120ms ease",
      }}
    >
      <span className="flex min-w-0 items-center gap-3">
        <CompanyLogo
          logoUrl={result.logo_url}
          name={result.name}
          symbol={result.symbol}
          size={34}
        />
        <span className="flex min-w-0 flex-col" style={{ gap: 3 }}>
          <span className="flex min-w-0 items-center gap-2">
            <span
              style={{
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                fontFamily: "var(--font-ui)",
                fontSize: 14,
                fontWeight: 650,
                letterSpacing: "-0.012em",
                color: "var(--text-primary)",
              }}
            >
              {result.symbol}
            </span>
            <span
              style={{
                flexShrink: 0,
                padding: "2px 5px",
                borderRadius: 5,
                background: "color-mix(in srgb, var(--text-primary) 7%, transparent)",
                color: "var(--text-tertiary)",
                fontSize: 9.5,
                fontWeight: 650,
                letterSpacing: "0.05em",
              }}
            >
              {exchange}
            </span>
          </span>
          <span
            title={result.name}
            style={{
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              fontSize: 11.5,
              lineHeight: 1.25,
              color: "var(--text-tertiary)",
            }}
          >
            {result.name}{type !== "Equity" ? ` · ${type}` : ""}
          </span>
        </span>
      </span>

      <span
        className="flex shrink-0 flex-col items-end"
        style={{ gap: 3, minWidth: 78, fontVariantNumeric: "tabular-nums" }}
      >
        <span style={{ fontSize: 13, fontWeight: 650, color: hasPrice ? "var(--text-primary)" : "var(--text-tertiary)" }}>
          {hasPrice ? formatPrice(result.price!) : "—"}
        </span>
        <span
          style={{
            fontSize: 11.5,
            fontWeight: 650,
            color: change === null
              ? "var(--text-tertiary)"
              : change > 0
                ? "var(--color-profit, #059669)"
                : change < 0
                  ? "var(--color-loss, #dc2626)"
                  : "var(--text-tertiary)",
          }}
        >
          {change === null ? "Unavailable" : `${change > 0 ? "+" : ""}${change.toFixed(2)}%`}
          {result.quote_source === "charto_relay" ? " · delayed" : ""}
        </span>
      </span>
    </li>
  );
}
