/**
 * Concept search: the state and the reading of it, with no React in sight.
 *
 * A query is a list of concepts, each either positive ("papers should be about
 * this") or negative ("steer away from this"). The service embeds each one and
 * searches the index near `sum(positive) − sum(negative)`, which is the
 * document-level form of "king − man + woman". Everything here is pure so the
 * same query can be built by a form today and by another feature later.
 */
import type { Concept, ConceptMatch, ConceptSearchRequest, ConceptSign } from "../types";

export const MAX_PER_SIGN = 8;
export const MIN_CONCEPT_LENGTH = 2;
export const MAX_CONCEPT_LENGTH = 200;
export const DEFAULT_LIMIT = 20;

export const SIGN_META: Record<ConceptSign, {
  /** Field label. The word "concept" is deliberate: these are not keywords, nothing is matched literally. */
  label: string;
  symbol: "+" | "−";
  hint: string;
  placeholder: string;
  /** Full class names so Tailwind picks them up when scanning. */
  text: string;
  bg: string;
  tint: string;
  border: string;
}> = {
  positive: {
    label: "Positive concepts",
    symbol: "+",
    hint: "Move towards work about these. At least one is needed.",
    placeholder: "chronic kidney disease",
    text: "text-v-effect",
    bg: "bg-v-effect",
    tint: "bg-[color-mix(in_srgb,var(--v-effect)_12%,transparent)]",
    border: "border-[color-mix(in_srgb,var(--v-effect)_45%,var(--line))]",
  },
  negative: {
    label: "Negative concepts",
    symbol: "−",
    hint: "Move away from work about these. Optional.",
    placeholder: "diabetes",
    text: "text-v-failed",
    bg: "bg-v-failed",
    tint: "bg-[color-mix(in_srgb,var(--v-failed)_12%,transparent)]",
    border: "border-[color-mix(in_srgb,var(--v-failed)_45%,var(--line))]",
  },
};

export const SIGNS: ConceptSign[] = ["positive", "negative"];

const key = (text: string) => text.trim().toLowerCase();

/** One typed line can hold several concepts; commas and newlines separate them. */
export function parseConceptInput(raw: string): string[] {
  return raw
    .split(/[,\n]/)
    .map((term) => term.trim().replace(/\s+/g, " "))
    .filter(Boolean);
}

const SIGN_PREFIX = /^([+\-−–])\s*/;

/**
 * A typed term carries its own sign when it starts with + or −, so one field can
 * hold both sides of the arithmetic: "SGLT2, −diabetes".
 */
export function parseSignedInput(raw: string, fallback: ConceptSign): { text: string; sign: ConceptSign }[] {
  const signed: { text: string; sign: ConceptSign }[] = [];
  for (const term of parseConceptInput(raw)) {
    const prefix = SIGN_PREFIX.exec(term);
    const text = prefix ? term.slice(prefix[0].length).trim() : term;
    if (!text) continue;
    signed.push({ text, sign: prefix && prefix[1] !== "+" ? "negative" : prefix ? "positive" : fallback });
  }
  return signed;
}

/**
 * Whether what is being typed is concept arithmetic rather than a question.
 * Signing a term is the only way into the arithmetic, so one box can hold both.
 */
export function isSignedTerm(raw: string): boolean {
  return raw.split(/[,\n]/).some((term) => {
    const trimmed = term.trim();
    const prefix = SIGN_PREFIX.exec(trimmed);
    if (!prefix) return false;
    return trimmed.slice(prefix[0].length).trim().length >= MIN_CONCEPT_LENGTH;
  });
}

export function addSignedConcepts(concepts: Concept[], raw: string, fallback: ConceptSign): Concept[] {
  let next = concepts;
  for (const { text, sign } of parseSignedInput(raw, fallback)) next = addConcepts(next, text, sign);
  return next;
}

export function bySign(concepts: Concept[], sign: ConceptSign): Concept[] {
  return concepts.filter((concept) => concept.sign === sign);
}

/**
 * Add whatever the user typed, keeping the list a set per sign. The same term
 * on both sides is allowed to cancel out: the arithmetic, not this function,
 * decides what that means, and the empty-direction case is reported honestly.
 */
