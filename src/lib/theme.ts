export type Theme = "light" | "dark";

// index.html reads the same key before first paint, so keep the two in sync.
const STORAGE_KEY = "nullmap-theme";

function isTheme(value: unknown): value is Theme {
  return value === "light" || value === "dark";
}

/** The explicit choice if one was made, otherwise what the OS asks for. */
export function currentTheme(): Theme {
  const forced = document.documentElement.dataset.theme;
  if (isTheme(forced)) return forced;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Private windows can refuse storage; the choice then lasts for this page only.
  }
}
