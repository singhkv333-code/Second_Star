"use client";

import { useRef, useState } from "react";
import dynamic from "next/dynamic";
import Image from "next/image";
import { HOME_ASSETS } from "@/lib/publicAssets";
import snapshots from "@/components/home-market-snapshots.json";
import { ArrowRight, ArrowUp, Plus, PieChart, ReceiptText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { PromptInput, PromptInputActions, PromptInputTextarea } from "@/components/prompt-kit/prompt-input";

const MeshGradient = dynamic(
  () => import("@paper-design/shaders-react").then((module) => module.MeshGradient),
  { ssr: false },
);
const QUESTIONS = [
  "Explain Bitcoin’s latest price action.",
  "Compare Ethereum and Bitcoin over the past month.",
  "Show key support and resistance for RELIANCE.",
];

export function HomeWelcome({ onOpenChart, onSendPrompt }: {
  onOpenChart: () => void;
  onSendPrompt: (prompt: string) => void;
}): React.ReactElement {
  const [prompt, setPrompt] = useState("");
  const composerRef = useRef<HTMLDivElement>(null);
  const submit = (): void => {
    const text = prompt.trim();
    if (!text) return;
    onSendPrompt(text);
    setPrompt("");
  };
  const chooseQuestion = (text: string): void => {
    setPrompt(text);
    composerRef.current?.querySelector("textarea")?.focus();
  };

  return (
    <section className="home-welcome" aria-label="Explore with Pivot">
      <div className="home-welcome-banner">
        <div className="home-mesh" aria-hidden="true">
          <MeshGradient colors={["#101820", "#78929d", "#243b51", "#b2c3c2", "#171f2c"]} distortion={0.75} swirl={0.25} grainMixer={0.12} grainOverlay={0.22} speed={0} frame={900000} scale={1.2} width="100%" height="100%" minPixelRatio={1} maxPixelCount={1600000} />
        </div>
        <div className="home-chart-intro">
          <h2>Explore our AI<br />chart interface.</h2>
          <Button type="button" onClick={onOpenChart} className="home-chart-cta">
            Explore charts <ArrowRight aria-hidden="true" />
          </Button>

        </div>
        <div className="home-chart-scene">
          <div className="home-asset-row" aria-label="Recorded market examples">
            <AssetCard name="Bitcoin" currency="USD" position="bitcoin" logo={HOME_ASSETS.bitcoin} snapshot={snapshots.bitcoin} />
            <AssetCard name="Ethereum" currency="USD" position="ethereum" logo={HOME_ASSETS.ethereum} snapshot={snapshots.ethereum} />
            <AssetCard name="Reliance" currency="INR" position="candles" candle logo={HOME_ASSETS.reliance} snapshot={snapshots.reliance} />
            <PortfolioCard />
          </div>
          <div className="home-composer" ref={composerRef}>
            <PromptInput value={prompt} onValueChange={setPrompt} onSubmit={submit} maxHeight={144} className="home-glass-prompt">
              <PromptInputTextarea aria-label="Ask Pivot" placeholder="Ask about any chart…" className="home-prompt-textarea" />
              <PromptInputActions className="home-prompt-actions">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button type="button" variant="ghost" size="icon" className="home-prompt-plus" aria-label="Add a chart question"><Plus size={19} strokeWidth={1.5} aria-hidden="true" /></Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" onCloseAutoFocus={(event) => { event.preventDefault(); composerRef.current?.querySelector("textarea")?.focus(); }}>
                    {QUESTIONS.map((example) => <DropdownMenuItem key={example} onSelect={() => chooseQuestion(example)}>{example}</DropdownMenuItem>)}
                  </DropdownMenuContent>
                </DropdownMenu>
                <div className="home-prompt-submit-group">
                  <span className="home-prompt-key-hint" aria-hidden="true">Enter ↵</span>
                  <Button type="button" size="icon" onClick={submit} disabled={!prompt.trim()} aria-label="Send question" className="home-prompt-send"><ArrowUp aria-hidden="true" /></Button>
                </div>
              </PromptInputActions>
            </PromptInput>
          </div>
        </div>
      </div>
    </section>
  );
}

// Recorded examples retain source/time details in accessible descriptions and hover text.
type MarketSnapshot = typeof snapshots.bitcoin | typeof snapshots.ethereum | typeof snapshots.reliance;
type Range = "1D" | "1W" | "1M" | "ALL";
const RANGES: Range[] = ["1D", "1W", "1M", "ALL"];
const priceFormat = (value: number, currency: "USD" | "INR"): string => new Intl.NumberFormat(currency === "INR" ? "en-IN" : "en-US", { style: "currency", currency, maximumFractionDigits: 2, minimumFractionDigits: 2 }).format(value);
function AssetCard({ name, currency, position, logo, snapshot, candle = false }: { name: string; currency: "USD" | "INR"; position: string; logo: string; snapshot: MarketSnapshot; candle?: boolean }): React.ReactElement {
  const [failed, setFailed] = useState(false);
  const [range, setRange] = useState<Range>(snapshot.defaultRange as Range);
  const current = snapshot.ranges[range];
  const change = `${current.changePct >= 0 ? "+" : ""}${current.changePct.toFixed(2)}%`;
  return (
    <div className={`home-asset-card home-scene-${position}`} title={`Recorded example · ${snapshot.source} · ${snapshot.asOf}`}>
      <div className="home-asset-header">
        {failed ? <span className="home-asset-monogram">{name[0]}</span> : <Image className="home-asset-logo" src={logo} unoptimized alt="" width={20} height={20} onError={() => setFailed(true)} />}
        <span className="home-asset-name">{name}</span>
      </div>
      <div className="home-asset-chart">
        <div className="home-price-row"><span className="home-asset-price">{priceFormat(current.price, currency)}</span><span className={`home-change-badge ${current.changePct < 0 ? "is-negative" : "is-positive"}`}>{change}</span></div>
        <div className="home-chart-ranges" role="group" aria-label={`${name} chart range`}>
          {RANGES.map(item => <button key={item} type="button" aria-pressed={item === range} onClick={() => setRange(item)}>{item}</button>)}
        </div>
        <svg className={`home-asset-spark ${candle ? "home-candles" : ""}`} viewBox="0 0 248 60" preserveAspectRatio="none" role="img" aria-label={`${name}, recorded ${range} ${candle ? "candlestick" : "line"} chart, ${change}, as of ${snapshot.dateLabel}`}>
          {candle ? current.candles.slice(-22).map((bar, i, bars) => <g key={i} stroke={bar.up ? "#447f73" : "#b06b65"} fill={bar.up ? "#447f73" : "#b06b65"}><line x1={8 + i * 232 / bars.length} x2={8 + i * 232 / bars.length} y1={bar.h} y2={bar.l} strokeWidth="1" /><rect x={5 + i * 232 / bars.length} y={Math.min(bar.o, bar.c)} width="5" height={Math.max(1.5, Math.abs(bar.o - bar.c))} rx=".7" stroke="none" /></g>) : <path d={current.path} stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" fill="none" />}
        </svg>
      </div>
    </div>
  );
}
function PortfolioCard(): React.ReactElement {
  const [selected, setSelected] = useState(0);
  const [hovered, setHovered] = useState<number | null>(null);
  const active = hovered ?? selected;
  const holding = snapshots.portfolio.holdings[active] ?? snapshots.portfolio.holdings[0]!;
  const colors = ["#cb9b52", "#719abb", "#64998b"];
  let offset = 0;
  return <div className="home-asset-card home-scene-portfolio" title={snapshots.portfolio.source}>
    <div className="home-portfolio-tabs"><span><PieChart size={12} />Holdings</span><span><ReceiptText size={12} />Transactions</span></div>
    <div className="home-portfolio-total">{priceFormat(snapshots.portfolio.total, "INR")}</div>
    <div className="home-allocation">
      <svg viewBox="0 0 120 120" role="group" aria-label="Illustrative paper allocation, ten shares each of Reliance, TCS and Infosys">
        {snapshots.portfolio.holdings.map((item, i) => {const dash = item.weight * 2.7646; const start = offset; offset += dash; return <circle key={item.symbol} className="home-allocation-segment" role="button" tabIndex={0} aria-label={`Select ${item.symbol} allocation`} aria-pressed={selected === i} onClick={() => setSelected(i)} onMouseEnter={() => setHovered(i)} onMouseLeave={() => setHovered(null)} onFocus={() => setHovered(i)} onBlur={() => setHovered(null)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelected(i); } }} cx="60" cy="60" r="44" fill="none" stroke={colors[i]} strokeWidth={active === i ? 9 : 7} opacity={active === i ? 1 : .55} strokeDasharray={`${Math.max(0, dash - 3)} ${276.46 - dash + 3}`} strokeDashoffset={-start} transform="rotate(-90 60 60)" />;})}
      </svg>
      <div className="home-allocation-label"><strong>{holding.weight.toFixed(1)}%</strong><span>{holding.symbol}</span></div>
    </div>
    <div className="home-portfolio-legend">{snapshots.portfolio.holdings.map((item, i) => <button type="button" key={item.symbol} aria-pressed={selected === i} onClick={() => setSelected(i)} onMouseEnter={() => setHovered(i)} onMouseLeave={() => setHovered(null)} onFocus={() => setHovered(i)} onBlur={() => setHovered(null)}><i style={{background:colors[i]}} />{item.symbol}</button>)}</div>
  </div>;
}
