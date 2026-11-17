import { useMemo } from 'react';
import { useFetch } from '../api/dash';
import type { Topology } from '../api/types';
import { useStore } from '../store';
import type { Pt } from '../charts/TimeChart';

interface SensorHistory { sensorId: string; unit: string; normalRange: { min: number; max: number }; points: { t: string; v: number; expected?: number }[] }

/** The lead-up from the backend history, continued by live readings from the stream. */
export function useSensorSeries(sensorId: string | undefined, minutes = 10) {
  const hist = useFetch<SensorHistory>(sensorId ? `/sensors/${sensorId}/history?minutes=${minutes}` : null);
  const live = useStore((s) => (sensorId ? s.readings[sensorId] : undefined));
  return useMemo(() => {
    const base = hist.data?.points ?? [];
    const lastT = base.length ? Date.parse(base[base.length - 1].t) : 0;
    const measured: Pt[] = base.map((p) => ({ t: Date.parse(p.t), v: p.v }));
    const expected: Pt[] = base.map((p) => ({ t: Date.parse(p.t), v: p.expected ?? null }));
    for (const r of live ?? []) {
      const t = Date.parse(r.timestamp);
      if (t > lastT) { measured.push({ t, v: r.value }); expected.push({ t, v: r.forecast ?? null }); }
    }
    return { measured, expected, loading: hist.loading && !hist.data, unit: hist.data?.unit, range: hist.data?.normalRange };
  }, [hist.data, hist.loading, live]);
}

/** [west, south, east, north] around some assets, for the map to frame an incident. */
export function assetBounds(topology: Topology | null, ids: string[]): [number, number, number, number] | null {
  if (!topology) return null;
  const pts = topology.assets.filter((a) => ids.includes(a.id)).flatMap((a) => a.geometry.coordinates);
  if (!pts.length) return null;
  const lng = pts.map((p) => p.lng), lat = pts.map((p) => p.lat);
  const padX = Math.max(0.004, (Math.max(...lng) - Math.min(...lng)) * 0.15), padY = Math.max(0.003, (Math.max(...lat) - Math.min(...lat)) * 0.15);
  return [Math.min(...lng) - padX, Math.min(...lat) - padY, Math.max(...lng) + padX, Math.max(...lat) + padY];
}

/** Agent text carries verified figures; this only adds thousands separators to money. */
export const money = (s: string) => s.replace(/(\d{4,}(?:\.\d+)?)(?=\s?EGP)/g, (m) => Math.round(Number(m)).toLocaleString('en-US'));
