export const SCENARIO_WEIGHTS = {
  baseline: {
    label: 'Baseline',
    weights: { ghi: 0.40, substation: 0.40, landcost: 0.10, temp_derating: 0.10 },
  },
  ghi_heavy: {
    label: 'GHI-heavy',
    weights: { ghi: 0.60, substation: 0.20, landcost: 0.10, temp_derating: 0.10 },
  },
  substation_heavy: {
    label: 'Substation-heavy',
    weights: { ghi: 0.20, substation: 0.60, landcost: 0.10, temp_derating: 0.10 },
  },
  balanced: {
    label: 'Balanced',
    weights: { ghi: 0.25, substation: 0.25, landcost: 0.25, temp_derating: 0.25 },
  },
};

export function calculateCandidateScore(candidate, scenarioKey = 'baseline') {
  if (candidate.status === 'excluded' || !candidate.normalized_criteria) {
    return null;
  }

  const scenario = SCENARIO_WEIGHTS[scenarioKey] || SCENARIO_WEIGHTS.baseline;
  const { weights } = scenario;
  const norm = candidate.normalized_criteria;

  const ghiVal = norm.ghi ?? 0;
  const subVal = norm.substation ?? 0;
  const lcVal = norm.landcost ?? 0;
  const tempVal = norm.temp_derating ?? 0;

  const rawScore = (ghiVal * weights.ghi) +
                   (subVal * weights.substation) +
                   (lcVal * weights.landcost) +
                   (tempVal * weights.temp_derating);

  return Math.round(rawScore * 10000) / 10000;
}
