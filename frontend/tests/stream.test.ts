import test from 'node:test';
import assert from 'node:assert/strict';
import { useStore } from '../src/store';
import { parseEnvelope, StreamClient, type StreamSocket } from '../src/api/ws';
import type { Reading, SnapshotPayload } from '../src/api/types';
import { readFile } from 'node:fs/promises';

const timestamp = '2026-10-28T12:00:00.000Z';
const reading = (sensorId = 'water-pump-03-pressure', value = 3): Reading => ({ sensorId, value, timestamp, quality: 'ok' });
const snapshot = (tick = 10): SnapshotPayload => ({ tick, timestamp, latest: [reading()], topology: { zones: [], assets: [], sensors: [], links: [] }, assets: [], alerts: [], incidents: [], findings: [], scenarios: [], llm: 'live' });
const batch = (tick: number, value: number) => ({ tick, timestamp, readings: [reading(undefined, value)] });

test('stale and duplicate ticks are dropped per sensor, rather than globally', () => {
  useStore.getState().applySnapshot(snapshot());
  const store = useStore.getState();
  store.ingestReadings(batch(12, 12)); store.ingestReadings(batch(11, 11)); store.ingestReadings(batch(12, 99));
  store.ingestReadings({ tick: 11, timestamp, readings: [reading('other-sensor', 5)] });
  assert.deepEqual(useStore.getState().readings['water-pump-03-pressure'].map((r) => r.value), [3, 12]);
  assert.equal(useStore.getState().readings['other-sensor'][0].value, 5);
});
test('ring buffer holds the most recent 300 readings and a reconnect replaces baseline state', () => {
  useStore.getState().applySnapshot(snapshot(0));
  for (let tick = 1; tick <= 350; tick++) useStore.getState().ingestReadings(batch(tick, tick));
  assert.equal(useStore.getState().readings['water-pump-03-pressure'].length, 300);
  assert.equal(useStore.getState().readings['water-pump-03-pressure'][0].value, 51);
  useStore.getState().applySnapshot(snapshot(1));
  assert.equal(useStore.getState().readings['water-pump-03-pressure'].length, 1);
  assert.equal(useStore.getState().sensorTicks['water-pump-03-pressure'], 1);
});
test('invalid JSON, unknown types and malformed known messages are ignored; extra fields are accepted', () => {
  assert.equal(parseEnvelope('{'), null);
  assert.equal(parseEnvelope(JSON.stringify({ type: 'future_message', payload: { value: 1 } })), null);
  assert.equal(parseEnvelope(JSON.stringify({ type: 'reading', payload: { tick: 2, readings: null } })), null);
  assert.equal(parseEnvelope(JSON.stringify({ type: 'reading', extra: true, payload: { ...batch(5, 5), futureField: 'hello' } }))?.type, 'reading');
});
test('all shared WS fixtures use accepted envelopes; snapshot hydrates actual fixture fields', async () => {
  for (const name of ['snapshot', 'reading', 'asset_status', 'alert', 'agent_finding', 'scenario_state', 'forecast_update', 'chat_route', 'chat_chunk', 'chat_done']) {
    const raw = await readFile(new URL(`../../fixtures/ws/${name}.json`, import.meta.url), 'utf8');
    const message = parseEnvelope(raw);
    assert.equal(message?.type, name);
    if (message?.type === 'snapshot') {
      useStore.getState().applySnapshot(message.payload);
      assert.equal(Object.keys(useStore.getState().readings).length, message.payload.latest.length);
      assert.equal(Object.keys(useStore.getState().assets).length, message.payload.assets?.length);
      assert.equal(useStore.getState().health?.llm, message.payload.llm);
    }
  }
});
test('socket applies the snapshot before early events, and stop detaches handlers', () => {
  const socket: StreamSocket = { onopen: null, onmessage: null, onclose: null, onerror: null, close() {} };
  useStore.getState().applySnapshot(snapshot());
  const client = new StreamClient('ws://test/ws/stream', () => socket);
  client.start();
  const emit = (message: unknown) => socket.onmessage?.({ data: JSON.stringify(message) });
  emit({ type: 'reading', payload: batch(12, 12) });
  assert.equal(useStore.getState().snapshotReady, false);
  emit({ type: 'snapshot', payload: snapshot(10) });
  assert.equal(useStore.getState().connection, 'connected');
  assert.deepEqual(useStore.getState().readings['water-pump-03-pressure'].map((r) => r.value), [3, 12]);
  emit({ type: 'snapshot', payload: snapshot(0) });
  assert.equal(useStore.getState().sensorTicks['water-pump-03-pressure'], 12);
  client.stop(); assert.equal(socket.onmessage, null); assert.equal(socket.onclose, null);
});
test('reconnect waits for a new snapshot and refreshes state before accepting new ticks', async () => {
  const sockets: StreamSocket[] = [];
  const client = new StreamClient('ws://test', () => {
    const socket: StreamSocket = { onopen: null, onmessage: null, onclose: null, onerror: null, close() {} };
    sockets.push(socket); return socket;
  });
  client.start();
  sockets[0].onmessage?.({ data: JSON.stringify({ type: 'snapshot', payload: snapshot(100) }) });
  sockets[0].onclose?.();
  assert.equal(useStore.getState().connection, 'reconnecting');
  assert.equal(useStore.getState().snapshotReady, false);
  await new Promise((resolve) => setTimeout(resolve, 1300));
  assert.equal(sockets.length, 2);
  sockets[1].onmessage?.({ data: JSON.stringify({ type: 'reading', payload: batch(2, 2) }) });
  sockets[1].onmessage?.({ data: JSON.stringify({ type: 'snapshot', payload: snapshot(1) }) });
  assert.equal(useStore.getState().sensorTicks['water-pump-03-pressure'], 2);
  client.stop();
});

