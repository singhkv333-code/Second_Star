"use client";

/**
 * StockAskBar — the floating ask bar on the stock detail page.
 *
 * Shape and placement are taken from the ChatGPT macOS companion bar: a slim
 * translucent pill that floats over the page rather than sitting in it, always
 * reachable, never in the way. The point of that pattern is that the question
 * arrives where the doubt does. On this page the doubt is always about ONE
 * company, so the bar is not a general chat box that happens to be here — it
 * opens already attached to the symbol you are reading.
 *
 * "Attached" is literal, not a placeholder string: every turn ships
 * `attachments: [{kind: "security", symbol}]`, which the server renders into
 * the prompt as tagged context (`_apply_attachments` in pivotted/server.py,
 * word for word Pivot's own envelope), so "is it expensive?" resolves to this
 * company and the tools are called with this symbol.
 *
 * What answers is Pivotted — charto's read tools with the ink removed, plus
 * fundamentals, ratios, filings and screens that reach EVERY listed company
 * rather than the ~500 whose bars this box stores. That split is the one trap
 * on this page: a company can be perfectly real, screenable on fundamentals,
 * and still have no price history here, and the model is told to say so
 * rather than reach for an index or a peer.
 *
 * It cannot commit anything. The alerts, journal, strategy and paper tools
 * charto's chat carries are dropped before the table reaches the model, so
 * nothing typed here can arm a rule or fill an order — the chart's own chat
 * is where that belongs, and this bar is deliberately not a second door to
 * it.
 *
 * Voice is the chat composer's own mic, imported rather than rebuilt, so a
 * spoken question takes the same path here as it does there.
 *
 * The glass: `backdrop-filter: blur(40px) saturate(200%)` over a translucent
 * fill, a hairline border, a top-lit inset and a sheen gradient — the standard
 * glassmorphism layer stack, which Apple's Liquid Glass extends with SVG
 * refraction that only Chromium can do behind `backdrop-filter: url(#…)`.
 * We stop at the part every browser renders: a refraction that appears in one
 * browser and vanishes in another is not a design, it is a coin toss. Where
 * `backdrop-filter` is unsupported entirely the fill goes opaque, so the bar
 * is never unreadable text over live content.
 */

import * as React from "react";

import AssistantMessage from "@/components/chat/AssistantMessage";
import { VoiceInputButton } from "@/components/VoiceInputButton";
import { getAccessToken } from "@/lib/authToken";
import { streamChat, type ChatHistoryMessage } from "@/lib/chatStream";

/** One exchange, as the panel shows it. */
type Turn = {
  question: string;
  answer: string;
  /** The tool the backend is running right now, for the waiting line. */
  tool: string | null;
  done: boolean;
  error: string | null;
};

/** Tool names are backend identifiers; the bar says what is happening in
 *  English. Anything unmapped falls back to a de-snaked version of the name
 *  rather than a generic "Working…", because naming the actual tool is the
 *  honest version of a progress line. */
const TOOL_WORD: Record<string, string> = {
  get_fundamentals: "Reading fundamentals",
  get_balance_sheet: "Reading the balance sheet",
  compare_fundamentals: "Comparing the financials",
  screen_fundamentals: "Screening on fundamentals",
  search_companies: "Finding the company",
  get_results: "Reading quarterly results",
  evaluate_results: "Scoring past results",
  search_web: "Searching the web",
  search_news: "Reading the news",
  get_bars: "Reading price history",
  get_indicator: "Computing an indicator",
  read_indicators: "Reading the technicals",
  read_symbol: "Reading the chart",
  get_levels: "Finding support and resistance",
  get_trendlines: "Fitting trendlines",
  get_trend: "Measuring the trend",
  get_patterns: "Looking for patterns",
  evaluate_pattern: "Testing the pattern's base rate",
  get_divergences: "Checking for divergence",
  get_gaps: "Finding gaps",
  confirm_reversal: "Testing the reversal",
  multi_timeframe: "Checking other timeframes",
  volume_profile: "Building the volume profile",
  explain_move: "Working out what moved it",
  get_flows: "Reading FII/DII flows",
  get_deals: "Reading bulk and block deals",
  get_peers: "Finding peers",
  compare_symbols: "Comparing",
  screen_universe: "Screening the universe",
};

