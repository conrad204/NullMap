import assert from 'node:assert/strict';
import test from 'node:test';
import { ForceLayout } from './forceLayout.ts';

const point = (id, x, y, region = 0) => ({ id, x, y, region, kind: 'point' });
const region = (id, x, y) => ({ id: `region-${id}`, x, y, region: id, kind: 'region' });

const positions = (layout) =>
  Array.from({ length: layout.size }, (_, i) => [layout.xAt(i), layout.yAt(i)]);

/** Smallest center-to-center distance between any two nodes, in plane units. */
function closestPair(layout) {
  let best = Infinity;
  for (let i = 0; i < layout.size; i += 1) {
    for (let j = i + 1; j < layout.size; j += 1) {
      best = Math.min(best, Math.hypot(layout.xAt(i) - layout.xAt(j), layout.yAt(i) - layout.yAt(j)));
    }
  }
  return best;
}

test('nodes enter at their projected coordinates and rest near them when nothing crowds them', () => {
  const layout = new ForceLayout();
  layout.update([point('a', 0, 0), point('b', 10, 0), point('c', 0, 10), region(0, 3, 3)]);
  const [ax, ay] = positions(layout)[0];
  assert.ok(Math.abs(ax) < 1e-3 && Math.abs(ay) < 1e-3, 'seeded at the projection');
  layout.settle(10, 10_000);
  assert.ok(layout.settled);
  const [bx, by] = positions(layout)[1];
  assert.ok(Math.hypot(bx - 10, by) < 1.5, `b stays near its anchor, was ${bx}, ${by}`);
});

test('a pile of coincident studies is spread until no two marks overlap', () => {
  const layout = new ForceLayout();
  const pile = Array.from({ length: 60 }, (_, i) => point(`p${i}`, 5, 5));
  layout.update([...pile, region(0, 5, 5)]);
  assert.ok(closestPair(layout) < 1e-3, 'they start on top of one another');
  const pixelsPerUnit = 20;
  layout.settle(pixelsPerUnit, 10_000);
  const markDiameter = (layout.options.pointRadius * 2) / pixelsPerUnit;
  assert.ok(closestPair(layout) > markDiameter * 0.95, `closest pair ${closestPair(layout)} vs mark ${markDiameter}`);
  // Spread, not scattered: the pile still sits where the projection put it.
  let sumX = 0;
  for (let i = 0; i < layout.size; i += 1) sumX += layout.xAt(i);
  assert.ok(Math.abs(sumX / layout.size - 5) < 1, 'the cloud keeps its center');
});

test('new nodes reheat a settled layout and keep the old ones where they were', () => {
  const layout = new ForceLayout();
  layout.update([point('a', 0, 0), point('b', 4, 0)]);
  layout.settle(10, 10_000);
  const before = positions(layout);
  layout.update([point('a', 0, 0), point('b', 4, 0), point('c', 8, 0)]);
  assert.ok(!layout.settled, 'arrivals wake the layout');
  assert.equal(layout.size, 3);
  assert.deepEqual(positions(layout).slice(0, 2), before, 'settled nodes are not re-seeded');
  assert.ok(Math.abs(layout.xAt(2) - 8) < 1e-3, 'the newcomer enters at its projection');
});

test('an anchor that moves nudges the layout rather than restarting it', () => {
  const layout = new ForceLayout();
  layout.update([point('a', 0, 0, 0), region(0, 1, 1)]);
  layout.settle(10, 10_000);
  layout.update([point('a', 0, 0, 0), region(0, 2, 2)]);
  assert.ok(layout.alpha > 0 && layout.alpha <= layout.options.nudge + 1e-9);
  layout.settle(10, 10_000);
  const home = layout.regionNode(0);
  assert.ok(Math.hypot(layout.xAt(home) - 2, layout.yAt(home) - 2) < 0.2, 'the region mark follows the clustering');
});

test('retain drops the nodes a new map no longer has and keeps the survivors in place', () => {
  const layout = new ForceLayout();
  layout.update([point('a', 0, 0), point('b', 3, 0), point('c', 6, 0), region(0, 3, 0)]);
  layout.settle(10, 10_000);
  const kept = [layout.xAt(layout.indexOf('c')), layout.yAt(layout.indexOf('c'))];
  layout.retain(new Set(['a', 'c', 'region-0']));
  assert.equal(layout.size, 3);
  assert.equal(layout.indexOf('b'), -1);
  assert.deepEqual([layout.xAt(layout.indexOf('c')), layout.yAt(layout.indexOf('c'))], kept);
  assert.equal(layout.regionNode(0), layout.indexOf('region-0'));
});

test('a pinned node stays under the pointer and is let go on release', () => {
  const layout = new ForceLayout();
  layout.update([point('a', 0, 0), point('b', 0.01, 0)]);
  const a = layout.indexOf('a');
  layout.pin(a, 7, 7);
  for (let i = 0; i < 20; i += 1) layout.tick(10);
  assert.deepEqual([layout.xAt(a), layout.yAt(a)], [7, 7]);
  assert.ok(layout.isPinned(a));
  layout.release(a);
  layout.settle(10, 10_000);
  assert.ok(Math.hypot(layout.xAt(a), layout.yAt(a)) < 7, 'released, it drifts back toward its anchor');
});

test('nearest finds the node under a point and nothing outside the radius', () => {
  const layout = new ForceLayout();
  layout.update([point('a', 0, 0), point('b', 10, 10)]);
  assert.equal(layout.idAt(layout.nearest(9.5, 10.2, 1)), 'b');
  assert.equal(layout.nearest(5, 5, 1), -1);
});

test('a tick over thousands of nodes is far from quadratic', () => {
  const layout = new ForceLayout();
  const many = Array.from({ length: 6000 }, (_, i) =>
    point(`p${i}`, Math.cos(i) * (i % 97), Math.sin(i * 1.3) * (i % 89), i % 40));
  const homes = Array.from({ length: 40 }, (_, i) => region(i, (i % 8) * 10 - 35, Math.floor(i / 8) * 10 - 20));
  layout.update([...many, ...homes]);
  const started = performance.now();
  for (let i = 0; i < 10; i += 1) layout.tick(8);
  const perTick = (performance.now() - started) / 10;
  assert.ok(perTick < 60, `a tick took ${perTick.toFixed(1)} ms`);
  for (let i = 0; i < layout.size; i += 1) {
    assert.ok(Number.isFinite(layout.xAt(i)) && Number.isFinite(layout.yAt(i)), 'positions stay finite');
  }
});
