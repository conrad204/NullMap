import { useState } from "react";
import { ArrowUpRight } from "@phosphor-icons/react";
import type { Paper, PursuitEstimate, SearchResult, Verdict } from "../types";
import {
  RECOMMENDATION_META,
  VERDICT_META,
  VERDICT_ORDER,
  countByVerdict,
} from "../lib/verdicts";
import { cx, formatAuthors, formatCount, percent, signed } from "../lib/format";

type Filter = Verdict | "all";

export default function ResultsView({ result }: { result: SearchResult }) {
  const [filter, setFilter] = useState<Filter>("all");
  const counts = countByVerdict(result.papers);
  const shown =
    filter === "all" ? result.papers : result.papers.filter((p) => p.verdict === filter);

  return (
    <div className="fade-up flex flex-col gap-12">
      <header>
        <p className="text-sm text-ink-3">Idea</p>
        <p className="mt-1 max-w-[60ch] text-lg leading-snug text-ink sm:text-xl">{result.idea}</p>
        <p className="mt-4 text-sm leading-relaxed text-ink-2">
          Scanned {formatCount(result.totalScanned)} records across{" "}
          {result.searchedSources.length} sources. Matched on{" "}
          {result.keywords.map((k, i) => (
            <span key={k}>
              <span className="rounded-mark bg-surface-2 px-1.5 py-0.5 font-mono text-xs whitespace-nowrap text-ink">
                {k}
              </span>
              {i < result.keywords.length - 1 ? " " : ""}
            </span>
          ))}
        </p>
      </header>

      <VerdictBreakdown
        counts={counts}
        total={result.papers.length}
        filter={filter}
        onFilter={setFilter}
      />

      <div className="grid grid-cols-1 gap-8 md:grid-cols-[1fr_minmax(260px,320px)]">
        <section>
          <h2 className="text-sm font-medium text-ink-2">Summary</h2>
          <p className="mt-3 max-w-[65ch] leading-relaxed text-ink">{result.summary}</p>
        </section>
        <EstimatePanel estimate={result.estimate} />
      </div>

      <PaperList papers={shown} filter={filter} onClear={() => setFilter("all")} />
    </div>
  );
}

