import type { RegionLabel } from "../types";

interface RegionMeta {
  label: string;
  description: string;
  /** Full class names so Tailwind can pick them up when scanning. */
  text: string;
  tint: string;
}

/** Why nothing, or nothing new, is happening in a region. Ordered worst-news first. */
export const REGION_ORDER: RegionLabel[] = [
  "null_saturated",
  "dark",
  "contested",
  "active",
  "unread",
  "thin",
];

export const REGION_META: Record<RegionLabel, RegionMeta> = {
  null_saturated: {
    label: "Null-saturated",
    description: "Tried repeatedly, mostly reported as no difference. Absence of papers here is absence of success, not absence of interest.",
    text: "text-v-reported-null",
    tint: "bg-[color-mix(in_srgb,var(--v-reported-null)_12%,transparent)]",
  },
  dark: {
    label: "Unreported",
    description: "A third or more of the studies completed without posting results or a linked publication. What happened here is unknown, not null.",
    text: "text-v-unreported",
    tint: "bg-[color-mix(in_srgb,var(--v-unreported)_14%,transparent)]",
  },
  contested: {
    label: "Contested",
    description: "Effects and nulls are both well represented. The literature disagrees with itself.",
    text: "text-v-inconclusive",
    tint: "bg-[color-mix(in_srgb,var(--v-inconclusive)_14%,transparent)]",
  },
  active: {
    label: "Active",
    description: "Mostly reported effects, with no concentration of nulls or missing reports.",
    text: "text-v-effect",
    tint: "bg-[color-mix(in_srgb,var(--v-effect)_12%,transparent)]",
  },
  unread: {
    label: "No readable outcome",
    description: "Registrations and reports the index could not read a result from. This says what the corpus is missing, not what the studies found.",
    text: "text-ink-2",
    tint: "bg-surface-2",
  },
  thin: {
    label: "Too thin to judge",
    description: "Fewer than five primary studies in this part of the index, so its mix of outcomes means nothing yet.",
    text: "text-ink-2",
    tint: "bg-surface-2",
  },
};

/** A region is named by its most-cited members, since the index stores no topic labels. */
export function regionName(titles: string[]): string {
  const first = titles.find((title) => title.trim().length > 0);
  if (!first) return "Untitled region";
  return first.length > 70 ? `${first.slice(0, 69)}…` : first;
}
