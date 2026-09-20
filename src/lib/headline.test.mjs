import assert from 'node:assert/strict';
import test from 'node:test';
import { headline } from './headline.ts';

const counts = (values) => ({ effect: 0, credible_null: 0, reported_null: 0, inconclusive: 0, failed: 0, unreported: 0, ...values });

test('no matches is reported as unknown, never as a new idea', () => {
  const result = headline(counts({}), false);
  assert.equal(result.title, 'No prior studies found');
  assert.match(result.detail, /not evidence either way/);
});

test('prior results are stated with what the silent studies were', () => {
  const result = headline(counts({ effect: 17, failed: 6, unreported: 3 }), true);
  assert.equal(result.title, 'This has been tested before');
  assert.match(result.detail, /^17 of 26 matching studies report a result\. All 17 report an effect\./);
  assert.match(result.detail, /size is unverified/);
  assert.match(result.detail, /3 completed trials never reported results, 6 failed or stopped early/);
});

test('split and all-null evidence are described as such', () => {
  assert.match(headline(counts({ effect: 2, reported_null: 3 }), false).detail, /Results are split: 2 report an effect, 3 report no difference\./);
  const nulls = headline(counts({ credible_null: 2 }), false).detail;
  assert.match(nulls, /All 2 found no significant difference, with intervals tight enough/);
  assert.doesNotMatch(headline(counts({ credible_null: 1, reported_null: 1 }), false).detail, /tight enough/);
});

test('studies without any readable result are not called results', () => {
  const result = headline(counts({ unreported: 4, inconclusive: 1 }), false);
  assert.equal(result.title, 'This has been tried before, but no results are available');
  assert.match(result.detail, /^5 matching studies, none with a readable result: 4 completed trials never reported results, 1 had no clear result\.$/);
});

test('matches without any controlled comparison are not called a prior attempt', () => {
  const result = headline(counts({ failed: 1, inconclusive: 3 }), false, 0);
  assert.equal(result.title, 'Related work exists, but nothing tested this directly');
  assert.match(result.detail, /^4 matching studies, none a controlled comparison of the intervention: 1 failed or stopped early, 3 had no clear result\./);
  // A controlled trial that never reported is a real attempt, and an older API omits the count.
  assert.equal(headline(counts({ unreported: 2 }), false, 2).title, 'This has been tried before, but no results are available');
  assert.equal(headline(counts({ unreported: 2 }), false).title, 'This has been tried before, but no results are available');
  // Results outrank the control count: a stated result already required a comparison group.
  assert.equal(headline(counts({ effect: 1 }), false, 0).title, 'This has been tested before');
});

test('effects favoring the comparator are named, not folded into "an effect"', () => {
  const directions = { favoursIntervention: 2, favoursComparator: 1, unclear: 0 };
  const result = headline(counts({ effect: 3 }), false, undefined, directions);
  assert.match(result.detail, /Not all in the same direction: 2 favored the intervention, 1 favored the comparator\./);
  // One direction is not a split, and an older payload carries no directions at all.
  assert.doesNotMatch(headline(counts({ effect: 3 }), false, undefined, { favoursIntervention: 2, favoursComparator: 0, unclear: 1 }).detail, /same direction/);
  assert.doesNotMatch(headline(counts({ effect: 3 }), false).detail, /same direction/);
});
