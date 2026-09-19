import { VERDICT_META, VERDICT_ORDER } from "../lib/verdicts";
import { cx } from "../lib/format";

export default function EmptyState() {
  return (
    <div className="fade-up flex flex-col gap-10 lg:pt-2">
      <div>
        <h2 className="text-sm font-medium text-ink-2">What comes back</h2>
        <p className="mt-2 max-w-[58ch] leading-relaxed text-ink-2">
          Matching studies are grouped by the strength of their results. Counts cover the
          indexed match set; source evidence and study-planning estimates appear below them.
        </p>
      </div>

      <ul className="grid grid-cols-1 gap-x-10 gap-y-6 sm:grid-cols-2">
        {VERDICT_ORDER.map((v) => {
          const meta = VERDICT_META[v];
          return (
            <li key={v} className="flex gap-3">
              <span
                aria-hidden
                className={cx("mt-[7px] h-2.5 w-2.5 shrink-0 rounded-mark", meta.bg)}
              />
              <div>
                <p className="font-medium text-ink">{meta.label}</p>
                <p className="mt-1 text-sm leading-relaxed text-ink-2">{meta.description}</p>
              </div>
            </li>
          );
        })}
      </ul>

      <p className="max-w-[58ch] text-sm leading-relaxed text-ink-3">
        Sources: OpenAlex literature and ClinicalTrials.gov registry records. Coverage depends
        on the indexed corpus. Missing or unreported evidence does not establish a null effect.
      </p>
    </div>
  );
}
