# Scope: a modular, extendible OpenAlex MCP

Target: the retrieval core that nullMap's `/search` endpoint sits on, shipped as a standalone MCP
server so it is usable by Claude/Cursor/agents directly and by the nullMap backend over the same
contracts. Written to be built and demoed in a hackathon, but factored so nothing has to be thrown
away afterwards.

The one-sentence scope: **a hybrid (semantic + boolean + graph) retrieval engine over OpenAlex with
a pluggable pipeline — sources, rankers, extractors, scorers — exposed as a small, stable MCP tool
surface.** Everything nullMap-specific (verdicts, pursuit estimates) is a plugin on top of that
core, not baked into it.

---

## 1. What already exists, and why it matters for scope

The OpenAlex API changed materially in Feb 2026, and most of the prior-art MCP servers
(`JOSETRA44/openalex-mcp`, `oksure/openalex-research-mcp`, `Mearman/mcp-openalex`, …) predate it.
They are thin one-tool-per-endpoint wrappers. Verified against the live API today:

| Capability | Reality | Consequence for us |
| --- | --- | --- |
| `search.semantic=<text>` on `/works` | Native AI semantic search. GTE-Large-EN, 1024-dim, cosine over title+abstract embeddings of every work | **We do not build or host an embedding index for v1.** This is the single biggest scope cut available |
| Semantic input | Up to 2,000 chars, truncated beyond. Longer, paragraph-shaped queries rank better | Query planner should *expand* the user's idea into a paragraph, not compress it into keywords |
| Semantic result cap | **50 results max**, ~6 s latency observed, 1 req/s | Recall has to come from *many complementary queries*, fused — not from one deep query |
| Semantic filters | Only `author.id`, `authorships.*`, `funders.id`, `has_abstract`, `has_fulltext`, `institution(s).id`, `is_oa`, `is_retracted`, `language`, `primary_location.license`, `primary_location.source.id`, `publication_year`, `type`. **`primary_topic.*` and `cited_by_count` are rejected** | Field-scoping (e.g. "biology only") must be a *post-filter* or a parallel lexical branch, not a semantic pre-filter. This is a real design constraint, verified by a 400 from the API |
| Lexical `search=` | Stemmed, stop-worded, supports `AND/OR/NOT`, quoted phrases, `search.exact` for unstemmed | The null-signal phrase branch ("no significant difference", "failed to replicate") lives here, not in semantic |
| One search param per request | `search`, `search.exact`, `search.semantic` are mutually exclusive | Multi-strategy retrieval is *forced* to be multi-request → fusion is mandatory, not optional |
| URL length | ~4 KB hard cap; long boolean `OR` lists 400 out | Chunk-and-union is a required utility in the client |
| Paging | `per_page` ≤ 100, basic paging ≤ 10,000 results, cursor paging beyond | Fine for us; we never bulk-pull |
| Pricing | API key required at scale, $1/day free. Singleton lookups free, list+filter $0.10/1k, search **and** semantic $1/1k, PDF download $10/1k | A budget accountant is a first-class component, not an afterthought. One naive agent loop can burn the daily budget in minutes |
| Abstracts | `abstract_inverted_index` (positions → words), not plain text | Reconstruction util needed; trivial but everyone forgets it |
| Classification | 4-level `topics` hierarchy: domain → field → subfield → topic, plus `keywords`, `sustainable_development_goals` | Biology scoping = domain `Life Sciences` (id 1) + fields 11, 13, 24, 28, 30 (+27 Medicine when clinical) |
| Graph | `referenced_works`, `related_works`, `cited_by_api_url` | Cheap recall expansion at list+filter prices ($0.10/1k), 10× cheaper than search |
| Full text | `has_fulltext`, `content.openalex.org/works/{id}.pdf` at $0.01/call | Evidence extraction on PDFs is *expensive*; gate it behind explicit opt-in and a budget cap |
| Deprecated | `/text` aboutness endpoint (also $10/1k) | Do not build on it |

**Differentiation vs. existing OpenAlex MCPs:** they expose the API; we expose *answers*. Hybrid
retrieval with fusion, evidence extraction, and cost control are the parts no existing server has.

---

## 2. Non-goals (v1)

Explicitly out of scope, listed so nobody "helpfully" adds them mid-hackathon:

