import assert from 'node:assert/strict';
import test from 'node:test';
import { ForceLayout } from './forceLayout.ts';

const point = (id, x, y) => ({ id, x, y });

const positions = (layout) =>
  Array.from({ length: layout.size }, (_, i) => [layout.xAt(i), layout.yAt(i)]);

const distance = (layout, a, b) =>
  Math.hypot(
    layout.xAt(layout.indexOf(a)) - layout.xAt(layout.indexOf(b)),
    layout.yAt(layout.indexOf(a)) - layout.yAt(layout.indexOf(b)),
  );

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
  layout.update([point('a', 0, 0), point('b', 10, 0), point('c', 0, 10)]);
  const [ax, ay] = positions(layout)[0];
  assert.ok(Math.abs(ax) < 1e-3 && Math.abs(ay) < 1e-3, 'seeded at the projection');
  layout.settle(10, 10_000);
  assert.ok(layout.settled);
  const [bx, by] = positions(layout)[1];
  assert.ok(Math.hypot(bx - 10, by) < 1.5, `b stays near its anchor, was ${bx}, ${by}`);
});

test('a pile of coincident studies is spread until no two marks overlap', () => {
  const layout = new ForceLayout();
  layout.update(Array.from({ length: 60 }, (_, i) => point(`p${i}`, 5, 5)));
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

test('a link pulls its two studies together, harder the higher the cosine', () => {
  // Three pairs seeded the same distance apart, joined by links of rising cosine.
  const layout = new ForceLayout();
  layout.update([
    point('a1', 0, 0), point('a2', 6, 0),
    point('b1', 0, 40), point('b2', 6, 40),
    point('c1', 0, 80), point('c2', 6, 80),
    point('free1', 0, 120), point('free2', 6, 120),
  ]);
  layout.link([['a1', 'a2', 0.8], ['b1', 'b2', 0.9], ['c1', 'c2', 0.99]]);
  layout.settle(10, 10_000);
  const a = distance(layout, 'a1', 'a2');
  const b = distance(layout, 'b1', 'b2');
  const c = distance(layout, 'c1', 'c2');
  const free = distance(layout, 'free1', 'free2');
  assert.ok(c < b && b < a, `tighter with cosine: ${c.toFixed(2)} < ${b.toFixed(2)} < ${a.toFixed(2)}`);
  assert.ok(a < free, `even the weakest link pulls closer than none: ${a.toFixed(2)} vs ${free.toFixed(2)}`);
});

test('a link at or below the floor exerts nothing, and a pair is linked once', () => {
  const layout = new ForceLayout();
  layout.update([point('a', 0, 0), point('b', 6, 0)]);
  layout.link([['a', 'b', layout.options.linkFloor], ['b', 'a', 0.99]]);
  assert.equal(layout.linkCount, 1, 'the reverse of an existing pair is the same link');
  layout.settle(10, 10_000);
  assert.ok(distance(layout, 'a', 'b') > 5, 'a floor-level link does not pull');
});

test('links name nodes by id and survive the nodes being reordered', () => {
  const layout = new ForceLayout();
  layout.update([point('x', 0, 0), point('a', 10, 0), point('b', 16, 0)]);
  layout.link([['a', 'b', 0.99]]);
  layout.retain(new Set(['a', 'b']));
  const seen = [];
  layout.eachLink((i, j, cosine) => seen.push([layout.idAt(i), layout.idAt(j), cosine]));
  assert.deepEqual(seen, [['a', 'b', 0.99]]);
  layout.settle(10, 10_000);
  assert.ok(distance(layout, 'a', 'b') < 4, 'the link still pulls after the reorder');
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

test('new links nudge a settled layout rather than restarting it', () => {
  const layout = new ForceLayout();
  layout.update([point('a', 0, 0), point('b', 4, 0)]);
  layout.settle(10, 10_000);
  layout.link([['a', 'b', 0.95]]);
  assert.ok(layout.alpha > 0 && layout.alpha <= layout.options.nudge + 1e-9);
});

test('retain drops the nodes a new map no longer has and keeps the survivors in place', () => {
  const layout = new ForceLayout();
  layout.update([point('a', 0, 0), point('b', 3, 0), point('c', 6, 0)]);
  layout.settle(10, 10_000);
  const kept = [layout.xAt(layout.indexOf('c')), layout.yAt(layout.indexOf('c'))];
  layout.retain(new Set(['a', 'c']));
  assert.equal(layout.size, 2);
  assert.equal(layout.indexOf('b'), -1);
  assert.deepEqual([layout.xAt(layout.indexOf('c')), layout.yAt(layout.indexOf('c'))], kept);
});

test('a pinned node stays under the pointer and is let go on release', () => {
  const layout = new ForceLayout();
  layout.update([point('a', 0, 0), point('b', 0.01, 0)]);
  const a = layout.indexOf('a');
  layout.pin(a, 7, 7);
  // Held still long enough for the graph to cool completely around it.
  layout.settle(10, 10_000);
  assert.deepEqual([layout.xAt(a), layout.yAt(a)], [7, 7]);
  assert.ok(layout.isPinned(a));
  assert.ok(layout.settled, 'the graph has gone cold while the node was held');
  layout.release(a);
  assert.ok(!layout.settled, 'letting go warms the graph so the node can move again');
  layout.settle(10, 10_000);
  assert.ok(!layout.isPinned(a));
  assert.ok(
    Math.hypot(layout.xAt(a), layout.yAt(a)) < Math.hypot(7, 7) - 1,
    'released, it drifts back toward its anchor',
  );
});

test('grabbing a paper wakes the graph, dragging it only keeps it awake', () => {
  const layout = new ForceLayout();
  layout.update([point('a', 0, 0), point('b', 1, 0)]);
  const a = layout.indexOf('a');
  layout.settle(10, 10_000);
  layout.pin(a, 3, 3);
  assert.equal(layout.alpha, layout.options.reheat, 'the grab wakes it fully');
  layout.settle(10, 10_000);
  layout.pin(a, 3.2, 3.1);
  assert.ok(
    layout.alpha <= layout.options.nudge + 1e-9,
    `a move within a drag nudges rather than reheats, was ${layout.alpha}`,
  );
});

test('nearest finds the node under a point and nothing outside the radius', () => {
  const layout = new ForceLayout();
  layout.update([point('a', 0, 0), point('b', 10, 10)]);
  assert.equal(layout.idAt(layout.nearest(9.5, 10.2, 1)), 'b');
  assert.equal(layout.nearest(5, 5, 1), -1);
});

test('a tick over thousands of linked nodes is far from quadratic', () => {
  const layout = new ForceLayout();
  const many = Array.from({ length: 6000 }, (_, i) =>
    point(`p${i}`, Math.cos(i) * (i % 97), Math.sin(i * 1.3) * (i % 89)));
  layout.update(many);
  layout.link(Array.from({ length: 9000 }, (_, l) => [`p${l % 6000}`, `p${(l * 7 + 1) % 6000}`, 0.8 + (l % 20) / 100]));
  const started = performance.now();
  for (let i = 0; i < 10; i += 1) layout.tick(8);
  const perTick = (performance.now() - started) / 10;
  assert.ok(perTick < 60, `a tick took ${perTick.toFixed(1)} ms`);
  for (let i = 0; i < layout.size; i += 1) {
    assert.ok(Number.isFinite(layout.xAt(i)) && Number.isFinite(layout.yAt(i)), 'positions stay finite');
  }
});

test('a node is drawn by how many links it has, between a floor and a ceiling', () => {
  const layout = new ForceLayout();
  layout.update([point('hub', 0, 0), ...Array.from({ length: 30 }, (_, i) => point(`s${i}`, i + 1, 0))]);
  layout.link(Array.from({ length: 30 }, (_, i) => ['hub', `s${i}`, 0.9]));
  const hub = layout.indexOf('hub');
  assert.equal(layout.degreeAt(hub), 30);
  assert.equal(layout.degreeAt(layout.indexOf('s0')), 1);
  assert.equal(layout.radiusAt(hub), layout.options.maxRadius, 'a hub past fullDegree is capped');
  assert.ok(layout.radiusAt(layout.indexOf('s0')) > layout.options.pointRadius);
  assert.ok(layout.radiusAt(layout.indexOf('s0')) < layout.options.maxRadius);
  layout.update([point('alone', 100, 100)]);
  assert.equal(layout.radiusAt(layout.indexOf('alone')), layout.options.pointRadius);
});
