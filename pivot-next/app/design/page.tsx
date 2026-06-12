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
  ChatBubble,
  ChatInputBar,
  CTABand,
  Delta,
  Display,
  Eyebrow,
  Figure,
  Hairline,
  MetricStat,
  MiniTable,
  MonoTag,
  Panel,
  PillButton,
  PromptChip,
  Prose,
  SectionShell,
  SparkLine,
  StatusPill,
  ThinkingTicker,
  Title,
  WorkflowStep,
} from "@/components/ds";

/* Deterministic demo series (no Math.random — stable screenshots). */
const NIFTY = [0.42, 0.45, 0.44, 0.5, 0.48, 0.55, 0.53, 0.6, 0.66, 0.62, 0.7, 0.74];
const DRAWDOWN = [0.8, 0.78, 0.72, 0.74, 0.66, 0.6, 0.63, 0.55, 0.5, 0.52, 0.47, 0.44];
const EQUITY_UP = [100, 101.2, 100.6, 102.8, 104.1, 103.2, 105.9, 107.4, 106.8, 109.3];
const EQUITY_DN = [100, 99.1, 99.6, 97.8, 98.2, 96.9, 97.3, 95.8, 96.4, 95.1];

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
          Newsreader for the voice, Inter for the interface, JetBrains Mono
          for the machine. Ink on paper, paper on ink — color belongs to
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
          note="Serif display speaks; Inter works; mono annotates."
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
                Tabular numerals everywhere a number lives.
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

      {/* ── 06 Dark mirror ────────────────────────────────────────── */}
      <section className="mx-auto max-w-5xl px-8 pb-20">
        <SectionHeader
          index="06"
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

      {/* ── 07 Landing voice ──────────────────────────────────────── */}
      <section className="pb-24">
        <div className="mx-auto max-w-5xl px-8">
          <SectionHeader
            index="07"
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
