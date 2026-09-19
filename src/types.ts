/** JSON contract shared with the FastAPI service. */
export type Verdict = "effect" | "credible_null" | "inconclusive" | "failed" | "unreported";
export type Source = "openalex" | "clinicaltrials" | "ctgov" | "merged" | "user" | "arxiv" | "pubmed" | "osf";
export type EffectType = "SMD" | "MD" | "logOR" | "logRR" | "logHR";
export interface EffectSize {
  metric: string;
  value: number;
  ci?: [number, number];
}
export interface Paper {
  id: string;
  title: string;
  authors: string[];
  year: number;
  venue: string | null;
  source: Source;
  url: string;
  citations: number;
  verdict: Verdict;
  rationale: string;
  sampleSize: number | null;
  effectSize: EffectSize | null;
  evidenceSpan?: string;
  numericSource?: string | null;
  evidenceTier?: "numeric" | "reconstructed" | "text_only";
  mde?: number | null;
  pValue?: number | null;
  whyStopped?: string | null;
  nctIds?: string[];
  pmids?: string[];
  significantButTrivial?: boolean;
  linkedUrls?: string[];
  analysisEffectType?: string | null;
  analysisEffectSize?: EffectSize | null;
  ciLevel?: number | null;
  pValueOperator?: string | null;
  numericNotes?: string[];
  primaryOutcome?: string | null;
  outcomeUnit?: string | null;
  extractionEvidence?: Record<string, string>;
}
export type Recommendation = "pursue" | "pursue_with_changes" | "deprioritize";
export interface PursuitEstimate {
  /** Bayesian expected power, not probability of meaningful benefit. */
  pSuccess: number | null;
  /** Raw expected utility, in the units supplied by the user. */
  expectedValue: number | null;
  confidence: "low" | "medium" | "high";
  recommendation: Recommendation;
  drivers: string[];
}
export interface SearchRequest {
  idea: string;
  field?: string;
  sesoi?: number;
  effectType?: EffectType;
  plannedN?: number;
  alpha?: number;
  valueSuccess?: number;
  valueNull?: number;
  studyCost?: number;
  outcomeSd?: number;
  baselineRisk?: number;
}
export type SearchStage = "keywords" | "searching" | "classifying" | "estimating";
export interface SearchProgress {
  stage: SearchStage;
  message: string;
  scanned?: number;
}
export interface Pico {
  population: string;
  intervention: string;
  comparator: string;
  outcome: string;
  synonyms: string[];
  studyDesigns: string[];
  sesoi: number;
  sesoiRationale: string;
  effectType: string;
}
export interface EvidencePool {
  effectType: string;
  outcome: string;
  unit: string;
  k: number;
  estimate: number;
  se: number;
  ci: [number, number];
  tau2: number;
  studyIds: string[];
}
export interface Statistics {
  pools: EvidencePool[];
  assurance: number | null;
  requiredN: number | null;
  expectedValue: number | null;
  plannedMde: number | null;
  fileDrawer: { completed: number; unreported: number; share: number | null };
  assumptions: string[];
  warnings: string[];
}
export interface QueryCosts {
  calls: number;
  inputTokens: number;
  outputTokens: number;
  cachedTokens: number;
  estimatedUsd: number;
  latencyMs: number;
  extractionCacheHits: number;
  extracted: number;
  naiveEstimatedUsd: number;
  coldEstimatedUsd: number;
  warmEstimatedUsd: number;
}
export interface SearchResult {
  queryId: string;
  idea: string;
  field: string | null;
  keywords: string[];
  searchedSources: Source[];
  totalScanned: number;
  papers: Paper[];
  summary: string;
  estimate: PursuitEstimate;
  completedAt: string;
  bucketCounts?: Record<Verdict, number>;
  countScope?: string;
  yearCounts?: { year: number; count: number }[];
  nullTerms?: { term: string; score: number; count: number }[];
  pico?: Pico;
  statistics?: Statistics;
  costs?: QueryCosts;
  warnings?: string[];
  alternativeRoutes?: { label: string; reason: string; evidenceCount: number }[];
  retrieval?: { mode: string; expanded: number };
  spin?: { eligible: number; disagreements: number };
}
export interface ContributionRequest {
  title: string;
  description: string;
  outcome: Verdict;
  files: File[];
  ownershipAcknowledged: boolean;
}
export interface ContributionReceipt {
  contributionId: string;
  status: "queued" | "drafting" | "ready";
  receivedAt: string;
}
