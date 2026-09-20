import type { EvidencePool, Paper, QueryCosts, SearchResult } from "../types";
import { formatCount, percent, signed } from "../lib/format";
import { effectForPool } from "../lib/effects";

export default function EvidenceDetails({ result }: { result: SearchResult }) {
  const stats = result.statistics;
  return <>
    {stats && <section className="border-y border-line py-5">
      <h2 className="text-sm font-medium text-ink-2">Quantitative evidence</h2>
      {stats.pools.length ? <div className="mt-4 space-y-6">{stats.pools.map((pool, index) => <ForestPlot key={`${pool.outcome}-${pool.effectType}-${index}`} pool={pool} papers={result.papers} sesoi={result.pico?.effectType === pool.effectType ? result.pico.sesoi : undefined} />)}</div>
        : <p className="mt-3 text-sm leading-relaxed text-ink-2">No compatible group of at least three studies was available to pool. A missing pooled estimate does not establish no effect.</p>}
      <div className="mt-5">
        <h3 className="text-sm font-medium text-ink">Registry reporting gap</h3>
        <p className="mt-1 text-sm leading-relaxed text-ink-2">{stats.fileDrawer.share === null ? "No eligible completed trials were available to estimate the reporting gap." : <>{stats.fileDrawer.unreported} of {stats.fileDrawer.completed} eligible completed trials ({percent(stats.fileDrawer.share)}) have no posted results or linked result publication.</>}</p>
        <p className="mt-1 text-xs leading-relaxed text-ink-3">Based on registry records and publication links available in this index. Unreported outcomes are unknown; they are not evidence of no effect. Incomplete coverage can overstate this share.</p>
      </div>
      {(stats.assumptions.length > 0 || stats.warnings.length > 0) && <details className="mt-4 text-sm">
        <summary className="cursor-pointer text-ink-2">Statistical assumptions & limitations</summary>
        <ul className="mt-2 list-disc space-y-1.5 pl-4 leading-relaxed text-ink-2">{[...new Set([...stats.assumptions, ...stats.warnings])].map((item) => <li key={item}>{item}</li>)}</ul>
      </details>}
    </section>}
    {result.spin && result.spin.eligible > 0 && <section className="border-l-2 border-line-strong pl-4">
      <h2 className="text-sm font-medium text-ink-2">Registry/abstract disagreement</h2>
      <p className="mt-2 text-sm text-ink">{result.spin.disagreements} of {result.spin.eligible} assessed linked records have disagreeing registry and abstract findings.</p>
      <p className="mt-1 text-xs leading-relaxed text-ink-3">Possible abstract spin, not proof: different outcomes, follow-up times, or reporting choices may explain the disagreement. Check the linked sources.</p>
    </section>}
    {Boolean(result.nullTerms?.length || result.alternativeRoutes?.length) && <section>
      <h2 className="text-sm font-medium text-ink-2">Where to look next</h2>
      {!!result.nullTerms?.length && <><p className="mt-3 text-sm text-ink-2">Terms over-represented in credible-null abstracts</p><div className="mt-2 flex flex-wrap gap-2">{result.nullTerms.map(({ term, count }) => <span key={term} className="rounded-control bg-surface-2 px-2 py-1 text-xs text-ink">{term} <span className="font-mono text-ink-3">{formatCount(count)}</span></span>)}</div><p className="mt-2 text-xs text-ink-3">An Elasticsearch text association; this does not establish that the term causes a null result.</p></>}
      {!!result.alternativeRoutes?.length && <ul className="mt-4 space-y-4">{result.alternativeRoutes.map((route) => <li key={route.label}><h3 className="text-sm font-medium text-ink">{route.label}</h3><p className="mt-1 text-sm leading-relaxed text-ink-2">{route.reason}</p><p className="mt-1 text-xs text-ink-3">{formatCount(route.evidenceCount)} indexed studies supporting this suggestion</p></li>)}</ul>}
    </section>}
    {!!result.yearCounts?.length && <details className="text-sm">
      <summary className="cursor-pointer font-medium text-ink-2">Matching studies by year</summary>
      <div className="mt-3 grid grid-cols-3 gap-x-5 gap-y-2 sm:grid-cols-5">{[...result.yearCounts].sort((a, b) => a.year - b.year).map(({ year, count }) => <div key={year} className="flex justify-between gap-2 border-b border-line pb-1 text-xs"><span className="text-ink-3">{year}</span><span className="font-mono text-ink">{formatCount(count)}</span></div>)}</div>
    </details>}
    {result.costs && <Costs costs={result.costs} />}
  </>;
}
function ForestPlot({ pool, papers, sesoi }: { pool: EvidencePool; papers: Paper[]; sesoi?: number }) {
  const studies = papers.filter((paper) => pool.studyIds.includes(paper.id)).flatMap((paper) => {
    const effect = effectForPool(paper, pool.effectType);
    return effect ? [{ paper, effect }] : [];
  });
  const rows = studies.map(({ paper, effect }, index) => ({ label: `${index + 1}. ${paper.year || "Study"}`, title: paper.title, estimate: effect.value, ci: effect.ci!, pooled: false }));
  rows.push({ label: "Pooled", title: "Random-effects pooled estimate", estimate: pool.estimate, ci: pool.ci, pooled: true });
  const all = rows.flatMap((row) => [...row.ci, row.estimate]);
  const low = Math.min(0, ...all, -(sesoi ?? 0));
  const high = Math.max(0, ...all, sesoi ?? 0);
  const padding = (high - low || 1) * 0.1;
  const x = (value: number) => 75 + ((value - low + padding) / (high - low + padding * 2)) * 375;
  const height = rows.length * 30 + 38;
  return <div>
    <h3 className="text-sm font-medium text-ink">{pool.outcome || "Compatible outcome"}</h3>
    <p className="mt-1 text-xs leading-relaxed text-ink-3">{pool.k} studies · {pool.effectType}{pool.unit ? ` (${pool.unit})` : ""} · random effects (DerSimonian–Laird){pool.grouping === "model" ? " · outcomes matched by a language model, numbers computed from the studies" : ""}</p>
    <svg viewBox={`0 0 540 ${height}`} role="img" aria-label={`Pooled ${pool.effectType} ${pool.estimate.toFixed(3)}, 95% interval ${pool.ci[0].toFixed(3)} to ${pool.ci[1].toFixed(3)}. Individual study intervals and pooled interval.`} className="mt-3 w-full text-ink-2">
      {sesoi !== undefined && <rect x={x(-sesoi)} y="0" width={x(sesoi) - x(-sesoi)} height={height - 24} fill="var(--accent-soft)" opacity="0.65" />}
      <line x1={x(0)} x2={x(0)} y1="0" y2={height - 24} stroke="var(--ink-3)" strokeDasharray="3 3" />
      {rows.map((row, index) => {
        const y = 15 + index * 30;
        return <g key={`${row.label}-${index}`}><title>{row.title}: {row.estimate.toFixed(3)} [{row.ci[0].toFixed(3)}, {row.ci[1].toFixed(3)}]</title>
          <text x="0" y={y + 4} fill="currentColor" fontSize="12">{row.label}</text>
          <line x1={x(row.ci[0])} x2={x(row.ci[1])} y1={y} y2={y} stroke={row.pooled ? "var(--accent)" : "var(--ink-3)"} strokeWidth={row.pooled ? 3 : 1.5} />
          <rect x={x(row.estimate) - 4} y={y - 4} width="8" height="8" fill={row.pooled ? "var(--accent)" : "var(--ink)"} transform={row.pooled ? `rotate(45 ${x(row.estimate)} ${y})` : undefined} />
          <text x="535" y={y + 4} textAnchor="end" fill="currentColor" fontSize="12">{signed(row.estimate)}</text>
        </g>;
      })}
      {[low, ...(low !== 0 && high !== 0 ? [0] : []), high].filter((value, index, values) => values.indexOf(value) === index).map((tick) => <text key={tick} x={x(tick)} y={height - 4} textAnchor="middle" fill="currentColor" fontSize="11">{tick.toFixed(2)}</text>)}
    </svg>
    <p className="mt-1 font-mono text-xs text-ink">μ = {signed(pool.estimate, 3)} · 95% CI [{pool.ci[0].toFixed(3)}, {pool.ci[1].toFixed(3)}] · SE {pool.se.toFixed(3)} · τ² {pool.tau2.toFixed(4)}</p>
    <PublicationBias pool={pool} studies={studies.length === pool.k ? studies.map(({ effect }) => ({ estimate: effect.value, se: (effect.ci![1] - effect.ci![0]) / (2 * 1.959964) })) : []} />
    {studies.length < pool.k && <p className="mt-1 text-xs text-ink-3">{studies.length} of {pool.k} pooled studies have compatible 95% intervals on this displayed page.</p>}
    {sesoi !== undefined && <p className="mt-1 text-xs text-ink-3">Shaded area: ±SESOI. Horizontal lines show 95% confidence intervals.</p>}
    {studies.length > 0 && <details className="mt-2 text-xs"><summary className="cursor-pointer text-ink-3">Identify plotted studies</summary><ol className="mt-2 list-decimal space-y-1 pl-5 text-ink-2">{studies.map(({ paper }) => <li key={paper.id}>{paper.title}</li>)}</ol></details>}
  </div>;
}
function PublicationBias({ pool, studies }: { pool: EvidencePool; studies: { estimate: number; se: number }[] }) {
  const egger = pool.egger;
  if (!egger) return <p className="mt-2 text-xs leading-relaxed text-ink-3">Funnel asymmetry was not tested: Egger's regression needs at least 10 pooled studies of differing precision, and this pool has {pool.k}. A missing test is not evidence that reporting is unbiased.</p>;
  return <div className="mt-3">
    <h4 className="text-xs font-medium text-ink">Publication bias · Egger's test</h4>
    <p className="mt-1 font-mono text-xs text-ink">intercept {signed(egger.intercept, 2)} · 95% CI [{egger.ci[0].toFixed(2)}, {egger.ci[1].toFixed(2)}] · t({egger.df}) = {egger.t.toFixed(2)} · p = {egger.pValue < 0.001 ? "<0.001" : egger.pValue.toFixed(3)}</p>
    <Funnel studies={studies} pooled={pool.estimate} />
    <p className="mt-1 text-xs leading-relaxed text-ink-3">{egger.asymmetric
      ? `The funnel is asymmetric at p < ${egger.alpha}: smaller studies report systematically different effects, so the pooled estimate may reflect selective reporting.`
      : `No funnel asymmetry was detected at p < ${egger.alpha}. This test is underpowered, so it cannot establish that nothing is missing.`}{" "}Asymmetry also arises from heterogeneity, study quality and chance; no estimate here is adjusted for it.</p>
  </div>;
}
function Funnel({ studies, pooled }: { studies: { estimate: number; se: number }[]; pooled: number }) {
  const maxSe = Math.max(...studies.map((row) => row.se));
  if (!studies.length || !(maxSe > 0)) return null;
  const half = Math.max(...studies.map((row) => Math.abs(row.estimate - pooled)), 1.959964 * maxSe) * 1.15;
  const x = (value: number) => 75 + ((value - pooled + half) / (2 * half)) * 375;
  const y = (se: number) => 10 + (se / maxSe) * 110;
  return <svg viewBox="0 0 540 150" role="img" aria-label={`Funnel plot: ${studies.length} studies plotted by effect and standard error around the pooled estimate ${pooled.toFixed(3)}.`} className="mt-2 w-full text-ink-2">
    <line x1={x(pooled)} x2={x(pooled)} y1={y(0)} y2={y(maxSe)} stroke="var(--accent)" strokeWidth="1.5" />
    <line x1={x(pooled)} x2={x(pooled - 1.959964 * maxSe)} y1={y(0)} y2={y(maxSe)} stroke="var(--ink-3)" strokeDasharray="3 3" />
    <line x1={x(pooled)} x2={x(pooled + 1.959964 * maxSe)} y1={y(0)} y2={y(maxSe)} stroke="var(--ink-3)" strokeDasharray="3 3" />
    {studies.map((row, index) => <circle key={index} cx={x(row.estimate)} cy={y(row.se)} r="3.5" fill="var(--ink)" opacity="0.75"><title>{signed(row.estimate, 3)} (SE {row.se.toFixed(3)})</title></circle>)}
    <text x="0" y={y(0) + 4} fill="currentColor" fontSize="11">SE 0</text>
    <text x="0" y={y(maxSe) + 4} fill="currentColor" fontSize="11">SE {maxSe.toFixed(2)}</text>
    <text x={x(pooled)} y="145" textAnchor="middle" fill="currentColor" fontSize="11">pooled {signed(pooled, 2)}</text>
  </svg>;
}
function usd(value: number) { return `$${value.toFixed(value < 0.01 ? 5 : 3)}`; }
function Costs({ costs }: { costs: QueryCosts }) {
  return <details className="rounded-control border border-line p-4 text-sm">
    <summary className="cursor-pointer font-medium text-ink-2">Query cost <span className="ml-2 font-mono text-ink">{usd(costs.estimatedUsd)}</span><span className="ml-2 text-xs font-normal text-ink-3">{costs.calls} model calls · {(costs.latencyMs / 1000).toFixed(1)}s</span></summary>
    <div className="mt-4 overflow-x-auto"><table className="w-full text-left text-xs"><caption className="mb-2 text-left text-ink-3">Estimated model cost comparison</caption><thead><tr className="border-b border-line text-ink-3"><th className="pb-2 pr-3 font-normal">Read 200 abstracts</th><th className="pb-2 pr-3 font-normal">Cold pipeline</th><th className="pb-2 font-normal">Warm pipeline</th></tr></thead><tbody><tr className="font-mono text-ink"><td className="pt-2 pr-3">{usd(costs.naiveEstimatedUsd)}</td><td className="pt-2 pr-3">{usd(costs.coldEstimatedUsd)}</td><td className="pt-2">{usd(costs.warmEstimatedUsd)}</td></tr></tbody></table></div>
    <dl className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-3">{([
      ["Input tokens", costs.inputTokens], ["Output tokens", costs.outputTokens], ["Cached input tokens", costs.cachedTokens], ["New extractions", costs.extracted], ["Extraction cache hits", costs.extractionCacheHits],
    ] as [string, number][]).map(([label, value]) => <div key={label}><dt className="text-ink-3">{label}</dt><dd className="mt-1 font-mono text-ink">{formatCount(value)}</dd></div>)}</dl>
    <p className="mt-3 text-xs leading-relaxed text-ink-3">Model costs use configured token prices; estimates exclude search hosting and offline indexing. Cold and warm figures are modeled comparisons. Bucket aggregations use indexed fields and make no model calls.</p>
  </details>;
}
