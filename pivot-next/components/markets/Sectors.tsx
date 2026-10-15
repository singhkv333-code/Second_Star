"use client";

/**
 * Sectors: where the money sits (the heatmap, sized by market cap), how each
 * sector moved, and where each one is in its rotation against the market.
 */

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChatCircle } from "./icons";
import { askPivot } from "@/lib/launch";
import {
  fmtCr,
  fmtPct,
  fmtPrice,
  moveTint,
  sectorReturn,
  squarify,
  stockReturn,
  type MarketSector,
  type MarketStock,
  type Period,
} from "@/lib/markets";
import { CardHead, Delta, Empty, Mark, css as s, cx, useOpenStock } from "./parts";

export function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.getBoundingClientRect().width);
    const ro = new ResizeObserver(([e]) => { if (e) setW(e.contentRect.width); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

// ── heatmap ──────────────────────────────────────────────────────────────

type Tip = { x: number; y: number; stock: MarketStock };

const PER_SECTOR = 16;
const LABEL_H = 20;

export function Heatmap({
  stocks,
  sectors,
  selected,
  period,
  onSelectSector,
}: {
  stocks: MarketStock[];
  sectors: MarketSector[];
  selected: string | null;
  period: Period;
  onSelectSector: (key: string) => void;
}): React.ReactElement {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [tip, setTip] = useState<Tip | null>(null);
  const openStock = useOpenStock();
  const height = width < 640 ? 360 : 460;

  const layout = useMemo(() => {
    if (!width) return [];
    const bySector = new Map<string, MarketStock[]>();
    for (const x of stocks) {
      if (selected && x.sector !== selected) continue;
      const list = bySector.get(x.sector) ?? [];
      list.push(x);
      bySector.set(x.sector, list);
    }
    const groups = [...bySector.entries()].map(([key, xs]) => ({
      key,
      label: sectors.find((t) => t.key === key)?.label ?? key,
      stocks: xs.sort((a, b) => b.mcap_cr - a.mcap_cr).slice(0, selected ? 60 : PER_SECTOR),
    }));
    const outer = squarify(groups, (g) => g.stocks.reduce((t, x) => t + x.mcap_cr, 0), { x: 0, y: 0, w: width, h: height });
    return outer.map((g) => {
      const showLabel = !selected && g.w > 70 && g.h > 44;
      const inner = squarify(g.item.stocks, (x) => x.mcap_cr, {
        x: g.x,
        y: g.y + (showLabel ? LABEL_H : 0),
        w: g.w,
        h: g.h - (showLabel ? LABEL_H : 0),
      });
      return { ...g, showLabel, inner };
    });
  }, [stocks, sectors, selected, width, height]);

  return (
    <div ref={ref} className={s.heat} style={{ height }} onMouseLeave={() => setTip(null)}>
      {layout.map((g) => (
        <div key={g.item.key}>
          {g.showLabel && (
            <button
              type="button"
              className={s.heatSector}
              style={{ left: g.x, top: g.y, width: g.w, height: LABEL_H, pointerEvents: "auto", background: "none", border: "none", textAlign: "left", cursor: "pointer" }}
              onClick={() => onSelectSector(g.item.key)}
            >
              {g.item.label}
            </button>
          )}
          {g.inner.map((c) => {
            const v = stockReturn(c.item, period);
            const big = c.w > 52 && c.h > 34;
            const huge = c.w > 96 && c.h > 60;
            return (
              <div
                key={c.item.symbol}
                role="button"
                tabIndex={0}
                aria-label={`${c.item.name}, ${fmtPct(v)}`}
                className={s.heatCell}
                style={{ left: c.x, top: c.y, width: c.w, height: c.h, background: moveTint(v, period === "1D" ? 3 : period === "1W" ? 6 : period === "1M" ? 12 : 40) }}
                onClick={() => openStock(c.item.symbol)}
                onKeyDown={(e) => { if (e.key === "Enter") openStock(c.item.symbol); }}
                onMouseMove={(e) => {
                  const box = ref.current?.getBoundingClientRect();
                  if (box) setTip({ x: e.clientX - box.left, y: e.clientY - box.top, stock: c.item });
                }}
              >
                {big && (
                  <div style={{ lineHeight: 1.15 }}>
                    <div className="truncate" style={{ fontSize: huge ? 13 : 11, fontWeight: 600, letterSpacing: "-0.01em" }}>
                      {c.item.symbol}
                    </div>
                    <div className={s.num} style={{ fontSize: huge ? 12 : 10.5, color: "var(--text-primary)" }}>{fmtPct(v)}</div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      ))}
      {tip && <HeatTip tip={tip} width={width} height={height} period={period} sectors={sectors} />}
    </div>
  );
}

function HeatTip({ tip, width, height, period, sectors }: { tip: Tip; width: number; height: number; period: Period; sectors: MarketSector[] }): React.ReactElement {
  const left = Math.min(tip.x + 14, width - 230);
  const top = tip.y + 120 > height ? tip.y - 110 : tip.y + 14;
  const x = tip.stock;
  return (
    <div className={s.tip} style={{ left: Math.max(4, left), top }}>
      <div className="flex items-center gap-2">
        <Mark src={x.logo} label={x.name} size={22} />
        <div className="min-w-0">
          <div className={s.symbol}>{x.symbol}</div>
          <div className={s.subname}>{x.name}</div>
        </div>
      </div>
      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-0.5">
        <span className={s.muted}>Price</span><span className={cx(s.num, "text-right")}>{fmtPrice(x.price)}</span>
        <span className={s.muted}>{period}</span><span className="text-right"><Delta v={stockReturn(x, period)} /></span>
        <span className={s.muted}>Market cap</span><span className={cx(s.num, "text-right")}>₹{fmtCr(x.mcap_cr)}</span>
        <span className={s.muted}>Sector</span><span className="truncate text-right">{sectors.find((t) => t.key === x.sector)?.label}</span>
      </div>
    </div>
  );
}

// ── sector bars ──────────────────────────────────────────────────────────

export function SectorBars({
  sectors,
  period,
  selected,
  onSelect,
}: {
  sectors: MarketSector[];
  period: Period;
  selected: string | null;
  onSelect: (key: string | null) => void;
}): React.ReactElement {
  const rows = useMemo(
    () => sectors.filter((x) => sectorReturn(x, period) != null)
      .sort((a, b) => (sectorReturn(b, period) ?? 0) - (sectorReturn(a, period) ?? 0)),
    [sectors, period],
  );
  const max = Math.max(0.01, ...rows.map((r) => Math.abs(sectorReturn(r, period) ?? 0)));
  if (!rows.length) return <Empty text="No sector moves to show." />;
  return (
    <div className="flex flex-col">
      {rows.map((r) => {
        const v = sectorReturn(r, period) ?? 0;
        const w = (Math.abs(v) / max) * 50;
        const active = selected === r.key;
        return (
          <button
            key={r.key}
            type="button"
            onClick={() => onSelect(active ? null : r.key)}
            className={cx(s.row, "text-left")}
            style={{ gridTemplateColumns: "minmax(0,1fr) auto", rowGap: 6, padding: "8px 10px", border: "none", background: active ? "var(--surface-active)" : undefined, font: "inherit", color: "inherit" }}
            aria-pressed={active}
          >
            <span className="truncate text-[12.5px] font-medium">{r.label}</span>
            <Delta v={v} className="text-right text-[12.5px]" />
            <span className="relative col-span-2 h-[4px] rounded-full" style={{ background: "var(--surface-track)" }} aria-hidden>
              <span className="absolute -inset-y-[3px] left-1/2 w-px" style={{ background: "var(--glass-border-hover)" }} />
              <span
                className="absolute inset-y-0 rounded-full"
                style={{
                  left: v >= 0 ? "50%" : `${50 - w}%`,
                  width: `${Math.max(w, 0.6)}%`,
                  background: v >= 0 ? "var(--color-profit)" : "var(--color-loss)",
                  opacity: active ? 1 : 0.7,
                }}
              />
            </span>
          </button>
        );
      })}
    </div>
  );
}

// ── rotation map ─────────────────────────────────────────────────────────

export function RotationMap({
  sectors,
  selected,
  onSelect,
}: {
  sectors: MarketSector[];
  selected: string | null;
  onSelect: (key: string | null) => void;
}): React.ReactElement {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<MarketSector | null>(null);
  const height = 400;
  const pad = 28;
  const pts = sectors.filter((x) => x.rs_1m != null && x.rs_1w != null && x.key !== "diversified");
  const span = (vals: number[]): number => Math.max(1, ...vals.map(Math.abs)) * 1.18;
  const sx = span(pts.map((p) => p.rs_1m!));
  const sy = span(pts.map((p) => p.rs_1w!));
  const maxCap = Math.max(1, ...pts.map((p) => p.mcap_cr));
  const X = (v: number): number => pad + ((v + sx) / (2 * sx)) * (width - 2 * pad);
  const Y = (v: number): number => pad + ((sy - v) / (2 * sy)) * (height - 2 * pad);

  // Each label tries right, left, above and below its bubble and takes the
  // first spot that clears every bubble and every label already placed. A
  // sector with no free spot goes unlabelled; hovering it still names it.
  const labels = useMemo(() => {
    const radius = (p: MarketSector): number => 5 + 13 * Math.sqrt(p.mcap_cr / maxCap);
    const circles = pts.map((p) => ({ x: X(p.rs_1m!), y: Y(p.rs_1w!), r: radius(p) }));
    const boxes: { x: number; y: number; w: number; h: number }[] = [];
    const hits = (bx: { x: number; y: number; w: number; h: number }): boolean =>
      bx.x < pad || bx.x + bx.w > width - pad || bx.y < pad || bx.y + bx.h > height - pad
      || boxes.some((t) => bx.x < t.x + t.w && t.x < bx.x + bx.w && bx.y < t.y + t.h && t.y < bx.y + bx.h)
      || circles.some((c) => {
        const nx = Math.max(bx.x, Math.min(c.x, bx.x + bx.w));
        const ny = Math.max(bx.y, Math.min(c.y, bx.y + bx.h));
        return (nx - c.x) ** 2 + (ny - c.y) ** 2 < c.r * c.r;
      });
    const out = new Map<string, { x: number; y: number; anchor: "start" | "middle" | "end" }>();
    for (const p of [...pts].sort((a, b) => b.mcap_cr - a.mcap_cr)) {
      const cx0 = X(p.rs_1m!);
      const cy0 = Y(p.rs_1w!);
      const r = radius(p);
      const w = p.label.length * 6.2;
      const h = 13;
      const spots: { bx: { x: number; y: number; w: number; h: number }; at: { x: number; y: number; anchor: "start" | "middle" | "end" } }[] = [
        { bx: { x: cx0 + r + 4, y: cy0 - h / 2, w, h }, at: { x: cx0 + r + 4, y: cy0 + 4, anchor: "start" } },
        { bx: { x: cx0 - r - 4 - w, y: cy0 - h / 2, w, h }, at: { x: cx0 - r - 4, y: cy0 + 4, anchor: "end" } },
        { bx: { x: cx0 - w / 2, y: cy0 - r - 3 - h, w, h }, at: { x: cx0, y: cy0 - r - 6, anchor: "middle" } },
        { bx: { x: cx0 - w / 2, y: cy0 + r + 3, w, h }, at: { x: cx0, y: cy0 + r + 13, anchor: "middle" } },
      ];
      const spot = spots.find((o) => !hits(o.bx));
      if (!spot) continue;
      boxes.push(spot.bx);
      out.set(p.key, spot.at);
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pts.map((p) => `${p.key}:${p.rs_1m}:${p.rs_1w}`).join(), width]);

  return (
    <div ref={ref} className="relative" style={{ height }}>
      {width > 0 && pts.length > 0 ? (
        <svg width={width} height={height} role="img" aria-label="Sector rotation: one-month strength against one-week pace">
          <rect x={X(0)} y={pad} width={width - pad - X(0)} height={Y(0) - pad} fill="var(--color-profit)" opacity={0.04} />
          <rect x={pad} y={Y(0)} width={X(0) - pad} height={height - pad - Y(0)} fill="var(--color-loss)" opacity={0.04} />
          <line x1={X(0)} x2={X(0)} y1={pad} y2={height - pad} stroke="var(--glass-border-hover)" />
          <line x1={pad} x2={width - pad} y1={Y(0)} y2={Y(0)} stroke="var(--glass-border-hover)" />
          {[
            { t: "Leading", x: width - pad - 4, y: pad + 12, a: "end" },
            { t: "Weakening", x: width - pad - 4, y: height - pad - 6, a: "end" },
            { t: "Lagging", x: pad + 4, y: height - pad - 6, a: "start" },
            { t: "Improving", x: pad + 4, y: pad + 12, a: "start" },
          ].map((q) => (
            <text key={q.t} x={q.x} y={q.y} textAnchor={q.a as "start" | "end"} fontSize={10.5} fontWeight={600} letterSpacing="0.06em" fill="var(--text-primary)">
              {q.t.toUpperCase()}
            </text>
          ))}
          <text x={width - pad} y={height - 8} textAnchor="end" fontSize={10.5} fill="var(--text-primary)">1M vs market →</text>
          <text x={X(0) + 6} y={pad - 10} fontSize={10.5} fill="var(--text-primary)">↑ 1W vs market</text>
          {pts.map((p) => {
            const r = 5 + 13 * Math.sqrt(p.mcap_cr / maxCap);
            const active = selected === p.key;
            const lab = labels.get(p.key);
            return (
              <g
                key={p.key}
                style={{ cursor: "pointer" }}
                onClick={() => onSelect(active ? null : p.key)}
                onMouseEnter={() => setHover(p)}
                onMouseLeave={() => setHover(null)}
              >
                <circle
                  cx={X(p.rs_1m!)}
                  cy={Y(p.rs_1w!)}
                  r={r}
                  fill="var(--text-primary)"
                  fillOpacity={active ? 0.9 : hover?.key === p.key ? 0.32 : 0.14}
                  stroke="var(--text-primary)"
                  strokeOpacity={active ? 1 : 0.45}
                  strokeWidth={1}
                  style={{ transition: "fill-opacity 150ms" }}
                />
                {lab && (
                  <text
                    x={lab.x}
                    y={lab.y}
                    textAnchor={lab.anchor}
                    fontSize={11}
                    fontWeight={active ? 600 : 500}
                    fill={active ? "var(--text-primary)" : "var(--text-primary)"}
                  >
                    {p.label}
                  </text>
                )}
              </g>
            );
          })}
        </svg>
      ) : (
        <Empty text="Rotation needs a month of prices." height={height} />
      )}
      {hover && width > 0 && (
        <div className={s.tip} style={{ left: Math.min(X(hover.rs_1m!) + 16, width - 200), top: Math.max(4, Y(hover.rs_1w!) - 70) }}>
          <div className="font-semibold">{hover.label}</div>
          <div className="mt-1 grid grid-cols-2 gap-x-3">
            <span className={s.muted}>1M vs market</span><span className="text-right"><Delta v={hover.rs_1m} /></span>
            <span className={s.muted}>1W vs market</span><span className="text-right"><Delta v={hover.rs_1w} /></span>
            <span className={s.muted}>Stocks</span><span className={cx(s.num, "text-right")}>{hover.count}</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ── one sector up close ──────────────────────────────────────────────────

export function SectorDetail({
  sector,
  sectors,
  stocks,
  onClear,
  onSelect,
}: {
  sector: MarketSector | null;
  sectors: MarketSector[];
  stocks: Map<string, MarketStock>;
  onClear: () => void;
  onSelect: (key: string) => void;
}): React.ReactElement {
  const openStock = useOpenStock();
  if (!sector) return <Quadrants sectors={sectors} onSelect={onSelect} />;
  const leaders = sector.leaders.map((k) => stocks.get(k)).filter((x): x is MarketStock => !!x);
  const best = sector.best ? stocks.get(sector.best) : undefined;
  const worst = sector.worst ? stocks.get(sector.worst) : undefined;
  const quad = sector.rs_1m == null || sector.rs_1w == null ? null
    : sector.rs_1m >= 0 ? (sector.rs_1w >= 0 ? "Leading" : "Weakening") : (sector.rs_1w >= 0 ? "Improving" : "Lagging");

  return (
    <div className="flex h-full flex-col">
      <CardHead
        title={sector.label}
        right={<button type="button" className={s.ghostBtn} onClick={onClear}>All sectors</button>}
      />
      <div className="grid grid-cols-3 gap-3">
        <Fig label="Today"><Delta v={sector.change} /></Fig>
        <Fig label="1 month"><Delta v={sector.ret_1m} digits={1} /></Fig>
        <Fig label="1 year"><Delta v={sector.ret_1y} digits={1} /></Fig>
        <Fig label="Up / down"><span className={s.num}>{sector.advancers} / {sector.decliners}</span></Fig>
        <Fig label="Median P/E"><span className={s.num}>{sector.pe_median?.toFixed(1) ?? "—"}</span></Fig>
        <Fig label="Rotation"><span>{quad ?? "—"}</span></Fig>
      </div>
      <div className={cx(s.label, "mb-1 mt-4")}>Largest</div>
      <div className="flex flex-col">
        {leaders.map((x) => (
          <button
            key={x.symbol}
            type="button"
            className={s.row}
            style={{ gridTemplateColumns: "28px minmax(0,1fr) auto", border: "none", background: "none", font: "inherit", color: "inherit", textAlign: "left" }}
            onClick={() => openStock(x.symbol)}
          >
            <Mark src={x.logo} label={x.name} size={24} />
            <span className="min-w-0">
              <span className={cx(s.symbol, "block text-[12.5px]")}>{x.symbol}</span>
            </span>
            <Delta v={x.change} className="text-[12.5px]" />
          </button>
        ))}
      </div>
      {(best || worst) && (
        <div className="mt-2 grid grid-cols-2 gap-3 border-t pt-3" style={{ borderColor: "var(--glass-border)" }}>
          {best && <Fig label="Best today"><span className="text-[12.5px] font-semibold">{best.symbol} </span><Delta v={best.change} className="text-[12px]" /></Fig>}
          {worst && <Fig label="Worst today"><span className="text-[12.5px] font-semibold">{worst.symbol} </span><Delta v={worst.change} className="text-[12px]" /></Fig>}
        </div>
      )}
      <div className="mt-auto pt-4">
        <button
          type="button"
          className={s.ghostBtn}
          onClick={() => askPivot(`How is the ${sector.label} sector doing? It moved ${fmtPct(sector.change)} today and ${fmtPct(sector.ret_1m, 1)} over a month. What is driving it?`)}
        >
          <ChatCircle size={14} /> Ask about {sector.label}
        </button>
      </div>
    </div>
  );
}

const QUADS = [
  { name: "Leading", test: (m: number, w: number) => m >= 0 && w >= 0 },
  { name: "Improving", test: (m: number, w: number) => m < 0 && w >= 0 },
  { name: "Weakening", test: (m: number, w: number) => m >= 0 && w < 0 },
  { name: "Lagging", test: (m: number, w: number) => m < 0 && w < 0 },
] as const;

/** With no sector picked: which sectors sit in each corner of the map. */
function Quadrants({ sectors, onSelect }: { sectors: MarketSector[]; onSelect: (key: string) => void }): React.ReactElement {
  const placed = sectors.filter((x) => x.rs_1m != null && x.rs_1w != null && x.key !== "diversified");
  return (
    <div className="flex h-full flex-col">
      <CardHead title="Where each sector sits" />
      <div className="grid flex-1 grid-cols-2 gap-x-4 gap-y-5">
        {QUADS.map((q) => {
          const xs = placed.filter((x) => q.test(x.rs_1m!, x.rs_1w!))
            .sort((a, b) => (b.rs_1m! + b.rs_1w!) - (a.rs_1m! + a.rs_1w!));
          return (
            <div key={q.name} className="min-w-0">
              <div className={cx(s.label, "mb-1.5")}>{q.name} · {xs.length}</div>
              <div className="flex flex-col">
                {xs.length === 0 && <span className={cx(s.muted, "text-[12.5px]")}>None</span>}
                {xs.map((x) => (
                  <button
                    key={x.key}
                    type="button"
                    onClick={() => onSelect(x.key)}
                    className={cx(s.row, "text-left")}
                    style={{ gridTemplateColumns: "minmax(0,1fr) auto", padding: "5px 8px", margin: "0 -8px", border: "none", background: "none", font: "inherit", color: "inherit" }}
                  >
                    <span className="truncate text-[12.5px] font-medium">{x.label}</span>
                    <Delta v={x.ret_1m} digits={1} className="text-[12px]" />
                  </button>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      <p className={cx(s.stamp, "mt-3")} style={{ whiteSpace: "normal" }}>1-month returns. Pick a sector for its leaders and figures.</p>
    </div>
  );
}

function Fig({ label, children }: { label: string; children: React.ReactNode }): React.ReactElement {
  return (
    <div className="min-w-0">
      <div className={s.label}>{label}</div>
      <div className="mt-0.5 truncate text-[14px] font-semibold">{children}</div>
    </div>
  );
}

/** Re-render a child only once it scrolls near view. */
export function useInView<T extends HTMLElement>(margin = "200px"): [React.RefObject<T | null>, boolean] {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    const io = new IntersectionObserver(([e]) => { if (e?.isIntersecting) setSeen(true); }, { rootMargin: margin });
    io.observe(el);
    return () => io.disconnect();
  }, [margin, seen]);
  return [ref, seen];
}
