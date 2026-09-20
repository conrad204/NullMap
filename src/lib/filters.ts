import type { SearchFilters } from "../types";

/** The composer's raw inputs. An empty string means "no bound", not zero. */
export interface FilterDraft {
  yearFrom: string;
  yearTo: string;
  minCitations: string;
  maxCitations: string;
}
export interface FilterState {
  draft: FilterDraft;
  /** When on, the values carry over to the next search instead of clearing. */
  sticky: boolean;
}

export const EMPTY_DRAFT: FilterDraft = { yearFrom: "", yearTo: "", minCitations: "", maxCitations: "" };
export const EMPTY_STATE: FilterState = { draft: EMPTY_DRAFT, sticky: false };
const FIELDS = Object.keys(EMPTY_DRAFT) as (keyof FilterDraft)[];
export const YEAR_RANGE = { min: 1500, max: 2100 };
const STORAGE_KEY = "nullmap-filters";

function bound(value: string): number | undefined {
  const text = value.trim();
  if (!text) return undefined;
  const parsed = Number(text);
  return Number.isFinite(parsed) ? Math.trunc(parsed) : undefined;
}

/** Undefined when nothing is bounded, so an untouched form never sends a filter. */
export function parseFilters(draft: FilterDraft): SearchFilters | undefined {
  const filters: SearchFilters = {};
  for (const field of FIELDS) {
    const value = bound(draft[field]);
    if (value !== undefined) filters[field] = value;
  }
  return Object.keys(filters).length > 0 ? filters : undefined;
}

/** What the user must fix before searching, or null. Mirrors the API's own validator. */
export function filterError(draft: FilterDraft): string | null {
  const { yearFrom, yearTo, minCitations, maxCitations } = parseFilters(draft) ?? {};
  for (const year of [yearFrom, yearTo]) {
    if (year !== undefined && (year < YEAR_RANGE.min || year > YEAR_RANGE.max)) {
      return `Publication years must be between ${YEAR_RANGE.min} and ${YEAR_RANGE.max}.`;
    }
  }
  if (yearFrom !== undefined && yearTo !== undefined && yearFrom > yearTo) {
    return "The earliest publication year must not be later than the latest.";
  }
  for (const count of [minCitations, maxCitations]) {
    if (count !== undefined && count < 0) return "Citation counts cannot be negative.";
  }
  if (minCitations !== undefined && maxCitations !== undefined && minCitations > maxCitations) {
    return "The minimum citation count must not exceed the maximum.";
  }
  return null;
}

/** Short labels for the chips shown beside the question and in the report. */
export function describeFilters(filters: SearchFilters | undefined): string[] {
  if (!filters) return [];
  const { yearFrom, yearTo, minCitations, maxCitations } = filters;
  const labels: string[] = [];
  if (yearFrom !== undefined && yearTo !== undefined) labels.push(`Published ${yearFrom}\u2013${yearTo}`);
  else if (yearFrom !== undefined) labels.push(`Published ${yearFrom} or later`);
  else if (yearTo !== undefined) labels.push(`Published ${yearTo} or earlier`);
  if (minCitations !== undefined && maxCitations !== undefined) labels.push(`${minCitations}\u2013${maxCitations} citations`);
  else if (minCitations !== undefined) labels.push(`\u2265 ${minCitations} citations`);
  else if (maxCitations !== undefined) labels.push(`\u2264 ${maxCitations} citations`);
  return labels;
}

/** What the form holds once a search is under way: sticky values stay, the rest clear. */
export function afterSearch(state: FilterState): FilterState {
  return state.sticky ? state : { ...state, draft: EMPTY_DRAFT };
}

function isDraft(value: unknown): value is FilterDraft {
  return typeof value === "object" && value !== null
    && FIELDS.every((field) => typeof (value as Record<string, unknown>)[field] === "string");
}

/** Stored state, ignoring anything that is not the shape this version writes. */
export function loadFilterState(): FilterState {
  try {
    const stored: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    if (typeof stored !== "object" || stored === null) return EMPTY_STATE;
    const { draft, sticky } = stored as { draft?: unknown; sticky?: unknown };
    return { draft: isDraft(draft) ? draft : EMPTY_DRAFT, sticky: sticky === true };
  } catch {
    // Unreadable or refused storage: this session simply starts unfiltered.
    return EMPTY_STATE;
  }
}

export function saveFilterState(state: FilterState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Private windows can refuse storage; the choice then lasts for this page only.
  }
}
