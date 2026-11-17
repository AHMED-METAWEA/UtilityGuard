import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import type { ForecastEvidence } from '../src/api/dash';
import { reliabilityChip, sortDayTypes, worstDayType } from '../src/lib/reliability';

const evidence = JSON.parse(await readFile(new URL('../../fixtures/rest/forecast_evidence.json', import.meta.url), 'utf8')) as ForecastEvidence;

test('forecast fixture verdict maps to a chip with text for every reliability label', () => {
  assert.ok(evidence.reliability);
  assert.equal(reliabilityChip(evidence.reliability.label).label.toLowerCase(), evidence.reliability.label);
  assert.deepEqual(reliabilityChip('reliable'), { cls: 'ok', label: 'Reliable' });
  assert.deepEqual(reliabilityChip('indicative'), { cls: 'warn', label: 'Indicative' });
  assert.deepEqual(reliabilityChip('unreliable'), { cls: 'crit', label: 'Unreliable' });
  assert.ok(evidence.reliability.checks.every((check) => check.name && check.detail && ['ok', 'weak', 'fail'].includes(check.status)));
});

test('day types sort worst first without changing the fixture or its figures', () => {
  assert.ok(evidence.byDayType?.length);
  const rows = evidence.byDayType;
  const before = structuredClone(rows);
  const sorted = sortDayTypes(rows);
  assert.notEqual(sorted, rows);
  assert.deepEqual(rows, before);
  assert.equal(sorted.length, rows.length);
  assert.equal(worstDayType(rows), sorted[0]);
  assert.equal(sorted[0].mape, Math.max(...rows.map((row) => row.mape)));
  assert.ok(sorted.every((row, i) => row === rows.find((original) => original.name === row.name) && (!i || sorted[i - 1].mape >= row.mape)));
});

test('legacy evidence, empty day types and tied errors are safe and deterministic', () => {
  const legacy = { ...evidence };
  delete legacy.reliability; delete legacy.byDayType;
  assert.deepEqual(sortDayTypes(legacy.byDayType ?? []), []);
  assert.equal(worstDayType(legacy.byDayType ?? []), undefined);
  const rows = evidence.byDayType!.map((row) => ({ ...row, mape: evidence.byDayType![0].mape }));
  assert.deepEqual(sortDayTypes(rows), rows);
  assert.equal(worstDayType(rows), rows[0]);
});