- Self-hosted vector index / snapshot ingestion / Postgres+pgvector. Native semantic search covers it.
- Any source other than OpenAlex **implemented** — but the adapter seam for them is in scope (§4.1).
- Write paths to OpenAlex (curation, collections), author disambiguation, institution analytics.
- User accounts, persistence beyond a local cache, multi-tenancy.
- Fine-tuned or trained models. Every classifier is prompt- or heuristic-based in v1.
- The nullMap contribution/upload flow (`POST /contributions`). Separate track, separate owner.

---

## 3. The core: robust, results-focused search

### 3.1 Pipeline

```
idea (free text, optional field hint)
  │
  ├─▶ PLAN        query planner → QueryPlan { semantic_probes[], lexical_clauses[], filters, budget }
  │
  ├─▶ RETRIEVE    N branches in parallel, each a Retriever plugin
  │                 · semantic(idea verbatim)                      50 results
  │                 · semantic(idea + "null result / no effect")   50
  │                 · semantic(idea + mechanism paraphrase)        50
  │                 · lexical(boolean from planner)                100/page
  │                 · lexical.exact(null-signal phrases ∧ topic)   100
  │                 · graph(refs + citations of top-k seeds)       cheap
  │
  ├─▶ FUSE        reciprocal-rank fusion over branches, dedup by OpenAlex id → DOI → title-hash
  │
  ├─▶ FILTER      post-filters the semantic API cannot do: topic/field, citation floor,
  │                 year, type, retraction, dedup of preprint↔VOR pairs
  │
  ├─▶ RERANK      Reranker plugin over ~150 candidates → top 25
  │                 v1: LLM listwise on title+abstract; fallback: heuristic (RRF × recency × venue)
  │
  ├─▶ EXTRACT     Extractor plugins per paper: sample size, effect size + CI, design,
  │                 outcome direction, preregistration — abstract-only by default,
  │                 full text only when explicitly requested and budget allows
  │
  └─▶ SCORE       Scorer plugin: per-paper verdict + corpus-level pursuit estimate
```

Fusion is what makes this *robust*: the 50-result semantic cap stops being a ceiling once five
complementary probes are unioned, and the lexical branch catches the exact-phrase null signals that
embeddings smear away. RRF needs no score calibration across branches, which is why it beats a
weighted sum here — branch scores are not comparable (cosine vs. BM25 vs. graph distance).

### 3.2 Results-focused ranking

"Results-focused" is the actual product claim, and it is a *ranking and extraction* problem, not a
query problem. OpenAlex has no "what did they find" field. Three signals, in increasing cost:

1. **Lexical null-signal probes** (free-ish, $1/1k): `search.exact` over a curated phrase bank —
   "no significant difference", "failed to replicate", "did not differ", "null result",
   "no evidence for", "futility", "terminated early". Biology adds "no significant enrichment",
   "did not rescue", "knockout showed no phenotype".
2. **Abstract-level extraction** (LLM, no API cost): structured extraction into
   `{design, n, effect: {metric, value, ci}, direction, hedging}` with a `confidence` and a
   `provenance` span quoted from the abstract. Refuse rather than guess — `inconclusive` with an
   honest reason beats a fabricated effect size.
3. **Full-text extraction** (opt-in, $0.01/paper): only for the top-k the user asks to deepen.

The verdict taxonomy is already fixed by the frontend contract in `src/types.ts`
(`effect | credible_null | inconclusive | failed | unreported`) — the scorer plugin targets exactly
that, and the MCP tool returns exactly those shapes so the nullMap backend is a pass-through.

`unreported` cannot come from OpenAlex at all (it is, definitionally, absent from the literature).
It is the clearest argument for the source-adapter seam: it needs ClinicalTrials.gov / OSF
registrations diffed against OpenAlex hits. In v1 the field exists and is always empty; the adapter
that fills it is a post-core extension.

### 3.3 Budget and caching (first-class, not polish)

- `CostAccountant` wraps every call: pre-flight estimate from `meta.count`, per-request
  `meta.cost_usd` accounting, per-search and per-session caps, and a hard stop that degrades
  gracefully (drop probes, not correctness) rather than throwing at 80% of the way through.
- Disk+memory cache keyed on the normalized request URL. Singleton lookups are free and cacheable
  forever; searches are cached for the session. During a demo this is the difference between a 6 s
  and a 200 ms response — and during development it keeps the $1/day from evaporating.
