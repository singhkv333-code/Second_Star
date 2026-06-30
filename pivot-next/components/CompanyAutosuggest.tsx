"use client";

/**
 * CompanyAutosuggest — debounced company search input with keyboard-navigable
 * dropdown. Self-contained: owns input state + fetch lifecycle.
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

interface CompanyAutosuggestProps {
  placeholder?: string;
  onSelect: (symbol: string, name: string) => void;
  className?: string;
  autoFocus?: boolean;
  inputDataTestId?: string;
}

// Debounce interval in ms — short enough to feel live, long enough to
// avoid hammering the API on every keystroke.
const DEBOUNCE_MS = 150;

export function CompanyAutosuggest({
  placeholder,
  onSelect,
  className,
  autoFocus,
  inputDataTestId,
}: CompanyAutosuggestProps): React.ReactElement {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CompanySearchResult[]>([]);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const [loading, setLoading] = useState(false);

  const wrapperRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Abort flag for stale requests — avoids race conditions when the user
  // types quickly and an earlier slow response arrives after a later one.
  const cancelledRef = useRef(false);

  // ── Fetch results whenever query changes (debounced) ─────────────────
  useEffect(() => {
    const q = query.trim();
    if (q.length < 1) {
      setResults([]);
      setOpen(false);
      setLoading(false);
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
          setOpen(res.data.results.length > 0);
          setHighlighted(0);
        } else {
          setResults([]);
          setOpen(false);
        }
      } catch {
        if (!cancelledRef.current) {
          setResults([]);
          setOpen(false);
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
    (result: CompanySearchResult): void => {
      onSelect(result.symbol, result.name);
      setQuery("");
      setResults([]);
      setOpen(false);
      setHighlighted(0);
    },
    [onSelect],
  );

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>): void => {
    if (!open || results.length === 0) return;

    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlighted((h) => Math.min(h + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlighted((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const pick = results[highlighted] ?? results[0];
      if (pick) handleSelect(pick);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    }
  };

  return (
    <div
      ref={wrapperRef}
      className={className}
      style={{ position: "relative", flex: 1, minWidth: 0 }}
    >
      <input
        ref={inputRef}
        type="text"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={handleKeyDown}
        onFocus={() => {
          if (results.length > 0) setOpen(true);
        }}
        placeholder={placeholder}
        autoFocus={autoFocus}
        autoComplete="off"
        spellCheck={false}
        data-testid={inputDataTestId}
        aria-label={placeholder ?? "Search companies"}
        aria-autocomplete="list"
        aria-controls={open ? "company-autosuggest-list" : undefined}
        aria-activedescendant={
          open && results[highlighted]
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

      {open && results.length > 0 && (
        <ul
          id="company-autosuggest-list"
          role="listbox"
          aria-label="Company suggestions"
          style={{
            position: "absolute",
            top: "calc(100% + 10px)",
            left: -14,
            right: -14,
            zIndex: 200,
            margin: 0,
            padding: "4px 0",
            listStyle: "none",
            background: "var(--bg-primary)",
            border: "1px solid var(--glass-border)",
            borderRadius: "var(--radius-md)",
            boxShadow: "0 8px 32px rgba(0,0,0,0.18)",
            maxHeight: 280,
            overflowY: "auto",
          }}
        >
          {results.map((r, i) => (
            <DropdownRow
              key={r.symbol}
              result={r}
              index={i}
              highlighted={highlighted === i}
              onMouseEnter={() => setHighlighted(i)}
              onSelect={handleSelect}
            />
          ))}
          {/* logo.dev attribution — required wherever logos render */}
          <li
            role="presentation"
            style={{
              padding: "5px 14px 3px",
              fontSize: 10,
              color: "var(--text-tertiary)",
              borderTop: "1px solid var(--glass-border)",
              marginTop: 2,
            }}
          >
            Logos provided by{" "}
            <a
              href="https://logo.dev"
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "inherit", textDecoration: "underline" }}
              onMouseDown={(e) => e.stopPropagation()}
            >
              Logo.dev
            </a>
          </li>
        </ul>
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
}: {
  result: CompanySearchResult;
  index: number;
  highlighted: boolean;
  onMouseEnter: () => void;
  onSelect: (r: CompanySearchResult) => void;
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
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        padding: "7px 14px",
        cursor: "pointer",
        background: highlighted ? "var(--surface-hover)" : "transparent",
        transition: "background 0.1s",
      }}
    >
      {/* Company logo (monogram fallback when none / on load error) */}
      <CompanyLogo
        logoUrl={result.logo_url}
        name={result.name}
        symbol={result.symbol}
        size={22}
      />

      {/* Symbol badge */}
      <span
        style={{
          flexShrink: 0,
          fontFamily: "var(--font-mono)",
          fontSize: 11,
          fontWeight: 700,
          letterSpacing: "0.03em",
          color: "var(--text-primary)",
          minWidth: 60,
        }}
      >
        {result.symbol}
        {result.has_fundamentals && (
          <span
            title="Fundamentals available"
            aria-label="Fundamentals available"
            style={{
              display: "inline-block",
              width: 5,
              height: 5,
              borderRadius: "50%",
              background: "var(--color-profit)",
              marginLeft: 4,
              verticalAlign: "middle",
            }}
          />
        )}
      </span>

      {/* Company name (primary) */}
      <span
        style={{
          flex: 1,
          fontFamily: "var(--font-ui)",
          fontSize: 12.5,
          color: "var(--text-primary)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {result.name}
      </span>

      {/* Sector (muted) */}
      {result.sector && (
        <span
          style={{
            flexShrink: 0,
            fontFamily: "var(--font-ui)",
            fontSize: 11,
            color: "var(--text-tertiary)",
          }}
        >
          {result.sector}
        </span>
      )}
    </li>
  );
}
