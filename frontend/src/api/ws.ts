import { useStore } from '../store';
import type { WsEnvelope } from './types';

const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === 'string';
const number = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const list = (value: unknown): value is unknown[] => Array.isArray(value);
const agent = (value: unknown) => ['water', 'electricity', 'cooling', 'orchestrator'].includes(String(value));
const routing = (value: unknown) => list(value) && value.every((step) => record(step) && agent(step.agent) && text(step.reason));
const calculation = (value: unknown) => record(value) && text(value.name) && text(value.formula) && number(value.result) && text(value.unit) && list(value.inputs) && value.inputs.every((input) => record(input) && text(input.name) && number(input.value) && text(input.unit) && text(input.source));
const explanation = (value: unknown) => record(value) && number(value.confidence) && text(value.calculationId) && list(value.reasons) && value.reasons.every((reason) => record(reason) && text(reason.text) && ['measured', 'forecast', 'rule', 'assumed'].includes(String(reason.evidenceType))) && list(value.calculations) && value.calculations.every(calculation) && list(value.assumptions) && list(value.sensitivity);
const reading = (value: unknown) => record(value) && text(value.sensorId) && text(value.timestamp) && number(value.value) && ['ok', 'suspect', 'missing'].includes(String(value.quality));
const wording = (value: unknown) => value === 'llm' || value === 'template';
const optionalText = (value: unknown) => value == null || text(value);
const optionalNumber = (value: unknown) => value == null || number(value);
const strings = (value: unknown) => list(value) && value.every(text);
const network = (value: unknown) => ['water', 'electricity', 'cooling'].includes(String(value));
const boardFact = (v: unknown) => record(v) && text(v.label) && number(v.value) && text(v.unit) && ['measured', 'calculated', 'assumed'].includes(String(v.source));
const boardTask = (v: unknown) => record(v) && text(v.id) && network(v.agent) && text(v.assetId) && text(v.assetName) && text(v.kind) && text(v.action) && text(v.crew) && number(v.hoursToFix) && number(v.hourlyCost) && number(v.repairCost) && number(v.returnPerCrewHour) && strings(v.relievedBy);
const boardStep = (v: unknown) => record(v) && number(v.order) && text(v.taskId) && network(v.agent) && text(v.assetName) && text(v.action) && number(v.crewSlot) && number(v.startHour) && number(v.endHour) && number(v.hourlyCost) && number(v.lossUntilFixed) && number(v.repairCost) && number(v.repairMultiplier) && typeof v.dropped === 'boolean' && optionalNumber(v.reliefHour);
const boardPlan = (v: unknown) => record(v) && list(v.steps) && v.steps.every(boardStep) && ['expectedCost', 'p10', 'p50', 'p90', 'winShare', 'regretMean', 'regretP90', 'hourlyBleed', 'runs'].every((key) => number(v[key]));
const boardAlternative = (v: unknown) => record(v) && text(v.id) && text(v.name) && strings(v.order) && number(v.expectedCost) && number(v.extraCost);
const boardFlip = (v: unknown) => record(v) && text(v.taskId) && text(v.assetName) && number(v.current) && number(v.threshold) && ['below', 'above'].includes(String(v.direction));
const boardRisk = (v: unknown) => record(v) && ['overload', 'queue', 'tail'].includes(String(v.kind)) && text(v.text);

/** Narrow known messages; additive fields never invalidate an otherwise known envelope. */
export function parseEnvelope(raw: string): WsEnvelope | null {
  let message: unknown;
  try { message = JSON.parse(raw); } catch { return null; }
  if (!record(message) || !text(message.type) || !record(message.payload)) return null;
  const p = message.payload;
  let valid = false;
  switch (message.type) {
    case 'snapshot': valid = number(p.tick) && text(p.timestamp) && list(p.latest) && p.latest.every(reading); break;
    case 'reading': valid = number(p.tick) && text(p.timestamp) && list(p.readings) && p.readings.every(reading); break;
    case 'asset_status': valid = text(p.assetId) && ['normal', 'warning', 'fault', 'offline'].includes(String(p.status)) && number(p.healthScore); break;
    case 'alert': valid = text(p.id) && text(p.incidentId) && text(p.title) && text(p.summary) && list(p.assetIds) && ['info', 'warning', 'critical'].includes(String(p.severity)); break;
    case 'agent_finding': valid = text(p.id) && text(p.incidentId) && agent(p.agent) && text(p.headline) && text(p.explanation) && list(p.reasoningSteps) && p.reasoningSteps.every(text) && text(p.createdAt) && explanation(p.explain) && record(p.recommendedAction) && text(p.recommendedAction.text) && text(p.recommendedAction.crewType) && text(p.recommendedAction.urgency) && record(p.cost) && number(p.cost.actNow) && number(p.cost.costOfDelay) && number(p.cost.delayHours) && number(p.cost.expectedSaving); break;
    case 'incident': valid = text(p.id) && text(p.title) && list(p.alertIds) && ['info', 'warning', 'critical'].includes(String(p.severity)) && text(p.openedAt); break;
    case 'scenario_state': valid = text(p.runId) && text(p.scenarioId) && ['running', 'stopped'].includes(String(p.state)); break;
    case 'forecast_update': valid = text(p.network) && text(p.zoneId) && list(p.points) && record(p.metrics); break;
    case 'chat_route': valid = text(p.messageId) && routing(p.routing); break;
    case 'chat_chunk': valid = text(p.messageId) && text(p.text); break;
    case 'chat_done': valid = text(p.messageId) && list(p.agentsConsulted) && p.agentsConsulted.every(agent) && routing(p.routing); break;
    case 'board_state': valid = text(p.sessionId) && optionalText(p.incidentId) && ['running', 'done'].includes(String(p.state)) && text(p.startedAt); break;
    case 'board_message': valid = text(p.id) && text(p.sessionId) && number(p.round) && Number.isInteger(p.round) && p.round >= 0 && p.round <= 4 && agent(p.agent) && ['opening', 'position', 'dependent', 'challenge', 'decision', 'concession'].includes(String(p.kind)) && text(p.text) && list(p.facts) && p.facts.every(boardFact) && wording(p.source); break;
    case 'board_decision': valid = text(p.sessionId) && strings(p.incidentIds) && text(p.createdAt) && number(p.crews) && list(p.tasks) && p.tasks.every(boardTask) && boardPlan(p.plan) && list(p.alternatives) && p.alternatives.every(boardAlternative) && list(p.flips) && p.flips.every(boardFlip) && list(p.risks) && p.risks.every(boardRisk) && optionalNumber(p.extraCrewSaving) && typeof p.conflict === 'boolean' && text(p.summary) && wording(p.summarySource) && optionalText(p.calculationId); break;
    default: return null;
  }
  return valid ? message as unknown as WsEnvelope : null;
}

