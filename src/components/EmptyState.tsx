import { BAR_GROUPS } from "../lib/verdicts";
import { cx } from "../lib/format";

export default function EmptyState() {
  return (
    <div className="fade-up flex flex-col gap-10 lg:pt-2">
      <div>
        <h2 className="text-sm font-medium text-ink-2">What comes back</h2>
        <p className="mt-2 max-w-[58ch] leading-relaxed text-ink-2">
          Matching studies are sorted into three answers. Counts cover the indexed match set;
          source evidence and study-planning estimates appear below them.
        </p>
      </div>

      <ul className="flex max-w-[58ch] flex-col gap-6">
        {BAR_GROUPS.map((group) => (
          <li key={group.key} className="flex gap-3">
            <span
              aria-hidden
              className={cx("mt-[7px] h-2.5 w-2.5 shrink-0 rounded-mark", group.bg)}
            />
            <div>
              <p className="font-medium text-ink">{group.label}</p>
              <p className="mt-1 text-sm leading-relaxed text-ink-2">{group.description}</p>
            </div>
          </li>
        ))}
      </ul>

      <p className="max-w-[58ch] text-sm leading-relaxed text-ink-2">
        Matches that were too imprecise to call, or that could not be read, are counted beside
        the answers as inconclusive, each with its reason. That is not a finding.
      </p>

      <p className="max-w-[58ch] text-sm leading-relaxed text-ink-3">
        Sources: OpenAlex literature and ClinicalTrials.gov registry records. Coverage depends
        on the indexed corpus. Missing or unreported evidence does not establish a null effect.
      </p>
    </div>
  );
}
