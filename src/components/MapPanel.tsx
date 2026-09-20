import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowRight } from "@phosphor-icons/react";
import type { GapMap, MapGap, MapRegion, Verdict } from "../types";
import { fetchMap } from "../api/client";
import { cx, formatCount, percent } from "../lib/format";
import { REGION_META, REGION_ORDER, regionName } from "../lib/regions";
import { VERDICT_META, VERDICT_ORDER } from "../lib/verdicts";

type State =
  | { kind: "loading" }
  | { kind: "ready"; map: GapMap }
  | { kind: "error"; message: string };

function BucketBar({ counts, attempts }: { counts: MapRegion["bucketCounts"]; attempts: number }) {
  if (!attempts) return null;
  return (
    <div className="flex h-1.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
      {VERDICT_ORDER.map((verdict) => {
        const count = counts[verdict] ?? 0;
        if (!count) return null;
        return (
          <span
            key={verdict}
            className={VERDICT_META[verdict].bg}
            style={{ width: `${(count / attempts) * 100}%` }}
          />
        );
      })}
    </div>
  );
}

function bucketSummary(counts: MapRegion["bucketCounts"]): string {
  const parts = VERDICT_ORDER.filter((verdict) => counts[verdict]).map(
    (verdict) => `${counts[verdict]} ${VERDICT_META[verdict as Verdict].label.toLowerCase()}`,
  );
  return parts.join(" · ") || "no readable primary studies";
}

function RegionCard({ region }: { region: MapRegion }) {
  const meta = REGION_META[region.label];
  return (
    <li className="rounded-control border border-line bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 text-sm text-ink">{regionName(region.exemplars.map((e) => e.title))}</p>
        <span className={cx("shrink-0 rounded-[6px] px-2 py-0.5 text-xs font-medium", meta.text, meta.tint)}>
          {meta.label}
        </span>
      </div>
      <p className="mt-2 text-xs text-ink-3">
        {formatCount(region.attempts)} primary studies of {formatCount(region.size)} papers
        {region.medianYear ? ` · median ${region.medianYear}` : ""}
        {region.cosine !== undefined ? ` · ${region.cosine.toFixed(2)} from your idea` : ""}
      </p>
      <div className="mt-2.5">
        <BucketBar counts={region.bucketCounts} attempts={region.attempts} />
        <p className="mt-1.5 text-xs text-ink-2">{bucketSummary(region.bucketCounts)}</p>
      </div>
    </li>
  );
}

function GapCard({ gap, regions }: { gap: MapGap; regions: MapRegion[] }) {
  const parents = gap.regions.map((id) => regions.find((region) => region.id === id));
  return (
    <li className="rounded-control border border-line bg-surface p-4">
      <div className="flex items-start justify-between gap-3">
        <p className="min-w-0 text-sm text-ink">
          {parents
            .map((parent) => (parent ? regionName(parent.exemplars.map((e) => e.title)) : "a region"))
            .join("  ×  ")}
        </p>
        {gap.discouraged && (
          <span className="shrink-0 rounded-[6px] bg-[color-mix(in_srgb,var(--v-reported-null)_12%,transparent)] px-2 py-0.5 text-xs font-medium text-v-reported-null">
            neighbours failed
          </span>
        )}
      </div>
      <p className="mt-2 text-xs text-ink-2">
        {gap.band === 0 ? "No sampled papers" : `${formatCount(gap.band)} sampled papers`} sit
        between these two literatures, which hold {formatCount(gap.support)} each nearby. Closest
        sampled work is {gap.nearest.cosine.toFixed(2)} away.
      </p>
      <p className="mt-1.5 text-xs text-ink-3">
        {gap.discouraged
          ? "Open because the surrounding work reported nulls or never reported at all — read those first."
          : `Open between ${gap.parentLabels
              .map((label) => REGION_META[label].label.toLowerCase())
              .join(" and ")} regions.`}
      </p>
    </li>
  );
}

