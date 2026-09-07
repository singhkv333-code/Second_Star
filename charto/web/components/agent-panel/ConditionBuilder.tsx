"use client";

/**
 * ConditionBuilder — visual editor for a DSL condition tree.
 *
 * Props:
 *   value    — the current DslNode (null = empty state)
 *   onChange — called with the rebuilt tree on every change
 *   mode     — "entry" or "exit" (exit allows position operands)
 *   schema   — the DslSchema from GET /api/workflows/dsl/schema
 *
 * Supports one level of logic nesting (AND/OR of comparisons, with one
 * nested group allowed). Anything deeper uses the "Advanced (JSON)"
 * escape hatch. Auto-opens the hatch when the incoming value uses node
 * types the builder cannot render (math, aggregate, option_*, etc.).
 *
 * Emits EXACTLY the shapes in the SHARED CONTRACT (exchange defaults
 * "NSE", offset 0).
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Plus, Trash2, ChevronDown, ChevronUp, AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";
import { describeDsl } from "@/lib/api";
import type {
  DslComparisonNode,
  DslConstantNode,
  DslIndicatorNode,
  DslLeafNode,
  DslLogicNode,
  DslNode,
  DslPositionNode,
  DslPriceNode,
  DslSchema,
} from "@/lib/types";

// ---------------------------------------------------------------------------
// Types used internally in the builder
// ---------------------------------------------------------------------------

/** Internal row — always a comparison (two operands + op). */
type CompRow = {
  kind: "comparison";
  id: string;
  left: OperandDraft;
  op: string;
  right: OperandDraft;
};

/** Internal nested group (one level deep). */
type GroupRow = {
  kind: "group";
  id: string;
  op: "and" | "or";
  rows: CompRow[];
};

type Row = CompRow | GroupRow;

// An operand being edited. We store a discriminated union.
type IndicatorDraft = {
  kind: "indicator";
  indicator: string;
  symbol: string;
  period: number;
  timeframe: "daily" | "weekly";
  component: string;
};

type PriceDraft = {
  kind: "price";
  symbol: string;
  basis: "close" | "open" | "high" | "low";
};

type ConstantDraft = {
  kind: "constant";
  value: number;
};

type PositionDraft = {
  kind: "position";
  field: string;
};

type OperandDraft =
  | IndicatorDraft
  | PriceDraft
  | ConstantDraft
  | PositionDraft;

// ---------------------------------------------------------------------------
// Node types the builder cannot render → escape hatch
// ---------------------------------------------------------------------------

const ADVANCED_TYPES = new Set([
  "math", "aggregate", "option_metric", "option_greek", "dte",
  "spread", "volume", "session_day", "gap", "pct_change", "conditional",
]);

function nodeNeedsEscapeHatch(node: DslNode | null): boolean {
  if (!node) return false;
  // Walk a shallow tree to detect advanced nodes.
  function walk(n: DslNode): boolean {
    if (ADVANCED_TYPES.has(n.type)) return true;
    if (n.type === "comparison") return walk(n.left) || walk(n.right);
    if (n.type === "logic") return n.operands.some(walk);
    return false;
  }
  return walk(node);
}

// ---------------------------------------------------------------------------
// Conversions: DslNode ↔ internal draft
// ---------------------------------------------------------------------------

function leafToDraft(node: DslLeafNode): OperandDraft {
  switch (node.type) {
    case "indicator":
      return {
        kind: "indicator",
        indicator: node.indicator,
        symbol: node.symbol,
        period: node.period,
        timeframe: node.timeframe ?? "daily",
        component: node.component ?? "",
      };
    case "price":
      return { kind: "price", symbol: node.symbol, basis: node.basis };
    case "constant":
      return { kind: "constant", value: node.value };
    case "position":
      return { kind: "position", field: node.field };
  }
}

function draftToLeaf(d: OperandDraft): DslLeafNode {
  switch (d.kind) {
    case "indicator": {
      const node: DslIndicatorNode = {
        type: "indicator",
        indicator: d.indicator,
        symbol: d.symbol,
        period: d.period,
        timeframe: d.timeframe,
        exchange: "NSE",
        offset: 0,
      };
      if (d.component) node.component = d.component;
      return node;
    }
    case "price": {
      const node: DslPriceNode = {
        type: "price",
        symbol: d.symbol,
        basis: d.basis,
        exchange: "NSE",
        offset: 0,
      };
      return node;
    }
    case "constant": {
      const node: DslConstantNode = { type: "constant", value: d.value };
      return node;
    }
    case "position": {
      const node: DslPositionNode = { type: "position", field: d.field };
      return node;
    }
  }
}

