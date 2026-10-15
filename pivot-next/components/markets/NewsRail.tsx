"use client";

/**
 * Market news, with every story tagged with the stocks it names and how they
 * moved, and the events calendar. Headlines come from the chart server's
 * fixed list of public market feeds; Pivot links out and never republishes.
 */

import { useEffect, useMemo, useState } from "react";
import { ArrowsClockwise, BankDuo, CalendarBlankDuo, ChartBarDuo, CoinsDuo, Newspaper, ScissorsDuo, type IconProps } from "./icons";
import { getCompanyLogos } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import {
  fetchCalendar,
  fetchNews,
  fmtPct,
  newsTagger,
  type CalendarItem,
  type MarketStock,
  type NewsItem,
} from "@/lib/markets";
import { CardHead, Empty, Hint, Mark, Segmented, css as s, cx, useOpenStock } from "./parts";

function ago(ts: number): string {
  const m = Math.max(0, Math.round((Date.now() / 1000 - ts) / 60));
  if (m < 1) return "now";
  if (m < 60) return `${m}m`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.round(h / 24)}d`;
}

type NewsView = "Latest" | "Movers";

export function NewsRail({
  stocks,
  sector,
  sectorLabel,
  limit = 30,
}: {
  stocks: Map<string, MarketStock>;
  sector: string | null;
  sectorLabel: string | null;
  limit?: number;
}): React.ReactElement {
  const [items, setItems] = useState<NewsItem[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [view, setView] = useState<NewsView>("Latest");
  const [tick, setTick] = useState(0);
  const openStock = useOpenStock();

  useEffect(() => {
    let alive = true;
    fetchNews(60)
      .then((xs) => { if (alive) { setItems(xs); setFailed(false); } })
      .catch(() => alive && setFailed(true));
    const id = window.setInterval(() => setTick((t) => t + 1), 5 * 60_000);
    return () => { alive = false; window.clearInterval(id); };
  }, [tick]);

  const tag = useMemo(() => newsTagger([...stocks.values()]), [stocks]);
  const tagged = useMemo(
    () => (items ?? []).map((n) => ({ n, syms: tag(n.title) })),
    [items, tag],
  );

  const list = useMemo(() => {
    let xs = tagged;
    if (view === "Movers") {
      xs = xs.filter((x) => x.syms.length)
        .sort((p, q) => maxMove(q.syms, stocks) - maxMove(p.syms, stocks));
    }
    if (sector) {
      const inSector = xs.filter((x) => x.syms.some((sym) => stocks.get(sym)?.sector === sector));
      if (inSector.length) xs = inSector;
    }
    return xs.slice(0, limit);
  }, [tagged, view, sector, stocks, limit]);

  const filtered = sector && list.some((x) => x.syms.some((sym) => stocks.get(sym)?.sector === sector));

  return (
    <section aria-label="Market news" className="flex min-h-0 flex-col">
      <CardHead
        Icon={Newspaper}
        title={filtered ? `News · ${sectorLabel}` : "News"}
        right={
          <span className="flex items-center gap-1">
            <Segmented size="sm" label="News view" options={["Latest", "Movers"] as const} value={view} onChange={setView} />
            <button type="button" className={s.iconBtn} aria-label="Refresh news" onClick={() => { setItems(null); setTick((t) => t + 1); }}>
              <ArrowsClockwise size={13} />
            </button>
          </span>
        }
      />
      {failed && !items ? (
        <Empty text="News is unavailable right now." />
      ) : !items ? (
        <div className="flex flex-col gap-4 pt-1">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="flex flex-col gap-1.5">
              <Skeleton style={{ height: 13, width: "95%" }} />
              <Skeleton style={{ height: 13, width: "70%" }} />
              <Skeleton style={{ height: 10, width: "35%" }} />
            </div>
          ))}
        </div>
      ) : list.length === 0 ? (
        <Empty text={view === "Movers" ? "No story names a stock from the 500 yet." : "No stories yet."} />
      ) : (
        <div className="flex flex-col">
          {list.map(({ n, syms }, i) => (
            <div key={n.link}>
              {i > 0 && <hr className={s.rowDivider} />}
              <a className={s.newsItem} href={n.link} target="_blank" rel="noopener noreferrer">
                <NewsArt item={n} lead={syms[0] ? stocks.get(syms[0]) : undefined} />
                <p className={s.newsTitle}>{n.title}</p>
                <div className={s.newsMeta}>
                  <span>{n.source}</span>
                  <span aria-hidden>·</span>
                  <span>{ago(n.ts)}</span>
                  {syms.map((sym) => {
                    const st = stocks.get(sym);
                    const v = st?.change;
                    return (
                      <button
                        key={sym}
                        type="button"
                        className={s.tag}
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); openStock(sym); }}
                        title={st?.name}
                      >
                        {sym}{" "}
                        <span className={v == null || v === 0 ? s.muted : v > 0 ? s.up : s.down}>{fmtPct(v)}</span>
                      </button>
                    );
                  })}
                </div>
              </a>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/**
 * Every story carries a picture: the publisher's own, or, when the feed has
 * none (or it fails to load), the company it names on the frame's shade, or
 * the publisher's name set large. Never a stock photo we chose.
 */
function NewsArt({ item, lead }: { item: NewsItem; lead?: MarketStock }): React.ReactElement {
  const [broken, setBroken] = useState(false);
  const box: React.CSSProperties = { aspectRatio: "16 / 8", borderRadius: 10, background: "var(--shell-chrome)" };
  if (item.image && !broken) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={item.image}
        alt=""
        loading="lazy"
        decoding="async"
        className="mb-2.5 block w-full object-cover"
        style={box}
        onError={() => setBroken(true)}
      />
    );
  }
  return (
    <div className="mb-2.5 flex w-full items-center justify-center gap-3" style={box} aria-hidden>
      {lead ? (
        <>
          <Mark src={lead.logo} label={lead.name} size={44} />
          <span className="text-[15px] font-semibold tracking-tight">{lead.name}</span>
        </>
      ) : (
        <>
          <Newspaper size={26} />
          <span className="text-[15px] font-semibold tracking-tight">{item.source}</span>
        </>
      )}
    </div>
  );
}

function maxMove(syms: string[], stocks: Map<string, MarketStock>): number {
  return Math.max(0, ...syms.map((x) => Math.abs(stocks.get(x)?.change ?? 0)));
}

// ── calendar ─────────────────────────────────────────────────────────────

type CalView = "All" | "Results" | "Dividends" | "Macro";

/** Each kind of event gets its own symbol and a quiet colour. */
const KINDS: Record<string, { Icon: React.ComponentType<IconProps>; tone: string; name: string }> = {
  results: { Icon: ChartBarDuo, tone: "var(--pivot-blue)", name: "Results" },
  dividend: { Icon: CoinsDuo, tone: "var(--color-profit)", name: "Dividend" },
  action: { Icon: ScissorsDuo, tone: "var(--color-warn)", name: "Corporate action" },
  board: { Icon: CalendarBlankDuo, tone: "#7c5cbf", name: "Board meeting" },
  macro: { Icon: BankDuo, tone: "var(--text-primary)", name: "Macro" },
};

function KindMark({ kind, size = 28 }: { kind: string; size?: number }): React.ReactElement {
  const k = KINDS[kind] ?? KINDS.board!;
  return (
    <Hint text={k.name}>
      <span
        className="inline-flex items-center justify-center"
        style={{ width: size, height: size, borderRadius: 8, flex: "none", color: k.tone, background: `color-mix(in srgb, ${k.tone} 13%, var(--bg-base))` }}
      >
        <k.Icon size={Math.round(size * 0.58)} />
      </span>
    </Hint>
  );
}

function inView(i: CalendarItem, v: CalView): boolean {
  if (v === "All") return true;
  if (v === "Dividends") return i.kind === "dividend";
  if (v === "Macro") return i.kind === "macro";
  return i.kind === "results" || i.kind === "board";
}

/** One short line under the name. NSE's own text is a paragraph. */
function eventLine(i: CalendarItem): string {
  if (i.kind === "results") return `${i.period ? `${i.period} ` : ""}results`;
  if (i.kind === "board") return i.purpose || "Board meeting";
  return i.detail || i.title;
}

export function EventsCalendar({ stocks }: { stocks: Map<string, MarketStock> }): React.ReactElement {
  const [items, setItems] = useState<CalendarItem[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [view, setView] = useState<CalView>("All");
  const [all, setAll] = useState(false);
  const openStock = useOpenStock();

  const [logos, setLogos] = useState<Record<string, string | null>>({});

  useEffect(() => {
    let alive = true;
    fetchCalendar(14).then((xs) => alive && setItems(xs)).catch(() => alive && setFailed(true));
    return () => { alive = false; };
  }, []);

  // The 500 first: an event for a company people hold is the one to see.
  const rows = useMemo(() => {
    const xs = (items ?? []).filter((i) => inView(i, view));
    return xs.sort((a, b) =>
      a.date.localeCompare(b.date)
      || Number(!(a.symbol && stocks.has(a.symbol))) - Number(!(b.symbol && stocks.has(b.symbol)))
      || (stocks.get(b.symbol ?? "")?.mcap_cr ?? 0) - (stocks.get(a.symbol ?? "")?.mcap_cr ?? 0));
  }, [items, view, stocks]);
  const shown = all ? rows.slice(0, 80) : rows.slice(0, 18);

  // Logos for the companies outside the 500, fetched for what is on screen.
  useEffect(() => {
    const want = shown.map((i) => i.symbol).filter((x): x is string => !!x && !stocks.has(x) && !(x in logos));
    if (!want.length) return;
    let alive = true;
    void getCompanyLogos(want).then((m) => {
      if (alive) setLogos((cur) => ({ ...cur, ...Object.fromEntries(want.map((x) => [x, m[x] ?? null])) }));
    });
    return () => { alive = false; };
  }, [shown, stocks, logos]);

  const days = useMemo(() => {
    const out: { date: string; items: CalendarItem[] }[] = [];
    for (const i of shown) {
      const last = out[out.length - 1];
      if (last && last.date === i.date) last.items.push(i);
      else out.push({ date: i.date, items: [i] });
    }
    return out;
  }, [shown]);

  return (
    <div className={cx(s.card, s.cardPad)}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <Segmented size="sm" label="Event type" options={["All", "Results", "Dividends", "Macro"] as const} value={view} onChange={(v) => { setView(v); setAll(false); }} />
        <span className={cx(s.stamp, s.num)}>{rows.length} in the next two weeks</span>
      </div>
      {failed ? (
        <Empty text="The calendar is unavailable right now." />
      ) : !items ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} style={{ height: 16, width: `${90 - i * 8}%` }} />)}
        </div>
      ) : days.length === 0 ? (
        <Empty text="Nothing scheduled." />
      ) : (
        <div className="flex flex-col gap-5">
          {days.map((d) => (
            <div key={d.date} className="min-w-0">
              <div className={cx(s.label, "mb-1.5 text-[12px] font-semibold")}>
                {new Date(`${d.date}T00:00:00`).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short" })}
              </div>
              <div className="grid gap-x-6" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(250px, 1fr))" }}>
              {d.items.map((i, k) => {
                const listed = i.symbol && stocks.has(i.symbol);
                const company = i.symbol && i.kind !== "macro";
                const logo = i.symbol ? stocks.get(i.symbol)?.logo ?? logos[i.symbol] ?? null : null;
                return (
                  <div
                    key={`${i.symbol}-${i.kind}-${k}`}
                    className={s.row}
                    style={{ gridTemplateColumns: "30px minmax(0,1fr) 28px", cursor: listed ? "pointer" : "default" }}
                    onClick={() => listed && openStock(i.symbol!)}
                  >
                    {company ? <Mark src={logo} label={i.title || i.symbol!} size={30} /> : <KindMark kind={i.kind} size={30} />}
                    <span className="min-w-0">
                      <span className="block truncate text-[12.5px] font-semibold">
                        {company ? i.symbol : i.title}
                        {i.time ? <span className="ml-1.5 font-normal">{i.time}</span> : null}
                      </span>
                      <span className={cx(s.subname, "block")}>{company ? eventLine(i) : i.detail || i.region || ""}</span>
                    </span>
                    {company ? <KindMark kind={i.kind} size={24} /> : <span />}
                  </div>
                );
              })}
              </div>
            </div>
          ))}
        </div>
      )}
      {rows.length > 18 && (
        <div className="mt-3 flex justify-center">
          <button type="button" className={s.ghostBtn} onClick={() => setAll((a) => !a)}>
            {all ? "Show fewer" : `Show all ${Math.min(rows.length, 80)}`}
          </button>
        </div>
      )}
    </div>
  );
}
