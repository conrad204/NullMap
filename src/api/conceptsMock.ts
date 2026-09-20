/** Entirely fictional concept-search fixtures, reachable only when VITE_USE_MOCK=true. */
import type { ConceptMatch, ConceptSearchRequest, ConceptSearchResult } from "../types";

const VERDICTS = ["effect", "credible_null", "reported_null", "unreported", "inconclusive"] as const;

/** Deterministic pseudo-cosines, so the demo is stable and obviously synthetic. */
function cosine(seed: number, offset: number): number {
  return Math.round((0.18 + ((seed * 37 + offset * 11) % 40) / 100) * 100) / 100;
}

export async function mockConceptSearch(
  req: ConceptSearchRequest,
  signal?: AbortSignal,
): Promise<ConceptSearchResult> {
  await new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("Aborted", "AbortError"));
    const abort = () => { clearTimeout(timer); reject(new DOMException("Aborted", "AbortError")); };
    const timer = setTimeout(() => { signal?.removeEventListener("abort", abort); resolve(null); }, 350);
    signal?.addEventListener("abort", abort, { once: true });
  });
  const concepts = [
    ...req.positive.map((text) => ({ text, sign: "positive" as const })),
    ...req.negative.map((text) => ({ text, sign: "negative" as const })),
  ];
  // The real service rejects a combination that cancels to a zero vector; the demo must not
  // invent results for a query the backend would refuse.
  const same = (a: string[], b: string[]) => {
    const norm = (terms: string[]) => [...terms].map((term) => term.toLowerCase()).sort().join("|");
    return a.length > 0 && norm(a) === norm(b);
  };
  if (same(req.positive, req.negative)) {
    throw new Error("The concepts cancel each other out, so the search has no direction.");
  }
  const matches: ConceptMatch[] = Array.from({ length: Math.min(req.limit ?? 8, 8) }, (_, index) => ({
    id: `demo_concept_${index}`,
    title: `Illustrative paper ${index + 1} near ${req.positive[0] ?? "the combined direction"}`,
    year: 2024 - index,
    url: "",
    source: "openalex" as const,
    verdict: VERDICTS[index % VERDICTS.length],
    citations: (8 - index) * 3,
    cosine: Math.round((0.62 - index * 0.04) * 100) / 100,
    concepts: concepts.map((concept, position) => ({
      ...concept,
      cosine: cosine(index, position + (concept.sign === "negative" ? 7 : 0)),
    })),
  }));
  return {
    version: "concepts-demo",
    concepts,
    matches,
    warnings: [
      "Illustrative demo: these papers and cosines are fictional fixtures and describe no real literature.",
    ],
  };
}
