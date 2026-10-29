import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const fx = async (name) => JSON.parse(await readFile(new URL(`../../fixtures/rest/${name}.json`, import.meta.url), 'utf8'));
const has = (obj, keys, where) => { for (const k of keys) assert.ok(k in obj, `${where} must expose "${k}" (the page reads it)`); };

// The camelCase of a snake_case field is not always what it looks like (per_100h is Per100H), so the keys the pages read are pinned to the fixtures.
test('benchmark fixture exposes the keys the AI lab page reads', async () => {
  const b = await fx('lab_benchmark');
  has(b, ['detectors', 'leakCurve', 'faults', 'bestDetectorId', 'config', 'notes'], 'benchmark');
  has(b.detectors[0], ['falseAlarmsPer100H', 'detectionRate', 'medianDelayMin', 'p90DelayMin', 'meanDelayCost', 'unseenDetectionRate', 'byFault', 'kind', 'note'], 'detector');
  has(b.leakCurve[0], ['leakM3H', 'results'], 'leak point');
  has(b.config, ['windowMinutes', 'testRuns', 'cleanHours'], 'config');
});

test('localization, sensor, trial, quality and decision fixtures expose the keys the pages read', async () => {
  has(await fx('lab_localization'), ['bySize', 'top1', 'top3', 'runs', 'sensorsTotal'], 'localization');
  has((await fx('lab_localization')).bySize[0], ['leakM3H', 'top1'], 'localization row');
  const sv = await fx('lab_sensors');
  has(sv, ['leakM3H', 'runsPerAsset', 'baselineDelayMin', 'hourlyCost', 'rows'], 'sensor value');
  has(sv.rows[0], ['extraCost', 'extraDelayMin', 'detectionRate', 'localizationTop1', 'sensors'], 'sensor value row');
  const t = await fx('lab_trial');
  has(t, ['truthAssetId', 'truthName', 'leakM3H', 'hourlyCost', 'detectors', 'localization', 'hitRank', 'disabledSensors'], 'trial');
  has(t.localization.candidates[0], ['assetId', 'name', 'score', 'leakM3H', 'rank'], 'candidate');
  has(await fx('lab_quality'), ['kinds', 'falseFlagsPer100SensorHours', 'processRuns', 'processFalseFlags', 'windowTicks'], 'quality');
  has(await fx('quality_now'), ['sensorsTotal', 'flagged'], 'live quality');
  const d = await fx('decision');
  has(d, ['options', 'breakEven', 'decisionMap', 'recommendedOptionId', 'review'], 'decision');
  has(d.options[0], ['expectedCost', 'p10', 'p90', 'expectedRegret', 'winShareText', 'netBenefitVsFixNow'], 'option');
  has(d.decisionMap, ['xValues', 'yValues', 'cells', 'currentX', 'currentY', 'xName', 'yName'], 'map');
});

test('the trial request key the page sends matches the contract spelling', async () => {
  const page = await readFile(new URL('../src/pages/Lab.tsx', import.meta.url), 'utf8');
  assert.match(page, /leakM3H: size/);
});

test('forecast evidence and kpi fixtures carry what the pages need', async () => {
  has(await fx('forecast_evidence'), ['skill', 'coverage', 'nominal', 'baselines', 'byHour', 'scenario', 'liveCorrection', 'zones'], 'forecast evidence');
  const kpis = await fx('kpis');
  for (const k of kpis) assert.ok(['measured', 'model', 'assumed', 'calculated'].includes(k.evidence), `${k.id} carries an evidence label`);
});
