"use client";

import { useEffect, useState } from "react";
import { getDslSchema } from "@/lib/api";
import { isError } from "@/lib/types";
import type { DslSchema, ErrorBody } from "@/lib/types";

type DslSchemaState =
  | { status: "loading" }
  | { status: "ready"; schema: DslSchema }
  | { status: "error"; error: ErrorBody };

/**
 * Loads the DSL condition-tree schema (GET /api/workflows/dsl/schema).
 * Falls back to the inline mock when the backend is unreachable.
 * 5-min cache lives inside getDslSchema().
 */
export function useDslSchema(): DslSchemaState {
  const [state, setState] = useState<DslSchemaState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const result = await getDslSchema();
      if (cancelled) return;
      if (isError(result)) {
        setState({ status: "error", error: result.error });
      } else {
        setState({ status: "ready", schema: result.data });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
