# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

nullMap (HackMIT 2026) finds prior clinical studies, surfaces null and unreported results, and estimates whether a proposed study is adequately powered. Three independent pieces live in one repo:

- **`backend/`** — FastAPI service + offline ingestion (Python 3.12, `uv`). Elasticsearch is the only datastore: documents, vectors, LLM extraction cache.
- **`src/`** — React 19 + Vite + Tailwind 4 frontend (root `package.json`).
- **`evidence_workflow/`** — a standalone `hatchling` package for heterogeneity-aware meta-analysis and "research redundancy" scoring. It shares no code with `backend/`; it was added separately and has its own README, tests, and CLI. Its statistical policy differs from the backend on purpose (REML + Hartung–Knapp, reconstructed effects only in sensitivity analyses), so do not copy rules between the two without checking.

Docs worth reading before nontrivial changes: `docs/ARCHITECTURE.md` (the design rationale), `backend/app/ingest/README.md` (S3 ingestion, checkpoints, scope), `docs/VALIDATION.md` (what has and has not been measured).

## Commands

Frontend (repo root):

```sh
npm ci
npm run dev          # Vite on :5173, proxies /api -> http://127.0.0.1:8000 (strips the /api prefix)
npm run build        # tsc --noEmit && vite build -> dist/
npm run typecheck
npm test             # node:test files under src/api and src/lib (node:test, no framework)
node src/api/stream.test.mjs   # run one frontend test file
```

Backend (`cd backend`). Use `.venv/bin/...` directly or `uv run --no-sync ...`; a plain `uv sync`/`uv run` can uninstall the separately installed torch/sentence-transformers. Use `uv sync --inexact` to refresh core deps.

```sh
uv sync
uv pip install torch --index-url https://download.pytorch.org/whl/cpu
uv pip install sentence-transformers scikit-learn
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
.venv/bin/pytest -q                              # ~320 tests in ~2s, no ES or network needed; the 5 skips are the real-ES tests
.venv/bin/pytest tests/test_statistics.py -q     # one file
.venv/bin/pytest tests/test_pipeline.py -q -k name  # one test
NULLMAP_TEST_ELASTIC_URL=http://127.0.0.1:9200 .venv/bin/pytest tests/test_repository.py -q  # real ES, isolated indexes
.venv/bin/ruff check app tests                   # line-length 100; rules E4,E7,E9,F,I,UP
```

Ingestion and bootstrap (`cd backend`):

```sh
.venv/bin/python -m app.bootstrap --plan         # default; reads only the public S3 manifest
.venv/bin/python -m app.bootstrap --run --max-files 1 --max-records 100 --registry-limit 20 --data-dir data/s3-smoke
# --largest-first orders parts by size so a partial --max-files run covers more bytes; still a subset of update partitions, not a random sample
.venv/bin/python -m app.ingest --help            # snapshot | fetch ctgov | normalize | classify | embed | link | index | reference-ids | tei | label | train-classifier
.venv/bin/python -m app.benchmark search --idea '...' --output data/search-benchmark.json
.venv/bin/python -m app.novelty '<hypothesis>' --scan 120 --read 5   # novelty-gap CLI, same engine as POST /novelty
```

Local Elasticsearch: `docker compose up -d elasticsearch` (9.1.4, security disabled, bound to localhost). `docker compose up --build -d` also runs the API image with `./dist` mounted at `/web`.

evidence_workflow (`cd evidence_workflow`):

```sh
pip install -e .          # only needed for the evidence-workflow CLI; tests run from the backend venv below
PYTHONPATH=src ../backend/.venv/bin/python -m unittest discover -s tests -v
evidence-workflow examples/studies.csv --sesoi-low -0.2 --sesoi-high 0.2 --output-dir output
```

Environment: settings load from `backend/.env` via pydantic-settings (`app/config.py`); the frontend reads `VITE_API_URL` (default `/api`) and `VITE_USE_MOCK` from root `.env`. Both `.env` files already exist and are gitignored; copy from `.env.example` only on a fresh checkout with `cp -n`. Never put service keys in `VITE_*` variables. `keys.txt` is gitignored and denied by the Vite dev server.

## Architecture

The central idea: **move expensive reading from query time to index time.** Result direction, buckets, and embeddings are precomputed per document; query-time bucket counts are an Elasticsearch aggregation over the full match set, costing zero LLM calls. The LLM is used only for PICO parsing, extracting numbers from the handful of top uncached papers (written back to ES so each paper is read at most once), and narration over a compact table.

