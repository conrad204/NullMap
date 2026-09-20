import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import type { SearchProgress, SearchRequest, SearchResult } from "./types";
import { searchIdea, usingMockApi } from "./api/client";
import TopBar from "./components/TopBar";
import IdeaComposer from "./components/IdeaComposer";
import QuestionHeader, { GLIDE_MS, type GlideOrigin } from "./components/QuestionHeader";
import ResultsView from "./components/ResultsView";
import ResultsSkeleton from "./components/ResultsSkeleton";
import EmptyState from "./components/EmptyState";
import ConceptSearch from "./components/ConceptSearch";
import ErrorNotice from "./components/ErrorNotice";
import { SAMPLE_RESULT } from "./api/mock";
import { afterSearch, loadFilterState, saveFilterState, type FilterState } from "./lib/filters";

type Status =
  | { kind: "idle" }
  | { kind: "searching"; progress: SearchProgress | null }
  | { kind: "done"; result: SearchResult }
  | { kind: "error"; message: string };

/**
 * Dev affordance while there is no backend: deep-link into a UI state.
 *   ?state=results | loading | error
 */
function initialFromUrl(): Status {
  if (!usingMockApi) return { kind: "idle" };
  switch (new URLSearchParams(window.location.search).get("state")) {
    case "results":
      return { kind: "done", result: SAMPLE_RESULT };
    case "loading":
      return { kind: "searching", progress: { stage: "searching", message: "", scanned: 1180 } };
    case "error":
      return { kind: "error", message: "Search failed (502 Bad Gateway)" };
    default:
      return { kind: "idle" };
  }
}

export default function App() {
  const [status, setStatus] = useState<Status>(initialFromUrl);
  const abortRef = useRef<AbortController | null>(null);
  const [request, setRequest] = useState<SearchRequest | null>(null);
  // Corpus filters outlive the composer's own state: sticky ones carry over to the next
  // search, so the header has to be able to say that they are still narrowing the corpus.
  const [filters, setFilters] = useState<FilterState>(loadFilterState);
  useEffect(() => saveFilterState(filters), [filters]);
  // Set only by a composer submit, so retries and deep links render the question in place.
  const [origin, setOrigin] = useState<GlideOrigin | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const run = useCallback(async (req: SearchRequest, from: GlideOrigin | null = null) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setRequest(req);
    setOrigin(from);
    // Non-sticky filters clear as the search starts, so the next question is unfiltered
    // unless the user asked for the bounds to stay.
    setFilters(afterSearch);
    // The question lands at the top of the page; `from` is in viewport coordinates, so the glide still starts where it was typed.
    window.scrollTo(0, 0);
    setStatus({ kind: "searching", progress: null });
    try {
      const result = await searchIdea(
        req,
        (progress) => {
          if (!ac.signal.aborted) setStatus({ kind: "searching", progress });
        },
        ac.signal,
      );
      if (!ac.signal.aborted) setStatus({ kind: "done", result });
    } catch (err) {
      if (ac.signal.aborted) return;
      setStatus({
        kind: "error",
        message: err instanceof Error ? err.message : "Something went wrong.",
      });
    }
  }, []);

  // Back to the centered composer, which kept the draft.
  const reset = useCallback(() => {
    abortRef.current?.abort();
    setStatus({ kind: "idle" });
  }, []);

  // Mock deep links (?state=loading) have no request; they borrow the sample question.
  const question = request?.idea ?? (status.kind === "done" ? status.result.idea : usingMockApi ? SAMPLE_RESULT.idea : "");

  return (
    <div className="flex min-h-[100dvh] flex-col">
      <TopBar sampleData={usingMockApi} />

      <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-8 sm:px-6 lg:px-8 lg:py-12">
        {usingMockApi && <p className="mb-6 rounded-control border border-line bg-surface-2 p-3 text-sm text-ink-2"><strong className="text-ink">Illustrative demo.</strong> Studies and findings are fictional. Searches and uploads do not contact a service or save data.</p>}
        {/* Top padding, not flex centering, so opening the study-plan fields never moves the input. */}
        <div hidden={status.kind !== "idle"} className="pt-[max(0.5rem,calc(50dvh-22.5rem))]">
          <IdeaComposer hidden={status.kind !== "idle"} onSubmit={run} filters={filters} onFiltersChange={setFilters} />
          <div className="mx-auto mt-24 w-full max-w-[720px] border-t border-line pt-10">
            <ConceptSearch />
          </div>
          <div className="mx-auto mt-16 w-full max-w-[720px] border-t border-line pt-10">
            <EmptyState />
          </div>
        </div>

        {status.kind !== "idle" && (
          <div className={status.kind === "done" ? "mx-auto flex w-full max-w-[1400px] flex-col gap-8" : "mx-auto flex w-full max-w-[960px] flex-col gap-8"}>
            {question && <QuestionHeader question={question} busy={status.kind === "searching"} origin={origin} onEdit={reset} onCancel={reset} filters={request?.filters} sticky={filters.sticky} />}
            {/* While the question glides through this space, hold the progress back so the two never overlap. */}
            <section aria-live="polite" className="min-w-0" style={{ "--fade-delay": origin && status.kind === "searching" ? `${GLIDE_MS * 0.6}ms` : "0ms" } as CSSProperties}>
              {status.kind === "searching" && <ResultsSkeleton progress={status.progress} />}
              {status.kind === "error" && (
                <ErrorNotice message={status.message} onRetry={request ? () => run(request) : undefined} />
              )}
              {status.kind === "done" && <ResultsView key={status.result.queryId} result={status.result} />}
            </section>
          </div>
        )}
      </main>
    </div>
  );
}
