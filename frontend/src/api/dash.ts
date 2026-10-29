import { useCallback, useEffect, useRef, useState } from 'react';
import { request } from './client';
import type { Assumption, CalcInputLike, ForecastMetrics, ForecastPoint, Network, Reason } from './types';

export interface SeriesPoint { t: string; v: number }
export interface Kpi {
  id: string; label: string; value: number; unit: string; digits: number; target?: number; better?: 'lower' | 'higher';
  delta?: number; series: SeriesPoint[]; note: string; formula: string; network?: Network;
}
export interface DemandSeries { network: Network; zoneId: string; unit: string; now: string; actual: SeriesPoint[]; forecast: ForecastPoint[]; metrics: ForecastMetrics }
export interface SubstationSeries { unit: string; capacity: number; lines: { id: string; name: string; points: SeriesPoint[] }[] }
export interface DailySeries { unit: string; target: number; days: { date: string; value: number; ambientMax: number }[] }
export interface SavingRange { p10: number; p50: number; p90: number; runs: number }
export interface Opportunity {
  id: string; title: string; network: Network; where: string; summary: string; monthlySaving: number; savingRange: SavingRange;
  investment: number; paybackDays: number | null; formula: string; inputs: CalcInputLike[]; reasons: Reason[]; assumptions: Assumption[];
  confidence: number; status: 'recommended' | 'approved';
}
export interface SavingsReport { generatedAt: string; totalMonthly: number; totalInvestment: number; totalRange: SavingRange; opportunities: Opportunity[] }

export interface Fetched<T> { data: T | null; error: string | null; loading: boolean; reload: () => void }

/** Fetch an endpoint, optionally on an interval. Keeps the previous data while refreshing so nothing flashes. */
export function useFetch<T>(path: string | null, every = 0): Fetched<T> {
  const [state, setState] = useState<{ data: T | null; error: string | null; loading: boolean }>({ data: null, error: null, loading: !!path });
  const alive = useRef(true);
  const load = useCallback(() => {
    if (!path) return;
    request<T>(path).then((data) => alive.current && setState({ data, error: null, loading: false }))
      .catch((e: Error) => alive.current && setState((s) => ({ data: s.data, error: e.message, loading: false })));
  }, [path]);
  useEffect(() => {
    alive.current = true;
    setState((s) => ({ ...s, loading: s.data === null }));
    load();
    const i = every ? setInterval(load, every) : undefined;
    return () => { alive.current = false; if (i) clearInterval(i); };
  }, [load, every]);
  return { ...state, reload: load };
}
