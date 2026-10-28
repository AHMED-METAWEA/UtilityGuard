import test from 'node:test';
import assert from 'node:assert/strict';
import { useStore } from '../src/store';
import { parseEnvelope, StreamClient, type StreamSocket } from '../src/api/ws';
import type { Reading, SnapshotPayload } from '../src/api/types';
import { readFile } from 'node:fs/promises';

const timestamp = '2026-10-28T12:00:00.000Z';
const reading = (sensorId = 'water-pump-03-pressure', value = 3): Reading => ({ sensorId, value, timestamp, quality: 'ok' });
const snapshot = (tick = 10): SnapshotPayload => ({ tick, timestamp, latest: [reading()] });
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
  for (const name of ['snapshot', 'reading', 'asset_status', 'alert', 'agent_finding', 'scenario_state', 'forecast_update', 'chat_chunk', 'chat_done']) {
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
