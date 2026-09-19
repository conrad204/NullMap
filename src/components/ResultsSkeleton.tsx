import { Check } from "@phosphor-icons/react";
import type { SearchProgress, SearchStage } from "../types";
import { cx, formatCount } from "../lib/format";

const STAGES: Array<{ id: SearchStage; label: string }> = [
  { id: "keywords", label: "Extract hypotheses and null-signal phrases" },
  { id: "searching", label: "Query OpenAlex, arXiv, PubMed and registries" },
  { id: "classifying", label: "Assign a verdict to each prior attempt" },
  { id: "estimating", label: "Estimate expected value of pursuit" },
];

export default function ResultsSkeleton({ progress }: { progress: SearchProgress | null }) {
  const currentIndex = progress ? STAGES.findIndex((s) => s.id === progress.stage) : 0;

  return (
    <div className="fade-up flex flex-col gap-10" aria-busy="true">
      <ol className="flex flex-col gap-3">
        {STAGES.map((s, i) => {
          const done = i < currentIndex;
          const active = i === currentIndex;
          return (
            <li key={s.id} className="flex items-center gap-3 text-sm">
              <span
                className={cx(
                  "flex h-5 w-5 shrink-0 items-center justify-center rounded-full border transition-colors",
                  done && "border-accent bg-accent text-on-accent",
                  active && "border-accent",
                  !done && !active && "border-line",
                )}
                aria-hidden
              >
                {done && <Check size={12} weight="bold" />}
                {active && <span className="h-2 w-2 rounded-full bg-accent" />}
              </span>
              <span className={cx(active ? "text-ink" : done ? "text-ink-2" : "text-ink-3")}>
                {s.label}
              </span>
              {active && progress?.scanned !== undefined && (
                <span className="font-mono text-xs tabular-nums text-ink-3">
                  {formatCount(progress.scanned)} records
                </span>
              )}
            </li>
          );
        })}
      </ol>

      <div className="flex flex-col gap-4">
        <div className="skeleton h-4 w-40" />
        <div className="skeleton h-3 w-full" />
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 lg:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex flex-col gap-2">
              <div className="skeleton h-6 w-8" />
              <div className="skeleton h-3 w-24" />
            </div>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-1 gap-8 md:grid-cols-[1fr_minmax(260px,320px)]">
        <div className="flex flex-col gap-3">
          <div className="skeleton h-4 w-24" />
          <div className="skeleton h-3 w-full" />
          <div className="skeleton h-3 w-[92%]" />
          <div className="skeleton h-3 w-[88%]" />
          <div className="skeleton h-3 w-[60%]" />
        </div>
        <div className="skeleton h-56 w-full rounded-panel" />
      </div>
    </div>
  );
}
