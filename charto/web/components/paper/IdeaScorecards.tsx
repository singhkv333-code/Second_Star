"use client";

/**
 * IdeaScorecards — the forward-test "Ideas" list.
 *
 * The PaperDashboard "Ideas" tab renders this (the lead wires the tab — this
 * component does NOT touch PaperDashboard). Reads getPaperIdeas() into a 4-state
 * S<T> machine and renders a responsive grid of Quartr idea cards: label,
 * origin/status badge, the verdict chip, and a compact metric row (cum return,
 * forward Sharpe, PSR, alpha, max DD, maturity, a has-backtest dot). Clicking a
 * card opens IdeaDetailPanel for that id inside a shadcn Dialog.
 *
 * Mirrors the Quartr idioms of EquityCurveChart / KpiStatCards.
 */

import { useEffect, useState } from "react";

import { getPaperIdeas, type PaperIdea } from "@/lib/api";
import { isError } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { dateShort, pct, pnlColor } from "@/components/paper/format";
import { IdeaDetailPanel } from "@/components/paper/IdeaDetailPanel";

type S<T> =
  | { k: "loading" }
  | { k: "ok"; d: T }
  | { k: "err" }
  | { k: "empty" };

const DASH = "—";

const GRID_STYLE: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))",
  gap: 12,
};

/** Verdict → { Badge variant, humanized label }. */
const VERDICT_MAP: Record<
  string,
  { variant: "success" | "warning" | "destructive" | "muted"; label: string }
> = {
  on_track: { variant: "success", label: "On track" },
  decayed: { variant: "warning", label: "Decayed" },
  execution_problem: { variant: "destructive", label: "Execution problem" },
  insufficient_data: { variant: "muted", label: "Insufficient data" },
};

/** A number → fixed-2 string with a null/NaN → em-dash guard (Sharpe/PSR have
 * no shared formatter). */
function fixed2(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return DASH;
  return n.toFixed(2);
}

function VerdictChip({ verdict }: { verdict: string | null }): React.ReactElement {
  if (!verdict) {
    return (
      <Badge variant="muted" style={{ fontSize: 10 }}>
        {DASH}
      </Badge>
    );
  }
  const m = VERDICT_MAP[verdict];
  return (
    <Badge variant={m?.variant ?? "muted"} style={{ fontSize: 10 }}>
      {m?.label ?? verdict}
    </Badge>
  );
}

