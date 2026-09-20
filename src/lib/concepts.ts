/**
 * Concept tags: the state and the reading of it, with no React in sight.
 *
 * A tag is a concept the search should move towards (positive) or away from
 * (negative). They ride along on the one literature search: the backend adds
 * and subtracts their embeddings from the question's own vector, the way
 * "king − man + woman" lands near queen, so they reorder the question's matches
 * and never decide which papers match. Everything here is pure so the same tags
 * can be built by a form today and by another feature later.
 */
import type { Concept, ConceptSign, ConceptSteer } from "../types";

export const MAX_PER_SIGN = 8;
export const MIN_CONCEPT_LENGTH = 2;
export const MAX_CONCEPT_LENGTH = 200;

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
    hint: "Rank work about these higher. Optional.",
    placeholder: "chronic kidney disease",
    text: "text-v-effect",
    bg: "bg-v-effect",
    tint: "bg-[color-mix(in_srgb,var(--v-effect)_12%,transparent)]",
    border: "border-[color-mix(in_srgb,var(--v-effect)_45%,var(--line))]",
  },
  negative: {
    label: "Negative concepts",
    symbol: "−",
    hint: "Rank work about these lower. They are never removed. Optional.",
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
 * hold both sides of the steer: "SGLT2, −diabetes".
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
 * on both sides is allowed to cancel out: the embedding arithmetic, not this
 * function, decides what that means, and the backend reports it honestly when
 * the tags cancel the question out.
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

/** "kidney disease + SGLT2 inhibitor − diabetes", the steer as arithmetic. */
export function expression(concepts: Concept[]): string {
  const positive = bySign(concepts, "positive");
  const negative = bySign(concepts, "negative");
  const head = positive.map((concept) => concept.text).join(" + ");
  const tail = negative.map((concept) => ` − ${concept.text}`).join("");
  return `${head}${tail}`;
}

/**
 * Why the tags cannot ride along yet, in the words the form should show. Tags
 * are optional, so no tags is never an error: the question alone is a search.
 */
export function conceptError(concepts: Concept[]): string | null {
  for (const sign of SIGNS) {
    if (bySign(concepts, sign).length > MAX_PER_SIGN) {
      return `Use at most ${MAX_PER_SIGN} ${sign} concepts; beyond that the combined direction means very little.`;
    }
  }
  const short = concepts.find((concept) => concept.text.length < MIN_CONCEPT_LENGTH);
  if (short) return `"${short.text}" is too short to embed. Name the concept in a word or two.`;
  return null;
}

/** The tags as they ride on the search request; null when there are none to send. */
export function toSteer(concepts: Concept[]): ConceptSteer | null {
  if (!concepts.length) return null;
  return {
    positive: bySign(concepts, "positive").map((concept) => concept.text),
    negative: bySign(concepts, "negative").map((concept) => concept.text),
  };
}

/** The steer a result came back with, as chips again; empty when nothing steered it. */
export function fromSteer(steer: ConceptSteer | null | undefined): Concept[] {
  if (!steer) return [];
  let concepts: Concept[] = [];
  for (const sign of SIGNS) for (const text of steer[sign]) concepts = addConcepts(concepts, text, sign);
  return concepts;
}
