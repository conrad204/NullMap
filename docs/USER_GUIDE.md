# nullMap user guide

nullMap helps you check the file drawer before you run a study. Give it a clinical research question and it maps the prior literature — OpenAlex papers and ClinicalTrials.gov records in one index — with special attention to the results that are hardest to find: published nulls, trials that completed and never reported, and reports whose text claims more than their numbers show. It then asks whether your planned study is adequately powered given what that evidence pool actually contains.

This guide covers what each feature does and how to use it effectively. For setup, see the [README](../README.md); for how it works internally, see [ARCHITECTURE.md](ARCHITECTURE.md); for what has and has not been validated, see [VALIDATION.md](VALIDATION.md).

One principle runs through everything: **nullMap reports what your index contains, with its limits stated, rather than a confident answer.** Warnings in the interface are part of the result. If a report says its counts are unscreened, or the map says it describes a sample, that caveat is the tool working as intended.

## Before you start

nullMap searches *your* Elasticsearch index, not the live web. What it can find is bounded by what you ingested:

- Run ingestion (`python -m app.bootstrap --run ...` from `backend/`) before expecting results. A partial import (`--max-files`, `--max-records`) covers a subset of the snapshot's update partitions, and reports over it describe that subset only — the tool will say so, and you should not read "0 matches" over a partial corpus as "nobody has studied this".
- The default corpus scope is hypertension and kidney research. Questions outside the ingested scope will legitimately match little or nothing.
- Ask a question represented in your index. `GET /ready` shows whether Elasticsearch is reachable and embeddings are enabled.
- An OpenAI API key enables question parsing, evidence extraction from papers, and narration. Without it, searches still run but with reduced interpretation.
- Local embeddings (torch + sentence-transformers, installed separately per the README) enable hybrid semantic retrieval and the gap map's idea placement (`POST /map`). Without them, search falls back to BM25 keyword retrieval — the report will carry a warning saying so.

## The Search tab

### Asking a question

Type a specific, answerable question into the box: an intervention, an outcome, and a population — *"Does intermittent fasting improve working memory in healthy adults?"* rather than *"fasting and cognition"*. The question is parsed into population, intervention, comparator and outcome (shown under "Interpreted question" in the report), and those terms drive retrieval, so a question that names its parts searches better than a topic phrase.

The parser also proposes a **meaningful-effect threshold** (SESOI — the smallest effect size that would matter clinically) with a written rationale. This threshold decides which results count as meaningful effects and which nulls count as confirmed, so read the rationale: if the proposed threshold is not the one you would prespecify, the verdicts are relative to a threshold you disagree with. The web interface uses the proposed threshold and a default study plan (total N = 200, two-sided α = 0.05); to supply your own SESOI, planned N, alpha, or value/cost figures, call `POST /search` directly (see "Using the API" below).

### Narrowing the corpus first

**Filters** (below the question box) restrict the corpus *before* anything runs: publication year range and citation bounds. Two behaviors to know:

- Registry records (ClinicalTrials.gov rows) carry no citation counts, so **citation bounds never remove them** — a citation floor would delete exactly the terminated and never-reported trials the tool exists to surface. When this exemption keeps registry rows in a filtered result, the report says so and counts them.
- "Keep these filters for my next search" makes the bounds sticky across questions. The question header always shows the bounds a search ran under, plus a marker when sticky filters will carry to the next one — check it if results look thinner than expected.

A filtered report states which bounds applied and how many otherwise-matching records they excluded. If that count could not be computed, the report says so instead of implying nothing was removed.

### Reading the report

From top to bottom:

**Coverage line and headline.** How many indexed records matched, from which sources, and the retrieval mode (hybrid semantic + keyword, or BM25-only). The headline answers "has this been tested before?" from the match set.

**Coverage & limitations.** Read this box before the numbers. It lists everything that qualifies the report: BM25 fallback, unscreened counts, missing review references, sample limits. These are not boilerplate; each one appears only when it is true of this search.

**What prior work found.** A bar over the full match set (not just the displayed page), grouped into four answers:

| Answer | Buckets inside it | What it means |
| --- | --- | --- |
| Favored the intervention | effect, direction `favours_intervention` | The groups ended up different and the study's own report puts the intervention ahead. The direction is only as good as the sentence it was quoted from. |
| Favored the comparator | effect, direction `favours_comparator` | The same evidence rules, the other way round: this is where harm from the intervention appears. |
| Made no difference | confirmed + claimed | **Confirmed** means a confidence interval sits entirely inside the equivalence bounds: any effect is too small to matter. **Claimed** means the abstract says "no significant difference" without an interval to back it — that is a claim, not evidence of equivalence, and often just an underpowered study. |
| No answer | stopped/flawed + never reported | Trials stopped early, retracted, or completed more than a year ago with no posted results and no linked publication. **Outcomes here are unknown, not negative.** |