function VerdictBreakdown({
  counts,
  total,
  filter,
  onFilter,
}: {
  counts: Record<Verdict, number>;
  total: number;
  filter: Filter;
  onFilter: (f: Filter) => void;
}) {
  const toggle = (v: Verdict) => onFilter(filter === v ? "all" : v);

  return (
    <section>
      <h2 className="text-sm font-medium text-ink-2">What prior work found</h2>

      <div
        role="img"
        aria-label={VERDICT_ORDER.map((v) => `${counts[v]} ${VERDICT_META[v].label}`).join(", ")}
        className="mt-3 flex h-3 w-full gap-px overflow-hidden rounded-mark"
      >
        {total === 0 && <div className="w-full bg-surface-2" />}
        {VERDICT_ORDER.filter((v) => counts[v] > 0).map((v) => (
          <div
            key={v}
            style={{ flexGrow: counts[v] }}
            className={cx(
              VERDICT_META[v].bg,
              "transition-opacity duration-300",
              filter !== "all" && filter !== v && "opacity-25",
            )}
          />
        ))}
      </div>

      <ul className="mt-5 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-5">
        {VERDICT_ORDER.map((v) => {
          const meta = VERDICT_META[v];
          const selected = filter === v;
          return (
            <li key={v}>
              <button
                type="button"
                onClick={() => toggle(v)}
                aria-pressed={selected}
                className={cx(
                  "group -m-2 flex w-[calc(100%+1rem)] flex-col items-start gap-1 rounded-control p-2 text-left transition-colors",
                  selected ? "bg-surface-2" : "hover:bg-surface-2/60",
                )}
              >
                <span className="flex items-center gap-2">
                  <span aria-hidden className={cx("h-2.5 w-2.5 rounded-mark", meta.bg)} />
                  <span className="font-mono text-2xl tabular-nums leading-none tracking-tight text-ink">
                    {counts[v]}
                  </span>
                </span>
                <span className="text-sm leading-snug text-ink-2 group-hover:text-ink">
                  {meta.label}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function EstimatePanel({ estimate }: { estimate: PursuitEstimate }) {
  const rec = RECOMMENDATION_META[estimate.recommendation];
  return (
    <aside className="rounded-panel border border-line bg-surface p-5 shadow-panel">
      <h2 className="text-sm font-medium text-ink-2">Expected value of pursuit</h2>

      <div className="mt-4 flex flex-wrap items-baseline gap-x-2">
        <span className="font-mono text-4xl tabular-nums leading-none tracking-tight text-ink">
          {percent(estimate.pSuccess)}
        </span>
        <span className="text-sm text-ink-2">chance of a meaningful effect</span>
      </div>

      <dl className="mt-5 grid grid-cols-2 gap-4 text-sm">
        <div>
          <dt className="text-ink-3">EV score</dt>
          <dd className="mt-0.5 font-mono tabular-nums text-ink">
            {signed(estimate.expectedValue)}
          </dd>
        </div>
        <div>
          <dt className="text-ink-3">Confidence</dt>
          <dd className="mt-0.5 capitalize text-ink">{estimate.confidence}</dd>
        </div>
      </dl>

      <p
        className={cx(
          "mt-5 inline-flex items-center rounded-control px-2.5 py-1 text-sm font-medium",
          rec.text,
          rec.tint,
        )}
      >
        {rec.label}
      </p>

      <ul className="mt-4 list-disc space-y-2 pl-4 text-sm leading-relaxed text-ink-2 marker:text-ink-3">
        {estimate.drivers.map((d) => (
          <li key={d}>{d}</li>
        ))}
      </ul>
    </aside>
  );
}

function PaperList({
  papers,
  filter,
  onClear,
}: {
  papers: Paper[];
  filter: Filter;
  onClear: () => void;
}) {
  const heading =
    filter === "all"
      ? `${papers.length} prior attempts`
      : `${papers.length} ${VERDICT_META[filter].label.toLowerCase()}`;

  return (
    <section>
      <div className="flex flex-wrap items-baseline justify-between gap-4">
        <h2 className="text-sm font-medium text-ink-2">{heading}</h2>
        {filter !== "all" && (
          <button
            type="button"
            onClick={onClear}
            className="text-sm text-accent underline-offset-4 hover:underline"
          >
            Show all
          </button>
        )}
      </div>

      {papers.length === 0 ? (
        <p className="mt-4 text-sm text-ink-2">Nothing in this bucket for this idea.</p>
      ) : (
        <ol className="mt-2">
          {papers.map((p) => (
            <PaperRow key={p.id} paper={p} />
          ))}
        </ol>
      )}
    </section>
  );
}

function PaperRow({ paper: p }: { paper: Paper }) {
  const meta = VERDICT_META[p.verdict];
  return (
    <li className="grid grid-cols-1 gap-x-8 gap-y-3 border-t border-line py-5 sm:grid-cols-[1fr_auto]">
      <div className="min-w-0">
        <a
          href={p.url}
          target="_blank"
          rel="noreferrer"
          className="group inline-flex items-start gap-1.5 font-medium leading-snug text-ink transition-colors hover:text-accent"
        >
          <span>{p.title}</span>
          <ArrowUpRight
            size={14}
            className="mt-1 shrink-0 text-ink-3 transition-colors group-hover:text-accent"
            aria-hidden
          />
        </a>
        <p className="mt-1 text-sm text-ink-2">
          {formatAuthors(p.authors)}, {p.year}
          {p.venue ? `, ${p.venue}` : ""}
        </p>
        <p className="mt-2 max-w-[70ch] text-sm leading-relaxed text-ink-2">{p.rationale}</p>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs sm:flex-col sm:items-end sm:gap-1.5">
        <span
          className={cx(
            "rounded-control px-2 py-0.5 text-xs font-medium whitespace-nowrap",
            meta.text,
            meta.tint,
          )}
        >
          {meta.label}
        </span>
        <span className="font-mono tabular-nums text-ink-3">
          {p.sampleSize !== null ? `n = ${p.sampleSize}` : "n unknown"}
        </span>
        {p.effectSize && (
          <span className="font-mono tabular-nums text-ink-3">
            {p.effectSize.metric} = {p.effectSize.value.toFixed(2)}
            {p.effectSize.ci &&
              ` [${p.effectSize.ci[0].toFixed(2)}, ${p.effectSize.ci[1].toFixed(2)}]`}
          </span>
        )}
        <span className="font-mono tabular-nums text-ink-3">
          {formatCount(p.citations)} citations
        </span>
      </div>
    </li>
  );
}