function compToRow(node: DslComparisonNode, id: string): CompRow | null {
  // Only works for leaf operands — deeper nesting uses the JSON hatch.
  const leftNode = node.left;
  const rightNode = node.right;
  if (
    leftNode.type === "comparison" || leftNode.type === "logic" ||
    rightNode.type === "comparison" || rightNode.type === "logic"
  ) {
    return null;
  }
  return {
    kind: "comparison",
    id,
    left: leafToDraft(leftNode as DslLeafNode),
    op: node.op,
    right: leafToDraft(rightNode as DslLeafNode),
  };
}

function rowToNode(row: CompRow): DslComparisonNode {
  return {
    type: "comparison",
    op: row.op,
    left: draftToLeaf(row.left),
    right: draftToLeaf(row.right),
  };
}

function rowsToNode(rows: CompRow[], op: "and" | "or"): DslNode {
  const comparisons = rows.map(rowToNode);
  if (comparisons.length === 1) {
    // Safe: we know rows.length >= 1, so comparisons[0] is defined.
    return comparisons[0] as DslComparisonNode;
  }
  const node: DslLogicNode = { type: "logic", op, operands: comparisons };
  return node;
}

function nodeToRows(node: DslNode): { op: "and" | "or"; rows: Row[] } | null {
  if (node.type === "comparison") {
    const row = compToRow(node, uid());
    if (!row) return null;
    return { op: "and", rows: [row] };
  }
  if (node.type === "logic") {
    const rows: Row[] = [];
    for (const operand of node.operands) {
      if (operand.type === "comparison") {
        const row = compToRow(operand, uid());
        if (!row) return null;
        rows.push(row);
      } else if (operand.type === "logic") {
        // One level of nested group.
        const groupRows: CompRow[] = [];
        for (const inner of operand.operands) {
          if (inner.type !== "comparison") return null;
          const row = compToRow(inner, uid());
          if (!row) return null;
          groupRows.push(row);
        }
        rows.push({
          kind: "group",
          id: uid(),
          op: operand.op,
          rows: groupRows,
        });
      } else {
        return null; // advanced node
      }
    }
    return { op: node.op, rows };
  }
  return null;
}

let _uidCounter = 0;
function uid(): string {
  return `r${++_uidCounter}`;
}

// ---------------------------------------------------------------------------
// Default operand drafts
// ---------------------------------------------------------------------------

function defaultIndicatorDraft(schema: DslSchema, lastSymbol: string): IndicatorDraft {
  const ind = schema.indicators[0];
  return {
    kind: "indicator",
    indicator: ind?.id ?? "rsi",
    symbol: lastSymbol || "RELIANCE",
    period: ind?.default_period ?? 14,
    timeframe: "daily",
    component: "",
  };
}

function defaultConstantDraft(): ConstantDraft {
  return { kind: "constant", value: 30 };
}

function defaultCompRow(schema: DslSchema, lastSymbol: string): CompRow {
  return {
    kind: "comparison",
    id: uid(),
    left: defaultIndicatorDraft(schema, lastSymbol),
    op: schema.operators[0]?.id ?? ">",
    right: defaultConstantDraft(),
  };
}

function defaultGroupRow(schema: DslSchema, lastSymbol: string): GroupRow {
  return {
    kind: "group",
    id: uid(),
    op: "and",
    rows: [defaultCompRow(schema, lastSymbol)],
  };
}

// ---------------------------------------------------------------------------
// Derive the "last used symbol" from a set of rows
// ---------------------------------------------------------------------------

function extractSymbol(d: OperandDraft): string | null {
  if (d.kind === "indicator" || d.kind === "price") return d.symbol;
  return null;
}

