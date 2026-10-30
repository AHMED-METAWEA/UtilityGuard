import type { StateCreator } from 'zustand';
import type { AgentFinding, Alert, AssetStatusUpdate, AuditEntry, BoardDecision, BoardMessage, BoardSession, BoardState, ChatChunk, ChatDone, ChatMessage, ChatRoute, EarlyWarning, Incident, Forecast, Health, Reading, ReadingPayload, Scenario, ScenarioState, SnapshotPayload, Topology } from '../api/types';

export type LoadState = 'loading' | 'ready' | 'error';
export interface Resource<T> { data: T; status: LoadState; error?: string }
export type ConnectionStatus = 'connecting' | 'connected' | 'reconnecting' | 'offline';
export interface ConnectionSlice { connection: ConnectionStatus; health: Health | null; snapshotReady: boolean; setConnection: (status: ConnectionStatus) => void; setHealth: (health: Health) => void }
export interface TopologySlice { topology: Resource<Topology | null>; selectedZone: string | null; setTopology: (data: Topology) => void; selectZone: (id: string | null) => void }
export interface ReadingsSlice { readings: Record<string, Reading[]>; sensorTicks: Record<string, number>; ingestReadings: (batch: ReadingPayload) => void }
export interface AssetsSlice { assets: Record<string, AssetStatusUpdate>; updateAsset: (asset: AssetStatusUpdate) => void }
export interface AlertsSlice { alerts: Resource<Alert[]>; upsertAlert: (alert: Alert) => void }
export interface FindingsSlice { findings: Resource<AgentFinding[]>; upsertFinding: (finding: AgentFinding) => void }
export interface ScenariosSlice { scenarios: Resource<Scenario[]>; scenarioRuns: Record<string, ScenarioState>; updateScenario: (state: ScenarioState) => void }
export interface IncidentsSlice { incidents: Incident[]; upsertIncident: (incident: Incident) => void }
export interface ChatSlice { chat: Resource<ChatMessage[]>; appendUserMessage: (text: string) => void; routeChat: (route: ChatRoute) => void; appendChunk: (chunk: ChatChunk) => void; finishChat: (done: ChatDone) => void }
export interface ForecastSlice { forecast: Record<string, Forecast>; updateForecast: (forecast: Forecast) => void }
export interface BoardSlice {
  boards: Record<string, BoardSession>;
  applyBoardState: (state: BoardState) => void;
  appendBoardMessage: (message: BoardMessage) => void;
  setBoardDecision: (decision: BoardDecision) => void;
  loadBoardSession: (session: BoardSession) => void;
  clearBoards: () => void;
}
export interface AuditSlice { audit: AuditEntry[]; auditRevision: number; replaceAudit: (entries: AuditEntry[]) => void; appendAuditEntry: (entry: AuditEntry) => void; clearAudit: () => void }
export interface WarningsSlice { warnings: Record<string, EarlyWarning>; upsertWarning: (warning: EarlyWarning) => void; setWarnings: (warnings: EarlyWarning[]) => void; clearWarnings: () => void }
export type Store = ConnectionSlice & TopologySlice & ReadingsSlice & AssetsSlice & AlertsSlice & FindingsSlice & ScenariosSlice & ChatSlice & ForecastSlice & IncidentsSlice & BoardSlice & AuditSlice & WarningsSlice & { applySnapshot: (snapshot: SnapshotPayload) => void };
type Slice<T> = StateCreator<Store, [], [], T>;
const upsert = <T extends { id: string }>(items: T[], item: T) => [item, ...items.filter((old) => old.id !== item.id)];
export const connectionSlice: Slice<ConnectionSlice> = (set) => ({ connection: 'connecting', health: null, snapshotReady: false, setConnection: (connection) => set({ connection }), setHealth: (health) => set({ health }) });
export const topologySlice: Slice<TopologySlice> = (set) => ({ topology: { data: null, status: 'loading' }, selectedZone: null, setTopology: (data) => set({ topology: { data, status: 'ready' } }), selectZone: (selectedZone) => set({ selectedZone }) });
export const readingsSlice: Slice<ReadingsSlice> = (set) => ({
  readings: {}, sensorTicks: {}, ingestReadings: (batch) => set((state) => {
    const readings = { ...state.readings }; const sensorTicks = { ...state.sensorTicks };
    for (const reading of batch.readings) {
      if (!Number.isFinite(reading.value) || !reading.sensorId || batch.tick <= (sensorTicks[reading.sensorId] ?? -1)) continue;
      readings[reading.sensorId] = [...(readings[reading.sensorId] ?? []), reading].slice(-300);
      sensorTicks[reading.sensorId] = batch.tick;
    }
    return { readings, sensorTicks };
  }),
});
export const assetsSlice: Slice<AssetsSlice> = (set) => ({ assets: {}, updateAsset: (asset) => set((s) => ({ assets: { ...s.assets, [asset.assetId]: asset } })) });
export const alertsSlice: Slice<AlertsSlice> = (set) => ({ alerts: { data: [], status: 'loading' }, upsertAlert: (alert) => set((s) => ({ alerts: { data: upsert(s.alerts.data, alert), status: 'ready' } })) });
export const findingsSlice: Slice<FindingsSlice> = (set) => ({ findings: { data: [], status: 'loading' }, upsertFinding: (finding) => set((s) => ({ findings: { data: upsert(s.findings.data, finding), status: 'ready' } })) });
export const scenariosSlice: Slice<ScenariosSlice> = (set) => ({ scenarios: { data: [], status: 'loading' }, scenarioRuns: {}, updateScenario: (run) => set((s) => ({ scenarioRuns: { ...s.scenarioRuns, [run.runId]: run } })) });
export const chatSlice: Slice<ChatSlice> = (set) => ({
  chat: { data: [], status: 'loading' },
  appendUserMessage: (text) => set((s) => ({ chat: { status: 'ready', data: [...s.chat.data, { messageId: crypto.randomUUID(), role: 'user', text, complete: true, routing: [], agentsConsulted: [], fallback: false }] } })),
  routeChat: (route) => set((s) => {
    const old = s.chat.data.find((m) => m.messageId === route.messageId);
    const message: ChatMessage = { role: 'assistant', text: '', complete: false, agentsConsulted: [], fallback: false, ...old, ...route, activeAgent: route.routing[0]?.agent };
    return { chat: { status: 'ready', data: old ? s.chat.data.map((m) => m.messageId === route.messageId ? message : m) : [...s.chat.data, message] } };
  }),
  appendChunk: (chunk) => set((s) => {
    const old = s.chat.data.find((m) => m.messageId === chunk.messageId);
    const message: ChatMessage = { messageId: chunk.messageId, role: 'assistant', complete: false, agentsConsulted: [], routing: [], fallback: false, ...old, text: (old?.text ?? '') + chunk.text, activeAgent: chunk.agent };
    return { chat: { status: 'ready', data: old ? s.chat.data.map((m) => m.messageId === chunk.messageId ? message : m) : [...s.chat.data, message] } };
  }),
  finishChat: (done) => set((s) => {
    const old = s.chat.data.find((m) => m.messageId === done.messageId);
    const message: ChatMessage = { role: 'assistant', text: '', ...old, ...done, complete: true, activeAgent: undefined };
    return { chat: { status: 'ready', data: old ? s.chat.data.map((m) => m.messageId === done.messageId ? message : m) : [...s.chat.data, message] } };
  }),
});
export const incidentsSlice: Slice<IncidentsSlice> = (set) => ({ incidents: [], upsertIncident: (incident) => set((s) => ({ incidents: upsert(s.incidents, incident) })) });
export const forecastSlice: Slice<ForecastSlice> = (set) => ({ forecast: {}, updateForecast: (forecast) => set((s) => ({ forecast: { ...s.forecast, [`${forecast.network}:${forecast.zoneId}`]: forecast } })) });

