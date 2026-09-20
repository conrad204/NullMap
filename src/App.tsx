import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import type { SearchProgress, SearchRequest, SearchResult } from "./types";
import { searchIdea, usingMockApi } from "./api/client";
import TopBar, { type Mode } from "./components/TopBar";
import IdeaComposer from "./components/IdeaComposer";
import QuestionHeader, { GLIDE_MS, type GlideOrigin } from "./components/QuestionHeader";
import ResultsView from "./components/ResultsView";
import ResultsSkeleton from "./components/ResultsSkeleton";
import EmptyState from "./components/EmptyState";
import ErrorNotice from "./components/ErrorNotice";
import MapPanel from "./components/MapPanel";
import { SAMPLE_RESULT } from "./api/mock";

type Status =
  | { kind: "idle" }
  | { kind: "searching"; progress: SearchProgress | null }
  | { kind: "done"; result: SearchResult }
  | { kind: "error"; message: string };

/**
 * Dev affordance while there is no backend: deep-link into a UI state.
 *   ?mode=map
 *   ?state=results | loading | error
 */
function modeFromUrl(): Mode {
  return new URLSearchParams(window.location.search).get("mode") === "map" ? "map" : "search";
}

function initialFromUrl(): { mode: Mode; status: Status } {
  const q = new URLSearchParams(window.location.search);
  const mode = modeFromUrl();
  if (!usingMockApi) return { mode, status: { kind: "idle" } };
  switch (q.get("state")) {
    case "results":
      return { mode, status: { kind: "done", result: SAMPLE_RESULT } };
    case "loading":
      return {
        mode,
        status: {
          kind: "searching",
          progress: { stage: "searching", message: "", scanned: 1180 },
        },
      };
    case "error":
      return { mode, status: { kind: "error", message: "Search failed (502 Bad Gateway)" } };
    default:
      return { mode, status: { kind: "idle" } };
  }
}

export default function App() {
  const [initial] = useState(initialFromUrl);
  const [mode, setMode] = useState<Mode>(initial.mode);
  const [status, setStatus] = useState<Status>(initial.status);
  const abortRef = useRef<AbortController | null>(null);
  const [request, setRequest] = useState<SearchRequest | null>(null);
  // Set only by a composer submit, so retries and deep links render the question in place.
  const [origin, setOrigin] = useState<GlideOrigin | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  // Tabs are locations: a mode change is a history entry, so Back returns to the previous tab.
  const changeMode = useCallback((next: Mode) => {
    setMode(next);
    const url = new URL(window.location.href);
    if (next === "search") url.searchParams.delete("mode");
    else url.searchParams.set("mode", next);
    window.history.pushState(null, "", url);
  }, []);
  useEffect(() => {
    const restore = () => setMode(modeFromUrl());
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);

  const run = useCallback(async (req: SearchRequest, from: GlideOrigin | null = null) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    setRequest(req);
    setOrigin(from);
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
      <TopBar mode={mode} onModeChange={changeMode} sampleData={usingMockApi} />

      <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-8 sm:px-6 lg:px-8 lg:py-12">
        {usingMockApi && <p className="mb-6 rounded-control border border-line bg-surface-2 p-3 text-sm text-ink-2"><strong className="text-ink">Illustrative demo.</strong> Studies and findings are fictional. Searches and uploads do not contact a service or save data.</p>}
        {mode === "search" ? (
          <>
            {/* Top padding, not flex centering, so opening the study-plan fields never moves the input. */}
            <div hidden={status.kind !== "idle"} className="pt-[max(0.5rem,calc(50dvh-22.5rem))]">
              <IdeaComposer hidden={status.kind !== "idle"} onSubmit={run} />
              <div className="mx-auto mt-24 w-full max-w-[720px] border-t border-line pt-10">
                <EmptyState />
              </div>
            </div>

            {status.kind !== "idle" && (
              <div className="mx-auto flex w-full max-w-[960px] flex-col gap-8">
                {question && <QuestionHeader question={question} busy={status.kind === "searching"} origin={origin} onEdit={reset} onCancel={reset} />}
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
          </>
        ) : (
          <MapPanel />
        )}
      </main>
    </div>
  );
}
