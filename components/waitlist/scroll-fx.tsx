"use client";

import { useEffect, useRef, useState } from "react";

/**
 * useInView — fires once when the element first enters the viewport.
 * Stays true after that (no toggle on scroll-up).
 */
function useInView<T extends HTMLElement>(rootMargin = "0px 0px -12% 0px"): {
  ref: React.RefObject<T | null>;
  inView: boolean;
} {
  const ref = useRef<T | null>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (
      typeof window !== "undefined" &&
      window.matchMedia &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches
    ) {
      setInView(true);
      return;
    }
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            setInView(true);
            obs.disconnect();
            break;
          }
        }
      },
      { rootMargin, threshold: 0.05 },
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [rootMargin]);

  return { ref, inView };
}

/**
 * Reveal — wraps children and fades + slides them up on first scroll-in.
 * Delay is in ms; useful for staggering adjacent Reveal blocks.
 */
export function Reveal({
  children,
  delay = 0,
  className,
  as: Tag = "div",
}: {
  children: React.ReactNode;
  delay?: number;
  className?: string;
  as?: "div" | "section";
}): React.ReactElement {
  const { ref, inView } = useInView<HTMLElement>();
  const Component = Tag as keyof React.JSX.IntrinsicElements;
  return (
    <Component
      ref={ref as React.Ref<HTMLElement> as never}
      className={className}
      style={{
        opacity: inView ? 1 : 0,
        transform: inView ? "translate3d(0,0,0)" : "translate3d(0,20px,0)",
        transition: `opacity 700ms cubic-bezier(0.22,1,0.36,1) ${delay}ms, transform 700ms cubic-bezier(0.22,1,0.36,1) ${delay}ms`,
        willChange: "opacity, transform",
      }}
    >
      {children}
    </Component>
  );
}
