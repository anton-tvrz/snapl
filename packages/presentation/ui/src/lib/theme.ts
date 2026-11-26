export type Theme = "system" | "light" | "dark";

const KEY = "snapl.theme";
const THEMES: readonly Theme[] = ["system", "light", "dark"];

/** The remembered theme. Storage can be blocked, so it is never relied on. */
export function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(KEY);
    return THEMES.includes(stored as Theme) ? (stored as Theme) : "system";
  } catch {
    return "system";
  }
}

export function storeTheme(theme: Theme): void {
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    // A private window or blocked storage: the choice lasts for this page only.
  }
}

/** "system" clears the mark, and the stylesheet then follows the system setting. */
export function applyTheme(theme: Theme): void {
  if (theme === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = theme;
}
