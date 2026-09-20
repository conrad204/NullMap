import type { MapCoverage, MapEdge, MapPoint, MapProgress } from "../types";

// Formatted here rather than through lib/format so this module stays importable
// by the node:test files, which resolve no runtime imports of their own.
const formatCount = (n: number) => new Intl.NumberFormat("en-US").format(n);
const percent = (share: number) => `${Math.round(share * 100)}%`;

/** A coverage payload as it arrives: an older server states what it sampled, not what it clustered. */
export type RawCoverage = Partial<MapCoverage> & { sampled?: number };

const count = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

/**
 * Coverage as counts that exist.
 *
 * A server older than the streaming build reports `sampled` and no `drawn` or
 * `complete`, and arithmetic on a missing count produces `NaN`, which reads as
 * a broken map rather than an honest one. Missing numbers are filled from what
 * the payload does say, and never invented: a map whose clustered count does
 * not reach the corpus is incomplete regardless of what it claims.
 */
export function normalizeCoverage(raw: RawCoverage | null | undefined): MapCoverage {
  const clustered = count(raw?.clustered) ?? count(raw?.sampled) ?? 0;
  const corpus = count(raw?.corpus) ?? clustered;
  const drawn = count(raw?.drawn) ?? 0;
  const neighborhood = raw?.scope === "neighborhood";
  // A neighborhood is complete when it holds what it asked for; it never
  // claims the corpus, so the corpus is no measure of it.
  const complete = raw?.complete ?? (neighborhood ? false : clustered >= corpus);
  return {
    clustered,
    corpus,
    regions: count(raw?.regions) ?? 0,
    drawn,
    complete: neighborhood ? complete : complete && clustered >= corpus,
    ...(count(raw?.target) !== null ? { target: raw!.target } : {}),
    ...(neighborhood ? { scope: "neighborhood" as const } : {}),
    ...(count(raw?.neighborhood) !== null ? { neighborhood: raw!.neighborhood } : {}),
  };
}

/** The map as far as the stream has built it: real partial state, never a preview of the result. */
export interface PartialMap {
  points: MapPoint[];
  /** Every similarity edge sent so far, over `points` by position. */
  edges: MapEdge[];
  centroids: { id: number; size: number; x: number; y: number }[];
  coverage: MapCoverage;
  stage: MapProgress["stage"];
  /** The question's place on the plane being drawn, kept from the last state that had one. */
  placement: { x: number; y: number } | null;
}

/**
 * Folds one streamed state into the last one.
 *
 * Points and edges are cumulative — the server sends each one once — while
 * membership is resent in full, because which region a study belongs to changes
 * on every k-means pass. Nothing is carried over from a state the server did
 * not send.
 */
export function advanceMap(previous: PartialMap | null, progress: MapProgress): PartialMap {
  const points = [...(previous?.points ?? []), ...progress.points];
  const edges = progress.edges?.length
    ? [...(previous?.edges ?? []), ...progress.edges]
    : previous?.edges ?? [];
  for (let index = 0; index < points.length && index < progress.pointRegions.length; index += 1) {
    const region = progress.pointRegions[index];
    if (points[index].region !== region) points[index] = { ...points[index], region };
  }
  return {
    points,
    edges,
    centroids: progress.regions ?? [],
    coverage: normalizeCoverage(progress.coverage),
    stage: progress.stage,
    placement: progress.placement ?? previous?.placement ?? null,
  };
}

/**
 * The server's warnings, minus any that the coverage contradicts.
 *
 * A server that clustered the whole index cannot also have sampled it, and
 * showing both leaves the reader to guess which is true. The coverage counts
 * are the measurement, so a sampling warning is dropped only when they say the
 * corpus was covered in full — never the other way round. A neighborhood map's
 * warning that it is not the index is what its coverage line already says, so
 * it is not said twice.
 */
export function mapWarnings(warnings: string[], raw: RawCoverage): string[] {
  const coverage = normalizeCoverage(raw);
  if (coverage.scope === "neighborhood") {
    return warnings.filter((warning) => !/nearest your question/i.test(warning));
  }
  if (!coverage.complete) return warnings;
  return warnings.filter((warning) => !/\bsample\b|\bsampled\b/i.test(warning));
}

/**
 * What the picture on screen covers, in words.
 *
 * A map that is still building says so, and says it in the same terms as the
 * finished one, so an in-progress picture can never be read as the whole index.
 */
export function coverageLine(
  raw: RawCoverage,
  stage?: MapProgress["stage"],
  building = stage !== undefined,
): string {
  const coverage = normalizeCoverage(raw);
  if (coverage.scope === "neighborhood") {
    const asked = coverage.neighborhood && coverage.neighborhood !== coverage.clustered
      ? ` (asked for ${formatCount(coverage.neighborhood)})`
      : "";
    const index = coverage.corpus > 0 ? ` of ${formatCount(coverage.corpus)} in the index` : "";
    const drawn = coverage.drawn && coverage.drawn < coverage.clustered
      ? `, ${formatCount(coverage.drawn)} of them drawn`
      : "";
    const regions = building
      ? `${coverage.regions} ${stage === "clustering" ? "regions settling" : "regions forming"}`
      : `${coverage.regions} regions`;
    return `The ${formatCount(coverage.clustered)} studies nearest your question${asked}${index}, in ${regions}${drawn}. This is the neighborhood of the question, not the whole index.`;
  }
  const share = coverage.corpus > 0 ? ` (${percent(coverage.clustered / coverage.corpus)})` : "";
  if (!building) {
    const drawn = coverage.drawn && coverage.drawn < coverage.clustered
      ? `, ${formatCount(coverage.drawn)} of them drawn`
      : "";
    const whole = coverage.complete
      ? ""
      : " — not the whole index";
    return `${formatCount(coverage.clustered)} embedded studies of ${formatCount(coverage.corpus)} in the index${share}, in ${coverage.regions} regions${drawn}${whole}`;
  }
  const target = coverage.target ?? coverage.corpus;
  const read = `${formatCount(coverage.clustered)} of ${formatCount(target)} embedded studies read${share}`;
  const work = stage === "clustering" ? "regions settling" : "regions forming";
  return `Still building: ${read}, ${formatCount(coverage.drawn)} drawn, ${coverage.regions} ${work}. This is a partial map, not the whole index yet.`;
}
