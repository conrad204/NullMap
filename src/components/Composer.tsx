import { useEffect, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowRight, Minus, Plus, X } from "@phosphor-icons/react";
import type { Concept, SearchRequest } from "../types";
import type { GlideOrigin } from "./QuestionHeader";
import FilterControls from "./FilterControls";
import {
  SIGN_META, composeLine, conceptError, flipConcept, parseLine, removeConcept, toSteer,
} from "../lib/concepts";
import { filterError, parseFilters, type FilterState } from "../lib/filters";
import { cx } from "../lib/format";

interface Props {
  hidden: boolean;
  onSubmit: (req: SearchRequest, origin: GlideOrigin | null) => void;
  filters: FilterState;
  onFiltersChange: (filters: FilterState) => void;
}

const QUESTIONS = [
  "Does azilsartan lower systolic blood pressure more than placebo in adults with essential hypertension?",
  "Does renal artery stenting improve blood pressure or kidney function in adults with atherosclerotic renal artery stenosis?",
  "Does bardoxolone methyl improve kidney function in adults with chronic kidney disease?",
];
const TAG_EXAMPLES = ["+kidney outcomes −type 2 diabetes", "+quality of life −mortality", "−animal model"];
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

/**
 * One search, one box. The line is the research question, as it always was,
 * and any signed terms trailing it — "…in adults? +blood pressure −stroke" —
 * are optional tags that ride on the same request and only steer the ranking
 * of that question's matches. The chips under the box are a reading of the
 * line, never a second field: editing one rewrites what was typed.
 */
