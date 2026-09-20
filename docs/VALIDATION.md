# Validation record — 2026-09-19

This records observed implementation checks, not a clinical validation study. Counts and timings belong to these runs and may change as the index expands or caches warm.

## Numeric coverage, text-tier and corpus-quality checks

These are bounded spot checks made while fixing why `credible_null` never fired. None is a
human-reviewed accuracy study, and no re-ingest or re-index of the cloud index was performed,
so **the live index does not yet reflect any of this**.

**State of the live index before the change** (`studies-hypertension-kidney-s3`, 228,998
study records, read-only aggregations): 0 `credible_null`, 196,412 `inconclusive`; 772 records
with a CI; 201,357 `no_result_stated`; 311 of 197,100 papers with a PMID, 0 with a PMCID, 90
mentioning an NCT ID; 5 merged trial–paper records against 31,893 trials. A random 2,000-paper
sample came from two update partitions (`2025-10`: 1,785; `2025-07`: 214), median citation
count 0, and 24% of 2,099 abstract-bearing records shared no content word between title and
abstract (an upper bound on mismatched records, since non-English titles also fail it). The
`unreported` count therefore rests on almost no linking and is probably inflated.

**Registry arm-level derivation.** 1,000 real ClinicalTrials.gov trials with posted results
from the scope query, flattened before and after: trials with a usable 95% interval rose from
**62 to 200**. On the default SMD 0.2 request, buckets went from 0 `effect` / 0 `credible_null`
to 37 / 6; the six credible nulls included NCT02185417 (hydrochlorothiazide vs chlorthalidone).
Of the rest, 360 are `failed` (242 single-arm) and the remainder post medians, adjusted means,
several timepoints, more than two groups without an analysis, or arms whose control cannot be
identified. The real VITAL fixture keeps its reported HR for HR requests and yields a derived
log OR from 793/12,927 vs 824/12,944 for OR requests.

**S3 partition quality and the PubMed profile.** Column-projected reads of one part per month:
PMID share ranged from 0.0% (`2025-10`, `2026-02`) to 11.5% (`2026-01`); `ids` never contained
`pmcid`. One 217 MB `2026-01-16` part scanned with `hypertension-kidney-pubmed` took 61 s and
produced 1,166 works: 1,166 with a PMID, 236 with a PMCID recovered from location URLs, and
833 of 850 abstract-bearing records passing the title/abstract check. This is one part, not a
corpus estimate.

**Paper extraction on 25 real trial-like abstracts** from that part (`gpt-4.1-mini`, about
$0.03 per pass, the same 25 each time):

| Pass | Rejected whole | With a usable interval | `inconclusive` |
| --- | ---: | ---: | ---: |
| Arm-level fields only | 19 | 1 | 16 |
| + number words, unsupported total `n` dropped alone | 10 | 4 | not recorded |
| + `p_value` and arm summaries dropped alone, SMD plausibility guard | 0 | 7 | not recorded |
| + quoted `reported_result` (enum), free-text scale names | 2 | 4 | 5 |

Fifteen of the first 19 rejections were `Number absent from quoted evidence for n`: spelled-out
sizes ("Four hundred patients") and totals the model had summed. One discarded extraction held
a mean difference of 0.308 with a 95% CI of −0.194 to 0.81. The interval count moved between
4 and 7 across passes with no code change to that path, so treat it as run-to-run model
variance, not a trend. The phrase lexicon labeled 17 of the 25 `inconclusive`; with
`reported_result` 5 remained. **That is a coverage figure, not accuracy**: the labels were not
checked against human judgment, at least two quoted sentences were weak support for their
label, and the sample included animal studies, which nothing in the pipeline excludes. About
70% of these abstracts stayed text-only on every pass.

