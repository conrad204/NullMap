# Validation record — 2026-09-19

This records observed implementation checks, not a clinical validation study. Counts and timings belong to these runs and may change as the index expands or caches warm.

## Corpus and source checks

The reproducible bootstrap produced **575 canonical studies**: **193 ClinicalTrials.gov records, 379 OpenAlex papers, and 3 merged records**, including **49 reviews/nonprimary sources**. All 575 had **384-dimensional local MiniLM embeddings**. Duplicate linked publications were archived rather than counted as additional studies. Live review-reference expansion brought the active index to **627 records** at final verification; this includes discovery reviews and is a changing index count, not the fixed bootstrap total.

- Live ClinicalTrials.gov filtered paging and OpenAlex list/singleton requests succeeded. The limited OpenAlex requests worked without an API key; larger access allowances remain source-dependent.
- The registry flattener was checked against the actual NCT01169259 response, including primary analyses, selected comparison groups, confidence levels and factorial denominators. The committed suite also contains **20 synthetic edge cases**, not 20 independently human-reviewed live trials.
- One actual OpenAlex Parquet part was inspected: **3,628,657 bytes**. Its abstract index was JSON encoded in a string column. A bounded, projected medicine scan returned **30 records**.
- The cached refresh performed no source downloads: source HTTP requests were explicitly disabled. The local index had no active studies missing the accent-folded `search_text` field. Original citation text remained unchanged.

Local generated artifacts: [bootstrap summary](../backend/data/bootstrap-summary.json), with its `canonical_jsonl` path. Repository provenance: [registry fixtures](../backend/tests/fixtures/README.md), [live registry record](../backend/tests/fixtures/ctgov_vital_live.json), and [snapshot schema inspection](../backend/tests/fixtures/openalex_snapshot_schema.json).

## Classifier and compression decisions

The pilot embedding classifier used **63 training examples and 16 held-out LLM-labelled examples**. Held-out agreement was **56.25%**; it recovered **0 of 2 null examples**. One mixed example was excluded because support was insufficient. This is agreement with generated labels on a small selected sample, not clinical accuracy. The model was **not promoted**; the auditable weak phrase classifier remains active.

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
