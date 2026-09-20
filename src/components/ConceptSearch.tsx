import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowRight, Minus, Plus, X } from "@phosphor-icons/react";
import type { Concept, ConceptMatch, ConceptSearchResult, ConceptSign } from "../types";
import { searchConcepts } from "../api/client";
import {
  SIGN_META, addSignedConcepts, conceptError, cosineWidth, expression,
  flipConcept, matchSignals, removeConcept, toRequest,
} from "../lib/concepts";
import { VERDICT_META } from "../lib/verdicts";
import { cx } from "../lib/format";

const EXAMPLES: { label: string; terms: string[] }[] = [
  { label: "SGLT2 inhibitor + kidney outcomes − type 2 diabetes", terms: ["SGLT2 inhibitor", "kidney outcomes", "−type 2 diabetes"] },
  { label: "blood pressure lowering − antihypertensive drug", terms: ["blood pressure lowering", "−antihypertensive drug"] },
  { label: "dialysis + quality of life", terms: ["dialysis", "quality of life"] },
];

type State =
  | { kind: "idle" }
  | { kind: "searching" }
  | { kind: "done"; result: ConceptSearchResult }
  | { kind: "error"; message: string };

/**
 * Concept arithmetic over the indexed corpus: the document-level form of
 * "king − man + woman". Concepts are typed into one field, each kept or pushed
 * away, and the corpus decides what actually sits in that direction.
 */
