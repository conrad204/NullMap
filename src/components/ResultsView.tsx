import { useState } from "react";
import { ArrowUpRight, Funnel, Info } from "@phosphor-icons/react";
import { registryExemptionNotice } from "../lib/filters";
import type { EffectTrend, InconclusiveReason, Paper, SearchResult, Source, Verdict } from "../types";
import { BAR_GROUPS, BAR_VERDICTS, INCONCLUSIVE_REASONS, VERDICT_META, countByVerdict, type BarGroupKey } from "../lib/verdicts";
import { headline } from "../lib/headline";
import { cx, formatAuthors, formatCount } from "../lib/format";
import EvidenceDetails from "./EvidenceDetails";

type Filter = BarGroupKey | "inconclusive" | "all";
const filterVerdicts = (filter: Filter): Verdict[] => filter === "inconclusive" ? ["inconclusive"] : BAR_GROUPS.find((group) => group.key === filter)?.verdicts ?? [];
const filterLabel = (filter: Filter) => filter === "inconclusive" ? VERDICT_META.inconclusive.label : BAR_GROUPS.find((group) => group.key === filter)?.label ?? "";
const SOURCES: Record<Source, string> = { openalex: "OpenAlex", clinicaltrials: "ClinicalTrials.gov", ctgov: "ClinicalTrials.gov", merged: "Linked paper + registry", arxiv: "arXiv", pubmed: "PubMed", osf: "OSF" };
const TIERS = { numeric: "Reported numbers", derived: "Computed from arm-level results", reconstructed: "Reconstructed estimate", text_only: "Text only · provisional" };
const DIRECTIONS = { favours_intervention: "Favours the intervention", favours_comparator: "Favours the comparator", unclear: "" };
const EXTRACTION_SOURCES = { abstract: "numbers read from abstract", full_text: "numbers read from full text" };
export function safeUrl(url: string): string | undefined {
  try { const parsed = new URL(url); return ["https:", "http:"].includes(parsed.protocol) ? parsed.href : undefined; }
  catch { return undefined; }
}

function countReasons(papers: Paper[]): Partial<Record<InconclusiveReason, number>> {
  const reasons: Partial<Record<InconclusiveReason, number>> = {};
  for (const paper of papers) if (paper.inconclusiveReason) reasons[paper.inconclusiveReason] = (reasons[paper.inconclusiveReason] ?? 0) + 1;
  return reasons;
}

