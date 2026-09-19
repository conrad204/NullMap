# negative-priors

Raw engine for the HackMIT project: turn failed runs into a searchable prior, then warn before
someone repeats one. No API server, no MCP layer — four functions and a demo script.

```
parse_raw_experiment(raw_text)         messy lab note / CSV excerpt -> Experiment (+ embedding)
index_to_elastic(client, experiment)   Experiment -> elasticsearch (dense_vector + BM25)
check_prior_risk(client, protocol)     protocol -> PriorCheck (internal failures + published work)
ingest_openalex(client, topic)         OpenAlex works -> the same index, as `source="openalex"`
```

## Published prior art

OpenAlex can be read live per query, but its semantic endpoint is rate-limited to ~1 req/s and 504s
often, and the lexical fallback answers a JAK2 query with mushroom oncology. So `ingest_openalex`
pulls a topic once, reconstructs each `abstract_inverted_index` into text, embeds it and writes it
into the *same* index as internal runs, with `source="openalex"`, `year` and `url`.

Origin is then a filter, not a second store: `check_prior_risk` runs the hybrid query twice, once
with `must_not source:openalex` (internal) and once with `filter source:openalex` (published), and
only hits the live API when the published leg comes back empty. Published matches never contribute
to `risk_score` — `risk_score` is about *our own* runs having already failed; papers are context.

A work's `outcome_type` comes from the same classifier as lab notes read over title+abstract, so it
is a weak signal on published text — good enough to surface "this was already tried", not evidence.

## Run it

```bash
docker run -d --name np-es -p 9200:9200 \
  -e discovery.type=single-node -e xpack.security.enabled=false \
  docker.elastic.co/elasticsearch/elasticsearch:8.15.0

pip install -r requirements.txt
python test_core.py
```

## Backends

Every dependency is optional and degrades instead of failing.

| Step | With `OPENAI_API_KEY` | Without (default here) |
| --- | --- | --- |
| Extraction | `chat.completions.parse` into `ExperimentExtraction` (structured outputs) | deterministic regex + cue-phrase classifier |
| Embeddings | `text-embedding-3-small`, 1536-d | `thenlper/gte-small`, 384-d, local CPU |
| Published prior art | OpenAlex `search.semantic` via pyalex, falling back to lexical `search` | same |

The index mapping takes its `dims` from the first vector indexed, so switching backends only
requires `reset_index(client)`.

Strict structured outputs reject defaults and free-form maps, so the LLM fills `ExperimentExtraction`
(every field required, variables as `name`/`value` pairs) and `to_draft()` folds that back into the
`dict[str, float | str]` shape the engine uses.

## Scoring

`check_prior_risk` runs two Elasticsearch legs — kNN over `embedding` and BM25 over
`hypothesis`/`summary` — and fuses their ranks with RRF. The reported `score` stays the raw cosine
rather than a blended relevance score, because the risk threshold needs a number that is comparable
*across* queries: a normalized score always makes the top hit look like a perfect match, so an
unrelated proposal would flag as HIGH RISK.

`risk_score` ramps cosine across a band, weighted by outcome — a `null` prior counts fully,
`inconclusive` counts 0.4, `positive` counts 0. Cosine is only comparable inside one embedding
space, so the band is per-backend (`BANDS` in `core.py`); measured on the demo snippets:

| Backend | Band | Same-compound proposal | Zebrafish control |
| --- | --- | --- | --- |
| `gte-small` | 0.80 → 0.95 | 0.903 / 0.894 → HIGH RISK | 0.762 / 0.760 → no flag |
| `text-embedding-3-small` | 0.45 → 0.80 | 0.746 / 0.566 → HIGH RISK | 0.267 / 0.233 → no flag |

A new embedding model needs its own band; the default (0.60 → 0.90) is a guess.

## Files

- `schema.py` — `Experiment`, `ExperimentDraft`/`ExperimentExtraction` (what the LLM fills in), `PriorRisk`, `PriorCheck`
- `core.py` — the three functions plus the extraction heuristics, OpenAlex ingestion and live leg
- `test_core.py` — end-to-end demo: two messy failures indexed, 30 OpenAlex works ingested, then one proposal and one control

## Environment variables

| Variable | Effect |
| --- | --- |
| `OPENAI_API_KEY` | switches extraction + embeddings to OpenAI |
| `NP_PARSE_MODEL` | extraction model, default `gpt-4o-mini` |
| `ELASTIC_URL` | default `http://localhost:9200` |
| `ELASTIC_API_KEY` | API key for a hosted cluster; omitted for the local node |
| `OPENALEX_EMAIL` | OpenAlex polite-pool address |
| `NP_OPENALEX_SEMANTIC=0` | skip the semantic endpoint, lexical only |