/** A label/value pair inside a card's metric grid. */
function Metric({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: string;
}): React.ReactElement {
  return (
    <div className="flex flex-col" style={{ gap: 2, minWidth: 0 }}>
      <span className="q-uppercase-label" style={{ fontSize: 9 }}>
        {label}
      </span>
      <span
        className="tabular-nums q-mono"
        style={{
          fontSize: 13,
          color: color ?? "var(--text-primary)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {value}
      </span>
    </div>
  );
}

function IdeaCard({
  idea,
  onOpen,
}: {
  idea: PaperIdea;
  onOpen: () => void;
}): React.ReactElement {
  const maturity =
    idea.maturity_days !== null
      ? `${idea.maturity_days}d`
      : dateShort(idea.inception_date);

  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex flex-col text-left"
      style={{
        gap: 12,
        padding: "14px 16px",
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        cursor: "pointer",
        transition: "border-color 0.35s var(--ease-quartr)",
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = "var(--glass-border-hover)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = "var(--glass-border)";
      }}
    >
      {/* Header: label + verdict */}
      <div className="flex items-start justify-between" style={{ gap: 10 }}>
        <span
          className="q-display"
          style={{
            fontSize: 16,
            lineHeight: 1.2,
            fontWeight: 600,
            color: "var(--text-primary)",
            minWidth: 0,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {idea.label}
        </span>
        <VerdictChip verdict={idea.verdict} />
      </div>

      {/* Origin + status + has-backtest */}
      <div className="flex items-center" style={{ gap: 8, flexWrap: "wrap" }}>
        <Badge
          variant="secondary"
          style={{
            fontSize: 10,
            background: "var(--bg-secondary)",
            color: "var(--text-secondary)",
          }}
        >
          {idea.origin_kind}
        </Badge>
        <Badge
          variant="secondary"
          style={{
            fontSize: 10,
            background: "var(--bg-secondary)",
            color: "var(--text-secondary)",
          }}
        >
          {idea.status}
        </Badge>
        {idea.has_backtest ? (
          <span
            className="flex items-center"
            style={{ gap: 4, fontSize: 10, color: "var(--text-tertiary)" }}
          >
            <span
              aria-hidden
              style={{
                display: "inline-block",
                width: 6,
                height: 6,
                borderRadius: "var(--radius-pill)",
                background: "var(--price-line)",
              }}
            />
            backtest
          </span>
        ) : null}
      </div>

      {/* Metric grid */}
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
          gap: 10,
        }}
      >
        <Metric
          label="Cum Ret"
          value={pct(idea.cum_return_pct)}
          color={pnlColor(idea.cum_return_pct)}
        />
        <Metric label="Sharpe" value={fixed2(idea.sharpe)} />
        <Metric label="PSR" value={fixed2(idea.psr)} />
        <Metric label="Alpha" value={pct(idea.alpha)} color={pnlColor(idea.alpha)} />
        <Metric
          label="Max DD"
          value={pct(idea.max_drawdown_pct)}
          color={pnlColor(idea.max_drawdown_pct)}
        />
        <Metric label="Maturity" value={maturity} />
      </div>
    </button>
  );
}

function CardShell({ children }: { children: React.ReactNode }): React.ReactElement {
  return (
    <div
      className="flex flex-col"
      style={{
        gap: 6,
        padding: "32px 16px",
        background: "var(--bg-primary)",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        alignItems: "center",
        textAlign: "center",
      }}
    >
      {children}
    </div>
  );
}

export function IdeaScorecards(): React.ReactElement {
  const [s, setS] = useState<S<PaperIdea[]>>({ k: "loading" });
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let on = true;
    getPaperIdeas()
      .then((r) => {
        if (!on) return;
        if (isError(r)) {
          setS({ k: "err" });
          return;
        }
        const d = r.data;
        setS(!Array.isArray(d) || d.length === 0 ? { k: "empty" } : { k: "ok", d });
      })
      .catch(() => {
        if (on) setS({ k: "err" });
      });
    return () => {
      on = false;
    };
  }, []);

  let body: React.ReactElement;

  if (s.k === "loading") {
    body = (
      <div style={GRID_STYLE}>
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} style={{ height: 168, borderRadius: "var(--radius-md)" }} />
        ))}
      </div>
    );
  } else if (s.k === "err") {
    body = (
      <CardShell>
        <span className="q-display" style={{ fontSize: 15, color: "var(--text-secondary)" }}>
          Couldn&rsquo;t load your ideas
        </span>
        <span style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
          Try again in a moment.
        </span>
      </CardShell>
    );
  } else if (s.k === "empty") {
    body = (
      <CardShell>
        <span className="q-display" style={{ fontSize: 15, color: "var(--text-secondary)" }}>
          No forward-test ideas yet
        </span>
        <span
          style={{
            fontSize: 13,
            color: "var(--text-tertiary)",
            maxWidth: 380,
          }}
        >
          Orders you place from chat or a workflow become tracked ideas here.
        </span>
      </CardShell>
    );
  } else {
    body = (
      <div style={GRID_STYLE}>
        {s.d.map((idea) => (
          <IdeaCard key={idea.id} idea={idea} onOpen={() => setSelected(idea.id)} />
        ))}
      </div>
    );
  }

  return (
    <>
      {body}
      <Dialog open={selected !== null} onOpenChange={(o) => !o && setSelected(null)}>
        <DialogContent
          aria-describedby={undefined}
          className="max-w-[820px] gap-0 overflow-y-auto p-5"
          style={{
            maxHeight: "88vh",
            background: "var(--bg-secondary)",
            border: "1px solid var(--glass-border)",
          }}
        >
          <DialogTitle className="sr-only">Idea scorecard</DialogTitle>
          {selected !== null ? (
            <IdeaDetailPanel ideaId={selected} onClose={() => setSelected(null)} />
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  );
}
