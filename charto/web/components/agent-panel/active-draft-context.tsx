"use client";

/**
 * ActiveDraftContext — shared state for the workflow draft currently open
 * in the AgentPanel editor.
 *
 * Lives at AppShell level so ChatDemo (deep in DashboardTab) and AgentPanel
 * can both read/write the same draft without prop-drilling every intermediate
 * component.
 *
 * Only UNSAVED drafts (id "" or "local-…", status "draft") are controlled
 * here. Saved/activated workflows are fetched from the server and don't need
 * this channel.
 */

import { createContext, useContext, type Dispatch, type SetStateAction } from "react";
import type { Workflow } from "@/lib/types";

export type ActiveDraftContextValue = {
  /** The draft currently open in the editor, or null when the panel is
   * closed / showing a saved workflow. */
  activeEditorDraft: Workflow | null;
  setActiveEditorDraft: Dispatch<SetStateAction<Workflow | null>>;
  /** True when the AgentPanel is open and bound to an unsaved draft. */
  panelOpenWithDraft: boolean;
};

export const ActiveDraftContext = createContext<ActiveDraftContextValue>({
  activeEditorDraft: null,
  setActiveEditorDraft: () => undefined,
  panelOpenWithDraft: false,
});

export function useActiveDraft(): ActiveDraftContextValue {
  return useContext(ActiveDraftContext);
}
