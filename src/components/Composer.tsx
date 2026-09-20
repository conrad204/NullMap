import { useState } from "react";
import type { SearchRequest } from "../types";
import type { GlideOrigin } from "./QuestionHeader";
import IdeaComposer from "./IdeaComposer";
import ConceptSearch from "./ConceptSearch";
import type { FilterState } from "../lib/filters";
import { cx } from "../lib/format";

interface Props {
  hidden: boolean;
  onSubmit: (req: SearchRequest, origin: GlideOrigin | null) => void;
  filters: FilterState;
  onFiltersChange: (filters: FilterState) => void;
}

type Mode = "question" | "concepts";

const MODES: { mode: Mode; label: string; hint: string }[] = [
  { mode: "question", label: "Ask a question", hint: "Map prior studies, separate confirmed no-difference results from uncertain ones, and plan your next study." },
  { mode: "concepts", label: "Combine concepts", hint: "Search near the sum of the concepts you keep minus the ones you push away, the way king − man + woman lands near queen. Nothing is matched literally." },
];

/** One search box with two ways to aim it: a question, or concept arithmetic. */
export default function Composer({ hidden, onSubmit, filters, onFiltersChange }: Props) {
  const [mode, setMode] = useState<Mode>("question");
  const hint = MODES.find((entry) => entry.mode === mode)?.hint;
  return (
    <div className="flex flex-col gap-6">
      <div className="mx-auto w-full max-w-[720px] text-center">
        <h1 className="text-balance text-3xl font-semibold leading-[1.05] tracking-tight text-ink sm:text-4xl">Check the file drawer before you run the study.</h1>
        <div role="tablist" aria-label="How to search" className="mt-5 inline-flex rounded-control border border-line bg-surface-2 p-1">
          {MODES.map((entry) => (
            <button key={entry.mode} type="button" role="tab" aria-selected={mode === entry.mode} onClick={() => setMode(entry.mode)}
              className={cx("rounded-mark px-3 py-1.5 text-sm transition-colors",
                mode === entry.mode ? "bg-surface text-ink shadow-sm" : "text-ink-3 hover:text-ink-2")}>
              {entry.label}
            </button>
          ))}
        </div>
        <p className="mx-auto mt-3 max-w-[58ch] text-balance leading-relaxed text-ink-2">{hint}</p>
      </div>
      {/* Both stay mounted so switching modes never throws away a draft or a result. */}
      <IdeaComposer hidden={hidden || mode !== "question"} onSubmit={onSubmit} filters={filters} onFiltersChange={onFiltersChange} />
      <ConceptSearch hidden={hidden || mode !== "concepts"} />
    </div>
  );
}