export default function ConceptSearch({ hidden }: { hidden: boolean }) {
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [draft, setDraft] = useState("");
  const [nextSign, setNextSign] = useState<ConceptSign>("positive");
  const [state, setState] = useState<State>({ kind: "idle" });
  const [error, setError] = useState<string | null>(null);
  const [searched, setSearched] = useState("");
  const fieldRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  // The form stays mounted while the other mode is shown; focus it again on return.
  const wasHidden = useRef(hidden);
  useEffect(() => {
    if (wasHidden.current && !hidden) fieldRef.current?.focus();
    wasHidden.current = hidden;
  }, [hidden]);

  const run = useCallback(async (query: Concept[]) => {
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

  const commit = (raw: string) => {
    if (!raw.trim()) return concepts;
    const next = addSignedConcepts(concepts, raw, nextSign);
    setConcepts(next);
    setDraft("");
    setNextSign("positive");
    setError(null);
    return next;
  };

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (hidden) return;
    // A term still being typed is part of the expression the user sees, so it is searched too.
    void run(commit(draft));
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      if (!draft.trim()) return;
      event.preventDefault();
      commit(draft);
    } else if (event.key === "Backspace" && !draft && concepts.length) {
      setConcepts(concepts.slice(0, -1));
    } else if ((event.key === "-" || event.key === "−") && !draft) {
      event.preventDefault();
      setNextSign("negative");
    }
  }

  const line = expression(concepts);
  const stale = state.kind === "done" && searched !== line;
  const meta = SIGN_META[nextSign];

  return (
    <div hidden={hidden} className="fade-up mx-auto flex w-full max-w-[720px] flex-col gap-6">
      <form onSubmit={submit} className="flex flex-col gap-2">
        <div className={"prompt-box" + (error ? " prompt-box-invalid" : "")}>
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
            <span className="flex min-w-[14rem] flex-1 items-center gap-2">
              <button type="button" onClick={() => setNextSign(nextSign === "positive" ? "negative" : "positive")}
                title={nextSign === "positive" ? "Next concept is searched towards. Click to push away instead." : "Next concept is pushed away. Click to search towards it instead."}
                aria-label={`Next concept: ${meta.label}`}
                className={cx("flex h-5 w-5 shrink-0 items-center justify-center rounded-mark text-xs font-semibold text-[var(--on-accent)]", meta.bg)}>
                {meta.symbol}
              </button>
              <label htmlFor="concept-input" className="sr-only">Add a concept</label>
              <input ref={fieldRef} id="concept-input" value={draft} maxLength={200} autoComplete="off"
                placeholder={concepts.length ? "another concept…" : "chronic kidney disease"}
                aria-invalid={error ? "true" : undefined} aria-describedby="concept-help"
                onChange={(event) => setDraft(event.target.value)} onKeyDown={onKeyDown} onBlur={() => commit(draft)}
                className="w-full min-w-0 bg-transparent py-1 text-base leading-relaxed text-ink placeholder:text-ink-3 focus:outline-none" />
            </span>
          </div>
          <div className="flex items-center justify-between gap-3 px-3 pb-3 pt-2">
            <p id="concept-help" className="min-w-0 pl-1 text-xs text-ink-3">
              {line ? (
                <>Searching near <span className="font-mono break-all text-ink-2">{line}</span></>
              ) : (
                <>Enter adds a concept; start it with <span className="font-mono">−</span> to push it away.</>
              )}
            </p>
            {/* Committing the draft on blur would move the button out from under a click already on its way. */}
            <button type="submit" disabled={state.kind === "searching"} onMouseDown={(event) => event.preventDefault()} className="btn btn-primary shrink-0">
              {state.kind === "searching" ? "Searching…" : "Search concepts"}
              <ArrowRight size={16} weight="bold" aria-hidden />
            </button>
          </div>
        </div>
        {error && <p role="alert" className="text-sm text-v-failed">{error}</p>}
      </form>

      {!concepts.length && (
        <div>
          <p className="mb-2 text-sm text-ink-3">Or start from a combination</p>
          <ul className="flex flex-col gap-1.5">
            {EXAMPLES.map((example) => (
              <li key={example.label}>
                <button type="button" className="text-left font-mono text-sm leading-snug text-ink-2 underline-offset-4 transition-colors hover:text-accent hover:underline"
                  onClick={() => {
                    setConcepts(addSignedConcepts([], example.terms.join(","), "positive"));
                    setError(null);
                    fieldRef.current?.focus();
                  }}>
                  {example.label}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div aria-live="polite" className="min-w-0">
        {state.kind === "error" && <p role="alert" className="text-sm text-v-failed">{state.message}</p>}
        {stale && <p className="mb-2 text-sm text-ink-3">Showing results for <span className="font-mono text-[0.9em] break-all">{searched}</span>. Search again for the combination above.</p>}
        {state.kind === "done" && <ConceptResults result={state.result} />}
      </div>
    </div>
  );
}

function ConceptResults({ result }: { result: ConceptSearchResult }) {
  if (!result.matches.length) {
    return (
      <p className="text-sm leading-relaxed text-ink-2">
        Nothing in the indexed corpus sits near that combination. That is a statement about this
        index, not about the literature.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {result.warnings?.map((warning) => (
        <p key={warning} className="rounded-control border border-line bg-surface-2 p-3 text-sm text-ink-2">{warning}</p>
      ))}
      <p className="text-sm text-ink-3">
        {result.matches.length} nearest indexed studies, by cosine to the combined direction. Cosine is a
        similarity, not a relevance score or a probability.
      </p>
      <ul className="flex flex-col gap-2">
        {result.matches.map((match) => <ConceptRow key={match.id} match={match} />)}
      </ul>
    </div>
  );
}

function ConceptRow({ match }: { match: ConceptMatch }) {
  const { contested } = matchSignals(match);
  const verdict = VERDICT_META[match.verdict];
  return (
    <li className="rounded-panel border border-line bg-surface p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 flex-1 break-words font-medium leading-snug text-ink">
          {match.url ? (
            <a href={match.url} target="_blank" rel="noreferrer" className="underline-offset-4 hover:text-accent hover:underline">{match.title}</a>
          ) : match.title}
        </p>
        <span className="font-mono text-xs text-ink-3">cos {match.cosine.toFixed(2)}</span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-3">
        {match.year != null && <span>{match.year}</span>}
        <span className={verdict.text}>{verdict.label}</span>
        <span>{match.citations} citations</span>
        {contested && (
          <span className="text-v-failed">Also close to a negative concept</span>
        )}
      </div>
      <ul className="mt-2 flex flex-col gap-1">
        {match.concepts.map((concept) => {
          const meta = SIGN_META[concept.sign];
          return (
            <li key={`${concept.sign}:${concept.text}`} className="grid grid-cols-[auto_minmax(0,10rem)_5rem_auto] items-center gap-2 text-xs">
              <span aria-hidden className={cx("font-mono", meta.text)}>{meta.symbol}</span>
              <span className="truncate text-ink-2">{concept.text}</span>
              <span aria-hidden className="h-1.5 rounded-mark bg-surface-2">
                <span className={cx("block h-full rounded-mark", meta.bg)} style={{ width: `${cosineWidth(concept.cosine) * 100}%` }} />
              </span>
              <span className="font-mono text-ink-3">{concept.cosine.toFixed(2)}</span>
            </li>
          );
        })}
      </ul>
    </li>
  );
}