export default function Composer({ hidden, onSubmit, filters, onFiltersChange }: Props) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [tagProblem, setTagProblem] = useState<string | null>(null);
  const [filterProblem, setFilterProblem] = useState<string | null>(null);
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const { question, concepts } = useMemo(() => parseLine(text), [text]);

  /** A chip is the line, read back, so changing one can only mean rewriting the line. */
  const rewrite = (next: Concept[]) => {
    setText(composeLine(question, next));
    setTagProblem(null);
    fieldRef.current?.focus();
  };

  // The form stays mounted while a search runs so the draft survives; focus it again on return.
  const wasHidden = useRef(hidden);
  useEffect(() => {
    if (wasHidden.current && !hidden) fieldRef.current?.focus();
    wasHidden.current = hidden;
  }, [hidden]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (hidden) return;
    // The tags are part of the line, so what is left of it is what gets searched.
    if (question.length < 20) {
      setError("Describe your question in at least 20 characters, including the intervention and outcome.");
      return;
    }
    setError(null);
    const tagged = conceptError(concepts);
    setTagProblem(tagged);
    if (tagged) return;
    if (!event.currentTarget.reportValidity()) return;
    const problem = filterError(filters.draft);
    setFilterProblem(problem);
    if (problem) return;
    onSubmit(
      {
        idea: question, field: "Medicine and health", ...DEFAULTS,
        filters: parseFilters(filters.draft), concepts: toSteer(concepts) ?? undefined,
      },
      fieldRef.current ? textOrigin(fieldRef.current) : null,
    );
  }

  function onQuestionKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing) return;
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  /** Examples type themselves into the same line, because there is nowhere else to type. */
  const append = (addition: string) => {
    setText((current) => `${current.trimEnd()}${current.trim() ? " " : ""}${addition} `);
    setError(null);
    fieldRef.current?.focus();
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="mx-auto w-full max-w-[720px] text-center">
        <h1 className="text-balance text-3xl font-semibold leading-[1.05] tracking-tight text-ink sm:text-4xl">Check the file drawer before you run the study.</h1>
        <p className="mx-auto mt-3 max-w-[58ch] text-balance leading-relaxed text-ink-2">
          Ask a research question to map prior studies and separate confirmed no-difference results
          from uncertain ones. End the question with <span className="font-mono">+</span> or{" "}
          <span className="font-mono">−</span> concepts to steer which of those studies rank highest.
        </p>
      </div>

      <form onSubmit={handleSubmit} noValidate hidden={hidden} className="fade-up mx-auto w-full max-w-[720px] flex flex-col gap-6">
        <div className="flex flex-col gap-2">
          <div className={"prompt-box" + (error ? " prompt-box-invalid" : "")}>
            <label htmlFor="idea" className="sr-only">Your research question</label>
            <textarea ref={fieldRef} id="idea" name="idea" rows={3} value={text} maxLength={10000} autoFocus
              onChange={(event) => setText(event.target.value)} onKeyDown={onQuestionKeyDown}
              placeholder="Does X change Y in population Z? +concept −concept"
              aria-invalid={error ? "true" : undefined} aria-describedby="idea-help"
              className="block w-full resize-none bg-transparent px-4 pt-3.5 text-base leading-relaxed text-ink placeholder:text-ink-3 focus:outline-none" />
            <div className="flex items-center justify-between gap-3 px-3 pb-3 pt-1">
              <p id="idea-help" className="min-w-0 pl-1 text-xs text-ink-3">OpenAlex literature and ClinicalTrials.gov records.</p>
              {/* Committing the draft on blur would move the button out from under a click already on its way. */}
              <button type="submit" onMouseDown={(event) => event.preventDefault()} className="btn btn-primary shrink-0">
                Map the literature
                <ArrowRight size={16} weight="bold" aria-hidden />
              </button>
            </div>
          </div>
          {error && <p role="alert" className="text-sm text-v-failed">{error}</p>}
        </div>

        {(concepts.length > 0 || tagProblem) && (
          <div className="-mt-3 flex flex-col gap-2">
            {/* A reading of the line, so the user can see how it was split before searching. */}
            <div className="flex flex-wrap items-center gap-1.5 pl-1">
              <span className="text-xs text-ink-3">Steering this search:</span>
              {concepts.map((concept) => {
                const chip = SIGN_META[concept.sign];
                return (
                  <span key={concept.id} className={cx("flex max-w-full items-center gap-1 rounded-control py-1 pl-2 pr-1 text-sm text-ink", chip.tint)}>
                    <span aria-hidden className={cx("font-mono text-xs", chip.text)}>{chip.symbol}</span>
                    <span className="max-w-[24ch] truncate">{concept.text}</span>
                    <button type="button" onClick={() => rewrite(flipConcept(concepts, concept.id))}
                      title={concept.sign === "positive" ? "Rank work about this lower instead" : "Rank work about this higher instead"}
                      aria-label={`Move "${concept.text}" to ${concept.sign === "positive" ? "negative" : "positive"} concepts`}
                      className="rounded-mark p-0.5 text-ink-3 transition-colors hover:text-accent">
                      {concept.sign === "positive" ? <Minus size={13} weight="bold" aria-hidden /> : <Plus size={13} weight="bold" aria-hidden />}
                    </button>
                    <button type="button" onClick={() => rewrite(removeConcept(concepts, concept.id))} aria-label={`Remove "${concept.text}"`}
                      className="rounded-mark p-0.5 text-ink-3 transition-colors hover:text-v-failed">
                      <X size={13} weight="bold" aria-hidden />
                    </button>
                  </span>
                );
              })}
              <span className="text-xs text-ink-3">· ranking only, no paper is removed</span>
            </div>
            {tagProblem && <p role="alert" className="text-sm text-v-failed">{tagProblem}</p>}
          </div>
        )}

        {!text.trim() && (
          <div className="flex flex-col gap-4">
            <div>
              <p className="mb-2 text-sm text-ink-3">Or start from a clinical question</p>
              <ul className="flex flex-col gap-1.5">{QUESTIONS.map((example) => <li key={example}>
                <button type="button" onClick={() => append(example)}
                  className="text-left text-sm leading-snug text-ink-2 underline-offset-4 transition-colors hover:text-accent hover:underline">{example}</button>
              </li>)}</ul>
            </div>
            <div>
              <p className="mb-2 text-sm text-ink-3">Then end the line with tags to steer it</p>
              <ul className="flex flex-col gap-1.5">{TAG_EXAMPLES.map((example) => <li key={example}>
                <button type="button" onClick={() => append(example)}
                  className="text-left font-mono text-sm leading-snug text-ink-2 underline-offset-4 transition-colors hover:text-accent hover:underline">
                  {example}
                </button>
              </li>)}</ul>
            </div>
          </div>
        )}

        {/* Re-validate only once a problem is on screen, so a half-typed year is not an error. */}
        <FilterControls state={filters} onChange={(next) => { onFiltersChange(next); if (filterProblem) setFilterProblem(filterError(next.draft)); }} error={filterProblem} />
      </form>
    </div>
  );
}