**Effect direction, trend and outcome grouping** (real models, same 25 abstracts plus 23 numeric
blood-pressure trials from the registry sample). Quoted `result_direction` came back as 15
favors-intervention, 2 favors-comparator, 7 unclear, 1 absent; not checked against human
reading. The first trend paragraph was coherent and mentioned the reported nulls, but said
"among 7 studies" when its table held 14, so the prompt now forbids counting and prose with a
number absent from its table is rejected. Outcome grouping with `gpt-4.1-mini` and a looser
prompt produced three groups from the 23 trials, two of them wrong: it combined a text-message
reminder, dapagliflozin, a smartphone app and local heat stress on a shared systolic-BP outcome,
and combined three different active-versus-active comparisons while mixing "reduction in" with
"change in" diastolic BP. With the question-anchored prompt and `gpt-4.1` it returned one group
of two (MK-0736 and nebivolol versus placebo, change in diastolic BP), correctly leaving out a
trial reporting a BP level, and 21 of 23 ungrouped, so no pool formed. **No model-grouped pool
has yet been observed on real data with the final prompt**; the pooled arithmetic for such
groups is covered by unit tests only. About $0.005 per grouping call and $0.008 per trend call
at configured prices.

**Relevance screening** (live API, growing cloud index). "In adults taking creatine supplements,
creatine increases resting … blood pressure" first returned 14 keyword matches and 3 `effect`
studies: a telmisartan trial and two case reports (a lymphoma, a COVID infection), matched
because the corpus mentions "creatine" constantly as in "creatine kinase". With the screen it
returned 0 relevant studies and cost $0.0008 instead of $0.0106, since nothing irrelevant was
read. A first version screened only the top 60 ranks and, on "hypertension is caused by high
salt intake" (678 keyword matches), kept 38 registry records and no papers, because the registry
sweep fills the early ranks; screening 160 in batches of 40 kept 64 (10 of the 50 shown are
papers) and the trend ran on 4 effect studies, at about $0.02 for that search. Two kept
"papers" were corrupt records with a literary title on a salt abstract ("Ah! L’amour,
l’amour…"), which the screen cannot see through because it reads the abstract. Screening
accuracy was not measured against human judgment. When the match set exceeds the screened
page, bucket counts still describe unscreened keyword matches and the response says so.
The screen now reaches 500 ranks (`SCREEN_LIMIT`, with retrieval widened to match via
`repository.RETRIEVE_LIMIT`, at most 8 screening calls in flight). The figures above were
measured at 160; cost and kept counts at 500 have not been re-measured. From payload size
and configured prices a full 500-record screen is estimated at about $0.06.

The Painless bucket script was checked for parity with `assign_bucket` on real Elasticsearch
9.1.4 across 8 scale/margin requests and 21 documents, including derived effects, a reported
HR alongside arm counts, and lexicon-versus-stated text labels. 311 backend tests (5 of them
real-ES), 3 frontend test files, typecheck, the production build and Ruff passed.

## Pre-search corpus filters

Publication-year and citation-count filters were added after this record's runs and are covered
by **synthetic tests only**. Checked automatically: the Elasticsearch clause shapes, that the
bounds are mandatory `filter` clauses an expanded review reference cannot escape, that a zero
citation floor is treated as a bound rather than an absence, that the same bounds reach
retrieval, the registry sweep and the aggregation, that the comparison count drops them so the
excluded figure is attributable, that a failed count is reported as uncounted rather than zero,
that a citation bound counts the registry rows it exempted and a date-only filter neither
exempts nor counts anything, that the exemption notice appears only when a bound applied and
registry rows were kept, and that an empty filter object produces no `filters` payload and no
warning. The real
Elasticsearch parity test for filtered retrieval/sweep/aggregation agreement
(`test_real_elasticsearch_filters_agree_across_retrieval_registry_sweep_and_counts`) was
**skipped**: no Elasticsearch was running in this environment. **No filtered search has been
run against the live index**, so nothing here measures how much of that corpus any bound removes.
The registry citation exemption is a policy decision, not a measurement: ClinicalTrials.gov
supplies no citation count and ingestion stores 0 for every trial row.

## Public S3 migration

The current implementation removes OpenAlex API fetching and live singleton/reference lookups. Literature ingestion uses unsigned public S3 Parquet reads. Search expands citations already present in Elasticsearch, with an offline S3 backfill command for missing references. The default scope is hypertension or kidney research across all years, retaining records without abstracts. ClinicalTrials.gov remains a separate keyless API source.

Observed source checks:

- Anonymous manifest planning succeeded against `https://openalex.s3.amazonaws.com/data/parquet/works/manifest.json`: **2,446 parts**, **724,970,323,127 physical bytes**, snapshot date **2026-06-26**; SHA-256 `a2513ed0b67571b515f7bdee6558e3ee07ffbcfbe52dcb5773628bc5df9bc064`.
- A real anonymous HTTPS scan read selected columns from `updated_date=2016-06-24/part_0000.parquet` (**3,628,657 bytes**), stopping at **20 records**. Its retained JSONL was **79,832 bytes**, and all 20 records normalized successfully. **13 lacked abstracts** and were retained as metadata-only records. The checkpoint correctly reported incomplete coverage. Physical file size is not a measurement of transferred range-request bytes.
- This check exposed a timestamp-conversion dependency; projecting timestamps as UTC strings removed it. Local regression tests cover that schema behavior.
- A second scan applied the actual hypertension/kidney profile to that same public part: **17 matching records**, including **3 without abstracts**, all normalized successfully. It exhausted that one part while correctly keeping full-snapshot coverage false. The [filtered JSONL](../backend/data/s3-migration-check/hypertension-kidney.jsonl) is another ignored local validation artifact.

The S3 migration passed **230 backend tests**, with **5 opt-in real Elasticsearch tests skipped** because the service remained stopped. Fixture-based integration exercises manifest → Parquet filtering → registry linking → embedding → indexing and a repeated run without reindexing. Regression tests also cover row caps, crash recovery, uncommitted-byte rollback, changed releases and classifier versions, appended source output, missing abstracts, reference backfills, source budgets and low disk space. **8 frontend checks**, the production build/typecheck, Ruff and diff whitespace checks passed.

The live source check used no OpenAlex API key or AWS credentials. Its local ignored artifacts are [scan validation](../backend/data/s3-migration-check/validation.json) and [the bounded JSONL](../backend/data/s3-migration-check/works.jsonl). No full hypertension/kidney import or remote batch job was launched, and no persistent service was restarted. The previous demo index was preserved. The migration's end-to-end integration uses fixture sources and an in-memory repository; it does not establish full-scale throughput or a new real Elasticsearch deployment.

## Historical demo corpus and source checks

The remaining sections describe the **earlier API-based demo**, before the S3-only migration. Its source commands and automatic reference fetching have since been replaced. These observations remain historical measurements, not claims that the current S3 corpus was populated.

The reproducible bootstrap produced **575 canonical studies**: **193 ClinicalTrials.gov records, 379 OpenAlex papers, and 3 merged records**, including **49 reviews/nonprimary sources**. All 575 had **384-dimensional local MiniLM embeddings**. Duplicate linked publications were archived rather than counted as additional studies. Live review-reference expansion brought the active index to **627 records** at final verification; this includes discovery reviews and is a changing index count, not the fixed bootstrap total.

- Live ClinicalTrials.gov filtered paging and OpenAlex list/singleton requests succeeded. The limited OpenAlex requests worked without an API key; larger access allowances remain source-dependent.
- The registry flattener was checked against the actual NCT01169259 response, including primary analyses, selected comparison groups, confidence levels and factorial denominators. The committed suite also contains **20 synthetic edge cases**, not 20 independently human-reviewed live trials.
- One actual OpenAlex Parquet part was inspected: **3,628,657 bytes**. Its abstract index was JSON encoded in a string column. A bounded, projected medicine scan returned **30 records**.
- The cached refresh performed no source downloads: source HTTP requests were explicitly disabled. The local index had no active studies missing the accent-folded `search_text` field. Original citation text remained unchanged.

Local generated artifacts: [bootstrap summary](../backend/data/bootstrap-summary.json), with its `canonical_jsonl` path. Repository provenance: [registry fixtures](../backend/tests/fixtures/README.md), [live registry record](../backend/tests/fixtures/ctgov_vital_live.json), and [snapshot schema inspection](../backend/tests/fixtures/openalex_snapshot_schema.json).

## Classifier and compression decisions

The pilot embedding classifier used **63 training examples and 16 held-out LLM-labeled examples**. Held-out agreement was **56.25%**; it recovered **0 of 2 null examples**. One mixed example was excluded because support was insufficient. This is agreement with generated labels on a small selected sample, not clinical accuracy. The model was **not promoted**; the auditable weak phrase classifier remains active.

The second paired compression benchmark evaluated **20 abstracts**. Uncompressed extraction passed evidence validation for **13**, compressed extraction for **2**, and **2 pairs** had verified exact fact agreement. The compression gate remains **off**. These numbers do not establish human-reviewed extraction accuracy, and shorter prompts alone do not justify enabling compression.

Local artifacts: [classifier metrics](../backend/data/classifier-metrics.json), [paired compression results](../backend/data/compression-benchmark-v2.json). Reproducible implementations: [classifier](../backend/app/classifier.py), [benchmark CLI](../backend/app/benchmark.py), and [quote-validation tests](../backend/tests/test_llm.py).

## Observed repeated search

Query: “Does vitamin D supplementation reduce depression in adults?” Both requests reported 31 matches in this recorded run.

| Observation | First recorded request | Immediate repeat |
| --- | ---: | ---: |
| Wall time | 7,678 ms | 70 ms |
| Provider calls | 3 | 0 |
| Input/output tokens | 2,861 / 471 | 0 / 0 |
| Estimated provider cost | $0.001898 | $0 |
| Extraction-cache hits | 4 | 6 |
| Newly verified extractions | 1 | 0 |
| Parse-cache hits | 0 | 1 |

The first request was **already partly warm**, so this is not a fully cold-versus-warm experiment. Costs use configured per-million-token prices and are estimates, not invoice totals. The separate “read 200 abstracts,” cold, and warm baseline columns in API responses are modeled comparisons, not measured runs.

Source artifact: [verified repeated-search benchmark](../backend/data/search-benchmark-verified.json). The recorded per-call estimates sum to $0.001898.

## Browser and automated checks

The live browser walkthrough checked desktop **1366 px** and mobile **390 px** widths. The search stream returned **HTTP 200**; the inspected result interface exposed **38 source links** and **33 quote blocks**, with **no page errors or horizontal overflow**. These are interface checks, not an assessment of the relevance or correctness of every displayed study.

The final automated run passed **216 backend tests**, including **5 real Elasticsearch integration tests**, and **8 frontend checks**. The production frontend build/typecheck, Ruff, and `git diff --check` passed. Real Elasticsearch tests used the opt-in local deployment and isolated test indexes. A dependency emits an AnyIO deprecation warning; no tests failed or were skipped in the final backend run.

The final live knee search returned **8 primary matches** and **3 screened reference matches** in **12,605 ms**. It included the Moseley paper (`W2100445448`) and the canonical Kirkley trial (`NCT00158431`). Regression checks excluded the previously surfaced elbow/ankle procedures and the scoping review from primary results. Explicit disease/anatomical population constraints apply to both direct and reference-expanded matches; reference cosine screening defaults to 0.55. Related interventions can still appear through reviews and require eligibility assessment. The response withheld assurance and EV because no compatible numerical pool was available. Its local source artifact is [the final knee response](../backend/data/knee-final-response.json).

Repository checks: [backend tests](../backend/tests), [streaming tests](../src/api/stream.test.mjs), [effect-display tests](../src/lib/effects.test.mjs). Local browser artifacts include [desktop results](/tmp/nullmap-live-results.png), [mobile viewport](/tmp/nullmap-live-mobile-viewport.png), and the [browser check script](/tmp/nullmap-live-browser-check.py).

## Scope and artifact retention

`backend/data/` outputs are ignored local run artifacts; `/tmp/` browser artifacts are ephemeral. They are available in the implementing workspace but are not committed evidence that automatically accompanies a fresh checkout. The fixture files, source code, and automated tests linked above are included project files. No credentials are included in this record.

The configured `.env`, `backend/.env`, and original `keys.txt` are ignored and have mode 600. A value-based scan found none of the five configured credential values in versionable project files.

No 300,000-to-million-record ingest was performed, no remote sponsor batch job was launched, and no Elastic Cloud endpoint was validated. The verified search deployment used local Docker Elasticsearch. The small corpus, heuristic classifier, source coverage, rejected extraction quotes, and incompatible study outcomes remain practical limitations. Missing reports are unknown outcomes; a textual null does not establish numerical equivalence, and incompatible studies do not justify pooling or an assurance estimate.

See [README](../README.md) for setup and verification commands, [the ingestion guide](../backend/app/ingest/README.md) for reproducible stages, and [the demo guide](DEMO.md) for presentation boundaries.
