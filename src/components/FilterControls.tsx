import { Funnel } from "@phosphor-icons/react";
import { EMPTY_DRAFT, YEAR_RANGE, describeFilters, parseFilters, type FilterDraft, type FilterState } from "../lib/filters";

interface Props {
  state: FilterState;
  onChange: (state: FilterState) => void;
  /** Validation message for the current draft, rendered next to the inputs. */
  error: string | null;
}

export default function FilterControls({ state, onChange, error }: Props) {
  const { draft, sticky } = state;
  const active = describeFilters(parseFilters(draft));
  const update = (field: keyof FilterDraft, value: string) => onChange({ ...state, draft: { ...draft, [field]: value } });

  function numberField(field: keyof FilterDraft, label: string, placeholder: string, min: number, max: number) {
    return <label key={field} className="flex flex-col gap-1.5 text-sm text-ink-2" htmlFor={field}>
      {label}
      <input id={field} name={field} type="number" inputMode="numeric" step="1" min={min} max={max} placeholder={placeholder}
        className="field font-mono tabular-nums" value={draft[field]}
        onChange={(event) => update(field, event.target.value)} />
    </label>;
  }
  return (
    <details className="border-t border-line pt-4">
      <summary className="cursor-pointer text-sm font-medium text-ink">
        Corpus filters
        <span className="ml-1 font-normal text-ink-3">{active.length > 0 ? active.join(" · ") : "searching every indexed record"}</span>
      </summary>
      <p className="mt-3 max-w-[70ch] text-xs leading-relaxed text-ink-3">Applied before the search runs, so they change which studies are counted, not just their order. Every count and histogram in the report will describe the filtered subset.</p>
      <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {numberField("yearFrom", "Published from", "any", YEAR_RANGE.min, YEAR_RANGE.max)}
        {numberField("yearTo", "Published until", "any", YEAR_RANGE.min, YEAR_RANGE.max)}
        {numberField("minCitations", "Min. citations", "any", 0, 10000000)}
        {numberField("maxCitations", "Max. citations", "any", 0, 10000000)}
      </div>
      {error && <p role="alert" className="mt-2 text-sm text-v-failed">{error}</p>}
      <p className="mt-3 max-w-[70ch] text-xs leading-relaxed text-ink-3">ClinicalTrials.gov records carry no citation count, so citation bounds never apply to them: filtering them out would remove the terminated and never-reported trials this tool exists to surface. Records with no publication date cannot satisfy a year bound.</p>
      <label className="mt-4 flex items-start gap-2.5 text-sm text-ink-2" htmlFor="sticky-filters">
        <input id="sticky-filters" name="stickyFilters" type="checkbox" className="mt-0.5 size-4 accent-accent"
          checked={sticky} onChange={(event) => onChange({ ...state, sticky: event.target.checked })} />
        <span>
          Keep these filters for my next search
          <span className="mt-0.5 block text-xs leading-relaxed text-ink-3">{sticky
            ? "Filters stay on until you change them, and are shown beside every filtered question."
            : "Filters clear as soon as this search starts, so the next one covers the whole index."}</span>
        </span>
      </label>
      {active.length > 0 && <div className="mt-4 flex flex-wrap items-center gap-2">
        <span className="inline-flex items-center gap-1.5 text-xs text-ink-2"><Funnel size={14} aria-hidden />Active:</span>
        {active.map((label) => <span key={label} className="rounded-mark bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-ink">{label}</span>)}
        <button type="button" onClick={() => onChange({ ...state, draft: EMPTY_DRAFT })} className="text-xs text-accent underline-offset-4 hover:underline">Clear filters</button>
      </div>}
    </details>
  );
}
