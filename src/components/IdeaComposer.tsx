import { useState, type FormEvent } from "react";
import { ArrowRight } from "@phosphor-icons/react";
import type { EffectType, SearchRequest } from "../types";

interface Props { busy: boolean; onSubmit: (req: SearchRequest) => void; onCancel: () => void }
const EXAMPLES = [
  "Does intermittent fasting improve working memory in healthy adults?",
  "Does vitamin D supplementation reduce depressive symptoms in adults?",
  "Does metformin improve survival in adults with lung cancer?",
];
const SCALES: { value: EffectType; label: string }[] = [
  { value: "SMD", label: "Standardized difference (SMD)" },
  { value: "MD", label: "Mean difference (outcome units)" },
  { value: "logOR", label: "Log odds ratio" },
  { value: "logRR", label: "Log risk ratio" },
  { value: "logHR", label: "Log hazard ratio" },
];
const DEFAULTS = { plannedN: "200", alpha: "0.05", valueSuccess: "100", valueNull: "20", studyCost: "30", outcomeSd: "1", baselineRisk: "0.2" };
type PlanField = keyof typeof DEFAULTS;

export default function IdeaComposer({ busy, onSubmit, onCancel }: Props) {
  const [idea, setIdea] = useState("");
  const [effectType, setEffectType] = useState<EffectType>("SMD");
  const [sesoi, setSesoi] = useState("");
  const [plan, setPlan] = useState(DEFAULTS);
  const [error, setError] = useState<string | null>(null);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    if (idea.trim().length < 20) {
      setError("Describe your question in at least 20 characters, including the intervention and outcome.");
      return;
    }
    if (!event.currentTarget.reportValidity()) return;
    setError(null);
    const numeric = Object.fromEntries(Object.entries(plan).map(([key, value]) => [key, Number(value)]));
    onSubmit({ idea: idea.trim(), field: "Medicine and health", effectType, ...numeric, ...(sesoi !== "" ? { sesoi: Number(sesoi) } : {}) });
  }
  function numberField(name: PlanField, label: string, options: { min?: number; max?: number; step?: string } = {}) {
    return <label key={name} className="flex flex-col gap-1.5 text-sm text-ink-2" htmlFor={name}>
      {label}
      <input id={name} name={name} type="number" className="field font-mono tabular-nums" required disabled={busy}
        value={plan[name]} min={options.min} max={options.max} step={options.step ?? "any"}
        onChange={(event) => setPlan((previous) => ({ ...previous, [name]: event.target.value }))} />
    </label>;
  }
  return (
    <form onSubmit={handleSubmit} noValidate className="flex flex-col gap-7">
      <div>
        <h1 className="text-3xl font-semibold leading-[1.05] tracking-tight text-ink sm:text-4xl">Check the file drawer before you run the study.</h1>
        <p className="mt-3 max-w-[44ch] leading-relaxed text-ink-2">Map prior clinical studies, distinguish credible nulls from uncertain results, and plan your next study.</p>
      </div>
      <div className="flex flex-col gap-2">
        <label htmlFor="idea" className="text-sm font-medium text-ink">Your research question</label>
        <textarea id="idea" name="idea" rows={4} value={idea} disabled={busy} maxLength={10000}
          onChange={(event) => setIdea(event.target.value)} placeholder="Does X change Y in population Z?"
          aria-invalid={error ? "true" : undefined} aria-describedby="idea-help" className="field resize-y disabled:opacity-60" />
        <p id="idea-help" className="text-sm text-ink-3">OpenAlex literature and ClinicalTrials.gov records.</p>
        {error && <p role="alert" className="text-sm text-v-failed">{error}</p>}
      </div>
      <fieldset className="rounded-panel border border-line bg-surface p-4">
        <legend className="px-1 text-sm font-medium text-ink">Smallest effect that matters</legend>
        <label htmlFor="effect-type" className="text-sm text-ink-2">Effect scale</label>
        <select id="effect-type" value={effectType} disabled={busy} onChange={(event) => { setEffectType(event.target.value as EffectType); setSesoi(""); }} className="field mt-1.5">
          {SCALES.map((scale) => <option key={scale.value} value={scale.value}>{scale.label}</option>)}
        </select>
        <label htmlFor="sesoi" className="mt-3 block text-sm text-ink-2">SESOI · {effectType}</label>
        <input id="sesoi" type="number" min="0.000001" max="1000" step="any" value={sesoi} disabled={busy}
          onChange={(event) => setSesoi(event.target.value)} placeholder="Propose from my question" className="field mt-1.5 font-mono" aria-describedby="sesoi-help" />
        <p id="sesoi-help" className="mt-2 text-xs leading-relaxed text-ink-3">The meaningful-effect threshold changes the evidence buckets. Leave blank for a proposal with a rationale, or enter your own positive threshold. {effectType.startsWith("log") ? "Ratios use the natural-log scale; a ratio of 1.2 is about 0.182." : effectType === "SMD" ? "SMD is measured in standard deviations." : "Use the same units as your primary outcome."}</p>
      </fieldset>
      <details className="border-y border-line py-4">
        <summary className="cursor-pointer text-sm font-medium text-ink">Study plan & value <span className="ml-1 font-normal text-ink-3">N = {plan.plannedN}</span></summary>
        <div className="mt-4 grid grid-cols-2 gap-3">
          {numberField("plannedN", "Planned total N", { min: 4, max: 1000000, step: "1" })}
          {numberField("alpha", "Two-sided α", { min: 0.001, max: 0.2, step: "0.001" })}
          {effectType === "MD" && numberField("outcomeSd", "Outcome SD", { min: 0.000001, max: 1e6 })}
          {effectType.startsWith("log") && numberField("baselineRisk", "Baseline event risk", { min: 0.001, max: 0.999, step: "0.001" })}
        </div>
        <p className="mt-3 text-xs leading-relaxed text-ink-3">Planning assumes equal study arms. Enter values and study cost in the same units (for example, utility points).</p>
        <div className="mt-3 grid grid-cols-2 gap-3">
          {numberField("valueSuccess", "Value of success", { min: 0, max: 1e12 })}
          {numberField("valueNull", "Value of a null result", { min: 0, max: 1e12 })}
          {numberField("studyCost", "Study cost", { min: 0, max: 1e12 })}
        </div>
        <p className="mt-3 text-xs leading-relaxed text-ink-3">EV = assurance × success value + (1 − assurance) × null value − cost.</p>
      </details>
      <div className="flex items-center gap-3">
        {busy ? <button type="button" onClick={onCancel} className="btn btn-secondary">Cancel search</button>
          : <button type="submit" className="btn btn-primary">Map the literature <ArrowRight size={16} weight="bold" aria-hidden /></button>}
      </div>
      <div>
        <p className="mb-2 text-sm text-ink-3">Or start from a clinical question</p>
        <ul className="flex flex-col gap-1.5">{EXAMPLES.map((example) => <li key={example}>
          <button type="button" disabled={busy} onClick={() => { setIdea(example); setError(null); }} className="text-left text-sm leading-snug text-ink-2 underline-offset-4 transition-colors hover:text-accent hover:underline disabled:opacity-60">{example}</button>
        </li>)}</ul>
      </div>
    </form>
  );
}
