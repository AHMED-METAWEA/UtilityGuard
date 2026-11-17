import { useEffect } from 'react';
import type { EarlyWarning } from './types';
import { request } from './client';
import { isEarlyWarning } from './ws';
import { useStore } from '../store';
import { useUi } from '../store/ui';

/** Hydrate once per snapshot, preserving any pushes received while REST was loading. */
export function WarningsSync() {
  const ready = useStore((s) => s.snapshotReady);
  const revision = useStore((s) => s.auditRevision);
  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    const streamed: EarlyWarning[] = [];
    const unsubscribe = useStore.subscribe((next, previous) => {
      if (next.auditRevision !== revision) { controller.abort(); return; }
      if (next.warnings !== previous.warnings) streamed.push(...Object.values(next.warnings).filter((warning) => previous.warnings[warning.id] !== warning));
    });
    void request<EarlyWarning[]>('/warnings', { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]) }).then((data) => {
      if (controller.signal.aborted || useStore.getState().auditRevision !== revision) return;
      if (!Array.isArray(data) || !data.every(isEarlyWarning)) throw new Error('Invalid early warning data.');
      unsubscribe();
      useStore.getState().setWarnings([...data, ...streamed]);
    }).catch((e: unknown) => {
      if (!controller.signal.aborted) useUi.getState().notify(e instanceof Error ? e.message : 'Could not load early warnings.');
    }).finally(unsubscribe);
    return () => { controller.abort(); unsubscribe(); };
  }, [ready, revision]);
  return null;
}
