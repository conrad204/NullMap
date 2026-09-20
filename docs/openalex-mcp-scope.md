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
| Semantic result cap | **50 results max**; **1 req/s, enforced hard** (a second concurrent call 429s in 0.1 s); ~0.8 s median latency | Recall comes from *many complementary probes*, fused. k probes costs ≈ k seconds of wall clock — the rate limit, not latency, is the bottleneck (§4) |
| Semantic filters | Only `author.id`, `authorships.*`, `funders.id`, `has_abstract`, `has_fulltext`, `institution(s).id`, `is_oa`, `is_retracted`, `language`, `primary_location.license`, `primary_location.source.id`, `publication_year`, `type`. **`primary_topic.*` and `cited_by_count` are rejected** | Field-scoping (e.g. "biology only") must be a *post-filter* or a parallel lexical branch, not a semantic pre-filter. This is a real design constraint, verified by a 400 from the API |
| Lexical `search=` | Stemmed, stop-worded, supports `AND/OR/NOT`, quoted phrases, `search.exact` for unstemmed | The null-signal phrase branch ("no significant difference", "failed to replicate") lives here, not in semantic |
| One search param per request | `search`, `search.exact`, `search.semantic` are mutually exclusive | Multi-strategy retrieval is *forced* to be multi-request → fusion is mandatory, not optional |
| URL length | ~4 KB hard cap; long boolean `OR` lists 400 out | Chunk-and-union is a required utility in the client |
| Paging | `per_page` ≤ 100, basic paging ≤ 10,000 results, cursor paging beyond | Fine for us; we never bulk-pull |
| Pricing | API key required at scale, $1/day free. Singleton lookups free, list+filter $0.10/1k, search **and** semantic $1/1k, PDF download $10/1k | A budget accountant is a first-class component, not an afterthought. One naive agent loop can burn the daily budget in minutes |
| Abstracts | `abstract_inverted_index` (positions → words), not plain text | Reconstruction util needed; trivial but everyone forgets it |
| Classification | 4-level `topics` hierarchy: domain → field → subfield → topic, plus `keywords`, `sustainable_development_goals` | Biology scoping = domains **1 (Life Sciences) + 4 (Health Sciences)**. Not the "pure biology" fields: for a rodent-metabolism query, 67% of matching works classify as `field = Medicine` (§6) |
| Graph | `referenced_works`, `related_works`, `cites:` filter | **Edges are effectively free** — they ride along with metadata hydration, and inbound edges for 100 seeds come back in one $0.0001 call. This is why we do not precompute a graph (§4.2) |
| Full text | `has_fulltext`, `content.openalex.org/works/{id}.pdf` at $0.01/call | Evidence extraction on PDFs is *expensive*; gate it behind explicit opt-in and a budget cap |
| Deprecated | `/text` aboutness endpoint (also $10/1k) | Do not build on it |

**Differentiation vs. existing OpenAlex MCPs:** they expose the API; we expose *answers*. Hybrid
retrieval with fusion, evidence extraction, and cost control are the parts no existing server has.

---

## 2. Non-goals (v1)

Explicitly out of scope, listed so nobody "helpfully" adds them mid-hackathon:

- Self-hosted vector index / snapshot ingestion / Postgres+pgvector. Native semantic search covers
  it, and the numbers in §4.5 say building one would not pay for itself here.
- Any source other than OpenAlex **implemented** — but the adapter seam for them is in scope (§5.1).
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
  forever; searches are cached for the session. On the measured numbers in §4 this is the
  difference between a ~6.5 s and a ~0.2 s response — and during development it is what keeps the
  $1/day from evaporating (one cold search is ~1% of it).
- `select=` on every call. `per_page=100`. Batch ID lookups with `|` (≤100 values, chunked).

---

## 4. Performance and efficiency (measured)

