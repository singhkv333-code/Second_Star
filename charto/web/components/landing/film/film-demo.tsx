/**
 * film-demo — the entire choreography. One `useGSAP` hook, one master timeline.
 *
 * Structure follows the four-beat loop: for each prompt the cursor travels to
 * the composer, types, clicks send, the agent thinks briefly, the chart is
 * annotated, the answer lands, the result holds, and everything clears for the
 * next prompt. Labels are named for what the USER does (`s0Type`, `s0Send`),
 * and every tween is positioned relative to a label — so re-timing one beat
 * shifts the rest automatically instead of breaking absolute offsets.
 *
 * The cursor is presentation only: it never receives real pointer events, and
 * nothing inside the frame is interactive.
 */
"use client";

import * as React from "react";
import gsap from "gsap";
import { useGSAP } from "@gsap/react";
import { FilmChart, type ChartMap } from "./film-chart";
import type { Annotation } from "./film-script";
import {
  FilmAnnotations,
  FilmCursor,
  FilmProgress,
  FilmReadout,
  FilmSidebar,
  FilmTopbar,
} from "./film-primitives";
import {
  FILM_H,
  FILM_H_NARROW,
  FILM_W,
  FILM_W_NARROW,
  NARROW_AT,
  SCENES,
  TOPBAR_H,
} from "./film-script";

gsap.registerPlugin(useGSAP);

