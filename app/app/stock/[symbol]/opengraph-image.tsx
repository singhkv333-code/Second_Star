import { ImageResponse } from "next/og";
import { getDailyBars, getStock, type Bar } from "@/lib/seo/data";
import { priceStats } from "@/lib/seo/stockCopy";
import { WORDMARK_PATH } from "@/components/brand/PivotWordLogo";

/** The card a stock page shows when it is shared on WhatsApp, X, LinkedIn or
 *  Telegram. Built like a cashtag card: one price, one move, one line. The
 *  move is the chart's own (a year), so a falling line never sits beside a
 *  green number; today's move is the quiet second fact. */

export const revalidate = 900;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = "Share price and one-year chart on Pivot";

const INK = "#0b0c0d";
const BONE = "#ece7dc";
const QUIET = "#8b9096";
const RULE = "#2a2d30";
const UP = "#3fcf8e";
const DOWN = "#f0616d";

// The bundled font has no rupee sign; "Rs" reads correctly everywhere.
const rs = (v: number, dp = 2): string =>
  `Rs ${v.toLocaleString("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp })}`;
const signed = (v: number, dp = 2): string => `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(dp)}%`;

// Chart box, in card pixels.
const CX = 72;
const CW = 1056;
const CY = 330;
const CH = 210;

/** About 120 evenly spaced closes: enough for the shape, few enough that the
 *  curve reads as a line rather than as noise. The last close always stays. */
function thin(bars: Bar[], n = 120): number[] {
  const c = bars.map((b) => b.c);
  if (c.length <= n) return c;
  const out: number[] = [];
  for (let i = 0; i < n - 1; i++) out.push(c[Math.round((i * (c.length - 1)) / (n - 1))]!);
  out.push(c[c.length - 1]!);
  return out;
}