export default function ResultsView({ result }: { result: SearchResult }) {
  const [filter, setFilter] = useState<Filter>("all");
  // An API that predates a bucket omits its key; a missing count is zero, never NaN.
  const counts = { ...countByVerdict([]), ...(result.bucketCounts ?? countByVerdict(result.papers)) };
  // Without a server breakdown (mock data, older API), count the reasons on the displayed studies.
  const reasons = result.inconclusiveReasons ?? countReasons(result.papers);
  // Provisional when no displayed effect is backed by numbers.
  const effects = result.papers.filter((paper) => paper.verdict === "effect");
  const answer = headline(counts, effects.length > 0 && effects.every((paper) => (paper.evidenceTier ?? "text_only") === "text_only"), result.evidenceBase?.controlled);
  const matched = Object.values(counts).reduce((sum, count) => sum + count, 0);
  // Stated beside the active filters, not in the warnings list: a reader of filtered
  // results has to be told why unpublished trial records survived a citation bound.
  const exemption = registryExemptionNotice(result.filters, result.papers);
  const shown = filter === "all" ? result.papers : result.papers.filter((paper) => filterVerdicts(filter).includes(paper.verdict));
  return (
    <div className="fade-up flex flex-col gap-10">
      <header>
        <p className="text-sm leading-relaxed text-ink-2">{formatCount(result.totalScanned)} matching indexed records · {result.searchedSources.map((source) => SOURCES[source] ?? source).join(" + ") || "No sources available"}</p>
        {result.retrieval && <p className="mt-1 text-xs text-ink-3">Retrieval: {result.retrieval.mode} · {result.retrieval.expanded} additional review references</p>}
        {result.filters && <AppliedFilterNote filters={result.filters} />}
        {exemption && <p className="mt-2 max-w-[78ch] rounded-control border border-line bg-surface-2 px-3 py-2 text-xs leading-relaxed text-ink-2"><Info size={14} className="mr-1.5 inline align-[-2px] text-ink-3" aria-hidden />{exemption}</p>}
        <div className="mt-3 flex flex-wrap gap-1.5">{result.keywords.map((keyword) => <span key={keyword} className="rounded-mark bg-surface-2 px-1.5 py-0.5 font-mono text-xs text-ink">{keyword}</span>)}</div>
      </header>
      <section aria-label="Has this been tested before?">
        <h2 className="text-2xl leading-tight tracking-tight text-ink sm:text-3xl">{answer.title}</h2>
        <p className="mt-2 max-w-[65ch] leading-relaxed text-ink-2">{answer.detail}</p>
      </section>
      {result.effectTrend
        ? <EffectTrendPanel trend={result.effectTrend} />
        : result.overview ? <OverviewPanel overview={result.overview} />
        : matched > 0 && <p className="max-w-[65ch] border-l-2 border-line pl-4 text-sm leading-relaxed text-ink-2">No summary of effects is shown because none of the {formatCount(matched)} matching {matched === 1 ? "study" : "studies"} reported an effect, so there is no trend to describe. What each one did report is listed under the studies below.</p>}
      {result.pico && <details className="text-sm"><summary className="cursor-pointer text-ink-2">Interpreted question</summary><dl className="mt-2 space-y-2">{(["population", "intervention", "comparator", "outcome"] as const).map((key) => <div key={key}><dt className="capitalize text-ink-3">{key}</dt><dd className="text-ink">{result.pico![key] || "Not specified"}</dd></div>)}</dl></details>}
      {!!result.warnings?.length && <div className="rounded-control border border-line bg-surface-2 p-4 text-sm leading-relaxed text-ink-2"><p className="font-medium text-ink">Coverage & limitations</p><ul className="mt-2 list-disc space-y-1 pl-4">{result.warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul></div>}
      <VerdictBreakdown counts={counts} reasons={reasons} filter={filter} onFilter={setFilter} scope={result.countScope ?? (result.bucketCounts ? "Full lexical match set in the index." : "Counts cover the displayed studies only.")} />
      <section><h2 className="text-sm font-medium text-ink-2">What the evidence says</h2><p className="mt-3 max-w-[65ch] leading-relaxed text-ink">{result.summary}</p></section>
      <EvidenceDetails result={result} />
      <PaperList papers={shown} allDisplayed={result.papers.length} filter={filter} onClear={() => setFilter("all")} />
    </div>
  );
}
/** A filtered report describes a subset of the index, so say which subset and what it cost. */
function AppliedFilterNote({ filters }: { filters: NonNullable<SearchResult["filters"]> }) {
  const { description, excluded, matchedBeforeFilters } = filters;
  return <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-2">
    <Funnel size={14} className="text-ink-3" aria-hidden />
    <span>Filtered corpus: {description.join(" · ")}.</span>
    <span className="text-ink-3">{excluded !== null && matchedBeforeFilters !== null
      ? `${formatCount(excluded)} of ${formatCount(matchedBeforeFilters)} otherwise-matching records were excluded before counting.`
      : "How many records the filters excluded could not be counted."}</span>
  </p>;
}
function OverviewPanel({ overview }: { overview: NonNullable<SearchResult["overview"]> }) {
  return <section className="border-l-2 border-line pl-4">
    <h2 className="text-sm font-medium text-ink">What these studies are, and why none settles it</h2>
    <p className="mt-3 max-w-[65ch] leading-relaxed text-ink">{overview.summary}</p>
    {overview.notes.length > 0 && <dl className="mt-4 max-w-[70ch] space-y-3">{overview.notes.map(({ id, title, note }) => <div key={id}>
      <dt className="text-sm leading-snug text-ink">{title}</dt>
      <dd className="mt-0.5 text-sm leading-relaxed text-ink-2">{note}</dd>
    </div>)}</dl>}
    <p className="mt-3 max-w-[70ch] text-xs leading-relaxed text-ink-3">No trend of effects is shown because too few matching studies reported one. {overview.scope}</p>
  </section>;
}
function EffectTrendPanel({ trend }: { trend: EffectTrend }) {
  const split = [
    { label: "favour the intervention", count: trend.favoursIntervention, tone: "text-v-effect" },
    { label: "favour the comparator", count: trend.favoursComparator, tone: "text-v-failed" },
    { label: "direction not stated", count: trend.unclear, tone: "text-ink-2" },
  ];
  return <section className="border-l-2 border-v-effect pl-4">
    <h2 className="text-sm font-medium text-ink">What the reported effects have in common</h2>
    <dl className="mt-3 flex flex-wrap gap-x-8 gap-y-3">{split.map(({ label, count, tone }) => <div key={label} className="flex items-baseline gap-2">
      <dt className="order-2 text-sm text-ink-2">{label}</dt>
      <dd className={cx("order-1 font-mono text-2xl tabular-nums leading-none tracking-tight", count > 0 ? tone : "text-ink-3")}>{formatCount(count)}</dd>
    </div>)}</dl>
    {trend.summary && <p className="mt-4 max-w-[65ch] leading-relaxed text-ink">{trend.summary}</p>}
    {trend.patterns.length > 0 && <ul className="mt-3 max-w-[65ch] list-disc space-y-1 pl-4 text-sm leading-relaxed text-ink-2">{trend.patterns.map((pattern) => <li key={pattern}>{pattern}</li>)}</ul>}
    <p className="mt-3 max-w-[70ch] text-xs leading-relaxed text-ink-3">{trend.scope} An “effect” is a significant difference in either direction, so the split matters. {trend.summary ? "The counts are computed; the paragraph is a generated reading of the studies’ extracted facts and quotes, and introduces no numbers of its own." : "A written summary needs at least two such studies."}</p>
  </section>;
}
function VerdictBreakdown({ counts, reasons, filter, onFilter, scope }: { counts: Record<Verdict, number>; reasons: Partial<Record<InconclusiveReason, number>>; filter: Filter; onFilter: (filter: Filter) => void; scope: string }) {
  const classified = BAR_VERDICTS.reduce((sum, verdict) => sum + counts[verdict], 0);
  const groups = BAR_GROUPS.map((group) => ({ group, total: group.verdicts.reduce((sum, verdict) => sum + counts[verdict], 0) }));
  return <section>
    <h2 className="text-sm font-medium text-ink-2">What prior work found <span className="font-normal text-ink-3">· {formatCount(classified)} classified matches</span></h2>
    <div role="img" aria-label={groups.map(({ group, total }) => `${total} ${group.label}`).join(", ")} className="mt-3 flex h-3 w-full gap-1">
      {classified === 0 && <div className="w-full rounded-mark bg-surface-2" />}
      {groups.filter(({ total }) => total > 0).map(({ group, total }) => <div key={group.key} style={{ flexGrow: total }} className={cx(group.bg, "min-w-1 basis-0 rounded-mark transition-opacity duration-300", filter !== "all" && filter !== group.key && "opacity-25")} />)}
    </div>
    <ul className="mt-4 grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-3">{groups.map(({ group, total }) => <li key={group.key}>
      <button type="button" title={group.description} onClick={() => onFilter(filter === group.key ? "all" : group.key)} aria-pressed={filter === group.key} className={cx("group -mx-2 flex w-[calc(100%+1rem)] flex-col gap-1.5 rounded-control p-2 text-left transition-colors", filter === group.key ? "bg-surface-2" : "hover:bg-surface-2/60")}>
        <span className="flex items-baseline gap-2"><span aria-hidden className={cx("h-2.5 w-2.5 shrink-0 self-center rounded-mark", group.bg)} /><span className="font-mono text-2xl tabular-nums leading-none tracking-tight text-ink">{formatCount(total)}</span><span className="text-sm font-medium text-ink">{group.label}</span></span>
        <span className="text-xs leading-relaxed text-ink-3">{group.verdicts.length === 1 ? VERDICT_META[group.verdicts[0]].short : group.verdicts.map((verdict) => `${formatCount(counts[verdict])} ${VERDICT_META[verdict].short}`).join(" · ")}</span>
      </button>
    </li>)}</ul>
    <p className="mt-4 text-xs leading-relaxed text-ink-3">{scope} Select an answer to filter the displayed studies below.</p>
    <InconclusiveNote count={counts.inconclusive} reasons={reasons} active={filter === "inconclusive"} onToggle={() => onFilter(filter === "inconclusive" ? "all" : "inconclusive")} />
  </section>;
}
function InconclusiveNote({ count, reasons, active, onToggle }: { count: number; reasons: Partial<Record<InconclusiveReason, number>>; active: boolean; onToggle: () => void }) {
  if (count === 0) return null;
  const known = INCONCLUSIVE_REASONS.filter(({ key }) => (reasons[key] ?? 0) > 0);
  // With no breakdown from the server, still explain every way a study ends up here.
  const rows = known.length ? known : INCONCLUSIVE_REASONS.filter(({ key }) => key !== "no_threshold");
  return <div className="mt-6 border-t border-line pt-5">
    <button type="button" onClick={onToggle} aria-pressed={active} className={cx("group -m-2 flex items-baseline gap-2 rounded-control p-2 text-left transition-colors", active ? "bg-surface-2" : "hover:bg-surface-2/60")}>
      <span className="font-mono text-2xl tabular-nums leading-none tracking-tight text-ink">{formatCount(count)}</span>
      <span className="text-sm text-ink-2 group-hover:text-ink">further {count === 1 ? "match is" : "matches are"} inconclusive</span>
    </button>
    <p className="mt-3 max-w-[70ch] text-sm leading-relaxed text-ink-2">Inconclusive means neither an effect nor a null could be established. It is left out of the bar because it is not a finding: it does not mean the intervention does nothing, and most often it describes what could be read from the record, not what the study found.</p>
    <dl className="mt-4 grid grid-cols-1 gap-x-8 gap-y-4 sm:grid-cols-2">{rows.map(({ key, label, detail }) => <div key={key}>
      <dt className="flex items-baseline gap-2 text-sm text-ink">{known.length > 0 && <span className="font-mono tabular-nums text-ink">{formatCount(reasons[key] ?? 0)}</span>}<span>{label}</span></dt>
      <dd className="mt-1 text-xs leading-relaxed text-ink-3">{detail}</dd>
    </div>)}</dl>
  </div>;
}
function PaperList({ papers, allDisplayed, filter, onClear }: { papers: Paper[]; allDisplayed: number; filter: Filter; onClear: () => void }) {
  return <section>
    <div className="flex flex-wrap items-baseline justify-between gap-4"><h2 className="text-sm font-medium text-ink-2">{filter === "all" ? `${papers.length} displayed studies` : `${papers.length} of ${allDisplayed} displayed studies · ${filterLabel(filter)}`}</h2>{filter !== "all" && <button type="button" onClick={onClear} className="text-sm text-accent underline-offset-4 hover:underline">Show all displayed studies</button>}</div>
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
      <p className="mt-1 text-xs text-ink-3">{SOURCES[paper.source] ?? paper.source} · {TIERS[paper.evidenceTier ?? "text_only"]}{paper.verdict === "effect" && paper.resultDirection && DIRECTIONS[paper.resultDirection] ? ` · ${DIRECTIONS[paper.resultDirection]}` : ""}{paper.extractionSource ? ` · ${EXTRACTION_SOURCES[paper.extractionSource]}` : ""}</p>
      {paper.pmcid && <a href={`https://europepmc.org/article/PMC/${encodeURIComponent(paper.pmcid)}`} target="_blank" rel="noreferrer" className="mt-1 inline-block text-xs text-accent underline underline-offset-2">Open-access full text ({paper.pmcid})</a>}
      {paper.abstractAvailable === false && <p className="mt-1 text-xs text-ink-3">Bibliographic record · abstract unavailable</p>}
      {paper.snapshotDate && <p className="mt-1 text-xs text-ink-3">Literature snapshot: {paper.snapshotDate}</p>}
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
