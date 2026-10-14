"use client";

/**
 * ProductTour — the six-step first-run guide, on driver.js (MIT).
 *
 *   Home       Welcome, then the ask bar.
 *   Workspace  The chart, the Widgets button, the chat beside the chart.
 *   The rest   One card over the nav rail: Chat, Portfolio, Strategy, Screener.
 *
 * Look: liquid-glass cards with the ask bar's own colour wash, slowly moving
 * (product-tour.css). The page behind stays visible: there is no dark
 * overlay. The step's target is outlined by a glowing ring instead.
 *
 * The Workspace lives in a same-origin iframe (/chart-app), which driver.js
 * cannot reach into. So every target, inside the frame or not, is outlined by
 * one RING element in this document, placed over the target's rectangle and
 * kept there while the tour runs. driver.js anchors its popover to the ring.
 *
 * Starts once, right after sign-up (TOUR_PENDING_KEY, set by the signup page),
 * and on demand from the account menu ("Take the tour" → START_TOUR_EVENT).
 * Closing it at any point ends it; it never comes back by itself.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { driver, type Driver } from "driver.js";
import "driver.js/dist/driver.css";
import "./product-tour.css";
import { LAUNCH } from "@/lib/launch";
import { Welcome } from "./Welcome";
import {
  Blocks, ChartCandlestick, Compass, House, MessageSquareText, MessagesSquare, X,
  type LucideIcon,
} from "lucide-react";

type TabKey = "home" | "markets" | "chat" | "portfolio" | "agents" | "screener" | "brokers" | "chart";

/** Bump when the flow changes enough that people should see it again. */
const DONE_KEY = "pivot-tour-v2";
export const TOUR_PENDING_KEY = "pivot:tour-pending";
export const START_TOUR_EVENT = "pivot:start-tour";
/** Opens the welcome cards again (they lead into the tour). */
export const START_WELCOME_EVENT = "pivot:start-welcome";
/** Below this width the rail and the workspace are laid out for phones. */
const MIN_VIEWPORT = 1024;
const FRAME = 'iframe[src*="/chart-app/"]';
let running = false;

/** A target: a selector in this page, or one inside the chart frame. */
type Target = { page: string } | { frame: string };

interface Step {
  tab: TabKey;
  target?: Target;
  icon: LucideIcon;
  title: string;
  text: string;
  side?: "top" | "right" | "bottom" | "left";
  align?: "start" | "center" | "end";
  /** Runs before the step shows, e.g. opening the chat panel. */
  prepare?: () => Promise<void>;
  /** For a large target: attach the card to a point on it instead of its
   *  outside edge — its centre, or the middle of its left edge. */
  pin?: "center" | "left";
}

const frameDoc = (): Document | null => {
  const f = document.querySelector<HTMLIFrameElement>(FRAME);
  try { return f?.contentDocument ?? null; } catch { return null; }
};

const sleep = (ms: number) => new Promise<void>((r) => window.setTimeout(r, ms));

/** Open the workspace chat if it is closed, so the step can point at it. */
async function openWorkspaceChat(): Promise<void> {
  const d = frameDoc();
  const panel = d?.getElementById("chatPanel");
  if (panel && panel.classList.contains("hidden")) {
    d?.getElementById("chatToggle")?.click();
    await sleep(320);
  }
}

const STEPS: Step[] = [
  {
    tab: "home",
    icon: House,
    title: "This is Home",
    text: "The market, your watchlist and your portfolio at a glance.",
  },
  {
    tab: "home",
    target: { page: ".copilot-quick-ask" },
    side: "top",
    icon: MessageSquareText,
    title: "Ask anything",
    text: "Type a question about any stock, index or commodity. Answers use live market data.",
  },
  {
    tab: "chart",
    target: { frame: "#stage" },
    pin: "center",
    side: "bottom",
    align: "center",
    icon: ChartCandlestick,
    title: "Your workspace",
    text: "A live chart you can draw on, with tools on the left. It saves as you work.",
  },
  {
    tab: "chart",
    target: { frame: "#dockBtn" },
    side: "bottom",
    align: "end",
    icon: Blocks,
    title: "Make it yours",
    text: "Add a watchlist, news, option chain, notes and more. Drag widgets to arrange them.",
  },
  {
    tab: "chart",
    target: { frame: "#chatPanel" },
    pin: "left",
    side: "left",
    align: "center",
    icon: MessagesSquare,
    title: "Chat with your chart",
    text: "Ask about what you see. Pivot answers and marks the levels on the chart.",
    prepare: openWorkspaceChat,
  },
  {
    tab: "chart",
    target: { page: '[data-tour="nav"]' },
    side: "right",
    align: "start",
    icon: Compass,
    title: "The rest of Pivot",
    text: LAUNCH.mainChat && LAUNCH.strategies
      ? "Chat for longer questions, Portfolio for paper trading, Strategy to test ideas and Screener to filter the market."
      : "Portfolio for paper trading and Screener to filter the market. Ask Pivot from the bar on any page.",
  },
];

