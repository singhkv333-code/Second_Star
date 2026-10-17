"use client";

/** Find a company by name or ticker from any public page. The index is the
 *  directory's own list (/stocks/index.json), fetched on first focus, so a
 *  reader who never searches never downloads it. */

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import styles from "./public.module.css";

type Entry = [symbol: string, name: string];

let index: Promise<Entry[]> | null = null;
function loadIndex(): Promise<Entry[]> {
  index ??= fetch("/stocks/index.json")
    .then((r) => (r.ok ? (r.json() as Promise<Entry[]>) : []))
    .catch(() => {
      index = null;
      return [];
    });
  return index;
}

function rank(entries: Entry[], q: string): Entry[] {
  const needle = q.trim().toUpperCase();
  if (!needle) return [];
  const scored: [number, Entry][] = [];
  for (const e of entries) {
    const sym = e[0];
    const name = e[1].toUpperCase();
    const s = sym === needle ? 0 : sym.startsWith(needle) ? 1 : name.startsWith(needle) ? 2
      : name.includes(` ${needle}`) ? 3 : sym.includes(needle) || name.includes(needle) ? 4 : -1;
    if (s >= 0) scored.push([s, e]);
    if (scored.length > 400) break;
  }
  // The list arrives largest company first, so a stable sort keeps the big
  // names on top within each match class.
  return scored.sort((a, b) => a[0] - b[0]).slice(0, 8).map(([, e]) => e);
}

export function StockSearch(): React.ReactElement {
  const router = useRouter();
  const [entries, setEntries] = useState<Entry[]>([]);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const hits = useMemo(() => rank(entries, q), [entries, q]);

  useEffect(() => {
    const close = (e: MouseEvent): void => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const go = (sym: string): void => {
    setOpen(false);
    router.push(`/stock/${encodeURIComponent(sym)}`);
  };

  return (
    <div className={styles.search} ref={box} role="search">
      <input
        type="search"
        aria-label="Search Indian stocks"
        placeholder="Search a stock or company"
        value={q}
        onFocus={() => {
          setOpen(true);
          void loadIndex().then(setEntries);
        }}
        onChange={(e) => {
          setQ(e.target.value);
          setActive(0);
          setOpen(true);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, hits.length - 1)); }
          if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          if (e.key === "Enter" && hits[active]) { e.preventDefault(); go(hits[active][0]); }
          if (e.key === "Escape") setOpen(false);
        }}
      />
      {open && hits.length > 0 && (
        <ul className={styles.results} role="listbox">
          {hits.map(([sym, name], i) => (
            <li key={sym}>
              <a
                href={`/stock/${encodeURIComponent(sym)}`}
                role="option"
                aria-selected={i === active}
                onClick={(e) => { e.preventDefault(); go(sym); }}
              >
                {name}
                <span>{sym}</span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