- `select=` on every call. `per_page=100`. Batch ID lookups with `|` (≤100 values, chunked).

---

## 4. Modularity: where the seams are

Modularity means *one file added, one line registered, zero core edits*. Five extension points,
each a Protocol with a registry:

### 4.1 `SourceAdapter`
```python
class SourceAdapter(Protocol):
    name: Source                                  # matches src/types.ts Source union
    capabilities: frozenset[Capability]           # SEMANTIC | LEXICAL | GRAPH | FULLTEXT | REGISTRY
    async def search(self, plan: QueryPlan) -> list[Candidate]: ...
    async def hydrate(self, ids: Sequence[str]) -> list[Paper]: ...
```
OpenAlex is the reference implementation and the only one in v1. arXiv, Europe PMC, OSF,
ClinicalTrials.gov are ~100-line adapters afterwards. The fusion stage is source-agnostic, so a new
adapter improves recall with no change to ranking code.

### 4.2 `Retriever` — one retrieval strategy (a semantic probe, a boolean clause set, graph
expansion). Branches are declarative config: adding a probe family is a list entry, and the planner
decides which to fire per query.

### 4.3 `Reranker` — `(query, candidates) -> ranked`. Ship heuristic + LLM-listwise; a cross-encoder
drops in later behind the same signature.

### 4.4 `Extractor` — `(paper, text_level) -> EvidenceFragment`. Composable and additive: an
effect-size extractor, a sample-size extractor, a preregistration-checker can be written by three
people in parallel with no merge conflicts.

### 4.5 `Scorer` — `(query, [paper+evidence]) -> (verdicts, PursuitEstimate)`. Swappable so the
estimate can go from heuristic → Bayesian → LLM-judge without touching retrieval.

Registration via entry points (`nullmap.sources`, `nullmap.extractors`, …) so a plugin can even live
in a separate repo. Every plugin declares a capability manifest; the MCP server advertises the
resulting capability set, which keeps agent-facing behaviour honest when a plugin is absent.

### 4.6 What is deliberately *not* pluggable
Fusion, dedup, the cost accountant, the cache, and the wire types. One implementation each. Pluggable
infrastructure is how hackathon codebases die.

---

## 5. MCP surface (v1)

Small on purpose — 7 tools. Agents degrade badly past ~15, and the 31-tool prior art is a
demonstration of that failure mode. Every tool takes an explicit `budget_usd` ceiling and returns
`cost_usd` and `provenance`.

| Tool | Signature sketch | Notes |
| --- | --- | --- |
| `search_literature` | `(idea, field?, mode=balanced\|fast\|deep, budget_usd?) -> SearchResult` | The headline tool. Runs the whole pipeline. `fast` = 2 probes no rerank (~$0.003), `deep` = 6 probes + LLM rerank |
| `find_null_results` | `(idea, field?) -> SearchResult` | Same pipeline, null-signal probes weighted up. The differentiated one; keeps the null-result framing legible to an agent |
| `get_work` | `(id \| doi \| pmid, include=[abstract, fulltext?]) -> Paper` | Singleton, free at the API level |
| `resolve_entity` | `(name, type) -> [{id, display_name, hint}]` | The two-step-ID-lookup discipline the API docs demand, done once so agents cannot get it wrong |
| `expand_from_seeds` | `(work_ids, direction=refs\|citations\|related, limit) -> [Paper]` | Cheap graph recall |
| `facet_works` | `(query, group_by) -> counts` | `group_by` for landscape/trend questions at list+filter prices |
| `assess_idea` | `(idea, field?) -> PursuitEstimate + summary` | Search + score; what nullMap's front page calls |

Plus MCP **resources** (`openalex://work/{id}`, `nullmap://search/{query_id}` so a long result set is
referenced, not re-pasted, into context) and **prompts** (`literature-scan`, `null-result-audit`,
`replication-check`) that encode the good query shapes so the tools work well from a naked client.

Transport: stdio + streamable HTTP from one server object. The nullMap FastAPI backend imports the
same pipeline package directly and maps it to `POST /search` + the SSE progress stream — the four
`SearchStage` values (`keywords`, `searching`, `classifying`, `estimating`) map 1:1 onto the pipeline
stages, so progress events fall out of the architecture rather than being faked.

---

## 6. Biology-first: yes, but as configuration

Narrowing to biology is worth doing, and it should cost ~30 lines, not a fork. It buys:

