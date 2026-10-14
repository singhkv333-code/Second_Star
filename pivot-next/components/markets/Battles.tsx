"use client";

/**
 * Battles: two rivals, six rounds, one year of price side by side. Each round
 * is a single number per side, so the winner is arithmetic, not opinion.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronLeft, ChevronRight, MessageCircle, Search, X } from "lucide-react";
import { askPivot } from "@/lib/launch";
import {
  BATTLES,
  fetchDailyCloses,
  rebase,
  scoreBattle,
  type Battle,
  type MarketStock,
} from "@/lib/markets";
import { Empty, Mark, css as s, cx, useOpenStock } from "./parts";
import { useInView, useWidth } from "./Sectors";

export function Battles({
  stocks,
  perPage,
}: {
  stocks: Map<string, MarketStock>;
  perPage: number;
}): React.ReactElement {
  const list = useMemo(() => BATTLES.filter((b) => stocks.has(b.a) && stocks.has(b.b)), [stocks]);
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(list.length / perPage));
  const shown = list.slice(page * perPage, page * perPage + perPage);
  const [custom, setCustom] = useState<{ a: string | null; b: string | null }>({ a: null, b: null });

  useEffect(() => { if (page >= pages) setPage(0); }, [page, pages]);

  return (
    <div>
      <div className="mb-3 flex items-center justify-end gap-2">
        <span className={cx(s.stamp, s.num)}>{page + 1} / {pages}</span>
        <button type="button" className={s.iconBtn} aria-label="Previous battles" onClick={() => setPage((p) => (p - 1 + pages) % pages)}>
          <ChevronLeft size={16} />
        </button>
        <button type="button" className={s.iconBtn} aria-label="Next battles" onClick={() => setPage((p) => (p + 1) % pages)}>
          <ChevronRight size={16} />
        </button>
      </div>
      <div className="grid gap-3" style={{ gridTemplateColumns: `repeat(${perPage}, minmax(0, 1fr))` }}>
        {shown.map((b) => (
          <BattleCard key={`${b.a}-${b.b}`} battle={b} a={stocks.get(b.a)!} b={stocks.get(b.b)!} />
        ))}
      </div>
      <div className="mt-3">
        <BuildYourOwn stocks={stocks} value={custom} onChange={setCustom} />
      </div>
    </div>
  );
}

function BattleCard({ battle, a, b }: { battle: Battle; a: MarketStock; b: MarketStock }): React.ReactElement {
  const score = useMemo(() => scoreBattle(a, b), [a, b]);
  const openStock = useOpenStock();
  const lead = score.a === score.b ? null : score.a > score.b ? "a" : "b";
  return (
    <article className={cx(s.card, s.cardPad, "flex min-w-0 flex-col")}>
      <div className={cx(s.label, "mb-3")}>{battle.title}</div>
      <div className="grid items-center gap-2" style={{ gridTemplateColumns: "minmax(0,1fr) auto minmax(0,1fr)" }}>
        <Side stock={a} align="left" lead={lead === "a"} onOpen={() => openStock(a.symbol)} />
        <div className={cx(s.num, "flex items-baseline gap-1.5 text-[26px] font-semibold tracking-tight")}>
          <span style={{ color: lead === "b" ? "var(--text-tertiary)" : "var(--text-primary)" }}>{score.a}</span>
          <span className={s.muted} style={{ fontSize: 16 }}>–</span>
          <span style={{ color: lead === "a" ? "var(--text-tertiary)" : "var(--text-primary)" }}>{score.b}</span>
        </div>
        <Side stock={b} align="right" lead={lead === "b"} onOpen={() => openStock(b.symbol)} />
      </div>

      <Race a={a.symbol} b={b.symbol} />

      <div className="mt-1 flex flex-col">
        {score.rounds.map((r) => (
          <div key={r.label} className="grid items-center gap-2 py-[5px]" style={{ gridTemplateColumns: "minmax(0,1fr) 72px minmax(0,1fr)" }}>
            <RoundValue text={r.fmt(r.a)} win={r.winner === "a"} lose={r.winner === "b"} align="left" />
            <span className={cx(s.label, "text-center")}>{r.label}</span>
            <RoundValue text={r.fmt(r.b)} win={r.winner === "b"} lose={r.winner === "a"} align="right" />
          </div>
        ))}
      </div>

      <div className="mt-auto pt-3">
        <button
          type="button"
          className={s.ghostBtn}
          onClick={() => askPivot(`Compare ${a.name} (${a.symbol}) with ${b.name} (${b.symbol}): growth, margins, valuation and recent price action.`)}
        >
          <MessageCircle size={14} strokeWidth={1.8} /> Ask for the full comparison
        </button>
      </div>
    </article>
  );
}

function Side({ stock, align, lead, onOpen }: { stock: MarketStock; align: "left" | "right"; lead: boolean; onOpen: () => void }): React.ReactElement {
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cx("flex min-w-0 items-center gap-2 rounded-lg border-none bg-transparent p-0 text-inherit", align === "right" ? "flex-row-reverse text-right" : "text-left")}
      style={{ font: "inherit", cursor: "pointer" }}
    >
      <Mark src={stock.logo} label={stock.name} size={32} />
      <span className="min-w-0">
        <span className={cx(s.symbol, "block truncate")} style={{ color: lead ? "var(--text-primary)" : "var(--text-secondary)" }}>{stock.symbol}</span>
        <span className={cx(s.subname, "block")}>{stock.name}</span>
      </span>
    </button>
  );
}

function RoundValue({ text, win, lose, align }: { text: string; win: boolean; lose: boolean; align: "left" | "right" }): React.ReactElement {
  return (
    <span
      className={cx(s.num, "truncate text-[12.5px]", align === "right" && "text-right")}
      style={{
        fontWeight: win ? 600 : 400,
        color: lose ? "var(--text-tertiary)" : "var(--text-primary)",
      }}
    >
      {align === "left" && win && <Pip />}
      {text}
      {align === "right" && win && <Pip />}
    </span>
  );
}

/** The round's winner: a small check beside the number. */
function Pip(): React.ReactElement {
  return <Check size={12} strokeWidth={2.4} aria-label="wins" style={{ display: "inline-block", verticalAlign: "-1px", margin: "0 5px" }} />;
}

