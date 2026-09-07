"use client";

/**
 * /design — Pivot design-system showcase.
 *
 * Every DS component rendered with realistic Pivot content, in light
 * AND dark (ink sections force the `.dark` token set), so the system
 * can be reviewed — and screenshot-verified — on one page.
 *
 * The document scroll is locked globally (app shell owns scrolling),
 * so this page provides its own scroll container.
 */

import {
  AgentCard,
  BacktestWidget,
  BlueprintTile,
  CandlePulse,
  ChainFlow,
  ChatBubble,
  ChatInputBar,
  CTABand,
  Delta,
  Display,
  DotDrift,
  Eyebrow,
  Figure,
  Hairline,
  MacWindow,
  MetricStat,
  MiniTable,
  MonoTag,
  Panel,
  PayoffWidget,
  PillButton,
  PromptChip,
  Prose,
  ScanTile,
  SectionShell,
  SparkLine,
  StatusPill,
  StockSnapshotWidget,
  ThinkingTicker,
  TickerTape,
  Title,
  WorkflowStep,
} from "@/components/ds";

/* Deterministic demo series (no Math.random — stable screenshots). */
const NIFTY = [0.42, 0.45, 0.44, 0.5, 0.48, 0.55, 0.53, 0.6, 0.66, 0.62, 0.7, 0.74];
const DRAWDOWN = [0.8, 0.78, 0.72, 0.74, 0.66, 0.6, 0.63, 0.55, 0.5, 0.52, 0.47, 0.44];
const EQUITY_UP = [100, 101.2, 100.6, 102.8, 104.1, 103.2, 105.9, 107.4, 106.8, 109.3];
const EQUITY_DN = [100, 99.1, 99.6, 97.8, 98.2, 96.9, 97.3, 95.8, 96.4, 95.1];

const RELIANCE_SERIES = [
  2731, 2748, 2722, 2769, 2784, 2761, 2810, 2837, 2818, 2862, 2841, 2889,
  2904, 2876, 2921, 2898, 2937, 2912, 2949, 2968,
];
/* Contains a real −14.3% drawdown (133 → 114) so the widget's computed
   drawdown strip and the quoted Max DD metric agree. */
const BT_EQUITY = [
  100, 102, 105, 109, 113, 118, 124, 129, 133, 127, 120, 114, 118, 124, 130,
  127, 135, 141, 138, 146, 151, 148, 156, 162,
];
const BT_BENCH = [
  100, 101, 103, 102, 106, 104, 108, 110, 107, 112, 109, 114, 117, 113, 119,
  116, 122, 125, 121, 127, 124, 130, 134, 131,
];

const TICKER_ITEMS = [
  { symbol: "NIFTY 50", price: "24,612.40", changePct: -0.82 },
  { symbol: "BANKNIFTY", price: "52,184.65", changePct: 0.34 },
  { symbol: "RELIANCE", price: "₹2,968.45", changePct: 1.18 },
  { symbol: "HDFCBANK", price: "₹1,617.30", changePct: -0.61 },
  { symbol: "TCS", price: "₹4,182.90", changePct: 0.92 },
  { symbol: "INFY", price: "₹1,734.55", changePct: 1.47 },
  { symbol: "GOLDBEES", price: "₹86.42", changePct: 0.28 },
  { symbol: "ITC", price: "₹472.15", changePct: -0.19 },
];

function SectionHeader({
  index,
  title,
  note,
}: {
  index: string;
  title: string;
  note?: string;
}) {
  return (
    <div className="mb-8 flex flex-col gap-2.5">
      <Eyebrow>
        {index} · {title}
      </Eyebrow>
      {note && <Prose size={14}>{note}</Prose>}
      <Hairline style={{ marginTop: 6 }} />
    </div>
  );
}

