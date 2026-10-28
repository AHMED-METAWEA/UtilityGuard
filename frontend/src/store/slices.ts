import type { StateCreator } from 'zustand';
import type { AgentFinding, Alert, AssetStatusUpdate, ChatChunk, ChatDone, ChatMessage, ChatRoute, Incident, Forecast, Health, Reading, ReadingPayload, Scenario, ScenarioState, SnapshotPayload, Topology } from '../api/types';

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
export interface IncidentsSlice { incidents: Incident[] }
export interface ChatSlice { chat: Resource<ChatMessage[]>; appendUserMessage: (text: string) => void; routeChat: (route: ChatRoute) => void; appendChunk: (chunk: ChatChunk) => void; finishChat: (done: ChatDone) => void }
export interface ForecastSlice { forecast: Record<string, Forecast>; updateForecast: (forecast: Forecast) => void }
export type Store = ConnectionSlice & TopologySlice & ReadingsSlice & AssetsSlice & AlertsSlice & FindingsSlice & ScenariosSlice & ChatSlice & ForecastSlice & IncidentsSlice & { applySnapshot: (snapshot: SnapshotPayload) => void };
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
export const forecastSlice: Slice<ForecastSlice> = (set) => ({ forecast: {}, updateForecast: (forecast) => set((s) => ({ forecast: { ...s.forecast, [`${forecast.network}:${forecast.zoneId}`]: forecast } })) });
