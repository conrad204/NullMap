import assert from 'node:assert/strict';
import test from 'node:test';
import { EMPTY_DRAFT, EMPTY_STATE, afterSearch, describeFilters, filterError, loadFilterState, parseFilters, saveFilterState } from './filters.ts';

const draft = (values) => ({ ...EMPTY_DRAFT, ...values });

test('an untouched form sends no filter at all', () => {
  assert.equal(parseFilters(EMPTY_DRAFT), undefined);
  assert.equal(parseFilters(draft({ yearFrom: '   ', maxCitations: '' })), undefined);
  assert.equal(parseFilters(draft({ minCitations: 'abc' })), undefined);
});

test('each bound is independent and zero is a bound, not an absence', () => {
  assert.deepEqual(parseFilters(draft({ yearFrom: '2015' })), { yearFrom: 2015 });
  assert.deepEqual(parseFilters(draft({ maxCitations: '0' })), { maxCitations: 0 });
  assert.deepEqual(parseFilters(draft({ yearTo: ' 2020 ', minCitations: '5.9' })), { yearTo: 2020, minCitations: 5 });
});

test('the draft is rejected on the same grounds the API rejects it', () => {
  assert.equal(filterError(EMPTY_DRAFT), null);
  assert.equal(filterError(draft({ yearFrom: '2000', yearTo: '2020', minCitations: '5', maxCitations: '5' })), null);
  assert.match(filterError(draft({ yearFrom: '2020', yearTo: '2010' })), /not be later/);
  assert.match(filterError(draft({ minCitations: '9', maxCitations: '2' })), /not exceed/);
  assert.match(filterError(draft({ yearFrom: '900' })), /between 1500 and 2100/);
  assert.match(filterError(draft({ minCitations: '-3' })), /cannot be negative/);
});

test('active bounds are labelled for display beside the question', () => {
  assert.deepEqual(describeFilters(undefined), []);
  assert.deepEqual(describeFilters({ yearFrom: 2015, yearTo: 2020 }), ['Published 2015\u20132020']);
  assert.deepEqual(describeFilters({ yearFrom: 2015 }), ['Published 2015 or later']);
  assert.deepEqual(describeFilters({ yearTo: 1999 }), ['Published 1999 or earlier']);
  assert.deepEqual(describeFilters({ minCitations: 5 }), ['\u2265 5 citations']);
  assert.deepEqual(describeFilters({ maxCitations: 50 }), ['\u2264 50 citations']);
  assert.deepEqual(describeFilters({ yearFrom: 2015, minCitations: 5, maxCitations: 50 }), ['Published 2015 or later', '5\u201350 citations']);
});

test('only sticky filters survive a search; the toggle itself always does', () => {
  const values = draft({ yearFrom: '2015', minCitations: '5' });
  assert.deepEqual(afterSearch({ draft: values, sticky: true }), { draft: values, sticky: true });
  assert.deepEqual(afterSearch({ draft: values, sticky: false }), { draft: EMPTY_DRAFT, sticky: false });
});

test('stored state round-trips, and anything else falls back to unfiltered', () => {
  // Storage is absent in this runtime unless stubbed; that must not throw.
  assert.deepEqual(loadFilterState(), EMPTY_STATE);
  const store = new Map();
  globalThis.localStorage = { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => store.set(key, value) };
  try {
    const state = { draft: draft({ yearTo: '2020' }), sticky: true };
    saveFilterState(state);
    assert.deepEqual(loadFilterState(), state);
    for (const stored of ['not json', 'null', '{"draft":{"yearFrom":2015},"sticky":"yes"}']) {
      store.set('nullmap-filters', stored);
      assert.deepEqual(loadFilterState(), EMPTY_STATE);
    }
  } finally {
    delete globalThis.localStorage;
  }
});
