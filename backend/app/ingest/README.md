# Public S3 ingestion

OpenAlex literature comes exclusively from the anonymous public bucket
`s3://openalex/data/parquet/works/`. No OpenAlex API key, AWS account or signed URL
is used. ClinicalTrials.gov still uses its separate, keyless API. The snapshot
contains bibliographic metadata, citation references and available abstracts;
it is not a collection of full-text PDFs. The normalizer keeps each work's PubMed
Central ID when present; the API uses it at query time to read open-access full text
from Europe PMC (see [fulltext.py](../fulltext.py)). No full text is stored by ingestion.

Run these commands from `backend/` **on the batch host**, such as the sponsor
EC2 machine. Python reads selected Parquet columns over HTTPS, retains matching
rows there, computes embeddings there, and writes to Elasticsearch. Serving
FastAPI there also puts query embedding inference there. OpenAI handles PICO
parsing and bounded evidence extraction through the existing API integration.
The browser only sends queries and receives results; it never scans S3.

## Scope and planning

The default `hypertension-kidney` profile includes the **union** of those fields:
hypertension topics, the Nephrology subfield, additional renal topics, and
matching terms in titles, reconstructed abstracts and topic names. Exact rules
and the registry query are versioned in [scope.py](scope.py). All years and work
types are retained, including records without abstracts. Nonprimary works are
retained for discovery but excluded from primary-study statistical pools.
Missing abstracts do not imply missing results.

Completion means all matches under those rules in the supplied snapshot.
Incomplete metadata, terminology and source coverage prevent a promise of every
relevant study worldwide. The snapshot is a dated release, not a live feed.

```sh
.venv/bin/python -m app.bootstrap --plan
```

Planning is the default when neither `--plan` nor `--run` is supplied. It reads
only the manifest and prints its hash, date, part count, physical byte budget
and target index. On 2026-09-19 the public works manifest described **2,446 parts,
724,970,323,127 bytes, dated 2026-06-26**.

Partitions are update dates, **not topics**. Broad topic coverage requires
scanning all parts. Column projection can reduce traffic; the physical size
budget is conservative, not a measurement of transferred bytes or required
output disk space. The 5 GB default refuses a full scan instead of silently
importing a partial corpus.

## Full build on remote compute

Install dependencies using the root [setup guide](../../../README.md). Configure
Elasticsearch and models in the remote host's `backend/.env`. For an auditable
fresh corpus, choose a dedicated index such as
`ELASTIC_INDEX=studies-hypertension-kidney-s3`. Existing indexes are preserved and
updated by ID; importing does not delete unrelated older records.

Choose an existing persistent mount with room for the retained JSONL and index.
Inspect storage first: the sponsor machine's root disk may be small. The code
does not format or mount disks. After inspecting the plan, an example run on an
adequately provisioned batch host is:

```sh
.venv/bin/python -m app.bootstrap --run \
  --data-dir /mnt/research/nullmap/2026-06-26 \
  --max-snapshot-bytes 750000000000
```

Replace that directory with the actual persistent mount. Set the budget from
the printed manifest and host capacity. Repeat the same command and directory
to resume. There is no default year or record cap. The registry condition query
includes all statuses and study types and exhausts its cursor. Snapshot records
are normalized, weakly classified, embedded, linked across batches and indexed.
Bootstrap performs no paid LLM labelling or extraction.

For a bounded smoke test:

```sh
.venv/bin/python -m app.bootstrap --plan --max-files 1
.venv/bin/python -m app.bootstrap --run --max-files 1 --max-records 100 \
  --registry-limit 20 --data-dir data/s3-smoke
```

If the selected part exceeds 5 GB, choose an explicit budget after inspecting
its size. A part may contain no scope matches. `--max-files`, `--max-records` and
`--registry-limit` are explicit partial runs. Use a separate directory for the
full run: changing selected files invalidates the scan checkpoint.

## Checkpoints and coverage

Keep each output and its sidecars together:

