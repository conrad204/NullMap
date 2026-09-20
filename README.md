# nullMap

Find prior clinical studies, inspect null and missing results, and explore whether a proposed study is adequately powered. The application combines OpenAlex papers and ClinicalTrials.gov records in one Elasticsearch index, with a React interface and a FastAPI service.

OpenAlex ingestion uses its **public S3 Parquet snapshot**, with no OpenAlex API or API key. The default corpus scope is hypertension **or** kidney research across all years, including records without abstracts. Full imports run on the batch/compute host; the browser receives search results. The previous small demo index is preserved. A full replacement corpus has not been imported yet.

See the [S3 ingestion guide](backend/app/ingest/README.md) for the remote workflow, exact scope, storage requirements, checkpoints and reference backfills. Running `python -m app.bootstrap` defaults to a manifest-only plan; scanning requires `--run` and an adequate explicit byte budget.

## Local setup

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), Node.js/npm, and Docker or an existing Elasticsearch deployment. Run the first commands from the repository root.

Keep the existing ignored `.env` and `backend/.env` files. Credentials have already been configured in this workspace; do not overwrite them or put service keys in `VITE_*` variables. On a fresh checkout only, create files without replacing existing ones:

```sh
cp -n .env.example .env
cp -n backend/.env.example backend/.env
```

On a fresh checkout, fill in `OPENAI_API_KEY` and the Elasticsearch connection in `backend/.env`; leave the supplied workspace's configured file intact.

Start the local development Elasticsearch service:

```sh
docker compose up -d elasticsearch
```

The service uses Elasticsearch 9.1.4, a trial license, localhost binding, and 3/2/1 GB free-space watermarks. Security is disabled for this local service. If Docker Compose is unavailable, the equivalent fallback is:

```sh
docker run -d --name nullmap-elasticsearch --memory 3g \
  -p 127.0.0.1:9200:9200 \
  -e discovery.type=single-node \
  -e xpack.security.enabled=false \
  -e xpack.license.self_generated.type=trial \
  -e 'ES_JAVA_OPTS=-Xms1g -Xmx1g' \
  -e cluster.routing.allocation.disk.watermark.low=3gb \
  -e cluster.routing.allocation.disk.watermark.high=2gb \
  -e cluster.routing.allocation.disk.watermark.flood_stage=1gb \
  -v nullmap-elastic-data:/usr/share/elasticsearch/data \
  docker.elastic.co/elasticsearch/elasticsearch:9.1.4
```

Install the backend and CPU embedding dependencies, then inspect the data plan:

```sh
cd backend
uv sync
uv pip install torch --index-url https://download.pytorch.org/whl/cpu
uv pip install sentence-transformers scikit-learn
.venv/bin/python -m app.bootstrap --plan
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Planning reads only the public S3 manifest. For an import, follow the ingestion guide on a host with sufficient persistent storage and an Elasticsearch connection. The first model load downloads MiniLM to that host. The importer scans selected columns, filters the declared scope, normalizes, classifies, embeds, links and indexes in resumable batches. Inspect its `corpus-summary.json` for completion and coverage. Starting the API alone does not populate an empty index.

Use `.venv/bin/...` or `uv run --no-sync ...` after the CPU installation. A later plain `uv sync` or `uv run` can remove those separately installed optional packages; use `uv sync --inexact` when refreshing core dependencies. For an appropriately provisioned GPU machine, the optional `ingest` dependency group is also available.

In another terminal at the repository root:

```sh
npm ci
npm run dev
```

Open **http://localhost:5173**. Vite proxies `/api` to the backend on port 8000. Live API errors remain errors; fictional fixtures are enabled only by explicitly setting `VITE_USE_MOCK=true`.

## Serve the built interface

From the repository root, build the frontend before starting the API:

```sh
npm run build
cd backend
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**. FastAPI serves the built `dist/` interface and same-origin `/api` routes; `/docs` exposes the API schema. `FRONTEND_DIST` can select another build directory. The API also retains its unprefixed routes for direct clients.

Alternatively, after `npm run build`, run `docker compose up --build -d` from the repository root. Compose mounts `./dist` into the API container at `/web` and sets `FRONTEND_DIST=/web`. Populate the target index using the ingestion guide first. The API image includes CPU embedding dependencies; its first model load may download the configured model.

For remote inference, run the batch job and FastAPI on the remote compute host, configured to use the same Elasticsearch index and embedding model. Keep FastAPI bound to localhost and access it through an SSH tunnel, for example `ssh -L 8000:127.0.0.1:8000 user@compute-host`. Opening `http://127.0.0.1:8000` then uses the remotely served interface and model. Source records and model files stay on the compute host. No remote job or storage provisioning is launched automatically.

## Configuration

Backend settings are read from `backend/.env`; examples contain names and defaults, never credentials.

| Setting | Purpose |
| --- | --- |
| `ELASTIC_URL`, `ELASTIC_API_KEY`, `ELASTIC_INDEX` | Elasticsearch connection and shared study index |
| `ELASTIC_LOCAL=true` | Unauthenticated local development service; use `false` with Elastic Cloud |
| `OPENAI_API_KEY` | Structured PICO parsing, quoted extraction, and bounded narration |
| `OPENALEX_SNAPSHOT_MANIFEST` | Public works manifest; defaults to anonymous OpenAlex S3 |
| `EMBEDDING_MODEL`, `EMBEDDING_DEVICE` | MiniLM model shared by indexing and retrieval on the compute host |
| `EMBEDDINGS_ENABLED=false` | Explicit BM25 fallback when local inference is unavailable |
| `REFERENCE_MIN_SIMILARITY` | Review-reference cosine cutoff; defaults to 0.55 for the configured MiniLM model |
| `CLASSIFIER_MODEL_PATH` | Optional evaluated classifier artifact; leave empty for the active weak classifier |
| `FULLTEXT_ENABLED`, `EUROPEPMC_URL`, `FULLTEXT_MAX_LINES` | Query-time Europe PMC full text for open-access papers with a PMCID; keyless, on by default |
| `TTC_API_KEY`, `COMPRESSION_ENABLED`, `COMPRESSION_VALIDATED` | Optional compression experiment; compression remains off by default |

