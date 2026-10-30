import { create } from 'zustand';
import { alertsSlice, assetsSlice, auditSlice, boardSlice, chatSlice, connectionSlice, findingsSlice, forecastSlice, incidentsSlice, readingsSlice, scenariosSlice, topologySlice, warningsSlice, type Store } from './slices';

export { latestBoardFor } from './slices';

export const useStore = create<Store>()((...args) => ({
  ...connectionSlice(...args), ...topologySlice(...args), ...readingsSlice(...args),
  ...assetsSlice(...args), ...alertsSlice(...args), ...findingsSlice(...args),
  ...scenariosSlice(...args), ...chatSlice(...args), ...forecastSlice(...args),
  ...incidentsSlice(...args), ...boardSlice(...args), ...auditSlice(...args), ...warningsSlice(...args),
  applySnapshot: (snapshot) => {
    const [set, get] = args;
    set({
      incidents: snapshot.incidents ?? [],
      snapshotReady: true, readings: {}, sensorTicks: {}, assets: {}, forecast: {}, scenarioRuns: {}, boards: {}, warnings: {}, audit: [], auditRevision: get().auditRevision + 1,
      alerts: { data: snapshot.alerts ?? [], status: 'ready' },
      findings: { data: snapshot.findings ?? [], status: 'ready' },
      chat: { data: get().chat.data, status: 'ready' },
      ...(snapshot.topology ? { topology: { data: snapshot.topology, status: 'ready' } } : {}),
      ...(snapshot.llm ? { health: { status: 'ok', llm: snapshot.llm } } : {}),
    });
    const store = get();
    store.ingestReadings({ tick: snapshot.tick, timestamp: snapshot.timestamp, readings: snapshot.latest });
    snapshot.assets?.forEach(store.updateAsset);
    snapshot.findings?.forEach(store.upsertFinding);
    snapshot.scenarios?.forEach(store.updateScenario);
  },
}));

if (typeof window !== 'undefined' && import.meta.env?.DEV) (window as unknown as { __store: typeof useStore }).__store = useStore;
