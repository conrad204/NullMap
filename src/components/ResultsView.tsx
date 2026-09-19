import { useState } from "react";
import { ArrowUpRight } from "@phosphor-icons/react";
import type { Paper, PursuitEstimate, SearchResult, Source, Statistics, Verdict } from "../types";
import { RECOMMENDATION_META, VERDICT_META, VERDICT_ORDER, countByVerdict } from "../lib/verdicts";
import { cx, formatAuthors, formatCount, percent, signed } from "../lib/format";
import EvidenceDetails from "./EvidenceDetails";

type Filter = Verdict | "all";
const SOURCES: Record<Source, string> = { openalex: "OpenAlex", clinicaltrials: "ClinicalTrials.gov", ctgov: "ClinicalTrials.gov", merged: "Linked paper + registry", user: "Contribution", arxiv: "arXiv", pubmed: "PubMed", osf: "OSF" };
const TIERS = { numeric: "Reported numbers", reconstructed: "Reconstructed estimate", text_only: "Text only · provisional" };
export function safeUrl(url: string): string | undefined {
  try { const parsed = new URL(url); return ["https:", "http:"].includes(parsed.protocol) ? parsed.href : undefined; }
  catch { return undefined; }
}

export default function ResultsView({ result }: { result: SearchResult }) {
  const [filter, setFilter] = useState<Filter>("all");
  const counts = result.bucketCounts ?? countByVerdict(result.papers);
  const total = VERDICT_ORDER.reduce((sum, verdict) => sum + counts[verdict], 0);
  const shown = filter === "all" ? result.papers : result.papers.filter((paper) => paper.verdict === filter);
  return (
    <div className="fade-up flex flex-col gap-10">
      <header>
        <p className="text-sm text-ink-3">Research question</p>
        <p className="mt-1 max-w-[60ch] text-lg leading-snug text-ink sm:text-xl">{result.idea}</p>
        <p className="mt-3 text-sm leading-relaxed text-ink-2">{formatCount(result.totalScanned)} matching indexed records · {result.searchedSources.map((source) => SOURCES[source] ?? source).join(" + ") || "No sources available"}</p>
        {result.retrieval && <p className="mt-1 text-xs text-ink-3">Retrieval: {result.retrieval.mode} · {result.retrieval.expanded} additional review references</p>}
        <div className="mt-3 flex flex-wrap gap-1.5">{result.keywords.map((keyword) => <span key={keyword} className="rounded-mark bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-ink">{keyword}</span>)}</div>
      </header>
      {result.pico && <section className="border-l-2 border-accent pl-4">
        <h2 className="text-sm font-medium text-ink">Meaningful-effect threshold: {result.pico.sesoi} {result.pico.effectType}</h2>
        <p className="mt-1 text-sm leading-relaxed text-ink-2">{result.pico.sesoiRationale}</p>
        <p className="mt-2 text-xs leading-relaxed text-ink-3">Change the SESOI in your study plan and search again to recalculate. Text-only classifications remain provisional.</p>
        <details className="mt-3 text-sm"><summary className="cursor-pointer text-ink-2">Interpreted question</summary><dl className="mt-2 space-y-2">{(["population", "intervention", "comparator", "outcome"] as const).map((key) => <div key={key}><dt className="capitalize text-ink-3">{key}</dt><dd className="text-ink">{result.pico![key] || "Not specified"}</dd></div>)}</dl></details>
      </section>}
      {!!result.warnings?.length && <div className="rounded-control border border-line bg-surface-2 p-4 text-sm leading-relaxed text-ink-2"><p className="font-medium text-ink">Coverage & limitations</p><ul className="mt-2 list-disc space-y-1 pl-4">{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div>}
      <VerdictBreakdown counts={counts} total={total} filter={filter} onFilter={setFilter} scope={result.countScope ?? (result.bucketCounts ? "Full lexical match set in the index." : "Counts cover the displayed studies only.")} />
      <div className="grid grid-cols-1 gap-8 xl:grid-cols-[1fr_minmax(245px,290px)]">
        <section><h2 className="text-sm font-medium text-ink-2">What the evidence says</h2><p className="mt-3 max-w-[65ch] leading-relaxed text-ink">{result.summary}</p></section>
        <EstimatePanel estimate={result.estimate} statistics={result.statistics} />
      </div>
      <EvidenceDetails result={result} />
      <PaperList papers={shown} allDisplayed={result.papers.length} filter={filter} onClear={() => setFilter("all")} />
    </div>
  );
}
function VerdictBreakdown({ counts, total, filter, onFilter, scope }: { counts: Record<Verdict, number>; total: number; filter: Filter; onFilter: (filter: Filter) => void; scope: string }) {
  return <section>
    <h2 className="text-sm font-medium text-ink-2">What prior work found <span className="font-normal text-ink-3">· {formatCount(total)} classified matches</span></h2>
    <div role="img" aria-label={VERDICT_ORDER.map((verdict) => `${counts[verdict]} ${VERDICT_META[verdict].label}`).join(", ")} className="mt-3 flex h-3 w-full gap-px overflow-hidden rounded-mark">
      {total === 0 && <div className="w-full bg-surface-2" />}
      {VERDICT_ORDER.filter((verdict) => counts[verdict] > 0).map((verdict) => <div key={verdict} style={{ flexGrow: counts[verdict] }} className={cx(VERDICT_META[verdict].bg, "transition-opacity duration-300", filter !== "all" && filter !== verdict && "opacity-25")} />)}
    </div>
    <ul className="mt-5 grid grid-cols-2 gap-x-5 gap-y-4 sm:grid-cols-3 xl:grid-cols-5">{VERDICT_ORDER.map((verdict) => {
      const meta = VERDICT_META[verdict];
      return <li key={verdict}><button type="button" onClick={() => onFilter(filter === verdict ? "all" : verdict)} aria-pressed={filter === verdict} className={cx("group -m-2 flex w-[calc(100%+1rem)] flex-col items-start gap-1 rounded-control p-2 text-left transition-colors", filter === verdict ? "bg-surface-2" : "hover:bg-surface-2/60")}>
        <span className="flex items-center gap-2"><span aria-hidden className={cx("h-2.5 w-2.5 rounded-mark", meta.bg)} /><span className="font-mono text-2xl tabular-nums leading-none tracking-tight text-ink">{formatCount(counts[verdict])}</span></span>
        <span className="text-sm leading-snug text-ink-2 group-hover:text-ink">{meta.label}</span>
      </button></li>;
    })}</ul>
    <p className="mt-4 text-xs leading-relaxed text-ink-3">{scope} Select a bucket to filter the displayed studies below.</p>
  </section>;
}
function EstimatePanel({ estimate, statistics }: { estimate: PursuitEstimate; statistics?: Statistics }) {
  const assurance = statistics ? statistics.assurance : estimate.pSuccess;
  const ev = statistics ? statistics.expectedValue : estimate.expectedValue;
  const rec = RECOMMENDATION_META[estimate.recommendation];
  return <aside className="rounded-panel border border-line bg-surface p-5 shadow-panel">
    <h2 className="text-sm font-medium text-ink-2">Your planned study</h2>
    <div className="mt-4 flex flex-wrap items-baseline gap-x-2"><span className="font-mono text-4xl tabular-nums leading-none tracking-tight text-ink">{assurance === null ? "—" : percent(assurance)}</span><span className="text-sm text-ink-2">assurance</span></div>
    <p className="mt-2 text-xs leading-relaxed text-ink-3">Bayesian expected power to detect an effect under the model. This does not measure the chance of a meaningful benefit.</p>
    <dl className="mt-5 grid grid-cols-2 gap-4 text-sm">
      <div><dt className="text-ink-3">Expected value</dt><dd className="mt-0.5 font-mono tabular-nums text-ink">{ev === null ? "Unavailable" : signed(ev)}</dd></div>
      <div><dt className="text-ink-3">Evidence confidence</dt><dd className="mt-0.5 capitalize text-ink">{estimate.confidence}</dd></div>
      <div><dt className="text-ink-3">N for 80% assurance</dt><dd className="mt-0.5 font-mono text-ink">{statistics?.requiredN != null ? formatCount(statistics.requiredN) : "Unavailable"}</dd></div>
      <div><dt className="text-ink-3">Planned MDE</dt><dd className="mt-0.5 font-mono text-ink">{statistics?.plannedMde != null ? statistics.plannedMde.toFixed(3) : "Unavailable"}</dd></div>
    </dl>
    <p className="mt-2 text-xs text-ink-3">EV uses your value and cost units. MDE uses the selected effect scale.</p>
    <p className={cx("mt-5 inline-flex items-center rounded-control px-2.5 py-1 text-sm font-medium", rec.text, rec.tint)}>{assurance === null ? "More evidence needed" : rec.label}</p>
    <ul className="mt-4 list-disc space-y-2 pl-4 text-sm leading-relaxed text-ink-2 marker:text-ink-3">{estimate.drivers.map((driver) => <li key={driver}>{driver}</li>)}</ul>
  </aside>;
}
function PaperList({ papers, allDisplayed, filter, onClear }: { papers: Paper[]; allDisplayed: number; filter: Filter; onClear: () => void }) {
  return <section>
    <div className="flex flex-wrap items-baseline justify-between gap-4"><h2 className="text-sm font-medium text-ink-2">{filter === "all" ? `${papers.length} displayed studies` : `${papers.length} of ${allDisplayed} displayed studies · ${VERDICT_META[filter].label}`}</h2>{filter !== "all" && <button type="button" onClick={onClear} className="text-sm text-accent underline-offset-4 hover:underline">Show all displayed studies</button>}</div>
    {papers.length === 0 ? <p className="mt-4 text-sm leading-relaxed text-ink-2">{filter === "all" ? "No studies were returned for this question. Missing evidence cannot establish a null effect." : "No studies from this bucket are on the displayed page. Headline counts may include other matching records."}</p> : <ol className="mt-2">{papers.map((paper) => <PaperRow key={paper.id} paper={paper} />)}</ol>}
  </section>;
}
function PaperRow({ paper }: { paper: Paper }) {
  const meta = VERDICT_META[paper.verdict];
  const url = safeUrl(paper.url);
  const links = [...new Set([...(paper.linkedUrls ?? []), ...(paper.nctIds ?? []).map((id) => `https://clinicaltrials.gov/study/${encodeURIComponent(id)}`), ...(paper.pmids ?? []).map((id) => `https://pubmed.ncbi.nlm.nih.gov/${encodeURIComponent(id)}/`)])].map((link) => safeUrl(link)).filter((link): link is string => Boolean(link) && link !== url);
  return <li className="grid grid-cols-1 gap-x-6 gap-y-3 border-t border-line py-5 sm:grid-cols-[1fr_auto]">
    <div className="min-w-0">
      {url ? <a href={url} target="_blank" rel="noreferrer" className="group inline-flex items-start gap-1.5 font-medium leading-snug text-ink transition-colors hover:text-accent"><span>{paper.title}</span><ArrowUpRight size={14} className="mt-1 shrink-0 text-ink-3 group-hover:text-accent" aria-hidden /></a> : <p className="font-medium leading-snug text-ink">{paper.title}</p>}
      <p className="mt-1 text-sm text-ink-2">{formatAuthors(paper.authors)}{paper.year ? `, ${paper.year}` : ""}{paper.venue ? `, ${paper.venue}` : ""}</p>
      <p className="mt-1 text-xs text-ink-3">{SOURCES[paper.source] ?? paper.source} · {TIERS[paper.evidenceTier ?? "text_only"]}</p>
      {paper.primaryOutcome && <p className="mt-2 text-xs leading-relaxed text-ink-2"><span className="font-medium text-ink">Primary outcome:</span> {paper.primaryOutcome}{paper.outcomeUnit ? ` · ${paper.outcomeUnit}` : ""}</p>}
      <p className="mt-2 max-w-[70ch] text-sm leading-relaxed text-ink-2">{paper.rationale}</p>
      {paper.evidenceSpan && <blockquote className="mt-3 border-l-2 border-line-strong pl-3 text-sm leading-relaxed text-ink"><span className="mb-1 block text-xs text-ink-3">Source evidence</span>“{paper.evidenceSpan}”</blockquote>}
      {paper.numericSource && <div className="mt-3 border-l-2 border-line-strong pl-3"><p className="text-xs text-ink-3">Structured registry evidence · numeric source path</p><code className="mt-1 block break-all font-mono text-xs leading-relaxed text-ink">{paper.numericSource}</code>{paper.nctIds?.[0] && <a href={`https://clinicaltrials.gov/api/v2/studies/${encodeURIComponent(paper.nctIds[0])}`} target="_blank" rel="noreferrer" className="mt-1 inline-block text-xs text-accent underline underline-offset-2">View registry JSON</a>}</div>}
      {paper.whyStopped && <p className="mt-2 text-sm text-ink-2"><span className="font-medium">Why it stopped:</span> {paper.whyStopped}</p>}
      {paper.mde != null && <p className="mt-2 text-xs leading-relaxed text-ink-2">80%-power MDE ≈ {paper.mde.toFixed(3)} {paper.analysisEffectType ?? paper.effectSize?.metric ?? ""} (two-sided α = 0.05). Smaller effects may be missed.</p>}
      {(paper.numericNotes?.length || Object.keys(paper.extractionEvidence ?? {}).length > 0) ? <details className="mt-3 text-xs text-ink-2"><summary className="cursor-pointer">Numeric evidence & analysis notes</summary>{paper.numericNotes?.map((note) => <p key={note} className="mt-2 leading-relaxed">{note}</p>)}{Object.entries(paper.extractionEvidence ?? {}).map(([field, quote]) => <blockquote key={field} className="mt-2 border-l border-line pl-2 leading-relaxed"><span className="font-medium">{field.replaceAll("_", " ")}:</span> “{quote}”</blockquote>)}</details> : null}
      {paper.significantButTrivial && <p className="mt-2 text-xs text-v-unreported">Statistically significant, but below the meaningful-effect threshold.</p>}
      {links.length > 0 && <div className="mt-3 flex flex-wrap gap-x-3 gap-y-1">{links.map((link, index) => <a key={link} href={link} target="_blank" rel="noreferrer" className="text-xs text-accent underline underline-offset-2">{link.includes("clinicaltrials.gov/study/") ? decodeURIComponent(link.split("/").pop() ?? "Trial registry") : link.includes("pubmed.ncbi.nlm.nih.gov") ? "PubMed record" : `Linked source ${index + 1}`}</a>)}</div>}
    </div>
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs sm:max-w-[185px] sm:flex-col sm:items-end">
      <span className={cx("rounded-control px-2 py-0.5 text-xs font-medium", meta.text, meta.tint)}>{meta.label}</span>
      <span className="font-mono tabular-nums text-ink-3">{paper.sampleSize !== null ? `n = ${formatCount(paper.sampleSize)}` : "n unknown"}</span>
      {paper.effectSize && <span className="font-mono tabular-nums text-ink-3 sm:text-right">{paper.effectSize.metric} = {paper.effectSize.value.toFixed(3)}{paper.effectSize.ci && <span className="block">{paper.ciLevel != null ? `${Math.round(paper.ciLevel * 100)}% CI` : "CI (level unspecified)"} [{paper.effectSize.ci[0].toFixed(3)}, {paper.effectSize.ci[1].toFixed(3)}]</span>}</span>}
      {paper.pValue != null && <span className="font-mono text-ink-3">p {paper.pValueOperator || "="} {paper.pValue.toPrecision(3)}</span>}
      {paper.citations > 0 && <span className="font-mono tabular-nums text-ink-3">{formatCount(paper.citations)} citations</span>}
    </div>
  </li>;
}
