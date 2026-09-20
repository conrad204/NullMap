/**
 * British-spelling guard. Zero dependency, no network, no filesystem writes.
 *
 * `britishWords` reads a chunk of source and returns every token that is a British
 * spelling of a word American English writes differently. Tokens are split on
 * camelCase and non-letters, so `favoursIntervention`, `normalise_all` and prose all
 * go through the same rules. Rules are curated rather than generated: -ise/-isation
 * applies only to listed verb stems (so `precise`, `wise`, `analysis` and `analyses`
 * are never flagged), and the doubled -lled/-lling rule only to listed stems (so
 * `controlled` and `compelled`, correct in both dialects, are left alone).
 */

/** our -> or. Matched inside a token, so `unfavourable` and `neighbourhood` count. */
const OUR_STEMS = [
  "armour", "behaviour", "colour", "endeavour", "favour", "flavour", "harbour", "honour",
  "humour", "labour", "neighbour", "odour", "rumour", "savour", "splendour", "tumour",
  "valour", "vapour", "vigour",
];

/** -ise/-ised/-ises/-ising/-isation -> -ize/... Only these stems; the suffix is the rule. */
const ISE_STEMS = [
  "apolog", "author", "categor", "central", "character", "custom", "decentral", "digit",
  "emphas", "final", "formal", "general", "harmon", "hospital", "industrial", "initial",
  "legal", "local", "margin", "maxim", "memor", "minim", "mobil", "modern", "monet",
  "neutral", "normal", "optim", "organ", "penal", "priorit", "random", "rational", "real",
  "recogn", "revital", "serial", "special", "stabil", "standard", "summar", "symbol",
  "synthes", "util", "visual",
];

/** Single-l stems American English doubles nothing extra on: -lled/-lling/-ller/-llous. */
const DOUBLE_L_STEMS = [
  "cancel", "channel", "counsel", "dial", "duel", "equal", "fuel", "funnel", "jewel",
  "label", "level", "marvel", "model", "quarrel", "signal", "total", "travel",
];

/** Everything else, as whole words. */
const EXACT = new Map(Object.entries({
  analyse: "analyze", analysed: "analyzed", analysing: "analyzing", analyser: "analyzer",
  catalyse: "catalyze", catalysed: "catalyzed", paralyse: "paralyze", paralysed: "paralyzed",
  centre: "center", centres: "centers", centred: "centered", centring: "centering",
  judgement: "judgment", judgements: "judgments",
  catalogue: "catalog", catalogues: "catalogs", catalogued: "cataloged",
  programme: "program", programmes: "programs",
  defence: "defense", defences: "defenses", offence: "offense", offences: "offenses",
  licence: "license", licences: "licenses", practise: "practice", practised: "practiced",
  practising: "practicing",
  artefact: "artifact", artefacts: "artifacts",
  grey: "gray", greyed: "grayed", greyscale: "grayscale",
  sceptic: "skeptic", sceptical: "skeptical", scepticism: "skepticism",
  whilst: "while", amongst: "among", learnt: "learned", spelt: "spelled",
  fulfil: "fulfill", fulfils: "fulfills", fulfilment: "fulfillment",
  enrolment: "enrollment", enrolments: "enrollments", instalment: "installment",
  skilful: "skillful", wilful: "willful",
  metre: "meter", metres: "meters", litre: "liter", litres: "liters",
  fibre: "fiber", fibres: "fibers", calibre: "caliber", manoeuvre: "maneuver",
  aluminium: "aluminum", ageing: "aging", storey: "story",
  haemoglobin: "hemoglobin", haemodialysis: "hemodialysis", haemorrhage: "hemorrhage",
  anaemia: "anemia", anaemic: "anemic", oedema: "edema", foetal: "fetal",
  paediatric: "pediatric", paediatrics: "pediatrics", orthopaedic: "orthopedic",
  randomised: "randomized", standardised: "standardized",
}));

const ISE_SUFFIX = /(?:ise|ised|ises|ising|isation|isations)$/;
const DOUBLE_L_SUFFIX = /(?:led|ling|ler|lers|lous|lor|lors)$/;

/** A stem without the prefix a rule does not care about, so `unrecognised` reaches `recogn`. */
const strip = (stem) => stem.replace(/^(?:un|re|de|dis|mis|over|under|non)/, "");

/**
 * Wire-contract and stored-data spellings. These are values in Elasticsearch documents,
 * LLM prompts and the JSON the API returns, so they are spelled the way they are indexed;
 * only the labels shown for them are American. Renaming one means migrating the mapping,
 * the pipeline, `src/types.ts` and every already-indexed document together.
 */
export const PROTECTED_LITERALS = [
  "favours_intervention", "favours_comparator", "favours_effect", "favours_null",
  "favoursIntervention", "favoursComparator", "pFavours",
];

const PROTECTED_RE = new RegExp(PROTECTED_LITERALS.join("|"), "g");

/** The text with every protected literal removed, so only prose around it is checked. */
export const withoutProtected = (text) => text.replace(PROTECTED_RE, " ");

/** camelCase and snake_case split into their words, lowercased. */
const tokenize = (text) =>
  text
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .split(/[^A-Za-z]+/)
    .filter(Boolean)
    .map((word) => word.toLowerCase());

/** The American spelling of a British token, or null when the token is already fine. */
export function american(token) {
  const exact = EXACT.get(token);
  if (exact) return exact;
  const our = OUR_STEMS.find((stem) => token.includes(stem));
  if (our) return token.replace(our, our.replace("our", "or"));
  const ise = ISE_SUFFIX.exec(token);
  if (ise) {
    const stem = token.slice(0, -ise[0].length);
    if (ISE_STEMS.includes(stem) || ISE_STEMS.includes(strip(stem))) {
      return stem + ise[0].replace("is", "iz");
    }
  }
  const doubled = DOUBLE_L_SUFFIX.exec(token);
  if (doubled) {
    const stem = token.slice(0, -doubled[0].length);
    if (DOUBLE_L_STEMS.includes(stem) || DOUBLE_L_STEMS.includes(strip(stem))) {
      return stem + doubled[0].slice(1);
    }
  }
  return null;
}

/**
 * Every offending token in `text`, once per line, as `{ line, token, suggestion }`.
 * `allowed` holds tokens this file is permitted to keep spelled the British way.
 */
export function britishWords(text, allowed = new Set()) {
  const found = [];
  text.split("\n").forEach((line, index) => {
    for (const token of new Set(tokenize(line))) {
      if (allowed.has(token)) continue;
      const suggestion = american(token);
      if (suggestion) found.push({ line: index + 1, token, suggestion });
    }
  });
  return found;
}
