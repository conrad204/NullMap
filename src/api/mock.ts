/** Entirely fictional fixtures, reachable only when VITE_USE_MOCK=true. */
import type { SearchProgress, SearchRequest, SearchResult } from "../types";
import { describeFilters } from "../lib/filters";
function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("Aborted", "AbortError"));
    const abort = () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); };
    const timer = setTimeout(() => { signal?.removeEventListener("abort", abort); resolve(); }, ms);
    signal?.addEventListener("abort", abort, { once: true });
  });
}
export async function mockSearch(req: SearchRequest, onProgress?: (progress: SearchProgress) => void, signal?: AbortSignal): Promise<SearchResult> {
  for (const stage of ["keywords", "searching", "classifying", "estimating"] as const) {
    onProgress?.({ stage, message: "Illustrative demo — simulating a search; no sources are being queried." });
    await wait(300, signal);
  }
  return {
    ...SAMPLE_RESULT, idea: req.idea, field: req.field ?? null, queryId: `demo_${Date.now()}`,
    completedAt: new Date().toISOString(), filters: mockFilters(req.filters),
    papers: steerPapers(SAMPLE_RESULT.papers, req.concepts),
    retrieval: { mode: "hybrid", expanded: 0, concepts: req.concepts ?? null },
  };
}
/** The demo of the semantics: tags reorder the same fictional studies, and remove none. */
function steerPapers(papers: SearchResult["papers"], concepts: SearchRequest["concepts"]): SearchResult["papers"] {
  if (!concepts) return papers;
  const mentions = (paper: SearchResult["papers"][number], term: string) =>
    `${paper.title} ${paper.rationale}`.toLowerCase().includes(term.trim().toLowerCase());
  const pull = (paper: SearchResult["papers"][number]) =>
    concepts.positive.filter((term) => mentions(paper, term)).length -
    concepts.negative.filter((term) => mentions(paper, term)).length;
  return papers.map((paper, index) => ({ paper, index })).sort((a, b) => pull(b.paper) - pull(a.paper) || a.index - b.index).map((entry) => entry.paper);
}
/** Echoes the requested bounds so the filtered-corpus notices are visible in the demo.
 *  The counts are as fictional as the rest of this file; the 2 registry rows are its own. */
