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
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  Copy,
  Maximize2,
  Minimize2,
  Trash2,
} from "lucide-react";

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

/** How long the "new answer" chip stays before it settles to a dot. */
const NOTICE_MS = 4000;

/**
 * Charto's mark, inline.
 *
 * This is `app/icon.svg` — the disc with the bar and the slash cut out of it,
 * the same mark the tab and the chart carry. It replaces a PNG mask of the
 * older wordmark glyph, which was a different logo AND a network request that
 * could 404 (it did, on the chart's origin) and leave a blank square.
 *
 * As an inline SVG it takes `currentColor`, so it follows the header's text
 * colour into both themes with no second asset and nothing to fetch. The
 * cutouts are a mask rather than drawn strokes: strokes would have to be
 * re-tuned at every size, a mask stays exact.
 */
function ChartoMark({ size = 15 }: { size?: number }): React.ReactElement {
  const id = React.useId();
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 652 652"
      aria-hidden="true"
      focusable="false"
      style={{ display: "block", flex: "none" }}
    >
      <mask id={id}>
        <circle cx="326" cy="326" r="326" fill="#fff" />
        <rect x="460.5" y="0" width="37" height="652" fill="#000" />
        <path d="M567.4 18.1 595.4 42.4 83.6 632.9 55.6 608.6Z" fill="#000" />
      </mask>
      <circle cx="326" cy="326" r="326" fill="currentColor" mask={`url(#${id})`} />
    </svg>
  );
}

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
  /* An answer that finished while the transcript was collapsed. The pill says
     so once, quietly, and then keeps a dot — a panel that reopens itself over
     the page the reader is using is the rudest possible way to deliver news. */
  const [unseen, setUnseen] = React.useState(0);
  const [notice, setNotice] = React.useState(false);
  /* Taller, for an answer that is mostly table. Not a drag handle: two
     heights the reader can name beat a pixel value they have to maintain. */
  const [tall, setTall] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  /* Whether the transcript is scrolled to the end. Following the stream is
     right until the reader scrolls up to re-read something, at which point
     yanking them back down is the bug. */
  const [pinned, setPinned] = React.useState(true);

  // The stream finishes in a closure that captured `open` at send time; a ref
  // is what lets it ask whether the transcript is on screen NOW.
  const openRef = React.useRef(open);
  React.useEffect(() => { openRef.current = open; }, [open]);

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

  /* The phone keyboard, which a bar pinned to the bottom of the page cannot
     ignore. iOS does not shrink the LAYOUT viewport when the keyboard opens,
     so `position: fixed; bottom: 20px` stays measured against the full screen
     and the composer someone is typing into sits behind the keys. The visual
     viewport does move, and the difference between the two is exactly how far
     up the bar has to come. Zero on every desktop, and zero on Android, where
     the layout viewport resizes and `bottom` was already right. */
  const [keyboard, setKeyboard] = React.useState(0);
  React.useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    const track = (): void => {
      const covered = window.innerHeight - vv.height - vv.offsetTop;
      // Under a few pixels this is a URL bar collapsing, not a keyboard, and
      // reacting to it would make the bar twitch on every scroll.
      setKeyboard(covered > 24 ? Math.round(covered) : 0);
    };
    track();
    vv.addEventListener("resize", track);
    vv.addEventListener("scroll", track);
    return () => {
      vv.removeEventListener("resize", track);
      vv.removeEventListener("scroll", track);
    };
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
    setUnseen(0); setNotice(false); setTall(false); setPinned(true);
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

  // Follow the answer as it streams — unless the reader has scrolled up.
  React.useEffect(() => {
    const el = scrollRef.current;
    if (el && pinned) el.scrollTop = el.scrollHeight;
  }, [turns, pinned]);

  // Re-arm the notice each time an answer lands while collapsed.
  React.useEffect(() => {
    if (!notice) return;
    const t = window.setTimeout(() => setNotice(false), NOTICE_MS);
    return () => window.clearTimeout(t);
  }, [notice]);

  // Opening the transcript is what marks it read.
  React.useEffect(() => {
    if (open) { setUnseen(0); setNotice(false); }
  }, [open, turns.length]);

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
          if (!openRef.current) { setUnseen((n) => n + 1); setNotice(true); }
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
  const last = turns.length ? turns[turns.length - 1] : null;
  const streaming = busy && !!last && !last.done;
  const lastAnswer = React.useMemo(
    () => [...turns].reverse().find((t) => t.answer && t.done)?.answer ?? "",
    [turns],
  );

  /* Small until it is being used. At rest the bar is a narrow pill that says
     what it is and takes almost no room over the page; using it widens the
     pill and brings up the transcript. Anything mid-flight (a live stream, an
     open answer) counts as "in use" and holds it open, so the bar never
     shrinks away from an answer the reader is still reading. */
  const expanded = focused || busy || (open && turns.length > 0);
  const showPanel = open && turns.length > 0;

  const copyLast = React.useCallback(async (): Promise<void> => {
    if (!lastAnswer) return;
    try {
      await navigator.clipboard.writeText(lastAnswer);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      /* denied clipboard permission — nothing to say, the button just
         does not confirm */
    }
  }, [lastAnswer]);

  const clearThread = React.useCallback((): void => {
    abortRef.current?.abort();
    setTurns([]); setOpen(false); setUnseen(0); setNotice(false); setPinned(true);
    inputRef.current?.focus();
  }, []);

  /* Pinned means "the reader is at the end". Measured with a 24px tolerance
     because a streaming answer grows under the scroll position and an exact
     comparison flickers between pinned and not on every frame. */
  const onScroll = React.useCallback((e: React.UIEvent<HTMLDivElement>): void => {
    const el = e.currentTarget;
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 24);
  }, []);

  const jumpToLatest = React.useCallback((): void => {
    const el = scrollRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
    setPinned(true);
  }, []);

  return (
    <>
      <div ref={anchorRef} aria-hidden style={{ height: 0 }} />

      {/* The region and the field must not share an accessible name, or a
          screen reader announces the same phrase twice on the way in. */}
      <div
        className={`stock-ask${expanded ? " is-expanded" : ""}`}
        role="complementary"
        aria-label="Ask about this company"
        style={{
          ...(column
            ? {
              "--ask-cx": `${column.cx}px`,
              // Never wider than the column it is centred on.
              "--ask-max": `${Math.max(240, column.w - 28)}px`,
            }
            : {}),
          "--ask-kb": `${keyboard}px`,
        } as React.CSSProperties}
      >
        {showPanel ? (
          <section
            className={`stock-ask-panel${tall ? " is-tall" : ""}`}
            aria-label={`Answers about ${sym}`}
          >
            {/* Header. Identity on the left, controls on the right, one
                hairline between it and the reading area — the arrangement
                every well-behaved panel uses, because a reader looking for
                "how do I close this" looks top-right first. */}
            <header className="stock-ask-head">
              <span className="stock-ask-brand"><ChartoMark size={15} /></span>
              <span className="stock-ask-sym">{sym}</span>
              <span className="stock-ask-count">
                {turns.length} {turns.length === 1 ? "question" : "questions"}
              </span>
              <span className="stock-ask-grow" />
              <button
                type="button"
                className="stock-ask-icon"
                onClick={() => void copyLast()}
                disabled={!lastAnswer}
                aria-label={copied ? "Answer copied" : "Copy the last answer"}
                title={copied ? "Copied" : "Copy answer"}
              >
                {copied ? <Check size={14} /> : <Copy size={14} />}
              </button>
              <button
                type="button"
                className="stock-ask-icon"
                onClick={() => setTall((v) => !v)}
                aria-pressed={tall}
                aria-label={tall ? "Shrink the panel" : "Grow the panel"}
                title={tall ? "Shrink" : "Grow"}
              >
                {tall ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
              </button>
              <button
                type="button"
                className="stock-ask-icon"
                onClick={clearThread}
                aria-label="Clear this thread"
                title="Clear thread"
              >
                <Trash2 size={14} />
              </button>
              <button
                type="button"
                className="stock-ask-icon"
                onClick={() => setOpen(false)}
                aria-label="Hide the answers"
                title="Hide (Esc)"
              >
                <ChevronDown size={15} />
              </button>
            </header>

            {/* One hairline that travels while a turn is in flight. It sits on
                the header's own border, so nothing moves when it appears —
                a spinner that adds a row makes the whole panel jump. */}
            <div
              className={`stock-ask-rail${streaming ? " is-live" : ""}`}
              aria-hidden="true"
            />

            <div
              className="stock-ask-scroll"
              ref={scrollRef}
              onScroll={onScroll}
              aria-live="polite"
            >
              {turns.map((t, i) => (
                <article className="stock-ask-turn" key={i}>
                  {/* The question, as a quiet heading rather than a chat
                      bubble on the right. Two opposing bubble colours is a
                      messaging app; this is a document with a question at the
                      top of each section, which is what it actually is. */}
                  <p className="stock-ask-q">{t.question}</p>

                  {t.answer ? (
                    <AssistantMessage text={t.answer} className="stock-ask-a" />
                  ) : null}

                  {/* A failure does not delete what already arrived. A model
                      turn that stalls mid-stream had usually said something
                      useful first, and replacing three paragraphs of read
                      financials with one line of error is the reader losing
                      work they watched appear. */}
                  {t.error ? (
                    <p className="stock-ask-err">
                      {t.answer ? `Stopped mid-answer — ${t.error}` : t.error}
                    </p>
                  ) : t.answer ? null : (
                    <p className="stock-ask-wait">
                      <span className="stock-ask-spark" aria-hidden="true" />
                      {t.tool ? `${toolLine(t.tool)}…` : "Thinking…"}
                    </p>
                  )}
                </article>
              ))}
            </div>

            {/* Only while the reader is somewhere above the end. */}
            {!pinned ? (
              <button type="button" className="stock-ask-jump" onClick={jumpToLatest}>
                <ArrowDown size={13} aria-hidden="true" />
                Latest
              </button>
            ) : null}
          </section>
        ) : null}

        <form className="stock-ask-pill" onSubmit={submit}>
          <span className="stock-ask-mark" aria-hidden="true">
            <ChartoMark size={17} />
          </span>
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

          {/* The collapsed thread, and any news from it. A finished answer
              nobody has seen shows its count once and then settles to a dot:
              the reader is told, and never interrupted. */}
          {turns.length && !open ? (
            <button
              type="button"
              className={`stock-ask-recall${unseen ? " has-unseen" : ""}`}
              onClick={() => setOpen(true)}
              aria-label={
                unseen
                  ? `${unseen} new answer${unseen === 1 ? "" : "s"} — show them`
                  : "Show the answers"
              }
              title="Show answers"
            >
              {unseen && notice ? (
                <span className="stock-ask-recall-text">
                  {unseen} new
                </span>
              ) : (
                <>
                  <span className="stock-ask-recall-n">{turns.length}</span>
                  {unseen ? <span className="stock-ask-pip" aria-hidden="true" /> : null}
                </>
              )}
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
            {busy ? <span className="stock-ask-stop" /> : <ArrowUp size={16} strokeWidth={2.4} />}
          </button>
        </form>

        {/* The disclaimer belongs to the open bar, not the resting one — at
            rest the pill is meant to be nearly invisible. */}
        {expanded && !turns.length ? (
          <p className="stock-ask-hint">
            Answered from this company&apos;s own filings and bars. Not financial advice.
          </p>
        ) : null}
      </div>

      <style>{`
        /* ── surface ────────────────────────────────────────────────────
           Two surfaces, one system. The PILL stays glass: it floats over the
           page, it is small, and the blur is what keeps it from reading as a
           box dropped on top. The PANEL is nearly opaque, and that is the
           whole correction here — a 58%-translucent reading surface let the
           page's own tables and headings show through the answer, which is
           what made a long reply look like a mess rather than a document.
           Glass is right for chrome and wrong for prose. */
        .stock-ask {
          --ask-glass: color-mix(in srgb, var(--bg-base) 62%, transparent);
          --ask-solid: color-mix(in srgb, var(--bg-base) 97%, transparent);
          --ask-edge: var(--glass-border);
          --ask-edge-strong: var(--glass-border-hover);
          --ask-sheen: rgba(255, 255, 255, 0.5);
          --ask-lift: 0 18px 44px -12px rgba(15, 18, 22, 0.22),
                      0 2px 8px rgba(15, 18, 22, 0.06);
          --ask-ink: #0f1216;
          --ask-ink-fg: #ffffff;
          --ask-hover: color-mix(in srgb, var(--text-primary) 7%, transparent);

          --ask-cx: 50vw;
          --ask-max: calc(100vw - 28px);

          position: fixed;
          left: var(--ask-cx);
          /* 20px from the bottom, plus the phone's home-indicator inset (the
             root layout sets viewport-fit=cover, so this resolves to a real
             number there and 0 everywhere else), plus however much of the
             screen the keyboard is currently covering. */
          bottom: calc(20px + env(safe-area-inset-bottom, 0px) + var(--ask-kb, 0px));
          transform: translateX(-50%);
          z-index: 60;
          width: min(340px, var(--ask-max));
          display: flex;
          flex-direction: column;
          gap: 10px;
          font-family: var(--font-ui);
          transition: width 260ms cubic-bezier(0.32, 0.72, 0, 1);
        }
        .stock-ask.is-expanded { width: min(680px, var(--ask-max)); }
        .dark .stock-ask {
          --ask-sheen: rgba(255, 255, 255, 0.06);
          --ask-lift: 0 20px 50px -12px rgba(0, 0, 0, 0.68),
                      0 2px 8px rgba(0, 0, 0, 0.5);
          /* Black-on-black is not a button. In dark the "black" button is the
             one solid ink surface available — white — reading as the same
             high-contrast affordance. */
          --ask-ink: #f5f5f5;
          --ask-ink-fg: #0f1216;
        }

        /* ── the pill ───────────────────────────────────────────────── */
        .stock-ask-pill {
          position: relative;
          display: flex;
          align-items: center;
          gap: 10px;
          /* min-height, not height: the field grows to two lines for a long
             question and the pill has to grow with it. */
          min-height: 54px;
          padding: 7px 8px 7px 15px;
          border-radius: var(--radius-pill);
          background: var(--ask-glass);
          -webkit-backdrop-filter: blur(28px) saturate(180%);
          backdrop-filter: blur(28px) saturate(180%);
          border: 1px solid var(--ask-edge);
          box-shadow: var(--ask-lift), inset 0 1px 0 var(--ask-sheen);
          transition: min-height 260ms cubic-bezier(0.32, 0.72, 0, 1),
                      border-color 160ms ease;
        }
        .stock-ask.is-expanded .stock-ask-pill { min-height: 58px; }
        .stock-ask-pill:focus-within { border-color: var(--ask-edge-strong); }
        /* Where the browser cannot blur, a translucent fill would leave the
           page's own text showing through this one. Go opaque instead. */
        @supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
          .stock-ask-pill { background: var(--ask-solid); }
        }

        .stock-ask-mark {
          flex: none;
          display: grid;
          place-items: center;
          color: var(--text-primary);
          opacity: 0.9;
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
          letter-spacing: -0.006em;
        }
        /* iOS Safari zooms the page when a field it focuses is under 16px, and
           it does not zoom back out. 14px is the design on a pointer device;
           on a touch screen the field is 16px and the zoom never fires. */
        @media (pointer: coarse) {
          .stock-ask-input { font-size: 16px; }
        }
        .stock-ask-input::placeholder { color: var(--text-tertiary); }

        /* The collapsed thread. A count, quiet, with a dot when something in
           it is unread — and the word "new" only for the first few seconds. */
        .stock-ask-recall {
          flex: none;
          position: relative;
          display: inline-flex;
          align-items: center;
          gap: 5px;
          height: 26px;
          min-width: 26px;
          padding: 0 8px;
          border: 1px solid var(--ask-edge);
          border-radius: var(--radius-pill);
          background: transparent;
          color: var(--text-secondary);
          font-family: inherit;
          font-size: 11.5px;
          font-variant-numeric: tabular-nums;
          cursor: pointer;
          transition: background 140ms ease, border-color 140ms ease, color 140ms ease;
        }
        .stock-ask-recall:hover {
          background: var(--ask-hover);
          color: var(--text-primary);
          border-color: var(--ask-edge-strong);
        }
        .stock-ask-recall.has-unseen { color: var(--text-primary); }
        .stock-ask-recall-text { animation: stock-ask-fade 180ms ease both; }
        .stock-ask-pip {
          width: 5px;
          height: 5px;
          border-radius: 50%;
          background: var(--accent);
        }
        .stock-ask-send {
          flex: none;
          width: 38px;
          height: 38px;
          display: grid;
          place-items: center;
          border: 0;
          border-radius: 50%;
          background: var(--ask-ink);
          color: var(--ask-ink-fg);
          cursor: pointer;
          transition: opacity 140ms ease, transform 140ms ease;
        }
        .stock-ask-send:disabled { opacity: 0.22; cursor: default; }
        .stock-ask-send:not(:disabled):active { transform: scale(0.94); }
        .stock-ask-stop {
          width: 10px; height: 10px; border-radius: 2px; background: var(--ask-ink-fg);
        }

        /* ── the transcript ─────────────────────────────────────────── */
        .stock-ask-panel {
          position: relative;
          display: flex;
          flex-direction: column;
          min-height: 0;
          border-radius: var(--radius-lg);
          overflow: hidden;
          background: var(--ask-solid);
          -webkit-backdrop-filter: blur(28px) saturate(160%);
          backdrop-filter: blur(28px) saturate(160%);
          border: 1px solid var(--ask-edge);
          box-shadow: var(--ask-lift);
          animation: stock-ask-rise 260ms cubic-bezier(0.32, 0.72, 0, 1) both;
        }
        @supports not ((backdrop-filter: blur(1px)) or (-webkit-backdrop-filter: blur(1px))) {
          .stock-ask-panel { background: var(--bg-base); }
        }

        .stock-ask-head {
          display: flex;
          align-items: center;
          gap: 8px;
          height: 40px;
          padding: 0 8px 0 13px;
          border-bottom: 1px solid var(--ask-edge);
        }
        .stock-ask-brand {
          display: grid;
          place-items: center;
          color: var(--text-primary);
          opacity: 0.85;
        }
        .stock-ask-sym {
          font-size: 12.5px;
          font-weight: 600;
          letter-spacing: -0.01em;
          color: var(--text-primary);
        }
        .stock-ask-count {
          font-size: 11.5px;
          color: var(--text-tertiary);
          font-variant-numeric: tabular-nums;
        }
        .stock-ask-grow { flex: 1 1 auto; }
        /* One button shape for every control in the header. Icon-only, 28px
           of hit area around a 14px glyph, and colour is the only thing that
           changes on hover — a header where each control has its own border
           reads as a toolbar of unrelated parts. */
        .stock-ask-icon {
          flex: none;
          width: 28px;
          height: 28px;
          display: grid;
          place-items: center;
          border: 0;
          border-radius: var(--radius-sm);
          background: transparent;
          color: var(--text-tertiary);
          cursor: pointer;
          transition: background 140ms ease, color 140ms ease;
        }
        .stock-ask-icon:hover:not(:disabled) {
          background: var(--ask-hover);
          color: var(--text-primary);
        }
        .stock-ask-icon:disabled { opacity: 0.35; cursor: default; }
        .stock-ask-icon:focus-visible {
          outline: 2px solid var(--accent);
          outline-offset: -2px;
        }

        /* The in-flight rail. Zero height when idle, so nothing reflows when
           it appears; it draws ON the header's border rather than beside it. */
        .stock-ask-rail {
          position: relative;
          height: 0;
          overflow: hidden;
        }
        .stock-ask-rail.is-live { height: 1px; margin-top: -1px; }
        .stock-ask-rail.is-live::after {
          content: "";
          position: absolute;
          inset: 0 auto 0 0;
          width: 38%;
          background: linear-gradient(
            90deg,
            transparent,
            var(--accent),
            transparent
          );
          animation: stock-ask-sweep 1.5s ease-in-out infinite;
        }

        .stock-ask-scroll {
          flex: 1 1 auto;
          min-height: 0;
          max-height: min(42vh, 380px);
          overflow-y: auto;
          overscroll-behavior: contain;
          padding: 14px 16px 16px;
          display: flex;
          flex-direction: column;
          gap: 18px;
          scrollbar-width: thin;
        }
        .stock-ask-panel.is-tall .stock-ask-scroll { max-height: min(72vh, 720px); }

        /* A turn is a section of a document: the question, then the answer.
           Divided by a hairline rather than by opposing bubbles. */
        .stock-ask-turn {
          display: flex;
          flex-direction: column;
          gap: 9px;
        }
        .stock-ask-turn + .stock-ask-turn {
          border-top: 1px solid var(--ask-edge);
          padding-top: 18px;
        }
        .stock-ask-q {
          margin: 0;
          font-size: 13px;
          font-weight: 550;
          line-height: 1.45;
          letter-spacing: -0.008em;
          color: var(--text-secondary);
        }
        .stock-ask-a {
          font-size: 13.5px;
          line-height: 1.65;
          color: var(--text-primary);
        }
        /* A research answer is mostly tables, and a table wider than the panel
           used to push the whole page sideways. It scrolls inside its own box
           instead — the one thing that must never scroll is the document. */
        .stock-ask-a table { font-size: 12px; }
        .stock-ask-a pre,
        .stock-ask-a .overflow-x-auto { max-width: 100%; overflow-x: auto; }

        .stock-ask-err {
          margin: 0;
          font-size: 12px;
          line-height: 1.5;
          color: var(--color-loss);
        }
        .stock-ask-wait {
          display: flex;
          align-items: center;
          gap: 8px;
          margin: 0;
          font-size: 12.5px;
          color: var(--text-tertiary);
        }
        /* Not a spinner. A spinner says "busy"; this says "still arriving",
           which is the honest description of a stream that has already told
           you which tool it is running. */
        .stock-ask-spark {
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background: var(--accent);
          animation: stock-ask-breathe 1.4s ease-in-out infinite;
        }

        .stock-ask-jump {
          position: absolute;
          left: 50%;
          bottom: 14px;
          transform: translateX(-50%);
          display: inline-flex;
          align-items: center;
          gap: 5px;
          height: 26px;
          padding: 0 11px;
          border: 1px solid var(--ask-edge);
          border-radius: var(--radius-pill);
          background: var(--ask-solid);
          -webkit-backdrop-filter: blur(20px);
          backdrop-filter: blur(20px);
          color: var(--text-secondary);
          font-family: inherit;
          font-size: 11.5px;
          cursor: pointer;
          box-shadow: 0 6px 18px -6px rgba(15, 18, 22, 0.28);
          animation: stock-ask-fade 160ms ease both;
        }
        .stock-ask-jump:hover { color: var(--text-primary); }

        .stock-ask-hint {
          margin: 0;
          text-align: center;
          font-size: 10.5px;
          color: var(--text-tertiary);
          opacity: 0.85;
        }

        @keyframes stock-ask-rise {
          from { opacity: 0; transform: translateY(6px) scale(0.994); }
          to   { opacity: 1; transform: none; }
        }
        @keyframes stock-ask-fade {
          from { opacity: 0; }
          to   { opacity: 1; }
        }
        @keyframes stock-ask-breathe {
          0%, 100% { opacity: 0.25; }
          50%      { opacity: 1; }
        }
        @keyframes stock-ask-sweep {
          0%   { transform: translateX(-100%); }
          100% { transform: translateX(360%); }
        }

        @media (prefers-reduced-motion: reduce) {
          .stock-ask-spark,
          .stock-ask-rail.is-live::after { animation: none; opacity: 0.7; }
          .stock-ask-panel { animation: none; }
          .stock-ask,
          .stock-ask-pill,
          .stock-ask-send { transition: none; }
        }
      `}</style>
    </>
  );
}