ClinicalTrials.gov needs no key. OpenAlex's [public snapshot](https://help.openalex.org/access/snapshot/) requires no OpenAlex API access or AWS credentials. It provides dated metadata and available abstracts, not full-text PDFs. When a retrieved paper carries a PubMed Central ID, the API fetches its JATS XML from [Europe PMC](https://europepmc.org/RestfulWebService) at query time, flattens the abstract, primary-outcome methods sentences, results tables and results prose into verbatim lines, and runs the same quoted extraction over those lines. Papers outside PubMed Central remain abstract-only, and each result row states which it used. Optional Voloridge SSH settings are for batch-machine access; the API does not launch remote jobs.

## Verification

From the repository root:

```sh
npm test
npm run build
cd backend
.venv/bin/pytest -q
.venv/bin/ruff check app tests
NULLMAP_TEST_ELASTIC_URL=http://127.0.0.1:9200 .venv/bin/pytest tests/test_repository.py -q
```

The repository tests opt into real Elasticsearch only when `NULLMAP_TEST_ELASTIC_URL` is set and use isolated test indexes. Other tests cover normalization, linking, quote validation, statistical rules, API behavior, and resumable ingestion. Frontend tests cover streaming and effect-display conversions. Registry fixtures include one inspected live record plus **20 synthetic edge cases**, not 20 independently human-reviewed trials; see [fixture provenance](backend/tests/fixtures/README.md).

## Architecture and interpretation

The implementation follows [the architecture proposal](docs/ARCHITECTURE.md), updated for S3-only literature ingestion: index-time classification and embeddings; hybrid BM25/vector retrieval; indexed review-reference expansion; separate full-match aggregations; structured registry parsing; quoted, cached paper extraction; and pure-Python statistical analysis. Elasticsearch stores studies, vectors, extraction caches, and contribution drafts. Missing review references are reported and can be backfilled by an offline S3 pass. No additional database or queue is required.

The current classifier is an explicitly heuristic phrase lexicon. A small trained pilot achieved 56.25% agreement on 16 held-out LLM-labelled abstracts and missed both held-out nulls, so it was not promoted. These are model-agreement metrics, not human-validated clinical accuracy. The new anonymous S3 path was checked with a 20-record scan from one 3.6 MB public part. Full-snapshot processing has not been run.

- A textual “no significant difference” is inconclusive unless compatible numerical evidence supports equivalence within the chosen SESOI. Missing reports have unknown outcomes; they are not null findings.
- Explicit condition and anatomical terms constrain both direct matches and review references. Generic demographics affect ranking. The parser may propose up to four equivalent names of the same condition (for hypertension: high blood pressure); each is an alternative to the condition clause, never to the anatomical site. These aliases are model output, so full-match counts can vary slightly between parses of the same idea. Reference screening is heuristic and does not replace a systematic review's eligibility assessment.
- Pooling requires at least three compatible primary studies with matching effect scales, outcomes, and units. Without a suitable pool, assurance and required sample size are unavailable rather than invented.
- OR/RR/HR values remain inspectable as raw ratios; inference uses their logarithms and a compatible log-scale SESOI. Confidence levels and p-value inequalities are preserved.
- Approximate reconstruction requires an estimate and an exact eligible p-value under stated assumptions; p-value bounds and sample size alone do not justify manufacturing an effect or interval.
- Assurance is expected two-sided statistical power under the stated model, not the probability of meaningful clinical benefit. EV uses the user's success value, null-result value, and cost in common units.
- Paper facts select numbered source sentences, or numbered full-text lines and table rows when Europe PMC has the paper; code supplies exact quotations and rejects unsupported numeric values. Discussion and conclusion sections are never offered as evidence. Narration is deterministic when a compatible numerical pool is unavailable. Registry facts retain their JSON paths. Contributions are stored drafts with receipts, not automatic publications or emails.

See the [ingestion guide](backend/app/ingest/README.md) for checkpoints, scope rules, snapshot budgets, reference backfills and source limitations. The [demo guide](docs/DEMO.md) explains the workflow; the [validation record](docs/VALIDATION.md) distinguishes the S3 migration checks from historical demo measurements.

## Optional measured benchmarks

With the API running, execute from `backend/`:

```sh
.venv/bin/python -m app.benchmark search --idea 'Does vitamin D reduce depression?' --output data/search-benchmark.json
.venv/bin/python -m app.ingest normalize --input data/research-s3/openalex.jsonl --output data/benchmark-papers.jsonl --resume
.venv/bin/python -m app.benchmark compression --input data/benchmark-papers.jsonl --limit 20 --output data/compression-benchmark.json
```

The search benchmark records first/repeated requests; a first request is cold only for uncached documents. Token counts are observed, while dollar amounts use configured price assumptions. Compression requires both OpenAI and Token Company credentials and compares supported facts against uncompressed extraction; agreement alone is not clinical validation. Keep compression disabled until the paired results and actual pricing justify enabling it.

MIT licensed. Public repository publication, sponsor submission, slide export, and demo-video recording are separate deliverables.
