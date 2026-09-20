import { useCallback, useEffect, useRef, useState } from "react";
import type { GapMap, MapGap, MapRegion, Verdict } from "../types";
import { fetchMap } from "../api/client";
import { cx, formatCount, percent } from "../lib/format";
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
  | { kind: "loading" }
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
    <p className="mt-2 text-xs text-ink-2">{gap.band === 0 ? "No sampled papers" : `${formatCount(gap.band)} sampled papers`} sit between these two literatures, which hold {formatCount(gap.support)} each nearby. Closest sampled work is {gap.nearest.cosine.toFixed(2)} away.</p>
    <p className="mt-1.5 text-xs text-ink-3">{gap.discouraged
      ? "Nearly empty because the surrounding work reported nulls or never reported at all — read those first."
      : `Nearly empty between regions the sample reads as ${[...new Set(gap.parentLabels.map((label) => REGION_META[label].label.toLowerCase()))].join(" and ")}.`}</p>
  </li>;
}

/**
 * The corpus map beside the results, with this search's question placed on it. It is loaded on
 * demand: clustering the sample is the most expensive thing the API does, and it describes the
 * index rather than the match set, so it is not part of a search.
 */
export default function MapPanel({ idea }: { idea: string }) {
  const [state, setState] = useState<State>({ kind: "idle" });
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const load = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ kind: "loading" });
    try {
      // The endpoint rejects a shorter idea, and a map without a placement is still worth showing.
      const map = await fetchMap(idea.trim().length >= 8 ? { idea: idea.trim() } : {}, controller.signal);
      if (!controller.signal.aborted) setState({ kind: "ready", map });
    } catch (error) {
      if (controller.signal.aborted) return;
      setState({ kind: "error", message: error instanceof Error ? error.message : "The map could not be built." });
    }
  }, [idea]);

  const map = state.kind === "ready" ? state.map : null;
  const placement = map?.placement ?? null;
  const regions = map
    ? [...map.regions].sort((a, b) => REGION_ORDER.indexOf(a.label) - REGION_ORDER.indexOf(b.label) || b.attempts - a.attempts)
    : [];

  return <section>
    <div className="flex flex-wrap items-baseline justify-between gap-3">
      <h2 className="text-sm font-medium text-ink-2">Where this question sits in the corpus</h2>
      {state.kind !== "loading" && <button type="button" onClick={() => void load()} className="text-sm text-accent underline-offset-4 hover:underline">{state.kind === "idle" ? "Build the map" : "Rebuild"}</button>}
    </div>
    <p className="mt-1 max-w-[70ch] text-xs leading-relaxed text-ink-3">A sample of the index is clustered into regions, and each region is sorted into one of three groups: worth a look (the sample reports no difference, or never reports an outcome), already crowded (plenty of reported results, agreeing or not), and nothing to judge yet (the index could not read a result, or holds too few studies). This question is then placed against them. It describes the index, not the matches above, so it is built only when you ask for it.</p>

    <div aria-live="polite" className="mt-4">
      {state.kind === "loading" && <p className="text-sm text-ink-2">Clustering the indexed corpus…</p>}
      {state.kind === "error" && <p role="alert" className="rounded-control border border-line bg-surface-2 p-4 text-sm text-v-failed">{state.message}</p>}
      {map && <div className="flex flex-col gap-6">
        {map.points.length > 0 && <MapCanvas points={map.points} regions={map.regions} gaps={map.gaps} placementPoint={placement?.point ?? null} />}
        <div className="text-xs text-ink-3">
          <p>{formatCount(map.coverage.sampled)} embedded studies of {formatCount(map.coverage.corpus)} in the index{map.coverage.corpus > 0 && ` (${percent(map.coverage.sampled / map.coverage.corpus)})`}, in {map.coverage.regions} regions · {map.version}</p>
          {map.warnings.map((warning) => <p key={warning} className="mt-1">{warning}</p>)}
        </div>
        {placement?.nearest && placement.redundancy !== null && <div className="rounded-control border border-line bg-surface p-5">
          <p className="text-sm text-ink-2">Closest paper in the sample</p>
          <p className="mt-1 text-2xl font-semibold tracking-tight text-ink">{redundancyLabel(placement.redundancy)}</p>
          <ul className="mt-2 flex flex-col gap-1.5">
            {[placement.nearest, ...(placement.neighbors ?? [])].map((paper) => <li key={paper.id} className="flex items-baseline justify-between gap-3 text-sm">
              <span className="min-w-0 truncate text-ink">{paper.title}</span>
              <span className="shrink-0 text-xs text-ink-3">{paper.year ?? "year unknown"} · {VERDICT_META[paper.bucket as Verdict]?.label ?? paper.bucket} · {paper.cosine.toFixed(2)}</span>
            </li>)}
          </ul>
          {placement.region && <p className="mt-3 text-sm text-ink-2">It lands in a region the map reads as <span className={clusterMetaOf(placement.region.label).text}>{clusterMetaOf(placement.region.label).label.toLowerCase()}</span> ({REGION_META[placement.region.label].label.toLowerCase()}): {bucketSummary(placement.region.bucketCounts)}.</p>}
          {placement.nearestGap && <p className="mt-1.5 text-sm text-ink-2">The nearest stretch between two neighbouring literatures holds {formatCount(placement.nearestGap.band)} sampled papers against {formatCount(placement.nearestGap.support)} in each neighbour{placement.nearestGap.discouraged ? ", but those neighbours reported nulls." : "."}</p>}
        </div>}
        {map.gaps.length > 0 && <details>
          <summary className="cursor-pointer text-sm text-ink-2">Stretches with almost no papers between two literatures ({map.gaps.length})</summary>
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
