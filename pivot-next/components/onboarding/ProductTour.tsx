"use client";

/**
 * ProductTour — first-run guided onboarding ("coach marks") for the app
 * shell, built on driver.js (MIT). A dimmed overlay spotlights one element
 * at a time with a themed popover explaining what it is and what to do
 * with it; the tour walks Chat → header → sidebar, then crosses tabs
 * (Opinion Markets → Agents → Screener) and lands back on the chat
 * composer.
 *
 * Behaviour contract:
 *   • Auto-starts once per browser (localStorage `pivot-tour-v1`) on
 *     desktop viewports, ~1.2s after the shell settles. Closing it at any
 *     point counts as done — it never nags twice.
 *   • Replayable via the `pivot:start-tour` window event (wired to
 *     Help → "Replay the tour" in the account menu).
 *   • Tab switching happens BETWEEN steps: the Next/Prev handlers are
 *     intercepted, the target tab is activated (keep-alive panes mount on
 *     first visit), and the step advances only once the target element
 *     exists — so the spotlight never lands on a not-yet-rendered pane.
 *   • Visual theme lives in globals.css under `.pivot-tour` (serif titles,
 *     ink pill buttons, token-driven colors — light and dark for free).
 */

import { useCallback, useEffect, useRef } from "react";
import { driver, type Driver } from "driver.js";
import "driver.js/dist/driver.css";

type TabKey =
  | "home"
  | "chat"
  | "portfolio"
  | "agents"
  | "screener"
  | "views";

/** Bump when the flow changes enough that existing users should see it again. */
const DONE_KEY = "pivot-tour-v1";
/** Set by the signup success handler so the tour fires exactly once after a
 *  new account is created. The login path does NOT set this — existing users
 *  never see the auto-start; they can replay from Help. */
export const TOUR_PENDING_KEY = "pivot:tour-pending";
// Module-local alias for the file-internal effects below.
const PENDING_KEY = TOUR_PENDING_KEY;
/** Below this viewport width the targets (sidebar, header) are hidden. */
const MIN_VIEWPORT = 1024;

export const START_TOUR_EVENT = "pivot:start-tour";

interface TourStep {
  /** Tab that must be active for the target element to exist. */
  tab: TabKey;
  /** Spotlight target. Omit for a centered, element-less card. */
  element?: string;
  /** Selector to await after a tab switch (defaults to `element`). */
  waitFor?: string;
  title: string;
  /** Rendered as HTML — <strong> = key term. No italics; example prompts are plain quoted text. */
  description: string;
  side?: "top" | "right" | "bottom" | "left";
  align?: "start" | "center" | "end";
}

