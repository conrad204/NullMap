import type { Paper, Verdict } from "../types";

export type SortKey = "answer" | "relevance" | "newest" | "oldest" | "sample" | "citations";

export const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "answer", label: "Answer" },
  { key: "relevance", label: "Relevance" },
  { key: "newest", label: "Newest first" },
  { key: "oldest", label: "Oldest first" },
  { key: "sample", label: "Largest sample" },
  { key: "citations", label: "Most cited" },
];

/** The bar's reading order: findings first, then the studies that never answered. */
const ANSWER_RANK: Record<Verdict, number> = { effect: 0, credible_null: 1, reported_null: 2, failed: 3, unreported: 4, inconclusive: 5 };

type SortablePaper = Pick<Paper, "verdict" | "year" | "sampleSize" | "citations">;

/** A missing value sorts after every known one, whichever way the order runs. */
function byNumber<T>(value: (item: T) => number | null | undefined, descending: boolean) {
  return (a: T, b: T) => {
    const x = value(a) ?? null, y = value(b) ?? null;
    if (x === null || y === null) return x === y ? 0 : x === null ? 1 : -1;
    return descending ? y - x : x - y;
  };
}

const COMPARATORS: Record<SortKey, (a: SortablePaper, b: SortablePaper) => number> = {
  answer: (a, b) => ANSWER_RANK[a.verdict] - ANSWER_RANK[b.verdict],
  relevance: () => 0,
  // A year of 0 is the API's "unknown", not a date.
  newest: byNumber((paper) => paper.year || null, true),
  oldest: byNumber((paper) => paper.year || null, false),
  sample: byNumber((paper) => paper.sampleSize, true),
  citations: byNumber((paper) => paper.citations, true),
};

/**
 * Inconclusive is not a finding, so it never outranks one: whatever the order, those studies come
 * last. Ties keep the order the API returned, which is relevance.
 */
export function sortPapers<T extends SortablePaper>(papers: T[], key: SortKey): T[] {
  const compare = COMPARATORS[key];
  const last = (paper: T) => Number(paper.verdict === "inconclusive");
  return papers
    .map((paper, index) => ({ paper, index }))
    .sort((a, b) => last(a.paper) - last(b.paper) || compare(a.paper, b.paper) || a.index - b.index)
    .map(({ paper }) => paper);
}
