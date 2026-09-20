import type { EffectDirections, InconclusiveReason, Paper, ResultDirection, Verdict } from "../types";

export const VERDICT_ORDER: Verdict[] = [
  "effect",
  "credible_null",
  "reported_null",
  "inconclusive",
  "failed",
  "unreported",
];

/** Inconclusive is not a finding, so it is reported as a count beside the bar, not in it. */
export const BAR_VERDICTS: Verdict[] = VERDICT_ORDER.filter((verdict) => verdict !== "inconclusive");

export type BarGroupKey =
  | "favours_intervention"
  | "favours_comparator"
  | "no_difference"
  | "no_answer"
  | "direction_unstated";

/**
 * An answer in the bar. A group can be finer than a bucket — the two effect directions split one —
 * so it carries its own test on a study and its own total, and the bar, the legend and the
 * study-list filter all read from here rather than re-deriving the grouping.
 */
export interface BarGroup {
  key: BarGroupKey;
  label: string;
  description: string;
  /** The buckets a group draws from, for the study-type breakdown under its count. */
  verdicts: Verdict[];
  /** Set when the group is finer than its bucket, so its total needs a direction breakdown. */
  direction?: ResultDirection;
  matches: (paper: Paper) => boolean;
  total: (counts: Record<Verdict, number>, directions: EffectDirections) => number;
  /** The line under the count in the legend. */
  detail: (counts: Record<Verdict, number>) => string;
  bg: string;
}

const isEffect = (paper: Paper, direction: ResultDirection) =>
  paper.verdict === "effect" && (paper.resultDirection ?? "unclear") === direction;
const bucketDetail = (verdicts: Verdict[]) => (counts: Record<Verdict, number>) =>
  verdicts.length === 1
    ? VERDICT_META[verdicts[0]].short
    : verdicts.map((verdict) => `${counts[verdict]} ${VERDICT_META[verdict].short}`).join(" · ");

/**
 * The bar's answers. Direction is a property of an effect, not a bucket, so the backend taxonomy is
 * unchanged: `effect` is split by the direction its own report stated. Nulls stay one answer, with
 * "confirmed" and "claimed" counted apart in the line under it, because they are strengths of the
 * same evidence rather than separate answers.
 */
export const BAR_GROUPS: BarGroup[] = [
  {
    key: "favours_intervention",
    label: "Favoured the intervention",
    description: "The groups ended up different and the study's own report puts the intervention ahead. Either the 95% interval excludes zero and the estimate reaches your meaningful-effect threshold, or a controlled study states a significant result without usable numbers. The direction is only as good as the quoted sentence it was read from.",
    verdicts: ["effect"],
    direction: "favours_intervention",
    matches: (paper) => isEffect(paper, "favours_intervention"),
    total: (_counts, directions) => directions.favoursIntervention,
    detail: () => "significant difference, intervention ahead",
    bg: "bg-v-effect",
  },
  {
    key: "favours_comparator",
    label: "Favoured the comparator",
    description: "The groups ended up different and the study's own report puts the comparator ahead, so this is where harm from the intervention appears. The evidence rules are the same as for the opposite direction, and the direction is only as good as the quoted sentence it was read from.",
    verdicts: ["effect"],
    direction: "favours_comparator",
    matches: (paper) => isEffect(paper, "favours_comparator"),
    total: (_counts, directions) => directions.favoursComparator,
    detail: () => "significant difference, comparator ahead, including harm",
    bg: "bg-v-failed",
  },
  {
    key: "no_difference",
    label: "Made no difference",
    description: "Confirmed means the whole 95% interval sits inside your meaningful-effect threshold, so any effect is too small to matter. Claimed means a controlled study reports no significant difference, which is not evidence of equivalence.",
    verdicts: ["credible_null", "reported_null"],
    matches: (paper) => paper.verdict === "credible_null" || paper.verdict === "reported_null",
    total: (counts) => counts.credible_null + counts.reported_null,
    detail: bucketDetail(["credible_null", "reported_null"]),
    bg: "bg-v-null",
  },
  {
    key: "no_answer",
    label: "No answer",
    description: "The study never answered the question. Stopped or flawed means retracted, terminated, withdrawn or suspended, enrolled under half of plan, or run without a control arm. Never reported means a registered trial completed over 12 months ago with no posted results or linked publication.",
    verdicts: ["failed", "unreported"],
    matches: (paper) => paper.verdict === "failed" || paper.verdict === "unreported",
    total: (counts) => counts.failed + counts.unreported,
    detail: bucketDetail(["failed", "unreported"]),
    bg: "bg-v-unreported",
  },
];

/**
 * An effect whose report never said which arm it favoured. It is a difference, so it cannot be
 * dropped, but it is not an answer to "benefit or harm": it is counted beside the bar under its own
 * label, the way inconclusive matches are.
 */
export const UNSTATED_DIRECTION_GROUP: BarGroup = {
  key: "direction_unstated",
  label: "Made a difference, direction not stated",
  description: "The groups ended up different, but the report does not make the better arm evident, so it is not counted as benefit or as harm.",
  verdicts: ["effect"],
  direction: "unclear",
  matches: (paper) => isEffect(paper, "unclear"),
  total: (_counts, directions) => directions.unclear,
  detail: () => "significant difference, neither arm identified",
  bg: "bg-v-effect",
};

