"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

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
export default function AssistantMessage({ text, className }: Props): React.JSX.Element {
  return (
    <div
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
            <p className="leading-7 text-foreground">{children}</p>
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
            <li className="leading-7 [&>p]:m-0">{children}</li>
          ),
          strong: ({ children }) => (
            <strong className="font-semibold text-foreground">{children}</strong>
          ),
          em: ({ children }) => <em className="italic">{children}</em>,
          a: ({ href, children }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="font-medium text-primary underline underline-offset-2 hover:opacity-80"
            >
              {children}
            </a>
          ),
          hr: () => <hr className="my-4 border-border" />,
          blockquote: ({ children }) => (
            <blockquote className="border-l-2 border-border pl-4 text-muted-foreground">
              {children}
            </blockquote>
          ),
          code: ({ inline, className: cls, children, ...props }: {
            inline?: boolean;
            className?: string;
            children?: React.ReactNode;
          } & React.HTMLAttributes<HTMLElement>) => {
            if (inline) {
              return (
                <code
                  className="rounded bg-muted px-1.5 py-0.5 font-mono text-[0.85em] text-foreground"
                  {...props}
                >
                  {children}
                </code>
              );
            }
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
          table: ({ children }) => (
            <div className="overflow-x-auto rounded-md border border-border">
              <table className="w-full border-collapse text-sm">{children}</table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-muted/50 text-left">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="border-b border-border px-3 py-2 font-medium text-foreground">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border-b border-border/60 px-3 py-2 align-top text-foreground">
              {children}
            </td>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
