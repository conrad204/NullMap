import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowRight } from "@phosphor-icons/react";
import type { SearchRequest } from "../types";
import type { GlideOrigin } from "./QuestionHeader";
import FilterControls from "./FilterControls";
import { filterError, parseFilters, type FilterState } from "../lib/filters";

interface Props {
  hidden: boolean;
  onSubmit: (req: SearchRequest, origin: GlideOrigin | null) => void;
  filters: FilterState;
  onFiltersChange: (filters: FilterState) => void;
}
const EXAMPLES = [
  "Does intermittent fasting improve working memory in healthy adults?",
  "Does vitamin D supplementation reduce depressive symptoms in adults?",
  "Does metformin improve survival in adults with lung cancer?",
];
const DEFAULTS = { plannedN: 200, alpha: 0.05, valueSuccess: 100, valueNull: 20, studyCost: 30 };

// Where the first typed character sits on screen, so the question header can start its glide there.
function textOrigin(field: HTMLTextAreaElement): GlideOrigin {
  const rect = field.getBoundingClientRect();
  const style = getComputedStyle(field);
  return {
    left: rect.left + parseFloat(style.borderLeftWidth) + parseFloat(style.paddingLeft),
    top: rect.top + parseFloat(style.borderTopWidth) + parseFloat(style.paddingTop) - field.scrollTop,
    fontSize: parseFloat(style.fontSize),
  };
}

export default function IdeaComposer({ hidden, onSubmit, filters, onFiltersChange }: Props) {
  const [idea, setIdea] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [filterProblem, setFilterProblem] = useState<string | null>(null);
  const fieldRef = useRef<HTMLTextAreaElement>(null);

  // The form stays mounted while a search runs so the draft survives; focus it again on return.
  const wasHidden = useRef(hidden);
  useEffect(() => {
    if (wasHidden.current && !hidden) fieldRef.current?.focus();
    wasHidden.current = hidden;
  }, [hidden]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (hidden) return;
    if (idea.trim().length < 20) {
      setError("Describe your question in at least 20 characters, including the intervention and outcome.");
      return;
    }
    if (!event.currentTarget.reportValidity()) return;
    const problem = filterError(filters.draft);
    setFilterProblem(problem);
    if (problem) return;
    setError(null);
    onSubmit({ idea: idea.trim(), field: "Medicine and health", ...DEFAULTS, filters: parseFilters(filters.draft) }, fieldRef.current ? textOrigin(fieldRef.current) : null);
  }
  function submitOnEnter(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    event.currentTarget.form?.requestSubmit();
  }
  return (
    <form onSubmit={handleSubmit} noValidate hidden={hidden} className="fade-up mx-auto w-full max-w-[720px] flex flex-col gap-6">
      <div className="text-center">
        <h1 className="text-balance text-3xl font-semibold leading-[1.05] tracking-tight text-ink sm:text-4xl">Check the file drawer before you run the study.</h1>
        <p className="mx-auto mt-3 max-w-[52ch] text-balance leading-relaxed text-ink-2">Map prior clinical studies, distinguish credible nulls from uncertain results, and plan your next study.</p>
      </div>
      <div className="flex flex-col gap-2">
        <div className={"prompt-box" + (error ? " prompt-box-invalid" : "")}>
          <label htmlFor="idea" className="sr-only">Your research question</label>
          <textarea ref={fieldRef} id="idea" name="idea" rows={3} value={idea} maxLength={10000} autoFocus
            onChange={(event) => setIdea(event.target.value)} onKeyDown={submitOnEnter} placeholder="Does X change Y in population Z?"
            aria-invalid={error ? "true" : undefined} aria-describedby="idea-help"
            className="block w-full resize-none bg-transparent px-4 pt-3.5 text-base leading-relaxed text-ink placeholder:text-ink-3 focus:outline-none" />
          <div className="flex items-center justify-between gap-3 px-3 pb-3 pt-1">
            <p id="idea-help" className="pl-1 text-xs text-ink-3">OpenAlex literature and ClinicalTrials.gov records.</p>
            <button type="submit" className="btn btn-primary shrink-0">Map the literature <ArrowRight size={16} weight="bold" aria-hidden /></button>
          </div>
        </div>
        {error && <p role="alert" className="text-sm text-v-failed">{error}</p>}
      </div>
      <div>
        <p className="mb-2 text-sm text-ink-3">Or start from a clinical question</p>
        <ul className="flex flex-col gap-1.5">{EXAMPLES.map((example) => <li key={example}>
          <button type="button" onClick={() => { setIdea(example); setError(null); fieldRef.current?.focus(); }} className="text-left text-sm leading-snug text-ink-2 underline-offset-4 transition-colors hover:text-accent hover:underline">{example}</button>
        </li>)}</ul>
      </div>
      {/* Re-validate only once a problem is on screen, so a half-typed year is not an error. */}
      <FilterControls state={filters} onChange={(next) => { onFiltersChange(next); if (filterProblem) setFilterProblem(filterError(next.draft)); }} error={filterProblem} />
    </form>
  );
}
