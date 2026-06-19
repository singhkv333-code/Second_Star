"use client";
import { useMemo, useState } from "react";
import { ChevronDown, Sigma, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { ContentOverlay } from "@/components/chat/ContentOverlay";

type Side = "BUY" | "SELL";
type OptType = "CE" | "PE";
type Greeks = { iv: number; delta: number; theta: number; gamma: number; vega: number; rho: number };
type Quote = Greeks & { ltp: number; oi: number; oiChg: number; ltpChg: number };
type ChainRow = { strike: number; ce: Quote; pe: Quote };
type BasketLeg = { strike: number; type: OptType; side: Side; ltp: number };

const EXPIRIES = ["26 Jun", "3 Jul", "10 Jul", "31 Jul", "28 Aug"];
const DAY_CHG_ABS = -196.2;
const DAY_CHG_PCT = -0.81;

function buildChain(spot: number, step: number, span: number): ChainRow[] {
  const atm = Math.round(spot / step) * step;
  const rows: ChainRow[] = [];
  for (let i = -span; i <= span; i++) {
    const strike = atm + i * step;
    const d = strike - spot;
    const dist = Math.abs(d);
    const tv = 130 * Math.exp(-((dist / (step * 6)) ** 2)) + 7;
    const oiBase = 95000 * Math.exp(-((dist / (step * 5)) ** 2));
    const iv = 11.5 + 0.04 * (dist / step);
    const ceDelta = 1 / (1 + Math.exp(d / (step * 2.2)));
    const gamma = 0.0008 * Math.exp(-((dist / (step * 6)) ** 2)) + 0.00005;
    const vega = 17 * Math.exp(-((dist / (step * 7)) ** 2)) + 11;
    const theta = -(9 * Math.exp(-((dist / (step * 7)) ** 2)) + 6.5);
    const mk = (intr: number, delta: number, rhoSign: number): Quote => ({
      ltp: Math.round((intr + tv) * 100) / 100,
      oi: Math.round(oiBase * (0.7 + ((Math.abs(strike) % 7) / 10))),
      oiChg: Math.round((Math.sin(strike) * 8 + 6) * 100) / 100,
      ltpChg: Math.round((Math.cos(strike / 50) * 25 - 25) * 100) / 100,
      iv: Math.round(iv * 100) / 100,
      delta: Math.round(delta * 100) / 100,
      theta: Math.round(theta * 100) / 100,
      gamma: Math.round(gamma * 10000) / 10000,
      vega: Math.round(vega * 100) / 100,
      rho: Math.round(rhoSign * (5 - dist / (step * 6)) * 100) / 100,
    });
    rows.push({ strike, ce: mk(Math.max(spot - strike, 0), ceDelta, 1), pe: mk(Math.max(strike - spot, 0), -(1 - ceDelta), -1) });
  }
  return rows;
}

function fmtOi(n: number): string {
  if (n >= 1e7) return `${(n / 1e7).toFixed(2)}Cr`;
  if (n >= 1e5) return `${(n / 1e5).toFixed(2)}L`;
  return n.toLocaleString("en-IN");
}
const pct = (n: number): string => `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
const lossColor = (n: number): string => (n >= 0 ? "var(--color-profit)" : "var(--color-loss)");

export function OptionChainFullScreen({ open, onClose, underlying = "NIFTY", spot = 23971.8 }: { open: boolean; onClose: () => void; underlying?: string; spot?: number }): React.ReactElement | null {
  const [expiry, setExpiry] = useState(EXPIRIES[1]!);
  const [greeksOn, setGreeksOn] = useState(false);
  const [basket, setBasket] = useState<BasketLeg[]>([]);
  const rows = useMemo(() => buildChain(spot, 50, 9), [spot, expiry]);
  const atm = Math.round(spot / 50) * 50;
  const maxOi = useMemo(() => Math.max(...rows.flatMap((r) => [r.ce.oi, r.pe.oi]), 1), [rows]);
  if (!open) return null;
  const addLeg = (strike: number, type: OptType, side: Side, ltp: number): void => setBasket((p) => [...p, { strike, type, side, ltp }]);
  const dayDown = DAY_CHG_PCT < 0;
  // Greeks-on columns are fully fluid (minmax(0,…fr)) so all 17 columns fit the
  // width with NO horizontal scroll; greeks-off keeps a roomier core.
  const Gf = "minmax(0,0.72fr) minmax(0,0.72fr) minmax(0,0.82fr) minmax(0,0.72fr) minmax(0,0.62fr) minmax(0,0.58fr)";
  const coreTight = "minmax(0,1.3fr) minmax(0,1.28fr) minmax(0,0.8fr) minmax(0,1.28fr) minmax(0,1.3fr)";
  const coreWide = "minmax(120px,1.1fr) 120px 96px 120px minmax(120px,1.1fr)";
  const cols = greeksOn ? `${Gf} ${coreTight} ${Gf.split(" ").reverse().join(" ")}` : coreWide;
  return (
    <ContentOverlay open={open} onClose={onClose} label="Option chain">
      <div className="flex h-full w-full flex-col bg-background">
        <div className="flex shrink-0 items-center justify-between gap-4 border-b border-border/50 px-6 py-3.5 lg:px-9">
          <div className="flex items-center gap-3">
            <button type="button" className="inline-flex items-center gap-1.5 rounded-full border border-border/70 bg-card px-3 py-1.5 text-[13px] font-semibold tracking-tight text-foreground transition-colors hover:bg-muted/60">
              {underlying}
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            </button>
            <span className="relative inline-flex items-center">
              <select value={expiry} onChange={(e) => setExpiry(e.target.value)} aria-label="Expiry" className="appearance-none rounded-full border border-border/70 bg-card py-1.5 pl-3 pr-8 text-[13px] font-medium text-foreground transition-colors hover:bg-muted/60 focus:outline-none focus:ring-2 focus:ring-ring/30">
                {EXPIRIES.map((e) => (<option key={e} value={e}>{e}</option>))}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2.5 h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
            </span>
            <div className="ml-1 flex items-baseline gap-2">
              <span className="text-[18px] font-semibold leading-none tracking-tight tabular-nums text-foreground">{spot.toLocaleString("en-IN")}</span>
              <span className="text-[12px] font-medium tabular-nums" style={{ color: lossColor(DAY_CHG_PCT) }}>{dayDown ? "−" : "+"}{Math.abs(DAY_CHG_ABS).toFixed(2)} ({pct(DAY_CHG_PCT)})</span>
            </div>
          </div>
          <Button variant="ghost" size="icon" aria-label="Close option chain" onClick={onClose} className="rounded-full"><X className="h-4 w-4" aria-hidden="true" /></Button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden">
          <div>
            <div className="sticky top-0 z-10 grid items-center gap-x-2 border-b border-border/50 bg-background/95 px-6 py-2.5 text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground/70 backdrop-blur lg:px-9" style={{ gridTemplateColumns: cols }}>
              {greeksOn && <Th r>Rho</Th>}
              {greeksOn && <Th r>Vega</Th>}
              {greeksOn && <Th r>Gamma</Th>}
              {greeksOn && <Th r>Theta</Th>}
              {greeksOn && <Th r>Delta</Th>}
              {greeksOn && <Th r>IV</Th>}
              <Th>Call OI</Th>
              <Th r>Call LTP</Th>
              <Th c>Strike</Th>
              <Th>Put LTP</Th>
              <Th r>Put OI</Th>
              {greeksOn && <Th r>IV</Th>}
              {greeksOn && <Th r>Delta</Th>}
              {greeksOn && <Th r>Theta</Th>}
              {greeksOn && <Th r>Gamma</Th>}
              {greeksOn && <Th r>Vega</Th>}
              {greeksOn && <Th r>Rho</Th>}
            </div>
            {rows.map((r) => {
              const isAtm = r.strike === atm;
              const ceW = (r.ce.oi / maxOi) * 100;
              const peW = (r.pe.oi / maxOi) * 100;
              const ceItm = r.strike < spot;
              const peItm = r.strike > spot;
              return (
                <div key={r.strike}>
                  {isAtm && (
                    <div className="relative flex items-center justify-center py-2">
                      <div className="absolute inset-x-6 h-px bg-gradient-to-r from-transparent via-foreground/25 to-transparent lg:inset-x-9" />
                      <span className="relative z-10 inline-flex items-center gap-1.5 rounded-full border border-border/60 bg-card px-3 py-1 text-[11px] font-medium tabular-nums text-foreground shadow-sm">
                        <span className="h-1.5 w-1.5 rounded-full" style={{ background: lossColor(DAY_CHG_PCT) }} aria-hidden="true" />
                        {spot.toLocaleString("en-IN")}
                        <span className="text-muted-foreground/80">·</span>
                        <span style={{ color: lossColor(DAY_CHG_PCT) }}>{pct(DAY_CHG_PCT)}</span>
                      </span>
                    </div>
                  )}
                  <div className={cn("group relative grid items-center gap-x-2 px-6 py-2.5 text-[12px] tabular-nums transition-colors lg:px-9", isAtm ? "bg-amber-50/60 dark:bg-amber-400/[0.06]" : "hover:bg-muted/40")} style={{ gridTemplateColumns: cols }}>
                    {greeksOn && <Td r muted>{r.ce.rho.toFixed(2)}</Td>}
                    {greeksOn && <Td r muted>{r.ce.vega.toFixed(2)}</Td>}
                    {greeksOn && <Td r muted>{r.ce.gamma.toFixed(4)}</Td>}
                    {greeksOn && <Td r muted>{r.ce.theta.toFixed(2)}</Td>}
                    {greeksOn && <Td r muted>{r.ce.delta.toFixed(2)}</Td>}
                    {greeksOn && <Td r muted>{r.ce.iv.toFixed(2)}</Td>}
                    <OiCell side="call" oi={r.ce.oi} oiChg={r.ce.oiChg} width={ceW} dim={!ceItm} />
                    <PriceCell align="right" ltp={r.ce.ltp} chg={r.ce.ltpChg} onBuy={() => addLeg(r.strike, "CE", "BUY", r.ce.ltp)} onSell={() => addLeg(r.strike, "CE", "SELL", r.ce.ltp)} />
                    <div className="flex items-center justify-center">
                      <span className={cn("font-semibold tracking-tight", isAtm ? "text-amber-700 dark:text-amber-300" : "text-muted-foreground")}>{r.strike.toLocaleString("en-IN")}</span>
                    </div>
                    <PriceCell align="left" ltp={r.pe.ltp} chg={r.pe.ltpChg} onBuy={() => addLeg(r.strike, "PE", "BUY", r.pe.ltp)} onSell={() => addLeg(r.strike, "PE", "SELL", r.pe.ltp)} />
                    <OiCell side="put" oi={r.pe.oi} oiChg={r.pe.oiChg} width={peW} dim={!peItm} />
                    {greeksOn && <Td r muted>{r.pe.iv.toFixed(2)}</Td>}
                    {greeksOn && <Td r muted>{r.pe.delta.toFixed(2)}</Td>}
                    {greeksOn && <Td r muted>{r.pe.theta.toFixed(2)}</Td>}
                    {greeksOn && <Td r muted>{r.pe.gamma.toFixed(4)}</Td>}
                    {greeksOn && <Td r muted>{r.pe.vega.toFixed(2)}</Td>}
                    {greeksOn && <Td r muted>{r.pe.rho.toFixed(2)}</Td>}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
        {basket.length > 0 && (
          <div className="shrink-0 border-t border-border/50 bg-card/60 px-6 py-3 lg:px-9">
            <div className="mb-2 flex items-center justify-between">
              <span className="text-[10px] font-medium uppercase tracking-[0.08em] text-muted-foreground">Basket · {basket.length} {basket.length === 1 ? "leg" : "legs"}</span>
              <button type="button" onClick={() => setBasket([])} className="text-[11px] font-medium text-muted-foreground transition-colors hover:text-foreground">Clear all</button>
            </div>
            <div className="flex flex-wrap gap-2">
              {basket.map((b, i) => {
                const c = b.side === "BUY" ? "var(--color-profit)" : "var(--color-loss)";
                return (
                  <span key={i} className="inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium tabular-nums" style={{ borderColor: `color-mix(in srgb, ${c} 35%, transparent)`, background: `color-mix(in srgb, ${c} 8%, transparent)`, color: c }}>
                    <span className="font-semibold">{b.side === "BUY" ? "B" : "S"}</span>
                    {b.strike} {b.type}
                    <span className="opacity-70">₹{b.ltp.toFixed(2)}</span>
                    <button type="button" onClick={() => setBasket((p) => p.filter((_, idx) => idx !== i))} aria-label="Remove leg" className="ml-0.5 opacity-60 transition-opacity hover:opacity-100"><X className="h-3 w-3" aria-hidden="true" /></button>
                  </span>
                );
              })}
            </div>
          </div>
        )}
        <div className="flex shrink-0 items-center justify-center gap-3 border-t border-border/50 px-6 py-3">
          <Toggle on={greeksOn} onClick={() => setGreeksOn((v) => !v)} icon={<Sigma className="h-3.5 w-3.5" />} label="Greeks" />
        </div>
      </div>
    </ContentOverlay>
  );
}

function Th({ children, r, c }: { children: React.ReactNode; r?: boolean; c?: boolean }): React.ReactElement {
  return <span className={cn("min-w-0 truncate whitespace-nowrap", r && "text-right", c && "text-center", !r && !c && "text-left")}>{children}</span>;
}
function Td({ children, r, muted }: { children: React.ReactNode; r?: boolean; muted?: boolean }): React.ReactElement {
  return <span className={cn("min-w-0 truncate whitespace-nowrap", r ? "text-right" : "text-left", muted && "text-muted-foreground")}>{children}</span>;
}
function OiCell({ side, oi, oiChg, width, dim }: { side: "call" | "put"; oi: number; oiChg: number; width: number; dim?: boolean }): React.ReactElement {
  const isCall = side === "call";
  const barColor = isCall ? "var(--color-loss)" : "var(--color-profit)";
  return (
    <div className="relative flex h-full flex-col justify-center">
      <div className={cn("pointer-events-none absolute inset-y-1.5 flex overflow-hidden rounded-[5px]", isCall ? "right-0 justify-end" : "left-0 justify-start")} style={{ width: "100%" }} aria-hidden="true">
        <div className="h-full rounded-[5px]" style={{ width: `${Math.max(width, 2)}%`, background: `color-mix(in srgb, ${barColor} ${dim ? 9 : 16}%, transparent)` }} />
      </div>
      <div className={cn("relative flex flex-col leading-tight", isCall ? "items-start text-left" : "items-end text-right")}>
        <span className="font-medium text-foreground">{fmtOi(oi)}</span>
        <span className="text-[10px] font-medium" style={{ color: lossColor(oiChg) }}>{pct(oiChg)}</span>
      </div>
    </div>
  );
}
function PriceCell({ align, ltp, chg, onBuy, onSell }: { align: "left" | "right"; ltp: number; chg: number; onBuy: () => void; onSell: () => void }): React.ReactElement {
  // B/S sit in the blank space BESIDE the LTP (never over it). They occupy a
  // reserved slot (visibility toggled, not display) so the number never shifts,
  // and they reveal on ROW hover — so both legs' controls appear together.
  const bs = (
    <div className="invisible flex items-center gap-1 transition-opacity group-hover:visible">
      <button type="button" onClick={onBuy} aria-label="Buy" className="flex h-[18px] w-[18px] items-center justify-center rounded-[5px] text-[10px] font-bold text-white shadow-sm" style={{ background: "var(--color-profit)" }}>B</button>
      <button type="button" onClick={onSell} aria-label="Sell" className="flex h-[18px] w-[18px] items-center justify-center rounded-[5px] text-[10px] font-bold text-white shadow-sm" style={{ background: "var(--color-loss)" }}>S</button>
    </div>
  );
  const price = (
    <div className={cn("flex min-w-0 flex-col leading-tight", align === "right" ? "items-end text-right" : "items-start text-left")}>
      <span className="truncate text-[13px] font-semibold text-foreground">₹{ltp.toFixed(2)}</span>
      <span className="text-[10px] font-medium" style={{ color: lossColor(chg) }}>{pct(chg)}</span>
    </div>
  );
  return (
    <div className={cn("flex items-center gap-1.5", align === "right" ? "justify-end" : "justify-start")}>
      {align === "right" ? (<>{bs}{price}</>) : (<>{price}{bs}</>)}
    </div>
  );
}
function Toggle({ on, onClick, icon, label }: { on: boolean; onClick: () => void; icon: React.ReactNode; label: string }): React.ReactElement {
  return (
    <button type="button" onClick={onClick} aria-pressed={on} className={cn("inline-flex items-center gap-2 rounded-full border px-3.5 py-1.5 text-[12.5px] font-medium transition-colors", on ? "border-transparent bg-muted text-foreground" : "border-border/60 text-muted-foreground hover:text-foreground hover:bg-muted/50")}>
      {icon}
      {label}
      <span className={cn("ml-0.5 inline-flex h-4 w-7 items-center rounded-full p-0.5 transition-colors", on ? "bg-foreground" : "bg-muted-foreground/30")} aria-hidden="true">
        <span className={cn("h-3 w-3 rounded-full bg-background transition-transform", on && "translate-x-3")} />
      </span>
    </button>
  );
}
