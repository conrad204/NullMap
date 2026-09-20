import assert from 'node:assert/strict';
import test from 'node:test';
import {
  addConcepts, addSignedConcepts, bySign, conceptError, expression, flipConcept, fromSteer,
  parseConceptInput, parseSignedInput, removeConcept, toSteer,
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
  assert.deepEqual(toSteer(concepts), { positive: ['dialysis'], negative: ['dialysis'] });
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

test('tags are optional, and only their bounds can be wrong', () => {
  assert.equal(conceptError([]), null, 'the question alone is a search');
  assert.equal(conceptError(build([], ['diabetes'])), null, 'a negative tag alone still steers');
  assert.match(conceptError(build(['a'])), /too short/);
  assert.match(conceptError(build(['t1', 't2', 't3', 't4', 't5', 't6', 't7', 't8', 't9'])), /at most 8 positive/);
});

test('tags ride on the search only when there are some', () => {
  assert.equal(toSteer([]), null);
  assert.deepEqual(toSteer(build(['kidney'], ['diabetes'])), { positive: ['kidney'], negative: ['diabetes'] });
  assert.deepEqual(toSteer(build([], ['diabetes'])), { positive: [], negative: ['diabetes'] });
});

test('the tags a result was steered by read back as the same chips', () => {
  const steered = fromSteer({ positive: ['kidney'], negative: ['diabetes'] });
  assert.equal(expression(steered), 'kidney − diabetes');
  assert.deepEqual(fromSteer(null), []);
  assert.deepEqual(fromSteer({ positive: [], negative: [] }), []);
});

test('a typed term carries its own sign, and keeps the fallback without one', () => {
  assert.deepEqual(parseSignedInput('SGLT2, -diabetes, − obesity, +kidney', 'positive'), [
    { text: 'SGLT2', sign: 'positive' },
    { text: 'diabetes', sign: 'negative' },
    { text: 'obesity', sign: 'negative' },
    { text: 'kidney', sign: 'positive' },
  ]);
  assert.deepEqual(parseSignedInput('dialysis', 'negative'), [{ text: 'dialysis', sign: 'negative' }]);
  assert.deepEqual(parseSignedInput('-', 'positive'), []);
});

test('one field fills both sides of the steer', () => {
  const concepts = addSignedConcepts([], 'kidney disease, −diabetes', 'positive');
  assert.equal(expression(concepts), 'kidney disease − diabetes');
  assert.deepEqual(toSteer(concepts).negative, ['diabetes']);
});
