"use client";

/**
 * Explore: the market cut a dozen ways. Every list is a rule over the same
 * 500 stocks, written in lib/markets.ts, and follows the sector picked above.
 */

import { useEffect, useMemo, useState } from "react";
import { Info } from "lucide-react";
import {
  LENSES,
  fmtPrice,
  rangePosition,
  type Lens,
  type LensContext,
  type MarketSector,
  type MarketStock,
} from "@/lib/markets";
import { Delta, Empty, Hint, Mark, RangeBar, RowActions, Segmented, css as s, cx, useOpenStock } from "./parts";

const GROUPS = ["Movers", "Trend", "Value", "Setups"] as const;
type Group = (typeof GROUPS)[number];

export function Explore({
  stocks,
  sectors,
  ctx,
  sector,
  onClearSector,
  compact,
  onVisible,
}: {
  stocks: MarketStock[];
  sectors: MarketSector[];
  ctx: LensContext;
  sector: string | null;
  onClearSector: () => void;
  compact: boolean;
  onVisible?: (lens: Lens, rows: MarketStock[]) => void;
}): React.ReactElement {
  const [group, setGroup] = useState<Group>("Movers");
  const [lensId, setLensId] = useState("gainers");
  const [more, setMore] = useState(false);
  const lens = LENSES.find((l) => l.id === lensId) ?? LENSES[0]!;
  const openStock = useOpenStock();

  const pool = useMemo(() => (sector ? stocks.filter((x) => x.sector === sector) : stocks), [stocks, sector]);
  const rows = useMemo(() => lens.pick(pool, ctx), [lens, pool, ctx]);
  const shown = rows.slice(0, more ? 25 : 10);
  const sectorLabel = sector ? sectors.find((x) => x.key === sector)?.label : null;

  // The page assistant reads what is on screen.
  useEffect(() => {
    onVisible?.(lens, rows.slice(0, more ? 25 : 10));
  }, [onVisible, lens, rows, more]);

  const cols = compact
    ? "minmax(0,1fr) 72px 76px"
    : "22px minmax(0,1fr) 96px 84px 104px 84px 64px";

  const pickGroup = (g: Group): void => {
    setGroup(g);
    setLensId(LENSES.find((l) => l.group === g)!.id);
    setMore(false);
  };

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Segmented label="List group" options={GROUPS} value={group} onChange={pickGroup} />
        <div className={cx(s.chipRow, "min-w-[260px] flex-1")}>
          {LENSES.filter((l) => l.group === group).map((l) => (
            <Hint key={l.id} text={l.hint}>
              <button
                type="button"
                className={s.chip}
                data-active={l.id === lens.id}
                onClick={() => { setLensId(l.id); setMore(false); }}
              >
                {l.label}
              </button>
            </Hint>
          ))}
        </div>
      </div>

      <div className={cx(s.card, s.cardPad)}>
        <div className="mb-2 flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <span className="text-[13px] font-semibold">{lens.label}</span>
            <Hint text={lens.hint}>
              <button type="button" className={s.iconBtn} aria-label="How this list is made" style={{ width: 22, height: 22 }}>
                <Info size={13} strokeWidth={1.8} />
              </button>
            </Hint>
            {sectorLabel && (
              <button type="button" className={s.ghostBtn} style={{ padding: "3px 9px", fontSize: 12 }} onClick={onClearSector}>
                {sectorLabel} ×
              </button>
            )}
          </div>
          <span className={cx(s.stamp, s.num)}>{rows.length} {rows.length === 1 ? "stock" : "stocks"}</span>
        </div>

        <div className={s.headRow} style={{ gridTemplateColumns: cols }}>
          {!compact && <span />}
          <span>Company</span>
          {!compact && <span className="text-right">Price</span>}
          <span className="text-right">1D</span>
          {!compact && <span className="text-right">{lens.column.label}</span>}
          {compact ? <span className="text-right">{lens.column.label}</span> : <span className="text-right">52W range</span>}
          {!compact && <span />}
        </div>

        {shown.length === 0 ? (
          <Empty text={sectorLabel ? `Nothing in ${sectorLabel} fits this list today.` : "Nothing fits this list today."} />
        ) : (
          shown.map((x, i) => {
            const signed = lens.column.signed?.(x);
            const metric = lens.column.value(x, ctx);
            return (
              <div
                key={x.symbol}
                role="link"
                tabIndex={0}
                className={s.row}
                style={{ gridTemplateColumns: cols }}
                onClick={() => openStock(x.symbol)}
                onKeyDown={(e) => { if (e.key === "Enter") openStock(x.symbol); }}
              >
                {!compact && <span className={cx(s.num, s.muted, "text-[12px]")}>{i + 1}</span>}
                <span className="flex min-w-0 items-center gap-2.5">
                  <Mark src={x.logo} label={x.name} />
                  <span className="min-w-0">
                    <span className={cx(s.symbol, "block truncate")}>{x.symbol}</span>
                    <span className={cx(s.subname, "block")}>{x.name}</span>
                  </span>
                </span>
                {!compact && <span className={cx(s.num, "text-right text-[13px]")}>{fmtPrice(x.price)}</span>}
                <Delta v={x.change} className="text-right text-[13px] font-medium" />
                {!compact && (
                  <span className={cx(s.num, "text-right text-[13px]", signed != null && signed !== 0 && (signed > 0 ? s.up : s.down))}>
                    {metric}
                  </span>
                )}
                {compact ? (
                  <span className={cx(s.num, "text-right text-[12.5px]", signed != null && signed !== 0 && (signed > 0 ? s.up : s.down))}>{metric}</span>
                ) : (
                  <span className="flex justify-end"><RangeBar pos={rangePosition(x)} /></span>
                )}
                {!compact && (
                  <span className="flex justify-end">
                    <RowActions symbol={x.symbol} name={x.name} />
                  </span>
                )}
              </div>
            );
          })
        )}

        {rows.length > 10 && (
          <div className="mt-2 flex justify-center">
            <button type="button" className={s.ghostBtn} onClick={() => setMore((m) => !m)}>
              {more ? "Show fewer" : `Show ${Math.min(25, rows.length)}`}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
