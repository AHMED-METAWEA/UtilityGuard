import type { AgentFinding, Alert, ApiError, Incident, Health, Scenario, Topology } from './types';
import { useStore } from '../store';

export class RequestError extends Error {
  constructor(public code: string, message: string) { super(message); }
}
export async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options, signal: options?.signal ?? AbortSignal.timeout(8000),
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null) as ApiError | null;
    throw new RequestError(error?.code ?? String(response.status), error?.message ?? response.statusText);
  }
  return response.json() as Promise<T>;
}

export async function loadResources(signal?: AbortSignal) {
  const store = useStore.getState();
  const loads = [
    request<Topology>('/topology', { signal }).then((data) => {
      if (!Array.isArray(data.zones) || !Array.isArray(data.assets) || !Array.isArray(data.sensors) || !Array.isArray(data.links)) throw new Error('INVALID_TOPOLOGY');
      store.setTopology(data);
    }).catch(() => { if (!signal?.aborted && !useStore.getState().topology.data) useStore.setState({ topology: { data: null, status: 'error' } }); }),
    request<Alert[]>('/alerts', { signal }).then((data) => {
      if (!Array.isArray(data)) throw new Error('INVALID_ALERTS');
      // A snapshot is authoritative if it arrives while REST is loading.
      if (!useStore.getState().snapshotReady) useStore.setState({ alerts: { data, status: 'ready' } });
    }).catch(() => { if (!signal?.aborted && !useStore.getState().snapshotReady) useStore.setState({ alerts: { data: [], status: 'error' } }); }),
    request<Scenario[]>('/scenarios', { signal }).then((data) => {
      if (!Array.isArray(data)) throw new Error('INVALID_SCENARIOS');
      useStore.setState({ scenarios: { data, status: 'ready' } });
    }).catch(() => { if (!signal?.aborted) useStore.setState({ scenarios: { data: [], status: 'error' } }); }),
    request<AgentFinding[]>('/findings', { signal }).then((data) => {
      if (!Array.isArray(data)) throw new Error('INVALID_FINDINGS');
      if (!useStore.getState().snapshotReady) useStore.setState({ findings: { data, status: 'ready' } });
    }).catch(() => undefined),
    request<Incident[]>('/incidents', { signal }).then((data) => {
      if (Array.isArray(data) && !useStore.getState().snapshotReady) useStore.setState({ incidents: data });
    }).catch(() => undefined),
    request<Health>('/health', { signal }).then(store.setHealth).catch(() => undefined),
  ];
  await Promise.allSettled(loads);
}

export function post<T>(path: string, body?: unknown) { return request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }); }
export function put<T>(path: string, body: unknown) { return request<T>(path, { method: 'PUT', body: JSON.stringify(body) }); }
