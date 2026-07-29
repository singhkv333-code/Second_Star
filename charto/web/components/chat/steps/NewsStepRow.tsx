"use client";

/**
 * NewsStepRow — renders a fetch.news or trigger.event workflow step
 * prettily inside WorkflowDraftCard and AgentPanel step list.
 *
 * Read-only. Editing config fields is Phase 2.
 */

import { Newspaper } from "lucide-react";
import { SourceLogo } from "@/components/news/SourceBadge";
import { ArticleCard } from "@/components/news/ArticleCard";
import type { NewsStepConfig, NewsRunOutput } from "@/lib/news-types";

// ---------------------------------------------------------------------------
// Default trusted sources shown when no allowlist is specified
// ---------------------------------------------------------------------------

const DEFAULT_SOURCES = [
  "Reuters",
  "Bloomberg",
  "The Hindu",
  "Business Standard",
  "Moneycontrol",
];

const MAX_VISIBLE_KEYWORDS = 6;
const MAX_VISIBLE_ARTICLES = 3;

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

type NewsStepRowProps = {
  step: {
    step_type: "fetch.news" | "trigger.event";
    config: NewsStepConfig;
    label?: string | null;
  };
  runOutput?: NewsRunOutput | null;
};

// ---------------------------------------------------------------------------
// NewsStepRow
// ---------------------------------------------------------------------------

export function NewsStepRow({ step, runOutput }: NewsStepRowProps): React.ReactElement {
  const { config } = step;
  const isEvent = step.step_type === "trigger.event";
  const stepLabel = isEvent ? "On news event" : "News watch";
  const minConf = config.min_confidence ?? 0.85;
  const confPct = Math.round(minConf * 100);

  const keywords = config.keywords ?? [];
  const visibleKeywords = keywords.slice(0, MAX_VISIBLE_KEYWORDS);
  const overflowCount = keywords.length - visibleKeywords.length;

  const sources = config.sources ?? [];
  const usingDefaultSources = sources.length === 0;
  const displaySources = usingDefaultSources ? DEFAULT_SOURCES : sources;

  const topArticles = runOutput?.articles?.slice(0, MAX_VISIBLE_ARTICLES) ?? [];
  const matched = runOutput?.matched ?? false;
  const maxConf = runOutput ? Math.round(runOutput.max_confidence * 100) : null;

  return (
    <div
      data-testid="news-step-row"
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        padding: "12px 14px",
        background: "var(--bg-base)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        fontFamily: "var(--font-ui)",
      }}
    >
      {/* 1. Step header row */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span
          aria-hidden="true"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            width: 28,
            height: 28,
            borderRadius: 6,
            background: "rgba(33, 158, 188, 0.10)",
            color: "#219ebc",
            flexShrink: 0,
          }}
        >
          <Newspaper size={14} strokeWidth={1.75} aria-hidden />
        </span>
        <span
          style={{
            fontSize: 12.5,
            fontWeight: 600,
            color: "var(--text-primary)",
            flex: 1,
            letterSpacing: "-0.01em",
          }}
        >
          {stepLabel}
        </span>
        <span
          style={{
            fontSize: 10,
            fontWeight: 500,
            padding: "2px 7px",
            // Rounded-rect (matches the "Agent" pill shape on the draft card),
            // no border — just the tinted fill.
            borderRadius: 6,
            background: "rgba(33, 158, 188, 0.10)",
            color: "#219ebc",
            flexShrink: 0,
          }}
        >
          min_conf &ge; {confPct}%
        </span>
      </div>

      {/* 2. Event description or keywords */}
      {config.event_description ? (
        <p
          style={{
            margin: 0,
            fontSize: 12,
            color: "var(--text-secondary)",
            lineHeight: 1.45,
          }}
        >
          Looking for: {config.event_description}
        </p>
      ) : keywords.length > 0 ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
          {visibleKeywords.map((kw) => (
            <span
              key={kw}
              style={{
                fontSize: 11,
                fontWeight: 500,
                padding: "2px 8px",
                borderRadius: "var(--radius-pill)",
                background: "var(--bg-elevated)",
                border: "1px solid var(--glass-border)",
                color: "var(--text-secondary)",
              }}
            >
              {kw}
            </span>
          ))}
          {overflowCount > 0 && (
            <span
              style={{
                fontSize: 11,
                fontWeight: 500,
                padding: "2px 8px",
                borderRadius: "var(--radius-pill)",
                background: "var(--bg-elevated)",
                border: "1px solid var(--glass-border)",
                color: "var(--text-tertiary)",
              }}
            >
              +{overflowCount}
            </span>
          )}
        </div>
      ) : null}

      {/* 3. Source logos row */}
      <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
          {displaySources.map((src) => (
            <SourceLogo key={src} sourceId={src} size={18} />
          ))}
          {usingDefaultSources && (
            <span
              style={{
                fontSize: 10.5,
                color: "var(--text-tertiary)",
                marginLeft: 2,
              }}
            >
              default sources
            </span>
          )}
        </div>
      </div>

      {/* 4. Run output section — only when runOutput is provided */}
      {runOutput && maxConf !== null && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {/* Match summary */}
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span
              aria-hidden="true"
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: matched ? "var(--color-profit)" : "var(--text-tertiary)",
                flexShrink: 0,
              }}
            />
            <span
              style={{
                fontSize: 12,
                color: matched ? "var(--color-profit)" : "var(--text-secondary)",
                fontWeight: 500,
              }}
            >
              {matched
                ? `Matched at ${maxConf}% confidence`
                : `No match (highest signal ${maxConf}%)`}
            </span>
          </div>

          {/* Article list */}
          {topArticles.length > 0 && (
            <div
              role="list"
              aria-label="Top matched articles"
              style={{ display: "flex", flexDirection: "column", gap: 6 }}
            >
              {topArticles.map((article) => (
                <ArticleCard key={article.id} article={article} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