// A message may arrive before its metadata. The state event fills in startedAt.
const emptyBoard = (id: string): BoardSession => ({ id, state: 'running', startedAt: '', messages: [] });
const uniqueMessages = (messages: BoardMessage[]) => {
  const seen = new Set<string>();
  return messages.filter((message) => {
    if (seen.has(message.id)) return false;
    seen.add(message.id); return true;
  });
};
export const boardSlice: Slice<BoardSlice> = (set) => ({
  boards: {},
  applyBoardState: (event) => set((s) => {
    const old = s.boards[event.sessionId] ?? emptyBoard(event.sessionId);
    const session: BoardSession = { ...old, id: event.sessionId, startedAt: event.startedAt,
      ...(event.incidentId != null ? { incidentId: event.incidentId } : {}),
      state: old.decision || old.state === 'done' ? 'done' : event.state };
    return { boards: { ...s.boards, [session.id]: session } };
  }),
  appendBoardMessage: (message) => set((s) => {
    const old = s.boards[message.sessionId] ?? emptyBoard(message.sessionId);
    if (old.messages.some((m) => m.id === message.id)) return s;
    return { boards: { ...s.boards, [old.id]: { ...old, messages: [...old.messages, message] } } };
  }),
  setBoardDecision: (decision) => set((s) => {
    const old = s.boards[decision.sessionId] ?? { ...emptyBoard(decision.sessionId), startedAt: decision.createdAt };
    return { boards: { ...s.boards, [old.id]: { ...old, state: 'done', decision } } };
  }),
  loadBoardSession: (session) => set((s) => {
    const old = s.boards[session.id];
    const decision = old?.decision ?? session.decision;
    return { boards: { ...s.boards, [session.id]: { ...session,
      incidentId: session.incidentId ?? old?.incidentId,
      messages: uniqueMessages([...session.messages, ...(old?.messages ?? [])]), decision,
      state: decision || old?.state === 'done' ? 'done' : session.state } } };
  }),
  clearBoards: () => set({ boards: {} }),
});

