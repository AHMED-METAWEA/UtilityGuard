import { useEffect, useMemo, useState } from 'react';
import { put } from '../api/client';
import { useFetch } from '../api/dash';
import type { Asset, Assumption, CalculationRecord, Sensor } from '../api/types';
import { useStore } from '../store';
import { useUi } from '../store/ui';
import { Drawer, Empty, Meter, NetIcon, Skeleton, StatusChip } from '../ui';
import { Basis } from '../ui/Provenance';
import { CalcView } from '../ui/Calc';
import { TimeChart } from '../charts/TimeChart';
import { ago, fmt, sensorLabel } from '../lib/format';
import { useSensorSeries } from '../lib/useSensorSeries';

const NETVAR = { water: 'var(--water)', electricity: 'var(--elec)', cooling: 'var(--cool)' } as const;

function SensorCard({ s, asset }: { s: Sensor; asset: Asset }) {
  const { measured, expected, loading } = useSensorSeries(s.id, 5);
  const last = measured[measured.length - 1];
  return (
    <div className="card" style={{ padding: 12 }}>
      <div className="row spread"><span style={{ fontWeight: 500 }}>{sensorLabel(s.id.replace(s.assetId + '-', '')).replace(/^./, (c) => c.toUpperCase())}</span><b className="num">{last?.v != null ? fmt(last.v, Math.abs(last.v) < 100 ? 2 : 0) : '–'} <span className="muted">{s.unit}</span></b></div>
      <div className="muted" style={{ fontSize: 12 }}>Normal range {fmt(s.normalRange.min, 1)} to {fmt(s.normalRange.max, 1)} {s.unit}</div>
      {measured.length > 3 ? <TimeChart label={`${s.id} recent readings`} height={140} unit={s.unit} digits={2} xTick={(t) => new Date(t).toLocaleTimeString('en-GB', { minute: '2-digit', second: '2-digit' })}
        series={[{ id: 'm', label: 'Measured', color: NETVAR[asset.network], points: measured }, { id: 'e', label: 'Twin expects', color: 'var(--text-3)', dash: true, width: 1.5, points: expected }]}
        thresholds={[{ v: s.normalRange.min, label: '', color: 'var(--border-strong)' }, { v: s.normalRange.max, label: '', color: 'var(--border-strong)' }]} /> : loading ? <Skeleton h={120} style={{ marginTop: 8 }} /> : null}
    </div>
  );
}

