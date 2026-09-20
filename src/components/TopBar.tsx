import { useState } from "react";
import { Moon, Sun } from "@phosphor-icons/react";
import { cx } from "../lib/format";
import { applyTheme, currentTheme, type Theme } from "../lib/theme";

export type Mode = "search" | "map" | "contribute";

interface Props {
  mode: Mode;
  onModeChange: (m: Mode) => void;
  sampleData: boolean;
}

const MODES: Array<{ id: Mode; label: string }> = [
  { id: "search", label: "Search" },
  { id: "map", label: "Map" },
  { id: "contribute", label: "Contribute" },
];

export default function TopBar({ mode, onModeChange, sampleData }: Props) {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  const nextTheme: Theme = theme === "dark" ? "light" : "dark";
  const toggleTheme = () => {
    applyTheme(nextTheme);
    setTheme(nextTheme);
  };

  return (
    <header className="sticky top-0 z-20 h-16 border-b border-line bg-bg/85 backdrop-blur-md">
      <div className="mx-auto flex h-full w-full max-w-[1400px] items-center justify-between gap-6 px-4 sm:px-6 lg:px-8">
        <a href="/" className="text-[17px] font-semibold tracking-tight text-ink">
          null<span className="text-accent">Map</span>
        </a>

        <nav aria-label="Mode" className="flex items-center gap-1 rounded-control bg-surface-2 p-1">
          {MODES.map((m) => (
            <button
              key={m.id}
              type="button"
              onClick={() => onModeChange(m.id)}
              aria-pressed={mode === m.id}
              className={cx(
                "h-8 rounded-[6px] px-3.5 text-sm font-medium transition-colors",
                mode === m.id
                  ? "bg-surface text-ink shadow-panel"
                  : "text-ink-2 hover:text-ink",
              )}
            >
              {m.label}
            </button>
          ))}
        </nav>

        <div className="flex items-center justify-end gap-3">
          {sampleData && (
            <span
              title="Illustrative fixtures only. VITE_USE_MOCK=true was explicitly enabled."
              className="hidden rounded-[6px] border border-line bg-surface-2 px-2 py-1 font-mono text-xs text-ink-2 sm:block"
            >
              illustrative demo
            </span>
          )}
          <button
            type="button"
            onClick={toggleTheme}
            aria-label={`Switch to ${nextTheme} mode`}
            title={`Switch to ${nextTheme} mode`}
            className="flex h-8 w-8 items-center justify-center rounded-control text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            {nextTheme === "light" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </div>
      </div>
    </header>
  );
}