const STEPS: TourStep[] = [
  {
    tab: "chat",
    title: "Welcome to Pivot",
    description:
      "An investing copilot that speaks your language. You describe what you want — Pivot answers with live market data, or builds the thing and hands it to you. The next minute shows you around.",
  },
  {
    tab: "chat",
    element: '[data-testid="chat-composer"]',
    side: "top",
    title: "It all starts here",
    description:
      "Type what you want, in plain English or Hinglish. “Analyse Reliance for me”, “Buy 10 INFY when RSI drops below 30”, “Show me the option chain for BANKNIFTY” — answers come back with real NSE data, and anything buildable arrives as an editable card in the chat.",
  },
  {
    tab: "chat",
    element: '[data-tour="search"]',
    side: "bottom",
    align: "start",
    title: "Any stock, on demand",
    description:
      "Look up any NSE stock — live price, chart, fundamentals, news — or jump back into an earlier conversation. Every symbol on the platform clicks through to its full page.",
  },
  {
    tab: "chat",
    element: '[data-testid="metric-strip"]',
    side: "bottom",
    align: "end",
    title: "Your money, always in sight",
    description:
      "Portfolio value and P&amp;L are visible at the top of the screen. You start in <strong>paper trading</strong>. Find the full breakdown in the Portfolio tab.",
  },
  {
    tab: "chat",
    element: '[data-tour="nav"]',
    side: "right",
    align: "start",
    title: "Everything else",
    description:
      "<strong>Home</strong> gives you the market at a glance, <strong>Chat</strong> is where you ask questions, and <strong>Portfolio</strong> holds your positions. We'll walk through the next three — starting with Opinions.",
  },
  {
    tab: "views",
    element:
      '[data-testid="views-grid"] [data-testid^="view-card"], [data-testid="views-grid"]',
    waitFor:
      '[data-testid="views-grid"], [data-testid="views-empty"], [data-testid="views-error"]',
    side: "right",
    title: "Trade what you believe",
    description:
      "You don't think in strike prices — you think “RBI will cut rates” or “defence has a decade ahead”. Each card is one such belief, researched and scored, with ready-made ways to put money behind it at your kind of risk. Open one and look around.",
  },
  {
    tab: "agents",
    element: '[data-testid="agents-list"], [data-testid="agents-empty"]',
    waitFor: '[data-testid="agents-list"], [data-testid="agents-empty"]',
    side: "top",
    title: "Your ideas, on duty",
    description:
      "Everything you build in chat shows up here — price alerts, option strategies, stock baskets. Each one is an agent that keeps watching the market for you, and you can pause or edit it anytime.",
  },
  {
    tab: "screener",
    element: ".screener-toolbar",
    waitFor: ".screener-toolbar",
    side: "bottom",
    align: "start",
    title: "The whole market, filtered",
    description:
      "Every NSE stock in one table. Cut it by sector, size, P/E, ROE — and when a filter combination starts to feel like a strategy, take it back to the chat and ask for a backtest.",
  },
  {
    tab: "chat",
    title: "Over to you",
    description:
      "That's the platform. Ask the first thing on your mind — or borrow one of ours: “Analyse TCS”, “Make me a basket of monsoon stocks”, “Backtest a 50/200 crossover on NIFTYBEES”. Replay this tour anytime from Help, under your profile.",
  },
];

/** Resolve once `selector` matches something (or the timeout passes — the
 *  tour degrades to a centered card rather than stalling forever). */
function waitForElement(selector: string, timeoutMs = 4000): Promise<void> {
  return new Promise((resolve) => {
    const started = performance.now();
    const tick = (): void => {
      if (
        document.querySelector(selector) ||
        performance.now() - started > timeoutMs
      ) {
        resolve();
        return;
      }
      window.setTimeout(tick, 60);
    };
    tick();
  });
}

