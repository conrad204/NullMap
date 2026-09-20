import type { InconclusiveReason, Verdict } from "../types";

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

export type BarGroupKey = "difference" | "no_difference" | "no_answer";

/**
 * The bar has three answers. The buckets inside a group are still counted apart in the line under
 * each answer, because "confirmed" and "claimed" nulls are different strengths of evidence, but
 * they are not separate categories: one segment, one count and one filter per answer.
 */
export const BAR_GROUPS: { key: BarGroupKey; label: string; description: string; verdicts: Verdict[]; bg: string }[] = [
  {
    key: "difference",
    label: "Made a difference",
    description: "The groups ended up different, in either direction, so this includes harm.",
    verdicts: ["effect"],
    bg: "bg-v-effect",
  },
  {
    key: "no_difference",
    label: "Made no difference",
    description: "Confirmed means the interval rules out any effect large enough to matter. Claimed means the report says no significant difference, which is not evidence of equivalence.",
    verdicts: ["credible_null", "reported_null"],
    bg: "bg-v-null",
  },
  {
    key: "no_answer",
    label: "No answer",
    description: "The study was run but never answered the question: stopped early, flawed or retracted, or completed without ever reporting results.",
    verdicts: ["failed", "unreported"],
    bg: "bg-v-unreported",
  },
];

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
    short: "In either direction, including harm",
    description: "The groups ended up different. Either the 95% interval excludes zero and the estimate reaches your meaningful-effect threshold, or a controlled study states a significant result without usable numbers, in which case its size is unverified. Either direction counts, so this includes harm.",
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
