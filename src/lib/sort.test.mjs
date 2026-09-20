import assert from 'node:assert/strict';
import test from 'node:test';
import { SORT_OPTIONS, sortPapers } from './sort.ts';

const paper = (id, verdict, values = {}) => ({ id, verdict, year: 2020, sampleSize: 100, citations: 0, ...values });
const ids = (papers) => papers.map(({ id }) => id).join('');

const papers = [
  paper('a', 'inconclusive', { year: 2024, sampleSize: 9000, citations: 500 }),
  paper('b', 'unreported', { year: 0, sampleSize: null }),
  paper('c', 'reported_null', { year: 2011, sampleSize: 40, citations: 12 }),
  paper('d', 'effect', { year: 2019, sampleSize: 800, citations: 3 }),
  paper('e', 'inconclusive', { year: 2001, sampleSize: 20 }),
  paper('f', 'credible_null', { year: 2019, sampleSize: null, citations: 90 }),
  paper('g', 'failed', { year: 2015, sampleSize: 12 }),
];

test('answer order follows the bar and leaves inconclusive for last', () => {
  assert.equal(ids(sortPapers(papers, 'answer')), 'dfcgbae');
});

test('inconclusive studies come last in every order', () => {
  for (const { key } of SORT_OPTIONS) assert.match(ids(sortPapers(papers, key)), /^[bcdfg]{5}[ae]{2}$/, key);
});

test('relevance keeps the returned order apart from the inconclusive studies', () => {
  assert.equal(ids(sortPapers(papers, 'relevance')), 'bcdfgae');
});

test('ties keep the returned order and unknown values sort last in both directions', () => {
  assert.equal(ids(sortPapers(papers, 'newest')), 'dfgcbae');
  assert.equal(ids(sortPapers(papers, 'oldest')), 'cgdfbea');
  assert.equal(ids(sortPapers(papers, 'sample')), 'dcgbfae');
  assert.equal(ids(sortPapers(papers, 'citations')), 'fcdbgae');
});

test('sorting returns a new array and leaves the input alone', () => {
  const before = ids(papers);
  assert.notEqual(sortPapers(papers, 'answer'), papers);
  assert.equal(ids(papers), before);
});
