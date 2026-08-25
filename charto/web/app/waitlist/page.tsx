"use client";

import { useEffect } from "react";
import { PivotLanding } from "@/components/landing/PivotLanding";

export default function WaitlistPage(): React.ReactElement {
  // The landing page is one long document, but globals.css locks `html, body`
  // to `height:100%; overflow:hidden` for the app shell (where each pane
  // scrolls internally). This class hands the scroll back to the document —
  // see the rule in PivotLanding.css. It has to be a class rather than the
  // inline `overflow:auto` that used to live here: setting it on BOTH html and
  // body left <body> scrolling inside a viewport-height <html>, which drew a
  // second scrollbar inset from the window edge.
  useEffect(() => {
    const html = document.documentElement;
    html.classList.add("pivot-landing-active");
    return () => html.classList.remove("pivot-landing-active");
  }, []);

  return <PivotLanding />;
}