export function dispatchMessage(message: WsEnvelope) {
  const store = useStore.getState();
  switch (message.type) {
    case 'snapshot': store.applySnapshot(message.payload); break;
    case 'reading': store.ingestReadings(message.payload); break;
    case 'asset_status': store.updateAsset(message.payload); break;
    case 'alert': store.upsertAlert(message.payload); break;
    case 'incident': store.upsertIncident(message.payload); break;
    case 'agent_finding': store.upsertFinding(message.payload); break;
    case 'scenario_state': store.updateScenario(message.payload); break;
    case 'forecast_update': store.updateForecast(message.payload); break;
    case 'chat_route': store.routeChat(message.payload); break;
    case 'chat_chunk': store.appendChunk(message.payload); break;
    case 'chat_done': store.finishChat(message.payload); break;
    case 'board_state': store.applyBoardState(message.payload); break;
    case 'board_message': store.appendBoardMessage(message.payload); break;
    case 'board_decision': store.setBoardDecision(message.payload); break;
  }
}

export interface StreamSocket {
  onopen: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: (() => void) | null;
  onerror: (() => void) | null;
  close: () => void;
}

function createSocket(url: string): StreamSocket {
  const socket = new WebSocket(url);
  const adapter: StreamSocket = { onopen: null, onmessage: null, onclose: null, onerror: null, close: () => socket.close() };
  socket.onopen = () => adapter.onopen?.();
  socket.onmessage = (event) => adapter.onmessage?.({ data: event.data });
  socket.onclose = () => adapter.onclose?.();
  socket.onerror = () => adapter.onerror?.();
  return adapter;
}

export class StreamClient {
  private socket: StreamSocket | null = null;
  private retryTimer?: ReturnType<typeof setTimeout>;
  private snapshotTimer?: ReturnType<typeof setTimeout>;
  private stopped = true;
  private attempts = 0;
  private hasSnapshot = false;
  private pending: WsEnvelope[] = [];

  constructor(private url: string, private factory: (url: string) => StreamSocket = createSocket) {}

  start() { if (!this.stopped) return; this.stopped = false; this.connect(); }

  stop() {
    this.stopped = true;
    clearTimeout(this.retryTimer); clearTimeout(this.snapshotTimer);
    this.retryTimer = undefined; this.snapshotTimer = undefined;
    this.detach(); this.pending = [];
  }

  private detach() {
    if (!this.socket) return;
    this.socket.onopen = this.socket.onmessage = this.socket.onclose = this.socket.onerror = null;
    this.socket.close(); this.socket = null;
  }

  private reconnect = () => {
    if (this.stopped || this.retryTimer) return;
    clearTimeout(this.snapshotTimer); this.detach();
    useStore.setState({ connection: 'reconnecting', snapshotReady: false });
    const delay = Math.min(30_000, 1000 * 2 ** Math.min(this.attempts++, 5)) * (0.8 + Math.random() * 0.4);
    this.retryTimer = setTimeout(() => { this.retryTimer = undefined; this.connect(); }, delay);
  };

  private connect() {
    this.hasSnapshot = false; this.pending = [];
    useStore.setState({ connection: this.attempts ? 'reconnecting' : 'connecting', snapshotReady: false });
    try { this.socket = this.factory(this.url); } catch { this.reconnect(); return; }
    this.snapshotTimer = setTimeout(this.reconnect, 12_000);
    this.socket.onopen = () => { /* Connected only once the initial snapshot arrives. */ };
    this.socket.onmessage = ({ data }) => {
      if (typeof data !== 'string') return;
      const message = parseEnvelope(data);
      if (!message) return;
      if (message.type === 'snapshot') {
        if (this.hasSnapshot) return;
        dispatchMessage(message);
        this.hasSnapshot = true; this.attempts = 0;
        clearTimeout(this.snapshotTimer);
        useStore.getState().setConnection('connected');
        this.pending.forEach(dispatchMessage); this.pending = [];
      } else if (this.hasSnapshot) dispatchMessage(message);
      else if (this.pending.length < 100) this.pending.push(message);
    };
    this.socket.onclose = this.reconnect;
    this.socket.onerror = this.reconnect;
  }
}
