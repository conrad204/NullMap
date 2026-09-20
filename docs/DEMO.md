# nullMap demo guide

This demo maps indexed OpenAlex literature and ClinicalTrials.gov records, then uses compatible numerical evidence to help plan a clinical study. A missing result is an unknown outcome, not a negative finding.

## Start the application

Use the existing, ignored `backend/.env` for service credentials. Do not copy an example file over an already configured file, put keys in browser variables, or display credentials while recording.

From the repository root, start the API:

```sh
cd backend
uv sync --inexact
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

In a second terminal at the repository root:

```sh
npm install
npm run dev
```

Open `http://localhost:5173`. The browser uses `/api`; Vite strips that prefix and forwards requests to port 8000. For the bundled deployment, run `npm run build` first: FastAPI serves `dist/` and `/api` on the same origin at port 8000. Separate hosting can use a reverse proxy or `VITE_API_URL` pointing at the backend root URL. `VITE_USE_MOCK` defaults to false. API failures produce an error state, never fictional evidence.

The API needs a reachable Elasticsearch deployment with a populated index.
OpenAI enables structured PICO parsing, evidence extraction and narration.
OpenAlex uses the public S3 snapshot exclusively, with no OpenAlex API key.
ClinicalTrials.gov uses its separate keyless API. Embeddings run on the host
running Python; serving FastAPI on remote compute makes query inference remote.
Complete the CPU dependency setup in [README.md](../README.md) on that host.
Check readiness and coverage before recording.

## Prepare the hypertension/kidney corpus

Follow the [S3 ingestion guide](../backend/app/ingest/README.md). Begin with a
manifest-only plan on the batch host:

```sh
.venv/bin/python -m app.bootstrap --plan
```

The full workflow needs persistent output/index storage and an explicit scan
budget. It scans the hypertension/kidney union across all years, retains records
without abstracts, and writes resumable provenance and coverage reports. The
snapshot contains metadata and available abstracts, not full-text PDFs.

For a deliberately small demonstration, inspect and run a capped import:

```sh
.venv/bin/python -m app.bootstrap --plan --max-files 1
.venv/bin/python -m app.bootstrap --run --max-files 1 --max-records 100 \
  --registry-limit 20 --data-dir data/s3-smoke
```

A date partition may contain few or no relevant works. It does not represent
complete topic coverage. Inspect `data/s3-smoke/corpus-summary.json` and select
a question represented in the actual index. Previous vitamin D/depression,
omega-3/cognition and knee-surgery demo records are historical slices; the S3
migration does not silently replace them or claim a full renal corpus is loaded.

Search uses stored review references. Missing references are reported and can
be imported with the guide's offline S3 backfill; no query makes OpenAlex API
calls. The default classifier remains a phrase heuristic. For a lexical-only
build, use `--skip-embeddings` and serve with `EMBEDDINGS_ENABLED=false`.

Show actual logs, counts and resource identity before claiming a full scan or
sponsor-compute deployment. The checked 20-record S3 read proves connectivity
and decoding, not corpus completeness.

## A five-minute live walkthrough

1. Enter a question covered by the indexed topic, such as “Does intensive blood pressure control slow chronic kidney disease progression?” after that corpus is loaded Use SMD only when standardized continuous outcomes fit the study question. Leave SESOI blank to show the proposed value and rationale, or supply a prespecified threshold.
2. Open **Study plan & value**. Explain total N, two-sided alpha, and the equal-arm planning assumption. Put success value, null-result value, and study cost in the same units. Raw EV is utility, not a percentage.
3. Search. Point to the returned source coverage, retrieval mode, and warnings. The full lexical-match bucket aggregation is distinct from the displayed result page and any semantic/reference-expanded records. Counts reflect the available index.
4. Open a source study. Check its exact evidence span, source URL, evidence tier, original CI level, and p-value inequality. Show why “no significant difference” without a compatible narrow CI remains inconclusive. Raw ratios appear in the study row; the forest plot uses the pool's analysis scale.
5. Show the meaningful-effect threshold and the quantitative panel. Pools require at least three compatible primary studies. Different effect types, outcomes, and units stay separate. If pooling is unavailable, say so; do not change the question solely to manufacture a number.
6. Explain the registry reporting gap. These trials have unknown outcomes. It is a measured count within the indexed registry/linking coverage, not an estimate that every unreported trial was null.
7. Change N or SESOI and rerun. Explain how the study plan changes assurance and EV, and how SESOI changes the interpretation of the evidence. Assurance is Bayesian expected statistical power under the stated model, not the probability of a clinically meaningful benefit. Show N for 80% assurance only when returned.
8. Expand **Query cost**, record the first run, then repeat the identical query. Compare actual token counts, extraction-cache hits, new extractions, and estimated model spend. The “read 200 abstracts,” cold, and warm columns are modeled cost comparisons; a second observed run is needed to support a measured cache-saving claim.