test('chat_route creates the reply before chunks, tracks the active specialist and preserves calculation links', async () => {
  const { dispatchMessage } = await import('../src/api/ws');
  useStore.setState({ chat: { data: [], status: 'ready' } });
  for (const name of ['chat_route', 'chat_chunk', 'chat_done']) {
    const message = parseEnvelope(await readFile(new URL(`../../fixtures/ws/${name}.json`, import.meta.url), 'utf8'));
    assert.ok(message);
    dispatchMessage(message);
    const reply = useStore.getState().chat.data[0];
    if (name === 'chat_route') { assert.equal(reply.text, ''); assert.equal(reply.routing.length, 3); assert.equal(reply.activeAgent, 'electricity'); assert.equal(reply.complete, false); }
    if (name === 'chat_chunk') { assert.equal(reply.activeAgent, 'orchestrator'); assert.equal(reply.routing.length, 3); assert.ok(reply.text.includes('SS-02')); }
    if (name === 'chat_done') { assert.equal(reply.complete, true); assert.equal(reply.calculationId, 'calc-0001'); assert.deepEqual(reply.agentsConsulted, ['electricity', 'water', 'cooling']); }
  }
  assert.equal(useStore.getState().chat.data.length, 1);
});

test('burst fixture sequence updates the open counter, statuses, findings and resolution over three rehearsals', async () => {
  const { dispatchMessage } = await import('../src/api/ws');
  const { isOpenAlert } = await import('../src/presentation');
  const baseline = parseEnvelope(await readFile(new URL('../../fixtures/ws/snapshot.json', import.meta.url), 'utf8'))!;
  const sequence = JSON.parse(await readFile(new URL('../../fixtures/sequence/burst.json', import.meta.url), 'utf8'));
  for (let rehearsal = 0; rehearsal < 3; rehearsal++) {
    dispatchMessage(baseline);
    for (const entry of sequence) {
      const message = parseEnvelope(JSON.stringify(entry.message)); assert.ok(message); dispatchMessage(message);
      if (message.type === 'alert') {
        const expected = message.payload.state === 'resolved' ? 0 : 1;
        assert.equal(useStore.getState().alerts.data.filter(isOpenAlert).length, expected);
        if (expected) { useStore.getState().upsertAlert({ ...message.payload, state: 'acknowledged' }); assert.equal(useStore.getState().alerts.data.filter(isOpenAlert).length, 1); }
      }
    }
    assert.equal(useStore.getState().findings.data.length, 1);
    const finding = useStore.getState().findings.data[0];
    const recomputed = { ...finding, headline: 'Recomputed decision', cost: { ...finding.cost, expectedSaving: 12345 } };
    useStore.getState().upsertFinding(recomputed);
    assert.equal(useStore.getState().findings.data.length, 1);
    assert.equal(useStore.getState().findings.data[0].cost.expectedSaving, 12345);
    assert.equal(useStore.getState().alerts.data.filter(isOpenAlert).length, 0);
    assert.ok(Object.values(useStore.getState().scenarioRuns).every((run) => run.state === 'stopped'));
  }
});

test('formula view substitutes supplied inputs without calculating or inventing missing inputs', async () => {
  const { keyCalculations, substitutedFormula } = await import('../src/presentation');
  const record = JSON.parse(await readFile(new URL('../../fixtures/rest/calculation.json', import.meta.url), 'utf8'));
  const calculations = record.explanation.calculations;
  assert.deepEqual(keyCalculations(calculations).map((c) => c.name), ['Water loss per hour', 'Cost of delay per hour', 'Saving from acting now']);
  assert.equal(substitutedFormula(calculations[0]), '140 m3/h × 8 EGP/m3');
  assert.equal(substitutedFormula(calculations[2]), '3,400 EGP + 15,000 EGP + equipment');
  assert.equal(calculations[0].result, 1120);
});
