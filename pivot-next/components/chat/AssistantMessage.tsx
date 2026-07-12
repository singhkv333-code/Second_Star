"use client";

import { memo } from "react";
import Link from "next/link";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";
import { SmartMarkdownTable } from "@/components/chat/SmartMarkdownTable";

// ── Ticker detection ─────────────────────────────────────────────────────
// Matches NSE/BSE-listed symbols: optional exchange prefix, all-uppercase
// core of 2–15 chars (letters + digits + & and -), optional .NS/.BO suffix.
// Lowercase letters or underscores anywhere → NOT a ticker (is code).
const INLINE_TICKER_RE =
  /^(NSE:|BSE:)?([A-Z][A-Z0-9&-]{1,14})(\.NS|\.BO)?$/;

/**
 * Returns the canonical symbol (prefix/suffix stripped) if `raw` looks like
 * an NSE/BSE ticker; returns null otherwise.
 */
export function extractTickerSymbol(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed || trimmed !== trimmed.toUpperCase()) return null;
  const m = INLINE_TICKER_RE.exec(trimmed);
  return m ? (m[2] ?? null) : null;
}

// ── Gain/loss number coloring ────────────────────────────────────────────
// Only numbers carrying an EXPLICIT +/-/− sign are colored — an unsigned
// "8.2%" is ambiguous (could be a P/E, an expense ratio, anything) and
// coloring it would misrepresent data that isn't a gain/loss. Matches
// "+12.4%", "-8.2%", "+₹1,240.50", "₹-500", "−3.1%" (U+2212 minus).
const GAIN_LOSS_RE = /([+\-−]\s?₹\s?[\d,]+(?:\.\d+)?|₹\s?[+\-−]\s?[\d,]+(?:\.\d+)?|[+\-−]\s?\d[\d,]*(?:\.\d+)?%)/g;

export function colorizeGainLoss(text: string, keyPrefix: string): React.ReactNode {
  if (!/[+\-−]/.test(text)) return text;
  const parts = text.split(GAIN_LOSS_RE);
  if (parts.length === 1) return text;
  return parts.map((part, i) => {
    if (i % 2 === 0) return part;
    const negative = part.includes("-") || part.includes("−");
    return (
      <span
        key={`${keyPrefix}-${i}`}
        className="font-medium tabular-nums"
        style={{ color: negative ? "var(--color-loss)" : "var(--color-profit)" }}
      >
        {part}
      </span>
    );
  });
}

/** Colorizes plain-string children in place; already-rendered elements
 * (links, bold, code) pass through untouched — we never re-parse markup
 * that's already been resolved into React nodes. */
function withGainLossColoring(children: React.ReactNode): React.ReactNode {
  const arr = Array.isArray(children) ? children : [children];
  return arr.map((child, i) =>
    typeof child === "string" ? colorizeGainLoss(child, `gl-${i}`) : child,
  );
}

type Props = {
  text: string;
  className?: string;
};

/**
 * AssistantMessage — renders an assistant turn as flowing prose, not a
 * bordered card. Mirrors the ChatGPT / Claude reading experience: real
 * headings, real lists, real code blocks; no `**asterisks**` leaking
 * through to the user; no surrounding box.
 *
 * The model emits standard GitHub-flavored markdown. We render it with
 * react-markdown + remark-gfm and style each element via Tailwind so
 * the output matches the rest of the app (font, color tokens, spacing).
 */
/**
 * Memoised so a parent re-render (market-data poll, hover state, the
 * streaming elapsed counter, …) does NOT re-parse the markdown and
 * replace the rendered text nodes. Stable DOM nodes are what let a
 * user's text selection survive long enough to use the "reply by
 * selecting" gesture — otherwise the selection collapses mid-render.
 */
