import type { InconclusiveReason, Recommendation, Verdict } from "../types";

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
  description: string;
  /** Full class names so Tailwind can pick them up when scanning. */
  bg: string;
  text: string;
  tint: string;
}

export const VERDICT_META: Record<Verdict, VerdictMeta> = {
  effect: {
    label: "Meaningful effect",
    description: "An interval excluding zero and an effect reaching the selected meaningful-effect threshold.",
    bg: "bg-v-effect",
    text: "text-v-effect",
    tint: "bg-[color-mix(in_srgb,var(--v-effect)_12%,transparent)]",
  },
  credible_null: {
    label: "Credible null",
    description: "A confidence interval entirely within the selected equivalence bounds.",
    bg: "bg-v-null",
    text: "text-v-null",
    tint: "bg-[color-mix(in_srgb,var(--v-null)_12%,transparent)]",
  },
  reported_null: {
    label: "Reported null · unverified",
    description: "The abstract reports no significant difference, but no interval shows the effect is too small to matter. Not evidence of equivalence.",
    bg: "bg-v-reported-null",
    text: "text-v-reported-null",
    tint: "bg-[color-mix(in_srgb,var(--v-reported-null)_12%,transparent)]",
  },
  inconclusive: {
    label: "Inconclusive",
    description: "Neither an effect nor a null could be established, because the result was too imprecise or could not be read.",
    bg: "bg-v-inconclusive",
    text: "text-v-inconclusive",
    tint: "bg-[color-mix(in_srgb,var(--v-inconclusive)_14%,transparent)]",
  },
  failed: {
    label: "Failed methodologically",
    description: "A stopped trial, retraction, inadequate enrollment, or identified design failure.",
    bg: "bg-v-failed",
    text: "text-v-failed",
    tint: "bg-[color-mix(in_srgb,var(--v-failed)_12%,transparent)]",
  },
  unreported: {
    label: "Never reported",
    description: "Completed over 12 months ago, with no posted results or linked result publication. Outcomes remain unknown.",
    bg: "bg-v-unreported",
    text: "text-v-unreported",
    tint: "bg-[color-mix(in_srgb,var(--v-unreported)_14%,transparent)]",
  },
};

export const RECOMMENDATION_META: Record<
  Recommendation,
  { label: string; text: string; tint: string }
> = {
  pursue: {
    label: "Worth pursuing",
    text: "text-v-effect",
    tint: "bg-[color-mix(in_srgb,var(--v-effect)_12%,transparent)]",
  },
  pursue_with_changes: {
    label: "Pursue with changes",
    text: "text-v-unreported",
    tint: "bg-[color-mix(in_srgb,var(--v-unreported)_14%,transparent)]",
  },
  deprioritize: {
    label: "Deprioritize",
    text: "text-v-failed",
    tint: "bg-[color-mix(in_srgb,var(--v-failed)_12%,transparent)]",
  },
};

export function countByVerdict<T extends { verdict: Verdict }>(items: T[]): Record<Verdict, number> {
  const counts = Object.fromEntries(VERDICT_ORDER.map((v) => [v, 0])) as Record<Verdict, number>;
  for (const item of items) counts[item.verdict] += 1;
  return counts;
}
