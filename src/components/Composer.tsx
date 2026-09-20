import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowRight, Minus, Plus, X } from "@phosphor-icons/react";
import type { Concept, ConceptSearchResult, ConceptSign, SearchRequest } from "../types";
import type { GlideOrigin } from "./QuestionHeader";
import ConceptResults from "./ConceptResults";
import FilterControls from "./FilterControls";
import { searchConcepts } from "../api/client";
import {
  SIGN_META, addSignedConcepts, conceptError, expression, flipConcept,
  isSignedTerm, removeConcept, toRequest,
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
const COMBINATIONS: { label: string; terms: string[] }[] = [
  { label: "SGLT2 inhibitor + kidney outcomes − type 2 diabetes", terms: ["+SGLT2 inhibitor", "+kidney outcomes", "−type 2 diabetes"] },
  { label: "blood pressure lowering − antihypertensive drug", terms: ["+blood pressure lowering", "−antihypertensive drug"] },
  { label: "dialysis + quality of life", terms: ["+dialysis", "+quality of life"] },
];
const DEFAULTS = { plannedN: 200, alpha: 0.05, valueSuccess: 100, valueNull: 20, studyCost: 30 };

type ConceptState =
  | { kind: "idle" }
  | { kind: "searching" }
  | { kind: "done"; result: ConceptSearchResult }
  | { kind: "error"; message: string };

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
 * One search box for both ways of asking. Plain prose is a research question and
 * maps the literature; signing a term (`−diabetes`) turns it into a concept chip
 * and the same box searches near `sum(positive) − sum(negative)` instead. The
 * chips are the only state that decides which search runs, so there is no mode
 * to pick and nothing to switch between.
 */
export default function Composer({ hidden, onSubmit, filters, onFiltersChange }: Props) {
  const [text, setText] = useState("");
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [nextSign, setNextSign] = useState<ConceptSign>("positive");
  const [error, setError] = useState<string | null>(null);
  const [filterProblem, setFilterProblem] = useState<string | null>(null);
  const [state, setState] = useState<ConceptState>({ kind: "idle" });
  const [searched, setSearched] = useState("");
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  // The form stays mounted while a search runs so the draft survives; focus it again on return.
  const wasHidden = useRef(hidden);
  useEffect(() => {
    if (wasHidden.current && !hidden) fieldRef.current?.focus();
    wasHidden.current = hidden;
  }, [hidden]);

  const runConcepts = useCallback(async (query: Concept[]) => {
    const problem = conceptError(query);
    setError(problem);
    if (problem) return;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setSearched(expression(query));
    setState({ kind: "searching" });
    try {
      const result = await searchConcepts(toRequest(query), ac.signal);
      if (!ac.signal.aborted) setState({ kind: "done", result });
    } catch (err) {
      if (ac.signal.aborted) return;
      setState({ kind: "error", message: err instanceof Error ? err.message : "Concept search failed." });
    }
  }, []);

  /** Turn what is typed into chips. Returns the resulting list so a submit can search it at once. */
  const commit = (raw: string) => {
    if (!raw.trim()) return concepts;
    const next = addSignedConcepts(concepts, raw, nextSign);
    setConcepts(next);
    setText("");
    setNextSign("positive");
    setError(null);
    return next;
  };

  // A chip in the box means the box is arithmetic; so does a term the user has just signed.
  const arithmetic = concepts.length > 0 || isSignedTerm(text);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (hidden) return;
    if (arithmetic) {
      // A term still being typed is part of the expression on screen, so it is searched too.
      void runConcepts(commit(text));
      return;
    }
    if (text.trim().length < 20) {
      setError("Describe your question in at least 20 characters, including the intervention and outcome.");
      return;
    }
    if (!event.currentTarget.reportValidity()) return;
    const problem = filterError(filters.draft);
    setFilterProblem(problem);
    if (problem) return;
    setError(null);
    onSubmit({ idea: text.trim(), field: "Medicine and health", ...DEFAULTS, filters: parseFilters(filters.draft) }, fieldRef.current ? textOrigin(fieldRef.current) : null);
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.nativeEvent.isComposing) return;
    // Once the box holds concepts, Enter and comma add another; otherwise Enter asks the question.
    if ((event.key === "Enter" && !event.shiftKey) || event.key === ",") {
      if (!arithmetic || !text.trim()) {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          event.currentTarget.form?.requestSubmit();
        }
        return;
      }
      event.preventDefault();
      commit(text);
    } else if (event.key === "Backspace" && !text && concepts.length) {
      setConcepts(concepts.slice(0, -1));
    } else if ((event.key === "-" || event.key === "−") && !text && concepts.length) {
      event.preventDefault();
      setNextSign("negative");
    }
  }

  const line = expression(concepts);
  const stale = state.kind === "done" && searched !== line;
  const meta = SIGN_META[nextSign];

  return (
    <div className="flex flex-col gap-6">
      <div className="mx-auto w-full max-w-[720px] text-center">
        <h1 className="text-balance text-3xl font-semibold leading-[1.05] tracking-tight text-ink sm:text-4xl">Check the file drawer before you run the study.</h1>
        <p className="mx-auto mt-3 max-w-[58ch] text-balance leading-relaxed text-ink-2">
          Ask a research question to map prior studies and separate confirmed no-difference results
          from uncertain ones — or sign a term to combine concepts instead, the way
          king <span className="font-mono">−</span> man <span className="font-mono">+</span> woman lands near queen.
        </p>
      </div>

      <form onSubmit={handleSubmit} noValidate hidden={hidden} className="fade-up mx-auto w-full max-w-[720px] flex flex-col gap-6">
        <div className="flex flex-col gap-2">
          <div className={"prompt-box" + (error ? " prompt-box-invalid" : "")}>
            {concepts.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5 px-3 pt-3">
                {concepts.map((concept) => {
                  const chip = SIGN_META[concept.sign];
                  return (
                    <span key={concept.id} className={cx("flex max-w-full items-center gap-1 rounded-control py-1 pl-2 pr-1 text-sm text-ink", chip.tint)}>
                      <span aria-hidden className={cx("font-mono text-xs", chip.text)}>{chip.symbol}</span>
                      <span className="max-w-[24ch] truncate">{concept.text}</span>
                      <button type="button" onClick={() => setConcepts((current) => flipConcept(current, concept.id))}
                        title={concept.sign === "positive" ? "Push this concept away instead" : "Search towards this concept instead"}
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
                <button type="button" onClick={() => { setNextSign(nextSign === "positive" ? "negative" : "positive"); fieldRef.current?.focus(); }}
                  title={nextSign === "positive" ? "Next concept is searched towards. Click to push away instead." : "Next concept is pushed away. Click to search towards it instead."}
                  aria-label={`Next concept: ${meta.label}`}
                  className={cx("flex h-5 w-5 shrink-0 items-center justify-center rounded-mark text-xs font-semibold text-[var(--on-accent)]", meta.bg)}>
                  {meta.symbol}
                </button>
              </div>
            )}
            <label htmlFor="idea" className="sr-only">Your research question, or a concept to combine</label>
            <textarea ref={fieldRef} id="idea" name="idea" rows={concepts.length ? 1 : 3} value={text} maxLength={10000} autoFocus
              onChange={(event) => setText(event.target.value)} onKeyDown={onKeyDown}
              placeholder={concepts.length ? "another concept…" : "Does X change Y in population Z?"}
              aria-invalid={error ? "true" : undefined} aria-describedby="idea-help"
              className="block w-full resize-none bg-transparent px-4 pt-3.5 text-base leading-relaxed text-ink placeholder:text-ink-3 focus:outline-none" />
            <div className="flex items-center justify-between gap-3 px-3 pb-3 pt-1">
              <p id="idea-help" className="min-w-0 pl-1 text-xs text-ink-3">
                {arithmetic ? (
                  <>Searching near <span className="font-mono break-all text-ink-2">{line || expression(addSignedConcepts([], text, nextSign))}</span>{concepts.length ? "; Enter adds another concept" : ""}</>
                ) : (
                  <>OpenAlex literature and ClinicalTrials.gov records. Start a term with <span className="font-mono">−</span> or <span className="font-mono">+</span> to combine concepts instead.</>
                )}
              </p>
              {/* Committing the draft on blur would move the button out from under a click already on its way. */}
              <button type="submit" disabled={state.kind === "searching"} onMouseDown={(event) => event.preventDefault()} className="btn btn-primary shrink-0">
                {state.kind === "searching" ? "Searching…" : arithmetic ? "Search concepts" : "Map the literature"}
                <ArrowRight size={16} weight="bold" aria-hidden />
              </button>
            </div>
          </div>
          {error && <p role="alert" className="text-sm text-v-failed">{error}</p>}
        </div>

        {!concepts.length && !text.trim() && (
          <div className="flex flex-col gap-4">
            <div>
              <p className="mb-2 text-sm text-ink-3">Or start from a clinical question</p>
              <ul className="flex flex-col gap-1.5">{QUESTIONS.map((example) => <li key={example}>
                <button type="button" onClick={() => { setText(example); setError(null); fieldRef.current?.focus(); }}
                  className="text-left text-sm leading-snug text-ink-2 underline-offset-4 transition-colors hover:text-accent hover:underline">{example}</button>
              </li>)}</ul>
            </div>
            <div>
              <p className="mb-2 text-sm text-ink-3">Or combine concepts</p>
              <ul className="flex flex-col gap-1.5">{COMBINATIONS.map((example) => <li key={example.label}>
                <button type="button" className="text-left font-mono text-sm leading-snug text-ink-2 underline-offset-4 transition-colors hover:text-accent hover:underline"
                  onClick={() => { setConcepts(addSignedConcepts([], example.terms.join(","), "positive")); setError(null); fieldRef.current?.focus(); }}>
                  {example.label}
                </button>
              </li>)}</ul>
            </div>
          </div>
        )}

        {/* Re-validate only once a problem is on screen, so a half-typed year is not an error. */}
        <FilterControls state={filters} onChange={(next) => { onFiltersChange(next); if (filterProblem) setFilterProblem(filterError(next.draft)); }} error={filterProblem} />
      </form>

      <div aria-live="polite" hidden={hidden} className="mx-auto min-w-0 w-full max-w-[720px]">
        {state.kind === "error" && <p role="alert" className="text-sm text-v-failed">{state.message}</p>}
        {stale && <p className="mb-2 text-sm text-ink-3">Showing results for <span className="font-mono text-[0.9em] break-all">{searched}</span>. Search again for the combination above.</p>}
        {state.kind === "done" && <ConceptResults result={state.result} />}
      </div>
    </div>
  );
}
