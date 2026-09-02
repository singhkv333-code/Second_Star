"use client";

/**
 * StrategiesPanel — the rules that put the shares there.
 *
 * The paper book's other five views answer "what do I hold". This one answers
 * "why", which is the question a simulated portfolio exists to make answerable:
 * every fill in the journal has a strategy behind it, and every strategy states
 * its entry and exit as the sentence the card showed when it was built.
 *
 * Two controls only — pause/arm and retire. There is deliberately no editor
 * here: a strategy is amended in the chat that built it, where the model can
 * re-emit the whole draft and the condition tree stays the thing the backtest
 * ran. A form that edits a threshold in isolation would let the two drift.
 */

import * as React from "react";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { inr, qty as fmtQty } from "@/components/paper/format";
import {
  getStrategies,
  patchStrategy,
  retireStrategy,
  type Strategy,
} from "@/lib/strategiesApi";
import { isError } from "@/lib/types";

type S =
  | { k: "loading" }
  | { k: "ok"; d: Strategy[] }
  | { k: "err"; m: string }
  | { k: "empty" };

const PROFIT = "var(--color-profit)";
const LOSS = "var(--color-loss)";

/** State pill. Armed is the only one that carries the accent — the rest are
 *  quiet on purpose, because a paused rule is not a warning. */
function StatePill({ s }: { s: Strategy }): React.ReactElement {
  const armed = s.state === "armed";
  const color = armed ? PROFIT : "var(--text-secondary)";
  return (
    <span
      className="q-uppercase-label"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        padding: "2px 8px",
        borderRadius: "var(--radius-pill)",
        background: `color-mix(in srgb, ${color} 12%, transparent)`,
        color,
        letterSpacing: "0.04em",
        fontWeight: 600,
        lineHeight: 1.4,
      }}
    >
      {armed ? (
        <span
          aria-hidden
          style={{
            width: 6,
            height: 6,
            borderRadius: 999,
            background: color,
          }}
        />
      ) : null}
      {s.state}
    </span>
  );
}

function Row({
  s,
  onChanged,
}: {
  s: Strategy;
  onChanged: () => void;
}): React.ReactElement {
  const [busy, setBusy] = useState(false);

  const act = useCallback(
    async (what: "toggle" | "retire") => {
      setBusy(true);
      try {
        if (what === "retire") {
          await retireStrategy(s.id);
        } else {
          await patchStrategy(s.id, {
            state: s.state === "armed" ? "paused" : "armed",
          });
        }
        onChanged();
      } finally {
        setBusy(false);
      }
    },
    [s.id, s.state, onChanged],
  );

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 10,
        padding: "14px 16px",
        borderTop: "1px solid var(--glass-border)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          flexWrap: "wrap",
          gap: 10,
        }}
      >
        <span
          style={{
            fontFamily: "var(--font-ui)",
            fontSize: 14,
            fontWeight: 600,
            color: "var(--text-primary)",
          }}
        >
          {s.symbol}
        </span>
        <span
          className="q-uppercase-label"
          style={{ fontSize: 11, color: "var(--text-secondary)" }}
        >
          {s.interval}
        </span>
        <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
          {s.side} {fmtQty(s.quantity)}
        </span>
        <StatePill s={s} />
        {s.in_position ? (
          <Badge variant="outline" style={{ fontSize: 11 }}>
            holding
            {s.entry_price !== null ? ` from ${inr(s.entry_price)}` : ""}
          </Badge>
        ) : null}
        <span style={{ flex: 1 }} />
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
          {s.fire_count === 0
            ? "not fired yet"
            : `${s.fire_count} fire${s.fire_count === 1 ? "" : "s"}`}
        </span>
      </div>

      {/* The rule itself, in the words the card used. This is the whole point
          of the row — a strategy you cannot read is one you cannot trust. */}
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        {s.readback.entry ? (
          <Sentence label="Entry" text={s.readback.entry} />
        ) : null}
        {s.readback.exit ? (
          <Sentence label="Exit" text={s.readback.exit} />
        ) : (
          <Sentence label="Exit" text="none — it buys and holds" muted />
        )}
      </div>

      {s.last_error ? (
        <p
          style={{
            margin: 0,
            fontSize: 12.5,
            color: LOSS,
            fontFamily: "var(--font-ui)",
          }}
        >
          {s.last_error}
        </p>
      ) : null}

      <div style={{ display: "flex", gap: 8 }}>
        <button
          type="button"
          disabled={busy}
          onClick={() => act("toggle")}
          style={btn}
        >
          {s.state === "armed" ? "Pause" : "Arm"}
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={() => act("retire")}
          style={btn}
        >
          Retire
        </button>
      </div>
    </div>
  );
}

