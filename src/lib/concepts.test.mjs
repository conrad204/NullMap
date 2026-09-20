import assert from 'node:assert/strict';
import test from 'node:test';
import {
  addConcepts, bySign, conceptError, cosineWidth, expression, flipConcept,
  matchSignals, parseConceptInput, removeConcept, toRequest,
} from './concepts.ts';

const build = (positive = [], negative = []) => {
  let concepts = [];
  for (const text of positive) concepts = addConcepts(concepts, text, 'positive');
  for (const text of negative) concepts = addConcepts(concepts, text, 'negative');
  return concepts;
};

test('one line can hold several concepts and loses no meaning to spacing', () => {
  assert.deepEqual(parseConceptInput('kidney disease,  dialysis\nSGLT2   inhibitor , '), [
    'kidney disease', 'dialysis', 'SGLT2 inhibitor',
  ]);
});

test('a concept is added once per side, case insensitively', () => {
  const concepts = build(['dialysis', 'Dialysis', 'kidney']);
  assert.deepEqual(bySign(concepts, 'positive').map((c) => c.text), ['dialysis', 'kidney']);
});

test('the same term may sit on both sides; the arithmetic decides what that means', () => {
  const concepts = build(['dialysis'], ['dialysis']);
  assert.equal(concepts.length, 2);
  assert.deepEqual(toRequest(concepts), { positive: ['dialysis'], negative: ['dialysis'], limit: 20 });
});

test('flipping moves a concept to the other side, and collapses a duplicate', () => {
  const concepts = build(['dialysis', 'kidney']);
  const flipped = flipConcept(concepts, concepts[1].id);
  assert.deepEqual(bySign(flipped, 'positive').map((c) => c.text), ['dialysis']);
  assert.deepEqual(bySign(flipped, 'negative').map((c) => c.text), ['kidney']);
  const collision = flipConcept(build(['dialysis'], ['dialysis']), 'positive:dialysis');
  assert.deepEqual(collision.map((c) => c.id), ['negative:dialysis']);
});

test('removing leaves the other concepts alone', () => {
  const concepts = build(['a term', 'another term']);
  assert.deepEqual(removeConcept(concepts, concepts[0].id).map((c) => c.text), ['another term']);
});

test('the expression reads as the arithmetic it runs', () => {
  assert.equal(expression(build(['kidney disease', 'SGLT2'], ['diabetes'])), 'kidney disease + SGLT2 − diabetes');
  assert.equal(expression(build(['kidney disease'])), 'kidney disease');
});

test('a query needs a positive concept and stays within the term bounds', () => {
  assert.match(conceptError([]), /at least one positive/);
  assert.equal(conceptError(build(['dialysis'])), null);
  assert.match(conceptError(build(['a'])), /too short/);
  assert.match(conceptError(build(['t1', 't2', 't3', 't4', 't5', 't6', 't7', 't8', 't9'])), /at most 8 positive/);
});

const match = (concepts) => ({
  id: 'W1', title: 't', year: 2020, url: '', source: 'openalex', verdict: 'effect',
  citations: 0, cosine: 0.4, concepts,
});

test('a result names what pulled it in and what should have pushed it away', () => {
  const signals = matchSignals(match([
    { text: 'kidney', sign: 'positive', cosine: 0.31 },
    { text: 'dialysis', sign: 'positive', cosine: 0.52 },
    { text: 'diabetes', sign: 'negative', cosine: 0.2 },
  ]));
  assert.equal(signals.nearestPositive.text, 'dialysis');
  assert.equal(signals.nearestNegative.text, 'diabetes');
  assert.equal(signals.contested, false);
});

test('a result closer to an excluded concept is flagged, not hidden', () => {
  const signals = matchSignals(match([
    { text: 'kidney', sign: 'positive', cosine: 0.3 },
    { text: 'diabetes', sign: 'negative', cosine: 0.44 },
  ]));
  assert.equal(signals.contested, true);
});

test('with no negative concepts nothing is contested', () => {
  const signals = matchSignals(match([{ text: 'kidney', sign: 'positive', cosine: 0.1 }]));
  assert.equal(signals.nearestNegative, null);
  assert.equal(signals.contested, false);
});

test('bar widths stay inside the track whatever the cosine', () => {
  for (const cosine of [-1, 0, 0.3, 0.6, 1]) {
    const width = cosineWidth(cosine);
    assert.ok(width >= 0 && width <= 1, String(cosine));
  }
  assert.ok(cosineWidth(0.5) > cosineWidth(0.2));
});
