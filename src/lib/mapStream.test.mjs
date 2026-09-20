import assert from 'node:assert/strict';
import test from 'node:test';
import { advanceMap, coverageLine, mapWarnings, normalizeCoverage } from './mapStream.ts';

const point = (id, region) => ({ id, x: 0, y: 0, region, bucket: 'unreported', title: id, year: null });
const progress = (over) => ({
  stage: 'scanning',
  iteration: null,
  coverage: { clustered: 2, corpus: 10, regions: 1, drawn: 2, complete: false, target: 10 },
  points: [],
  pointRegions: [],
  regions: [],
  ...over,
});

test('points accumulate once and keep the membership of the latest state', () => {
  const first = advanceMap(null, progress({ points: [point('a', 0), point('b', 0)], pointRegions: [0, 0] }));
  assert.deepEqual(first.points.map((p) => p.id), ['a', 'b']);
  const second = advanceMap(first, progress({
    stage: 'clustering',
    iteration: 1,
    points: [point('c', 1)],
    pointRegions: [0, 1, 1],
    regions: [{ id: 1, size: 3, x: 0.2, y: -0.1 }],
  }));
  assert.deepEqual(second.points.map((p) => p.id), ['a', 'b', 'c']);
  assert.deepEqual(second.points.map((p) => p.region), [0, 1, 1]);
  assert.deepEqual(second.centroids, [{ id: 1, size: 3, x: 0.2, y: -0.1 }]);
  assert.equal(second.stage, 'clustering');
});

test('a partial map says it is partial, and a finished one states the coverage it has', () => {
  const building = coverageLine(
    { clustered: 250000, corpus: 2078515, regions: 64, drawn: 700, complete: false, target: 2078515 },
    'scanning',
  );
  assert.match(building, /Still building/);
  assert.match(building, /not the whole index yet/);
  assert.match(building, /250,000 of 2,078,515 embedded studies read/);

  const done = coverageLine({ clustered: 2078515, corpus: 2078515, regions: 80, drawn: 6000, complete: true });
  assert.equal(
    done,
    '2,078,515 embedded studies of 2,078,515 in the index (100%), in 80 regions, 6,000 of them drawn',
  );
  assert.doesNotMatch(done, /building|partial/i);
});

test('a build that will not read the whole corpus counts against the corpus, not its own target', () => {
  const capped = coverageLine(
    { clustered: 100000, corpus: 2078515, regions: 40, drawn: 500, complete: false, target: 200000 },
    'clustering',
  );
  assert.match(capped, /100,000 of 200,000 embedded studies read \(5%\)/);
  assert.match(capped, /regions settling/);
});

test('counts a payload does not carry are never rendered as arithmetic on nothing', () => {
  // A server older than the streaming build reports what it sampled.
  const legacy = { sampled: 6000, corpus: 2128219, regions: 80 };
  assert.deepEqual(normalizeCoverage(legacy), {
    clustered: 6000, corpus: 2128219, regions: 80, drawn: 0, complete: false,
  });
  const line = coverageLine(legacy);
  assert.doesNotMatch(line, /NaN/);
  assert.match(line, /6,000 embedded studies of 2,128,219 in the index \(0%\)/);
  assert.match(line, /not the whole index/);

  const partial = coverageLine({ corpus: 2128219, regions: 80 }, 'scanning');
  assert.doesNotMatch(partial, /NaN/);
  assert.match(partial, /0 of 2,128,219 embedded studies read/);
});

test('a map that covered the whole corpus does not also warn that it sampled it', () => {
  const whole = { clustered: 2128219, corpus: 2128219, regions: 80, drawn: 6000, complete: true };
  const warnings = [
    'The map describes a random sample of 6000 of 2128219 embedded studies, not the whole index.',
    'All 2128219 clustered studies shape the regions; the canvas draws 6000 of them.',
  ];
  assert.deepEqual(mapWarnings(warnings, whole), [warnings[1]]);
  // A map that did not cover the corpus keeps every warning it was given.
  assert.deepEqual(mapWarnings(warnings, { ...whole, clustered: 6000, complete: false }), warnings);
});