const btn: React.CSSProperties = {
  fontFamily: "var(--font-ui)",
  fontSize: 12.5,
  padding: "4px 12px",
  borderRadius: "var(--radius-xs)",
  border: "1px solid var(--glass-border)",
  background: "transparent",
  color: "var(--text-secondary)",
  cursor: "pointer",
};

function Sentence({
  label,
  text,
  muted,
}: {
  label: string;
  text: string;
  muted?: boolean;
}): React.ReactElement {
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
      <span
        className="q-uppercase-label"
        style={{
          fontSize: 10.5,
          width: 40,
          flexShrink: 0,
          color: "var(--text-secondary)",
          letterSpacing: "0.06em",
        }}
      >
        {label}
      </span>
      <span
        style={{
          fontFamily: "var(--font-ui)",
          fontSize: 13,
          color: muted ? "var(--text-secondary)" : "var(--text-primary)",
        }}
      >
        {text}
      </span>
    </div>
  );
}

export function StrategiesPanel(): React.ReactElement {
  const [s, setS] = useState<S>({ k: "loading" });

  const load = useCallback(() => {
    let live = true;
    void getStrategies().then((r) => {
      if (!live) return;
      if (isError(r)) {
        setS({ k: "err", m: r.error.message || "could not load strategies" });
        return;
      }
      const rows = r.data.strategies ?? [];
      setS(rows.length ? { k: "ok", d: rows } : { k: "empty" });
    });
    return () => {
      live = false;
    };
  }, []);

  useEffect(() => load(), [load]);

  if (s.k === "loading") {
    return (
      <div style={card}>
        <Skeleton style={{ height: 96 }} />
      </div>
    );
  }
  if (s.k === "err") {
    return (
      <div style={card}>
        <p style={note}>{s.m}</p>
      </div>
    );
  }
  if (s.k === "empty") {
    return (
      <div style={card}>
        <p style={note}>
          No strategies yet. Build one in the chart&apos;s execution mode —
          describe the rule, then say &ldquo;save it&rdquo;. It arms against the
          live tick and fills into this book.
        </p>
      </div>
    );
  }
  return (
    <div style={card}>
      <div style={{ padding: "14px 16px 4px" }}>
        <h3 style={heading}>Strategies</h3>
        <p style={note}>
          Every fill in this book came from one of these. They place simulated
          orders only.
        </p>
      </div>
      {s.d.map((row) => (
        <Row key={row.id} s={row} onChanged={load} />
      ))}
    </div>
  );
}

const card: React.CSSProperties = {
  background: "var(--bg-secondary)",
  border: "1px solid var(--glass-border)",
  borderRadius: "var(--radius-sm)",
  overflow: "hidden",
};

const heading: React.CSSProperties = {
  margin: 0,
  fontFamily: "var(--font-ui)",
  fontSize: 14,
  fontWeight: 600,
  color: "var(--text-primary)",
};

const note: React.CSSProperties = {
  margin: "4px 0 12px",
  fontFamily: "var(--font-ui)",
  fontSize: 12.5,
  color: "var(--text-secondary)",
  maxWidth: "62ch",
};