All numbers below come from `docs/bench/retrieval_bench.py`, run against the live API today from a
single 8-core box, keyless. It runs the real pipeline on one biology query ("does intermittent
fasting improve insulin sensitivity in DIO mice"): 5 semantic probes + 4 lexical clauses → RRF →
hydrate 150 → biology post-filter → local embedding rerank.

```
branches: [46, 42, 46, 45, 49, 100, 100, 100, 100]   fused distinct: 480
semantic-only distinct: 146        single best probe: 46
hydrated 141; biology post-filter keeps 141; 8,149 citation edges came along free
citation expansion returned 100 works in one call

retrieve (5 semantic ∥ 4 lexical)     4.71s
hydrate 150 ids (2 calls)             0.95s
graph expansion (1 call)              0.94s
rerank 141 abstracts (gte-small, cpu) 3.60s
---------------------------------------------
12 API calls, $0.0093
```

**A whole cold search costs about one cent and ~6.5 s of API time.** That is ~100 cold searches
inside the $1/day free budget, and the retrieval core is not where the seconds go — the LLM stages
(planning, extraction) will dominate once they exist.

### 4.1 Where the time actually goes

| Stage | Measured | Parallelizable? | Notes |
| --- | --- | --- | --- |
| Semantic probe | 0.75–1.9 s each | **No** — 1 req/s, hard | The only true serialization point. 5 probes ≈ 5 s floor |
| Lexical `search=` | 0.6–0.9 s | Yes (4 in 0.85 s wall) | Runs *inside* the semantic ladder's shadow, so it is free wall-clock |
| `filter=` only | ~0.5 s | Yes | 10× cheaper than search ($0.0001) |
| Singleton `/works/{id}` | ~0.4 s for 8 in parallel | Yes | **$0** and carries `referenced_works` |
| Hydrate 100 works w/ abstracts | ~0.5 s, $0.0001 | Yes | 44 KB gzipped with `select`; 260 KB without |
| Local rerank, gte-small | 23 ms/abstract | Yes | 141 abstracts in 3.6 s cold, ~0 warm (cache vectors by work id) |
| Local rerank, gte-large | 230 ms/abstract | — | 10× slower on CPU. OpenAlex's own model; GPU-only option |

### 4.2 Should we precompute edges? No — they are already free

This was worth checking properly, and the answer is a clean no for v1:

- **Outbound edges cost nothing.** `referenced_works` ships inside the hydration call we were
  making anyway: 141 works carried **8,149 citation edges** for $0.0001 total. A singleton lookup
  carries them too, and singletons are *free*.
- **Inbound edges are one call.** `filter=cites:<id1>|<id2>|…` takes 100 seeds at once — 0.94 s,
  $0.0001, and `meta.count` gives the full inbound degree (29,322 for our 50 seeds) even though
  only 100 rows come back.
- So a precomputed edge store would save ~1 s and ~$0.0002 per query. It is not the bottleneck.

Precomputation only starts paying when we want something the API charges for repeatedly:
multi-hop expansion, co-citation/bibliographic-coupling scores, or an offline eval loop that
replays hundreds of queries. **The cheap way to get there is accretion, not ingestion:** every
hydration already returns edges, so write them to a local SQLite edge table as a side effect of
normal traffic. After a few dozen queries in one domain the local graph is dense enough for
co-citation features, and it cost nothing extra. Bulk snapshot ingest stays out of scope.

### 4.3 What to do instead, in order of leverage

1. **Overlap the branches.** Lexical, filter, and graph calls run concurrently with the 1 req/s
   semantic ladder, so 9 branches took 4.71 s — the same as the 5 semantic probes alone. Anything
   not semantic is wall-clock free.
2. **Fire the first probe before planning finishes.** The verbatim idea needs no LLM planning, so
   probe 1 goes out immediately and the planner's 1–2 s hides inside the ladder.
3. **Stream stages over SSE.** The `SearchStage` union in `src/types.ts` already anticipates this.
   Fused candidates can render at ~1.5 s while rerank and extraction continue, so *perceived*
   latency is ~1.5 s regardless of the deep-mode total. This is the single biggest demo win and
   costs no infrastructure.
4. **Content-addressed cache** keyed on the normalized request URL, in SQLite. Singleton lookups
   are free and can be cached indefinitely; probe results for the session. A warm repeat query is
   ~0.2 s. Plus in-flight request coalescing so duplicate probes share one future.
5. **Rerank locally, not with an LLM.** gte-small scores 141 candidates in 3.6 s on CPU and ~0 when
   vectors are cached per work id — cheaper and faster than an LLM listwise pass, and it frees the
   LLM budget for extraction, where judgment actually matters.
6. **`select=` on every call.** 50 works: 5 KB minimal, 44 KB with abstracts, 260 KB with no
   `select` — a 50× difference in wire bytes and JSON parse time.
7. **Token buckets per endpoint class** (semantic 1/s; everything else ≤10 concurrent against a
   100/s ceiling), with backoff on 429 *and* 5xx — a 504 showed up during benchmarking, so retries
   must cover transient upstream errors, not just rate limits.

### 4.4 How many probes?

Measured marginal recall: one probe → 46 distinct works; five semantic probes → 146 (3.2×); adding
four lexical clauses → 480. Each extra semantic probe costs exactly 1 s and $0.001, so the knob is
linear and legible. Proposed modes: `fast` = 2 probes, no rerank (~2 s, $0.003); `balanced` =
5 probes + local rerank (~7 s, $0.01); `deep` = 8 probes + graph expansion + LLM rerank.

### 4.5 If we ever do want a real index (post-hackathon)

The only reasons to build one: breaking the 50-result cap, escaping 1 req/s, or sub-second cold
latency. That means a local corpus slice (biology, abstracts, 2010+), gte-large embeddings to stay
in OpenAlex's own vector space, and HNSW. gte-large runs at 230 ms/abstract on CPU, so this is GPU
work, ~1–2 sessions plus hardware. Explicitly deferred — and the `Retriever` seam means it drops in
as one more branch without touching fusion, ranking, or the MCP surface.

---

## 5. Modularity: where the seams are

Modularity means *one file added, one line registered, zero core edits*. Five extension points,
each a Protocol with a registry:

### 5.1 `SourceAdapter`
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

### 5.2 `Retriever` — one retrieval strategy (a semantic probe, a boolean clause set, graph
expansion). Branches are declarative config: adding a probe family is a list entry, and the planner
decides which to fire per query.

