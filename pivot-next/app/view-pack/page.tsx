"use client";

/**
 * /view-pack — a standalone showcase for View Pack 01 (8 fresh curated views),
 * rendered through the REAL Views components (ViewCard + ViewDetailPage) so it is
 * pixel-identical to the live Views tab. Data is static (computed offline) and
 * fed via ViewDetailPage's detailOverride, so no /api/views call is made. The
 * three DB-backed curated views are untouched.
 *
 * The root layout locks `html, body { overflow: hidden }`; this is a long-scroll
 * page, so we release that lock on mount and restore it on unmount.
 */

import * as React from "react";
import { ViewCard } from "@/components/views/ViewCard";
import { ViewDetailPage } from "@/components/views/ViewDetailPage";
import type { ViewSummary, ViewDetail, StanceIntent } from "@/lib/types";
import summariesRaw from "@/components/views/pack/viewpack01.summaries.json";
import detailsRaw from "@/components/views/pack/viewpack01.details.json";

const SUMMARIES = summariesRaw as unknown as ViewSummary[];
const DETAILS = detailsRaw as unknown as Record<string, ViewDetail>;

const FONT = "var(--font-display)";

export default function ViewPackPage(): React.ReactElement {
  const [openId, setOpenId] = React.useState<string | null>(null);
  const [openStance, setOpenStance] = React.useState<StanceIntent | null>(null);
  const detail = openId ? (DETAILS[openId] ?? null) : null;

  const openView = React.useCallback(
    (id: string, intent?: StanceIntent): void => {
      setOpenStance(intent ?? null);
      setOpenId(id);
    },
    [],
  );

  React.useEffect(() => {
    const html = document.documentElement;
    const body = document.body;
    const prevHtml = html.style.overflow;
    const prevBody = body.style.overflow;
    html.style.overflow = "auto";
    body.style.overflow = "auto";
    return () => {
      html.style.overflow = prevHtml;
      body.style.overflow = prevBody;
    };
  }, []);

  return (
    <main
      style={{
        background: "var(--bg-base)",
        minHeight: "100vh",
        height: "100vh",
        overflowY: "auto",
      }}
    >
      <div style={{ maxWidth: 1200, margin: "0 auto", padding: "40px 28px 80px" }}>
        {detail ? (
          <ViewDetailPage
            viewId={openId!}
            detailOverride={detail}
            initialStance={openStance}
            onBack={() => {
              setOpenId(null);
              setOpenStance(null);
            }}
            onOpenWorkflowById={() => {}}
          />
        ) : (
          <>
            <div style={{ marginBottom: 28 }}>
              <div
                style={{
                  fontFamily: FONT,
                  fontSize: 13,
                  fontWeight: 500,
                  color: "var(--text-tertiary)",
                  marginBottom: 4,
                }}
              >
                View Pack 01
              </div>
              <h1
                style={{
                  fontFamily: FONT,
                  fontSize: 30,
                  fontWeight: 600,
                  letterSpacing: "-0.02em",
                  color: "var(--text-primary)",
                  margin: "0 0 8px",
                }}
              >
                Views
              </h1>
              <p
                style={{
                  fontFamily: FONT,
                  fontSize: 15,
                  color: "var(--text-secondary)",
                  margin: 0,
                  maxWidth: 720,
                  lineHeight: 1.5,
                }}
              >
                Beliefs, expressed as deployable strategies — with the return each one has paid.
              </p>
            </div>
            <div
              className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 items-stretch"
              style={{ gap: 20 }}
            >
              {SUMMARIES.map((v) => (
                <ViewCard key={v.id} view={v} onOpen={openView} />
              ))}
            </div>
          </>
        )}
      </div>
    </main>
  );
}
