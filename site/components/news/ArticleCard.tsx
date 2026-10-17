"use client";

import { ExternalLink } from "lucide-react";
import { formatDistanceToNow } from "date-fns";
import { SourceBadge, SourceLogo } from "@/components/news/SourceBadge";
import type { NewsArticle } from "@/lib/news-types";

/**
 * ArticleCard — news article row.
 * When matched=true, adds a thin accent border using --color-profit.
 */

type Props = {
  article: NewsArticle;
};

export function ArticleCard({ article }: Props) {
  const timeAgo = (() => {
    try {
      return formatDistanceToNow(new Date(article.published_at), {
        addSuffix: true,
      });
    } catch {
      return article.published_at;
    }
  })();

  return (
    <div
      role="listitem"
      style={{
        padding: "14px 16px",
        background: "var(--bg-primary)",
        border: article.matched
          ? "1px solid var(--color-profit)"
          : "1px solid var(--glass-border)",
        borderRadius: "var(--radius-sm)",
        display: "flex",
        flexDirection: "column",
        gap: 6,
        transition: "border-color 150ms var(--ease-quartr)",
      }}
    >
      {/* Title + link-out */}
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
        <p
          style={{
            margin: 0,
            flex: 1,
            fontSize: 13,
            fontWeight: 500,
            lineHeight: 1.45,
            color: "var(--text-primary)",
            fontFamily: "var(--font-ui)",
          }}
        >
          {article.title}
        </p>
        <a
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Open article: ${article.title}`}
          style={{
            flexShrink: 0,
            marginTop: 1,
            color: "var(--text-tertiary)",
            lineHeight: 1,
          }}
        >
          <ExternalLink aria-hidden className="h-3.5 w-3.5" />
        </a>
      </div>

      {/* Description (if present) */}
      {article.description && (
        <p
          style={{
            margin: 0,
            fontSize: 12,
            lineHeight: 1.5,
            color: "var(--text-secondary)",
            display: "-webkit-box",
            WebkitLineClamp: 2,
            WebkitBoxOrient: "vertical",
            overflow: "hidden",
            fontFamily: "var(--font-ui)",
          }}
        >
          {article.description}
        </p>
      )}

      {/* Footer: source logo + source name + credibility badge + confidence pill + time */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginTop: 2,
          flexWrap: "wrap",
        }}
      >
        <SourceLogo sourceId={article.source} size={14} />
        <span
          style={{
            fontSize: 11,
            color: "var(--text-secondary)",
            fontFamily: "var(--font-ui)",
            fontWeight: 500,
          }}
        >
          {article.source}
        </span>

        <SourceBadge score={article.credibility_score} />

        {article.match_confidence !== null && (
          <span
            style={{
              fontSize: 10,
              fontFamily: "var(--font-ui)",
              padding: "1px 6px",
              borderRadius: "var(--radius-pill)",
              background: article.matched
                ? "rgba(5,150,105,0.12)"
                : "var(--bg-elevated)",
              color: article.matched
                ? "var(--color-profit)"
                : "var(--text-tertiary)",
              fontWeight: 500,
            }}
          >
            {Math.round(article.match_confidence * 100)}% match
          </span>
        )}

        {article.matched && (
          <span
            style={{
              fontSize: 10,
              fontFamily: "var(--font-ui)",
              padding: "1px 6px",
              borderRadius: "var(--radius-pill)",
              background: "rgba(5,150,105,0.12)",
              color: "var(--color-profit)",
              fontWeight: 600,
            }}
          >
            Matched
          </span>
        )}

        <span
          style={{
            fontSize: 11,
            color: "var(--text-tertiary)",
            marginLeft: "auto",
            fontFamily: "var(--font-ui)",
          }}
        >
          {timeAgo}
        </span>
      </div>

      {/* Reason (if present) */}
      {article.reason && (
        <p
          style={{
            margin: 0,
            fontSize: 11,
            lineHeight: 1.5,
            color: "var(--text-tertiary)",
            fontStyle: "italic",
            fontFamily: "var(--font-ui)",
          }}
        >
          {article.reason}
        </p>
      )}
    </div>
  );
}