/** Stable selector result: newest meeting covering this incident, including shared decisions. */
// a proposal dropped as stale never hides a plan that is being carried out or waiting for a decision
const live = (b: BoardSession) => (b.decision?.status === 'stale' ? 0 : 1);
export const latestBoardFor = (incidentId: string) => (s: Pick<Store, 'boards'>): BoardSession | undefined =>
  Object.values(s.boards).filter((b) => b.incidentId === incidentId || b.decision?.incidentIds.includes(incidentId))
    .reduce<BoardSession | undefined>((latest, b) => !latest || live(b) > live(latest) || (live(b) === live(latest) && (Date.parse(b.startedAt) || 0) > (Date.parse(latest.startedAt) || 0)) ? b : latest, undefined);

/** Sequence is authoritative even when timestamps arrive out of order. */
export const auditEntries = (entries: AuditEntry[]) => [...new Map(entries.map((entry) => [entry.seq, entry])).values()].sort((a, b) => a.seq - b.seq).slice(-500);
export const auditSlice: Slice<AuditSlice> = (set) => ({
  audit: [], auditRevision: 0,
  replaceAudit: (entries) => set({ audit: auditEntries(entries) }),
  appendAuditEntry: (entry) => set((s) => ({ audit: auditEntries([...s.audit, entry]) })),
  clearAudit: () => set((s) => ({ audit: [], auditRevision: s.auditRevision + 1 })),
});

/** Keep record iteration newest first, including when an older warning is updated. */
export const warningRecord = (warnings: EarlyWarning[]) => Object.fromEntries([...new Map(warnings.map((warning) => [warning.id, warning])).values()]
  .sort((a, b) => Date.parse(b.createdAt) - Date.parse(a.createdAt)).map((warning) => [warning.id, warning]));
export const warningsSlice: Slice<WarningsSlice> = (set) => ({
  warnings: {},
  upsertWarning: (warning) => set((s) => ({ warnings: warningRecord([...Object.values(s.warnings), warning]) })),
  setWarnings: (warnings) => set({ warnings: warningRecord(warnings) }),
  clearWarnings: () => set({ warnings: {} }),
});
