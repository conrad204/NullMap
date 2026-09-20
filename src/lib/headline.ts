import type { Verdict } from "../types";

export interface Headline {
  /** Has this been tested before? */
  title: string;
  /** What was found, in one or two sentences. */
  detail: string;
}

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`;

/**
 * The plain answer to "has this been tried, and what happened?", from the same
 * counts as the verdict bar. `provisional` is true when the reported effects rest on
 * abstract wording, not numbers, so the headline never claims more than the rows do.
 */
export function headline(
  counts: Record<Verdict, number>,
  provisional: boolean,
  /** Read studies with a comparison group; omitted when the API does not report it. */
  controlled?: number,
): Headline {
  const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
  if (total === 0) {
    return {
      title: "No prior studies found",
      detail: "Nothing in this index matches your question. That can mean the idea is new, or that the index does not cover it yet; it is not evidence either way.",
    };
  }
  const nulls = counts.credible_null + counts.reported_null;
  const reported = counts.effect + nulls;
  const silent = [
    counts.unreported && `${plural(counts.unreported, "completed trial", "completed trials")} never reported results`,
    counts.failed && `${counts.failed} failed or stopped early`,
    counts.inconclusive && `${counts.inconclusive} had no clear result`,
  ].filter(Boolean).join(", ");
  if (reported === 0 && controlled === 0) {
    // Matching records are not tests: with no comparison group among them, nothing tried this.
    return {
      title: "Related work exists, but nothing tested this directly",
      detail: `${plural(total, "matching study", "matching studies")}, none a controlled comparison of the intervention: ${silent}. Observational or single-arm work can motivate a trial; it does not answer the question.`,
    };
  }
  if (reported === 0) {
    return {
      title: "This has been tried before, but no results are available",
      detail: `${plural(total, "matching study", "matching studies")}, none with a readable result: ${silent}.`,
    };
  }
  const caveat = provisional ? " These are claims read from abstracts; their size is unverified." : "";
  const found =
    nulls === 0 ? `${counts.effect === 1 ? "It reports" : `All ${counts.effect} report`} an effect.${caveat}`
    : counts.effect === 0 ? `${nulls === 1 ? "It found" : `All ${nulls} found`} no significant difference${counts.credible_null === nulls ? ", with intervals tight enough to rule out a meaningful effect" : ""}.`
    : `Results are split: ${plural(counts.effect, "reports", "report")} an effect, ${plural(nulls, "reports", "report")} no difference.${caveat}`;
  return {
    title: "This has been tested before",
    detail: `${reported} of ${plural(total, "matching study reports", "matching studies report")} a result. ${found}${silent ? ` Of the rest, ${silent}.` : ""}`,
  };
}
