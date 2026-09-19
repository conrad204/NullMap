/** Entirely fictional fixtures, reachable only when VITE_USE_MOCK=true. */
import type { ContributionReceipt, ContributionRequest, SearchProgress, SearchRequest, SearchResult } from "../types";
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
  return { ...SAMPLE_RESULT, idea: req.idea, field: req.field ?? null, queryId: `demo_${Date.now()}`, completedAt: new Date().toISOString() };
}
export async function mockSubmitContribution(req: ContributionRequest, signal?: AbortSignal): Promise<ContributionReceipt> {
  void req;
  await wait(300, signal);
  return { contributionId: `demo_${Date.now()}`, status: "queued", receivedAt: new Date().toISOString() };
}
export const SAMPLE_RESULT: SearchResult = {
  queryId: "illustrative_demo",
  idea: "Illustrative evidence map: an example clinical intervention",
  field: "Medicine and health",
  keywords: ["illustrative intervention", "example outcome"],
  searchedSources: ["openalex", "clinicaltrials"],
  totalScanned: 7,
  bucketCounts: { effect: 1, credible_null: 3, inconclusive: 1, failed: 1, unreported: 1 },
  countScope: "Fictional example counts. No actual literature search was performed.",
  warnings: ["All studies, numbers, costs, and findings on this page are fictional UI fixtures. They are unrelated to your research question and must not inform a study decision."],
  papers: [
    { id: "demo_effect", title: "Illustrative study A — a meaningful effect", authors: [], year: 2024, venue: "Fictional fixture", source: "openalex", url: "", citations: 0, verdict: "effect", rationale: "Example interval excludes zero and reaches an SMD threshold of 0.2.", sampleSize: 200, effectSize: { metric: "SMD", value: 0.4, ci: [0.12, 0.68] }, ciLevel: 0.95, evidenceTier: "numeric", mde: 0.4, evidenceSpan: "Fictional source sentence: the standardized mean difference was 0.40 (95% CI 0.12 to 0.68)." },
    ...[1, 2, 3].map((index) => ({ id: `demo_null_${index}`, title: `Illustrative equivalence study ${index}`, authors: [], year: 2020 + index, venue: "Fictional fixture", source: "openalex" as const, url: "", citations: 0, verdict: "credible_null" as const, rationale: "The example confidence interval lies entirely inside ±0.2.", sampleSize: 800, effectSize: { metric: "SMD", value: 0.02, ci: [-0.12, 0.16] as [number, number] }, ciLevel: 0.95, evidenceTier: "numeric" as const, mde: 0.2 })),
    { id: "demo_inconclusive", title: "Illustrative study B — an uncertain result", authors: [], year: 2024, venue: "Fictional fixture", source: "openalex", url: "", citations: 0, verdict: "inconclusive", rationale: "This fictional interval includes both zero and effects larger than the selected threshold.", sampleSize: 40, effectSize: { metric: "SMD", value: 0.1, ci: [-0.52, 0.72] }, ciLevel: 0.95, evidenceTier: "reconstructed", mde: 0.89 },
    { id: "demo_failed", title: "Illustrative trial C — stopped for recruitment", authors: [], year: 2023, venue: null, source: "clinicaltrials", url: "", citations: 0, verdict: "failed", rationale: "The example trial was terminated before reaching its planned enrollment.", sampleSize: 12, effectSize: null, evidenceTier: "text_only", whyStopped: "Illustrative reason: recruitment target could not be met." },
    { id: "demo_unreported", title: "Illustrative trial D — outcomes unreported", authors: [], year: 2021, venue: null, source: "clinicaltrials", url: "", citations: 0, verdict: "unreported", rationale: "Example completed trial without posted results or a linked publication. Its outcome is unknown.", sampleSize: 120, effectSize: null, evidenceTier: "text_only" },
  ],
  pico: { population: "Example population", intervention: "Illustrative intervention", comparator: "Example control", outcome: "Example outcome", synonyms: [], studyDesigns: ["Randomized trial"], sesoi: 0.2, sesoiRationale: "Fictional threshold solely for illustrating the interface.", effectType: "SMD" },
  summary: "This fictional map demonstrates meaningful effects, equivalence bounds, uncertain estimates, stopped trials, and unreported outcomes. It is a UI example, not a finding about any intervention. The unreported trial has an unknown outcome.",
  estimate: { pSuccess: null, expectedValue: null, confidence: "low", recommendation: "pursue_with_changes", drivers: ["No study-planning recommendation is computed from fictional data."] },
  statistics: { pools: [{ effectType: "SMD", outcome: "Fictional example outcome", unit: "SD", k: 3, estimate: 0.02, se: 0.0412, ci: [-0.061, 0.101], tau2: 0, studyIds: ["demo_null_1", "demo_null_2", "demo_null_3"] }], assurance: null, requiredN: null, expectedValue: null, plannedMde: null, fileDrawer: { completed: 1, unreported: 1, share: 1 }, assumptions: ["Everything in this example is fictional."], warnings: ["This demonstration is not suitable for study planning."] },
  costs: { calls: 0, inputTokens: 0, outputTokens: 0, cachedTokens: 0, estimatedUsd: 0, latencyMs: 0, extractionCacheHits: 0, extracted: 0, naiveEstimatedUsd: 0, coldEstimatedUsd: 0, warmEstimatedUsd: 0 },
  completedAt: "2026-09-19T14:00:00.000Z",
};