export default function DesignShowcase() {
  return (
    <div
      className="h-screen overflow-y-auto"
      style={{ background: "var(--bg-base)" }}
    >
      {/* ── Masthead ───────────────────────────────────────────────── */}
      <header className="mx-auto max-w-5xl px-8 pb-16 pt-20">
        <Eyebrow style={{ marginBottom: 18 }}>
          Pivot · Design System · v1
        </Eyebrow>
        <Display size="hero" as="h1">
          One identity.
          <br />
          <Display.Em>Every surface</Display.Em> it touches.
        </Display>
        <Prose size={15} style={{ maxWidth: 560, marginTop: 22 }}>
          Newsreader for the voice, Inter for the interface and for every
          figure. Ink on paper, paper on ink — color belongs to
          P&amp;L alone. Extracted from pivotnow.in and the app&apos;s Quartr
          token set; every component below reads theme variables, so it
          renders both modes unchanged.
        </Prose>
        <div className="mt-8 flex items-center gap-4">
          <PillButton withArrow>Join the Waitlist</PillButton>
          <PillButton variant="outline">View tokens</PillButton>
          <PillButton variant="ghost" withArrow>
            See how it works
          </PillButton>
        </div>
      </header>

      {/* ── 01 Type ───────────────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-8 pb-20">
        <SectionHeader
          index="01"
          title="Typography"
          note="Serif display speaks; Inter works, and counts."
        />
        <div className="flex flex-col gap-10">
          <Display size="section">
            Markets move. <Display.Em>You just talk.</Display.Em>
          </Display>
          <div className="grid grid-cols-1 gap-10 sm:grid-cols-2">
            <div className="flex flex-col gap-3">
              <Title size={16}>Card title — Inter 600, −0.025em</Title>
              <Prose>
                Body copy sits in the mid-grey, 1.7 leading. It never
                competes with figures: ₹24,612.40 stays ink-dark and
                tabular while prose around it stays quiet.
              </Prose>
            </div>
            <div className="flex flex-col items-start gap-3">
              <Eyebrow>Eyebrow / section label</Eyebrow>
              <div className="flex items-baseline gap-3">
                <Figure size={28}>₹24,612.40</Figure>
                <Delta value={1.24} />
                <Delta value={-2.08} />
              </div>
              <Prose size={13}>
                Numerals are Inter with tabular figures — one face, and
                columns that still line up on the decimal.
              </Prose>
            </div>
          </div>
        </div>
      </section>

      {/* ── 02 Tags, status, actions ──────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-8 pb-20">
        <SectionHeader
          index="02"
          title="Tags · Status · Actions"
          note="The mono microlabel is the signature; the pill is the only button shape."
        />
        <div className="flex flex-col gap-7">
          <div className="flex flex-wrap items-center gap-2.5">
            <MonoTag tone="ink" dot>
              Alert
            </MonoTag>
            <MonoTag tone="ink" dot>
              Backtest
            </MonoTag>
            <MonoTag tone="fill">RSI &lt; 30</MonoTag>
            <MonoTag tone="fill">FRI 09:30</MonoTag>
            <MonoTag>Research</MonoTag>
            <MonoTag>NFO · Weekly</MonoTag>
          </div>
          <div className="flex flex-wrap items-center gap-2.5">
            <StatusPill state="armed" />
            <StatusPill state="running" />
            <StatusPill state="paused" />
            <StatusPill state="draft" />
            <StatusPill state="error" />
          </div>
          <div className="flex flex-wrap items-center gap-3.5">
            <PillButton withArrow>Register order</PillButton>
            <PillButton variant="outline">Edit draft</PillButton>
            <PillButton variant="outline" size="sm">
              Pause
            </PillButton>
            <PillButton size="sm">Arm agent</PillButton>
            <PillButton variant="ghost" withArrow>
              Run backtest first
            </PillButton>
          </div>
        </div>
      </section>

      {/* ── 03 Chat ───────────────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-8 pb-20">
        <SectionHeader
          index="03"
          title="Chat"
          note="User speaks in a soft bubble; Pivot answers in plain ink. Suggestions wear their intent tag."
        />
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-5">
          <Panel pad={24} className="lg:col-span-3">
            <div className="flex flex-col gap-4">
              <ChatBubble role="user">
                Buy 50 shares of IREDA at market open tomorrow.
              </ChatBubble>
              <ChatBubble role="assistant">
                Order registered for 9:15 AM IST — 50 × IREDA at market.
                You&apos;ll confirm the fill in your broker app; I&apos;ll
                track it the moment it lands.
              </ChatBubble>
              <ChatBubble role="user">
                Alert me if NIFTY drops 2% in a single day.
              </ChatBubble>
              <ThinkingTicker phrase="Arming the alert…" />
              <div className="mt-2">
                <ChatInputBar />
              </div>
            </div>
          </Panel>
          <div className="lg:col-span-2">
            <SectionShell
              tone="ink"
              grid
              glow
              className="h-full"
              style={{ borderRadius: "var(--radius-xl)" }}
            >
              <div className="flex h-full flex-col items-start justify-center gap-7 p-8">
                <PromptChip tag="Alert" onClick={() => {}}>
                  Alert me if BANKNIFTY drops 2% in a single day.
                </PromptChip>
                <PromptChip tag="Backtest" onClick={() => {}} className="self-end">
                  Backtest a 50/200 SMA crossover on RELIANCE.
                </PromptChip>
                <PromptChip tag="Rule" onClick={() => {}}>
                  Build a capital-protective security with equity exposure.
                </PromptChip>
              </div>
            </SectionShell>
          </div>
        </div>
      </section>

      {/* ── 04 Analytics ──────────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-8 pb-20">
        <SectionHeader
          index="04"
          title="Analytics"
          note="Metric blocks, monochrome sparklines, quiet tables. Color enters only through P&L."
        />
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
          <Panel pad={22}>
            <MetricStat
              label="Portfolio value"
              value="₹4,82,310"
              delta={1.92}
              spark={NIFTY}
            />
          </Panel>
          <Panel pad={22}>
            <MetricStat
              label="Day P&L"
              value="−₹3,128"
              delta={-0.64}
              spark={DRAWDOWN}
            />
          </Panel>
          <Panel variant="ink" pad={22}>
            <div className="dark">
              <MetricStat
                label="Paper book NAV"
                value="₹1,09,340"
                delta={9.34}
                spark={EQUITY_UP}
              />
            </div>
          </Panel>
        </div>

        <Panel pad={24} className="mt-6">
          <div className="mb-4 flex items-center justify-between">
            <Title size={15}>Holdings</Title>
            <MonoTag tone="fill">Live · Kite</MonoTag>
          </div>
          <MiniTable
            head={["Symbol", "Qty", "Avg", "LTP", "P&L"]}
            rows={[
              [
                "RELIANCE",
                "24",
                "₹2,871.20",
                "₹2,968.45",
                <Delta key="r" value={3.39} arrow={false} />,
              ],
              [
                "HDFCBANK",
                "40",
                "₹1,642.00",
                "₹1,617.30",
                <Delta key="h" value={-1.5} arrow={false} />,
              ],
              [
                "NIFTYBEES",
                "310",
                "₹262.14",
                "₹274.91",
                <Delta key="n" value={4.87} arrow={false} />,
              ],
              [
                "GOLDBEES",
                "520",
                "₹81.05",
                "₹86.42",
                <Delta key="g" value={6.63} arrow={false} />,
              ],
            ]}
          />
        </Panel>
      </section>

      {/* ── 05 Agents ─────────────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-8 pb-20">
        <SectionHeader
          index="05"
          title="Agents & workflows"
          note="Drafts read as proposals — outlined, quiet. Armed agents carry the only pulse on the page."
        />
        <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-3">
          <AgentCard
            name="IREDA dip buyer"
            tag="−5% dip · qty 50"
            state="armed"
            nextRun="market open"
            lastRun="—"
            equity={EQUITY_UP}
          />
          <AgentCard
            name="Friday NIFTYBEES SIP"
            tag="FRI 09:30 · ₹10,000"
            state="running"
            nextRun="Fri 09:30"
            lastRun="7 Jun"
            equity={EQUITY_DN}
          />
          <Panel pad={20}>
            <div className="mb-5 flex items-start justify-between gap-3">
              <Title size={15}>Drawdown shield</Title>
              <StatusPill state="draft" />
            </div>
            <WorkflowStep index={0} kind="trigger">
              Portfolio drawdown crosses <Figure size={13}>−8%</Figure> from
              peak
            </WorkflowStep>
            <WorkflowStep index={1} kind="condition">
              Market is open · position exists
            </WorkflowStep>
            <WorkflowStep index={2} kind="action">
              Square off all CNC holdings → park in LIQUIDBEES
            </WorkflowStep>
            <WorkflowStep index={3} kind="notify" last>
              Push: &ldquo;Shield fired — book moved to cash.&rdquo;
            </WorkflowStep>
            <div className="mt-5 flex gap-2.5">
              <PillButton size="sm">Arm agent</PillButton>
              <PillButton size="sm" variant="outline">
                Backtest
              </PillButton>
            </div>
          </Panel>
        </div>
      </section>

      {/* ── 06 Motion & patterns ──────────────────────────────────── */}
      <section className="pb-20">
        <div className="mx-auto max-w-5xl px-8">
          <SectionHeader
            index="06"
            title="Motion & patterns"
            note="CSS-only atmosphere: the tape, the chain, the candles, the marks. Color still belongs to P&L."
          />
        </div>

        {/* Ticker tape — full width */}
        <TickerTape items={TICKER_ITEMS} />

        <div className="mx-auto max-w-5xl px-8">
          {/* Chain flow */}
          <Panel pad={28} className="mt-10">
            <div className="mb-6 flex items-center justify-between">
              <Title size={15}>An agent, armed</Title>
              <StatusPill state="armed" />
            </div>
            <ChainFlow
              nodes={[
                { label: "Trigger", detail: "RSI(14) < 30" },
                { label: "Condition", detail: "Market open" },
                { label: "Action", detail: "Buy 10 × INFY" },
                { label: "Notify", detail: "Push alert" },
              ]}
            />
          </Panel>

          {/* Pattern tiles */}
          <div className="mt-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
            <BlueprintTile style={{ border: "1px solid var(--glass-border)" }}>
              <div className="flex h-44 flex-col items-center justify-center gap-3">
                <Eyebrow>Blueprint</Eyebrow>
                <Prose size={13}>Grid + registration marks</Prose>
              </div>
            </BlueprintTile>
            <DotDrift style={{ border: "1px solid var(--glass-border)" }}>
              <div className="flex h-44 flex-col items-center justify-center gap-3">
                <Eyebrow>Drift</Eyebrow>
                <Prose size={13}>Dot grid, crawling</Prose>
              </div>
            </DotDrift>
            <ScanTile style={{ border: "1px solid var(--glass-border)" }}>
              <div className="flex h-44 flex-col items-center justify-center gap-3">
                <CandlePulse scale={0.8} />
                <Eyebrow>The tape, breathing</Eyebrow>
              </div>
            </ScanTile>
          </div>
        </div>
      </section>

      {/* ── 07 Mac mockup ─────────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-8 pb-20">
        <SectionHeader
          index="07"
          title="Product shot — Mac window"
          note="The Linear-style hero frame: drop any composition inside; tone follows the section."
        />
        <SectionShell
          tone="ink"
          grid
          glow
          style={{ borderRadius: "var(--radius-xl)" }}
        >
          <div className="px-6 pt-12 pb-0 sm:px-14">
            <MacWindow
              url="pivotnow.in/chat"
              style={{
                borderBottomLeftRadius: 0,
                borderBottomRightRadius: 0,
                borderBottom: "none",
              }}
            >
              <div className="grid grid-cols-1 gap-0 lg:grid-cols-5">
                <div
                  className="flex flex-col justify-between gap-5 p-6 lg:col-span-2"
                  style={{ borderRight: "1px solid var(--glass-border)" }}
                >
                  <div className="flex flex-col gap-4">
                    <ChatBubble role="user">
                      Analyse RELIANCE — is the trend intact?
                    </ChatBubble>
                    <ChatBubble role="assistant">
                      Price ₹2,968.45 sits above all three SMAs with RSI at
                      61 — uptrend intact, not yet overbought. Snapshot on
                      the right.
                    </ChatBubble>
                    <ThinkingTicker phrase="Watching the 50-day…" />
                  </div>
                  <ChatInputBar />
                </div>
                <div className="p-6 lg:col-span-3">
                  <StockSnapshotWidget
                    symbol="RELIANCE"
                    name="Reliance Industries"
                    price="₹2,968.45"
                    changePct={1.18}
                    series={RELIANCE_SERIES}
                    returns={[
                      { label: "1W", pct: 1.2 },
                      { label: "1M", pct: 3.4 },
                      { label: "3M", pct: -2.1 },
                      { label: "6M", pct: 8.9 },
                      { label: "1Y", pct: 14.2 },
                    ]}
                    week52={{ low: 2221, high: 3024, last: 2968 }}
                  />
                </div>
              </div>
            </MacWindow>
          </div>
        </SectionShell>
      </section>

      {/* ── 08 Chat widgets ───────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-8 pb-20">
        <SectionHeader
          index="08"
          title="Chat widgets"
          note="More information per square inch: shaded P/L zones, breakevens, drawdown strips, 52-week bands."
        />
        <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
          <PayoffWidget
            spec={{
              strategy: "Bull Call Spread",
              underlying: "NIFTY · 24500/25000 CE · 26 Jun · 1 lot (65)",
              vertices: [
                [23800, -11700],
                [24500, -11700],
                [25000, 20800],
                [25700, 20800],
              ],
              breakevens: [24680],
              strikes: [24500, 25000],
              maxProfit: "+₹20,800",
              maxLoss: "−₹11,700",
              pop: "58%",
            }}
          />
          <BacktestWidget
            title="SMA 50/200 crossover"
            period="RELIANCE · 2019–2026 · daily bars · costs incl."
            equity={BT_EQUITY}
            benchmark={BT_BENCH}
            verdict="Beats B&H"
            metrics={[
              { label: "CAGR", value: "11.8%" },
              { label: "Sharpe", value: "1.21" },
              { label: "Max DD", value: "", signedPct: -14.3 },
              { label: "Win rate", value: "54%" },
            ]}
          />
        </div>
      </section>

      {/* ── 09 Dark mirror ────────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-8 pb-20">
        <SectionHeader
          index="09"
          title="Dark mirror"
          note="The same components inside a .dark subtree — zero prop changes."
        />
        <SectionShell
          tone="ink"
          grid
          style={{ borderRadius: "var(--radius-xl)" }}
        >
          <div className="grid grid-cols-1 gap-6 p-8 lg:grid-cols-3">
            <Panel variant="glass" pad={22} className="flex flex-col justify-center">
              <MetricStat
                label="NIFTY 50"
                value="24,612.40"
                delta={-0.82}
                spark={DRAWDOWN}
              />
            </Panel>
            <Panel variant="glass" pad={22}>
              <div className="mb-3 flex items-center justify-between">
                <Title size={15}>Covered Call · NIFTY</Title>
                <MonoTag tone="ink">Income</MonoTag>
              </div>
              <Prose size={13}>
                Hold NIFTYBEES, sell monthly OTM calls. Premium cushions
                drawdowns; upside capped at strike.
              </Prose>
              <div className="mt-4 flex items-center justify-between">
                <SparkLine data={EQUITY_UP} width={120} height={26} />
                <Delta value={7.4} size={13} />
              </div>
            </Panel>
            <Panel variant="glass" pad={22}>
              <div className="flex h-full flex-col justify-between gap-4">
                <div className="flex flex-col gap-3">
                  <ChatBubble role="user">
                    Roll my 24600 CE to next expiry.
                  </ChatBubble>
                  <ThinkingTicker phrase="Pricing the roll…" />
                </div>
                <ChatInputBar placeholder="Ask Pivot anything…" />
              </div>
            </Panel>
          </div>
        </SectionShell>
      </section>

      {/* ── 10 Landing voice ──────────────────────────────────────── */}
      <section className="pb-24">
        <div className="mx-auto max-w-5xl px-8">
          <SectionHeader
            index="10"
            title="Landing voice"
            note="The closing move: serif conviction on ink, one pill."
          />
        </div>
        {/* Rounded inside the showcase; full-bleed on the real landing. */}
        <div
          className="mx-auto max-w-5xl overflow-hidden px-8"
          style={{ borderRadius: "var(--radius-xl)" }}
        >
          <CTABand
            eyebrow="Pivot · Early access"
            action={
              <PillButton size="lg" withArrow>
                Join the Waitlist
              </PillButton>
            }
            className="overflow-hidden rounded-3xl"
          >
            <Display size="section" as="p">
              Stop watching charts.
              <br />
              <Display.Em>Start describing outcomes.</Display.Em>
            </Display>
          </CTABand>
        </div>
      </section>
    </div>
  );
}
