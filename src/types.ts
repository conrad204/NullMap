/**
 * nullMap API contract.
 *
 * This file is the source of truth shared by the frontend and the backend.
 * The backend should return JSON matching these shapes. See README.md for
 * the endpoint list.
 */

/** How a prior attempt at the idea turned out. */
export type Verdict =
  | "effect" // reported a meaningful effect with adequate power
  | "credible_null" // well-powered, found no meaningful effect
  | "inconclusive" // underpowered or mixed, cannot support or rule out
  | "failed" // completed but methodological problems undermine the result
  | "unreported"; // registered or funded, results never published

export type Source = "openalex" | "arxiv" | "pubmed" | "osf" | "clinicaltrials" | "user";

export interface EffectSize {
  /** e.g. "d", "g", "OR", "r", "beta" */
  metric: string;
  value: number;
  /** 95% confidence interval, when reported */
  ci?: [number, number];
}

export interface Paper {
  id: string;
  title: string;
  /** Short-form author names, e.g. "A. Okonkwo". */
  authors: string[];
  year: number;
  venue: string | null;
  source: Source;
  url: string;
  citations: number;
  verdict: Verdict;
  /** One sentence explaining why the classifier assigned this verdict. */
  rationale: string;
  sampleSize: number | null;
  effectSize: EffectSize | null;
}

export type Recommendation = "pursue" | "pursue_with_changes" | "deprioritize";

export interface PursuitEstimate {
  /** Posterior probability (0..1) that pursuing the idea yields a meaningful effect. */
  pSuccess: number;
  /** Expected value of pursuit on a -1..1 scale. Negative means the prior evidence argues against it. */
  expectedValue: number;
  confidence: "low" | "medium" | "high";
  recommendation: Recommendation;
  /** Short, plain-language reasons behind the numbers. */
  drivers: string[];
}

export interface SearchRequest {
  idea: string;
  /** Optional discipline hint to narrow the search. */
  field?: string;
}

export type SearchStage = "keywords" | "searching" | "classifying" | "estimating";

/** Emitted by the backend while a search is running (SSE or websocket). */
export interface SearchProgress {
  stage: SearchStage;
  message: string;
  /** Records scanned so far, when known. */
  scanned?: number;
}

export interface SearchResult {
  queryId: string;
  idea: string;
  field: string | null;
  /** Keywords and null-signal phrases the search matched on. */
  keywords: string[];
  searchedSources: Source[];
  totalScanned: number;
  papers: Paper[];
  /** Plain-prose summary of the state of the evidence. */
  summary: string;
  estimate: PursuitEstimate;
  /** ISO 8601 */
  completedAt: string;
}

/** A researcher dropping a shelved or unpublished experiment. */
export interface ContributionRequest {
  title: string;
  description: string;
  outcome: Verdict;
  files: File[];
  /** Contributor keeps ownership and grants nullMap a non-exclusive license to index. */
  ownershipAcknowledged: boolean;
}

export interface ContributionReceipt {
  contributionId: string;
  status: "queued" | "drafting" | "ready";
  /** ISO 8601 */
  receivedAt: string;
}
