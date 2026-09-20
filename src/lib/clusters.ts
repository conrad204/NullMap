import type { MapEdge } from "../types";

/**
 * The connected components of the similarity graph, one per node by position.
 *
 * Two papers are in the same cluster when a chain of above-threshold cosine
 * edges joins them, so a cluster is a topic that hangs together, not a k-means
 * cell. Components are numbered by the lowest node index they contain, which
 * keeps a cluster's number — and so its color — stable as further nodes and
 * edges arrive: only a merge of two clusters renumbers one of them, and that
 * is a real change of assignment. Nodes with no edges are -1: unclustered.
 */
export function clusterComponents(size: number, edges: readonly MapEdge[]): Int32Array {
  const parent = new Int32Array(size);
  for (let i = 0; i < size; i += 1) parent[i] = i;
  const find = (i: number): number => {
    while (parent[i] !== i) {
      parent[i] = parent[parent[i]];
      i = parent[i];
    }
    return i;
  };
  const linked = new Uint8Array(size);
  for (const [a, b] of edges) {
    if (a < 0 || b < 0 || a >= size || b >= size || a === b) continue;
    linked[a] = 1;
    linked[b] = 1;
    const ra = find(a);
    const rb = find(b);
    if (ra === rb) continue;
    // The lower index becomes the root, so the root is the component's lowest member.
    if (ra < rb) parent[rb] = ra;
    else parent[ra] = rb;
  }
  const clusters = new Int32Array(size);
  for (let i = 0; i < size; i += 1) clusters[i] = linked[i] ? find(i) : -1;
  return clusters;
}

/** How many clusters of at least `minimum` members the assignment holds. */
export function countClusters(clusters: Int32Array, minimum = 2): number {
  const sizes = new Map<number, number>();
  for (const cluster of clusters) {
    if (cluster >= 0) sizes.set(cluster, (sizes.get(cluster) ?? 0) + 1);
  }
  let count = 0;
  for (const size of sizes.values()) if (size >= minimum) count += 1;
  return count;
}

/** The band of hues held back for the reader's own question, so nothing else is blue. */
const BLUE_FROM = 198;
const BLUE_TO = 262;
const HUE_SPAN = 360 - (BLUE_TO - BLUE_FROM);

/**
 * A stable hue for a cluster number, spread by the golden angle so neighboring
 * numbers land far apart, and stepped over the blue band, which belongs to the
 * question node alone.
 */
export function clusterHue(cluster: number): number {
  const hue = (Math.abs(cluster) * 137.508) % HUE_SPAN;
  return hue < BLUE_FROM ? hue : hue + (BLUE_TO - BLUE_FROM);
}

/**
 * Keeps only each paper's strongest links, which is what turns a hairball into
 * a graph: above the server's floor almost every paper in a query neighborhood
 * is similar to almost every other, so drawing all of it yields one blob with
 * no structure to read. Keeping the `perNode` best links of each paper (their
 * union, so a link survives when either end ranks it) leaves the same
 * neighborhoods but only their strongest ties.
 */
export function pruneEdges(size: number, edges: readonly MapEdge[], perNode: number): MapEdge[] {
  const best: { cosine: number; edge: number }[][] = Array.from({ length: size }, () => []);
  for (let e = 0; e < edges.length; e += 1) {
    const [a, b, cosine] = edges[e];
    if (a < 0 || b < 0 || a >= size || b >= size || a === b) continue;
    for (const end of [a, b]) {
      const kept = best[end];
      kept.push({ cosine, edge: e });
      if (kept.length > perNode) {
        kept.sort((one, two) => two.cosine - one.cosine);
        kept.length = perNode;
      }
    }
  }
  const keep = new Set<number>();
  for (const kept of best) for (const { edge } of kept) keep.add(edge);
  return [...keep].sort((one, two) => one - two).map((e) => edges[e]);
}

/**
 * Topic communities of the similarity graph, by weighted label propagation.
 *
 * Connected components cannot say which topic a paper belongs to: one chain of
 * similar papers joins nearly the whole neighborhood into a single component,
 * because a query's neighborhood is similar to itself by construction.
 * Propagation instead lets each paper take the label its links pull hardest
 * toward (weighted by cosine), which settles on groups densely linked inside
 * and sparsely linked outside — topics. Passes run over the nodes in index
 * order, so the same graph always gives the same grouping, and each community
 * is numbered by its lowest member, so a color survives a later frame that only
 * adds papers around it. Papers with no links are -1: unclustered.
 */
export function clusterLabels(size: number, edges: readonly MapEdge[], passes = 12): Int32Array {
  const heads = new Int32Array(size).fill(-1);
  const next = new Int32Array(edges.length * 2).fill(-1);
  const other = new Int32Array(edges.length * 2);
  const weight = new Float64Array(edges.length * 2);
  let slot = 0;
  const add = (from: number, to: number, cosine: number) => {
    other[slot] = to;
    weight[slot] = cosine;
    next[slot] = heads[from];
    heads[from] = slot;
    slot += 1;
  };
  for (const [a, b, cosine] of edges) {
    if (a < 0 || b < 0 || a >= size || b >= size || a === b) continue;
    add(a, b, cosine);
    add(b, a, cosine);
  }

  const label = new Int32Array(size);
  for (let i = 0; i < size; i += 1) label[i] = i;
  const totals = new Map<number, number>();
  for (let pass = 0; pass < passes; pass += 1) {
    let changed = false;
    for (let i = 0; i < size; i += 1) {
      if (heads[i] === -1) continue;
      totals.clear();
      for (let e = heads[i]; e !== -1; e = next[e]) {
        const neighbor = label[other[e]];
        totals.set(neighbor, (totals.get(neighbor) ?? 0) + weight[e]);
      }
      let bestLabel = label[i];
      let bestWeight = -Infinity;
      for (const [candidate, total] of totals) {
        // Ties go to the lower label, so the result cannot depend on map order.
        if (total > bestWeight || (total === bestWeight && candidate < bestLabel)) {
          bestWeight = total;
          bestLabel = candidate;
        }
      }
      if (bestLabel !== label[i]) {
        label[i] = bestLabel;
        changed = true;
      }
    }
    if (!changed) break;
  }

  // Each community is renumbered by its lowest member, so its color is stable.
  const lowest = new Map<number, number>();
  for (let i = 0; i < size; i += 1) {
    if (heads[i] === -1) continue;
    if (!lowest.has(label[i])) lowest.set(label[i], i);
  }
  const clusters = new Int32Array(size);
  for (let i = 0; i < size; i += 1) clusters[i] = heads[i] === -1 ? -1 : lowest.get(label[i])!;
  return clusters;
}