/** Past this the field scrolls instead of growing — a pill that keeps
 *  growing eventually eats the page it is asking about. */
const MAX_FIELD_PX = 96;

const toolLine = (name: string): string =>
  TOOL_WORD[name] ?? name.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export function StockAskBar({
  symbol,
  name,
}: {
  symbol: string;
  /** Company name, when the page has it. Only enriches the attachment. */
  name?: string | null;
}): React.ReactElement {
  const [value, setValue] = React.useState("");
  const [turns, setTurns] = React.useState<Turn[]>([]);
  const [open, setOpen] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [focused, setFocused] = React.useState(false);

  const inputRef = React.useRef<HTMLTextAreaElement | null>(null);
  const scrollRef = React.useRef<HTMLDivElement | null>(null);
  const abortRef = React.useRef<AbortController | null>(null);

  /* The bar is fixed to the viewport so it survives scrolling, but it belongs
     to the READING COLUMN, not the window: centred on the screen it sits off
     to one side of the page whenever the app shell's sidebar is open. So an
     in-flow anchor of zero height is rendered where the component is mounted
     — inside the content column — and the fixed bar is centred on that
     anchor's box instead. Measured, not assumed: the sidebar collapses, the
     window resizes, and a hardcoded offset would be wrong in both cases. */
  const anchorRef = React.useRef<HTMLDivElement | null>(null);
  const [column, setColumn] = React.useState<{ cx: number; w: number } | null>(null);

  React.useEffect(() => {
    const el = anchorRef.current;
    if (!el) return;
    const measure = (): void => {
      const r = el.getBoundingClientRect();
      if (r.width > 0) setColumn({ cx: r.left + r.width / 2, w: r.width });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener("resize", measure);
    return () => { ro.disconnect(); window.removeEventListener("resize", measure); };
  }, []);

  // One conversation per symbol. Switching companies starts a fresh thread
  // rather than carrying the last one's subject into this one — "it" must not
  // mean the previous company. (Pivotted holds no server-side state for the
  // id; the thread is the transcript this component sends, which is why the
  // reset below is the whole reset.)
  const conversationId = React.useMemo(
    () => `stock-${symbol.toLowerCase()}-${Math.random().toString(36).slice(2, 10)}`,
    [symbol],
  );

  React.useEffect(() => {
    setTurns([]); setValue(""); setOpen(false);
    abortRef.current?.abort();
  }, [symbol]);

  // Abandoned streams must not outlive the page.
  React.useEffect(() => () => abortRef.current?.abort(), []);

  // ⌘K / Ctrl+K focuses the bar from anywhere on the page; Escape closes the
  // transcript without losing it.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
      } else if (e.key === "Escape" && open) {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  // Follow the answer as it streams.
  React.useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns]);

  /* Autosize the field. A bare <textarea rows="1"> is whatever height the
     browser thinks a textarea should be — which was making the resting pill
     74px tall instead of the 56 it is laid out for. Collapse to zero, measure
     the content, then take that height up to the cap. */
  React.useLayoutEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, MAX_FIELD_PX)}px`;
  }, [value]);

  const ask = React.useCallback(async (question: string): Promise<void> => {
    const q = question.trim();
    if (!q || busy) return;

    setValue("");
    setOpen(true);
    setBusy(true);
    setTurns((prev) => [...prev, { question: q, answer: "", tool: null, done: false, error: null }]);

    // The transcript so far, so a follow-up ("and its margins?") has the
    // thread it is a follow-up to.
    const history: ChatHistoryMessage[] = turns.flatMap((t) =>
      t.answer
        ? [
          { role: "user" as const, content: t.question },
          { role: "assistant" as const, content: t.answer },
        ]
        : [],
    );

    const controller = new AbortController();
    abortRef.current = controller;

    /** Mutate only the turn we just pushed — a stream that finishes after the
     *  user has asked again must not overwrite the newer answer. */
    const patch = (fn: (t: Turn) => Turn): void =>
      setTurns((prev) => prev.map((t, i) => (i === prev.length - 1 ? fn(t) : t)));

    try {
      const token = await getAccessToken();
      const attachment: Record<string, unknown> = { kind: "security", symbol: symbol.toUpperCase() };
      if (name) attachment.name = name;

      for await (const ev of streamChat(
        q, history, token, controller.signal, conversationId,
        null, null, null, [attachment],
      )) {
        if (ev.type === "tool_start") patch((t) => ({ ...t, tool: ev.name }));
        else if (ev.type === "tool_done") patch((t) => ({ ...t, tool: null }));
        else if (ev.type === "delta") patch((t) => ({ ...t, answer: t.answer + ev.text }));
        else if (ev.type === "replace") patch((t) => ({ ...t, answer: ev.text }));
        else if (ev.type === "error") patch((t) => ({ ...t, error: ev.message, done: true }));
        else if (ev.type === "done") {
          patch((t) => ({ ...t, answer: ev.response || t.answer, tool: null, done: true }));
        }
      }
      patch((t) => ({ ...t, done: true }));
    } catch (err) {
      // An aborted stream is the user's own doing, not a failure to report.
      if (!controller.signal.aborted) {
        patch((t) => ({
          ...t,
          error: err instanceof Error ? err.message : "Could not reach the research server.",
          done: true,
        }));
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setBusy(false);
    }
  }, [busy, conversationId, name, symbol, turns]);

  const submit = (e: React.FormEvent): void => {
    e.preventDefault();
    void ask(value);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void ask(value);
    }
  };

  const sym = symbol.toUpperCase();

  /* Small until it is being used. At rest the bar is a narrow pill that says
     what it is and takes almost no room over the page; clicking it widens the
     pill and brings up the transcript. Anything mid-flight (a live stream, an
     open answer) counts as "in use" and holds it open, so the bar never
     shrinks away from an answer the reader is still reading. */
  const expanded = focused || busy || (open && turns.length > 0);

  return (
    <>
      <div ref={anchorRef} aria-hidden style={{ height: 0 }} />

      {/* The region and the field must not share an accessible name, or a
          screen reader announces the same phrase twice on the way in. */}
      <div
        className={`stock-ask${expanded ? " is-expanded" : ""}`}
        role="complementary"
        aria-label="Ask about this company"
        style={column
          ? ({
            "--ask-cx": `${column.cx}px`,
            // Never wider than the column it is centred on.
            "--ask-max": `${Math.max(240, column.w - 28)}px`,
          } as React.CSSProperties)
          : undefined}
      >
        {open && turns.length ? (
          <div className="stock-ask-panel">
            <div className="stock-ask-panel-head">
              <span className="stock-ask-panel-title">{sym}</span>
              <span className="stock-ask-panel-note">
                {turns.length === 1 ? "Latest turn" : `${turns.length} turns`}
              </span>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Hide answers"
                className="stock-ask-x"
              >
                ✕
              </button>
            </div>

            <div className="stock-ask-scroll" ref={scrollRef} aria-live="polite">
              {turns.map((t, i) => (
                <div key={i} className="stock-ask-turn">
                  <div className="stock-ask-q">{t.question}</div>
                  {t.error ? (
                    <div className="stock-ask-err">{t.error}</div>
                  ) : t.answer ? (
                    <AssistantMessage text={t.answer} className="stock-ask-a" />
                  ) : (
                    <div className="stock-ask-wait">
                      <span className="stock-ask-dot" />
                      {t.tool ? `${toolLine(t.tool)}…` : "Thinking…"}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <form className="stock-ask-pill" onSubmit={submit}>
          {/* Charto's glyph — the same asset the chart signs itself with,
              copied from charto/preview/assets. Painted as a MASK over
              currentColor rather than dropped in as an <img>, which is
              charto's own reason for doing it that way: the file is a
              single-colour alpha, and an image of it would be black-on-black
              the moment the theme goes dark. One file, both themes. */}
          <span className="stock-ask-mark" aria-hidden />
          <textarea
            ref={inputRef}
            rows={1}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            onFocus={() => { setFocused(true); if (turns.length) setOpen(true); }}
            /* Blur alone must not collapse it — clicking the send button or
               scrolling the answer both blur the field, and a bar that folded
               up under the cursor mid-question would be unusable. It shrinks
               back only once the field is empty and nothing is in flight. */
            onBlur={() => { if (!value.trim()) setFocused(false); }}
            placeholder={`Ask about ${sym}…`}
            aria-label={`Ask about ${sym}`}
            className="stock-ask-input"
          />
          {turns.length && !open ? (
            <button
              type="button"
              className="stock-ask-reopen"
              onClick={() => setOpen(true)}
            >
              {turns.length} answer{turns.length === 1 ? "" : "s"}
            </button>
          ) : null}
          {/* The same mic the chat composer uses, not a second one: it already
              records through MediaRecorder, posts the blob to
              /audio/transcribe, and comes back with ENGLISH text even when the
              question was spoken in Hindi or Hinglish. It renders nothing at
              all where MediaRecorder is unavailable, so this layout never
              carries a dead button. */}
          <VoiceInputButton
            size={17}
            className="stock-ask-mic"
            data-testid="stock-ask-voice-btn"
            onTranscript={(text) => {
              // Append rather than replace: a second dictation continues the
              // question instead of throwing the first half away.
              setValue((prev) => {
                const existing = prev.trimEnd();
                return existing ? `${existing} ${text}` : text;
              });
              setFocused(true);
              inputRef.current?.focus();
            }}
          />
          <button
            type={busy ? "button" : "submit"}
            onClick={busy ? () => abortRef.current?.abort() : undefined}
            disabled={!busy && !value.trim()}
            aria-label={busy ? "Stop" : "Ask"}
            className="stock-ask-send"
          >
            {busy ? <span className="stock-ask-stop" /> : "↑"}
          </button>
        </form>

        {/* The disclaimer belongs to the open bar, not the resting one — at
            rest the pill is meant to be nearly invisible. */}
        {expanded && !turns.length ? (
          <div className="stock-ask-hint" aria-hidden>
            Answered from this company&apos;s own filings and bars. Not financial advice.
          </div>
        ) : null}
      </div>

      <style>{`
        /* ── the glass ──────────────────────────────────────────────────
           One set of tokens, two themes. The fill carries a real alpha so
           the blur behind it has something to tint; the inset highlight is
           what stops a translucent panel reading as a flat grey rectangle. */
        .stock-ask {
          --ask-fill: rgba(255, 255, 255, 0.58);
          --ask-fill-solid: #ffffff;
          --ask-edge: rgba(15, 18, 22, 0.09);
          --ask-gloss: rgba(255, 255, 255, 0.92);
          --ask-sheen: rgba(255, 255, 255, 0.5);
          --ask-drop: 0 16px 48px rgba(15, 18, 22, 0.16), 0 3px 10px rgba(15, 18, 22, 0.07);
          --ask-ink: #0f1216;
          --ask-ink-fg: #ffffff;

          --ask-cx: 50vw;
          --ask-max: calc(100vw - 28px);

          position: fixed;
          left: var(--ask-cx);
          bottom: 20px;
          transform: translateX(-50%);
          z-index: 60;
          /* Small at rest — a pill that names itself and little else. */
          width: min(340px, var(--ask-max));
          display: flex;
          flex-direction: column;
          gap: 8px;
          font-family: var(--font-ui);
          transition: width 220ms cubic-bezier(0.32, 0.72, 0, 1);
        }
        /* …and only as big as it needs to be once someone is using it. */
        .stock-ask.is-expanded { width: min(640px, var(--ask-max)); }
        .dark .stock-ask {
          --ask-fill: rgba(28, 28, 29, 0.58);
          --ask-fill-solid: #1c1c1d;
          --ask-edge: rgba(255, 255, 255, 0.16);
          --ask-gloss: rgba(255, 255, 255, 0.14);
          --ask-sheen: rgba(255, 255, 255, 0.07);
          --ask-drop: 0 18px 54px rgba(0, 0, 0, 0.6), 0 3px 10px rgba(0, 0, 0, 0.45);
          /* Black-on-black is not a button. In dark the "black" button is the
             one solid ink surface available — white — reading as the same
             high-contrast affordance. */
          --ask-ink: #f5f5f5;
          --ask-ink-fg: #0f1216;
        }

        .stock-ask-pill,
        .stock-ask-panel {
          position: relative;
          background: var(--ask-fill);
          -webkit-backdrop-filter: blur(40px) saturate(200%);
          backdrop-filter: blur(40px) saturate(200%);
          border: 1px solid var(--ask-edge);
          /* Three shadows doing three jobs: the drop lifts it off the page,
             the top inset is the lit edge where glass catches light, the
             bottom inset is the thin shadow the far edge throws back. */
          box-shadow:
            var(--ask-drop),
            inset 0 1px 0 var(--ask-gloss),
            inset 0 -1px 0 rgba(15, 18, 22, 0.05);
        }
        /* The sheen. Real glass is brighter at the top than the bottom, and
           without this gradient a blurred panel reads as flat frosted plastic.
           Non-interactive and clipped to the same radius as its host. */
        .stock-ask-pill::before,
        .stock-ask-panel::before {
          content: "";
          position: absolute;
          inset: 0;
          border-radius: inherit;
          pointer-events: none;
          background: linear-gradient(
            180deg,
            var(--ask-sheen) 0%,
            rgba(255, 255, 255, 0) 42%,
            rgba(255, 255, 255, 0) 100%
          );
        }
        .stock-ask-pill > *,
        .stock-ask-panel > * { position: relative; z-index: 1; }
        /* Where the browser cannot blur, a translucent fill would leave the
           page's own text showing through this one. Go opaque instead. */
        @supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
          .stock-ask-pill,
          .stock-ask-panel { background: var(--ask-fill-solid); }
        }

        /* ── the pill ───────────────────────────────────────────────── */
        .stock-ask-pill {
          display: flex;
          align-items: center;
          gap: 11px;
          /* min-height, not height: the field grows to two lines for a long
             question and the pill has to grow with it. */
          min-height: 56px;
          padding: 8px 10px 8px 16px;
          border-radius: 28px;
          transition: min-height 220ms cubic-bezier(0.32, 0.72, 0, 1);
        }
        .stock-ask.is-expanded .stock-ask-pill { min-height: 60px; }
        /* At rest there is nothing to send and nothing to stop, so the button
           is not there to be aimed at. The MIC stays — speaking instead of
           typing is a reason to reach for the bar in the first place, and
           hiding it behind a click would cost the gesture it saves. */
        .stock-ask:not(.is-expanded) .stock-ask-send { display: none; }
        .stock-ask-mic { flex: none; align-self: center; }

        .stock-ask-mark {
          flex: none;
          width: 22px;
          height: 22px;
          background: var(--text-primary);
          /* The chart's own file, at the chart's own address. Pivot kept a
             copy in its /public; here that copy would 404, because this page
             is proxied onto the chart's origin and /charto-mark.png is a
             route the chart has never heard of. /assets/pivot-mark.png is
             the same bytes (identical checksum) already served there — one
             file, and no second copy to drift. */
          -webkit-mask: url("/assets/pivot-mark.png") center / contain no-repeat;
                  mask: url("/assets/pivot-mark.png") center / contain no-repeat;
        }
        .stock-ask-input {
          flex: 1 1 auto;
          min-width: 0;
          /* Base height for one line. The layout effect overwrites it on
             every keystroke; this is what it looks like before the first. */
          height: 21px;
          max-height: 96px;
          overflow-y: auto;
          border: 0;
          outline: none;
          resize: none;
          background: transparent;
          color: var(--text-primary);
          font-family: inherit;
          font-size: 14px;
          line-height: 1.5;
          padding: 0;
        }
        .stock-ask-input::placeholder { color: var(--text-tertiary); }

        .stock-ask-reopen {
          flex: none;
          border: 1px solid var(--ask-edge);
          background: transparent;
          color: var(--text-tertiary);
          border-radius: var(--radius-pill);
          padding: 3px 9px;
          font-size: 11px;
          cursor: pointer;
        }

        .stock-ask-send {
          flex: none;
          width: 40px;
          height: 40px;
          display: grid;
          place-items: center;
          border: 0;
          border-radius: 50%;
          background: var(--ask-ink);
          color: var(--ask-ink-fg);
          font-size: 16px;
          line-height: 1;
          cursor: pointer;
          transition: opacity 120ms ease;
        }
        .stock-ask-send:disabled { opacity: 0.28; cursor: default; }
        .stock-ask-stop {
          width: 11px; height: 11px; border-radius: 2px; background: var(--ask-ink-fg);
        }

        /* ── the transcript ─────────────────────────────────────────── */
        .stock-ask-panel {
          border-radius: 18px;
          overflow: hidden;
        }
        .stock-ask-panel-head {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 9px 12px;
          border-bottom: 1px solid var(--ask-edge);
        }
        .stock-ask-panel-title {
          font-size: 11.5px;
          font-weight: 650;
          letter-spacing: 0.02em;
          color: var(--text-primary);
        }
        .stock-ask-panel-note {
          flex: 1 1 auto;
          font-size: 11px;
          color: var(--text-tertiary);
        }
        .stock-ask-x {
          border: 0;
          background: transparent;
          color: var(--text-tertiary);
          font-size: 12px;
          cursor: pointer;
          line-height: 1;
          padding: 2px 4px;
        }

        .stock-ask-scroll {
          max-height: min(46vh, 420px);
          overflow-y: auto;
          padding: 12px;
          display: flex;
          flex-direction: column;
          gap: 16px;
        }
        .stock-ask-turn { display: flex; flex-direction: column; gap: 7px; }
        .stock-ask-q {
          align-self: flex-end;
          max-width: 88%;
          padding: 6px 11px;
          border-radius: 14px 14px 4px 14px;
          background: var(--accent-wash);
          color: var(--text-primary);
          font-size: 12.5px;
          line-height: 1.5;
        }
        .stock-ask-a {
          font-size: 13px;
          line-height: 1.62;
          color: var(--text-primary);
        }
        .stock-ask-err {
          font-size: 12px;
          color: var(--color-loss);
        }
        .stock-ask-wait {
          display: flex;
          align-items: center;
          gap: 7px;
          font-size: 12px;
          color: var(--text-tertiary);
        }
        .stock-ask-dot {
          width: 6px; height: 6px; border-radius: 50%;
          background: var(--pivot-blue);
          animation: stock-ask-pulse 1.1s ease-in-out infinite;
        }
        @keyframes stock-ask-pulse {
          0%, 100% { opacity: 0.25; }
          50%      { opacity: 1; }
        }

        .stock-ask-hint {
          text-align: center;
          font-size: 10.5px;
          color: var(--text-tertiary);
          opacity: 0.85;
        }

        @media (prefers-reduced-motion: reduce) {
          .stock-ask-dot { animation: none; opacity: 0.7; }
          .stock-ask,
          .stock-ask-pill,
          .stock-ask-send { transition: none; }
        }
      `}</style>
    </>
  );
}