/** Every group a study list can be filtered by: the bar's answers plus the unstated-direction note. */
export const FILTER_GROUPS: BarGroup[] = [...BAR_GROUPS, UNSTATED_DIRECTION_GROUP];

/** Displayed studies only: the fallback for a payload that carries no direction split. */
export function directionsFromPapers(papers: Paper[]): EffectDirections {
  return {
    favoursIntervention: papers.filter((paper) => isEffect(paper, "favours_intervention")).length,
    favoursComparator: papers.filter((paper) => isEffect(paper, "favours_comparator")).length,
    unclear: papers.filter((paper) => isEffect(paper, "unclear")).length,
  };
}

/** Ordered from "says something about the study" to "says something about what we could read". */
export const INCONCLUSIVE_REASONS: { key: InconclusiveReason; label: string; detail: string }[] = [
  {
    key: "wide_interval",
    label: "Too imprecise to call",
    detail: "Numbers were reported, but the 95% interval includes both no effect and effects large enough to matter. Usually an underpowered study: the question is still open.",
  },
  {
    key: "mixed_result",
    label: "Conflicting primary results",
    detail: "The report states results that point in different directions, and no interval is available to weigh them.",
  },
  {
    key: "other_scale",
    label: "Reported on another scale",
    detail: "The study reports, say, a hazard ratio while your threshold is a standardized difference. The result is real but cannot be compared with this threshold.",
  },
  {
    key: "no_result",
    label: "No readable result",
    detail: "No usable numbers and no stated outcome: a protocol, a non-comparative paper, a record without an abstract, or an abstract that does not say what was found. This reflects what could be read, not what the study found.",
  },
  {
    key: "no_threshold",
    label: "No valid threshold",
    detail: "The meaningful-effect threshold was missing or invalid for this scale, so nothing could be compared with it.",
  },
];

interface VerdictMeta {
  label: string;
  /** The line under the bucket's answer in the bar legend: after its count, or alone when the answer has one bucket. */
  short: string;
  description: string;
  /** Full class names so Tailwind can pick them up when scanning. */
  bg: string;
  text: string;
  tint: string;
}

export const VERDICT_META: Record<Verdict, VerdictMeta> = {
  effect: {
    label: "Made a difference",
    short: "a difference in one direction or the other",
    description: "The groups ended up different. Either the 95% interval excludes zero and the estimate reaches your meaningful-effect threshold, or a controlled study states a significant result without usable numbers, in which case its size is unverified. A difference is not a benefit: which arm it favoured is counted separately, and the bar answers favouring the intervention and favouring the comparator are both made of this bucket.",
    bg: "bg-v-effect",
    text: "text-v-effect",
    tint: "bg-[color-mix(in_srgb,var(--v-effect)_12%,transparent)]",
  },
  credible_null: {
    label: "Made no difference (confirmed)",
    short: "confirmed by an interval",
    description: "The whole 95% interval sits inside your meaningful-effect threshold, so any effect is too small to matter. This can include a statistically significant effect that is smaller than the threshold.",
    bg: "bg-v-null",
    text: "text-v-null",
    tint: "bg-[color-mix(in_srgb,var(--v-null)_12%,transparent)]",
  },
  reported_null: {
    label: "Made no difference (claimed)",
    short: "claimed, not confirmed",
    description: "A controlled study says it found no significant difference, in a quoted statement or in the wording of its abstract, but no comparable interval shows the effect is too small to matter. Not evidence of equivalence.",
    bg: "bg-v-reported-null",
    text: "text-v-reported-null",
    tint: "bg-[color-mix(in_srgb,var(--v-reported-null)_12%,transparent)]",
  },
  inconclusive: {
    label: "Inconclusive",
    short: "inconclusive",
    description: "Not a finding. The interval was too wide to tell no effect from a meaningful one, the primary results conflicted, the result was on a different scale from your threshold, or no comparative result could be read.",
    bg: "bg-v-inconclusive",
    text: "text-v-inconclusive",
    tint: "bg-[color-mix(in_srgb,var(--v-inconclusive)_14%,transparent)]",
  },
  failed: {
    label: "Stopped or flawed",
    short: "stopped or flawed",
    description: "A retracted publication, a trial the registry lists as terminated, withdrawn or suspended, enrollment under half of what was planned, or a study with no control arm. These override any result the study reported.",
    bg: "bg-v-stopped",
    text: "text-v-stopped",
    tint: "bg-[color-mix(in_srgb,var(--v-stopped)_12%,transparent)]",
  },
  unreported: {
    label: "Never reported",
    short: "never reported",
    description: "A registered trial whose primary completion was over 12 months ago, with no results posted on the registry and no linked publication. The outcome is unknown, not null.",
    bg: "bg-v-unreported",
    text: "text-v-unreported",
    tint: "bg-[color-mix(in_srgb,var(--v-unreported)_14%,transparent)]",
  },
};

export function countByVerdict<T extends { verdict: Verdict }>(items: T[]): Record<Verdict, number> {
  const counts = Object.fromEntries(VERDICT_ORDER.map((v) => [v, 0])) as Record<Verdict, number>;
  for (const item of items) counts[item.verdict] += 1;
  return counts;
}
