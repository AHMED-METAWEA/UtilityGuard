import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import type { WhyRecord } from '../src/api/types';
import { RequestError } from '../src/api/client';
import { fetchWhy } from '../src/api/why';
import { describeRef, forecastRef, healthRef, isWhyRecord, kpiRef, riskRef, savingRef, warningRef, whyError } from '../src/lib/why';

const fixture = async (name: string): Promise<unknown> => JSON.parse(await readFile(new URL(`../../fixtures/rest/${name}.json`, import.meta.url), 'utf8'));
const kpi = await fixture('why_kpi');
const warning = await fixture('why_warning');

test('reference helpers preserve backend identifiers, including colons and reserved characters', () => {
  assert.equal(kpiRef('nrw'), 'kpi:nrw');
  assert.equal(healthRef('P-03'), 'asset-health:P-03');
  assert.equal(riskRef('P-03'), 'asset-risk:P-03');
  assert.equal(savingRef('pump_efficiency'), 'saving:pump_efficiency');
  for (const network of ['water', 'electricity', 'cooling']) assert.equal(forecastRef(network), `forecast:${network}`);
  assert.equal(warningRef('warn-0001'), 'warning:warn-0001');
  assert.equal(savingRef('district:A/B ?#%'), 'saving:district:A/B ?#%');
});

test('reference descriptions give readable labels and preserve asset identifiers', () => {
  assert.equal(describeRef(kpiRef('nrw')), 'Non-revenue water');
  for (const id of ['water_production', 'electric_load', 'cooling_efficiency', 'districts_served', 'savings_available']) assert.ok(!describeRef(kpiRef(id)).includes('_'));
  assert.equal(describeRef(healthRef('P-03')), 'Health of P-03');
  assert.equal(describeRef(riskRef('P-03')), 'Risk of P-03');
  assert.equal(describeRef(savingRef('pump_efficiency')), 'Monthly saving for pump efficiency');
  assert.equal(describeRef(forecastRef('water')), 'Water forecast error (MAPE)');
  assert.equal(describeRef(warningRef('warn-0001')), 'Early warning warn-0001');
  assert.equal(describeRef('asset-health:district:P-03'), 'Health of district:P-03');
  assert.equal(describeRef('kpi:future_metric'), 'future metric');
  assert.equal(describeRef('future:metric'), 'future:metric');
  assert.equal(describeRef('unregistered'), 'unregistered');
  assert.equal(describeRef(''), '');
});

test('real KPI and warning fixtures validate as WhyRecords with their provenance intact', () => {
  assert.ok(isWhyRecord(kpi)); assert.ok(isWhyRecord(warning));
  const records: WhyRecord[] = [kpi, warning];
  assert.equal(records[0].ref, kpiRef('nrw'));
  assert.equal(records[0].value, 10.3);
  assert.equal(records[1].ref, warningRef('warn-0001'));
  assert.equal(records[1].value, 10235.68);
  assert.ok(records[1].inputs.some((input) => input.source === 'assumed' && input.key === 'planned_factor' && input.status === 'planning' && input.basis.length));
  assert.ok(records[1].notes.some((note) => /synthetic/.test(note)));
  for (const record of records) {
    assert.ok(record.formula.length && record.steps.length && record.inputs.length && record.links.length);
    assert.ok(record.inputs.every((input) => input.basis.length));
    assert.ok(record.links.every((link) => link.href.startsWith('#/')));
  }
});

test('optional values and future fields are accepted while malformed explanation records are rejected', () => {
  assert.ok(isWhyRecord(kpi));
  const { value: _value, ...withoutValue } = kpi; void _value;
  assert.ok(isWhyRecord(withoutValue));
  for (const evidence of ['measured', 'calculated', 'model', 'assumed', 'forecast']) assert.ok(isWhyRecord({ ...kpi, evidence, future: true }));
  for (const record of [null, {}, { ...kpi, value: null }, { ...kpi, value: NaN }, { ...kpi, evidence: 'rule' },
    { ...kpi, computedAt: 'invalid' }, { ...kpi, steps: [42] }, { ...kpi, notes: null }, { ...kpi, links: [{}] },
    { ...kpi, inputs: [{ name: 'x', value: 1, unit: '%', source: 'model', basis: 'x' }] },
    { ...kpi, inputs: [{ ...kpi.inputs[0], value: Infinity }] }, { ...kpi, inputs: [{ ...kpi.inputs[0], basis: null }] },
    { ...kpi, inputs: [{ ...kpi.inputs[0], key: 42 }] }, { ...kpi, inputs: [{ ...kpi.inputs[0], status: 42 }] },
  ]) assert.equal(isWhyRecord(record), false);
});

test('missing explanations have a friendly message and other server errors retain their message', () => {
  assert.equal(whyError(new RequestError('no_explanation', 'Unknown reference')), 'There is no explanation for this number yet.');
  assert.equal(whyError(new RequestError('404', 'Not Found')), 'There is no explanation for this number yet.');
  assert.equal(whyError(new RequestError('busy', 'Model is loading')), 'Model is loading');
  assert.equal(whyError(new Error('Connection lost')), 'Connection lost');
  assert.equal(whyError(null), 'Could not load this explanation.');
});

test('fetchWhy encodes reserved path characters, preserves colons, passes cancellation and keeps typed server errors', async (t) => {
  const abort = new AbortController();
  const fetchMock = t.mock.method(globalThis, 'fetch', async (path: string | URL | Request, options?: RequestInit) => {
    assert.equal(path, '/api/v1/why/saving:district:A%2FB%20%3F%23%25');
    assert.equal(options?.signal, abort.signal);
    return new Response(JSON.stringify(kpi), { status: 200 });
  });
  assert.deepEqual(await fetchWhy('saving:district:A/B ?#%', abort.signal), kpi);
  fetchMock.mock.mockImplementation(async () => new Response(JSON.stringify({ code: 'no_explanation', message: 'Unknown reference' }), { status: 404 }));
  await assert.rejects(fetchWhy('kpi:missing'), (error: unknown) => error instanceof RequestError && error.code === 'no_explanation' && whyError(error) === 'There is no explanation for this number yet.');
  fetchMock.mock.mockImplementation(async () => new Response(JSON.stringify({}), { status: 200 }));
  await assert.rejects(fetchWhy('kpi:nrw'), /invalid explanation/);
  fetchMock.mock.restore();
});
