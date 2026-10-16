import { ImageResponse } from "next/og";
import { getDailyBars, getStock } from "@/lib/seo/data";
import { crore, num, pct } from "@/lib/seo/format";
import { priceStats } from "@/lib/seo/stockCopy";

/** The card a stock page shows when it is shared on WhatsApp, X, LinkedIn or
 *  Telegram: name, last close, the day's move and the year's shape. */

export const revalidate = 900;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "Share price, one-year chart and key numbers on Pivot";

// The bundled font has no rupee sign; "Rs" reads correctly everywhere.
const rs = (v: number | null | undefined): string =>
  v == null ? "—" : `Rs ${v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

export default async function Image({ params }: { params: Promise<{ symbol: string }> }) {
  let symbol = (await params).symbol;
  try { symbol = decodeURIComponent(symbol); } catch { /* raw */ }
  symbol = symbol.toUpperCase();
  const [s, bars] = await Promise.all([getStock(symbol).catch(() => null), getDailyBars(symbol)]);
  const stats = priceStats(bars);
  const closes = bars.map((b) => b.c);
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const W = 1040;
  const H = 230;
  const path = closes.length > 1
    ? closes.map((c, i) => `${i ? "L" : "M"}${((i / (closes.length - 1)) * W).toFixed(1)},${(H - ((c - min) / (max - min || 1)) * H).toFixed(1)}`).join("")
    : "";
  const up = closes.length > 1 ? closes[closes.length - 1]! >= closes[0]! : true;
  const change = s?.market?.change ?? null;
  const name = s?.name ?? symbol;

  return new ImageResponse(
    (
      <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column", background: "#ffffff", padding: "56px 80px", color: "#0d0d0e" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
          <div style={{ display: "flex", flexDirection: "column", maxWidth: 720 }}>
            <div style={{ fontSize: 60, fontWeight: 700, letterSpacing: -2, lineHeight: 1.05 }}>{name}</div>
            <div style={{ fontSize: 28, color: "#4d555c", marginTop: 12 }}>
              {`NSE: ${symbol}${s?.sector_label ? ` · ${s.sector_label}` : ""}`}
            </div>
          </div>
          <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end" }}>
            <div style={{ fontSize: 56, fontWeight: 700 }}>{rs(stats?.last ?? s?.market?.price)}</div>
            {change != null && (
              <div style={{ fontSize: 32, fontWeight: 600, color: change >= 0 ? "#059669" : "#dc2626", marginTop: 6 }}>{pct(change, 2, true)}</div>
            )}
          </div>
        </div>
        <div style={{ display: "flex", marginTop: 40 }}>
          {path && (
            <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`}>
              <path d={path} fill="none" stroke={up ? "#059669" : "#dc2626"} strokeWidth={5} />
            </svg>
          )}
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", marginTop: "auto", fontSize: 26, color: "#4d555c" }}>
          <div style={{ display: "flex", gap: 36 }}>
            {s?.market?.mcap_cr ? <span>{`Mkt cap ${crore(s.market.mcap_cr, true).replace("₹", "Rs ")}`}</span> : null}
            {s?.market?.pe ? <span>{`P/E ${num(s.market.pe, 1)}`}</span> : null}
            {s?.latest?.roe ? <span>{`ROE ${pct(s.latest.roe.value)}`}</span> : null}
            {stats ? <span>{`52w ${rs(stats.low52)} – ${rs(stats.high52)}`}</span> : null}
          </div>
          <div style={{ fontSize: 34, fontWeight: 700, color: "#0d0d0e" }}>Pivot.</div>
        </div>
      </div>
    ),
    size,
  );
}