### 5.3 `Reranker` — `(query, candidates) -> ranked`. Ship heuristic + LLM-listwise; a cross-encoder
drops in later behind the same signature.

### 5.4 `Extractor` — `(paper, text_level) -> EvidenceFragment`. Composable and additive: an
effect-size extractor, a sample-size extractor, a preregistration-checker can be written by three
people in parallel with no merge conflicts.

### 5.5 `Scorer` — `(query, [paper+evidence]) -> (verdicts, PursuitEstimate)`. Swappable so the
estimate can go from heuristic → Bayesian → LLM-judge without touching retrieval.

Registration via entry points (`nullmap.sources`, `nullmap.extractors`, …) so a plugin can even live
in a separate repo. Every plugin declares a capability manifest; the MCP server advertises the
resulting capability set, which keeps agent-facing behaviour honest when a plugin is absent.

### 5.6 What is deliberately *not* pluggable
Fusion, dedup, the cost accountant, the cache, and the wire types. One implementation each. Pluggable
infrastructure is how hackathon codebases die.

---

## 6. MCP surface (v1)

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

## 7. Biology-first: yes, but as configuration

Narrowing to biology is worth doing, and it should cost ~30 lines, not a fork. It buys:

- **Scoping** — `primary_topic.domain.id:domains/1|domains/4` (Life Sciences 48M + Health Sciences
  63M works). Applied as a post-filter on semantic branches and a pre-filter on lexical ones.
  Do **not** scope to the five "pure biology" fields: a `group_by` on a rodent-metabolism query puts
  67% of matching works under `field = Medicine`, and an early version of the benchmark threw away
  134 of 141 good hits that way. Wet-lab biology and clinical work are not separable by field id.
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