## Capture evidence for the presentation

Save the first and repeated search JSON responses or browser network responses with the question and plan recorded. Preserve the indexed corpus size, source mix, date, retrieval mode, exact study IDs, and configuration assumptions alongside the run. Avoid recording authorization headers.

| Claim | Evidence to show | What it does not establish |
| --- | --- | --- |
| Full-match counts use Elasticsearch | Response counts/scope and aggregation implementation | Exhaustive worldwide literature coverage |
| Extraction is cached | First/repeated run token use, extraction counts, and cache-hit counts | A guaranteed savings percentage on all topics |
| A result is a credible null | Verifiable numeric interval, scale, and SESOI | Absence of every possible benefit |
| Registry reporting gap | Eligible completed count and missing-report count | That unknown outcomes were negative |
| A model classifier performs well | Held-out metrics and label provenance | Performance inferred from weak-label agreement |
| Compression saves tokens | Paired token and field-fidelity measurements | Safe compression based only on shorter prompts |
| Sponsor compute processed a large corpus | Actual job logs, corpus counts, runtime, and resource identity | Scale implied by an available snapshot command |

Token Company compression is optional and should stay disabled until the evidence-preservation gate has been measured. Do not present unrun compression, a synthetic cost baseline, or a hypothetical compute scale as observed performance.

## Eleven-slide outline

1. **The missed evidence problem.** A clinical planning question; published nulls, registry-only results, and unreported completed trials.
2. **A result taxonomy with a threshold.** Five buckets, explicit SESOI, and the difference between equivalence and an underpowered result.
3. **Live question to evidence map.** A real query, actual corpus/source counts, and displayed-study versus full-match counts.
4. **Every claim is inspectable.** One paper and one registry record with source links, verbatim evidence, numeric tiers, CI level, and p-value inequality.
5. **One index, two execution stages.** Offline normalization/embedding/classification/linking; FastAPI plus Elasticsearch online retrieval and extraction cache. Explain any active fallbacks.
6. **Finding buried work with Elastic.** Demonstrate the retrieval mode actually used, full-match aggregations, null-associated terms, and reference expansion if it returned records.
7. **Registry linkage and the reporting gap.** Deduplicated paper/trial identity and the observed unreported fraction, with coverage limitations.
8. **Planning the next study.** Compatible random-effects pools, MDE, assurance, required N, and user-valued EV; show one N or SESOI change.
9. **Cost before and after reuse.** Observed first and repeated query usage next to clearly labeled modeled baselines. Include compression only with paired fidelity evidence.
10. **What was built and checked.** Registry flattener, numeric/statistical tests, streaming/browser checks, batch checkpoints, and a concrete Codex-authored contribution. Cite actual check results and run records.
11. **Limits and next milestones.** Corpus coverage, classifier calibration, endpoint comparability, missing outcomes, and a measured expansion plan. Link the repository and demo video only after those artifacts are actually available.

For a Regeneron submission, verify the current challenge's packaging requirements against the supplied sponsor documents. The architecture calls for an MIT license, public repository, demo video, and a 10–12-slide deck; preparing this guide does not publish any of them.

## Explicit UI-only demonstration

When service access is unavailable, use a separately labeled illustrative run:

```sh
VITE_USE_MOCK=true npm run dev
```

`?state=results` displays fictional fixtures only in that explicitly enabled mode. A banner states that all studies, findings, and costs are illustrative. This mode demonstrates the interface and must not be used as evidence of search quality, scientific findings, API access, or cost savings.
