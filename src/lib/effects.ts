import type { EffectSize, Paper } from "../types";

/** Prefer the backend's transformed 95% analysis interval; never mix raw ratios with logs. */
export function effectForPool(paper: Paper, effectType: string): EffectSize | null {
  const analysis = paper.analysisEffectSize;
  if (analysis) return analysis.metric === effectType && validInterval(analysis) ? analysis : null;
  const raw = paper.effectSize;
  if (!raw || !validInterval(raw) || paper.ciLevel !== 0.95) return null;
  const metric = ["d", "g"].includes(raw.metric) ? "SMD" : raw.metric;
  if (metric === effectType) return raw;
  if (`log${metric}` === effectType && ["OR", "RR", "HR"].includes(metric)) {
    if (raw.value <= 0 || raw.ci![0] <= 0 || raw.ci![1] <= 0) return null;
    return { metric: effectType, value: Math.log(raw.value), ci: [Math.log(raw.ci![0]), Math.log(raw.ci![1])] };
  }
  return null;
}
function validInterval(effect: EffectSize): boolean {
  return Boolean(effect.ci && [effect.value, ...effect.ci].every(Number.isFinite) && effect.ci[0] <= effect.ci[1]);
}
