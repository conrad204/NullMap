import type { ConceptMatch, ConceptSearchResult } from "../types";
import { SIGN_META, cosineWidth, matchSignals } from "../lib/concepts";
import { VERDICT_META } from "../lib/verdicts";
import { cx } from "../lib/format";

/** The nearest indexed studies to a combined concept direction, and how near each concept put them. */
export default function ConceptResults({ result }: { result: ConceptSearchResult }) {
  if (!result.matches.length) {
    return (
      <p className="text-sm leading-relaxed text-ink-2">
        Nothing in the indexed corpus sits near that combination. That is a statement about this
        index, not about the literature.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3">
      {result.warnings?.map((warning) => (
        <p key={warning} className="rounded-control border border-line bg-surface-2 p-3 text-sm text-ink-2">{warning}</p>
      ))}
      <p className="text-sm text-ink-3">
        {result.matches.length} nearest indexed studies, by cosine to the combined direction. Cosine is a
        similarity, not a relevance score or a probability.
      </p>
      <ul className="flex flex-col gap-2">
        {result.matches.map((match) => <ConceptRow key={match.id} match={match} />)}
      </ul>
    </div>
  );
}

function ConceptRow({ match }: { match: ConceptMatch }) {
  const { contested } = matchSignals(match);
  const verdict = VERDICT_META[match.verdict];
  return (
    <li className="rounded-panel border border-line bg-surface p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 flex-1 break-words font-medium leading-snug text-ink">
          {match.url ? (
            <a href={match.url} target="_blank" rel="noreferrer" className="underline-offset-4 hover:text-accent hover:underline">{match.title}</a>
          ) : match.title}
        </p>
        <span className="font-mono text-xs text-ink-3">cos {match.cosine.toFixed(2)}</span>
      </div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-3">
        {match.year != null && <span>{match.year}</span>}
        <span className={verdict.text}>{verdict.label}</span>
        <span>{match.citations} citations</span>
        {contested && (
          <span className="text-v-failed">Also close to a negative concept</span>
        )}
      </div>
      <ul className="mt-2 flex flex-col gap-1">
        {match.concepts.map((concept) => {
          const meta = SIGN_META[concept.sign];
          return (
            <li key={`${concept.sign}:${concept.text}`} className="grid grid-cols-[auto_minmax(0,10rem)_5rem_auto] items-center gap-2 text-xs">
              <span aria-hidden className={cx("font-mono", meta.text)}>{meta.symbol}</span>
              <span className="truncate text-ink-2">{concept.text}</span>
              <span aria-hidden className="h-1.5 rounded-mark bg-surface-2">
                <span className={cx("block h-full rounded-mark", meta.bg)} style={{ width: `${cosineWidth(concept.cosine) * 100}%` }} />
              </span>
              <span className="font-mono text-ink-3">{concept.cosine.toFixed(2)}</span>
            </li>
          );
        })}
      </ul>
    </li>
  );
}
