import type { MapCoverage, MapPoint, MapProgress } from "../types";

// Formatted here rather than through lib/format so this module stays importable
// by the node:test files, which resolve no runtime imports of their own.
const formatCount = (n: number) => new Intl.NumberFormat("en-US").format(n);
const percent = (share: number) => `${Math.round(share * 100)}%`;

/** The map as far as the stream has built it: real partial state, never a preview of the result. */
export interface PartialMap {
  points: MapPoint[];
  centroids: { id: number; size: number; x: number; y: number }[];
  coverage: MapCoverage;
  stage: MapProgress["stage"];
}

/**
 * Folds one streamed state into the last one.
 *
 * Points are cumulative — the server sends each one once — while membership is
 * resent in full, because which region a study belongs to changes on every
 * k-means pass. Nothing is carried over from a state the server did not send.
 */
export function advanceMap(previous: PartialMap | null, progress: MapProgress): PartialMap {
  const points = [...(previous?.points ?? []), ...progress.points];
  for (let index = 0; index < points.length && index < progress.pointRegions.length; index += 1) {
    const region = progress.pointRegions[index];
    if (points[index].region !== region) points[index] = { ...points[index], region };
  }
  return {
    points,
    centroids: progress.regions,
    coverage: progress.coverage,
    stage: progress.stage,
  };
}

/**
 * What the picture on screen covers, in words.
 *
 * A map that is still building says so, and says it in the same terms as the
 * finished one, so an in-progress picture can never be read as the whole index.
 */
export function coverageLine(coverage: MapCoverage, stage?: MapProgress["stage"]): string {
  const share = coverage.corpus > 0 ? ` (${percent(coverage.clustered / coverage.corpus)})` : "";
  if (coverage.complete) {
    return `${formatCount(coverage.clustered)} embedded studies of ${formatCount(coverage.corpus)} in the index${share}, in ${coverage.regions} regions`;
  }
  const target = coverage.target ?? coverage.corpus;
  const read = `${formatCount(coverage.clustered)} of ${formatCount(target)} embedded studies read${share}`;
  const work = stage === "clustering" ? "regions settling" : "regions forming";
  return `Still building: ${read}, ${formatCount(coverage.drawn)} drawn, ${coverage.regions} ${work}. This is a partial map, not the whole index yet.`;
}
