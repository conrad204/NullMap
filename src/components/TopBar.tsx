import { cx } from "../lib/format";

export type Mode = "search" | "contribute";

interface Props {
  mode: Mode;
  onModeChange: (m: Mode) => void;
  sampleData: boolean;
}

const MODES: Array<{ id: Mode; label: string }> = [
  { id: "search", label: "Search" },
  { id: "contribute", label: "Contribute" },
];

export default function TopBar({ mode, onModeChange, sampleData }: Props) {
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

        <div className="hidden min-w-[110px] justify-end sm:flex">
          {sampleData && (
            <span
              title="No backend configured. Set VITE_API_URL to use real data."
              className="rounded-[6px] border border-line bg-surface-2 px-2 py-1 font-mono text-xs text-ink-2"
            >
              sample data
            </span>
          )}
        </div>
      </div>
    </header>
  );
}
