import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GapMap, MapRegion, Verdict } from "../types";
import { streamMap } from "../api/client";
import { formatCount } from "../lib/format";
import { advanceMap, type PartialMap } from "../lib/mapStream";
import { REGION_META, clusterMetaOf, redundancyLabel } from "../lib/regions";
import { VERDICT_META, VERDICT_ORDER } from "../lib/verdicts";
import MapCanvas from "./MapCanvas";

type State =
  | { kind: "idle" }
  | { kind: "loading"; partial: PartialMap | null }
  | { kind: "ready"; map: GapMap }
  | { kind: "error"; message: string };

function bucketSummary(counts: MapRegion["bucketCounts"]): string {
  return VERDICT_ORDER.filter((verdict) => counts[verdict])
    .map((verdict) => `${counts[verdict]} ${VERDICT_META[verdict].label.toLowerCase()}`)
    .join(" · ") || "no readable primary studies";
}

/**
 * The map of the question's neighborhood, beside the results.
 *
 * It builds itself, streaming: the build starts the first time the panel is scrolled into view —
 * the map is the most expensive thing the API does, so a reader who never reaches this section
 * never pays for it — and every state the server sends is drawn as it arrives. The picture is the
 * explanation, so nothing is written around it; what the map cannot show, the card under it says
 * in papers rather than in region jargon.
 */
export default function MapPanel({ idea }: { idea: string }) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const abortRef = useRef<AbortController | null>(null);
  const sectionRef = useRef<HTMLElement>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const load = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ kind: "loading", partial: null });
    try {
      // The endpoint rejects a shorter idea, and a map without a placement is still worth showing.
      const map = await streamMap(
        idea.trim().length >= 8 ? { idea: idea.trim() } : {},
        (progress) => setState((current) =>
          current.kind === "loading" ? { kind: "loading", partial: advanceMap(current.partial, progress) } : current),
        controller.signal,
      );
      if (!controller.signal.aborted) setState({ kind: "ready", map });
    } catch (error) {
      if (controller.signal.aborted) return;
      setState({ kind: "error", message: error instanceof Error ? error.message : "The map could not be built." });
    }
  }, [idea]);

  // Started on first sight rather than on mount, and only once per question.
  useEffect(() => {
    const section = sectionRef.current;
    if (!section) return;
    if (typeof IntersectionObserver !== "function") {
      void load();
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        observer.disconnect();
        void load();
      }
    }, { rootMargin: "200px" });
    observer.observe(section);
    return () => observer.disconnect();
  }, [load]);

  const map = state.kind === "ready" ? state.map : null;
  const partial = state.kind === "loading" ? state.partial : null;
  const drawnPoints = useMemo(() => map?.points ?? partial?.points ?? [], [map, partial]);
  const drawnEdges = useMemo(() => map?.edges ?? partial?.edges ?? [], [map, partial]);
  const placement = map?.placement ?? null;

  return <section ref={sectionRef}>
    {state.kind === "error" && <div className="flex justify-end">
      <button type="button" onClick={() => void load()} className="text-sm text-accent underline-offset-4 hover:underline">Retry</button>
    </div>}

    <div aria-live="polite" className="flex flex-col gap-6">
      {state.kind === "error" && <p role="alert" className="rounded-control border border-line bg-surface-2 p-4 text-sm text-v-failed">{state.message}</p>}
      {/*
        One canvas for both states. Finishing the build replaces what it draws,
        not the canvas itself: a second element here would be a fresh component,
        and the reader's pan and zoom would die with the old one.
      */}
      {(state.kind === "loading" || map) && <MapCanvas
        points={drawnPoints}
        edges={drawnEdges}
        placementPoint={map ? placement?.point ?? null : partial?.placement ?? null}
        building={!map}
      />}
      {state.kind === "loading" && <p className="text-xs text-ink-3">Building the map…</p>}
      {map && <div className="flex flex-col gap-6">
        {placement?.nearest && placement.redundancy !== null && <div className="rounded-control border border-line bg-surface p-5">
          <p className="text-sm text-ink-2">Closest paper in the index</p>
          <p className="mt-1 text-2xl font-semibold tracking-tight text-ink">{redundancyLabel(placement.redundancy)}</p>
          <ul className="mt-2 flex flex-col gap-1.5">
            {[placement.nearest, ...(placement.neighbors ?? [])].map((paper) => <li key={paper.id} className="flex items-baseline justify-between gap-3 text-sm">
              <span className="min-w-0 truncate text-ink">{paper.title}</span>
              <span className="shrink-0 text-xs text-ink-3">{paper.year ?? "year unknown"} · {VERDICT_META[paper.bucket as Verdict]?.label ?? paper.bucket} · {paper.cosine.toFixed(2)}</span>
            </li>)}
          </ul>
          {placement.region && <p className="mt-3 text-sm text-ink-2">It lands in a region the map reads as <span className={clusterMetaOf(placement.region.label).text}>{clusterMetaOf(placement.region.label).label.toLowerCase()}</span> ({REGION_META[placement.region.label].label.toLowerCase()}): {bucketSummary(placement.region.bucketCounts)}.</p>}
          {placement.nearestGap && <p className="mt-1.5 text-sm text-ink-2">The nearest stretch between two neighbouring literatures holds {formatCount(placement.nearestGap.band)} papers against {formatCount(placement.nearestGap.support)} in each neighbour{placement.nearestGap.discouraged ? ", but those neighbours reported nulls." : "."}</p>}
        </div>}
      </div>}
    </div>
  </section>;
}