export default function MapPanel() {
  const [state, setState] = useState<State>({ kind: "loading" });
  const [idea, setIdea] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  useEffect(() => () => abortRef.current?.abort(), []);

  const load = useCallback(async (body: { idea?: string }) => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setState({ kind: "loading" });
    try {
      const map = await fetchMap(body, controller.signal);
      if (!controller.signal.aborted) setState({ kind: "ready", map });
    } catch (error) {
      if (controller.signal.aborted) return;
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "The map could not be built.",
      });
    }
  }, []);

  useEffect(() => {
    void load({});
  }, [load]);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = idea.trim();
    if (trimmed && trimmed.length < 8) {
      setInvalid("Describe the idea in at least eight characters, or leave the box empty for the map alone.");
      return;
    }
    setInvalid(null);
    void load(trimmed ? { idea: trimmed } : {});
  }

  const map = state.kind === "ready" ? state.map : null;
  const placement = map?.placement ?? null;
  const regions = map ? [...map.regions].sort(
    (a, b) => REGION_ORDER.indexOf(a.label) - REGION_ORDER.indexOf(b.label) || b.attempts - a.attempts,
  ) : [];

  return (
    <div className="grid grid-cols-1 items-start gap-8 lg:grid-cols-[minmax(320px,380px)_1fr] lg:gap-14">
      <form onSubmit={handleSubmit} className="flex flex-col gap-5">
        <div>
          <h1 className="text-3xl font-semibold leading-[1.05] tracking-tight text-ink sm:text-4xl">
            Where does your idea land?
          </h1>
          <p className="mt-3 max-w-[44ch] leading-relaxed text-ink-2">
            A sample of the index is clustered into regions and each region is described by what
            happened in it: effects, reported nulls, studies that were run and never reported, or
            records no result could be read from at all. An idea is placed
            against that map, so "nothing here" can be told apart from "this has been tried and it
            did not work".
          </p>
        </div>
        <label htmlFor="map-idea" className="flex flex-col gap-2 text-sm font-medium text-ink">
          Your idea
          <textarea
            id="map-idea"
            rows={4}
            value={idea}
            maxLength={4000}
            onChange={(event) => {
              setIdea(event.target.value);
              setInvalid(null);
            }}
            aria-invalid={invalid ? true : undefined}
            aria-describedby={invalid ? "map-idea-error" : undefined}
            placeholder="Does X change Y in population Z?"
            className="field resize-y font-normal"
          />
        </label>
        {invalid && (
          <p id="map-idea-error" role="alert" className="text-sm text-v-failed">
            {invalid}
          </p>
        )}
        <button type="submit" className="btn btn-primary self-start" disabled={state.kind === "loading"}>
          {state.kind === "loading" ? "Building the map…" : "Place it on the map"}
          <ArrowRight size={16} weight="bold" />
        </button>
      </form>

      <section aria-live="polite" className="min-w-0">
        {state.kind === "error" && (
          <p role="alert" className="rounded-control border border-line bg-surface-2 p-4 text-sm text-v-failed">
            {state.message}
          </p>
        )}
        {state.kind === "loading" && <p className="text-sm text-ink-2">Clustering the indexed corpus…</p>}
        {map && (
          <div className="flex flex-col gap-8">
            <div className="text-xs text-ink-3">
              <p>
                {formatCount(map.coverage.sampled)} embedded studies of{" "}
                {formatCount(map.coverage.corpus)} in the index
                {map.coverage.corpus > 0 &&
                  ` (${percent(map.coverage.sampled / map.coverage.corpus)})`}
                , in {map.coverage.regions} regions · {map.version}
              </p>
              {map.warnings.map((warning) => (
                <p key={warning} className="mt-1">
                  {warning}
                </p>
              ))}
            </div>
            {placement?.nearest && placement.redundancy !== null && (
              <div className="rounded-control border border-line bg-surface p-5">
                <p className="text-sm text-ink-2">Closest paper in the sample</p>
                <p className="mt-1 text-2xl font-semibold tabular-nums text-ink">
                  {placement.redundancy.toFixed(2)} cosine
                </p>
                <p className="mt-2 text-sm text-ink">{placement.nearest.title}</p>
                <p className="mt-1 text-xs text-ink-3">
                  {placement.nearest.year ?? "year unknown"} ·{" "}
                  {VERDICT_META[placement.nearest.bucket as Verdict]?.label ?? placement.nearest.bucket}
                </p>
                {placement.region && (
                  <p className="mt-3 text-sm text-ink-2">
                    It lands in a{" "}
                    <span className={REGION_META[placement.region.label].text}>
                      {REGION_META[placement.region.label].label.toLowerCase()}
                    </span>{" "}
                    region: {bucketSummary(placement.region.bucketCounts)}.
                  </p>
                )}
                {placement.nearestGap && (
                  <p className="mt-1.5 text-sm text-ink-2">
                    Nearest open band holds {formatCount(placement.nearestGap.band)} papers against{" "}
                    {formatCount(placement.nearestGap.support)} in each neighbour
                    {placement.nearestGap.discouraged ? ", but its neighbours reported nulls." : "."}
                  </p>
                )}
              </div>
            )}

            {map.gaps.length > 0 && (
              <div>
                <h2 className="text-sm font-medium text-ink">Sparse bands between literatures</h2>
                <p className="mt-1 text-xs text-ink-3">
                  Pairs of related regions with almost nothing between them in the sample, most open
                  first.
                </p>
                <ul className="mt-3 flex flex-col gap-3">
                  {map.gaps.map((gap) => (
                    <GapCard key={gap.regions.join("-")} gap={gap} regions={map.regions} />
                  ))}
                </ul>
              </div>
            )}

            <div>
              <h2 className="text-sm font-medium text-ink">Regions</h2>
              <ul className="mt-3 grid grid-cols-1 gap-3 xl:grid-cols-2">
                {regions.map((region) => (
                  <RegionCard key={region.id} region={region} />
                ))}
              </ul>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
