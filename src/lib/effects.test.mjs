import assert from 'node:assert/strict';
import test from 'node:test';
import { effectForPool } from './effects.ts';

test('raw ratios transform to the matching logarithmic pool scale', () => {
  const paper = { ciLevel: .95, effectSize: { metric: 'OR', value: 2, ci: [1, 4] } };
  const result = effectForPool(paper, 'logOR');
  assert.equal(result.value, Math.log(2));
  assert.deepEqual(result.ci, [0, Math.log(4)]);
  assert.equal(effectForPool(paper, 'logRR'), null);
  assert.equal(effectForPool(paper, 'SMD'), null);
});

test('backend normalized intervals take precedence over reported uncertainty', () => {
  const normalized = { metric: 'logOR', value: .3, ci: [-.2, .8] };
  assert.equal(effectForPool({ analysisEffectSize: normalized, ciLevel: .9, effectSize: { metric: 'OR', value: 1.3, ci: [1.1, 1.5] } }, 'logOR'), normalized);
});

test('refuses absent confidence levels, non-95% raw intervals, invalid ratios and incompatible normalized intervals', () => {
  for (const paper of [
    { effectSize: { metric: 'SMD', value: 1, ci: [0, 2] } },
    { ciLevel: .9, effectSize: { metric: 'SMD', value: 1, ci: [0, 2] } },
    { ciLevel: .95, effectSize: { metric: 'OR', value: 1, ci: [0, 2] } },
    { analysisEffectSize: { metric: 'logRR', value: 1, ci: [0, 2] } },
  ]) assert.equal(effectForPool(paper, 'logOR'), null);
});
