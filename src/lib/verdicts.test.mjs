import assert from 'node:assert/strict';
import test from 'node:test';
import { BAR_GROUPS, FILTER_GROUPS, UNSTATED_DIRECTION_GROUP, directionsFromPapers } from './verdicts.ts';

const paper = (verdict, resultDirection) => ({ id: verdict + (resultDirection ?? ''), verdict, resultDirection });
const counts = (values) => ({ effect: 0, credible_null: 0, reported_null: 0, inconclusive: 0, failed: 0, unreported: 0, ...values });
const group = (key) => FILTER_GROUPS.find((candidate) => candidate.key === key);

test('the two directions are separate answers over the full match set', () => {
  const directions = { favoursIntervention: 4, favoursComparator: 3, unclear: 2 };
  const totals = counts({ effect: 9, credible_null: 1, reported_null: 2, failed: 1, unreported: 1 });
  assert.deepEqual(BAR_GROUPS.map((entry) => [entry.key, entry.total(totals, directions)]), [
    ['favours_intervention', 4],
    ['favours_comparator', 3],
    ['no_difference', 3],
    ['no_answer', 2],
  ]);
  // The bar's two effect answers plus the note beside it account for every effect, none silently dropped.
  const effects = BAR_GROUPS.filter((entry) => entry.direction).reduce((sum, entry) => sum + entry.total(totals, directions), 0);
  assert.equal(effects + UNSTATED_DIRECTION_GROUP.total(totals, directions), totals.effect);
});

test('one definition drives the bar and the study-list filter', () => {
  const papers = [paper('effect', 'favours_intervention'), paper('effect', 'favours_comparator'), paper('effect', null), paper('credible_null'), paper('unreported')];
  assert.deepEqual(papers.filter((entry) => group('favours_comparator').matches(entry)).map((entry) => entry.id), ['effectfavours_comparator']);
  // An effect whose report never stated a direction is neither benefit nor harm.
  assert.equal(group('favours_intervention').matches(paper('effect', null)), false);
  assert.equal(UNSTATED_DIRECTION_GROUP.matches(paper('effect', null)), true);
  assert.equal(group('no_answer').matches(paper('unreported')), true);
});

test('a payload without a direction split falls back to the displayed studies', () => {
  assert.deepEqual(directionsFromPapers([paper('effect', 'favours_intervention'), paper('effect', 'unclear'), paper('credible_null')]), {
    favoursIntervention: 1,
    favoursComparator: 0,
    unclear: 1,
  });
});