function usePrefersReducedMotion() {
  const [reduced, setReduced] = React.useState(false);
  React.useEffect(() => {
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const on = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

export function ProductFilm() {
  const rootRef = React.useRef<HTMLDivElement | null>(null);
  const outerRef = React.useRef<HTMLDivElement | null>(null);
  const [scale, setScale] = React.useState<number | null>(null);
  const [narrow, setNarrow] = React.useState(false);
  const [map, setMap] = React.useState<ChartMap | null>(null);
  const reduced = usePrefersReducedMotion();

  const designW = narrow ? FILM_W_NARROW : FILM_W;
  const designH = narrow ? FILM_H_NARROW : FILM_H;

  // The frame lays out at the design size and scales down to fit. Everything
  // inside — including the chart's own coordinates — stays in design pixels, so
  // cursor measurement only ever divides by this one number.
  //
  // The width guard matters: setting a style from inside a ResizeObserver can
  // re-trigger the observer, and without comparing against the previous width
  // that becomes a feedback loop.
  React.useLayoutEffect(() => {
    const el = outerRef.current;
    if (!el) return;
    let prevW = 0;
    const update = () => {
      const w = el.clientWidth;
      if (w === prevW || w <= 0) return;
      prevW = w;
      const isNarrow = w <= NARROW_AT;
      setNarrow(isNarrow);
      setScale(Math.min(w / (isNarrow ? FILM_W_NARROW : FILM_W), 1));
    };
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useGSAP(
    () => {
      const root = rootRef.current;
      // Reduced motion means NO timeline at all — not a timeline racing the
      // static state. The resting state is set by the effect below.
      if (!root || !map || scale == null || reduced) return;
      const q = gsap.utils.selector(root);

      const cursor = q("[data-film-cursor]")[0] as HTMLElement | undefined;
      const ripple = q("[data-film-ripple]")[0] as HTMLElement | undefined;
      const composer = q("[data-film-composer]")[0] as HTMLElement | undefined;
      const sendBtn = q("[data-film-send]")[0] as HTMLElement | undefined;
      const typed = q("[data-film-typed]")[0] as HTMLElement | undefined;
      const caret = q("[data-film-caret]")[0] as HTMLElement | undefined;
      const placeholder = q("[data-film-placeholder]")[0] as HTMLElement | undefined;
      const greeting = q("[data-film-greeting]")[0] as HTMLElement | undefined;
      const userMsg = q("[data-film-user-msg]")[0] as HTMLElement | undefined;
      const userText = q("[data-film-user-text]")[0] as HTMLElement | undefined;
      const thinking = q("[data-film-thinking]")[0] as HTMLElement | undefined;
      const answer = q("[data-film-answer]")[0] as HTMLElement | undefined;
      const answerText = q("[data-film-answer-text]")[0] as HTMLElement | undefined;
      const tagRow = q("[data-film-tags]")[0] as HTMLElement | undefined;
      // The header's eraser only exists in the product while the chat has
      // drawn something, so it fades in with the marks and out with them.
      const eraser = q("[data-film-eraser]")[0] as HTMLElement | undefined;
      if (!cursor || !composer || !sendBtn || !typed || !userMsg || !answer) return;

      // ── measurement ──────────────────────────────────────────────────────
      // Never hardcode a cursor coordinate. Every target is read off the real
      // element and divided by the frame's scale, so the pointer lands on the
      // send button at 1200px and at 380px alike.
      const frame = root.getBoundingClientRect();
      const s = scale || 1;
      const measure = (el: Element) => {
        const r = el.getBoundingClientRect();
        return {
          x: Math.round((r.left + r.width / 2 - frame.left) / s),
          y: Math.round((r.top + r.height / 2 - frame.top) / s),
        };
      };
      const pComposer = measure(composer);
      const pSend = measure(sendBtn);
      // Rest points that keep the hand alive without stealing attention.
      const pChart = { x: Math.round(designW * 0.34), y: Math.round(designH * (narrow ? 0.2 : 0.46)) };
      const offscreen = { x: Math.round(designW * 0.62), y: designH + 90 };

      const counter = { v: 0 };

      const setTags = (tags: string[]) => {
        if (!tagRow) return;
        tagRow.textContent = "";
        tags.forEach((t) => {
          const el = document.createElement("span");
          el.textContent = t;
          tagRow.appendChild(el);
        });
      };

      // The scene counter sits UNDER the frame, i.e. outside the GSAP scope, so
      // the scoped selector cannot see it — `q` returned an empty list and the
      // dots never lit. Read them off the outer container instead.
      const dots = Array.from(
        outerRef.current?.querySelectorAll("[data-film-dot]") ?? [],
      );
      const setDots = (active: number) => {
        dots.forEach((d) => {
          d.classList.toggle("on", Number(d.getAttribute("data-film-dot")) === active);
        });
      };

      const resetFilm = () => {
        gsap.set(cursor, { x: offscreen.x, y: offscreen.y, scale: 1, autoAlpha: 1 });
        gsap.set(ripple ?? [], { scale: 0.2, autoAlpha: 0 });
        gsap.set(typed, { autoAlpha: 1 });
        typed.textContent = "";
        counter.v = 0;
        gsap.set(caret ?? [], { autoAlpha: 0 });
        gsap.set(placeholder ?? [], { autoAlpha: 1 });
        gsap.set(greeting ?? [], { autoAlpha: 1, y: 0 });
        gsap.set([userMsg, thinking ?? [], answer].flat(), { autoAlpha: 0, y: 8 });
        gsap.set(eraser ?? [], { autoAlpha: 0 });
        gsap.set(q("[data-film-anno]"), { autoAlpha: 0 });
        gsap.set(q("[data-film-anno-body]"), { scaleY: 0, transformOrigin: "50% 50%" });
        gsap.set(q("[data-film-anno-label]"), { autoAlpha: 0, x: "+=0" });
        gsap.set(q("[data-film-anno-detail]"), { autoAlpha: 0 });
        q("[data-film-anno-draw]").forEach((el) => {
          const dashed = el.getAttribute("data-film-dashed") === "1";
          const len = Number(el.getAttribute("data-film-len") ?? 0);
          if (!dashed && len) gsap.set(el, { strokeDashoffset: len });
          else gsap.set(el, { autoAlpha: 0 });
        });
        setDots(0);
      };

      const tl = gsap.timeline({
        repeat: -1,
        repeatDelay: 0,
        defaults: { ease: "sine.out" },
      });

      tl.add(resetFilm, 0);

      /** Cursor squeeze + ripple burst. Both, always — a ripple alone reads
       *  as a UI glitch, and a squeeze alone reads as nothing. */
      const click = (at: { x: number; y: number }, label: string) => {
        if (ripple) {
          tl.set(ripple, { x: at.x, y: at.y, scale: 0.2, autoAlpha: 0.5 }, label).to(
            ripple,
            { scale: 3.2, autoAlpha: 0, duration: 0.5, ease: "power2.out" },
            label,
          );
        }
        tl.to(cursor, { scale: 0.88, duration: 0.08, ease: "power2.out" }, label).to(
          cursor,
          { scale: 1, duration: 0.16, ease: "back.out(2.2)" },
          `${label}+=0.09`,
        );
      };

      SCENES.forEach((scene, i) => {
        const L = (n: string) => `s${i}${n}`;
        const first = i === 0;

        // — approach —
        tl.addLabel(L("Enter"), i === 0 ? ">" : ">");
        tl.add(() => {
          setDots(i);
          if (typed) typed.textContent = "";
          counter.v = 0;
        }, L("Enter"));
        tl.to(
          cursor,
          {
            x: pComposer.x,
            y: pComposer.y,
            duration: first ? 1.05 : 0.72,
            ease: "sine.inOut",
          },
          L("Enter"),
        );
        // Greeting only exists before the first question.
        if (first) {
          tl.to(greeting ?? [], { autoAlpha: 1, duration: 0.3 }, L("Enter"));
        } else {
          tl.to(greeting ?? [], { autoAlpha: 0, duration: 0.2 }, L("Enter"));
        }

        // — typing —
        tl.addLabel(L("Type"), `${L("Enter")}+=${first ? 0.9 : 0.6}`);
        tl.to(placeholder ?? [], { autoAlpha: 0, duration: 0.12 }, L("Type"));
        tl.to(caret ?? [], { autoAlpha: 1, duration: 0.1 }, L("Type"));

        // Three constant-speed segments at slightly different rates. A single
        // linear tween types like a machine; easing the counter makes letters
        // bunch and stutter. Segmenting gives cadence without either.
        const text = scene.prompt;
        const segs: [number, number][] = [
          [0.42, 0.03],
          [0.84, 0.024],
          [1, 0.032],
        ];
        let from = 0;
        segs.forEach(([toPct, perChar], si) => {
          const to = Math.round(text.length * toPct);
          tl.to(
            counter,
            {
              v: to,
              duration: Math.max(0.12, (to - from) * perChar),
              ease: "none",
              onUpdate: () => {
                typed.textContent = text.slice(0, Math.round(counter.v));
              },
            },
            si === 0 ? `${L("Type")}+=0.08` : ">",
          );
          from = to;
        });

        // — send —
        tl.addLabel(L("Send"), ">+=0.22");
        tl.to(
          cursor,
          { x: pSend.x, y: pSend.y, duration: 0.34, ease: "sine.inOut" },
          L("Send"),
        );
        click(pSend, `${L("Send")}+=0.3`);
        tl.to(sendBtn, { scale: 0.9, duration: 0.09, ease: "power2.out" }, `${L("Send")}+=0.3`);
        tl.to(sendBtn, { scale: 1, duration: 0.18, ease: "back.out(2)" }, `${L("Send")}+=0.39`);

        // Composer empties, the turn appears in the thread.
        tl.add(() => {
          typed.textContent = "";
          counter.v = 0;
          if (userText) userText.textContent = scene.prompt;
        }, `${L("Send")}+=0.36`);
        tl.to(caret ?? [], { autoAlpha: 0, duration: 0.1 }, `${L("Send")}+=0.36`);
        tl.to(placeholder ?? [], { autoAlpha: 1, duration: 0.2 }, `${L("Send")}+=0.42`);
        tl.to(
          userMsg,
          { autoAlpha: 1, y: 0, duration: 0.34, ease: "sine.out" },
          `${L("Send")}+=0.38`,
        );

        // — thinking — short on purpose: this is a demo, not a wait.
        tl.addLabel(L("Think"), `${L("Send")}+=0.56`);
        tl.to(
          thinking ?? [],
          { autoAlpha: 1, y: 0, duration: 0.22 },
          L("Think"),
        );
        // Cursor eases off the send button while the agent works.
        tl.to(
          cursor,
          { x: pSend.x - 26, y: pSend.y + 18, duration: 0.5, ease: "sine.inOut" },
          L("Think"),
        );

        // — the payoff —
        tl.addLabel(L("Draw"), `${L("Think")}+=${scene.thinkMs / 1000}`);
        tl.to(thinking ?? [], { autoAlpha: 0, duration: 0.18, ease: "sine.in" }, L("Draw"));

        // Selectors are built per annotation KIND. A blanket list would ask
        // GSAP for `[data-film-anno-body]` inside a marker (which has none) and
        // it warns about every empty target.
        const sel = (suffix: string, kinds: Annotation["kind"][]) =>
          scene.annotations
            .filter((a) => kinds.includes(a.kind))
            .map((a) => `[data-film-anno="${scene.id}-${a.id}"] ${suffix}`);

        if (scene.annotations.length) {
          const groups = scene.annotations.map(
            (a) => `[data-film-anno="${scene.id}-${a.id}"]`,
          );
          tl.to(groups, { autoAlpha: 1, duration: 0.2 }, L("Draw"));
          tl.to(eraser ?? [], { autoAlpha: 1, duration: 0.28 }, `${L("Draw")}+=0.12`);

          // Zones grow out of their own mid-line; lines walk their stroke on;
          // markers rise from the axis. No bounce anywhere — these are
          // measurements appearing, not objects landing.
          if (sel("[data-film-anno-body]", ["zone"]).length)
          tl.to(
            sel("[data-film-anno-body]", ["zone"]),
            { scaleY: 1, duration: 0.52, stagger: 0.09, ease: "power2.out" },
            `${L("Draw")}+=0.06`,
          );
          if (sel("[data-film-anno-draw]", ["line", "marker"]).length)
          tl.to(
            sel("[data-film-anno-draw]", ["line", "marker"]),
            {
              strokeDashoffset: 0,
              autoAlpha: 1,
              duration: 0.6,
              stagger: 0.1,
              ease: "power2.inOut",
            },
            `${L("Draw")}+=0.08`,
          );
          tl.to(
            sel("[data-film-anno-label]", ["zone", "line", "marker"]),
            { autoAlpha: 1, duration: 0.28, stagger: 0.08 },
            `${L("Draw")}+=0.34`,
          );
          if (sel("[data-film-anno-detail]", ["marker"]).length)
          tl.to(
            sel("[data-film-anno-detail]", ["marker"]),
            { autoAlpha: 1, duration: 0.28, stagger: 0.08 },
            `${L("Draw")}+=0.46`,
          );
          // The hand drifts toward what it just drew.
          tl.to(
            cursor,
            { x: pChart.x, y: pChart.y, duration: 0.8, ease: "sine.inOut" },
            `${L("Draw")}+=0.1`,
          );
        } else {
          // Nothing is drawn for "why is the price falling" — the cursor still
          // moves, so the empty chart reads as an answer rather than a failure.
          tl.to(
            cursor,
            { x: pChart.x + 90, y: pChart.y, duration: 0.7, ease: "sine.inOut" },
            `${L("Draw")}+=0.05`,
          );
        }

        // — answer —
        tl.addLabel(L("Answer"), `${L("Draw")}+=${scene.annotations.length ? 0.5 : 0.16}`);
        tl.add(() => {
          if (answerText) answerText.textContent = scene.answer;
          setTags(scene.tags);
        }, L("Answer"));
        tl.to(answer, { autoAlpha: 1, y: 0, duration: 0.4, ease: "sine.out" }, L("Answer"));

        // — hold — long enough to read the answer and match it to the chart.
        tl.to({}, { duration: 2.1 }, `${L("Answer")}+=0.4`);

        // — clear —
        tl.addLabel(L("Out"), ">");
        const isLast = i === SCENES.length - 1;
        if (scene.annotations.length) {
          tl.to(
            scene.annotations.map((a) => `[data-film-anno="${scene.id}-${a.id}"]`),
            { autoAlpha: 0, duration: 0.32, ease: "sine.in" },
            L("Out"),
          );
          tl.to(eraser ?? [], { autoAlpha: 0, duration: 0.24 }, L("Out"));
        }
        tl.to([userMsg, answer], { autoAlpha: 0, y: 8, duration: 0.28, ease: "sine.in" }, L("Out"));
        tl.to(
          cursor,
          isLast
            ? { x: offscreen.x, y: offscreen.y, duration: 0.75, ease: "sine.in" }
            : { x: pComposer.x + 40, y: pComposer.y + 30, duration: 0.5, ease: "sine.inOut" },
          `${L("Out")}+=0.08`,
        );
        tl.to({}, { duration: isLast ? 0.55 : 0.3 });
      });

      // A 30s loop has no business running while nobody can see it — GSAP's
      // ticker keeps a rAF alive for the whole page otherwise. Pause off-screen,
      // resume on the way back in.
      const io = new IntersectionObserver(
        ([entry]) => {
          if (entry?.isIntersecting) tl.play();
          else tl.pause();
        },
        { threshold: 0.12 },
      );
      io.observe(root);

      return () => {
        io.disconnect();
        tl.kill();
      };
    },
    { scope: rootRef, dependencies: [map, scale, narrow, reduced], revertOnUpdate: true },
  );

  // Reduced motion: the resting state of the first scene, no timeline at all.
  React.useEffect(() => {
    const root = rootRef.current;
    if (!root || !reduced || !map) return;
    const q = gsap.utils.selector(root);
    const scene = SCENES[0]!;
    // Only the first scene's marks belong in the resting state.
    gsap.set(q("[data-film-anno]"), { autoAlpha: 0 });
    gsap.set(q("[data-film-cursor]"), { autoAlpha: 0 });
    gsap.set(q("[data-film-eraser]"), { autoAlpha: 1 });
    gsap.set(q("[data-film-ripple]"), { autoAlpha: 0 });
    gsap.set(q("[data-film-caret]"), { autoAlpha: 0 });
    gsap.set(q("[data-film-greeting]"), { autoAlpha: 0 });
    const typedEl = q("[data-film-typed]")[0];
    if (typedEl) typedEl.textContent = "";
    gsap.set(q("[data-film-placeholder]"), { autoAlpha: 1 });
    gsap.set(q("[data-film-thinking]"), { autoAlpha: 0 });
    gsap.set(q("[data-film-user-msg]"), { autoAlpha: 1, y: 0 });
    gsap.set(q("[data-film-answer]"), { autoAlpha: 1, y: 0 });
    const userText = q("[data-film-user-text]")[0];
    const answerText = q("[data-film-answer-text]")[0];
    if (userText) userText.textContent = scene.prompt;
    if (answerText) answerText.textContent = scene.answer;
    const tagRow = q("[data-film-tags]")[0];
    if (tagRow) {
      tagRow.textContent = "";
      scene.tags.forEach((t) => {
        const el = document.createElement("span");
        el.textContent = t;
        tagRow.appendChild(el);
      });
    }
    outerRef.current
      ?.querySelectorAll("[data-film-dot]")
      .forEach((d) => d.classList.toggle("on", d.getAttribute("data-film-dot") === "0"));
    scene.annotations.forEach((a) => {
      gsap.set(q(`[data-film-anno="${scene.id}-${a.id}"]`), { autoAlpha: 1 });
      gsap.set(q(`[data-film-anno="${scene.id}-${a.id}"] [data-film-anno-body]`), {
        scaleY: 1,
      });
      gsap.set(q(`[data-film-anno="${scene.id}-${a.id}"] [data-film-anno-draw]`), {
        strokeDashoffset: 0,
        autoAlpha: 1,
      });
      gsap.set(q(`[data-film-anno="${scene.id}-${a.id}"] [data-film-anno-label]`), {
        autoAlpha: 1,
      });
    });
  }, [reduced, map]);

  return (
    <div className="film-outer" ref={outerRef}>
      <div className="film-stage" style={{ aspectRatio: `${designW} / ${designH}` }}>
        {/* Gated on scale: the chart and the choreography both measure on
            mount, and mounting them before the frame's size is known has them
            measuring the raw container instead of the design frame. */}
        {scale != null && (
          <div
            className={`film-frame${narrow ? " is-narrow" : ""}`}
            ref={rootRef}
            style={{
              width: designW,
              height: designH,
              transform: `scale(${scale})`,
              transformOrigin: "top left",
            }}
          >
            <FilmTopbar narrow={narrow} />
            <div className="film-body" style={{ top: TOPBAR_H }}>
              <div className="film-chart-pane">
                <FilmChart onMap={setMap} narrow={narrow} />
                <FilmAnnotations scenes={SCENES} map={map} narrow={narrow} />
                <FilmReadout narrow={narrow} />
              </div>
              <FilmSidebar narrow={narrow} />
            </div>
            <FilmCursor />
          </div>
        )}
      </div>
      <FilmProgress count={SCENES.length} />
    </div>
  );
}
