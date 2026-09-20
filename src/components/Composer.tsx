import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowRight, Minus, Plus, X } from "@phosphor-icons/react";
import type { Concept, ConceptSign, SearchRequest } from "../types";
import type { GlideOrigin } from "./QuestionHeader";
import FilterControls from "./FilterControls";
import {
  SIGN_META, addSignedConcepts, conceptError, flipConcept, removeConcept, toSteer,
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
const TAG_EXAMPLES: { label: string; terms: string }[] = [
  { label: "+kidney outcomes −type 2 diabetes", terms: "+kidney outcomes, −type 2 diabetes" },
  { label: "+quality of life −mortality", terms: "+quality of life, −mortality" },
  { label: "−animal model", terms: "−animal model" },
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

/**
 * One search. The box is the research question, as it always was; the concept
 * tags below it are an optional add-on that rides on the same request and only
 * steers the ranking of that question's matches. There is nothing to switch
 * between: without tags this is the original search, with them it is the same
 * search read in a different order.
 */
export default function Composer({ hidden, onSubmit, filters, onFiltersChange }: Props) {
  const [text, setText] = useState("");
  const [tag, setTag] = useState("");
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [nextSign, setNextSign] = useState<ConceptSign>("positive");
  const [error, setError] = useState<string | null>(null);
  const [tagProblem, setTagProblem] = useState<string | null>(null);
  const [filterProblem, setFilterProblem] = useState<string | null>(null);
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const tagRef = useRef<HTMLInputElement>(null);

  // The form stays mounted while a search runs so the draft survives; focus it again on return.
  const wasHidden = useRef(hidden);
  useEffect(() => {
    if (wasHidden.current && !hidden) fieldRef.current?.focus();
    wasHidden.current = hidden;
  }, [hidden]);

  /** Turn what is typed into chips. Returns the resulting list so a submit can send it at once. */
  const commit = (raw: string) => {
    if (!raw.trim()) return concepts;
    const next = addSignedConcepts(concepts, raw, nextSign);
    setConcepts(next);
    setTag("");
    setNextSign("positive");
    setTagProblem(null);
    return next;
  };

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (hidden) return;
    // A tag still being typed is on screen as part of the search, so it is sent too.
    const tags = commit(tag);
    if (text.trim().length < 20) {
      setError("Describe your question in at least 20 characters, including the intervention and outcome.");
      return;
    }
    setError(null);
    const tagged = conceptError(tags);
    setTagProblem(tagged);
    if (tagged) return;
    if (!event.currentTarget.reportValidity()) return;
    const problem = filterError(filters.draft);
    setFilterProblem(problem);
    if (problem) return;
    onSubmit(
      {
        idea: text.trim(), field: "Medicine and health", ...DEFAULTS,
        filters: parseFilters(filters.draft), concepts: toSteer(tags) ?? undefined,
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

  function onTagKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing) return;
    if ((event.key === "Enter" || event.key === ",") && tag.trim()) {
      event.preventDefault();
      commit(tag);
    } else if (event.key === "Backspace" && !tag && concepts.length) {
      setConcepts(concepts.slice(0, -1));
    } else if ((event.key === "-" || event.key === "−") && !tag) {
      event.preventDefault();
      setNextSign("negative");
    }
  }

  const meta = SIGN_META[nextSign];

  return (
    <div className="flex flex-col gap-6">
      <div className="mx-auto w-full max-w-[720px] text-center">
        <h1 className="text-balance text-3xl font-semibold leading-[1.05] tracking-tight text-ink sm:text-4xl">Check the file drawer before you run the study.</h1>
        <p className="mx-auto mt-3 max-w-[58ch] text-balance leading-relaxed text-ink-2">
          Ask a research question to map prior studies and separate confirmed no-difference results
          from uncertain ones. Add concept tags to steer which of those studies rank highest.
        </p>
      </div>

      <form onSubmit={handleSubmit} noValidate hidden={hidden} className="fade-up mx-auto w-full max-w-[720px] flex flex-col gap-6">
        <div className="flex flex-col gap-2">
          <div className={"prompt-box" + (error ? " prompt-box-invalid" : "")}>
            <label htmlFor="idea" className="sr-only">Your research question</label>
            <textarea ref={fieldRef} id="idea" name="idea" rows={3} value={text} maxLength={10000} autoFocus
              onChange={(event) => setText(event.target.value)} onKeyDown={onQuestionKeyDown}
              placeholder="Does X change Y in population Z?"
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

        <div className="flex flex-col gap-2">
          <div className={cx("flex flex-wrap items-center gap-1.5 rounded-control border border-line bg-surface-2 px-2.5 py-2", tagProblem && "border-v-failed")}>
            {concepts.map((concept) => {
              const chip = SIGN_META[concept.sign];
              return (
                <span key={concept.id} className={cx("flex max-w-full items-center gap-1 rounded-control py-1 pl-2 pr-1 text-sm text-ink", chip.tint)}>
                  <span aria-hidden className={cx("font-mono text-xs", chip.text)}>{chip.symbol}</span>
                  <span className="max-w-[24ch] truncate">{concept.text}</span>
                  <button type="button" onClick={() => setConcepts((current) => flipConcept(current, concept.id))}
                    title={concept.sign === "positive" ? "Rank work about this lower instead" : "Rank work about this higher instead"}
                    aria-label={`Move "${concept.text}" to ${concept.sign === "positive" ? "negative" : "positive"} concepts`}
                    className="rounded-mark p-0.5 text-ink-3 transition-colors hover:text-accent">
                    {concept.sign === "positive" ? <Minus size={13} weight="bold" aria-hidden /> : <Plus size={13} weight="bold" aria-hidden />}
                  </button>
                  <button type="button" onClick={() => setConcepts((current) => removeConcept(current, concept.id))} aria-label={`Remove "${concept.text}"`}
                    className="rounded-mark p-0.5 text-ink-3 transition-colors hover:text-v-failed">
                    <X size={13} weight="bold" aria-hidden />
                  </button>
                </span>
              );
            })}
            <button type="button" onClick={() => { setNextSign(nextSign === "positive" ? "negative" : "positive"); tagRef.current?.focus(); }}
              title={nextSign === "positive" ? "Next tag ranks its concept higher. Click to rank it lower instead." : "Next tag ranks its concept lower. Click to rank it higher instead."}
              aria-label={`Next tag: ${meta.label}`}
              className={cx("flex h-5 w-5 shrink-0 items-center justify-center rounded-mark text-xs font-semibold text-[var(--on-accent)]", meta.bg)}>
              {meta.symbol}
            </button>
            <label htmlFor="concept-tag" className="sr-only">Concept tags that steer the ranking</label>
            <input ref={tagRef} id="concept-tag" name="concept-tag" type="text" value={tag} maxLength={200} autoComplete="off"
              onChange={(event) => setTag(event.target.value)} onKeyDown={onTagKeyDown}
              placeholder={concepts.length ? "another concept…" : "Steer the ranking: +kidney outcomes, −diabetes"}
              aria-invalid={tagProblem ? "true" : undefined} aria-describedby="concept-help"
              className="min-w-[18ch] flex-1 bg-transparent px-1 py-0.5 text-sm text-ink placeholder:text-ink-3 focus:outline-none" />
          </div>
          <p id="concept-help" className="pl-1 text-xs text-ink-3">
            Optional. Tags nudge the ranking of the same results towards <span className="font-mono">+</span> concepts and away
            from <span className="font-mono">−</span> ones — they never remove papers. Enter or a comma adds one.
          </p>
          {tagProblem && <p role="alert" className="text-sm text-v-failed">{tagProblem}</p>}
        </div>

        {!text.trim() && (
          <div className="flex flex-col gap-4">
            <div>
              <p className="mb-2 text-sm text-ink-3">Or start from a clinical question</p>
              <ul className="flex flex-col gap-1.5">{QUESTIONS.map((example) => <li key={example}>
                <button type="button" onClick={() => { setText(example); setError(null); fieldRef.current?.focus(); }}
                  className="text-left text-sm leading-snug text-ink-2 underline-offset-4 transition-colors hover:text-accent hover:underline">{example}</button>
              </li>)}</ul>
            </div>
            {!concepts.length && (
              <div>
                <p className="mb-2 text-sm text-ink-3">Steering tags to try alongside it</p>
                <ul className="flex flex-col gap-1.5">{TAG_EXAMPLES.map((example) => <li key={example.label}>
                  <button type="button" className="text-left font-mono text-sm leading-snug text-ink-2 underline-offset-4 transition-colors hover:text-accent hover:underline"
                    onClick={() => { setConcepts(addSignedConcepts(concepts, example.terms, "positive")); setTagProblem(null); tagRef.current?.focus(); }}>
                    {example.label}
                  </button>
                </li>)}</ul>
              </div>
            )}
          </div>
        )}

        {/* Re-validate only once a problem is on screen, so a half-typed year is not an error. */}
        <FilterControls state={filters} onChange={(next) => { onFiltersChange(next); if (filterProblem) setFilterProblem(filterError(next.draft)); }} error={filterProblem} />
      </form>
    </div>
  );
}
