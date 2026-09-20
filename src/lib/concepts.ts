/**
 * Concept tags: the reading of one typed line, with no React in sight.
 *
 * There is one box and one search. The line the user types is the research
 * question, and any signed terms trailing it are tags:
 *
 *   Does renal artery stenting improve kidney function? +blood pressure −stroke
 *
 * A tag is a concept the search should move towards (positive) or away from
 * (negative). They ride along on that same literature search: the backend adds
 * and subtracts their embeddings from the question's own vector, the way
 * "king − man + woman" lands near queen, so they reorder the question's matches
 * and never decide which papers match. The typed line is the only source of
 * truth: chips are a reading of it, and editing a chip rewrites the line.
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

/**
 * A tag begins where a sign begins a term: the sign opens the term (start of
 * the line or after a space) and the term follows it immediately. That single
 * rule is what keeps prose prose — "renal-artery" has no space before its
 * hyphen and "a - b" has one after it, so neither is a tag.
 */
const TAG_START = /(^|\s)([+\-−–])(?=[\p{L}\p{N}("'“])/gu;

/** Tags are typed inside a sentence, so they arrive wearing its punctuation. */
function cleanTerm(raw: string): string {
  return raw
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^[(["'“‘]+/, "")
    .replace(/[)\]"'”’.,;:!?]+$/, "")
    .trim();
}

/**
 * Read one typed line as the question plus its tags. Everything before the
 * first signed term is the question; each signed term after it runs until the
 * next one. Tags never shorten the question the search runs: they only aim it.
 */
export function parseLine(raw: string): { question: string; concepts: Concept[] } {
  const starts = [...raw.matchAll(TAG_START)];
  if (!starts.length) return { question: raw.trim(), concepts: [] };
  let concepts: Concept[] = [];
  starts.forEach((start, index) => {
    const from = start.index + start[1].length + start[2].length;
    const to = index + 1 < starts.length ? starts[index + 1].index : raw.length;
    const text = cleanTerm(raw.slice(from, to));
    if (text) concepts = addConcepts(concepts, text, start[2] === "+" ? "positive" : "negative");
  });
  return { question: raw.slice(0, starts[0].index).trim(), concepts };
}

/** Where a tag sits in the typed line, sign included, as `[start, end)`. */
export interface TagRange {
  start: number;
  end: number;
  sign: ConceptSign;
}

const OPENING = new Set(["(", "[", '"', "'", "\u201c", "\u2018"]);
const CLOSING = new Set([")", "]", '"', "'", "\u201d", "\u2019", ".", ",", ";", ":", "!", "?"]);

/**
 * The same reading as `parseLine`, as character ranges, so anything drawing the
 * line can colour exactly the text that will be sent as a tag.
 */
export function tagRanges(raw: string): TagRange[] {
  const starts = [...raw.matchAll(TAG_START)];
  const ranges: TagRange[] = [];
  starts.forEach((start, index) => {
    const signAt = start.index + start[1].length;
    const from = signAt + start[2].length;
    const to = index + 1 < starts.length ? starts[index + 1].index : raw.length;
    let first = from;
    let last = to;
    while (first < last && (/\s/.test(raw[first]) || OPENING.has(raw[first]))) first += 1;
    while (last > first && (/\s/.test(raw[last - 1]) || CLOSING.has(raw[last - 1]))) last -= 1;
    if (last > first) {
      ranges.push({ start: signAt, end: last, sign: start[2] === "+" ? "positive" : "negative" });
    }
  });
  return ranges;
}

/** The line split into prose and tag runs, in order, covering every character. */
export function tagSegments(raw: string): { text: string; sign: ConceptSign | null }[] {
  const segments: { text: string; sign: ConceptSign | null }[] = [];
  let at = 0;
  for (const range of tagRanges(raw)) {
    if (range.start > at) segments.push({ text: raw.slice(at, range.start), sign: null });
    segments.push({ text: raw.slice(range.start, range.end), sign: range.sign });
    at = range.end;
  }
  if (at < raw.length) segments.push({ text: raw.slice(at), sign: null });
  return segments;
}

/** The line that reads back as exactly this question and these tags. */
export function composeLine(question: string, concepts: Concept[]): string {
  const tags = concepts.map((concept) => `${SIGN_META[concept.sign].symbol}${concept.text}`);
  return [question.trim(), ...tags].filter(Boolean).join(" ");
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