const ICONS = STEPS.map((s) =>
  renderToStaticMarkup(<s.icon size={18} strokeWidth={1.75} aria-hidden />));
const CLOSE_ICON = renderToStaticMarkup(<X size={15} strokeWidth={2} aria-hidden />);

/* Liquid glass: the card bends what is behind it near its edges, like a lens.
   An SVG displacement map does the bending — red shifts pixels sideways,
   green up and down, and the flat grey middle leaves them alone. Only
   Chromium applies an SVG filter to a backdrop, so other browsers keep the
   frosted glass without the bend. */
const LENS_ID = "pivot-tour-lens";
const LENS_EDGE = 22;

function canBend(): boolean {
  const brands = (navigator as Navigator & { userAgentData?: { brands?: { brand: string }[] } })
    .userAgentData?.brands;
  return !!brands?.some((b) => b.brand === "Chromium");
}

function lensMap(w: number, h: number, radius: number): string {
  const e = LENS_EDGE;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}">`
    + `<defs><linearGradient id="x" x2="1"><stop offset="0" stop-color="#000"/><stop offset="1" stop-color="#f00"/></linearGradient>`
    + `<linearGradient id="y" x2="0" y2="1"><stop offset="0" stop-color="#000"/><stop offset="1" stop-color="#0f0"/></linearGradient>`
    + `<filter id="b"><feGaussianBlur stdDeviation="${e / 2}"/></filter></defs>`
    + `<rect width="${w}" height="${h}" fill="url(#x)"/>`
    + `<rect width="${w}" height="${h}" fill="url(#y)" style="mix-blend-mode:screen"/>`
    + `<rect x="${e}" y="${e}" width="${w - 2 * e}" height="${h - 2 * e}" rx="${Math.max(0, radius - e / 2)}" fill="#808080" filter="url(#b)"/></svg>`;
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

function mountLens(): SVGFEImageElement {
  const ns = "http://www.w3.org/2000/svg";
  let host = (document.getElementById(LENS_ID) as SVGFilterElement | null)?.ownerSVGElement;
  if (!host) {
    host = document.createElementNS(ns, "svg");
    host.setAttribute("aria-hidden", "true");
    host.setAttribute("width", "0");
    host.setAttribute("height", "0");
    host.style.position = "absolute";
    host.innerHTML = `<filter id="${LENS_ID}" x="0" y="0" width="100%" height="100%" color-interpolation-filters="sRGB">`
      + `<feImage x="0" y="0" preserveAspectRatio="none" result="map"/>`
      + `<feDisplacementMap in="SourceGraphic" in2="map" scale="-60" xChannelSelector="R" yChannelSelector="G"/></filter>`;
    document.body.appendChild(host);
  }
  return host.querySelector("feImage") as SVGFEImageElement;
}

function resolve(target: Target): Element | null {
  if ("page" in target) {
    const el = document.querySelector(target.page);
    return el && el.getBoundingClientRect().width > 0 ? el : null;
  }
  return frameDoc()?.querySelector(target.frame) ?? null;
}

/** The target's rectangle in THIS page's coordinates, and its corner radius.
 *  A frame target counts only once the chart has loaded: the frame stays
 *  hidden until then, and its elements already have sizes. */
function rectOf(target: Target): { r: DOMRect; radius: number } | null {
  const el = resolve(target);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  if (!r.width || !r.height) return null;
  const radius = parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0;
  if ("page" in target) return { r, radius };
  const frame = document.querySelector<HTMLIFrameElement>(FRAME)!;
  if (getComputedStyle(frame).visibility !== "visible") return null;
  const f = frame.getBoundingClientRect();
  return { r: new DOMRect(r.left + f.left, r.top + f.top, r.width, r.height), radius };
}