export function ProductTour({
  activeTab,
  onTabChange,
  enabled = true,
}: {
  activeTab: TabKey;
  onTabChange: (tab: TabKey) => void;
  /** False on sub-routes (e.g. /stock/…) where the shell targets are absent. */
  enabled?: boolean;
}): null {
  // Refs, not state: driver.js lives outside React's render cycle, and the
  // Next/Prev interceptors need the CURRENT tab without re-instantiating.
  const tabRef = useRef(activeTab);
  tabRef.current = activeTab;
  const driverRef = useRef<Driver | null>(null);
  const onTabChangeRef = useRef(onTabChange);
  onTabChangeRef.current = onTabChange;

  const start = useCallback((): void => {
    if (driverRef.current?.isActive()) return;
    if (window.innerWidth < MIN_VIEWPORT) return;

    /** Move ±1 with a tab switch (and element wait) in between if needed. */
    const goStep = (dir: 1 | -1): void => {
      const d = driverRef.current;
      if (!d) return;
      const index = ((d.getState("activeIndex") as number | undefined) ?? 0) + dir;
      if (index < 0) return;
      if (index >= STEPS.length) {
        d.destroy();
        return;
      }
      const target = STEPS[index];
      if (!target) {
        d.destroy();
        return;
      }
      const proceed = (): void =>
        dir === 1 ? d.moveNext() : d.movePrevious();
      if (target.tab !== tabRef.current) {
        onTabChangeRef.current(target.tab);
        void waitForElement(
          target.waitFor ?? target.element ?? "body",
        ).then(proceed);
      } else {
        proceed();
      }
    };

    const d = driver({
      animate: true,
      smoothScroll: true,
      overlayColor: "#09090b",
      overlayOpacity: 0.55,
      stagePadding: 8,
      stageRadius: 14,
      popoverClass: "pivot-tour",
      showProgress: true,
      progressText: "{{current}} of {{total}}",
      nextBtnText: "Next",
      prevBtnText: "Back",
      doneBtnText: "Start asking",
      disableActiveInteraction: true,
      // Every step gets an explicit close (×) so the user is never trapped
      // mid-walk. driver.js gates that button on `allowClose`, which also
      // arms overlay-click and ESC as dismissals — so both are neutered
      // individually below. Result: the × is the one deliberate way out.
      allowClose: true,
      overlayClickBehavior: () => {},
      allowKeyboardControl: false,
      onNextClick: () => goStep(1),
      onPrevClick: () => goStep(-1),
      onDestroyed: (_el, _step, { state }) => {
        try {
          localStorage.setItem(DONE_KEY, "done");
        } catch {
          /* private mode — the tour will simply offer itself again */
        }
        const finished = state.activeIndex === STEPS.length - 1;
        driverRef.current = null;
        if (finished) {
          // The finale lives on the chat tab — drop the user straight
          // into the composer so "Start asking" means it.
          onTabChangeRef.current("chat");
          window.setTimeout(() => {
            window.dispatchEvent(new CustomEvent("pivot:focus-composer"));
          }, 250);
        }
      },
      steps: STEPS.map((s, i) => ({
        element: s.element,
        popover: {
          title: s.title,
          description: s.description,
          side: s.side,
          align: s.align ?? "center",
          // Close on every step — leaving is always one deliberate click away.
          // The welcome card has nothing to go back to, so it drops "previous".
          showButtons:
            i === 0 ? ["next", "close"] : ["next", "previous", "close"],
          ...(i === 0 ? { nextBtnText: "Show me around" } : null),
        },
      })),
    });
    driverRef.current = d;

    // The tour always begins on chat; if the user re-runs it from another
    // tab, walk them home first.
    if (tabRef.current !== "chat") {
      onTabChangeRef.current("chat");
      void waitForElement('[data-testid="chat-composer"]').then(() => d.drive());
    } else {
      d.drive();
    }
  }, []);

  // Auto-start exactly once, immediately after a NEW account signs up.
  // The signup page sets localStorage[PENDING_KEY]; here we consume it
  // (one-shot) and start the tour. Existing users who sign IN never see
  // the auto-start (the login page does not set PENDING_KEY); they can
  // always replay from Help → "Replay the tour".
  useEffect(() => {
    if (!enabled) return;
    // Read and immediately consume the pending flag.
    let pending = false;
    try {
      pending = localStorage.getItem(PENDING_KEY) === "1";
      if (pending) localStorage.removeItem(PENDING_KEY);
    } catch {
      pending = false;
    }
    if (!pending) return;
    if (window.innerWidth < MIN_VIEWPORT) return;

    let timer = 0;
    const arm = (): void => {
      timer = window.setTimeout(start, 1200);
    };
    // If the brand intro is currently owning the screen (set by
    // LoginIntroGate, covering its white lead-in too), defer until it
    // signals completion; otherwise arm now.
    if (window.__pivotIntroPending) {
      window.addEventListener("pivot:intro-done", arm, { once: true });
    } else {
      arm();
    }
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("pivot:intro-done", arm);
    };
  }, [enabled, start]);

  // Replay hook (account menu → Help → "Replay the tour").
  useEffect(() => {
    const onStart = (): void => start();
    window.addEventListener(START_TOUR_EVENT, onStart);
    return () => window.removeEventListener(START_TOUR_EVENT, onStart);
  }, [start]);

  // Never leave a dead overlay behind on unmount / route change.
  useEffect(() => {
    return () => {
      driverRef.current?.destroy();
      driverRef.current = null;
    };
  }, []);

  return null;
}

export default ProductTour;
