// Mock backend. Everything in this file is sample data for UI development.
// Delete it once VITE_API_URL points at a real server.
import type {
  ContributionReceipt,
  ContributionRequest,
  SearchProgress,
  SearchRequest,
  SearchResult,
} from "../types";

const STAGES: SearchProgress[] = [
  { stage: "keywords", message: "Extracting hypotheses and null-signal phrases" },
  { stage: "searching", message: "Querying OpenAlex, arXiv, PubMed and OSF registries", scanned: 0 },
  { stage: "classifying", message: "Reading abstracts and assigning verdicts" },
  { stage: "estimating", message: "Computing expected value of pursuit" },
];

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("Aborted", "AbortError"));
    const t = setTimeout(resolve, ms);
    signal?.addEventListener(
      "abort",
      () => {
        clearTimeout(t);
        reject(new DOMException("Aborted", "AbortError"));
      },
      { once: true },
    );
  });
}

export async function mockSearch(
  req: SearchRequest,
  onProgress?: (p: SearchProgress) => void,
  signal?: AbortSignal,
): Promise<SearchResult> {
  for (const stage of STAGES) {
    onProgress?.(stage);
    if (stage.stage === "searching") {
      for (const scanned of [412, 1180, 2347]) {
        await wait(350, signal);
        onProgress?.({ ...stage, scanned });
      }
    } else {
      await wait(650, signal);
    }
  }
  return {
    ...SAMPLE_RESULT,
    idea: req.idea,
    field: req.field ?? null,
    queryId: `q_${Math.random().toString(36).slice(2, 10)}`,
    completedAt: new Date().toISOString(),
  };
}

export async function mockSubmitContribution(
  req: ContributionRequest,
  signal?: AbortSignal,
): Promise<ContributionReceipt> {
  await wait(900, signal);
  void req;
  return {
    contributionId: `c_${Math.random().toString(36).slice(2, 10)}`,
    status: "queued",
    receivedAt: new Date().toISOString(),
  };
}