## 8. Build plan

Sessions, not weeks. Roughly parallelizable across 3-4 people after M0 lands.

| Milestone | Contents | Est. |
| --- | --- | --- |
| **M0 — skeleton** | Package layout, wire types mirrored from `src/types.ts`, OpenAlex client (key, per-class token buckets, backoff on 429/5xx, `select`, chunked `\|` batching, abstract reconstruction), cost accountant, SQLite cache + edge store, golden-file tests against recorded fixtures | ~0.5 session |
| **M1 — retrieval core** | Query planner, semantic + lexical + graph retrievers, RRF fusion, dedup, post-filters, local gte-small rerank. `search_literature` end-to-end. `docs/bench/retrieval_bench.py` is the throwaway prototype of exactly this — promote it, don't re-derive it | ~1 session |
| **M2 — MCP surface** | All 7 tools, resources, prompts, stdio + HTTP, capability manifest. Usable from Claude/Cursor at this point — **this is the demoable milestone; freeze it before doing anything else** | ~0.5 session |
| **M3 — results layer** | Abstract extractor, verdict classifier, pursuit estimator, null-signal phrase bank, biology `DomainProfile` | ~1 session |
| **M4 — nullMap backend** | FastAPI `POST /search` + SSE progress + `POST /contributions` stub, wired to the same pipeline; point the existing frontend at it via `VITE_API_URL` | ~0.5 session |

Parallel tracks after M0: retrieval (M1) ‖ MCP plumbing (M2) ‖ extraction prompts + eval set (M3) ‖
backend/frontend glue (M4). The wire types from M0 are the contract that lets those four not block
each other.

---

## 9. Evaluation (hackathon-sized, but real)

Without this we cannot tell whether hybrid retrieval actually beats `search=`, which is the entire
technical claim.

- **Gold set**: 25-40 biology idea-queries, each with 5-10 hand-labelled relevant works and, where
  known, at least one credible null result that plain keyword search buries. Built by hand during
  M1; this is the highest-leverage two hours anyone on the team will spend.
- **Retrieval metrics**: recall@50 and nDCG@25 for each branch alone vs. fused vs. `search=` baseline.
  Target: fused recall@50 ≥ 1.5× the lexical baseline, and ≥ 1 buried null result surfaced per query.
- **Extraction metrics**: verdict accuracy vs. hand labels on ~100 abstracts; separately, the rate of
  *fabricated* effect sizes, which must be ~0 — a wrong number is worse than an abstention.
- **Cost/latency**: p50 and p95 $ and seconds per `search_literature` call per mode, tracked by the
  same ledger the benchmark uses. Current measured baseline for the retrieval half of `balanced`:
  $0.0093 and ~6.5 s. Target with extraction included: ≤ $0.03 and ≤ 15 s, with first results
  streamed by 2 s.

---

## 10. Open questions

1. **API key** — needed before M1 (the $0.10/day keyless budget will not survive development).
   Whose account owns it, and do we prepay a few dollars for demo day?
2. **LLM for planning/extraction/rerank** — which provider and key? It sits on the critical path for
   M3, and the rerank step is the difference between "a search box" and "results-focused".
3. **Latency vs. depth for the demo** — retrieval measures ~6.5 s for 5 probes, and SSE streaming
   puts first results on screen at ~1.5 s. That is probably fine unstaged; confirm nobody wants a
   pre-warmed demo path instead.
4. **Does the MCP ship as its own repo?** Recommendation: `packages/` inside NullMap during the
   hackathon, split out after — a separate repo now adds coordination cost for no demo value.
