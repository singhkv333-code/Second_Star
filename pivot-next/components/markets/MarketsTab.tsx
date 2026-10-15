"use client";

/**
 * Markets — the whole market on one scrollable page.
 *
 *   header      title, when the prices are from, the asset-class switch
 *   Stocks      Overview (brief, breadth, benchmarks) · Sectors (heatmap,
 *               sector moves, rotation, one sector up close) · Explore (the
 *               lists) · Battles · Calendar
 *   Indices / Commodities / Crypto / Currencies   boards from the chart server
 *   rail        market news, tagged with the stocks each story names
 *
 * One data call (`/api/markets/explore`) feeds every stock section; the
 * lists, the heatmap and the battles are computed from it in the browser,
 * so moving between them never waits. The page tells the page assistant what
 * is on screen through `publishVisible("markets", …)`.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowsClockwise } from "./icons";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Skeleton } from "@/components/ui/skeleton";
import { publishVisible } from "@/lib/assist";
import { isMarketOpen } from "@/lib/market-hours";
import { isError } from "@/lib/types";
import {
  PERIODS,
  fmtDay,
  fmtPct,
  getMarketExplore,
  lensContext,
  sectorReturn,
  type AssetClass,
  type Lens,
  type MarketExplore,
  type MarketStock,
  type Period,
} from "@/lib/markets";
import { AssetBoard } from "./AssetBoard";
import { Battles } from "./Battles";
import { Explore } from "./Explore";
import { EventsCalendar, NewsRail } from "./NewsRail";
import { BreadthCard, BriefCard, MarketStrip } from "./Overview";
import { Heatmap, RotationMap, SectorBars, SectorDetail, useWidth } from "./Sectors";
import { CardHead, Empty, Hint, SectionHead, Segmented, css as s, cx } from "./parts";

type View = "stocks" | AssetClass;
const VIEWS: { value: View; label: string }[] = [
  { value: "stocks", label: "Stocks" },
  { value: "indices", label: "Indices" },
  { value: "commodities", label: "Commodities" },
  { value: "crypto", label: "Crypto" },
  { value: "currencies", label: "Currencies" },
];

const SECTIONS = [
  { id: "mk-overview", label: "Overview" },
  { id: "mk-sectors", label: "Sectors" },
  { id: "mk-explore", label: "Explore" },
  { id: "mk-battles", label: "Battles" },
  { id: "mk-calendar", label: "Calendar" },
] as const;

const RAIL_MIN = 1180;
const HEAT_CARD_H = 460 + 16 + 2; // the heatmap, the card's padding and border     // page width at which news gets its own column
const REFRESH_MS = 60_000;

export function MarketsTab(): React.ReactElement {
  const [view, setView] = useState<View>("stocks");
  const [data, setData] = useState<MarketExplore | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sector, setSector] = useState<string | null>(null);
  const [period, setPeriod] = useState<Period>("1D");
  const [rootRef, width] = useWidth<HTMLDivElement>();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<string>(SECTIONS[0].id);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    const load = (): void => {
      getMarketExplore()
        .then((r) => {
          if (!alive) return;
          if (isError(r)) setError(r.error.message || "Market data is unavailable right now.");
          else { setData(r.data); setError(null); }
        })
        .catch(() => alive && setError("Market data is unavailable right now."));
    };
    load();
    const id = window.setInterval(load, REFRESH_MS);
    return () => { alive = false; window.clearInterval(id); };
  }, [reload]);

  const stocks = useMemo(() => new Map((data?.stocks ?? []).map((x) => [x.symbol, x])), [data]);
  const ctx = useMemo(() => lensContext(data?.sectors ?? []), [data]);
  const sectorRow = data?.sectors.find((x) => x.key === sector) ?? null;

  const rail = width >= RAIL_MIN;
  const mainW = rail ? width - 360 : width;
  const wide = mainW >= 900;
  const battlesPerPage = mainW >= 1100 ? 3 : mainW >= 720 ? 2 : 1;
  // Phones get shorter sections rather than the desktop's stacked in full.
  const phone = width > 0 && width < 640;

  // The section bar scrolls sideways on a phone: keep the current section in
  // view, and fade the edge only while more lies beyond it.
  const navRef = useRef<HTMLDivElement>(null);
  const [navMore, setNavMore] = useState(false);
  const syncNav = (): void => {
    const el = navRef.current;
    if (el) setNavMore(el.scrollLeft + el.clientWidth < el.scrollWidth - 4);
  };
  useEffect(() => {
    const el = navRef.current;
    const on = el?.querySelector<HTMLElement>('[data-active="true"]');
    if (el && on) {
      const left = on.offsetLeft - 16, right = on.offsetLeft + on.offsetWidth + 16;
      if (left < el.scrollLeft) el.scrollTo({ left, behavior: "smooth" });
      else if (right > el.scrollLeft + el.clientWidth) el.scrollTo({ left: right - el.clientWidth, behavior: "smooth" });
    }
    syncNav();
  }, [active, width]);

  // Scroll-spy over the stock sections.
  useEffect(() => {
    if (view !== "stocks") return;
    const root = scrollRef.current;
    if (!root) return;
    const io = new IntersectionObserver(
      (entries) => {
        const top = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (top) setActive(top.target.id);
      },
      { root, rootMargin: "-56px 0px -60% 0px" },
    );
    SECTIONS.forEach((x) => { const el = document.getElementById(x.id); if (el) io.observe(el); });
    return () => io.disconnect();
  }, [view, data]);

  const jump = (id: string): void => {
    const root = scrollRef.current;
    const el = document.getElementById(id);
    if (!root || !el) return;
    root.scrollTo({ top: el.offsetTop - 52, behavior: "smooth" });
  };

  // What the page assistant sees.
  const visibleLens = useRef<string[]>([]);
  const publish = useCallback(() => {
    if (!data) return;
    const b = data.breadth;
    const top = (xs: MarketStock[]): string => xs.map((x) => `${x.symbol} ${fmtPct(x.change)}`).join(", ");
    const moved = data.stocks.filter((x) => x.change != null);
    const up = [...moved].sort((p, q) => (q.change ?? 0) - (p.change ?? 0)).slice(0, 5);
    const down = [...moved].sort((p, q) => (p.change ?? 0) - (q.change ?? 0)).slice(0, 5);
    const secs = [...data.sectors].filter((x) => x.change != null).sort((p, q) => (q.change ?? 0) - (p.change ?? 0));
    publishVisible("markets", [
      `Markets page, the ${data.universe.size} largest NSE companies, prices ${data.as_of.prices === "live" ? "live" : `from the ${data.as_of.eod_day} close`}.`,
      `Breadth: ${b.advancers} up, ${b.decliners} down; ${b.above_sma200} of ${b.sma200_total} above their 200-day average; ${b.near_high} near a 52-week high, ${b.near_low} near a low.`,
      `Sectors today: ${secs.map((x) => `${x.label} ${fmtPct(x.change)}`).join(", ")}.`,
      `Top gainers: ${top(up)}.`,
      `Top losers: ${top(down)}.`,
      sectorRow ? `Selected sector: ${sectorRow.label}, ${fmtPct(sectorRow.change)} today, ${fmtPct(sectorRow.ret_1m, 1)} over a month.` : "",
      ...visibleLens.current,
    ]);
  }, [data, sectorRow]);
  useEffect(publish, [publish]);
  const onLens = useCallback((lens: Lens, rows: MarketStock[]) => {
    visibleLens.current = [`List on screen, "${lens.label}" (${lens.hint}): ${rows.map((x) => `${x.symbol} ${fmtPct(x.change)}`).join(", ")}.`];
    publish();
  }, [publish]);

  const stamp = data
    ? `${isMarketOpen() ? "Market open" : "Market closed"} · ${data.as_of.prices === "live"
      ? data.as_of.price_source === "kite" ? "live prices" : "prices delayed up to 15 min"
      : `prices from the ${fmtDay(data.as_of.eod_day)} close`}`
    : "";

  return (
    <TooltipProvider delayDuration={200}>
      <div ref={rootRef} className={cx(s.root, "flex h-full min-h-0 w-full")} data-testid="markets-tab">
        <div ref={scrollRef} className="relative min-w-0 flex-1 overflow-y-auto overflow-x-hidden">
          {/* header */}
          <div className="px-4 pt-5 lg:px-8 lg:pt-7">
            <div className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
              <div className="min-w-0">
                <h1 className={s.pageTitle}>Markets</h1>
                <p className={cx(s.stamp, "mt-1.5")}>{(view === "stocks" ? stamp : data ? (isMarketOpen() ? "Market open" : "Market closed") : "") || " "}</p>
              </div>
              <div className={cx(phone && s.segFill)}>
                <Segmented label="Asset class" options={VIEWS} value={view} onChange={(v) => { setView(v); scrollRef.current?.scrollTo({ top: 0 }); }} />
              </div>
            </div>
          </div>

          {view === "stocks" ? (
            <>
              <nav className={cx(s.navBar, "mt-4 px-4 py-2 lg:px-8")} aria-label="Sections">
                <div ref={navRef} onScroll={syncNav} className={cx(s.fadeX, "flex items-center gap-1 overflow-x-auto")} data-fade={navMore} style={{ scrollbarWidth: "none" }}>
                  {SECTIONS.map((x) => (
                    <button key={x.id} type="button" className={s.navLink} data-active={active === x.id} onClick={() => jump(x.id)}>
                      {x.label}
                    </button>
                  ))}
                  {!rail && (
                    <button type="button" className={s.navLink} onClick={() => jump("mk-news")}>News</button>
                  )}
                </div>
              </nav>

              {error && !data ? (
                <div className="px-4 py-16 lg:px-8">
                  <Empty text={error} />
                  <div className="flex justify-center">
                    <button type="button" className={s.ghostBtn} onClick={() => setReload((n) => n + 1)}>
                      <ArrowsClockwise size={14} /> Try again
                    </button>
                  </div>
                </div>
              ) : (
                <div className={cx("flex flex-col px-4 pb-32 lg:px-8", phone ? "gap-9 pt-4" : "gap-12 pt-6")}>
                  {/* Overview */}
                  <section id="mk-overview" aria-label="Overview" className="flex flex-col gap-3">
                    <div className="grid gap-3" style={{ gridTemplateColumns: wide ? "minmax(0,1.7fr) minmax(0,1fr)" : "minmax(0,1fr)" }}>
                      <BriefCard data={data} />
                      <BreadthCard breadth={data?.breadth ?? null} />
                    </div>
                    <MarketStrip />
                  </section>

                  {/* Sectors */}
                  <section id="mk-sectors" aria-label="Sectors">
                    <SectionHead
                      title="Sectors"
                      right={<Segmented size="sm" label="Period" options={PERIODS} value={period} onChange={setPeriod} />}
                    />
                    <div className={cx(s.chipRow, "mb-3")}>
                      <button type="button" className={s.chip} data-active={sector === null} onClick={() => setSector(null)}>
                        All sectors
                      </button>
                      {(data?.sectors ?? []).map((x) => {
                        const v = sectorReturn(x, period);
                        return (
                          <button key={x.key} type="button" className={s.chip} data-active={sector === x.key} onClick={() => setSector(sector === x.key ? null : x.key)}>
                            {x.label}
                            <span className={cx(s.chipValue, v == null || v === 0 ? s.muted : v > 0 ? s.up : s.down)}>{fmtPct(v, 1)}</span>
                          </button>
                        );
                      })}
                    </div>
                    <div className="grid gap-3" style={{ gridTemplateColumns: wide ? "minmax(0,2.1fr) minmax(0,1fr)" : "minmax(0,1fr)" }}>
                      <div className={cx(s.card, "p-2")}>
                        {data ? (
                          <Heatmap stocks={data.stocks} sectors={data.sectors} selected={sector} period={period} onSelectSector={setSector} />
                        ) : (
                          <Skeleton style={{ height: 460 }} />
                        )}
                      </div>
                      <div className={cx(s.card, s.cardPad, "flex min-h-0 flex-col")} style={{ height: wide ? HEAT_CARD_H : undefined }}>
                        <CardHead title={`Sector moves · ${period}`} />
                        <div className={cx(s.scrollHide, "-mx-1 min-h-0 flex-1 overflow-y-auto px-1")}>
                          {data ? (
                            <SectorBars sectors={data.sectors} period={period} selected={sector} onSelect={setSector} limit={phone ? 8 : undefined} />
                          ) : (
                            <div className="flex flex-col gap-2">{Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} style={{ height: 14 }} />)}</div>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className="mt-3 grid gap-3" style={{ gridTemplateColumns: wide ? "minmax(0,1.5fr) minmax(0,1fr)" : "minmax(0,1fr)" }}>
                      <div className={cx(s.card, s.cardPad)}>
                        <CardHead
                          title="Rotation"
                          right={
                            <Hint text="Each sector's median one-month return against the market's (across) and its one-week return against the market's (up). Leading sectors are ahead on both.">
                              <span className={cx(s.stamp, "cursor-help")}>How to read</span>
                            </Hint>
                          }
                        />
                        {data ? <RotationMap sectors={data.sectors} selected={sector} onSelect={setSector} /> : <Skeleton style={{ height: 400 }} />}
                      </div>
                      <div className={cx(s.card, s.cardPad)}>
                        {data ? (
                          <SectorDetail sector={sectorRow} sectors={data.sectors} stocks={stocks} onClear={() => setSector(null)} onSelect={setSector} />
                        ) : (
                          <Skeleton style={{ height: 300 }} />
                        )}
                      </div>
                    </div>
                  </section>

                  {/* Explore */}
                  <section id="mk-explore" aria-label="Explore">
                    <SectionHead title="Explore" />
                    {data ? (
                      <Explore
                        stocks={data.stocks}
                        sectors={data.sectors}
                        ctx={ctx}
                        sector={sector}
                        onClearSector={() => setSector(null)}
                        compact={mainW < 720}
                        onVisible={onLens}
                      />
                    ) : (
                      <Skeleton style={{ height: 420 }} />
                    )}
                  </section>

                  {/* Battles */}
                  <section id="mk-battles" aria-label="Battles">
                    <SectionHead title="Battles" />
                    {data ? <Battles stocks={stocks} perPage={battlesPerPage} /> : <Skeleton style={{ height: 420 }} />}
                  </section>

                  {/* Calendar */}
                  <section id="mk-calendar" aria-label="Calendar">
                    <SectionHead title="Calendar" />
                    <EventsCalendar stocks={stocks} initial={phone ? 8 : 18} />
                  </section>

                  {!rail && (
                    <section id="mk-news" aria-label="News" className={cx(s.card, s.cardPad)}>
                      <NewsRail stocks={stocks} sector={sector} sectorLabel={sectorRow?.label ?? null} limit={phone ? 30 : 12} compact={phone} />
                    </section>
                  )}

                  <Footnote data={data} />
                </div>
              )}
            </>
          ) : (
            <div className="px-4 pb-32 pt-6 lg:px-8">
              <AssetBoard kind={view} columns={Math.max(1, Math.min(4, Math.floor(mainW / 250)))} />
            </div>
          )}
        </div>

        {rail && (
          <aside
            className={cx(s.scrollHide, "w-[360px] shrink-0 overflow-y-auto border-l px-5 pb-28 pt-6")}
            style={{ borderColor: "var(--glass-border)" }}
            aria-label="News"
          >
            <NewsRail stocks={stocks} sector={sector} sectorLabel={sectorRow?.label ?? null} />
          </aside>
        )}
      </div>
    </TooltipProvider>
  );
}

function Footnote({ data }: { data: MarketExplore | null }): React.ReactElement | null {
  if (!data) return null;
  return (
    <p className={s.stamp} style={{ whiteSpace: "normal", maxWidth: 760, lineHeight: 1.55 }}>
      The {data.universe.size} largest NSE companies by market cap (price × shares in the latest shareholding filing).
      Returns, RSI, volume and trend are end-of-day{data.as_of.eod_day ? ` as of ${fmtDay(data.as_of.eod_day)}` : ""}. P/E is price over trailing EPS.
      This is analysis, not financial advice.
    </p>
  );
}