An effect whose report never said which arm it favored is counted beside the bar under its own label rather than folded into benefit or harm. A further **inconclusive** count sits beside the bar rather than in it, because inconclusive is not a finding: each such match carries a reason (too imprecise to call, conflicting results, reported on an incompatible scale, no readable result, no valid threshold), and most reasons describe what could be *read* from the record, not what the study found. Click any bucket to filter the study list below.

**What the reported effects have in common** (or, when too few studies reported an effect, an overview of what the matches are and why none settles the question). Direction counts — how many effects favor the intervention versus the comparator — are computed in code; the accompanying prose is generated from extracted facts and quotes and is rejected if it introduces numbers not in its table.

**Your planned study.** The power panel:

- **Assurance** is Bayesian expected statistical power for your planned study under the evidence-derived model — *not* the probability that the intervention works or that the effect is clinically meaningful. Low assurance with a real evidence pool means your planned N is likely too small.
- **N for 80% assurance** is the sample size that would get you there; **planned MDE** is the smallest standardized effect your plan can detect.
- **Expected value** combines your success/null values and study cost (defaults in the UI; your own units via the API). It is raw utility, not a percentage.
- These appear only when a compatible evidence pool exists. "Unavailable" means the evidence could not support the calculation — nullMap does not invent a number.

**Quantitative evidence.** When at least three compatible primary studies report numeric effects on the same scale and outcome, they are pooled with a random-effects model and drawn as a forest plot. Pools never mix effect types (an odds ratio is never pooled with a mean difference), and reviews are excluded — only primary studies count. Each pool carries an Egger funnel-asymmetry test when there are enough studies; an asymmetric funnel is a *caveat* about possible publication bias, never an adjustment to the estimate. Some pools note that a language model judged the outcomes comparable ("model grouping") — the arithmetic is still done in code, and the report warns when this applies.

**Study rows.** Every verdict is inspectable: each study shows its verdict with a rationale, the verbatim quoted evidence the numbers came from, an evidence tier, and links to the paper, PubMed, and any linked trial registrations. The tiers, strongest first:

| Tier | Meaning |
| --- | --- |
| numeric | Estimate and interval reported directly |
| derived | Computed from reported arm-level data (means/SDs or event counts) — nothing imputed |
| reconstructed | Rebuilt from an estimate plus an exact p-value |
| text_only | Only a textual claim; no usable numbers |

Prefer conclusions that rest on numeric and derived evidence. A `text_only` "no difference" is a claim about wording.

**Registry reporting gap.** The count of trials that completed and never reported is a measured count within your index's registry coverage. It tells you results are missing; it does not tell you they were null.

**Where to look next** lists terms associated with null findings in the match set — useful threads for manual reading. **Query cost** shows model calls, tokens, and estimated spend for the search; repeated searches are cheaper because paper extractions are cached, so a paper is read at most once.

### Getting better search results

- Name the population, intervention and outcome explicitly; add the comparator when it matters ("versus placebo", "versus usual care").
- If the match set looks polluted with irrelevant records, check the screening warning: when the relevance screen could not cover the whole match set, the bucket counts include unscreened keyword matches.
- If a question returns nothing, first suspect corpus coverage (was this topic ingested?) before concluding the field is empty. The report's own summary makes this distinction — trust it.
- Re-running the same question is cheap (extraction cache) — iterate on phrasing.

## The gap map (API only)

The gap map has no tab in the interface on `main`; it is reachable through `POST /map`. The Map tab that draws it lives on the `map-tab` branch.

Where search answers "what exists for this question", the map answers a different question: **for each region of your indexed literature, why is nothing new there?** It clusters a deterministic random sample of embedded studies into regions and labels each by what happened inside it, so "nothing here" can be told apart from "this was tried and did not work".

### Region labels

| Label | Meaning |
| --- | --- |
| Null-saturated | Tried repeatedly, mostly reported no difference. An idea landing here is not novel — it is refuted, usually by uncited work. |
| Unreported (dark) | A large share of trials completed without ever posting results. What happened is unknown, not null. |
| Contested | Effects and nulls both well represented; the literature disagrees with itself. |
| Active | Mostly reported effects, no concentration of nulls or missing reports. |
| No readable outcome | Records the index could not read a result from (registrations, protocols). Says what the corpus is missing, not what studies found. |
| Too thin to judge | Fewer than five primary studies; the mix means nothing yet. |