function AssistantMessage({ text, className }: Props): React.JSX.Element {
  return (
    <div
      // Marks this prose as a valid source for the "reply by selecting"
      // gesture — ChatDemo's selection listener only surfaces the
      // floating Reply button for text highlighted inside this element.
      data-reply-source=""
      className={cn(
        // Base reading column — generous max-width so paragraphs breathe
        // but we don't fight the parent layout.
        "w-full max-w-3xl text-[15px] leading-7 text-foreground",
        // Vertical rhythm between block elements; matches ChatGPT/Claude.
        "[&>*+*]:mt-3",
        className,
      )}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => (
            <h1 className="mt-4 text-xl font-semibold tracking-tight text-foreground first:mt-0">
              {children}
            </h1>
          ),
          h2: ({ children }) => (
            <h2 className="mt-5 text-lg font-semibold tracking-tight text-foreground first:mt-0">
              {children}
            </h2>
          ),
          h3: ({ children }) => (
            <h3 className="mt-4 text-base font-semibold text-foreground first:mt-0">
              {children}
            </h3>
          ),
          h4: ({ children }) => (
            <h4 className="mt-4 text-sm font-semibold uppercase tracking-wide text-muted-foreground first:mt-0">
              {children}
            </h4>
          ),
          p: ({ children }) => (
            <p className="leading-7 text-foreground">
              {withGainLossColoring(children)}
            </p>
          ),
          ul: ({ children }) => (
            <ul className="ml-1 flex flex-col gap-1.5 pl-5 [list-style:disc] marker:text-muted-foreground/70">
              {children}
            </ul>
          ),
          ol: ({ children }) => (
            <ol className="ml-1 flex flex-col gap-1.5 pl-5 [list-style:decimal] marker:text-muted-foreground/70">
              {children}
            </ol>
          ),
          li: ({ children }) => (
            <li className="leading-7 [&>p]:m-0">
              {withGainLossColoring(children)}
            </li>
          ),
          strong: ({ children }) => (
            <strong className="font-semibold text-foreground">{children}</strong>
          ),
          em: ({ children }) => <em className="italic">{children}</em>,
          a: ({ href, children }) => {
            // Internal stock-page links (the model writes these for company
            // mentions it can resolve to a ticker) navigate client-side and
            // read bolder — they're the company name, not an external ref.
            if (href?.startsWith("/")) {
              return (
                <Link
                  href={href}
                  className="font-semibold text-primary underline underline-offset-2 hover:opacity-80"
                >
                  {children}
                </Link>
              );
            }
            return (
              <a
                href={href}
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-primary underline underline-offset-2 hover:opacity-80"
              >
                {children}
              </a>
            );
          },
          hr: () => <hr className="my-4 border-border" />,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-border pl-4 text-muted-foreground">
              {children}
            </blockquote>
          ),
          code: ({ className: cls, children, ...props }: {
            className?: string;
            children?: React.ReactNode;
          } & React.HTMLAttributes<HTMLElement>) => {
            // react-markdown v10 dropped the `inline` boolean prop.
            // Detect inline vs block: block code either has a `language-*`
            // className (fenced with language specifier) or its text content
            // contains a newline (fenced without specifier). Inline backtick
            // code is always a single-line string with no language class.
            const text = String(children ?? "");
            const isBlock = cls?.includes("language-") || text.includes("\n");

            if (!isBlock) {
              // Inline code path — detect ticker-shaped text (e.g. `RELIANCE`,
              // `ONGC`, `NSE:INFY`) and render as a same-font stock-page link.
              // Non-ticker code (e.g. `revenue_growth`, `cagrPct`, `pe_ratio`)
              // keeps the standard monospace chip style.
              const ticker = extractTickerSymbol(text);
              if (ticker) {
                return (
                  <Link
                    href={`/stock/${encodeURIComponent(ticker)}`}
                    className="font-semibold text-primary underline underline-offset-2 hover:opacity-80"
                  >
                    {text}
                  </Link>
                );
              }
              return (
                <code
                  className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.85em] text-foreground"
                  {...props}
                >
                  {children}
                </code>
              );
            }
            // Block code path — preserve monospace with language class.
            return (
              <code className={cn("font-mono text-[13px]", cls)} {...props}>
                {children}
              </code>
            );
          },
          pre: ({ children }) => (
            <pre className="overflow-x-auto rounded-lg bg-muted px-4 py-3 font-mono text-[13px] leading-6 text-foreground">
              {children}
            </pre>
          ),
          // Tables render through SmartMarkdownTable: sortable numeric/name
          // columns, ink-black header, per-cell borders, company cells
          // linked to /stock/[symbol], Kite-style hover quick actions.
          // It consumes the raw hast node and re-renders the table itself,
          // so the thead/th/td component overrides below never fire.
          table: ({ node }) => <SmartMarkdownTable node={node} />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

export default memo(AssistantMessage);
