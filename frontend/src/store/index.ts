import { create } from 'zustand';
import { alertsSlice, assetsSlice, chatSlice, connectionSlice, findingsSlice, forecastSlice, readingsSlice, scenariosSlice, topologySlice, type Store } from './slices';

export const useStore = create<Store>()((...args) => ({
  ...connectionSlice(...args), ...topologySlice(...args), ...readingsSlice(...args),
  ...assetsSlice(...args), ...alertsSlice(...args), ...findingsSlice(...args),
  ...scenariosSlice(...args), ...chatSlice(...args), ...forecastSlice(...args),
  applySnapshot: (snapshot) => {
    const [set, get] = args;
    set({
      snapshotReady: true, readings: {}, sensorTicks: {}, assets: {}, forecast: {}, scenarioRuns: {},
      alerts: { data: snapshot.alerts ?? [], status: 'ready' },
      findings: { data: snapshot.findings ?? [], status: 'ready' },
      chat: { data: snapshot.chat ?? [], status: 'ready' },
      ...(snapshot.topology ? { topology: { data: snapshot.topology, status: 'ready' } } : {}),
      ...(snapshot.llm ? { health: { status: 'ok', llm: snapshot.llm } } : {}),
    });
    const store = get();
    store.ingestReadings({ tick: snapshot.tick, timestamp: snapshot.timestamp, readings: snapshot.latest });
    snapshot.assets?.forEach(store.updateAsset);
    snapshot.findings?.forEach(store.upsertFinding);
    snapshot.scenarios?.forEach(store.updateScenario);
    snapshot.forecast?.forEach(store.updateForecast);
  },
}));
