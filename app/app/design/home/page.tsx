"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { HomeWelcome } from "@/components/HomeWelcome";

/** Public design preview; no account or market data required. */
export default function HomeDesignPreview(): React.ReactElement {
  const router = useRouter();
  const [selectedPrompt, setSelectedPrompt] = useState("");
  return (
    <main className="h-screen overflow-y-auto" style={{ background: "var(--bg-inset)", padding: "32px clamp(16px, 4vw, 64px)", fontFamily: "var(--font-ui)" }}>
      <div className="home-dashboard" style={{ maxWidth: 1400, margin: "0 auto" }}>
        <HomeWelcome onOpenChart={() => router.push("/#chart")} onSendPrompt={setSelectedPrompt} />
        <span role="status" className="sr-only">{selectedPrompt ? `Preview question: ${selectedPrompt}` : ""}</span>
      </div>
    </main>
  );
}