Labels describe **your index's sample**, and the coverage line says what fraction of the embedded corpus was sampled. Reviews are excluded from a region's evidence, since they restate other people's results.

### Sparse bands (gaps)

The map also lists pairs of *related* regions with almost nothing between them: combinations the neighboring literatures imply but nobody has run. Each band shows how many sampled papers sit in it, how much work sits on either side, and — importantly — the parent labels. A band with `discouraged: true` is open *because the surrounding work reported nulls or never reported*: read those refutations before treating the gap as an opportunity.

### Placing an idea

Send an `idea` in the request body. You get back:

- The **closest indexed papers** to your idea, each with a cosine similarity.
- The **region** your idea lands in, with its label and outcome mix.
- The **nearest open band**, if one is close.

Read the number for what it is. The redundancy score is the cosine between your idea's embedding and the single closest indexed paper — **a redundancy statistic, never a probability of novelty**. A high score means very similar work already exists in your index; verify by reading the listed neighbors, which is what they are shown for. A low score means only that *this sample of this index* holds nothing close — it cannot tell you the idea is good, novel, or unstudied elsewhere. The cosine is a heuristic landmark for the embedding model, not a calibrated score.

### Steering with arithmetic

The `arithmetic` field (`{start, remove[], add[]}`: start − remove + add) places a *combination* instead of a sentence: each phrase is embedded, combined, and the result is placed like any other point. `vitamin D for depression − depression + chronic kidney disease` asks "has anyone run the vitamin-D idea in kidney disease?" — and because the answer is the real papers nearest the implied point, it stays honest even when the analogy is loose. List several terms in `remove` or `add` to apply them at once.

### Calibration

`POST /map` with `cutoffYear` rebuilds the map as it looked before that year and reports what later papers did per historical label — did null-saturated regions keep producing nulls? This is a calibration record over one corpus and one cutoff, not a forecast.

## Novelty check (CLI and API)

Separate from the map, the novelty engine reads the strongest matches for a stated hypothesis and reports whether it has already been tested:

```sh
cd backend
.venv/bin/python -m app.novelty 'ACE inhibitors slow CKD progression in diabetic adults' --scan 120 --read 5
```

or `POST /novelty` with `{"hypothesis": "...", "scan": 120, "read": 5}`. Use it when you want a read-and-reasoned verdict on one hypothesis rather than a spatial overview.

## Using the API directly

All routes are served bare and under `/api` (the dev server proxies `/api` to the backend). Requests and responses match `src/types.ts`.

| Route | What it does |
| --- | --- |
| `GET /health` | Liveness. |
| `GET /ready` | Elasticsearch connectivity, index size, whether OpenAI and embeddings are configured. Check this first when anything misbehaves. |
| `POST /search` | Full search. Body: `idea` plus optional `sesoi`, `effectType`, `plannedN`, `alpha`, `valueSuccess`, `valueNull`, `studyCost`, `outcomeSd`, `baselineRisk`, `filters` ({`yearFrom`, `yearTo`, `minCitations`, `maxCitations`}). This is how you run a search with your own prespecified SESOI and study plan. |
| `POST /search/stream` | Same body, Server-Sent Events: `progress` events, then one `result` or `error`. |
| `GET /studies/{id}` | One indexed study, extraction evidence included. |
| `POST /novelty` | Hypothesis novelty check: `{hypothesis, scan, read}`. |
| `POST /map` | The gap map. Optional `idea` or `arithmetic` (`{start, remove[], add[]}`) to place, `cutoffYear` for calibration, `refresh: true` to rebuild the cached map after re-ingesting. |

The map is computed once and cached; after changing the index, send `"refresh": true` (or restart the API) to rebuild it.

## What the numbers do and do not mean

A recap of the interpretations that matter most, because misreading them defeats the tool's purpose:

- **A missing result is an unknown outcome, not a negative finding.** "Never reported" and "no readable outcome" quantify silence.
- **"No significant difference" in an abstract is a claim.** Only an interval inside the equivalence bounds confirms a null. The report keeps "confirmed" and "claimed" apart; so should you.
- **"Made a difference" includes harm.** Check each study's stated direction.
- **Assurance is expected statistical power, not the probability of a meaningful benefit.**
- **Map redundancy is overlap with existing indexed papers, not a novelty probability.** Verify high scores by reading the neighbors; treat low scores as "nothing close in this sample".
- **Every count is bounded by your corpus.** A partial import is a partial map, and the reports say so — keep that caveat when acting on them.
