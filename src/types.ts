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
  /** Raw expected utility, in the units supplied by the user. */
  expectedValue: number | null;
  confidence: "low" | "medium" | "high";
  recommendation: Recommendation;
  drivers: string[];
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
  /** Why the inconclusive matches are inconclusive, over the same full match set. */
  inconclusiveReasons?: Partial<Record<InconclusiveReason, number>> | null;
  countScope?: string;
  /** Null unless a bound was set: a result must never look filtered when it is not. */
  filters?: AppliedFilters | null;
  yearCounts?: { year: number; count: number }[];
  nullTerms?: { term: string; score: number; count: number }[];
  pico?: Pico;
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
/** Gap map: regions of the embedded corpus, described by what happened in them. */
export type RegionLabel =
  | "active"
  | "contested"
  | "null_saturated"
  | "dark"
  | "unread"
  | "thin";
export interface MapExemplar {
  id: string;
  title: string;
  year: number | null;
  bucket: Verdict | string;
}
export interface MapRegion {
  id: number;
  size: number;
  attempts: number;
  label: RegionLabel;
  bucketCounts: Partial<Record<Verdict, number>>;
  medianYear: number | null;
  medianCitations: number | null;
  coherence: number;
  exemplars: MapExemplar[];
  /** Present only on the region an idea was placed in. */
  cosine?: number;
  /** Centroid projected into the map's 2-D plane. */
  x: number;
  y: number;
}
/** One sampled study's position in the map's 2-D plane. */
export interface MapPoint {
  id: string;
  x: number;
  y: number;
  /** Id of the region this study was assigned to. */
  region: number;
  bucket: Verdict | string;
  title: string;
  year: number | null;
}
export interface MapNeighbour extends MapExemplar {
  cosine: number;
}
export interface MapGap {
  regions: number[];
  parentLabels: RegionLabel[];
  openness: number;
  support: number;
  band: number;
  separation: number;
  nearest: MapNeighbour;
  exemplars: MapExemplar[][];
  discouraged: boolean;
  /** Present only when an idea was placed against the gaps. */
  cosine?: number;
}
export interface MapArithmetic {
  start: string;
  remove: string[];
  add: string[];
}
export interface MapPlacement {
  redundancy: number | null;
  nearest: MapNeighbour | null;
  /** The next-closest papers after `nearest`, so closeness can be judged by reading. */
  neighbors?: MapNeighbour[];
  region: MapRegion | null;
  nearestGap: MapGap | null;
  /** The idea projected into the map's 2-D plane. */
  point?: { x: number; y: number } | null;
  /** Echoed back when the placement came from an arithmetic expression. */
  expression?: MapArithmetic;
}
export interface MapCalibrationRow {
  label: RegionLabel;
  regions: number;
  priorAttempts: number;
  laterPapers: number;
  laterNull: number;
  laterEffect: number;
  laterNullShare: number | null;
}
export interface GapMap {
  version: string;
  coverage: { sampled: number; corpus: number; regions: number };
  regions: MapRegion[];
  gaps: MapGap[];
  points: MapPoint[];
  placement: MapPlacement | null;
  calibration: {
    version: string;
    cutoffYear: number;
    past: number;
    future: number;
    labels: MapCalibrationRow[];
  } | null;
  warnings: string[];
}
export interface MapRequest {
  idea?: string;
  /** start − remove + add over embeddings; placed on the map like an idea. */
  arithmetic?: MapArithmetic;
  cutoffYear?: number;
  refresh?: boolean;
}