- **Scoping** — `primary_topic.domain.id:domains/1` (Life Sciences, 32.8M works) plus fields
  11/13/24/28/30, and 27 (Medicine) for clinical. Applied as a post-filter on semantic branches and a
  pre-filter on lexical ones.
- **Better evidence extraction** — biology/biomed abstracts are structured far more often
  (Background/Methods/Results/Conclusions), report n and effect sizes, and use a narrow, learnable
  vocabulary of null phrasings. Extraction accuracy is the riskiest part of the project; this is the
  single largest de-risking available.
- **A real `unreported` path later** — ClinicalTrials.gov registrations with no matching publication
  is a well-defined, biology-specific signal.
- **Free full text** — Europe PMC / PMC OA gives full text without the $10/1k content API.

Implementation: a `DomainProfile` object (topic filters, phrase bank, extraction prompt, metric
vocabulary, preferred adapters). Biology is the only profile shipped; a `general` profile stays in
the test suite so we can prove the narrowing is a config choice rather than an assumption baked into
the code. The demo still *says* "academia"; it just wins on biology queries.

---

## 7. Build plan

Sessions, not weeks. Roughly parallelizable across 3-4 people after M0 lands.

| Milestone | Contents | Est. |
| --- | --- | --- |
| **M0 — skeleton** | Package layout, wire types mirrored from `src/types.ts`, OpenAlex client (key, retry/backoff on 429, `select`, chunked `\|` batching, abstract reconstruction), cost accountant, cache, golden-file tests against recorded fixtures | ~0.5 session |
| **M1 — retrieval core** | Query planner, semantic + lexical + graph retrievers, RRF fusion, dedup, post-filters. `search_literature` end-to-end, unranked | ~1 session |
| **M2 — MCP surface** | All 7 tools, resources, prompts, stdio + HTTP, capability manifest. Usable from Claude/Cursor at this point — **this is the demoable milestone; freeze it before doing anything else** | ~0.5 session |
| **M3 — results layer** | Abstract extractor, verdict classifier, pursuit estimator, null-signal phrase bank, biology `DomainProfile` | ~1 session |
| **M4 — nullMap backend** | FastAPI `POST /search` + SSE progress + `POST /contributions` stub, wired to the same pipeline; point the existing frontend at it via `VITE_API_URL` | ~0.5 session |

Parallel tracks after M0: retrieval (M1) ‖ MCP plumbing (M2) ‖ extraction prompts + eval set (M3) ‖
backend/frontend glue (M4). The wire types from M0 are the contract that lets those four not block
each other.

---

## 8. Evaluation (hackathon-sized, but real)

Without this we cannot tell whether hybrid retrieval actually beats `search=`, which is the entire
technical claim.

- **Gold set**: 25-40 biology idea-queries, each with 5-10 hand-labelled relevant works and, where
  known, at least one credible null result that plain keyword search buries. Built by hand during
  M1; this is the highest-leverage two hours anyone on the team will spend.
- **Retrieval metrics**: recall@50 and nDCG@25 for each branch alone vs. fused vs. `search=` baseline.
  Target: fused recall@50 ≥ 1.5× the lexical baseline, and ≥ 1 buried null result surfaced per query.
- **Extraction metrics**: verdict accuracy vs. hand labels on ~100 abstracts; separately, the rate of
  *fabricated* effect sizes, which must be ~0 — a wrong number is worse than an abstention.
- **Cost/latency**: p50 and p95 $ and seconds per `search_literature` call per mode. Budget target:
  `balanced` ≤ $0.02 and ≤ 15 s.

---

## 9. Open questions

1. **API key** — needed before M1 (the $0.10/day keyless budget will not survive development).
   Whose account owns it, and do we prepay a few dollars for demo day?
2. **LLM for planning/extraction/rerank** — which provider and key? It sits on the critical path for
   M3, and the rerank step is the difference between "a search box" and "results-focused".
3. **Latency vs. depth for the demo** — semantic calls are ~6 s and capped at 1 req/s; six probes is
   therefore ~6-8 s wall-clock even fully parallel. Is `balanced` (≈15 s with rerank) acceptable on
   stage, or should the demo path be warm-cached?
4. **Does the MCP ship as its own repo?** Recommendation: `packages/` inside NullMap during the
   hackathon, split out after — a separate repo now adds coordination cost for no demo value.
