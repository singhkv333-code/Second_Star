"use client";

import { useEffect, useState } from "react";
import { getStepTypes } from "@/lib/api";
import { isError } from "@/lib/types";
import type { ErrorBody, StepTypeCatalog } from "@/lib/types";

type CatalogState =
  | { status: "loading" }
  | { status: "ready"; catalog: StepTypeCatalog }
  | { status: "error"; error: ErrorBody };

/**
 * Loads the step-type catalog (mock until Day 5; real `/api/step-types`
 * after `setStepTypesSource("real")`). 5-min cache lives inside `getStepTypes`.
 */
export function useStepCatalog(): CatalogState {
  const [state, setState] = useState<CatalogState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const result = await getStepTypes();
      if (cancelled) return;
      if (isError(result)) {
        setState({ status: "error", error: result.error });
      } else {
        setState({ status: "ready", catalog: result.data });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