function lastSymbolFromRows(rows: Row[]): string {
  for (let i = rows.length - 1; i >= 0; i--) {
    const row = rows[i];
    if (!row) continue;
    if (row.kind === "comparison") {
      return extractSymbol(row.left) ?? extractSymbol(row.right) ?? "";
    }
    if (row.kind === "group") {
      for (let j = row.rows.length - 1; j >= 0; j--) {
        const innerRow = row.rows[j];
        if (!innerRow) continue;
        return extractSymbol(innerRow.left) ?? extractSymbol(innerRow.right) ?? "";
      }
    }
  }
  return "";
}

// ---------------------------------------------------------------------------
// Derive a DslNode from the current editor state
// ---------------------------------------------------------------------------

function editorToNode(topOp: "and" | "or", rows: Row[]): DslNode | null {
  if (rows.length === 0) return null;

  const parts: DslNode[] = [];
  for (const row of rows) {
    if (row.kind === "comparison") {
      parts.push(rowToNode(row));
    } else {
      // group
      if (row.rows.length === 0) continue;
      parts.push(rowsToNode(row.rows, row.op));
    }
  }

  if (parts.length === 0) return null;
  if (parts.length === 1) return parts[0] ?? null;
  const node: DslLogicNode = { type: "logic", op: topOp, operands: parts };
  return node;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

type OperandEditorProps = {
  value: OperandDraft;
  onChange: (d: OperandDraft) => void;
  mode: "entry" | "exit";
  schema: DslSchema;
  fieldId: string;
};

function OperandEditor({
  value,
  onChange,
  mode,
  schema,
  fieldId,
}: OperandEditorProps): React.ReactElement {
  const kindId = `${fieldId}-kind`;
  const kindOptions = schema.operand_kinds.filter(
    (k) => k !== "position" || mode === "exit",
  );

  return (
    <div className="flex flex-wrap gap-1.5">
      {/* Kind selector */}
      <Select
        value={value.kind}
        onValueChange={(k) => {
          if (k === "indicator") {
            const sym =
              value.kind === "indicator" || value.kind === "price"
                ? value.symbol
                : "RELIANCE";
            onChange(defaultIndicatorDraft(schema, sym));
          } else if (k === "price") {
            const sym =
              value.kind === "indicator" || value.kind === "price"
                ? value.symbol
                : "RELIANCE";
            onChange({ kind: "price", symbol: sym, basis: "close" });
          } else if (k === "constant") {
            onChange({ kind: "constant", value: 30 });
          } else if (k === "position" && mode === "exit") {
            onChange({ kind: "position", field: schema.position_fields[0]?.id ?? "unrealised_pct" });
          }
        }}
      >
        <SelectTrigger
          id={kindId}
          className="h-7 w-[110px] text-xs"
          data-testid={`${fieldId}-kind-select`}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {kindOptions.map((k) => (
            <SelectItem key={k} value={k} className="text-xs capitalize">
              {k === "indicator" ? "Indicator" :
               k === "price"     ? "Price" :
               k === "constant"  ? "Number" :
               k === "position"  ? "Position" : k}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Kind-specific fields */}
      {value.kind === "indicator" && (
        <IndicatorOperandFields
          value={value}
          onChange={(d) => onChange(d)}
          schema={schema}
          fieldId={fieldId}
        />
      )}
      {value.kind === "price" && (
        <PriceOperandFields
          value={value}
          onChange={(d) => onChange(d)}
          fieldId={fieldId}
        />
      )}
      {value.kind === "constant" && (
        <ConstantOperandField
          value={value}
          onChange={(d) => onChange(d)}
          fieldId={fieldId}
        />
      )}
      {value.kind === "position" && mode === "exit" && (
        <PositionOperandField
          value={value}
          onChange={(d) => onChange(d)}
          schema={schema}
          fieldId={fieldId}
        />
      )}
    </div>
  );
}

function IndicatorOperandFields({
  value,
  onChange,
  schema,
  fieldId,
}: {
  value: IndicatorDraft;
  onChange: (d: IndicatorDraft) => void;
  schema: DslSchema;
  fieldId: string;
}): React.ReactElement {
  const indDef = schema.indicators.find((i) => i.id === value.indicator);
  return (
    <>
      {/* Indicator type */}
      <Select
        value={value.indicator}
        onValueChange={(v) => {
          const def = schema.indicators.find((i) => i.id === v);
          onChange({
            ...value,
            indicator: v,
            period: def?.default_period ?? value.period,
            component: "",
          });
        }}
      >
        <SelectTrigger className="h-7 w-[90px] text-xs" data-testid={`${fieldId}-ind-select`}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {schema.indicators.map((i) => (
            <SelectItem key={i.id} value={i.id} className="text-xs">
              {i.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Symbol */}
      <Input
        className="h-7 w-[90px] text-xs"
        placeholder="Symbol"
        value={value.symbol}
        onChange={(e) => onChange({ ...value, symbol: e.target.value.toUpperCase() })}
        data-testid={`${fieldId}-symbol`}
      />

      {/* Period */}
      <Input
        type="number"
        className="h-7 w-[60px] text-xs"
        min={1}
        step={1}
        value={value.period}
        onChange={(e) => {
          const n = parseInt(e.target.value, 10);
          if (Number.isFinite(n) && n >= 1) onChange({ ...value, period: n });
        }}
        data-testid={`${fieldId}-period`}
      />

      {/* Timeframe */}
      <Select
        value={value.timeframe}
        onValueChange={(v) =>
          onChange({ ...value, timeframe: v as "daily" | "weekly" })
        }
      >
        <SelectTrigger className="h-7 w-[80px] text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {schema.timeframes.map((tf) => (
            <SelectItem key={tf} value={tf} className="text-xs capitalize">
              {tf}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Component — only when multi_output */}
      {indDef?.multi_output && indDef.components.length > 0 && (
        <Select
          value={value.component || indDef.components[0]}
          onValueChange={(v) => onChange({ ...value, component: v })}
        >
          <SelectTrigger className="h-7 w-[80px] text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {indDef.components.map((c) => (
              <SelectItem key={c} value={c} className="text-xs">
                {c}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
    </>
  );
}

function PriceOperandFields({
  value,
  onChange,
  fieldId,
}: {
  value: PriceDraft;
  onChange: (d: PriceDraft) => void;
  fieldId: string;
}): React.ReactElement {
  return (
    <>
      <Input
        className="h-7 w-[90px] text-xs"
        placeholder="Symbol"
        value={value.symbol}
        onChange={(e) => onChange({ ...value, symbol: e.target.value.toUpperCase() })}
        data-testid={`${fieldId}-symbol`}
      />
      <Select
        value={value.basis}
        onValueChange={(v) =>
          onChange({ ...value, basis: v as PriceDraft["basis"] })
        }
      >
        <SelectTrigger className="h-7 w-[80px] text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {(["close", "open", "high", "low"] as const).map((b) => (
            <SelectItem key={b} value={b} className="text-xs capitalize">
              {b}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </>
  );
}

function ConstantOperandField({
  value,
  onChange,
  fieldId,
}: {
  value: ConstantDraft;
  onChange: (d: ConstantDraft) => void;
  fieldId: string;
}): React.ReactElement {
  return (
    <Input
      type="number"
      className="h-7 w-[90px] text-xs"
      step="any"
      value={value.value}
      onChange={(e) => {
        const n = parseFloat(e.target.value);
        if (Number.isFinite(n)) onChange({ kind: "constant", value: n });
      }}
      data-testid={`${fieldId}-constant`}
    />
  );
}

function PositionOperandField({
  value,
  onChange,
  schema,
  fieldId,
}: {
  value: PositionDraft;
  onChange: (d: PositionDraft) => void;
  schema: DslSchema;
  fieldId: string;
}): React.ReactElement {
  return (
    <Select
      value={value.field}
      onValueChange={(v) => onChange({ kind: "position", field: v })}
    >
      <SelectTrigger className="h-7 w-[180px] text-xs" data-testid={`${fieldId}-position-field`}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {schema.position_fields.map((f) => (
          <SelectItem key={f.id} value={f.id} className="text-xs">
            {f.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

function CompRowEditor({
  row,
  onChangeRow,
  onDelete,
  schema,
  mode,
  rowIdx,
}: {
  row: CompRow;
  onChangeRow: (r: CompRow) => void;
  onDelete: () => void;
  schema: DslSchema;
  mode: "entry" | "exit";
  rowIdx: number;
}): React.ReactElement {
  return (
    <div
      className="flex flex-wrap items-start gap-2 rounded-md border bg-muted/20 px-3 py-2"
      data-testid={`comp-row-${rowIdx}`}
    >
      {/* Left operand */}
      <OperandEditor
        value={row.left}
        onChange={(d) => onChangeRow({ ...row, left: d })}
        mode={mode}
        schema={schema}
        fieldId={`${row.id}-left`}
      />

      {/* Operator */}
      <Select
        value={row.op}
        onValueChange={(v) => onChangeRow({ ...row, op: v })}
      >
        <SelectTrigger
          className="h-7 w-[130px] text-xs"
          data-testid={`${row.id}-op-select`}
        >
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {schema.operators.map((op) => (
            <SelectItem key={op.id} value={op.id} className="text-xs">
              {op.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Right operand */}
      <OperandEditor
        value={row.right}
        onChange={(d) => onChangeRow({ ...row, right: d })}
        mode={mode}
        schema={schema}
        fieldId={`${row.id}-right`}
      />

      {/* Delete */}
      <button
        type="button"
        aria-label="Remove condition"
        onClick={onDelete}
        className="mt-0.5 rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
      >
        <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
      </button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main ConditionBuilder component
// ---------------------------------------------------------------------------

export type ConditionBuilderProps = {
  value: DslNode | null;
  onChange: (node: DslNode | null) => void;
  mode: "entry" | "exit";
  schema: DslSchema;
};

export function ConditionBuilder({
  value,
  onChange,
  mode,
  schema,
}: ConditionBuilderProps): React.ReactElement {
  // ── JSON hatch state ─────────────────────────────────────────────────
  const [jsonMode, setJsonMode] = useState(false);
  const [jsonText, setJsonText] = useState<string>(
    value ? JSON.stringify(value, null, 2) : "",
  );
  const [jsonError, setJsonError] = useState<string | null>(null);

  // Auto-open JSON hatch when incoming value has nodes the builder can't
  // render.
  const [forcedJson, setForcedJson] = useState(false);
  useEffect(() => {
    if (nodeNeedsEscapeHatch(value)) {
      setJsonMode(true);
      setForcedJson(true);
      setJsonText(value ? JSON.stringify(value, null, 2) : "");
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Visual editor state ───────────────────────────────────────────────
  const [topOp, setTopOp] = useState<"and" | "or">("and");
  const [rows, setRows] = useState<Row[]>(() => {
    if (!value || nodeNeedsEscapeHatch(value)) return [];
    const parsed = nodeToRows(value);
    if (!parsed) return [];
    return parsed.rows;
  });

  // Sync topOp from incoming value on first parse.
  useEffect(() => {
    if (value && value.type === "logic") {
      setTopOp(value.op);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Live readback ─────────────────────────────────────────────────────
  const [english, setEnglish] = useState<string>("");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scheduleReadback = useCallback(
    (node: DslNode | null) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      if (!node) {
        setEnglish("");
        return;
      }
      debounceRef.current = setTimeout(() => {
        void describeDsl(node, mode).then((result) => {
          if ("data" in result) setEnglish(result.data.english);
        });
      }, 300);
    },
    [mode],
  );

  // ── Emit onChange ─────────────────────────────────────────────────────
  const emitRows = useCallback(
    (newRows: Row[], newTopOp: "and" | "or") => {
      const node = editorToNode(newTopOp, newRows);
      onChange(node);
      scheduleReadback(node);
    },
    [onChange, scheduleReadback],
  );

  // Track last used symbol for defaulting new rows.
  const lastSymbol = lastSymbolFromRows(rows);

  // ── Row mutations ─────────────────────────────────────────────────────
  const addCompRow = (): void => {
    const newRows = [...rows, defaultCompRow(schema, lastSymbol)];
    setRows(newRows);
    emitRows(newRows, topOp);
  };

  const addGroupRow = (): void => {
    const newRows = [...rows, defaultGroupRow(schema, lastSymbol)];
    setRows(newRows);
    emitRows(newRows, topOp);
  };

  const deleteRow = (idx: number): void => {
    const newRows = rows.filter((_, i) => i !== idx);
    setRows(newRows);
    emitRows(newRows, topOp);
  };

  const updateRow = (idx: number, updated: Row): void => {
    const newRows = rows.map((r, i) => (i === idx ? updated : r));
    setRows(newRows);
    emitRows(newRows, topOp);
  };

  const changeTopOp = (op: "and" | "or"): void => {
    setTopOp(op);
    emitRows(rows, op);
  };

  // ── Sync JSON hatch text when value changes externally ────────────────
  const prevValueRef = useRef<DslNode | null>(null);
  useEffect(() => {
    if (jsonMode && value !== prevValueRef.current) {
      setJsonText(value ? JSON.stringify(value, null, 2) : "");
    }
    prevValueRef.current = value;
  }, [value, jsonMode]);

  // ── Render: JSON hatch ────────────────────────────────────────────────
  if (jsonMode) {
    return (
      <div className="space-y-2" data-testid="condition-builder-json">
        {forcedJson && (
          <div className="flex items-start gap-2 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950/30 dark:text-amber-400">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span>This condition uses advanced node types. Edit as JSON below.</span>
          </div>
        )}
        <Textarea
          rows={8}
          className="font-mono text-[11px]"
          placeholder='{"type":"comparison","op":">","left":{"type":"indicator","indicator":"rsi","symbol":"RELIANCE","period":14,"timeframe":"daily","exchange":"NSE","offset":0},"right":{"type":"constant","value":40}}'
          value={jsonText}
          onChange={(e) => {
            const raw = e.target.value;
            setJsonText(raw);
            setJsonError(null);
            if (raw.trim() === "") {
              onChange(null);
              scheduleReadback(null);
              return;
            }
            try {
              const parsed = JSON.parse(raw) as DslNode;
              onChange(parsed);
              scheduleReadback(parsed);
            } catch {
              setJsonError("Invalid JSON — keep editing");
            }
          }}
          data-testid="condition-builder-json-textarea"
          aria-label="Condition tree JSON"
        />
        {jsonError && (
          <p className="text-[11px] text-muted-foreground">{jsonError}</p>
        )}
        {!forcedJson && (
          <button
            type="button"
            className="text-[11px] text-muted-foreground underline hover:text-foreground"
            onClick={() => {
              setJsonMode(false);
              setJsonError(null);
              // Re-parse rows from the current value.
              if (value && !nodeNeedsEscapeHatch(value)) {
                const parsed = nodeToRows(value);
                if (parsed) {
                  setRows(parsed.rows);
                  setTopOp(parsed.op);
                }
              }
            }}
          >
            Switch to visual editor
          </button>
        )}
        {english && (
          <p className="rounded-md bg-muted/50 px-3 py-1.5 text-[11px] italic text-muted-foreground">
            {english}
          </p>
        )}
      </div>
    );
  }

  // ── Render: visual editor ─────────────────────────────────────────────
  return (
    <div className="space-y-2" data-testid="condition-builder-visual">
      {rows.length === 0 && (
        <p className="rounded-md border border-dashed px-3 py-4 text-center text-xs text-muted-foreground">
          No conditions yet — press &quot;+ Add condition&quot; to start.
        </p>
      )}

      {/* Top-level AND/OR toggle — only shown when 2+ rows */}
      {rows.length >= 2 && (
        <div className="flex items-center gap-2">
          <Label className="text-xs text-muted-foreground">Match</Label>
          <div className="flex rounded-md border text-xs">
            {(["and", "or"] as const).map((op) => (
              <button
                key={op}
                type="button"
                onClick={() => changeTopOp(op)}
                className={cn(
                  "px-2 py-0.5 first:rounded-l-md last:rounded-r-md",
                  topOp === op
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent",
                )}
                data-testid={`top-op-${op}`}
              >
                {op.toUpperCase()}
              </button>
            ))}
          </div>
          <span className="text-xs text-muted-foreground">of</span>
        </div>
      )}

      {rows.map((row, idx) => {
        if (row.kind === "comparison") {
          return (
            <CompRowEditor
              key={row.id}
              row={row}
              rowIdx={idx}
              onChangeRow={(r) => updateRow(idx, r)}
              onDelete={() => deleteRow(idx)}
              schema={schema}
              mode={mode}
            />
          );
        }
        // Group row
        return (
          <GroupRowEditor
            key={row.id}
            group={row}
            groupIdx={idx}
            onChangeGroup={(g) => updateRow(idx, g)}
            onDelete={() => deleteRow(idx)}
            schema={schema}
            mode={mode}
            lastSymbol={lastSymbol}
          />
        );
      })}

      {/* Action buttons */}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 gap-1 text-xs"
          onClick={addCompRow}
          data-testid="add-condition-btn"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          Add condition
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 gap-1 text-xs"
          onClick={addGroupRow}
          data-testid="add-group-btn"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          Add group
        </Button>
        <button
          type="button"
          className="ml-auto text-[11px] text-muted-foreground underline hover:text-foreground"
          onClick={() => {
            setJsonMode(true);
            setJsonText(value ? JSON.stringify(value, null, 2) : "");
          }}
          data-testid="advanced-json-btn"
        >
          Advanced (JSON)
        </button>
      </div>

      {/* Live readback */}
      {english && (
        <p
          className="rounded-md bg-muted/50 px-3 py-1.5 text-[11px] italic text-muted-foreground"
          data-testid="condition-readback"
        >
          {english}
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Group row sub-editor
// ---------------------------------------------------------------------------

function GroupRowEditor({
  group,
  groupIdx,
  onChangeGroup,
  onDelete,
  schema,
  mode,
  lastSymbol,
}: {
  group: GroupRow;
  groupIdx: number;
  onChangeGroup: (g: GroupRow) => void;
  onDelete: () => void;
  schema: DslSchema;
  mode: "entry" | "exit";
  lastSymbol: string;
}): React.ReactElement {
  const [collapsed, setCollapsed] = useState(false);

  const updateGroupRow = (rowIdx: number, updated: CompRow): void => {
    const newRows = group.rows.map((r, i) => (i === rowIdx ? updated : r));
    onChangeGroup({ ...group, rows: newRows });
  };

  const deleteGroupRow = (rowIdx: number): void => {
    const newRows = group.rows.filter((_, i) => i !== rowIdx);
    onChangeGroup({ ...group, rows: newRows });
  };

  const addGroupCompRow = (): void => {
    const lastGroupRow = group.rows.length > 0 ? group.rows[group.rows.length - 1] : undefined;
    const sym = lastGroupRow
      ? (extractSymbol(lastGroupRow.left) ?? lastSymbol)
      : lastSymbol;
    onChangeGroup({
      ...group,
      rows: [...group.rows, defaultCompRow(schema, sym)],
    });
  };

  return (
    <div
      className="rounded-md border border-dashed bg-muted/10 px-3 py-2"
      data-testid={`group-row-${groupIdx}`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className="flex rounded-md border text-xs">
            {(["and", "or"] as const).map((op) => (
              <button
                key={op}
                type="button"
                onClick={() => onChangeGroup({ ...group, op })}
                className={cn(
                  "px-2 py-0.5 first:rounded-l-md last:rounded-r-md",
                  group.op === op
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent",
                )}
              >
                {op.toUpperCase()}
              </button>
            ))}
          </div>
          <span className="text-[11px] text-muted-foreground">
            Group ({group.rows.length} condition{group.rows.length !== 1 ? "s" : ""})
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label={collapsed ? "Expand group" : "Collapse group"}
            onClick={() => setCollapsed((c) => !c)}
            className="rounded p-1 text-muted-foreground hover:bg-accent"
          >
            {collapsed ? (
              <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
            ) : (
              <ChevronUp className="h-3.5 w-3.5" aria-hidden="true" />
            )}
          </button>
          <button
            type="button"
            aria-label="Remove group"
            onClick={onDelete}
            className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
          >
            <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>

      {!collapsed && (
        <div className="mt-2 space-y-2">
          {group.rows.map((row, rowIdx) => (
            <CompRowEditor
              key={row.id}
              row={row}
              rowIdx={rowIdx}
              onChangeRow={(r) => updateGroupRow(rowIdx, r)}
              onDelete={() => deleteGroupRow(rowIdx)}
              schema={schema}
              mode={mode}
            />
          ))}
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 gap-1 text-xs"
            onClick={addGroupCompRow}
          >
            <Plus className="h-3 w-3" aria-hidden="true" />
            Add condition
          </Button>
        </div>
      )}
    </div>
  );
}
