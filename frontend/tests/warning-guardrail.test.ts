import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import type { EarlyWarning, GuardrailFigure, GuardrailResult, SnapshotPayload } from '../src/api/types';
import { dispatchMessage, isEarlyWarning, parseEnvelope } from '../src/api/ws';
import { useStore } from '../src/store';
import { PRESETS, segment, summarize } from '../src/lib/guardrail';
import { warningTitle } from '../src/lib/warning';

const fixture = async (path: string) => JSON.parse(await readFile(new URL(`../../fixtures/${path}.json`, import.meta.url), 'utf8'));
const envelope = await fixture('ws/early_warning');
const warning = envelope.payload as EarlyWarning;
const probe = await fixture('rest/guardrail_probe') as GuardrailResult;

test('early warning WS and REST fixtures validate, including lifecycle states and additive fields', async () => {
  assert.deepEqual(parseEnvelope(JSON.stringify(envelope)), envelope);
  const warnings = await fixture('rest/warnings') as EarlyWarning[];
  assert.ok(warnings.every(isEarlyWarning));
  for (const state of ['active', 'cleared', 'failed']) {
    assert.ok(parseEnvelope(JSON.stringify({ ...envelope, payload: { ...warning, state, leadTimeSeconds: 240, extra: 'future' } })));
  }
});

test('malformed warning fields, signals, chart points and costs are rejected', () => {
  for (const patch of [
    { state: 'broken' }, { assetName: 3 }, { network: 'gas' }, { whyRef: null },
    { risk: 'high' }, { risk: 1.1 }, { peerRisk: -1 }, { createdAt: 'not-a-date' },
    { risePerMinute: 'soon' }, { minutesTo90: false }, { leadTimeSeconds: 'four' },
    { signals: [{}] }, { signals: [{ ...warning.signals[0], peerValue: 'low' }] },
    { series: [{ ...warning.series[0], peer: 'low' }] }, { series: [{ ...warning.series[0], t: 'invalid' }] },
    { cost: { ...warning.cost, verdict: 'schedule' } }, { cost: { ...warning.cost, planned: 'free' } },
  ]) assert.equal(parseEnvelope(JSON.stringify({ ...envelope, payload: { ...warning, ...patch } })), null, JSON.stringify(patch));
  assert.equal(isEarlyWarning({ ...warning, excess: Infinity }), false);
});

test('warnings replace by id, keep createdAt order on updates, and setWarnings replaces the record', () => {
  const store = useStore.getState(); store.clearWarnings();
  store.setWarnings([warning]);
  const newer = { ...warning, id: 'warn-new', createdAt: '2026-10-28T12:00:00Z' };
  const older = { ...warning, id: 'warn-old', createdAt: '2026-10-28T10:00:00Z' };
  store.upsertWarning(newer); store.upsertWarning(older);
  const update = { ...warning, state: 'failed' as const, leadTimeSeconds: 240, updatedAt: '2026-10-28T13:00:00Z' };
  const parsed = parseEnvelope(JSON.stringify({ ...envelope, payload: update })); assert.ok(parsed);
  dispatchMessage(parsed); dispatchMessage(parsed);
  assert.deepEqual(Object.keys(useStore.getState().warnings), ['warn-new', warning.id, 'warn-old']);
  assert.deepEqual(useStore.getState().warnings[warning.id], update);
  store.setWarnings([older, newer, { ...newer, state: 'cleared' }]);
  assert.deepEqual(Object.keys(useStore.getState().warnings), ['warn-new', 'warn-old']);
  assert.equal(useStore.getState().warnings[newer.id].state, 'cleared');
});

test('clear and new snapshots discard warnings just like boards', async () => {
  const store = useStore.getState(); store.setWarnings([warning]); store.clearWarnings();
  assert.deepEqual(useStore.getState().warnings, {});
  store.upsertWarning(warning);
  store.applySnapshot((await fixture('ws/snapshot')).payload as SnapshotPayload);
  assert.deepEqual(useStore.getState().warnings, {});
});

test('real guardrail output segments losslessly and marks exactly the removed figures', () => {
  const pieces = segment(probe.raw, probe.figures);
  assert.equal(pieces.map((piece) => piece.text).join(''), probe.raw);
  assert.deepEqual(pieces.flatMap((piece) => piece.figure?.verdict === 'removed' ? [piece.figure] : []), probe.figures.filter((figure) => figure.verdict === 'removed'));
  assert.equal(pieces.map((piece) => piece.figure?.verdict === 'removed' ? '[figure removed]' : piece.text).join(''), probe.checked);
});

test('sequential segmentation distinguishes repeated numbers and skips unmatched and empty figures', () => {
  const figures: GuardrailFigure[] = [
    { text: '12', value: 12, verdict: 'verified', matched: 'cost of acting now', matchedValue: 12 },
    { text: 'missing', value: 0, verdict: 'removed' },
    { text: '', value: 0, verdict: 'removed' },
    { text: '12', value: 12, verdict: 'removed' },
  ];
  const raw = 'Cost 12 EGP; claim 12 EGP.';
  const pieces = segment(raw, figures);
  assert.equal(pieces.map((piece) => piece.text).join(''), raw);
  assert.deepEqual(pieces.filter((piece) => piece.figure).map((piece) => piece.figure), [figures[0], figures[3]]);
  assert.deepEqual(segment('', figures), []);
  assert.deepEqual(segment('plain text', []), [{ text: 'plain text' }]);
  assert.deepEqual(segment('12', [figures[0]]), [{ text: '12', figure: figures[0] }]);
});

test('summary handles zero, singular and plural counts', () => {
  assert.equal(summarize(probe), '4 figures checked against 39 calculated values; 4 removed');
  assert.equal(summarize({ ...probe, figures: probe.figures.slice(0, 1), allowedCount: 1, removed: 0 }), '1 figure checked against 1 calculated value; 0 removed');
  assert.equal(summarize({ ...probe, figures: [], allowedCount: 0, removed: 0 }), '0 figures checked against 0 calculated values; 0 removed');
  assert.equal(PRESETS.length, 3); assert.ok(PRESETS.every((prompt) => prompt.length > 0 && prompt.length <= 600));
});

test('warning titles reflect state and measured lead time without inventing a failure horizon', () => {
  assert.equal(warningTitle(warning), 'P-03 is wearing out');
  assert.equal(warningTitle({ ...warning, state: 'cleared' }), 'Early warning cleared');
  assert.equal(warningTitle({ ...warning, state: 'failed', leadTimeSeconds: 240 }), 'P-03 failed 4.0 min after the warning');
  assert.equal(warningTitle({ ...warning, state: 'failed', leadTimeSeconds: 90 }), 'P-03 failed 1.5 min after the warning');
  assert.equal(warningTitle({ ...warning, state: 'failed' }), 'P-03 failed after the warning');
});