/** Resolve once the target exists and has a size, or give up quietly. */
async function waitFor(target: Target | undefined, timeoutMs = 12000): Promise<boolean> {
  if (!target) return true;
  const started = performance.now();
  while (performance.now() - started < timeoutMs) {
    if (rectOf(target)) return true;
    await sleep(80);
  }
  return false;
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
}): React.ReactElement | null {
  const tabRef = useRef(activeTab);
  tabRef.current = activeTab;
  const tabChangeRef = useRef(onTabChange);
  tabChangeRef.current = onTabChange;
  const driverRef = useRef<Driver | null>(null);

  const start = useCallback((): void => {
    // One tour at a time: it takes a moment to reach the first step, and a
    // second start in that moment would run two tours on top of each other.
    if (running || window.innerWidth < MIN_VIEWPORT) return;
    running = true;

    // The ring: one element that outlines whatever the step points at.
    const ring = document.createElement("div");
    ring.className = "pivot-tour-ring";
    ring.setAttribute("aria-hidden", "true");
    document.body.appendChild(ring);
    // A point the card attaches to when it should sit over a large target.
    const anchor = document.createElement("div");
    anchor.className = "pivot-tour-anchor";
    anchor.setAttribute("aria-hidden", "true");
    document.body.appendChild(anchor);
    let current: Target | undefined;
    let pinLeft = false;
    const lens = canBend() ? mountLens() : null;
    let raf = 0;
    const place = (): void => {
      const hit = current ? rectOf(current) : null;
      if (!hit) { ring.style.opacity = "0"; return; }
      const { r } = hit;
      const pad = 6;
      Object.assign(ring.style, {
        opacity: "1",
        borderRadius: `${Math.min(hit.radius + pad, (r.height + pad * 2) / 2)}px`,
        left: `${r.left - pad}px`, top: `${r.top - pad}px`,
        width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px`,
      });
      Object.assign(anchor.style, {
        left: `${pinLeft ? r.left - pad : r.left + r.width / 2}px`,
        top: `${r.top + r.height * (pinLeft ? 0.5 : 0.42)}px`,
      });
    };
    const follow = (): void => { place(); raf = requestAnimationFrame(follow); };
    raf = requestAnimationFrame(follow);
    const onResize = (): void => { place(); driverRef.current?.refresh(); };
    window.addEventListener("resize", onResize);

    /** Get to step `index`: switch tab, prepare, wait for the target. */
    const arrive = async (index: number): Promise<void> => {
      const step = STEPS[index];
      if (!step) return;
      if (step.tab !== tabRef.current) tabChangeRef.current(step.tab);
      await waitFor(step.target);
      if (step.prepare) await step.prepare();
      current = step.target;
      pinLeft = step.pin === "left";
      place();
    };

    let moving = false;
    const go = (dir: 1 | -1): void => {
      const d = driverRef.current;
      if (!d || moving) return;            // a second click while switching tabs
      const index = ((d.getState("activeIndex") as number | undefined) ?? 0) + dir;
      if (index < 0) return;
      if (index >= STEPS.length) { d.destroy(); return; }
      moving = true;
      void arrive(index).then(() => {
        moving = false;
        if (driverRef.current !== d) return;
        if (dir === 1) d.moveNext();
        else d.movePrevious();
      });
    };

    const d = driver({
      animate: true,
      smoothScroll: false,
      overlayOpacity: 0,           // the page stays in view; the ring does the pointing
      stagePadding: 6,
      stageRadius: 14,
      popoverClass: "pivot-tour",
      popoverOffset: 14,
      showProgress: false,
      nextBtnText: "Next",
      prevBtnText: "Back",
      doneBtnText: "Start exploring",
      disableActiveInteraction: true,
      allowClose: true,
      overlayClickBehavior: () => {},
      allowKeyboardControl: true,
      onNextClick: () => go(1),
      onPrevClick: () => go(-1),
      onPopoverRender: (popover, { state }) => {
        const i = state.activeIndex ?? 0;
        const tile = document.createElement("div");
        tile.className = "pivot-tour-icon";
        tile.innerHTML = ICONS[i] ?? "";
        popover.wrapper.insertBefore(tile, popover.title);
        const dots = document.createElement("div");
        dots.className = "pivot-tour-dots";
        dots.setAttribute("aria-label", `Step ${i + 1} of ${STEPS.length}`);
        dots.innerHTML = STEPS.map((_, n) => `<i class="${n === i ? "on" : ""}"></i>`).join("");
        popover.footer.insertBefore(dots, popover.footer.firstChild);
        popover.closeButton.innerHTML = CLOSE_ICON;
        popover.closeButton.setAttribute("aria-label", "Close the tour");
        // driver.js focuses the first button, the close; Enter should move on.
        requestAnimationFrame(() => popover.nextButton.focus({ preventScroll: true }));
        if (lens) {
          const card = popover.wrapper;
          requestAnimationFrame(() => {
            const w = Math.round(card.offsetWidth), h = Math.round(card.offsetHeight);
            const radius = parseFloat(getComputedStyle(card).borderTopLeftRadius) || 0;
            lens.setAttribute("width", String(w));
            lens.setAttribute("height", String(h));
            lens.setAttribute("href", lensMap(w, h, radius));
            card.classList.add("is-lens");
          });
        }
      },
      onDestroyed: (_el, _step, { state }) => {
        try { localStorage.setItem(DONE_KEY, "done"); } catch { /* private mode */ }
        cancelAnimationFrame(raf);
        window.removeEventListener("resize", onResize);
        ring.remove();
        anchor.remove();
        (document.getElementById(LENS_ID) as SVGFilterElement | null)?.ownerSVGElement?.remove();
        driverRef.current = null;
        running = false;
        if (state.activeIndex === STEPS.length - 1) {
          window.setTimeout(() => window.dispatchEvent(new CustomEvent("pivot:focus-composer")), 250);
        }
      },
      steps: STEPS.map((s, i) => ({
        element: s.target ? () => (s.pin ? anchor : ring) : undefined,
        popover: {
          title: s.title,
          description: s.text,
          side: s.side,
          align: s.align ?? "center",
          showButtons: i === 0 ? ["next", "close"] : ["next", "previous", "close"],
          ...(i === 0 ? { nextBtnText: "Show me" } : null),
        },
      })),
    });
    driverRef.current = d;
    void arrive(0).then(() => d.drive());
  }, []);

  // Once, right after a new account is created: the welcome cards, then the tour.
  const [welcome, setWelcome] = useState(false);
  const afterWelcome = useCallback((tour: boolean): void => {
    // Clear the flag only when the welcome is answered. Clearing it on mount
    // lost the whole flow whenever the shell mounted twice: the first mount
    // took the flag and was torn down before it showed anything.
    try { localStorage.removeItem(TOUR_PENDING_KEY); } catch { /* private mode */ }
    setWelcome(false);
    if (tour && window.innerWidth >= MIN_VIEWPORT) window.setTimeout(start, 450);
  }, [start]);

  useEffect(() => {
    if (!enabled) return;
    let pending = false;
    try { pending = localStorage.getItem(TOUR_PENDING_KEY) === "1"; } catch { pending = false; }
    if (!pending) return;
    let timer = 0;
    const arm = (): void => { timer = window.setTimeout(() => setWelcome(true), 600); };
    if (window.__pivotIntroPending) window.addEventListener("pivot:intro-done", arm, { once: true });
    else arm();
    return () => { window.clearTimeout(timer); window.removeEventListener("pivot:intro-done", arm); };
  }, [enabled, start]);

  // On demand, from the account menu.
  useEffect(() => {
    const onStart = (): void => start();
    const onWelcome = (): void => setWelcome(true);
    window.addEventListener(START_TOUR_EVENT, onStart);
    window.addEventListener(START_WELCOME_EVENT, onWelcome);
    return () => {
      window.removeEventListener(START_TOUR_EVENT, onStart);
      window.removeEventListener(START_WELCOME_EVENT, onWelcome);
    };
  }, [start]);

  useEffect(() => () => { driverRef.current?.destroy(); driverRef.current = null; }, []);

  return welcome ? <Welcome onDone={afterWelcome} /> : null;
}

export default ProductTour;