function mockFilters(filters: SearchRequest["filters"]): SearchResult["filters"] {
  if (!filters) return null;
  const citationBound = filters.minCitations !== undefined || filters.maxCitations !== undefined;
  return {
    ...filters,
    description: describeFilters(filters),
    registryCitationExemption: citationBound,
    registryExempted: citationBound ? 2 : null,
    matchedBeforeFilters: SAMPLE_RESULT.totalScanned + 5,
    excluded: 5,
  };
}
export const SAMPLE_RESULT: SearchResult = {
  queryId: "illustrative_demo",
  idea: "Illustrative evidence map: an example clinical intervention",
  field: "Medicine and health",
  keywords: ["illustrative intervention", "example outcome"],
  searchedSources: ["openalex", "clinicaltrials"],
  totalScanned: 9,
  yearCounts: [{ year: 2021, count: 2 }, { year: 2022, count: 2 }, { year: 2023, count: 2 }, { year: 2024, count: 3 }],
  effectTrend: { studied: 3, totalEffects: 3, favoursIntervention: 1, favoursComparator: 1, unclear: 1, summary: null, patterns: [], scope: "Fictional example: based on the 3 effect-reporting studies shown." },
  bucketCounts: { effect: 3, credible_null: 3, reported_null: 0, inconclusive: 1, failed: 1, unreported: 1 },
  effectDirections: { favoursIntervention: 1, favoursComparator: 1, unclear: 1 },
  countScope: "Fictional example counts. No actual literature search was performed.",
  warnings: ["All studies, numbers, costs, and findings on this page are fictional UI fixtures. They are unrelated to your research question and must not inform a study decision."],
  papers: [
    { id: "demo_effect", title: "Illustrative study A — a meaningful effect", authors: [], year: 2024, venue: "Fictional fixture", source: "openalex", url: "", citations: 0, verdict: "effect", resultDirection: "favours_intervention", rationale: "Example interval excludes zero and reaches an SMD threshold of 0.2.", sampleSize: 200, effectSize: { metric: "SMD", value: 0.4, ci: [0.12, 0.68] }, ciLevel: 0.95, evidenceTier: "numeric", mde: 0.4, evidenceSpan: "Fictional source sentence: the standardized mean difference was 0.40 (95% CI 0.12 to 0.68)." },
    { id: "demo_harm", title: "Illustrative study A2 — a meaningful effect the other way", authors: [], year: 2022, venue: "Fictional fixture", source: "openalex", url: "", citations: 0, verdict: "effect", resultDirection: "favours_comparator", rationale: "Example interval excludes zero on the comparator's side and reaches an SMD threshold of 0.2.", sampleSize: 180, effectSize: { metric: "SMD", value: -0.35, ci: [-0.61, -0.09] }, ciLevel: 0.95, evidenceTier: "numeric", mde: 0.42, evidenceSpan: "Fictional source sentence: outcomes were worse in the intervention arm (SMD -0.35, 95% CI -0.61 to -0.09)." },
    { id: "demo_effect_unstated", title: "Illustrative study A3 — a difference of unstated direction", authors: [], year: 2024, venue: "Fictional fixture", source: "openalex", url: "", citations: 0, verdict: "effect", resultDirection: "unclear", rationale: "The example report states a significant difference without saying which arm it favoured.", sampleSize: 150, effectSize: null, evidenceTier: "text_only", evidenceSpan: "Fictional source sentence: the difference between groups was statistically significant (p = 0.01)." },
    ...[1, 2, 3].map((index) => ({ id: `demo_null_${index}`, title: `Illustrative equivalence study ${index}`, authors: [], year: 2020 + index, venue: "Fictional fixture", source: "openalex" as const, url: "", citations: 0, verdict: "credible_null" as const, rationale: "The example confidence interval lies entirely inside ±0.2.", sampleSize: 800, effectSize: { metric: "SMD", value: 0.02, ci: [-0.12, 0.16] as [number, number] }, ciLevel: 0.95, evidenceTier: "numeric" as const, mde: 0.2 })),
    { id: "demo_inconclusive", title: "Illustrative study B — an uncertain result", authors: [], year: 2024, venue: "Fictional fixture", source: "openalex", url: "", citations: 0, verdict: "inconclusive", inconclusiveReason: "wide_interval", rationale: "This fictional interval includes both zero and effects larger than the selected threshold.", sampleSize: 40, effectSize: { metric: "SMD", value: 0.1, ci: [-0.52, 0.72] }, ciLevel: 0.95, evidenceTier: "reconstructed", mde: 0.89 },
    { id: "demo_failed", title: "Illustrative trial C — stopped for recruitment", authors: [], year: 2023, venue: null, source: "clinicaltrials", url: "", citations: 0, verdict: "failed", rationale: "The example trial was terminated before reaching its planned enrollment.", sampleSize: 12, effectSize: null, evidenceTier: "text_only", whyStopped: "Illustrative reason: recruitment target could not be met." },
    { id: "demo_unreported", title: "Illustrative trial D — outcomes unreported", authors: [], year: 2021, venue: null, source: "clinicaltrials", url: "", citations: 0, verdict: "unreported", rationale: "Example completed trial without posted results or a linked publication. Its outcome is unknown.", sampleSize: 120, effectSize: null, evidenceTier: "text_only" },
  ],
  pico: { population: "Example population", intervention: "Illustrative intervention", comparator: "Example control", outcome: "Example outcome", synonyms: [], studyDesigns: ["Randomized trial"], sesoi: 0.2, sesoiRationale: "Fictional threshold solely for illustrating the interface.", effectType: "SMD" },
  summary: "This fictional map demonstrates meaningful effects, equivalence bounds, uncertain estimates, stopped trials, and unreported outcomes. It is a UI example, not a finding about any intervention. The unreported trial has an unknown outcome.",
  estimate: { pSuccess: null, pPursue: 0.105, expectedValue: null, confidence: "low", recommendation: "deprioritize", drivers: ["No study-planning recommendation is computed from fictional data."] },
  recommendedPico: { pico: { population: "Example population not yet studied", intervention: "Illustrative intervention", comparator: "Example control", outcome: "Example outcome" }, changes: [{ field: "population", to: "Example population not yet studied", reason: "Fictional: the studied population leans towards no effect while this one is absent from the matches." }], rationale: "Fictional rationale: redirect the question rather than repeat it.", source: "rules" },
  statistics: { pursuit: { prior: [1, 1], posterior: [2, 4], pEffect: 0.333, pOpen: 0.375, power: 0.28, pPursue: 0.105, recommendation: "deprioritize", reasons: ["The record leans one way (33% chance of a real effect) but the credible interval still spans even odds.", "The planned design has only 28% power to detect the SESOI; it would likely end inconclusive, which lowers the chance it is worth running as planned.", "100% of eligible completed trials never reported; the record may understate null results, so the lean above may be too optimistic."], ci: [0.053, 0.716], successes: 1, failures: 3, counted: { effect: 1, credible_null: 3, reported_null: 0 }, uninformative: 3, conflict: 0.25, state: "open", pMeaningful: 0.02, pFavours: 0.69, poolStudyIds: ["demo_null_1", "demo_null_2", "demo_null_3"], method: "Fictional." }, pools: [{ effectType: "SMD", outcome: "Fictional example outcome", unit: "SD", k: 3, estimate: 0.02, se: 0.0412, ci: [-0.061, 0.101], tau2: 0, studyIds: ["demo_null_1", "demo_null_2", "demo_null_3"], egger: null }], assurance: null, requiredN: null, expectedValue: null, plannedMde: null, fileDrawer: { completed: 1, unreported: 1, share: 1 }, assumptions: ["Everything in this example is fictional."], warnings: ["This demonstration is not suitable for study planning."] },
  costs: { calls: 0, inputTokens: 0, outputTokens: 0, cachedTokens: 0, estimatedUsd: 0, latencyMs: 0, extractionCacheHits: 0, extracted: 0, naiveEstimatedUsd: 0, coldEstimatedUsd: 0, warmEstimatedUsd: 0 },
  completedAt: "2026-09-19T14:00:00.000Z",
};
