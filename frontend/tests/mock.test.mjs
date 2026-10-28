import test from 'node:test';
import assert from 'node:assert/strict';
import { IncomingMessage, ServerResponse } from 'node:http';
import { Duplex } from 'node:stream';
import { once } from 'node:events';
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { createMockServer } from '../mock-server/index.mjs';

// Exercise Express and its real HTTP serialization without binding a network port.
async function invoke(app, method, url, body) {
  const chunks = [];
  const socket = new Duplex({ read() {}, write(chunk, _encoding, callback) { chunks.push(Buffer.from(chunk)); callback(); } });
  const req = new IncomingMessage(socket);
  req.method = method; req.url = url; req.headers = { host: 'localhost', 'content-type': 'application/json' };
  req.complete = true;
  const data = body === undefined ? '' : JSON.stringify(body);
  req.headers['content-length'] = Buffer.byteLength(data).toString();
  const res = new ServerResponse(req); res.assignSocket(socket);
  const complete = once(res, 'finish', { signal: AbortSignal.timeout(3000) });
  app(req, res); if (data) req.push(data); req.push(null);
  await complete;
  const raw = Buffer.concat(chunks).toString();
  socket.destroy();
  return { status: res.statusCode, body: JSON.parse(raw.slice(raw.indexOf('\r\n\r\n') + 4)) };
}

test('mock serves every shared REST fixture, acknowledgements and structured missing-file errors', async () => {
  const mock = createMockServer();
  try {
    for (const path of ['/topology', '/health', '/alerts', '/incidents', '/scenarios', '/forecast?network=water', '/metrics', '/assumptions', '/compare', '/drift', '/sensor-placement', '/calculations/calc-0001']) {
      const response = await invoke(mock.app, 'GET', `/api/v1${path}`);
      assert.equal(response.status, 200, path);
      assert.ok(response.body);
    }
    assert.equal((await invoke(mock.app, 'PUT', '/api/v1/assumptions', {})).status, 200);
    assert.equal((await invoke(mock.app, 'POST', '/api/v1/alerts/alert-0001/acknowledge', {})).body.state, 'acknowledged');
    assert.equal((await invoke(mock.app, 'POST', '/api/v1/alerts/unknown/acknowledge', {})).status, 404);
    const report = await invoke(mock.app, 'GET', '/api/v1/incidents/inc-0001/report');
    assert.equal(report.status, 404); assert.equal(report.body.code, 'FIXTURE_NOT_FOUND');
  } finally { mock.close(); }
  const empty = createMockServer({ fixtureRoot: fileURLToPath(new URL('./does-not-exist', import.meta.url)) });
  try {
    const response = await invoke(empty.app, 'GET', '/api/v1/topology');
    assert.equal(response.status, 404); assert.equal(response.body.code, 'FIXTURE_NOT_FOUND');
    assert.equal(typeof response.body.message, 'string');
  } finally { empty.close(); }
});

test('mock sends a snapshot first and produces incremented noisy reading batches every two seconds', async () => {
  const mock = createMockServer(); const messages = [];
  const socket = { readyState: 1, snapshotSent: false, send(raw) { messages.push(JSON.parse(raw)); }, on() {}, close() {}, terminate() {} };
  mock.wss.clients.add(socket);
  try {
    mock.wss.emit('connection', socket);
    await new Promise((resolve) => setTimeout(resolve, 40));
    assert.equal(messages[0].type, 'snapshot');
    await new Promise((resolve) => setTimeout(resolve, 2050));
    assert.equal(messages[1].type, 'reading');
    assert.ok(messages[1].payload.tick > messages[0].payload.tick);
    assert.ok(messages[1].payload.readings.length > 0);
    assert.ok(Number.isFinite(messages[1].payload.readings[0].value));
    assert.equal(messages[1].payload.readings[0].timestamp, messages[1].payload.timestamp);
  } finally { mock.close(); }
});

test('scenario run replays optional ordered messages and stop cancels pending events', async () => {
  const dir = await mkdtemp(fileURLToPath(new URL('./.mock-fixtures-', import.meta.url)));
  await Promise.all(['rest', 'ws', 'sequence'].map((path) => mkdir(`${dir}/${path}`)));
  await writeFile(`${dir}/rest/scenario_run.json`, JSON.stringify({ runId: 'run-test' }));
  await writeFile(`${dir}/ws/scenario_state.json`, JSON.stringify({ type: 'scenario_state', payload: { runId: 'run-test', scenarioId: 'scn-test', state: 'running', startedAt: '2026-10-28T00:00:00Z' } }));
  await writeFile(`${dir}/sequence/burst.json`, JSON.stringify([
    { delayMs: 0, message: { type: 'test', payload: { order: 1 } } },
    { delayMs: 100, message: { type: 'test', payload: { order: 2 } } },
  ]));
  const mock = createMockServer({ fixtureRoot: dir }); const messages = [];
  mock.wss.clients.add({ readyState: 1, snapshotSent: true, send(raw) { messages.push(JSON.parse(raw)); }, terminate() {} });
  try {
    assert.equal((await invoke(mock.app, 'POST', '/api/v1/scenarios/run', { scenarioId: 'scn-test' })).body.runId, 'run-test');
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.equal(messages[0].payload.order, 1);
    await invoke(mock.app, 'POST', '/api/v1/scenarios/stop', { runId: 'run-test' });
    await new Promise((resolve) => setTimeout(resolve, 130));
    assert.deepEqual(messages.map((m) => m.type), ['test', 'scenario_state']);
    assert.equal(messages[1].payload.state, 'stopped');
  } finally { mock.close(); await rm(dir, { recursive: true, force: true }); }
});
