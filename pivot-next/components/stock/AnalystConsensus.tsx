"use client";

import { useEffect, useState } from "react";
import { getAnalystConsensus, type AnalystConsensus as Consensus } from "@/lib/api";
import { isError } from "@/lib/types";

export function AnalystConsensus({ symbol, price }: { symbol: string; price: number | null }) {
  const [state, setState] = useState<{ symbol: string; data?: Consensus; error?: boolean }>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setState(undefined);
    void getAnalystConsensus(symbol).then(result => {
      if (!cancelled) setState(isError(result) ? { symbol, error: true } : { symbol, data: result.data });
    }).catch(() => { if (!cancelled) setState({ symbol, error: true }); });
    return () => { cancelled = true; };
  }, [symbol, attempt]);
  const current = state?.symbol === symbol ? state : undefined;
  const data = current?.data;
  const score = data?.score;
  const validScore = score != null && Number.isFinite(score) && score >= 1 && score <= 5;
  const label = !validScore ? "Rating unavailable" : score < 1.5 ? "Strong buy" : score < 2.5 ? "Buy" : score < 3.5 ? "Hold" : score < 4.5 ? "Sell" : "Strong sell";
  const angle = validScore ? Math.PI * (score - 1) / 4 : null;
  const target = data?.target;
  const change = target != null && price != null && price > 0 ? (target / price - 1) * 100 : null;
  const money = (n: number) => `₹${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
  return <aside className="analyst-consensus" aria-label="Analyst consensus">
    <div className="analyst-heading"><h3>Analyst rating</h3><span>{data?.analysts ? `${data.analysts} analysts` : "Consensus"}</span></div>
    {!current ? <p role="status">Loading analyst coverage…</p> : current.error ? <p role="status">Coverage could not be loaded. <button type="button" onClick={() => setAttempt(n => n + 1)}>Retry</button></p> : !data?.available ? <p>Analyst coverage unavailable.</p> : <>
      <svg viewBox="0 0 280 165" role="img" aria-label={`Analyst consensus: ${label}`}>
        {["#c49b6b", "#b9ad70", "#a3af83", "#71a697", "#398a7d"].map((color, i) => {
          const start = Math.PI - i * Math.PI / 5 - .025, end = Math.PI - (i + 1) * Math.PI / 5 + .025;
          return <path key={color} d={`M ${140 + 97 * Math.cos(start)} ${125 - 97 * Math.sin(start)} A 97 97 0 0 1 ${140 + 97 * Math.cos(end)} ${125 - 97 * Math.sin(end)}`} fill="none" stroke={color} strokeWidth="7" opacity={validScore ? 1 : .25} />;
        })}
        <g fill="var(--text-tertiary)" fontSize="10" textAnchor="middle"><text x="140" y="13">Hold</text><text x="27" y="145">Strong sell</text><text x="252" y="145">Strong buy</text><text x="52" y="45">Sell</text><text x="230" y="45">Buy</text></g>
        {angle !== null && <g stroke="var(--text-primary)" fill="var(--text-primary)"><line x1="140" y1="125" x2={140 + 77 * Math.cos(angle)} y2={125 - 77 * Math.sin(angle)} strokeWidth="2.5" strokeLinecap="round" /><circle cx="140" cy="125" r="4" /></g>}
      </svg>
      <strong className="analyst-verdict">{label}</strong>
      <div className="analyst-target"><span>1-year mean target</span><strong>{target == null ? "Unavailable" : money(target)}</strong></div>
      {change !== null && <div className="analyst-upside"><span>vs current price</span><span style={{ color: change >= 0 ? "var(--color-profit)" : "var(--color-loss)" }}>{change > 0 ? "+" : ""}{change.toFixed(1)}%</span></div>}
      <p className="analyst-source">{data.source} · retrieved {new Date(data.retrieved_at).toLocaleDateString("en-IN", { day: "numeric", month: "short" })}<br />Analyst estimates; revision dates unavailable.</p>
    </>}
    <style>{`
      .analyst-consensus { min-width:0; padding-left:24px; border-left:1px solid var(--glass-border); }
      .analyst-heading { display:flex; align-items:baseline; justify-content:space-between; gap:12px; }
      .analyst-heading h3 { font-size:13px; font-weight:600; margin:14px 0 8px; }
      .analyst-heading span,.analyst-consensus p { font-size:11px; color:var(--text-secondary); }
      .analyst-consensus svg { display:block; width:100%; max-width:280px; margin:0 auto; }
      .analyst-verdict { display:block; text-align:center; font-size:22px; font-weight:550; margin:-3px 0 18px; }
      .analyst-target,.analyst-upside { display:flex; align-items:baseline; justify-content:space-between; gap:12px; font-size:12px; font-variant-numeric:tabular-nums; }
      .analyst-target { border-top:1px solid var(--glass-border); padding-top:12px; }
      .analyst-target strong { font-size:17px; font-weight:550; }
      .analyst-upside { margin-top:5px; color:var(--text-secondary); }
      .analyst-consensus .analyst-source { margin:14px 0 0; font-size:10px; line-height:1.6; color:var(--text-tertiary); }
      @media(max-width:1100px){.analyst-consensus{grid-column:1/-1;border-left:0;border-top:1px solid var(--glass-border);padding:12px 0 0;max-width:360px;width:100%;justify-self:center;}}
    `}</style>
  </aside>;
}