| Artifact | Purpose |
| --- | --- |
| `manifest.json` | Pinned release metadata and part sizes |
| `openalex.jsonl` | Matched source records and snapshot provenance |
| `openalex.jsonl.checkpoint.json` | Committed bytes, part, within-part progress and coverage |
| `ctgov.jsonl` and checkpoint | Registry records, query signature and paging cursor |
| `*.materialize.json` | Indexed offset, counts, model and target-index signature |
| `corpus-summary.json` | Stage, declared scope, source completion and index health |

Writes flush before checkpoints advance. Restart rolls back uncommitted JSONL
bytes and repeats uncommitted index batches with idempotent IDs. Source, scope,
model and target-index signatures prevent incompatible resumes. Capped source
files may grow; materialization resumes at its committed offset. A new snapshot
release requires a new directory. A remote manifest change detected at scan
completion invalidates the completion claim.

`complete_snapshot_scope=true` means every part in the supplied manifest was
exhausted. A subset manifest establishes coverage only of that subset. The corpus
summary is complete only when the snapshot and registry query both finish.
Limits, interruptions and skipped sources remain explicit. Low disk space stops
the scan with its checkpoint preserved; it does not provision storage.

The importer uses bounded batches and an index-backed linker. It neither loads
the whole corpus into RAM nor duplicates all vectors into JSONL. Registry facts
override linked paper numbers; canonical links prevent double counting.
Metadata-only records are embedded using their available title/text and marked
as abstract unavailable in search results.

## Reference expansion without API calls

Search expands review citations using **already indexed** works and their stored
vectors, reporting missing references. There are no live OpenAlex lookups.
For missing references, collect IDs and run a separate S3 pass into the same
Elasticsearch index using a separate output directory:

```sh
.venv/bin/python -m app.ingest reference-ids \
  --input /mnt/research/nullmap/2026-06-26/openalex.jsonl \
  --output /mnt/research/nullmap/missing-references.txt
.venv/bin/python -m app.bootstrap --run --skip-registry \
  --work-ids /mnt/research/nullmap/missing-references.txt \
  --data-dir /mnt/research/nullmap/reference-backfill \
  --max-snapshot-bytes 750000000000
```

Skip the backfill if the ID file is empty. `--work-ids` overrides topic filtering
so differently worded references can be recovered. This requires another scan:
S3 has no work-ID search endpoint. Only works in the snapshot can be recovered.
Backfills keep separate provenance and do not claim completion of the main build.

## Individual stages and model choices

`python -m app.ingest --help` exposes snapshot scans, registry fetching,
normalization, classification, embedding, indexing, reference IDs, labelling and
classifier training. For example:

```sh
.venv/bin/python -m app.ingest snapshot --plan
.venv/bin/python -m app.ingest snapshot --input /data/sample.parquet \
  --profile hypertension-kidney --output data/sample.jsonl --resume
.venv/bin/python -m app.ingest fetch ctgov --query 'kidney' --filter '' \
  --all --output data/ctgov.jsonl --resume
```

Manifests use `{ "files": [{ "url": "…", "size_bytes": 123 }] }`; the public
`meta.content_length` form is supported. Local fixtures are supported for tests.
Remote paths accept only unsigned public OpenAlex works URLs. DuckDB installs
its `httpfs` extension under the output directory on first use.

The legacy standalone `link` command loads its input into memory and is suitable
for small slices. Bootstrap uses the bounded index-backed path. For lexical-only
operation use `--skip-embeddings` and serve with `EMBEDDINGS_ENABLED=false`.
Keep indexing and query embedding models identical.

The default classifier remains the auditable phrase heuristic. The earlier small
LLM-labelled pilot failed evaluation and was not promoted. Labelling and model
training remain explicit experiments; see the historical
[validation record](../../../docs/VALIDATION.md) and
[registry fixture provenance](../../tests/fixtures/README.md).

Sources: [OpenAlex snapshot documentation](https://help.openalex.org/access/snapshot/)
and the [sponsor's OpenAlex README](https://voloridge-hack-mit-2026.s3.us-east-1.amazonaws.com/src/openalex/README.md).