### Offline ingestion (`backend/app/ingest/`, `bootstrap.py`)

Two sources, one index:
- **OpenAlex** comes exclusively from the anonymous public S3 Parquet snapshot, read with DuckDB column projection (`snapshot.py`). There is no OpenAlex API path. Partitions are by update date, not topic, so scope filtering (`scope.py`, versioned `hypertension-kidney-v1`: topic IDs + word regex, all years/types, metadata-only works retained) runs over every part. Partition quality varies enormously (some parts have ~0% PMIDs and mismatched title/abstract records); the opt-in `hypertension-kidney-pubmed` profile keeps only PubMed-indexed works, which PMID linking and PMCID full text require. `ids` has no `pmcid` in the snapshot, so `work_pmcid` recovers it from PMC location URLs. Abstracts arrive as inverted indexes and are rebuilt (`openalex.py`).
- **ClinicalTrials.gov** API v2 (`ctgov.py`, `fetch.py`), keyless, exhausts a condition-union cursor. Results are already structured (p, estimate, CI), so no LLM.

Every stage is resumable JSONL with sidecar `*.checkpoint.json` files keyed by a signature (input path/size/mtime + stage version). Changing the input, scope, model, or target index invalidates the checkpoint on purpose. `transform_file` in `ingest/__main__.py` is the generic batch/checkpoint driver; `materialize.py` is the bounded index-backed path bootstrap uses; the standalone `link` command loads everything into memory and is only for small slices.

Stage order: snapshot/fetch → normalize (`flatten_trial` / `normalize_work`) → classify (`classifier.py`) → embed (`embeddings.py`) → link (`linker.py`: PMID and `NCT\d{8}` matching, produces `source: merged` rows with `canonical_id` redirects) → index (`repository.bulk_upsert` + `assign_bucket`).

### Online pipeline (`backend/app/pipeline.py`)

