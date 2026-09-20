/** JSON contract shared with the FastAPI service. */
export type InconclusiveReason =
  | "wide_interval"
  | "mixed_result"
  | "no_result"
  | "other_scale"
  | "no_threshold"
  | "review";

export type Verdict =
  | "effect"
  | "credible_null"
  | "reported_null"
  | "inconclusive"
  | "failed"
  | "unreported";
export type Source = "openalex" | "clinicaltrials" | "ctgov" | "merged" | "arxiv" | "pubmed" | "osf";
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
  abstractAvailable?: boolean | null;
  workType?: string | null;
  snapshotDate?: string | null;
  verdict: Verdict;
  rationale: string;
  /** Set only when verdict is "inconclusive". */
  inconclusiveReason?: InconclusiveReason | null;
  /** Which arm the reported primary result favours; a significant result is not always a benefit. */
  resultDirection?: ResultDirection | null;
  sampleSize: number | null;
  effectSize: EffectSize | null;
  evidenceSpan?: string;
  numericSource?: string | null;
  evidenceTier?: "numeric" | "derived" | "reconstructed" | "text_only";
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
  /** Where the LLM-extracted numbers were read from; registry rows have neither. */
  extractionSource?: "abstract" | "full_text" | null;
  /** PubMed Central ID when the paper is open access there. */
  pmcid?: string | null;
}
export type Recommendation = "pursue" | "pursue_with_changes" | "deprioritize";
export interface PursuitEstimate {
  /** Bayesian expected power, not probability of meaningful benefit. */
  pSuccess: number | null;
  /** Chance the study should be pursued: how unsettled the record is times the planned design's power. */
  pPursue?: number;
  /** Raw expected utility, in the units supplied by the user. */
  expectedValue: number | null;
  confidence: "low" | "medium" | "high";
  recommendation: Recommendation;
  drivers: string[];
}
export type PicoField = "population" | "intervention" | "comparator" | "outcome";
export interface PicoChange {
  field: PicoField;
  to: string;
  reason: string;
}
/** The PICO a new study should ask, given the record; `changes` is empty when the question is worth asking as posed. */
export interface RecommendedPico {
  pico: Record<PicoField, string>;
  changes: PicoChange[];
  rationale: string;
  source: "model" | "rules";
}
/** Pre-search corpus restrictions. Every bound is independently optional. */
export interface SearchFilters {
  yearFrom?: number;
  yearTo?: number;
  minCitations?: number;
  maxCitations?: number;
}
/** What the backend actually applied, and what it cost in coverage. */
export interface AppliedFilters extends SearchFilters {
  description: string[];
  /** Registry rows have no citation count, so citation bounds never apply to them. */
  registryCitationExemption: boolean;
  /** Matching registry rows the citation bound would have removed had it applied. */
  registryExempted?: number | null;
  matchedBeforeFilters: number | null;
  excluded: number | null;
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
  filters?: SearchFilters;
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
/** Egger regression of each study's estimate/SE on its precision 1/SE; the intercept is the asymmetry. */
export interface EggerTest {
  intercept: number;
  se: number;
  ci: [number, number];
  t: number;
  df: number;
  pValue: number;
  slope: number;
  asymmetric: boolean;
  alpha: number;
  method: string;
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
  /** "model" when a language model judged these outcomes comparable; the numbers are still computed. */
  grouping?: "model";
  /** null when the pool has fewer than ten studies, or too little spread in precision, to test. */
  egger?: EggerTest | null;
}
export type ResultDirection = "favours_intervention" | "favours_comparator" | "unclear";
/** What the effect-reporting studies have in common. Counts are computed; only the prose is generated. */
export interface EffectTrend {
  studied: number;
  totalEffects: number;
  favoursIntervention: number;
  favoursComparator: number;
  unclear: number;
  summary: string | null;
  patterns: string[];
  scope: string;
}
export type PursuitState = "unknown" | "open" | "contested" | "favours_effect" | "favours_null";
/** Beta(1, 1) prior updated by tier-weighted verdicts: the chance a real effect exists, as the record stands. */
export interface Pursuit {
  prior: [number, number];
  posterior: [number, number];
  pEffect: number;
  /** Twice the smaller posterior tail around even odds: 1 when nothing (or a balanced conflict) settles it, near 0 when the record leans hard. */
  pOpen: number;
  /** Two-sided power of the planned design against the SESOI; null when no usable plan. */
  power: number | null;
  /** pOpen x power (pOpen alone when power is null). */
  pPursue: number;
  recommendation: Recommendation;
  reasons: string[];
  ci: [number, number];
  successes: number;
  failures: number;
  counted: { effect: number; credible_null: number; reported_null: number };
  uninformative: number;
  /** Share of the informative weight on the minority side; 0.5 is a perfect split. */
  conflict: number;
  state: PursuitState;
  /** Predictive probability that the pooled true effect reaches the SESOI in either direction; null without one matching pool. */
  pMeaningful: number | null;
  pFavours: number | null;
  poolStudyIds: string[];
  method: string;
}
export interface Statistics {
  pools: EvidencePool[];
  pursuit?: Pursuit;
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
  /** Why the inconclusive matches are inconclusive, over the same full match set. */
  inconclusiveReasons?: Partial<Record<InconclusiveReason, number>> | null;
  countScope?: string;
  /** Null unless a bound was set: a result must never look filtered when it is not. */
  filters?: AppliedFilters | null;
  yearCounts?: { year: number; count: number }[];
  nullTerms?: { term: string; score: number; count: number }[];
  pico?: Pico;
  recommendedPico?: RecommendedPico | null;
  statistics?: Statistics;
  costs?: QueryCosts;
  warnings?: string[];
  effectTrend?: EffectTrend | null;
  /** Shown instead of a trend when too few studies reported an effect: what the matches are and why none answers the question. */
  overview?: { summary: string; notes: { id: string; title: string; note: string }[]; scope: string } | null;
  /** Studies read in detail, and how many had an identified comparison group. */
  evidenceBase?: { read: number; controlled: number } | null;
  /** Relevance screen over keyword matches; `complete` means the counts cover relevant studies only. */
  screening?: { screened: number; relevant: number; complete: boolean } | null;
  alternativeRoutes?: { label: string; reason: string; evidenceCount: number }[];
  retrieval?: { mode: string; expanded: number };
  spin?: { eligible: number; disagreements: number };
}

/** Concept search: nearest neighbours of `sum(positive) − sum(negative)` in the embedding space. */
export type ConceptSign = "positive" | "negative";
export interface Concept {
  id: string;
  text: string;
  sign: ConceptSign;
}
export interface ConceptSearchRequest {
  positive: string[];
  negative: string[];
  limit?: number;
}
export interface ConceptMatch {
  id: string;
  title: string;
  year: number | null;
  url: string;
  source: Source;
  verdict: Verdict;
  citations: number;
  /** Cosine to the combined direction, never a probability of relevance. */
  cosine: number;
  /** This paper's cosine to each supplied concept, positives first. */
  concepts: { text: string; sign: ConceptSign; cosine: number }[];
}
export interface ConceptSearchResult {
  version: string;
  concepts: { text: string; sign: ConceptSign }[];
  matches: ConceptMatch[];
  warnings?: string[];
}
