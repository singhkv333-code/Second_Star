"use client";

import { useEffect } from "react";
import { PivotLanding } from "@/components/landing/PivotLanding";

export default function HomePage(): React.ReactElement {
  // The application shell locks document scrolling because its panes scroll
  // independently. The public landing page is one continuous document, so
  // hand scrolling back to the browser while this route is mounted.
  useEffect(() => {
    const html = document.documentElement;
    html.classList.add("pivot-landing-active");
    return () => html.classList.remove("pivot-landing-active");
  }, []);

  return <PivotLanding />;
}
