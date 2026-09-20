import assert from 'node:assert/strict';
import test from 'node:test';
import { clusterComponents, clusterHue, countClusters } from './clusters.ts';

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

test('cluster hues are on the wheel and neighbors are far apart', () => {
  for (let c = 0; c < 50; c += 1) {
    const hue = clusterHue(c);
    assert.ok(hue >= 0 && hue < 360);
  }
  assert.ok(Math.abs(clusterHue(1) - clusterHue(2)) > 90);
});
