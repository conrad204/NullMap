import type { RegionLabel } from "../types";

interface RegionMeta {
  label: string;
  description: string;
  cluster: RegionCluster;
}

interface ClusterMeta {
  label: string;
  description: string;
  /** Full class names so Tailwind can pick them up when scanning. */
  text: string;
  tint: string;
  /** The theme variable the canvas resolves at draw time. */
  colorVar: string;
}

/**
 * The reader sees three groups, not six backend labels: what the sample says was
 * tried without a payoff, what it says is already busy, and what it cannot read at
 * all. The exact label stays available beside each group as `REGION_META`.
 */
export type RegionCluster = "worth_a_look" | "crowded" | "unjudged";

/** Ordered worst-news-for-the-literature first, which is best news for a new study. */
export const CLUSTER_ORDER: RegionCluster[] = ["worth_a_look", "crowded", "unjudged"];

export const CLUSTER_META: Record<RegionCluster, ClusterMeta> = {
  worth_a_look: {
    label: "Worth a look",
    description: "Studies were run here, and the sample either reports no difference or never reports an outcome at all. Few papers here is not lack of interest.",
    text: "text-v-reported-null",
    tint: "bg-[color-mix(in_srgb,var(--v-reported-null)_12%,transparent)]",
    colorVar: "--v-reported-null",
  },
  crowded: {
    label: "Already crowded",
    description: "Plenty of reported results in the sample, either pointing the same way or disagreeing with each other.",
    text: "text-v-effect",
    tint: "bg-[color-mix(in_srgb,var(--v-effect)_12%,transparent)]",
    colorVar: "--v-effect",
  },
  unjudged: {
    label: "Nothing to judge yet",
    description: "The index either could not read a result here or holds too few studies to say anything. This describes what the sample is missing, not what the studies found.",
    text: "text-ink-2",
    tint: "bg-surface-2",
    colorVar: "--ink-3",
  },
};

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
    label: "Mostly no difference",
    description: "Tried repeatedly, mostly reported as no difference. Absence of papers here is absence of success, not absence of interest.",
    cluster: "worth_a_look",
  },
  dark: {
    label: "Never reported",
    description: "A third or more of the studies completed without posting results or a linked publication. What happened here is unknown, not null.",
    cluster: "worth_a_look",
  },
  contested: {
    label: "Results disagree",
    description: "Effects and nulls are both well represented. The literature disagrees with itself.",
    cluster: "crowded",
  },
  active: {
    label: "Mostly reported effects",
    description: "Mostly reported effects, with no concentration of nulls or missing reports.",
    cluster: "crowded",
  },
  unread: {
    label: "No result the index could read",
    description: "Registrations and reports the index could not read a result from. This says what the corpus is missing, not what the studies found.",
    cluster: "unjudged",
  },
  thin: {
    label: "Too few studies sampled",
    description: "Fewer than five primary studies in this part of the index, so its mix of outcomes means nothing yet.",
    cluster: "unjudged",
  },
};

export const clusterOf = (label: RegionLabel): RegionCluster => REGION_META[label].cluster;
export const clusterMetaOf = (label: RegionLabel): ClusterMeta => CLUSTER_META[clusterOf(label)];

/** The exact labels of a cluster that this map actually contains, for secondary detail. */
export function clusterDetail(cluster: RegionCluster, present: RegionLabel[]): string {
  return REGION_ORDER.filter((label) => REGION_META[label].cluster === cluster && present.includes(label))
    .map((label) => REGION_META[label].label.toLowerCase())
    .join(", ");
}

/** A region is named by its most-cited members, since the index stores no topic labels. */
export function regionName(titles: string[]): string {
  const first = titles.find((title) => title.trim().length > 0);
  if (!first) return "Untitled region";
  return first.length > 70 ? `${first.slice(0, 69)}…` : first;
}

/**
 * A bare cosine means nothing on its own, so it is paired with a plain band.
 * These are heuristic landmarks for MiniLM document embeddings, not calibrated
 * similarity scores — the neighbouring papers listed with it are the check.
 */
export function redundancyLabel(cosine: number): string {
  if (cosine >= 0.8) return "Essentially already published";
  if (cosine >= 0.6) return "Very close work exists";
  if (cosine >= 0.45) return "Related territory";
  return "Sparse territory";
}