// ── one year, side by side ───────────────────────────────────────────────

function Race({ a, b }: { a: string; b: string }): React.ReactElement {
  const [view, seen] = useInView<HTMLDivElement>();
  const [ref, width] = useWidth<HTMLDivElement>();
  const [series, setSeries] = useState<{ t: number; a: number; b: number }[] | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const height = 92;

  useEffect(() => {
    if (!seen) return;
    let alive = true;
    void Promise.all([fetchDailyCloses(a, 250), fetchDailyCloses(b, 250)]).then(([sa, sb]) => {
      if (alive) setSeries(rebase(sa, sb));
    });
    return () => { alive = false; };
  }, [seen, a, b]);

  const geo = useMemo(() => {
    if (!series || series.length < 2 || !width) return null;
    const vals = series.flatMap((p) => [p.a, p.b]);
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const X = (i: number): number => (i / (series.length - 1)) * width;
    const Y = (v: number): number => 4 + (1 - (v - min) / (max - min || 1)) * (height - 8);
    const line = (k: "a" | "b"): string => series.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p[k]).toFixed(1)}`).join("");
    return { X, Y, la: line("a"), lb: line("b"), base: Y(100) };
  }, [series, width]);

  const point = hover != null && series ? series[hover] : series?.[series.length - 1];

  return (
    <div ref={view} className="my-3">
      <div className="mb-1 flex items-center justify-between text-[11.5px]">
        <span className="flex items-center gap-3">
          <Key dash={false} label={a} value={point ? point.a - 100 : null} />
          <Key dash label={b} value={point ? point.b - 100 : null} />
        </span>
        <span className={s.muted}>
          {point && hover != null ? new Date(point.t * 1000).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "2-digit" }) : "1 year"}
        </span>
      </div>
      <div ref={ref} style={{ height }} className="relative">
        {geo ? (
          <svg
            width={width}
            height={height}
            role="img"
            aria-label={`${a} and ${b} over one year, both starting at 100`}
            onMouseMove={(e) => {
              const box = e.currentTarget.getBoundingClientRect();
              const i = Math.round(((e.clientX - box.left) / width) * (series!.length - 1));
              setHover(Math.max(0, Math.min(series!.length - 1, i)));
            }}
            onMouseLeave={() => setHover(null)}
          >
            <line x1={0} x2={width} y1={geo.base} y2={geo.base} stroke="var(--glass-border)" strokeDasharray="2 3" />
            <path d={geo.lb} fill="none" stroke="var(--text-tertiary)" strokeWidth={1.4} strokeDasharray="4 3" />
            <path d={geo.la} fill="none" stroke="var(--text-primary)" strokeWidth={1.6} />
            {hover != null && (
              <line x1={geo.X(hover)} x2={geo.X(hover)} y1={0} y2={height} stroke="var(--glass-border-hover)" />
            )}
          </svg>
        ) : series && series.length < 2 ? (
          <Empty text="No shared price history." height={height} />
        ) : (
          <div className="h-full w-full rounded-lg" style={{ background: "var(--bg-secondary)" }} />
        )}
      </div>
    </div>
  );
}

function Key({ dash, label, value }: { dash: boolean; label: string; value: number | null }): React.ReactElement {
  return (
    <span className="inline-flex items-center gap-1.5">
      <svg width={14} height={4} aria-hidden>
        <line x1={0} x2={14} y1={2} y2={2} stroke={dash ? "var(--text-tertiary)" : "var(--text-primary)"} strokeWidth={1.6} strokeDasharray={dash ? "4 3" : undefined} />
      </svg>
      <span className="font-medium">{label}</span>
      {value != null && (
        <span className={cx(s.num, value > 0 ? s.up : value < 0 ? s.down : s.muted)}>
          {value > 0 ? "+" : ""}{value.toFixed(1)}%
        </span>
      )}
    </span>
  );
}

// ── build your own ───────────────────────────────────────────────────────

function BuildYourOwn({
  stocks,
  value,
  onChange,
}: {
  stocks: Map<string, MarketStock>;
  value: { a: string | null; b: string | null };
  onChange: (v: { a: string | null; b: string | null }) => void;
}): React.ReactElement {
  const a = value.a ? stocks.get(value.a) : undefined;
  const b = value.b ? stocks.get(value.b) : undefined;
  return (
    <div className={cx(s.card, s.cardPad)}>
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-[13px] font-semibold">Your battle</span>
        <Picker stocks={stocks} value={value.a} exclude={value.b} placeholder="First company" onPick={(sym) => onChange({ ...value, a: sym })} />
        <span className={s.muted}>vs</span>
        <Picker stocks={stocks} value={value.b} exclude={value.a} placeholder="Second company" onPick={(sym) => onChange({ ...value, b: sym })} />
      </div>
      {a && b && (
        <div className="mt-3 max-w-[560px]">
          <BattleCard battle={{ a: a.symbol, b: b.symbol, title: "Head to head" }} a={a} b={b} />
        </div>
      )}
    </div>
  );
}

function Picker({
  stocks,
  value,
  exclude,
  placeholder,
  onPick,
}: {
  stocks: Map<string, MarketStock>;
  value: string | null;
  exclude: string | null;
  placeholder: string;
  onPick: (sym: string | null) => void;
}): React.ReactElement {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [hi, setHi] = useState(0);
  const box = useRef<HTMLDivElement>(null);
  const hits = useMemo(() => {
    const t = q.trim().toLowerCase();
    if (!t) return [];
    const all = [...stocks.values()].filter((x) => x.symbol !== exclude);
    const rank = (x: MarketStock): number => {
      const sym = x.symbol.toLowerCase();
      const name = x.name.toLowerCase();
      if (sym === t) return 0;
      if (sym.startsWith(t)) return 1;
      if (name.startsWith(t)) return 2;
      if (name.includes(t) || sym.includes(t)) return 3;
      return 9;
    };
    return all.map((x) => ({ x, r: rank(x) })).filter((h) => h.r < 9)
      .sort((p, q2) => p.r - q2.r || q2.x.mcap_cr - p.x.mcap_cr).slice(0, 6).map((h) => h.x);
  }, [q, stocks, exclude]);

  useEffect(() => {
    const onDown = (e: MouseEvent): void => { if (!box.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const picked = value ? stocks.get(value) : undefined;
  if (picked) {
    return (
      <span className={s.chip} data-active="false" style={{ cursor: "default" }}>
        <Mark src={picked.logo} label={picked.name} size={18} />
        {picked.symbol}
        <button type="button" aria-label={`Remove ${picked.symbol}`} onClick={() => onPick(null)} className="ml-0.5 inline-flex border-none bg-transparent p-0" style={{ color: "var(--text-tertiary)", cursor: "pointer" }}>
          <X size={13} />
        </button>
      </span>
    );
  }
  return (
    <div ref={box} className="relative">
      <label className="flex items-center gap-2 rounded-lg border px-2.5 py-1.5" style={{ borderColor: "var(--glass-border)", background: "var(--bg-base)" }}>
        <Search size={14} className={s.muted} />
        <input
          value={q}
          onChange={(e) => { setQ(e.target.value); setOpen(true); setHi(0); }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { e.preventDefault(); setHi((h) => Math.min(hits.length - 1, h + 1)); }
            if (e.key === "ArrowUp") { e.preventDefault(); setHi((h) => Math.max(0, h - 1)); }
            if (e.key === "Enter" && hits[hi]) { onPick(hits[hi].symbol); setQ(""); setOpen(false); }
            if (e.key === "Escape") setOpen(false);
          }}
          placeholder={placeholder}
          aria-label={placeholder}
          className="w-[150px] border-none bg-transparent text-[12.5px] outline-none"
          style={{ color: "var(--text-primary)" }}
        />
      </label>
      {open && hits.length > 0 && (
        <div className={s.tip} style={{ pointerEvents: "auto", top: "calc(100% + 6px)", left: 0, padding: 4, minWidth: 240 }} role="listbox">
          {hits.map((x, i) => (
            <button
              key={x.symbol}
              type="button"
              role="option"
              aria-selected={i === hi}
              onMouseEnter={() => setHi(i)}
              onClick={() => { onPick(x.symbol); setQ(""); setOpen(false); }}
              className="flex w-full items-center gap-2 rounded-md border-none px-2 py-1.5 text-left"
              style={{ background: i === hi ? "var(--surface-active)" : "transparent", font: "inherit", color: "inherit", cursor: "pointer" }}
            >
              <Mark src={x.logo} label={x.name} size={20} />
              <span className="min-w-0">
                <span className="block text-[12.5px] font-semibold">{x.symbol}</span>
                <span className={cx(s.subname, "block")}>{x.name}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
