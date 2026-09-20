import assert from 'node:assert/strict';
import test from 'node:test';
import {
  addConcepts, bySign, composeLine, conceptError, expression, flipConcept, fromSteer,
  parseConceptInput, parseLine, removeConcept, tagRanges, tagSegments, toSteer,
} from './concepts.ts';

const QUESTION = 'Does renal artery stenting improve blood pressure or kidney function in adults with atherosclerotic renal artery stenosis?';
const read = (line) => {
  const { question, concepts } = parseLine(line);
  return { question, positive: bySign(concepts, 'positive').map((c) => c.text), negative: bySign(concepts, 'negative').map((c) => c.text) };
};

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

test('a line with no signed term is all question', () => {
  assert.deepEqual(read(QUESTION), { question: QUESTION, positive: [], negative: [] });
  assert.deepEqual(read('  '), { question: '', positive: [], negative: [] });
});

test('signed terms trailing the question are its tags, and the question keeps its own words', () => {
  assert.deepEqual(read(`${QUESTION} +blood pressure −stroke`), {
    question: QUESTION, positive: ['blood pressure'], negative: ['stroke'],
  });
  assert.deepEqual(read(`${QUESTION} -diabetes`), { question: QUESTION, positive: [], negative: ['diabetes'] });
  assert.deepEqual(read(`${QUESTION} +kidney outcomes +quality of life`), {
    question: QUESTION, positive: ['kidney outcomes', 'quality of life'], negative: [],
  });
});

test('every dash spelling steers, and the tag may be all there is', () => {
  assert.deepEqual(read(`${QUESTION} -a −b –c`).negative, ['a', 'b', 'c']);
  assert.deepEqual(read('+dialysis'), { question: '', positive: ['dialysis'], negative: [] });
});

test('a dash inside prose stays prose', () => {
  const hyphenated = 'Does renal-artery stenting help adults with well-controlled blood pressure?';
  assert.deepEqual(read(hyphenated), { question: hyphenated, positive: [], negative: [] });
  assert.deepEqual(read('Is a - b the right comparison for these trials?'), {
    question: 'Is a - b the right comparison for these trials?', positive: [], negative: [],
  });
  assert.deepEqual(read('Does the trial report a 5 - 10 mmHg drop over 12-24 weeks?').negative, []);
  assert.deepEqual(read('Dash on its own - and nothing after it').negative, []);
});

test('a tag typed into a sentence sheds the sentence\u2019s punctuation', () => {
  assert.deepEqual(read(`${QUESTION} +blood pressure -(something).`), {
    question: QUESTION, positive: ['blood pressure'], negative: ['something'],
  });
  assert.deepEqual(read(`${QUESTION} +"kidney outcomes", −mortality!`), {
    question: QUESTION, positive: ['kidney outcomes'], negative: ['mortality'],
  });
});

test('the line is the source of truth: editing a chip rewrites it and reads back the same', () => {
  const line = `${QUESTION} +blood pressure −stroke`;
  const { question, concepts } = parseLine(line);
  assert.equal(composeLine(question, concepts), line);
  const flipped = composeLine(question, flipConcept(concepts, concepts[0].id));
  assert.deepEqual(read(flipped), { question: QUESTION, positive: [], negative: ['blood pressure', 'stroke'] });
  const dropped = composeLine(question, removeConcept(concepts, concepts[0].id));
  assert.deepEqual(read(dropped), { question: QUESTION, positive: [], negative: ['stroke'] });
  assert.equal(composeLine(QUESTION, []), QUESTION);
});

const marked = (line) => tagRanges(line).map((range) => `${range.sign[0]}${line.slice(range.start, range.end)}`);

test('the highlighted ranges are the tags the search is sent, sign included', () => {
  const line = `${QUESTION} +kidney outcomes −type 2 diabetes`;
  assert.deepEqual(marked(line), ['p+kidney outcomes', 'n−type 2 diabetes']);
  const { concepts } = parseLine(line);
  assert.deepEqual(
    tagRanges(line).map((range) => line.slice(range.start + 1, range.end)),
    concepts.map((concept) => concept.text),
  );
});

test('prose is never highlighted', () => {
  assert.deepEqual(tagRanges('Does renal-artery stenting help well-controlled adults?'), []);
  assert.deepEqual(tagRanges('Is a - b the right comparison for these trials?'), []);
  assert.deepEqual(tagRanges('Does it drop 5 - 10 mmHg over 12-24 weeks?'), []);
  assert.deepEqual(tagRanges(''), []);
});

test('a highlighted tag stops short of the punctuation trailing it', () => {
  assert.deepEqual(marked(`${QUESTION} +"kidney outcomes", −mortality!`), ['p+"kidney outcomes', 'n−mortality']);
  assert.deepEqual(marked(`${QUESTION} +blood pressure -(something).`), ['p+blood pressure', 'n-(something']);
  assert.deepEqual(marked(`${QUESTION} +kidney outcomes   `), ['p+kidney outcomes']);
});

test('every character of the line survives the split, tagged or not', () => {
  for (const line of [
    `${QUESTION} +kidney outcomes −type 2 diabetes`,
    `${QUESTION} +"kidney outcomes", −mortality! trailing`,
    'Does renal-artery stenting help adults with a - b comparisons?',
    '+dialysis',
    '',
  ]) {
    const segments = tagSegments(line);
    assert.equal(segments.map((segment) => segment.text).join(''), line);
    assert.deepEqual(
      segments.filter((segment) => segment.sign).map((segment) => `${segment.sign[0]}${segment.text}`),
      marked(line),
    );
  }
});
