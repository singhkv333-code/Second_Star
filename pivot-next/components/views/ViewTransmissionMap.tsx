"use client";

/**
 * ViewTransmissionMap — the cause → effect chain, as a VERTICAL stack of
 * square, border-only node cells with WRAPPING labels.
 *
 * DESIGN LAW (components/views): square corners, borders only (no fills), no
 * jargon, 13px floor. The chain NEVER runs off the card — there is no
 * overflow-x scroll, no nowrap, no maxWidth, no truncation. Labels wrap.
 *
 * Plain words only:
 *  - node labels come from from_label / to_label (humanized backend fields),
 *    falling back to a de-slugged node id only if absent.
 *  - the connector carries strength_label / strengthLabel() — the WORD
 *    ("strong link" / "moderate link" / "weak link"), NEVER the bare 0.80.
 *  - the small caption under the arrow is plain_evidence (already plain text),
 *    NEVER the raw edge_label slug or the jargon evidence string.
 */

import * as React from "react";
import { ArrowDown } from "lucide-react";
import { strengthLabel } from "@/components/views/view-format";
import type { TransmissionEdge } from "@/lib/types";

interface ViewTransmissionMapProps {
  edges: TransmissionEdge[];
}

interface NodeItem {
  id: string;
  label: string;
}

/** De-slug a raw node id only as a last resort (when no plain label exists). */
function deslug(raw: string): string {
  const s = raw.replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Build a deduped, ordered node list using the plain humanized labels. */
function buildNodes(edges: TransmissionEdge[]): NodeItem[] {
  const seen = new Set<string>();
  const nodes: NodeItem[] = [];
  for (const e of edges) {
    if (!seen.has(e.from_node)) {
      seen.add(e.from_node);
      nodes.push({ id: e.from_node, label: e.from_label ?? deslug(e.from_node) });
    }
    if (!seen.has(e.to_node)) {
      seen.add(e.to_node);
      nodes.push({ id: e.to_node, label: e.to_label ?? deslug(e.to_node) });
    }
  }
  return nodes;
}

function NodeCell({ label }: { label: string }) {
  return (
    <div
      style={{
        width: "100%",
        border: "1px solid var(--glass-border)",
        borderRadius: "var(--radius-md)",
        background: "var(--bg-base)",
        padding: "12px 14px",
        fontFamily: "var(--font-display)",
        fontSize: 15,
        fontWeight: 600,
        lineHeight: 1.35,
        color: "var(--text-primary)",
        // Wrap freely — never run off the card.
        whiteSpace: "normal",
        overflowWrap: "anywhere",
        wordBreak: "break-word",
      }}
    >
      {label}
    </div>
  );
}

function Connector({ edge }: { edge: TransmissionEdge }) {
  const word = edge.strength_label ?? strengthLabel(edge.strength);
  // Encode strength as line opacity too (calm, not a number).
  const s = edge.strength;
  const opacity =
    s === null || s === undefined ? 0.55 : 0.4 + 0.6 * Math.min(1, Math.max(0, s));

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-start",
        gap: 4,
        padding: "8px 0",
        paddingLeft: 14,
      }}
    >
      <div
        aria-hidden
        style={{
          width: 1,
          height: 16,
          background: "var(--glass-border)",
          opacity,
        }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <ArrowDown
          aria-hidden
          size={15}
          style={{ color: "var(--text-tertiary)", flexShrink: 0 }}
        />
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 13,
            fontWeight: 500,
            color: "var(--text-tertiary)",
            lineHeight: 1.3,
          }}
        >
          {word}
        </span>
      </div>
      {edge.plain_evidence && (
        <span
          style={{
            fontFamily: "var(--font-display)",
            fontSize: 13,
            fontWeight: 400,
            color: "var(--text-secondary)",
            lineHeight: 1.4,
            paddingLeft: 23,
            overflowWrap: "anywhere",
          }}
        >
          {edge.plain_evidence}
        </span>
      )}
    </div>
  );
}

export function ViewTransmissionMap({ edges }: ViewTransmissionMapProps) {
  if (!edges || edges.length === 0) {
    return (
      <p
        style={{
          fontFamily: "var(--font-display)",
          fontSize: 15,
          color: "var(--text-tertiary)",
          margin: 0,
        }}
      >
        No causal map published yet.
      </p>
    );
  }

  const sorted = [...edges].sort((a, b) => a.seq - b.seq);
  const nodes = buildNodes(sorted);
  const edgeByFrom = new Map<string, TransmissionEdge>();
  for (const e of sorted) {
    if (!edgeByFrom.has(e.from_node)) edgeByFrom.set(e.from_node, e);
  }

  return (
    <div
      role="img"
      aria-label="Causal transmission map"
      style={{ display: "flex", flexDirection: "column", alignItems: "stretch" }}
    >
      {nodes.map((node, i) => {
        const edgeAfter = edgeByFrom.get(node.id);
        const showConnector = edgeAfter && i < nodes.length - 1;
        return (
          <React.Fragment key={node.id}>
            <NodeCell label={node.label} />
            {showConnector && <Connector edge={edgeAfter!} />}
          </React.Fragment>
        );
      })}
    </div>
  );
}
