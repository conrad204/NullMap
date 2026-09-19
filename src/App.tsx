import { useCallback, useEffect, useRef, useState } from "react";
import type { SearchProgress, SearchRequest, SearchResult } from "./types";
import { searchIdea, usingMockApi } from "./api/client";
import TopBar, { type Mode } from "./components/TopBar";
import IdeaComposer from "./components/IdeaComposer";
import ResultsView from "./components/ResultsView";
import ResultsSkeleton from "./components/ResultsSkeleton";
import EmptyState from "./components/EmptyState";
import ErrorNotice from "./components/ErrorNotice";
import ContributePanel from "./components/ContributePanel";
import { SAMPLE_RESULT } from "./api/mock";

type Status =
  | { kind: "idle" }
  | { kind: "searching"; progress: SearchProgress | null }
  | { kind: "done"; result: SearchResult }
  | { kind: "error"; message: string };

/**
 * Dev affordance while there is no backend: deep-link into a UI state.
 *   ?mode=contribute
 *   ?state=results | loading | error
 */
function initialFromUrl(): { mode: Mode; status: Status } {
  const q = new URLSearchParams(window.location.search);
  const mode: Mode = q.get("mode") === "contribute" ? "contribute" : "search";
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
  const lastRequest = useRef<SearchRequest | null>(null);

  const run = useCallback(async (req: SearchRequest) => {
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    lastRequest.current = req;
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

  // On narrow viewports the results sit below the composer; bring them into view when done.
  const resultsRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (status.kind !== "done" || window.matchMedia("(min-width: 1024px)").matches) return;
    resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [status.kind]);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    setStatus({ kind: "idle" });
  }, []);

  return (
    <div className="flex min-h-[100dvh] flex-col">
      <TopBar mode={mode} onModeChange={setMode} sampleData={usingMockApi} />

      <main className="mx-auto w-full max-w-[1400px] flex-1 px-4 py-8 sm:px-6 lg:px-8 lg:py-12">
        {mode === "search" ? (
          <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-[minmax(320px,380px)_1fr] lg:gap-14">
            <div className="lg:sticky lg:top-24">
              <IdeaComposer busy={status.kind === "searching"} onSubmit={run} onCancel={cancel} />
            </div>

            <section ref={resultsRef} aria-live="polite" className="min-w-0 scroll-mt-20">
              {status.kind === "idle" && <EmptyState />}
              {status.kind === "searching" && <ResultsSkeleton progress={status.progress} />}
              {status.kind === "error" && (
                <ErrorNotice
                  message={status.message}
                  onRetry={lastRequest.current ? () => run(lastRequest.current!) : undefined}
                />
              )}
              {status.kind === "done" && <ResultsView result={status.result} />}
            </section>
          </div>
        ) : (
          <ContributePanel />
        )}
      </main>
    </div>
  );
}
