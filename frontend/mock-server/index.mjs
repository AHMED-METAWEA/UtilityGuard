import express from 'express';
import { WebSocketServer, WebSocket } from 'ws';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
// Override only for isolated tests. The default is the repository's shared fixtures/.
const defaultFixtureRoot = resolve(process.env.UTILITYGUARD_FIXTURES ?? resolve(here, '../../fixtures'));
const port = Number(process.env.PORT ?? 8000);

export function createMockServer({ fixtureRoot = defaultFixtureRoot } = {}) {
const app = express();
const server = createServer(app);
const wss = new WebSocketServer({ noServer: true });
const timers = new Set();
const chatTimers = new Set();
let tick = 0;
let currentSnapshot = null;
let assumptionValues = null;
let chatCount = 0;
let runCount = 0;
app.use(express.json({ limit: '1mb' }));

async function readFixture(name) {
  try { return JSON.parse(await readFile(resolve(fixtureRoot, name), 'utf8')); }
  catch (error) {
    const missing = error.code === 'ENOENT';
    const failure = new Error(missing ? `Fixture not found: ${name}` : `Invalid fixture: ${name}`);
    failure.status = missing ? 404 : 500;
    failure.code = missing ? 'FIXTURE_NOT_FOUND' : 'INVALID_FIXTURE';
    throw failure;
  }
}

const route = (method, path, filename) => app[method](`/api/v1${path}`, async (_req, res, next) => {
  try { res.json(await readFixture(`rest/${filename}.json`)); } catch (error) { next(error); }
});
for (const name of ['topology', 'health', 'alerts', 'incidents', 'scenarios', 'forecast', 'metrics', 'compare', 'drift']) route('get', `/${name}`, name);
route('get', '/sensor-placement', 'sensor_placement');
route('get', '/calculations/:id', 'calculation');
app.get('/api/v1/findings', (_req, res) => res.json(currentSnapshot?.findings ?? []));
app.get('/api/v1/assumptions', async (_req, res, next) => {
  try { assumptionValues ??= await readFixture('rest/assumptions.json'); res.json(assumptionValues); } catch (error) { next(error); }
});
app.put('/api/v1/assumptions', async (req, res, next) => {
  try {
    assumptionValues ??= await readFixture('rest/assumptions.json');
    if (!req.body || Array.isArray(req.body) || typeof req.body !== 'object' || Object.entries(req.body).some(([key, value]) => !assumptionValues.some((a) => a.key === key) || typeof value !== 'number' || !Number.isFinite(value))) return res.status(422).json({ code: 'INVALID_ASSUMPTIONS', message: 'Provide known assumption keys with finite numeric values.' });
    assumptionValues = assumptionValues.map((a) => Object.hasOwn(req.body, a.key) ? { ...a, value: req.body[a.key], userChanged: true } : a);
    // Replay the recorded answer without implementing a second cost model in the frontend mock.
    const findings = currentSnapshot?.findings ?? [];
    const updates = findings.map((finding) => ({ ...finding, explain: { ...finding.explain, assumptions: assumptionValues }, cost: { ...finding.cost, explanation: { ...finding.cost.explanation, assumptions: assumptionValues } } }));
    res.json({ assumptions: assumptionValues, recomputed: updates.length });
    for (const finding of updates) broadcast({ type: 'agent_finding', payload: finding });
  } catch (error) { next(error); }
});

function envelope(data, type) { return data?.type && data?.payload ? data : { type, payload: data }; }
function send(socket, message) { if (socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify(message)); }
function remember(message) {
  if (!currentSnapshot) return;
  const p = message.payload;
  const upsert = (key, id, field) => { currentSnapshot[key] = [...(currentSnapshot[key] ?? []).filter((item) => item[field] !== p[id]), p]; };
  if (message.type === 'alert') upsert('alerts', 'id', 'id');
  if (message.type === 'agent_finding') upsert('findings', 'id', 'id');
  if (message.type === 'asset_status') upsert('assets', 'assetId', 'assetId');
  if (message.type === 'scenario_state') upsert('scenarios', 'runId', 'runId');
  if (message.type === 'reading') { const latest = new Map(currentSnapshot.latest.map((r) => [r.sensorId, r])); p.readings.forEach((r) => latest.set(r.sensorId, r)); currentSnapshot.latest = [...latest.values()]; currentSnapshot.tick = p.tick; currentSnapshot.timestamp = p.timestamp; }
}
function broadcast(message) { remember(message); for (const socket of wss.clients) if (socket.snapshotSent) send(socket, message); }
function stopSequence() { for (const timer of timers) clearTimeout(timer); timers.clear(); }

app.post('/api/v1/scenarios/run', async (req, res, next) => {
  try {
    const fixture = await readFixture('rest/scenario_run.json');
    const response = { ...fixture, runId: ++runCount === 1 ? fixture.runId : `run-mock-${runCount}`, scenarioId: req.body.scenarioId ?? fixture.scenarioId, startedAt: new Date().toISOString() };
    if (!currentSnapshot) { try { currentSnapshot = envelope(await readFixture('ws/snapshot.json'), 'snapshot').payload; } catch { /* Isolated sequence tests may omit the snapshot. */ } }
    let sequence;
    try { sequence = await readFixture('sequence/burst.json'); }
    catch (error) { if (error.status !== 404) throw error; }
    if (req.body.scenarioId && !['scn-pipe-burst', 'scn-test'].includes(req.body.scenarioId)) sequence = [];
    if (sequence && !Array.isArray(sequence)) throw Object.assign(new Error('Scenario sequence must be an array'), { status: 500, code: 'INVALID_FIXTURE' });
    stopSequence();
    if (!sequence?.length) broadcast({ type: 'scenario_state', payload: { ...response, state: 'running' } });
    // delayMs is relative to the preceding entry; retain fixture ordering.
    let elapsed = 0;
    for (const entry of sequence ?? []) {
      elapsed += Math.max(0, Number(entry.delayMs) || 0);
      const timer = setTimeout(() => {
        timers.delete(timer);
        if (entry.message?.type === 'reading') {
          tick = Math.max(tick + 1, Number(entry.message.payload?.tick) || 0);
          broadcast({ ...entry.message, payload: { ...entry.message.payload, tick } });
        } else if (entry.message?.type === 'scenario_state') broadcast({ type: 'scenario_state', payload: { ...entry.message.payload, ...response } });
        else broadcast(entry.message);
      }, elapsed);
      timers.add(timer);
    }
    res.json(response);
  } catch (error) { next(error); }
});

app.post('/api/v1/scenarios/stop', async (req, res, next) => {
  try {
    const fixture = envelope(await readFixture('ws/scenario_state.json'), 'scenario_state');
    stopSequence();
    const active = currentSnapshot?.scenarios?.find((run) => run.runId === req.body.runId);
    const message = { type: 'scenario_state', payload: { ...fixture.payload, ...active, runId: req.body.runId ?? fixture.payload.runId, state: 'stopped' } };
    broadcast(message);
    if (currentSnapshot) {
      const baseline = envelope(await readFixture('ws/snapshot.json'), 'snapshot').payload;
      for (const asset of baseline.assets) broadcast({ type: 'asset_status', payload: asset });
      for (const alert of currentSnapshot.alerts.filter((a) => a.state !== 'resolved')) broadcast({ type: 'alert', payload: { ...alert, state: 'resolved' } });
      broadcast({ type: 'reading', payload: { tick: ++tick, timestamp: new Date().toISOString(), readings: baseline.latest } });
    }
    res.json(message.payload);
  } catch (error) { next(error); }
});
app.post('/api/v1/scenarios/reset', async (_req, res, next) => {
  try {
    const snapshot = envelope(await readFixture('ws/snapshot.json'), 'snapshot');
    const baseline = Number(snapshot.payload.tick) || 0;
    stopSequence(); tick = baseline; currentSnapshot = snapshot.payload;
    // Reconnect so clients receive a new authoritative snapshot before resetting ticks.
    for (const socket of wss.clients) socket.close(1012, 'Twin baseline reset');
    res.json({ status: 'reset' });
  } catch (error) { next(error); }
});

app.post('/api/v1/chat', async (_req, res, next) => {
  try {
    const fixture = await readFixture('rest/chat_accepted.json');
    const accepted = { messageId: ++chatCount === 1 ? fixture.messageId : `msg-mock-${chatCount}` };
    const routing = envelope(await readFixture('ws/chat_route.json'), 'chat_route');
    const chunk = envelope(await readFixture('ws/chat_chunk.json'), 'chat_chunk');
    const done = envelope(await readFixture('ws/chat_done.json'), 'chat_done');
    res.status(202).json(accepted);
    const routeTimer = setTimeout(() => { chatTimers.delete(routeTimer); broadcast({ ...routing, payload: { ...routing.payload, messageId: accepted.messageId } }); }, 50);
    chatTimers.add(routeTimer);
    const chunkTimer = setTimeout(() => { chatTimers.delete(chunkTimer); broadcast({ ...chunk, payload: { ...chunk.payload, messageId: accepted.messageId } }); }, 250);
    const doneTimer = setTimeout(() => { chatTimers.delete(doneTimer); broadcast({ ...done, payload: { ...done.payload, messageId: accepted.messageId } }); }, 700);
    chatTimers.add(chunkTimer); chatTimers.add(doneTimer);
  } catch (error) { next(error); }
});
app.post('/api/v1/alerts/:id/acknowledge', async (req, res, next) => {
  try {
    const fixture = await readFixture('rest/alerts.json');
    const alert = (Array.isArray(fixture) ? fixture : fixture.alerts ?? []).find((item) => item.id === req.params.id);
    if (!alert) return res.status(404).json({ code: 'ALERT_NOT_FOUND', message: `Alert not found: ${req.params.id}` });
    const acknowledged = { ...alert, state: 'acknowledged' };
    broadcast({ type: 'alert', payload: acknowledged });
    res.json({ id: alert.id, state: 'acknowledged' });
  } catch (error) { next(error); }
});
app.get('/api/v1/incidents/:id/report', (_req, res) => res.status(404).json({ code: 'FIXTURE_NOT_FOUND', message: 'No PDF report fixture is specified for Phase 0' }));
app.use((_req, res) => res.status(404).json({ code: 'NOT_FOUND', message: 'Unknown mock endpoint' }));
app.use((error, _req, res, next) => {
  if (res.headersSent) return next(error);
  res.status(error.status ?? 500).json({ code: error.code ?? 'MOCK_ERROR', message: error.message });
});

server.on('upgrade', (req, socket, head) => {
  if (new URL(req.url, 'http://localhost').pathname !== '/ws/stream') { socket.destroy(); return; }
  wss.handleUpgrade(req, socket, head, (ws) => wss.emit('connection', ws));
});
wss.on('connection', async (socket) => {
  socket.snapshotSent = false;
  socket.on('error', () => { /* The client handles reconnecting after socket failure. */ });
  try {
    const snapshot = envelope(await readFixture('ws/snapshot.json'), 'snapshot');
    tick = Math.max(tick, Number(snapshot.payload.tick) || 0);
    currentSnapshot ??= snapshot.payload;
    send(socket, { type: 'snapshot', payload: currentSnapshot }); socket.snapshotSent = true;
    if (socket.readyState !== WebSocket.OPEN) return;
  } catch (error) { socket.close(1013, error.code); }
});

let readingBusy = false;
const readingTimer = setInterval(async () => {
  if (readingBusy || timers.size || ![...wss.clients].some((client) => client.snapshotSent)) return;
  readingBusy = true;
  try {
    const fixture = envelope(await readFixture('ws/reading.json'), 'reading');
    const payload = fixture.payload;
    tick = Math.max(tick, Number(payload.tick) || 0) + 1;
    const timestamp = new Date().toISOString();
    broadcast({ type: 'reading', payload: { ...payload, tick, timestamp, readings: (payload.readings ?? []).map((reading) => ({ ...reading, timestamp, value: reading.quality === 'missing' ? reading.value : reading.value + (Math.random() - 0.5) * Math.max(Math.abs(reading.value) * .01, .01) })) } });
  } catch (error) { if (error.status !== 404) console.error(error.message); }
  finally { readingBusy = false; }
}, 2000);

function close() {
  clearInterval(readingTimer); stopSequence();
  for (const timer of chatTimers) clearTimeout(timer);
  chatTimers.clear();
  for (const socket of wss.clients) socket.terminate();
  wss.close();
  if (server.listening) server.close();
}
return { app, server, wss, close };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const mock = createMockServer();
  mock.server.on('error', (error) => { console.error(`Mock server failed: ${error.message}`); mock.close(); process.exitCode = 1; });
  mock.server.listen(port, () => console.log(`UtilityGuard mock: http://localhost:${port} · fixtures: ${defaultFixtureRoot}`));
  process.on('SIGINT', () => mock.close());
  process.on('SIGTERM', () => mock.close());
}
