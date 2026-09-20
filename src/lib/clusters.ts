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

/**
 * A stable hue for a cluster number, spread around the wheel by the golden
 * angle so neighboring numbers land far apart.
 */
export function clusterHue(cluster: number): number {
  return (cluster * 137.508) % 360;
}
