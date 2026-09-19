import type { Recommendation, Verdict } from "../types";

export const VERDICT_ORDER: Verdict[] = [
  "effect",
  "credible_null",
  "inconclusive",
  "failed",
  "unreported",
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
    description: "Reported an effect the authors considered meaningful, with adequate power.",
    bg: "bg-v-effect",
    text: "text-v-effect",
    tint: "bg-[color-mix(in_srgb,var(--v-effect)_12%,transparent)]",
  },
  credible_null: {
    label: "Credible null",
    description: "Well-powered and found no meaningful effect. This is the result the file drawer hides.",
    bg: "bg-v-null",
    text: "text-v-null",
    tint: "bg-[color-mix(in_srgb,var(--v-null)_12%,transparent)]",
  },
  inconclusive: {
    label: "Inconclusive",
    description: "Underpowered or mixed. Neither supports nor rules out the effect.",
    bg: "bg-v-inconclusive",
    text: "text-v-inconclusive",
    tint: "bg-[color-mix(in_srgb,var(--v-inconclusive)_14%,transparent)]",
  },
  failed: {
    label: "Failed methodologically",
    description: "Completed, but attrition, blinding, or measurement problems undermine the result.",
    bg: "bg-v-failed",
    text: "text-v-failed",
    tint: "bg-[color-mix(in_srgb,var(--v-failed)_12%,transparent)]",
  },
  unreported: {
    label: "Never reported",
    description: "Registered or funded, but results were never published.",
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
