import test from 'node:test';
import assert from 'node:assert/strict';
import { comparisonSummary, modelLabel } from '../src/pages/forecastComparison';
import type { ModelComparisonRow } from '../src/api/types';

const row = (name: string, mae: number, oofMae: number, isLive = false): ModelComparisonRow => ({
  name, mae, oofMae, isLive, isSelected: false, mape: 2, rmse: 3, bias: 0, kind: 'linear',
  skillVsBestBaseline: .5, fitSeconds: 1, params: '', pLgbmBetter: .5, hourlyMape: Array(24).fill(2), weights: {}, weightsSource: '',
});

test('hourly comparison uses the best other test model, independently of dev selection', () => {
  const live = row('lightgbm', 10, 8, true);
  const testWinner = row('ridge', 7, 12);
  const devWinner = { ...row('ensemble', 9, 6), isSelected: true };
  const result = comparisonSummary([live, devWinner, testWinner], [{ name: 'Yesterday', mae: 5 }]);
  assert.equal(result.live, live);
  assert.equal(result.bestOther, testWinner);
  assert.equal(result.betterModel, testWinner);
  assert.equal(result.betterBaseline?.name, 'Yesterday');
  assert.equal(devWinner.isSelected, true); // display helper does not change backend selection
});

test('ties never become claims of improvement; empty and legacy states are safe', () => {
  const result = comparisonSummary([row('lightgbm', 10, 8, true), row('ridge', 10, 7)], [{ name: 'Yesterday', mae: 10 }]);
  assert.equal(result.betterModel, undefined);
  assert.equal(result.betterBaseline, undefined);
  assert.deepEqual(comparisonSummary([], []), { live: undefined, bestOther: undefined, betterModel: undefined, betterBaseline: undefined });
  assert.equal(modelLabel('seasonal_smoothing'), 'Seasonal smoothing');
  assert.equal(modelLabel('future-model'), 'future-model');
});
