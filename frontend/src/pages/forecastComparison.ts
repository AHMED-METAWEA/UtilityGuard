import type { ModelComparisonRow } from '../api/types';

export const modelLabel = (name: string): string => ({
  lightgbm: 'LightGBM', ridge: 'Ridge', random_forest: 'Random forest',
  seasonal_smoothing: 'Seasonal smoothing', ensemble: 'Ensemble',
})[name] ?? name;

/** Display choices only: selection is supplied by the backend's dev OOF scores. */
export function comparisonSummary(rows: ModelComparisonRow[], baselines: { name: string; mae: number }[]) {
  const live = rows.find((row) => row.isLive);
  const bestOther = rows.filter((row) => !row.isLive).reduce<ModelComparisonRow | undefined>(
    (best, row) => !best || row.mae < best.mae ? row : best, undefined);
  const bestBaseline = baselines.reduce<{ name: string; mae: number } | undefined>(
    (best, row) => !best || row.mae < best.mae ? row : best, undefined);
  return {
    live, bestOther,
    betterModel: live && bestOther && bestOther.mae < live.mae ? bestOther : undefined,
    betterBaseline: live && bestBaseline && bestBaseline.mae < live.mae ? bestBaseline : undefined,
  };
}