export function AssetDrawer() {
  const id = useUi((s) => s.selectedAsset); const set = useUi((s) => s.set);
  const topology = useStore((s) => s.topology.data);
  const state = useStore((s) => (id ? s.assets[id] : undefined));
  const alerts = useStore((s) => s.alerts.data);
  const asset = useMemo(() => topology?.assets.find((a) => a.id === id), [topology, id]);
  if (!id || !asset || !topology) return null;
  const zone = topology.zones.find((z) => z.id === asset.zoneId);
  const sensors = topology.sensors.filter((s) => s.assetId === id);
  const mine = alerts.filter((a) => a.assetIds.includes(id)).slice(0, 4);
  const status = state?.status ?? asset.status;
  return (
    <Drawer title={<span className="row" style={{ gap: 8 }}><NetIcon n={asset.network} size={18} />{asset.name}</span>} onClose={() => set({ selectedAsset: null })} width={480}>
      <div className="row wrap" style={{ gap: 8 }}><StatusChip s={status} /><span className="chip">{asset.kind}</span><span className="chip">{zone?.name ?? asset.zoneId}</span></div>
      <div className="grid" style={{ gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <div><div className="sub" style={{ fontSize: 12, marginBottom: 4 }}>Health score</div><div className="num" style={{ fontSize: 24, fontWeight: 650 }}>{state ? fmt(state.healthScore) : '–'}<span className="muted" style={{ fontSize: 13 }}> / 100</span></div><Meter value={(state?.healthScore ?? 0) / 100} color={NETVAR[asset.network]} label="Health score" /></div>
        <div><div className="sub" style={{ fontSize: 12, marginBottom: 4 }}>Failure risk, 30 days</div><div className="num" style={{ fontSize: 24, fontWeight: 650 }}>{state?.failureProbability !== undefined ? `${fmt(state.failureProbability * 100)}%` : '–'}</div><div className="muted" style={{ fontSize: 12 }}>from the failure model</div></div>
      </div>
      {sensors.length === 0 && <Empty title="No sensors on this asset">It is monitored through its neighbours.</Empty>}
      {sensors.map((s) => <SensorCard key={s.id} s={s} asset={asset} />)}
      <div>
        <div className="card-title" style={{ marginBottom: 6 }}>Recent alerts</div>
        {mine.length === 0 ? <div className="muted">None for this asset.</div> : mine.map((a) => <div key={a.id} className="row spread" style={{ padding: '6px 0', borderTop: '1px solid var(--border)' }}><span>{a.title}</span><span className="muted" style={{ fontSize: 12 }}>{ago(a.detectedAt)} ago · {a.state}</span></div>)}
      </div>
    </Drawer>
  );
}

const GROUPS: [string, (k: string) => boolean][] = [
  ['Prices', (k) => ['water_price', 'elec_tariff', 'cooling_price'].includes(k)],
  ['Customers and outage value', (k) => k.startsWith('customers_zone_') || k.startsWith('customer_hour_') || k.startsWith('voll_')],
  ['Repairs and response', (k) => ['crew_callout', 'response_hours', 'false_dispatch', 'false_dispatch_interval', 'planned_factor'].includes(k) || k.startsWith('parts_') || k.startsWith('delay_hours_') || k.startsWith('late_multiplier_')],
  ['Review thresholds', (k) => k.startsWith('saving_min_') || k.startsWith('saving_max_')],
  ['Savings levers and finance', () => true],
];

export function AssumptionsDrawer() {
  const open = useUi((s) => s.assumptionsOpen); const set = useUi((s) => s.set); const notify = useUi((s) => s.notify);
  const { data, loading, reload } = useFetch<Assumption[]>(open ? '/assumptions' : null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  useEffect(() => { if (open) setEdits({}); }, [open]);
  if (!open) return null;
  const changed = Object.entries(edits).filter(([k, v]) => v !== '' && Number(v) !== data?.find((a) => a.key === k)?.value);
  const save = async () => {
    setSaving(true);
    try {
      const r = await put<{ recomputed: number }>('/assumptions', Object.fromEntries(changed.map(([k, v]) => [k, Number(v)])));
      notify(r.recomputed ? `Saved. ${r.recomputed} open decision${r.recomputed > 1 ? 's' : ''} recomputed.` : 'Saved. Savings and costs are recomputed.');
      setEdits({}); reload();
    } catch (e) { notify((e as Error).message); } finally { setSaving(false); }
  };
  const grouped = (data ?? []).reduce<Record<string, Assumption[]>>((acc, a) => { const g = GROUPS.find(([, f]) => f(a.key))![0]; (acc[g] ??= []).push(a); return acc; }, {});
  return (
    <Drawer title="Assumptions" onClose={() => set({ assumptionsOpen: false })}
      foot={<><span className="sub" style={{ marginInlineEnd: 'auto', alignSelf: 'center' }}>{changed.length ? `${changed.length} change${changed.length > 1 ? 's' : ''}` : 'Edit a value to recompute'}</span><button className="btn primary" disabled={!changed.length || saving} onClick={save}>Save and recompute</button></>}>
      <p className="sub" style={{ margin: 0 }}>Every cost and saving reads these values. Open a value to see where it comes from. Planning figures are illustrative until you replace them with your own.</p>
      {loading && !data ? [0, 1, 2, 3].map((i) => <Skeleton key={i} h={44} />) : GROUPS.map(([g]) => grouped[g] && (
        <section key={g}>
          <div className="card-title" style={{ marginBottom: 8 }}>{g}</div>
          <div className="stack" style={{ gap: 8 }}>
            {grouped[g].map((a) => (
              <label key={a.key} className="row spread" style={{ gap: 12 }}>
                <span style={{ minWidth: 0, flex: 1 }}>{a.name}{a.userChanged && <span className="chip warn" style={{ marginInlineStart: 8 }}>changed</span>}<span className="muted" style={{ display: 'block', fontSize: 12 }}>{a.source}</span>
                  <details style={{ marginTop: 2 }} onClick={(e) => e.stopPropagation()}><summary style={{ fontSize: 12, color: 'var(--accent)', cursor: 'pointer' }}>Where this comes from</summary><div style={{ marginTop: 6 }}><Basis a={a} /></div></details></span>
                <span className="row" style={{ gap: 6, flex: 'none' }}><input className="field num" style={{ width: 110, textAlign: 'end' }} type="number" step="any" value={edits[a.key] ?? String(a.value)} onChange={(e) => setEdits((o) => ({ ...o, [a.key]: e.target.value }))} /><span className="muted" style={{ width: 70, fontSize: 12 }}>{a.unit}</span></span>
              </label>
            ))}
          </div>
        </section>
      ))}
    </Drawer>
  );
}

export function CalcDrawer() {
  const id = useUi((s) => s.calcId); const set = useUi((s) => s.set);
  const { data, loading, error } = useFetch<CalculationRecord>(id ? `/calculations/${id}` : null);
  if (!id) return null;
  return (
    <Drawer title={`Calculation ${id}`} onClose={() => set({ calcId: null })} width={560}>
      {data ? <CalcView cost={data.cost} explanation={data.explanation} /> : error ? <Empty title="Could not load this calculation">{error}</Empty> : loading ? <><Skeleton h={60} /><Skeleton h={160} /></> : null}
    </Drawer>
  );
}
