import assert from 'node:assert/strict';
import test from 'node:test';
import { clusterComponents, clusterHue, clusterLabels, countClusters, pruneEdges } from './clusters.ts';

test('papers joined by a chain of edges share a cluster; unlinked papers have none', () => {
  const clusters = clusterComponents(6, [[0, 1, 0.9], [1, 2, 0.85], [3, 4, 0.95]]);
  assert.deepEqual(Array.from(clusters), [0, 0, 0, 3, 3, -1]);
  assert.equal(countClusters(clusters), 2);
});

test('a cluster keeps its number as more edges and nodes arrive, unless two clusters merge', () => {
  const before = clusterComponents(5, [[1, 2, 0.9], [3, 4, 0.9]]);
  const after = clusterComponents(7, [[1, 2, 0.9], [3, 4, 0.9], [5, 6, 0.9]]);
  assert.deepEqual(Array.from(after).slice(0, 5), Array.from(before));
  assert.equal(after[5], 5);
  const merged = clusterComponents(7, [[1, 2, 0.9], [3, 4, 0.9], [2, 3, 0.9]]);
  assert.deepEqual(Array.from(merged).slice(1, 5), [1, 1, 1, 1], 'the merged cluster takes the lower number');
});

test('edges that point outside the drawn nodes are ignored', () => {
  const clusters = clusterComponents(2, [[0, 5, 0.9], [-1, 1, 0.9], [1, 1, 0.9]]);
  assert.deepEqual(Array.from(clusters), [-1, -1]);
});

test('cluster hues are on the wheel, far apart, and never blue', () => {
  for (let c = 0; c < 200; c += 1) {
    const hue = clusterHue(c);
    assert.ok(hue >= 0 && hue < 360);
    assert.ok(hue < 198 || hue >= 262, `cluster ${c} took the question's blue (${hue})`);
  }
  assert.ok(Math.abs(clusterHue(1) - clusterHue(2)) > 90);
});

test('pruning keeps each paper its strongest links and drops the rest', () => {
  // 0 is linked to everything, weakest to 4; the others only to 0.
  const edges = [[0, 1, 0.95], [0, 2, 0.93], [0, 3, 0.91], [0, 4, 0.81]];
  const kept = pruneEdges(5, edges, 2);
  assert.deepEqual(kept, [[0, 1, 0.95], [0, 2, 0.93], [0, 3, 0.91], [0, 4, 0.81]],
    'a link survives when either end ranks it, so a leaf keeps its only link');
  const hub = pruneEdges(5, [[0, 1, 0.95], [0, 2, 0.93], [1, 2, 0.99]], 1);
  assert.deepEqual(hub, [[0, 1, 0.95], [1, 2, 0.99]], 'the weakest link of a paper that has stronger ones goes');
});

test('two dense groups joined by one weak link are separate topics, not one component', () => {
  const edges = [];
  const group = (base) => {
    for (let i = base; i < base + 4; i += 1) {
      for (let j = i + 1; j < base + 4; j += 1) edges.push([i, j, 0.95]);
    }
  };
  group(0);
  group(4);
  edges.push([3, 4, 0.79]);
  assert.equal(countClusters(clusterComponents(8, edges)), 1, 'components see one blob');
  const labels = clusterLabels(8, edges);
  assert.equal(countClusters(labels), 2);
  assert.equal(new Set(Array.from(labels).slice(0, 4)).size, 1);
  assert.equal(new Set(Array.from(labels).slice(4)).size, 1);
  assert.notEqual(labels[0], labels[4]);
});

test('unlinked papers have no topic, and the same graph always gives the same one', () => {
  const edges = [[0, 1, 0.9], [1, 2, 0.9]];
  const labels = clusterLabels(4, edges);
  assert.equal(labels[3], -1);
  assert.equal(labels[0], 0, 'a community is numbered by its lowest member');
  assert.deepEqual(Array.from(clusterLabels(4, edges)), Array.from(labels));
});
