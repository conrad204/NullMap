import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { american, britishWords, withoutProtected } from './spelling.mjs';

const ROOT = fileURLToPath(new URL('..', import.meta.url));
const EXTENSIONS = ['.ts', '.tsx', '.js', '.mjs', '.css', '.py', '.md', '.html'];
const SKIP_DIRS = new Set(['.git', 'node_modules', '.venv', 'dist', 'data', 'fixtures', 'output', '__pycache__']);

/**
 * Files allowed to keep a British spelling, with the reason. A `*` allows the whole file.
 * Everything here is either a term matched against outside text (literature and registry
 * wording is British as often as not), a third-party API, or a file this change was told
 * not to touch.
 */
const ALLOWED = {
  // The word lists and their fixtures are British spellings by definition.
  'scripts/spelling.mjs': '*',
  'scripts/spelling.test.mjs': '*',
  // TODO(american-english): owned by another change in flight; Americanize in a follow-up.
  'src/components/Composer.tsx': '*',
  'src/components/MapPanel.tsx': '*',
  'src/components/MapCanvas.tsx': '*',
  'src/lib/concepts.ts': '*',
  'src/lib/regions.ts': '*',
  'backend/app/gapmap.py': '*',
  'backend/app/gapmap_service.py': '*',
  'backend/tests/test_gapmap.py': '*',
  // TODO(american-english): `neighbour_threshold` lives in gapmap.py, which this change
  // was told not to touch; rename the doc wording with the symbol.
  'CLAUDE.md': ['neighbour'],
  // Search terms and scale names matched against published text, which is often British.
  'backend/app/ingest/scope.py': ['haemodialysis'],
  'backend/app/ingest/tei.py': ['standardised'],
  'backend/app/repository.py': ['randomised', 'paediatric', 'ageing', 'cancelled'],
  // Reads the key older checkpoints were written with.
  'backend/app/ingest/label.py': ['labelled'],
  // asyncio.CancelledError.
  'backend/app/main.py': ['cancelled'],
  'backend/app/pipeline.py': ['cancelled'],
  'backend/tests/test_pipeline.py': ['cancelled'],
};

const files = function walk(directory) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    if (entry.name.startsWith('.') || SKIP_DIRS.has(entry.name)) return [];
    const path = join(directory, entry.name);
    if (entry.isDirectory()) return walk(path);
    return EXTENSIONS.some((extension) => entry.name.endsWith(extension)) ? [path] : [];
  });
}(ROOT);

test('the tokenizer reads identifiers and prose with the same rules', () => {
  assert.equal(american('favours'), 'favors');
  assert.equal(american('unfavourable'), 'unfavorable');
  assert.equal(american('normalisation'), 'normalization');
  assert.equal(american('unrecognised'), 'unrecognized');
  assert.equal(american('labelling'), 'labeling');
  assert.equal(american('judgement'), 'judgment');
  assert.deepEqual(
    britishWords('const favouriteColour = "grey";').map(({ token, suggestion }) => [token, suggestion]),
    [['favourite', 'favorite'], ['colour', 'color'], ['grey', 'gray']],
  );
});

test('words American English spells the same way are not flagged', () => {
  // -ise/-lled rules are stem lists, not suffix guesses, and analysis is not a verb.
  for (const word of ['analysis', 'analyses', 'precise', 'promises', 'exercises', 'wise',
                      'controlled', 'compelled', 'patrolling', 'labeled', 'modeling',
                      'color', 'behavior', 'center', 'program', 'four', 'hour', 'pouring']) {
    assert.equal(american(word), null, word);
  }
});

test('stored verdict values keep their indexed spelling', () => {
  assert.deepEqual(britishWords(withoutProtected('resultDirection === "favours_intervention"')), []);
  assert.deepEqual(britishWords(withoutProtected('{ favoursIntervention, pFavours }')), []);
  assert.equal(britishWords(withoutProtected('the study favours it')).length, 1);
});

test('no new British spellings in source, docs or backend', () => {
  const violations = [];
  for (const path of files) {
    const name = relative(ROOT, path).split(sep).join('/');
    const allowed = ALLOWED[name];
    if (allowed === '*') continue;
    const text = withoutProtected(readFileSync(path, 'utf8'));
    for (const found of britishWords(text, new Set(allowed ?? []))) {
      violations.push(`${name}:${found.line} ${found.token} -> ${found.suggestion}`);
    }
  }
  assert.deepEqual(violations, [], `British spellings found:\n${violations.join('\n')}`);
});
