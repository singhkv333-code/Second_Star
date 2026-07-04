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
  | "chat"
  | "portfolio"
  | "agents"
  | "calendar"
  | "screener"
  | "views";

/** Bump when the flow changes enough that existing users should see it again. */
const DONE_KEY = "pivot-tour-v1";
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
    element: ".composer-modes",
    side: "top",
    title: "When you'd rather point than type",
    description:
      "These pin down the intent of your next message: an <strong>automation</strong> that watches the market for you, a standing <strong>agent</strong>, a <strong>backtest</strong> over years of history, or a live <strong>option chain</strong>.",
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
      "Portfolio value and P&amp;L ride along at the top of every screen. You start in <strong>paper trading</strong> — a simulated book filled at real market prices — so ideas prove themselves before a single rupee moves. The full breakdown lives in the Portfolio tab.",
  },
  {
    tab: "chat",
    element: '[data-tour="nav"]',
    side: "right",
    align: "start",
    title: "The rest of the house",
    description:
      "Chat is home; these are the other rooms. Calendar tracks market events, Portfolio holds your book. The three worth a proper look come next — starting with Opinion Markets.",
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
      "Everything you build in chat reports here — price rules, weekly plans, option strategies, stock baskets — each one an agent you can pause, edit, or judge by its backtested curve. They keep watch so you don't have to keep the terminal open.",
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
          // The welcome card has nothing to go back to.
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

  // First visit → auto-start after the dashboard settles.
  useEffect(() => {
    if (!enabled) return;
    let seen: string | null = null;
    try {
      seen = localStorage.getItem(DONE_KEY);
    } catch {
      seen = "done";
    }
    if (seen) return;
    if (window.innerWidth < MIN_VIEWPORT) return;
    const id = window.setTimeout(start, 1200);
    return () => window.clearTimeout(id);
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
