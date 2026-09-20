import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GapMap, MapGap, MapRegion, Verdict } from "../types";
import { streamMap } from "../api/client";
import { cx, formatCount } from "../lib/format";
import { useSettlingMap } from "../lib/mapAnimation";
import { advanceMap, coverageLine, mapWarnings, type PartialMap } from "../lib/mapStream";
import {
  CLUSTER_META,
  CLUSTER_ORDER,
  REGION_META,
  REGION_ORDER,
  clusterDetail,
  clusterMetaOf,
  clusterOf,
  redundancyLabel,
  regionName,
} from "../lib/regions";
import { VERDICT_META, VERDICT_ORDER } from "../lib/verdicts";
import MapCanvas from "./MapCanvas";

type State =
  | { kind: "idle" }
  | { kind: "loading"; partial: PartialMap | null }
  | { kind: "ready"; map: GapMap }
  | { kind: "error"; message: string };

function BucketBar({ counts, attempts }: { counts: MapRegion["bucketCounts"]; attempts: number }) {
  if (!attempts) return null;
  return <div className="flex h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
    {VERDICT_ORDER.map((verdict) => counts[verdict]
      ? <span key={verdict} className={VERDICT_META[verdict].bg} style={{ width: `${((counts[verdict] ?? 0) / attempts) * 100}%` }} />
      : null)}
  </div>;
}

function bucketSummary(counts: MapRegion["bucketCounts"]): string {
  return VERDICT_ORDER.filter((verdict) => counts[verdict])
    .map((verdict) => `${counts[verdict]} ${VERDICT_META[verdict].label.toLowerCase()}`)
    .join(" · ") || "no readable primary studies";
}

function RegionCard({ region }: { region: MapRegion }) {
  const meta = REGION_META[region.label];
  const cluster = clusterMetaOf(region.label);
  return <li className="rounded-control border border-line bg-surface p-4">
    <div className="flex items-start justify-between gap-3">
      <p className="min-w-0 text-sm text-ink">{regionName(region.exemplars.map((exemplar) => exemplar.title))}</p>
      <span className={cx("shrink-0 rounded-[6px] px-2 py-0.5 text-xs font-medium", cluster.text, cluster.tint)} title={cluster.description}>{cluster.label}</span>
    </div>
    <p className="mt-1.5 text-xs text-ink-2"><span className="font-medium text-ink">{meta.label}.</span> {meta.description}</p>
    <p className="mt-2 text-xs text-ink-3">{formatCount(region.attempts)} primary studies of {formatCount(region.size)} papers{region.medianYear ? ` · median ${region.medianYear}` : ""}{region.cosine !== undefined ? ` · ${region.cosine.toFixed(2)} from your question` : ""}</p>
    <div className="mt-2.5">
      <BucketBar counts={region.bucketCounts} attempts={region.attempts} />
      <p className="mt-1.5 text-xs text-ink-2">{bucketSummary(region.bucketCounts)}</p>
    </div>
  </li>;
}

function GapCard({ gap, regions }: { gap: MapGap; regions: MapRegion[] }) {
  const parents = gap.regions.map((id) => regions.find((region) => region.id === id));
  return <li className="rounded-control border border-line bg-surface p-4">
    <div className="flex items-start justify-between gap-3">
      <p className="min-w-0 text-sm text-ink">{parents.map((parent) => parent ? regionName(parent.exemplars.map((exemplar) => exemplar.title)) : "a region").join("  ×  ")}</p>
      {gap.discouraged && <span className="shrink-0 rounded-[6px] bg-[color-mix(in_srgb,var(--v-reported-null)_12%,transparent)] px-2 py-0.5 text-xs font-medium text-v-reported-null">neighbours failed</span>}
    </div>
    <p className="mt-2 text-xs text-ink-2">{gap.band === 0 ? "No clustered papers" : `${formatCount(gap.band)} clustered papers`} sit between these two literatures, which hold {formatCount(gap.support)} each nearby. Closest clustered work is {gap.nearest.cosine.toFixed(2)} away.</p>
    <p className="mt-1.5 text-xs text-ink-3">{gap.discouraged
      ? "Nearly empty because the surrounding work reported nulls or never reported at all — read those first."
      : `Nearly empty between regions the index reads as ${[...new Set(gap.parentLabels.map((label) => REGION_META[label].label.toLowerCase()))].join(" and ")}.`}</p>
  </li>;
}

/**
 * The corpus map beside the results, with this search's question placed on it.
 *
 * It builds itself, streaming: the build starts the first time the panel is scrolled into view —
 * clustering the whole embedded index is the most expensive thing the API does, so a reader who
 * never reaches this section never pays for it — and every state the server sends is drawn as it
 * arrives. While that is happening the coverage line says what the picture actually covers so far;
 * only the final event describes the whole corpus.
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
  const frame = useSettlingMap(
    useMemo(
      () => ({ points: partial?.points ?? [], centroids: partial?.centroids ?? [] }),
      [partial],
    ),
    partial !== null,
  );
  const placement = map?.placement ?? null;
  const regions = map
    ? [...map.regions].sort((a, b) => REGION_ORDER.indexOf(a.label) - REGION_ORDER.indexOf(b.label) || b.attempts - a.attempts)
    : [];

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
        points={map ? map.points : frame.points}
        regions={map ? map.regions : []}
        gaps={map ? map.gaps : []}
        placementPoint={map ? placement?.point ?? null : partial?.placement ?? null}
        provisionalRegions={map ? [] : frame.centroids}
        building={!map}
      />}
      {state.kind === "loading" && <div className="text-xs text-ink-3">
        <p>{partial ? coverageLine(partial.coverage, partial.stage) : "Clustering the embedded index…"}</p>
      </div>}
      {map && <div className="flex flex-col gap-6">
        <div className="text-xs text-ink-3">
          <p>{coverageLine(map.coverage)} · {map.version}</p>
          {mapWarnings(map.warnings, map.coverage).map((warning) => <p key={warning} className="mt-1">{warning}</p>)}
        </div>
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
        {map.gaps.length > 0 && <details>
          <summary className="cursor-pointer text-sm text-ink-2">Sparse bands: almost no papers between two literatures ({map.gaps.length})</summary>
          <ul className="mt-3 flex flex-col gap-3">{map.gaps.map((gap) => <GapCard key={gap.regions.join("-")} gap={gap} regions={map.regions} />)}</ul>
        </details>}
        <details>
          <summary className="cursor-pointer text-sm text-ink-2">Regions ({regions.length})</summary>
          <div className="mt-3 flex flex-col gap-5">{CLUSTER_ORDER.map((cluster) => {
            const inCluster = regions.filter((region) => clusterOf(region.label) === cluster);
            if (!inCluster.length) return null;
            const meta = CLUSTER_META[cluster];
            return <div key={cluster}>
              <h3 className={cx("text-sm font-medium", meta.text)}>{meta.label} ({inCluster.length})</h3>
              <p className="mt-1 max-w-[70ch] text-xs leading-relaxed text-ink-3">{meta.description} In this map: {clusterDetail(cluster, inCluster.map((region) => region.label))}.</p>
              <ul className="mt-2.5 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-1">{inCluster.map((region) => <RegionCard key={region.id} region={region} />)}</ul>
            </div>;
          })}</div>
        </details>
      </div>}
    </div>
  </section>;
}