`SearchPipeline._search` runs: `llm.parse` (PICO + SESOI, falls back to raw idea) → `repo.retrieve` (hybrid BM25 + kNN fused by RRF, or BM25-only when embeddings are off) → `expand` (top 3 indexed reviews' `referenced_works`, re-scored by cosine against the query vector, screened by population; only already-indexed works, missing ones are reported as warnings) → `registry_sweep` → `link_studies` at query time (newly discovered links are persisted) → `screen` (one small-model yes/no relevance pass per 40 retrieved studies, up to `SCREEN_LIMIT`, run before extraction so irrelevant keyword matches are never paid for; fails open with a warning; when it covered the whole match set, `recount` replaces the keyword-match bucket counts, otherwise a warning says the counts are unscreened) → `extract_one` for top `extraction_limit` openalex/merged studies (per-document `asyncio.Lock`; cache is fresh when the stored `extraction_version` equals `EXTRACTION_CACHE_VERSION`, so bump that setting whenever the extraction prompt or evidence-line format changes; if the paper has a `pmcid`, `fulltext.py` fetches Europe PMC JATS XML and flattens abstract + primary-outcome methods sentences + table rows + results prose into verbatim lines, otherwise the abstract is split into sentences; quoted evidence must be a verbatim line or the extraction is `rejected`, except that an unsupported total `n`, `p_value`, `reported_result` or arm-level summary is dropped on its own (arm summaries as a whole group) because losing those can only remove information; numbers written in words or space-grouped digits count as verbatim; `fulltext_status` of `used`/`unavailable` stops re-fetching, and an abstract-only cache entry is upgraded to full text exactly once; a failed re-extraction keeps the previous facts as `retained_verified`) → `repo.aggregate` (bucket counts, year histogram, `significant_text` null terms, spin rate over the full match set) → `group_outcomes` (LLM comparability judgement over studies that already have a standard error) → `analyze_studies` (`statistics.py`, pure Python) → `effect_trend` → optional `llm.narrate` only when a compatible numeric pool exists.

The population filter (`repository.population_query`) defines the full match set, so it drives bucket counts and review-reference screening, not just ranking. It requires every specific condition term, matches a small explicit stem list by prefix (`_QUERY_STEMS`, the index analyzer does not stem), and accepts parser-supplied `populationAliases` as alternatives to the condition clause only. Alias breadth is guarded structurally in code and semantically only by the PICO prompt.

Registry primary-outcome numbers are authoritative and never overwritten by paper extraction. Reviews are excluded from study lists and pools.

`repository._prepare_document` decides what survives a re-ingest or partial update via three field sets (`_CACHE_FIELDS`, `_LINK_FIELDS`, `_PAPER_FIELDS`). A new per-document field usually has to be added in four places: the right field set, `index_mapping`, `pipeline.to_paper`, and `src/types.ts`. Cached extraction is kept only while the abstract is unchanged and no new registry numbers arrive.

### API surface (`backend/app/main.py`)

Routes are `/health`, `/ready`, `/search`, `/search/stream`, `/studies/{id}`, `/novelty`, `/map`. Every route is registered twice: bare (`/search`) and prefixed (`/api/search`, hidden from schema). Vite strips `/api` in dev; the built `dist/` is served by FastAPI at `/` when present (`FRONTEND_DIST`). `/search/stream` is SSE over POST with `progress`, `result`, `error` events and `: keepalive` comments; the frontend falls back to plain `/search` on 404/405.

### Gap map (`backend/app/gapmap.py`, `gapmap_service.py`)

`POST /map` describes the corpus rather than a query: `repository.sample_embedded` takes a deterministic `random_score` sample of embedded studies, `build_regions` clusters them with a dependency-free spherical k-means, and each region is labelled by *why* nothing new is there — `null_saturated`, `dark`, `contested`, `active`, `unread`, `thin` (`label_region`, reviews excluded from attempts). `unread` is the fallback rather than `active`: on the live index most regions carry no readable outcome at all, and calling those active would claim a literature nobody read. `find_gaps` interpolates: the midpoint between two neighbouring centroids is a gap when the two regions' own members almost never prefer the midpoint to either parent, and no third centroid sits in the band; adjacency is the top decile of pairwise centroid cosine (`neighbour_threshold`), not a constant, because "close" depends on the embedding model and corpus breadth. Whether a band is empty depends on `GAPMAP_REGIONS`, which is a resolution setting: measured on the live index, 24 regions yield none and 80 yield a handful. `place` returns the cosine to the nearest indexed paper as `redundancy` — a redundancy statistic, never a probability of novelty. `calibrate` rebuilds the map before a cutoff year and reports what later papers did per historical label; it is calibration, not prediction. The service caches one map, strips centroids from the payload, and warns whenever the sample is smaller than the embedded corpus.

### Statistics and bucket rules (`backend/app/statistics.py`)

Six buckets: `effect`, `credible_null`, `reported_null`, `inconclusive`, `failed`, `unreported`. Rules are in the ARCHITECTURE doc section 7 and encoded in `assign_bucket`. Key invariants that tests enforce and that code changes must preserve:
- A textual "no significant difference" is `reported_null`, never `credible_null`; only a CI inside the SESOI establishes equivalence. The text-level label is the extraction's quoted `reported_result` when present, else the classifier's `result_label`. Missing reports are `unreported` with unknown outcome, never null findings.
- `inconclusive` is not a finding: every such verdict carries an `inconclusive_reason` (`statistics.INCONCLUSIVE_REASONS`; only `wide_interval` describes the study, the rest describe what could be read). `BUCKET_SCRIPT` emits `inconclusive:<reason>` and `aggregate` folds it into `bucketCounts` plus `inconclusiveReasons`; the UI keeps it out of the verdict bar (`BAR_VERDICTS`) and shows the count with its reasons instead.
- A text-level verdict needs a comparison group: `reported_result` is honoured only with a quoted `has_control` (or a registry row), and a paper that was read (`extraction_status: verified`) without one gets no verdict from the lexicon either. This keeps case reports out of `effect`; `BUCKET_SCRIPT` mirrors it.
- `effect` means a significant difference in either direction, so extraction also records a quoted `result_direction` (`favours_intervention` / `favours_comparator` / `unclear`). Do not reuse `effect_direction`/`outcome_direction` for it: those are part of the pool key, and keying pools on which arm won would pool only the winners.
- The LLM may judge comparability but never computes. `llm.group_outcomes` (narration model, anchored to the query PICO, ungrouped by default) only maps study ids to a shared label that replaces the exact-text outcome/comparison match in `analyze_studies`; scale and MD-unit checks and all arithmetic stay in code, and such pools carry `grouping: "model"` plus a warning. When fewer than two read studies report an effect there is no trend, so `llm.overview` instead explains what the matches are and why each does or does not answer the question, from extracted fields plus each verdict and rationale (never abstracts); `evidenceBase.controlled` lets the headline say "nothing tested this directly" when no read study had a comparison group. `llm.trend` writes the "what the effects have in common" prose from extracted facts and quotes only; direction tallies are counted in `pipeline.effect_trend`, and prose containing a number absent from its table is rejected. Both calls are best-effort and degrade to a warning.
- Evidence tiers are `numeric` > `derived` > `reconstructed` > `text_only`. `derive_effects` computes Hedges' g / MD from arm means, SDs and sizes and log OR / log RR from event counts; it imputes nothing, never derives an HR, discards |d| > 5 as a standard error reported as an SD, and never replaces a reported interval on the requested scale. Documents store the scale-neutral `stored_analysis` plus `derived_<scale>_*` fields so `BUCKET_SCRIPT` can bucket any query scale; the real-ES parity test must keep agreeing with `assign_bucket`.
- OR/RR/HR are analyzed on the log scale; a ratio SESOI is a multiplicative margin > 1. Never pool across effect types or mix raw ratios with logs (the frontend mirrors this in `src/lib/effects.ts`).
- Pooling needs ≥3 compatible primary studies (DerSimonian–Laird via statsmodels, τ² clamped ≥0). Without a pool, assurance and required N are `null`, not invented.
- Reconstruction from p-value needs an estimate plus an exact (not bounded) p. p-value inequalities and CI levels are preserved as `p_value_operator` / `ci_level`.
- Assurance is expected two-sided power, not probability of clinical benefit.

### Classifier (`backend/app/classifier.py`)

The active classifier is the heuristic phrase lexicon (`weak_classify`, `weak-v2`). `null_score` is explicitly *not* a probability. A trained pilot was evaluated and not promoted; `CLASSIFIER_MODEL_PATH` stays empty unless a new model passes evaluation. Labels: `positive`, `null`, `mixed`, `no_result_stated`.

### Frontend (`src/`)

`App.tsx` owns a `Status` discriminated union (idle/searching/done/error) and an `AbortController`. The search tab is one column: idle shows the centered `IdeaComposer`, which stays mounted but hidden in every other state so the draft survives "Edit question"; on submit it reports where the typed text sat on screen (`GlideOrigin`) and `QuestionHeader` plays a FLIP glide from there to the top, with progress and then the report rendered under it. The header, not `ResultsView`, shows the question. `api/client.ts` handles SSE via `api/stream.ts` (hand-rolled parser that survives split UTF-8 and CRLF). `types.ts` is the JSON contract with the backend; `pipeline.to_paper` produces the `Paper` shape. Mock fixtures (`api/mock.ts`) are fictional and only reachable when `VITE_USE_MOCK=true`; API failures must surface as errors, never fall back to mock data. `lib/verdicts.ts` holds bucket labels/colors as full Tailwind class names so the scanner picks them up.

## Conventions and constraints

- **Honesty about coverage is a product requirement.** Partial runs (`--max-files`, `--max-records`, `--registry-limit`) never set `complete_snapshot_scope=true`. Do not describe a partial corpus as complete, and do not soften warnings the pipeline emits. `docs/VALIDATION.md` and `backend/tests/fixtures/README.md` distinguish measured checks from synthetic fixtures; keep that distinction when editing docs.
- Cost figures in `costs` are estimates from configured per-token prices, not bills. The compression experiment (`COMPRESSION_ENABLED`) stays off unless `COMPRESSION_VALIDATED` is also true and the paired benchmark justifies it.
- Bootstrap performs no paid LLM calls. `label` and `train-classifier` are explicit experiments.
- Indexing and query embedding models must be identical (`EMBEDDING_MODEL`); `embedding_model` is stored per document and checked by materialization signatures.
- Ingestion never stores full text. Full text is fetched lazily at query time from Europe PMC only for papers with a PMCID, and only the extracted facts plus `extraction_source` are cached. Discussion and conclusion sections are excluded from evidence lines on purpose.
- `backend/data/` is gitignored and holds local run artifacts; do not depend on its contents in tests.
- Frontend tests are plain `node:test` files importing `.ts` directly (Node type-stripping); keep them dependency-free.