export function addConcepts(concepts: Concept[], raw: string, sign: ConceptSign): Concept[] {
  const taken = new Set(bySign(concepts, sign).map((concept) => key(concept.text)));
  const added: Concept[] = [];
  for (const text of parseConceptInput(raw)) {
    if (taken.has(key(text)) || text.length > MAX_CONCEPT_LENGTH) continue;
    taken.add(key(text));
    added.push({ id: `${sign}:${key(text)}`, text, sign });
  }
  return added.length ? [...concepts, ...added] : concepts;
}

export function removeConcept(concepts: Concept[], id: string): Concept[] {
  return concepts.filter((concept) => concept.id !== id);
}

/** Flip a concept between the two sides in place, so a mistake costs one click. */
export function flipConcept(concepts: Concept[], id: string): Concept[] {
  const found = concepts.find((concept) => concept.id === id);
  if (!found) return concepts;
  const sign: ConceptSign = found.sign === "positive" ? "negative" : "positive";
  if (bySign(concepts, sign).some((concept) => key(concept.text) === key(found.text))) {
    return removeConcept(concepts, id);
  }
  return concepts.map((concept) =>
    concept.id === id ? { ...concept, id: `${sign}:${key(concept.text)}`, sign } : concept,
  );
}

/** "kidney disease + SGLT2 inhibitor − diabetes", the expression as arithmetic. */
export function expression(concepts: Concept[]): string {
  const positive = bySign(concepts, "positive");
  const negative = bySign(concepts, "negative");
  const head = positive.map((concept) => concept.text).join(" + ");
  const tail = negative.map((concept) => ` − ${concept.text}`).join("");
  return `${head}${tail}`;
}

/** Why a query cannot run yet, in the words the form should show. */
export function conceptError(concepts: Concept[]): string | null {
  const positive = bySign(concepts, "positive");
  if (!positive.length) return "Add at least one positive concept — the search needs somewhere to start.";
  for (const sign of SIGNS) {
    if (bySign(concepts, sign).length > MAX_PER_SIGN) {
      return `Use at most ${MAX_PER_SIGN} ${sign} concepts; beyond that the combined direction means very little.`;
    }
  }
  const short = concepts.find((concept) => concept.text.length < MIN_CONCEPT_LENGTH);
  if (short) return `"${short.text}" is too short to embed. Name the concept in a word or two.`;
  return null;
}

export function toRequest(concepts: Concept[], limit: number = DEFAULT_LIMIT): ConceptSearchRequest {
  return {
    positive: bySign(concepts, "positive").map((concept) => concept.text),
    negative: bySign(concepts, "negative").map((concept) => concept.text),
    limit,
  };
}

export interface MatchSignals {
  /** Closest positive concept: what pulled this paper in. */
  nearestPositive: ConceptMatch["concepts"][number] | null;
  /** Closest negative concept: what it was supposed to move away from. */
  nearestNegative: ConceptMatch["concepts"][number] | null;
  /**
   * True when a negative concept is closer to the paper than the positive that
   * pulled it in. The paper still matches the combined direction, so it is shown
   * — flagged rather than dropped, because the arithmetic, not the label, is the
   * claim being made.
   */
  contested: boolean;
}

/** Read one result: what pulled it in, what should have pushed it away. */
export function matchSignals(match: ConceptMatch): MatchSignals {
  const strongest = (sign: ConceptSign) =>
    match.concepts
      .filter((concept) => concept.sign === sign)
      .reduce<ConceptMatch["concepts"][number] | null>(
        (best, concept) => (best === null || concept.cosine > best.cosine ? concept : best),
        null,
      );
  const nearestPositive = strongest("positive");
  const nearestNegative = strongest("negative");
  return {
    nearestPositive,
    nearestNegative,
    contested:
      nearestPositive !== null &&
      nearestNegative !== null &&
      nearestNegative.cosine >= nearestPositive.cosine,
  };
}

/**
 * Cosines on this corpus live in a narrow band, so a raw 0.31 reads as "no
 * match" when it is a strong one. This scales a cosine to a bar width only; the
 * number beside it stays the measured cosine.
 */
export function cosineWidth(cosine: number): number {
  return Math.max(0, Math.min(1, (cosine - 0.05) / 0.55));
}
