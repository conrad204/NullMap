import { useCallback, useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowRight, Minus, Plus, X } from "@phosphor-icons/react";
import type { Concept, ConceptMatch, ConceptSearchResult, ConceptSign } from "../types";
import { searchConcepts } from "../api/client";
import {
  SIGNS, SIGN_META, addConcepts, bySign, conceptError, cosineWidth, expression,
  flipConcept, matchSignals, removeConcept, toRequest,
} from "../lib/concepts";
import { VERDICT_META } from "../lib/verdicts";
import { cx } from "../lib/format";

const EXAMPLES: { label: string; positive: string[]; negative: string[] }[] = [
  { label: "SGLT2 inhibitor + kidney outcomes, without diabetes", positive: ["SGLT2 inhibitor", "kidney outcomes"], negative: ["type 2 diabetes"] },
  { label: "Blood pressure lowering, without drug therapy", positive: ["blood pressure lowering"], negative: ["antihypertensive drug"] },
  { label: "Dialysis + quality of life", positive: ["dialysis", "quality of life"], negative: [] },
];

type State =
  | { kind: "idle" }
  | { kind: "searching" }
  | { kind: "done"; result: ConceptSearchResult }
  | { kind: "error"; message: string };

/**
 * Concept arithmetic over the indexed corpus: the document-level form of
 * "king − man + woman". Each concept is embedded, the positives are added, the
 * negatives subtracted, and the corpus decides what actually sits there.
 */
export default function ConceptSearch() {
  const [concepts, setConcepts] = useState<Concept[]>([]);
  const [state, setState] = useState<State>({ kind: "idle" });
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const run = useCallback(async (query: Concept[]) => {
    const problem = conceptError(query);
    setError(problem);
    if (problem) return;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setState({ kind: "searching" });
    try {
      const result = await searchConcepts(toRequest(query), ac.signal);
      if (!ac.signal.aborted) setState({ kind: "done", result });
    } catch (err) {
      if (ac.signal.aborted) return;
      setState({ kind: "error", message: err instanceof Error ? err.message : "Concept search failed." });
    }
  }, []);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void run(concepts);
  }

  const positive = bySign(concepts, "positive");
  const line = expression(concepts);

  return (
    <section aria-labelledby="concepts-heading" className="flex flex-col gap-6">
      <div>
        <h2 id="concepts-heading" className="text-sm font-medium text-ink-2">Search by concept</h2>
        <p className="mt-2 max-w-[58ch] leading-relaxed text-ink-2">
          Combine concepts instead of writing a question. Each one is embedded and the corpus is
          searched near their sum, the way <span className="font-mono text-[0.9em] text-ink">king − man + woman</span> lands
          near <span className="font-mono text-[0.9em] text-ink">queen</span>. Nothing is matched literally, and
          the corpus decides whether anything sits there.
        </p>
      </div>

      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="grid gap-4 sm:grid-cols-2">
          {SIGNS.map((sign) => (
            <ConceptField
              key={sign}
              sign={sign}
              concepts={bySign(concepts, sign)}
              onAdd={(raw) => { setConcepts((current) => addConcepts(current, raw, sign)); setError(null); }}
              onRemove={(id) => setConcepts((current) => removeConcept(current, id))}
              onFlip={(id) => setConcepts((current) => flipConcept(current, id))}
            />
          ))}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="min-w-0 text-sm text-ink-2">
            {positive.length ? (
              <>Searching near <span className="font-mono text-[0.9em] text-ink">{line}</span></>
            ) : (
              "Add a positive concept to search; negative concepts are optional."
            )}
          </p>
          <button type="submit" disabled={state.kind === "searching"} className="btn btn-primary shrink-0">
            {state.kind === "searching" ? "Searching…" : "Search concepts"}
            <ArrowRight size={16} weight="bold" aria-hidden />
          </button>
        </div>
        {error && <p role="alert" className="text-sm text-v-failed">{error}</p>}
      </form>

      {!concepts.length && (
        <div>
          <p className="mb-2 text-sm text-ink-3">Or start from a combination</p>
          <ul className="flex flex-col gap-1.5">
            {EXAMPLES.map((example) => (
              <li key={example.label}>
                <button type="button" className="text-left text-sm leading-snug text-ink-2 underline-offset-4 transition-colors hover:text-accent hover:underline"
                  onClick={() => {
                    let next: Concept[] = [];
                    for (const text of example.positive) next = addConcepts(next, text, "positive");
                    for (const text of example.negative) next = addConcepts(next, text, "negative");
                    setConcepts(next);
                    setError(null);
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
        {state.kind === "done" && <ConceptResults result={state.result} />}
      </div>
    </section>
  );
}

function ConceptField({ sign, concepts, onAdd, onRemove, onFlip }: {
  sign: ConceptSign;
  concepts: Concept[];
  onAdd: (raw: string) => void;
  onRemove: (id: string) => void;
  onFlip: (id: string) => void;
}) {
  const meta = SIGN_META[sign];
  const [draft, setDraft] = useState("");
  const commit = () => { if (draft.trim()) { onAdd(draft); setDraft(""); } };
  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit();
    } else if (event.key === "Backspace" && !draft && concepts.length) {
      onRemove(concepts[concepts.length - 1].id);
    }
  }
  const inputId = `concepts-${sign}`;
  return (
    <div className={cx("flex flex-col gap-2 rounded-panel border bg-surface p-3", meta.border)}>
      <label htmlFor={inputId} className="flex items-center gap-2 text-sm font-medium text-ink">
        <span aria-hidden className={cx("flex h-5 w-5 items-center justify-center rounded-mark text-xs font-semibold text-[var(--on-accent)]", meta.bg)}>
          {meta.symbol}
        </span>
        {meta.label}
      </label>
      <p className="text-xs text-ink-3">{meta.hint}</p>
      {concepts.length > 0 && (
        <ul className="flex flex-wrap gap-1.5">
          {concepts.map((concept) => (
            <li key={concept.id} className={cx("flex items-center gap-1 rounded-control py-1 pl-2 pr-1 text-sm text-ink", meta.tint)}>
              <span aria-hidden className={cx("font-mono text-xs", meta.text)}>{meta.symbol}</span>
              <span className="max-w-[24ch] truncate">{concept.text}</span>
              <button type="button" onClick={() => onFlip(concept.id)}
                title={sign === "positive" ? "Move to negative concepts" : "Move to positive concepts"}
                aria-label={`Move "${concept.text}" to ${sign === "positive" ? "negative" : "positive"} concepts`}
                className="rounded-mark p-0.5 text-ink-3 transition-colors hover:text-accent">
                {sign === "positive" ? <Minus size={13} weight="bold" aria-hidden /> : <Plus size={13} weight="bold" aria-hidden />}
              </button>
              <button type="button" onClick={() => onRemove(concept.id)} aria-label={`Remove "${concept.text}"`}
                className="rounded-mark p-0.5 text-ink-3 transition-colors hover:text-v-failed">
                <X size={13} weight="bold" aria-hidden />
              </button>
            </li>
          ))}
        </ul>
      )}
      <input id={inputId} value={draft} className="field" placeholder={meta.placeholder} maxLength={200}
        onChange={(event) => setDraft(event.target.value)} onKeyDown={onKeyDown} onBlur={commit} />
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
        <p className="min-w-0 flex-1 font-medium leading-snug text-ink">
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