/** Catmull-Rom through the points, written as cubic Béziers. */
function smooth(pts: [number, number][]): string {
  if (pts.length < 2) return "";
  let d = `M${pts[0]![0].toFixed(1)},${pts[0]![1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i]!;
    const p1 = pts[i]!;
    const p2 = pts[i + 1]!;
    const p3 = pts[i + 2] ?? p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += `C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`;
  }
  return d;
}

export default async function Image({ params }: { params: Promise<{ symbol: string }> }) {
  let symbol = (await params).symbol;
  try { symbol = decodeURIComponent(symbol); } catch { /* raw */ }
  symbol = symbol.toUpperCase();
  const [s, bars] = await Promise.all([getStock(symbol).catch(() => null), getDailyBars(symbol)]);
  const stats = priceStats(bars);
  const name = s?.name ?? symbol;
  const last = stats?.last ?? s?.market?.price ?? null;
  const today = s?.market?.change ?? null;

  const closes = thin(bars);
  const lo = Math.min(...closes);
  const hi = Math.max(...closes);
  const pad = (hi - lo) * 0.08 || 1;
  const y = (c: number) => CY + CH - ((c - (lo - pad)) / (hi - lo + 2 * pad)) * CH;
  const pts = closes.map((c, i) => [CX + (i / Math.max(1, closes.length - 1)) * CW, y(c)] as [number, number]);
  const line = smooth(pts);
  const end = pts[pts.length - 1];
  const first = closes[0];
  const move = first && last ? ((last - first) / first) * 100 : null;
  const up = move == null ? true : move >= 0;
  const tone = up ? UP : DOWN;
  const area = line && end ? `${line}L${end[0].toFixed(1)},${CY + CH + 40}L${CX},${CY + CH + 40}Z` : "";
  const base = first ? y(first) : null;

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%", height: "100%", display: "flex", flexDirection: "column",
          backgroundColor: INK,
          backgroundImage: `radial-gradient(circle at 88% 70%, ${up ? "rgba(63,207,142,0.10)" : "rgba(240,97,109,0.10)"}, rgba(11,12,13,0) 46%)`,
          color: BONE, padding: "56px 72px 0",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center" }}>
            <div
              style={{
                width: 56, height: 56, borderRadius: 28, backgroundColor: "#1b1d20", border: `1px solid ${RULE}`,
                display: "flex", alignItems: "center", justifyContent: "center", fontSize: 26, fontWeight: 700, color: BONE,
              }}
            >
              {name.slice(0, 1).toUpperCase()}
            </div>
            <div style={{ display: "flex", flexDirection: "column", marginLeft: 18 }}>
              <div style={{ fontSize: 30, fontWeight: 600, letterSpacing: -0.5, maxWidth: 720, overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis" }}>{name}</div>
              <div style={{ fontSize: 20, color: QUIET, marginTop: 2 }}>{`NSE · ${symbol}`}</div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", color: BONE }}>
            {/* The wordmark (components/brand/PivotWordLogo), 847 x 319. */}
            <svg width={101} height={38} viewBox="0 0 847 319">
              <path d={WORDMARK_PATH} fill={BONE} fillRule="evenodd" />
            </svg>
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "flex-end", marginTop: 34 }}>
          <div style={{ fontSize: 88, fontWeight: 700, letterSpacing: -3, lineHeight: 1 }}>{last != null ? rs(last) : symbol}</div>
        </div>
        <div style={{ display: "flex", alignItems: "center", marginTop: 18 }}>
          {move != null && (
            <div
              style={{
                display: "flex", alignItems: "center", padding: "6px 16px", borderRadius: 999,
                backgroundColor: up ? "rgba(63,207,142,0.14)" : "rgba(240,97,109,0.14)", color: tone, fontSize: 26, fontWeight: 600,
              }}
            >
              <svg width={16} height={14} viewBox="0 0 16 14" style={{ marginRight: 10, marginTop: 2 }}>
                <path d={up ? "M8 0L16 14H0Z" : "M0 0H16L8 14Z"} fill={tone} />
              </svg>
              {signed(move, 1)}
              <span style={{ color: QUIET, fontWeight: 400, marginLeft: 10 }}>1Y</span>
            </div>
          )}
          {today != null && (
            <div style={{ fontSize: 24, color: QUIET, marginLeft: 22 }}>{`Today ${signed(today)}`}</div>
          )}
        </div>

        <svg width={1200} height={300} viewBox={`0 ${CY - 30} 1200 300`} style={{ position: "absolute", left: 0, top: CY - 30 }}>
          <defs>
            <linearGradient id="fade" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={tone} stopOpacity={0.26} />
              <stop offset="100%" stopColor={tone} stopOpacity={0} />
            </linearGradient>
          </defs>
          {base != null && (
            <line x1={CX} x2={CX + CW} y1={base} y2={base} stroke={QUIET} strokeOpacity={0.45} strokeWidth={2} strokeDasharray="4 10" />
          )}
          {area && <path d={area} fill="url(#fade)" />}
          {line && <path d={line} fill="none" stroke={tone} strokeWidth={5} strokeLinecap="round" strokeLinejoin="round" />}
          {end && <circle cx={end[0]} cy={end[1]} r={26} fill={tone} fillOpacity={0.12} />}
          {end && <circle cx={end[0]} cy={end[1]} r={15} fill={tone} fillOpacity={0.24} />}
          {end && <circle cx={end[0]} cy={end[1]} r={8} fill={tone} stroke={INK} strokeWidth={3} />}
        </svg>

        <div
          style={{
            position: "absolute", left: 72, right: 72, bottom: 34, display: "flex", justifyContent: "space-between",
            fontSize: 22, color: QUIET,
          }}
        >
          <span>{stats ? `52w ${rs(stats.low52, 0)} – ${rs(stats.high52, 0)}` : ""}</span>
          <span style={{ color: BONE }}>Open the live chart on pivotnow.in</span>
        </div>
      </div>
    ),
    size,
  );
}
