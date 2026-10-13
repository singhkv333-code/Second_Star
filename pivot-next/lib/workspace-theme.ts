/** The shared browser runtime owns color validation, persistence and both UIs. */
export type WorkspacePalette = {
  id: string; name: string; finish: "flat" | "wash" | "grain";
  surface: string; chrome: string; border: string; accent: string; up: string; down: string;
};
declare global {
  interface Window {
    PivotWorkspaceTheme?: {
      getTheme: () => WorkspacePalette;
      setTheme: (theme: WorkspacePalette, persist?: boolean) => boolean;
      presets: WorkspacePalette[];
      openPicker: (anchor?: HTMLElement | null) => void;
      apply: () => void;
      chartPalette: () => { dark: boolean };
    };
  }
}
export function openWorkspaceColors(anchor?: HTMLElement | null): void {
  window.PivotWorkspaceTheme?.openPicker(anchor);
}
export function workspaceMode(): "dark" | "light" {
  return typeof window !== "undefined" && window.PivotWorkspaceTheme?.chartPalette().dark ? "dark" : "light";
}
