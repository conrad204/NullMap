Run ingestion from `backend/` with `uv run --no-sync python -m app.ingest --help`.
Every intermediate file is JSONL. Keep raw downloads so normalization and linking
can be rerun without paying for API calls again.

For the complete three-topic demo corpus, run:

```bash
uv run --extra ingest python -m app.bootstrap --per-topic 100
```

This fetches up to 100 registry records and 100 recent papers for each demo topic,
plus 30 most-cited papers per topic without the 2015 cutoff. It also looks up
seven explicitly identified landmark trial publications by DOI or PMID. It then
normalizes, weakly classifies, embeds, links and indexes the documents. Repeated
runs reuse raw-source and stage checkpoints; changing input contents creates new
derived stage files. Use `--cited-per-topic`, `--data-dir`, `--skip-landmarks`, or
`--skip-embeddings` to change that bounded scope. No LLM labelling or extraction
is performed by bootstrap. The observed counts and missing landmark abstracts
are written to `data/bootstrap-summary.json`.

The completed bootstrap on 2026-09-19 produced **575 canonical studies**:
193 registry records, 379 papers, and 3 merged records, including 49 reviews and
575 local embeddings. This is the bootstrap slice, not an assertion that a live
index cannot also contain query-expanded references or later contributions.
Actual OpenAlex abstracts were available for Moseley (2002), Kirkley (2008),
Sihvonen (2013), VITAL-DEP (2020), and AREDS2 cognition (2015). The OPAL (2010)
and MAPT (2017) singleton records lacked OpenAlex abstracts and were skipped.
The Sihvonen population has degenerative meniscal tears **without knee
osteoarthritis**; it is adjacent evidence and must not be presented as the same
population as the osteoarthritis trials.

The individual stages remain available:

```bash
uv run --no-sync python -m app.ingest fetch ctgov --query 'vitamin D depression' --limit 100 --output data/ctgov.jsonl --resume
uv run --no-sync python -m app.ingest fetch openalex --query 'vitamin D depression' --limit 100 --output data/openalex.jsonl --resume
uv run --no-sync python -m app.ingest normalize --input data/ctgov.jsonl --output data/trials.jsonl --resume
uv run --no-sync python -m app.ingest normalize --input data/openalex.jsonl --output data/papers.jsonl --resume
uv run --no-sync python -m app.ingest classify --input data/papers.jsonl --output data/classified.jsonl --resume
uv run --no-sync python -m app.ingest link --input data/trials.jsonl data/classified.jsonl --output data/studies.jsonl --resume
uv run --extra ingest python -m app.ingest embed --input data/studies.jsonl --output data/embedded.jsonl --resume
uv run --no-sync python -m app.ingest index --input data/embedded.jsonl --resume
```

The link step loads normalized documents into memory; provision RAM proportional
to the slice. Fetch, normalization, embedding and indexing use bounded batches.
`--resume` verifies the input and parameters, restores the last committed output
offset and resumes cursor/record progress. Reusing an output for different inputs
is rejected. Indexing upserts canonical IDs and is safe to resume after partial
bulk failures. A completed link stage is reused; an interrupted link stage
recomputes into a temporary file before replacement.

OpenAlex API keys are optional in the currently observed API and can be supplied
as `OPENALEX_API_KEY`. Both an actual list request without a key and the current
[official documentation](https://help.openalex.org/api/) were checked during
implementation. This differs from the architecture's older key requirement.
ClinicalTrials.gov requires no key. Default OpenAlex filters include medicine,
articles/reviews, abstracts, and publication dates from 2015; override `--filter`
when historical studies or another domain are needed. Always choose a deliberate
record limit instead of assuming that a seed slice represents all studies.
OpenAlex fetch also accepts `--sort cited_by_count:desc`; the default remains
`publication_date:desc`. The sort becomes part of the resume signature, preventing
a citation-ranked query from accidentally continuing a date-ranked cursor. The
supported query shape was checked against the
[official sorting documentation](https://help.openalex.org/api/sorting/) and a
live request.

The primary registry effect is the first comparative primary analysis with an
interpretable effect scale. All primary analyses, endpoint/timeframe, comparator,
selected group IDs, confidence level and p-value inequality are retained. Arm
means are never treated as treatment effects. Registry numbers override linked
paper numbers; their source is an exact JSON path, not a generated quotation.
RESULT references establish reporting independently of whether the paper is in
the local slice. A bare NCT mention only establishes reporting when the paper
also contains a classified or numeric outcome; reviews and protocols cannot
erase the missing-report signal.
Normalization identifies explicit systematic, scoping, narrative, umbrella and
literature reviews, meta-analyses, and guideline/recommendation titles as
nonprimary sources. Normalizer and classifier versions participate in bootstrap
fingerprints. Indexing persists canonical links and archives previously indexed
publication rows so later linkage cannot leave duplicate samples in counts.

Optional classifier training:

```bash
uv run --no-sync python -m app.ingest label --input data/embedded-papers.jsonl --output data/labels.jsonl --limit 3000 --resume
uv run --extra ingest python -m app.ingest train-classifier --input data/labels.jsonl --output data/classifier.json --label-field llm_label
uv run --no-sync python -m app.ingest classify --input data/embedded-papers.jsonl --output data/scored.jsonl --model data/classifier.json --resume
```

`label` records actual provider token usage and estimated cost in `.usage.json`.
Non-verbatim evidence is rejected and listed in its checkpoint. Generated labels
are explicitly marked as not human-reviewed. `train-classifier` requires at least
five independently labelled examples per included class, rejects duplicate IDs,
and reports held-out precision/recall, F1 and a confusion matrix. `positive` means
a reported statistically significant difference, not clinical benefit; `null`
means no significant difference, not equivalence. Weak scores are labelled as
heuristic scores rather than calibrated probabilities.

The small pilot run during implementation was not good enough to deploy: 63
training and 16 held-out LLM-labelled abstracts yielded 56.25% accuracy and zero
null recall on two held-out nulls. A single mixed example was insufficient for
training. Keep the auditable weak classifier enabled until a larger independently
reviewed evaluation supports replacing it. EvidenceInference's
[public dataset](https://github.com/jayded/evidence-inference) is available, but
its PICO-level increase/decrease labels require curation before use as these
abstract-level labels.

The optional Parquet path accepts explicit local files or a manifest and refuses
files whose total declared physical size exceeds the byte budget. It projects
only useful columns and validates abstract encoding before writing output.

```bash
uv run --no-sync python -m app.ingest snapshot --manifest data/parquet-manifest.json --max-files 2 --max-bytes 5000000000 --limit 100000 --topic medicine --output data/snapshot.jsonl
```

Manifest input can be a `{files: [{url, meta: {content_length}}]}` OpenAlex
manifest or an array of `{url, size_bytes}` entries. Remote S3 scans require
DuckDB's `httpfs` extension and network access; the total-file size is a
conservative budget rather than a measurement of range-request bytes. The actual
snapshot schema was checked on one 3.6 MB public part: the abstract index is a
JSON string, IDs a map, and topics a struct array. A 30-row projected medicine
scan passed. This validates the path; it is not a full-snapshot ingestion claim.

The regression fixtures contain one inspected live registry record and 20
synthetic edge cases. See `backend/tests/fixtures/README.md` for the exact scope;
these are not 20 independently human-reviewed clinical trials.