// Sample result for: "Does intermittent fasting improve working memory in healthy adults?"
export const SAMPLE_RESULT: SearchResult = {
  queryId: "q_sample",
  idea: "Does intermittent fasting improve working memory in healthy adults?",
  field: null,
  keywords: [
    "intermittent fasting",
    "time-restricted eating",
    "working memory",
    "n-back",
    "no significant difference",
    "equivalence",
  ],
  searchedSources: ["openalex", "arxiv", "pubmed", "osf", "clinicaltrials"],
  totalScanned: 2347,
  papers: [
    {
      id: "p1",
      title:
        "Time-restricted eating and executive function in young adults: a 12-week randomized trial",
      authors: ["A. Okonkwo", "L. Brandt", "M. Sallinen"],
      year: 2022,
      venue: "Appetite",
      source: "pubmed",
      url: "https://openalex.org/",
      citations: 41,
      verdict: "effect",
      rationale:
        "Reported a small improvement on a 2-back task at 12 weeks; the interval clears zero but only just.",
      sampleSize: 84,
      effectSize: { metric: "d", value: 0.31, ci: [0.05, 0.57] },
    },
    {
      id: "p2",
      title: "Alternate-day fasting improves n-back performance in a crossover design",
      authors: ["D. Varga", "R. Nakashima"],
      year: 2019,
      venue: "Physiology & Behavior",
      source: "openalex",
      url: "https://openalex.org/",
      citations: 67,
      verdict: "effect",
      rationale:
        "Moderate within-subject gain, but the crossover had no washout and participants were unblinded.",
      sampleSize: 36,
      effectSize: { metric: "d", value: 0.44, ci: [0.09, 0.79] },
    },
    {
      id: "p3",
      title:
        "No effect of 16:8 intermittent fasting on working memory: a preregistered equivalence trial",
      authors: ["S. Delacroix", "C. Ijeoma", "P. Holmberg", "W. Tan"],
      year: 2023,
      venue: "Psychological Science",
      source: "openalex",
      url: "https://openalex.org/",
      citations: 12,
      verdict: "credible_null",
      rationale:
        "Preregistered, powered for an equivalence bound of d = 0.2, and the observed interval sits entirely inside it.",
      sampleSize: 212,
      effectSize: { metric: "d", value: 0.02, ci: [-0.11, 0.15] },
    },
    {
      id: "p4",
      title: "Fasting state and cognitive performance in rotating-shift nurses",
      authors: ["H. Meriläinen"],
      year: 2018,
      venue: "Chronobiology International",
      source: "pubmed",
      url: "https://openalex.org/",
      citations: 23,
      verdict: "inconclusive",
      rationale:
        "Interval spans zero on both sides; the sample was too small for the effect size the authors expected.",
      sampleSize: 41,
      effectSize: { metric: "d", value: 0.18, ci: [-0.22, 0.58] },
    },
    {
      id: "p5",
      title: "Metabolic switching and short-term memory: a pilot study",
      authors: ["T. Adebayo", "J. Kowalczyk"],
      year: 2021,
      venue: null,
      source: "arxiv",
      url: "https://arxiv.org/",
      citations: 3,
      verdict: "inconclusive",
      rationale: "Pilot with no control arm and no effect size reported; direction only.",
      sampleSize: 19,
      effectSize: null,
    },
    {
      id: "p6",
      title: "Intermittent fasting in older adults: cognitive secondary outcomes at 6 months",
      authors: ["N. Petrosyan", "E. Whitcombe"],
      year: 2020,
      venue: "Journal of Nutrition, Health & Aging",
      source: "pubmed",
      url: "https://openalex.org/",
      citations: 55,
      verdict: "inconclusive",
      rationale:
        "Cognition was a secondary outcome in a weight-loss trial; not powered for it and the sample skews older than your population.",
      sampleSize: 63,
      effectSize: { metric: "d", value: 0.12, ci: [-0.2, 0.44] },
    },
    {
      id: "p7",
      title: "Ketone availability and working memory under caloric restriction",
      authors: ["K. Lindqvist", "F. Oyelaran"],
      year: 2017,
      venue: "Nutritional Neuroscience",
      source: "openalex",
      url: "https://openalex.org/",
      citations: 18,
      verdict: "failed",
      rationale:
        "Nearly half the fasting arm dropped out before the post-test and the analysis was not intention-to-treat.",
      sampleSize: 52,
      effectSize: null,
    },
    {
      id: "p8",
      title: "IF-COG: Effects of intermittent fasting on cognition in healthy adults",
      authors: ["M. Castellanos"],
      year: 2019,
      venue: null,
      source: "clinicaltrials",
      url: "https://clinicaltrials.gov/",
      citations: 0,
      verdict: "unreported",
      rationale:
        "Registered with a planned n of 120 and a working-memory primary outcome. Marked complete in 2021. No results posted.",
      sampleSize: 120,
      effectSize: null,
    },
  ],
  summary:
    "Eight prior attempts come close to this question. The two positive results both come from small samples with intervals that barely clear zero, and one of them was unblinded. The single well-powered, preregistered trial found equivalence to no effect. The three inconclusive studies were underpowered rather than mixed, and a registered trial of 120 participants finished in 2021 without posting results, which is consistent with an unpublished null. If you run this, the design change that matters most is sample size: nothing below roughly 200 participants will move the estimate.",
  estimate: {
    pSuccess: 0.27,
    expectedValue: -0.18,
    confidence: "medium",
    recommendation: "pursue_with_changes",
    drivers: [
      "The one high-powered preregistered trial found equivalence to zero.",
      "Both positive results have n below 100 and lower confidence bounds near zero.",
      "Three inconclusive trials were underpowered, not conflicting.",
      "A registered n = 120 trial never reported, consistent with a file-drawer null.",
    ],
  },
  completedAt: "2026-09-19T14:00:00.000Z",
};
