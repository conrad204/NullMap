import {
  useEffect, useLayoutEffect, useMemo, useRef, useState,
  type FormEvent, type KeyboardEvent, type UIEvent,
} from "react";
import { ArrowRight } from "@phosphor-icons/react";
import type { SearchRequest } from "../types";
import type { GlideOrigin } from "./QuestionHeader";
import FilterControls from "./FilterControls";
import { SIGN_META, conceptError, parseLine, tagSegments, toSteer } from "../lib/concepts";
import { filterError, parseFilters, type FilterState } from "../lib/filters";
import { cx } from "../lib/format";

interface Props {
  hidden: boolean;
  onSubmit: (req: SearchRequest, origin: GlideOrigin | null) => void;
  filters: FilterState;
  onFiltersChange: (filters: FilterState) => void;
}

const QUESTIONS = [
  "Does azilsartan lower systolic blood pressure more than placebo in adults with essential hypertension? +ambulatory blood pressure −animal model",
  "Does renal artery stenting improve blood pressure or kidney function in adults with atherosclerotic renal artery stenosis? +kidney outcomes −type 2 diabetes",
  "Does bardoxolone methyl improve kidney function in adults with chronic kidney disease? +quality of life −mortality",
];
const DEFAULTS = { plannedN: 200, alpha: 0.05, valueSuccess: 100, valueNull: 20, studyCost: 30 };

/**
 * The textarea and the coloured copy behind it must lay text out identically,
 * so every metric that affects wrapping lives in one string used by both.
 */
const FIELD = "block w-full resize-none border-0 px-4 pt-3.5 text-base leading-relaxed";

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
 * of that question's matches. The tags are coloured where they were typed: a
 * textarea cannot paint part of its own value, so a mirror of the line sits
 * behind a transparent-text textarea that remains the real input.
 */
export default function Composer({ hidden, onSubmit, filters, onFiltersChange }: Props) {
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [tagProblem, setTagProblem] = useState<string | null>(null);
  const [filterProblem, setFilterProblem] = useState<string | null>(null);
  const fieldRef = useRef<HTMLTextAreaElement>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);
  const { question, concepts } = useMemo(() => parseLine(text), [text]);
  const segments = useMemo(() => tagSegments(text), [text]);

  /**
   * Typing can scroll the field without a scroll event, so follow it after every
   * render. The mirror is also held to the field's `clientWidth`, which drops by
   * the width of a scrollbar the moment one appears: any other width would wrap
   * the coloured copy a word away from where the caret actually is.
   */
  useLayoutEffect(() => {
    const field = fieldRef.current;
    const mirror = mirrorRef.current;
    if (!field || !mirror) return;
    const follow = () => {
      mirror.style.width = `${field.clientWidth}px`;
      mirror.scrollTop = field.scrollTop;
      mirror.scrollLeft = field.scrollLeft;
    };
    follow();
    const observer = new ResizeObserver(follow);
    observer.observe(field);
    return () => observer.disconnect();
  }, [text]);

  function syncScroll(event: UIEvent<HTMLTextAreaElement>) {
    const mirror = mirrorRef.current;
    if (!mirror) return;
    mirror.scrollTop = event.currentTarget.scrollTop;
    mirror.scrollLeft = event.currentTarget.scrollLeft;
  }

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
          from uncertain ones.
        </p>
      </div>

      <form onSubmit={handleSubmit} noValidate hidden={hidden} className="fade-up mx-auto w-full max-w-[720px] flex flex-col gap-6">
        <div className="flex flex-col gap-2">
          <div className={"prompt-box" + (error ? " prompt-box-invalid" : "")}>
            <label htmlFor="idea" className="sr-only">Your research question</label>
            <div className="relative">
              {/* The line as it reads: the same string, with the tags in their sign's colour. */}
              <div ref={mirrorRef} aria-hidden
                className={cx(FIELD, "pointer-events-none absolute inset-0 select-none overflow-hidden whitespace-pre-wrap break-words text-ink")}>
                {segments.map((segment, index) => segment.sign
                  ? <mark key={index} className={cx("bg-transparent", SIGN_META[segment.sign].text)}>{segment.text}</mark>
                  : <span key={index}>{segment.text}</span>)}
                {"\n"}
              </div>
              <textarea ref={fieldRef} id="idea" name="idea" rows={3} value={text} maxLength={10000} autoFocus
                onChange={(event) => setText(event.target.value)} onKeyDown={onQuestionKeyDown} onScroll={syncScroll}
                placeholder="Does X change Y in population Z? +concept −concept"
                aria-invalid={error ? "true" : undefined} aria-describedby="idea-help"
                className={cx(FIELD, "relative bg-transparent text-transparent caret-ink placeholder:text-ink-3 focus:outline-none")} />
            </div>
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

        {tagProblem && <p role="alert" className="-mt-3 text-sm text-v-failed">{tagProblem}</p>}

        {!text.trim() && (
          <div className="flex flex-col gap-4">
            <div>
              <p className="mb-2 text-sm text-ink-3">Or start from a clinical question, tags and all</p>
              <ul className="flex flex-col gap-1.5">{QUESTIONS.map((example) => <li key={example}>
                <button type="button" onClick={() => append(example)}
                  className="text-left text-sm leading-snug text-ink-2 underline-offset-4 transition-colors hover:text-accent hover:underline">{example}</button>
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
