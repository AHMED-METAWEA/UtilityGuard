import { useMemo, useState } from 'react';
import { useFetch, type DemandSeries } from '../api/dash';
import type { Forecast as ForecastT, ModelMetrics, Network } from '../api/types';
import { useStore } from '../store';
import { TimeChart } from '../charts/TimeChart';
import { Card, Empty, Meter, NET, NetIcon, Seg, Skeleton } from '../ui';
import { ago, fmt, hhmm } from '../lib/format';

const FEATURE: Record<string, string> = {
  same_hour_last_week: 'Same hour last week', same_hour_yesterday: 'Same hour yesterday', hour_of_day: 'Hour of day', temperature: 'Temperature',
  weekday: 'Day of week', weekend: 'Weekend', zone_size: 'District size', zone_type: 'District type',
  age_years: 'Asset age', run_hours_k: 'Running hours', vibration_trend: 'Deviation from the twin', pressure_variance: 'Signal variability', load_factor: 'Load factor', temp_excess: 'Temperature excess',
};
const ms = (iso: string) => Date.parse(iso);

export function Forecast() {
  const [net, setNet] = useState<Network>('water');
  const [zone, setZone] = useState('all');
  const topology = useStore((s) => s.topology.data);
  const assets = useStore((s) => s.assets);
  const series = useFetch<DemandSeries>(`/series/demand?network=${net}&zoneId=${zone}`, 60000);
  const metrics = useFetch<ModelMetrics>('/metrics', 120000);
  const zoneFc = useFetch<ForecastT>(zone === 'all' ? null : `/forecast?network=${net}&zoneId=${zone}&horizonHours=24`);
  const color = NET[net].color;

  const view = useMemo(() => {
    const d = series.data; if (!d) return null;
    return {
      series: [
        { id: 'a', label: 'Measured', color, fill: true, points: d.actual.map((p) => ({ t: ms(p.t), v: p.v })) },
        { id: 'f', label: 'Forecast', color, dash: true, width: 1.8, points: d.forecast.map((p) => ({ t: ms(p.timestamp), v: p.predicted })) },
      ],
      band: { label: 'Forecast band', color, points: d.forecast.map((p) => ({ t: ms(p.timestamp), lo: p.lower, hi: p.upper })) },
      peak: d.forecast.reduce((a, b) => (b.predicted > a.predicted ? b : a)), now: ms(d.now),
    };
  }, [series.data, color]);

  const importances = (zone !== 'all' && zoneFc.data?.topFeatures) || metrics.data?.featureImportances[`forecast-${net}`] || [];
  const failure = metrics.data?.featureImportances.failure ?? [];
  const risky = Object.values(assets).filter((a) => a.failureProbability !== undefined).sort((a, b) => (b.failureProbability ?? 0) - (a.failureProbability ?? 0)).slice(0, 6);
  const name = (id: string) => topology?.assets.find((a) => a.id === id)?.name ?? id;
  const det = metrics.data?.detection as (Record<string, number> | undefined);

  return (
    <div className="page">
      <div className="page-head">
        <div><h1>Forecasts and model quality</h1><p>Every forecast shows its inputs, its error on past data and its uncertainty band. The models are measured, not just shown.</p></div>
        <div className="row wrap" style={{ gap: 10 }}>
          <Seg label="Network" value={net} onChange={(v) => { setNet(v); setZone('all'); }} options={[{ v: 'water', label: 'Water' }, { v: 'electricity', label: 'Electricity' }, { v: 'cooling', label: 'Cooling' }]} />
          <select className="field" style={{ width: 220 }} value={zone} onChange={(e) => setZone(e.target.value)} aria-label="District">
            <option value="all">All districts</option>
            {topology?.zones.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
          </select>
        </div>
      </div>

      <div className="inc-grid" style={{ gridTemplateColumns: 'minmax(0, 1.6fr) minmax(320px, 1fr)' }}>
        <Card title={<span className="row" style={{ gap: 8 }}><NetIcon n={net} />{NET[net].label} demand</span>} sub="Last 24 hours measured, next 24 hours forecast with its band">
          {series.data && view ? <>
            <TimeChart label="Demand, measured and forecast" series={view.series} band={view.band} nowT={view.now} unit={series.data.unit} height={300} />
            <div className="row wrap spread" style={{ marginTop: 10, gap: 12 }}>
              <span className="muted" style={{ fontSize: 12 }}>Peak expected at <b className="num" style={{ color: 'var(--text)' }}>{hhmm(view.peak.timestamp)}</b> · <b className="num" style={{ color: 'var(--text)' }}>{fmt(view.peak.predicted)} {series.data.unit}</b></span>
              <span className="row" style={{ gap: 14 }}>{[['MAE', series.data.metrics.mae, series.data.unit], ['RMSE', series.data.metrics.rmse, series.data.unit], ['MAPE', series.data.metrics.mape, '%']].map(([l, v, u]) => <span key={l as string} className="num"><span className="muted">{l} </span><b>{fmt(v as number, 1)}</b> <span className="muted">{u}</span></span>)}</span>
            </div>
          </> : series.error ? <Empty title="No forecast for this selection">Cooling only exists in districts with a cooling loop. Choose another district.</Empty> : <Skeleton h={320} />}
        </Card>
        <Card title="What drives this forecast" sub="Share of the model's decisions explained by each input">
          {importances.length ? <div className="stack" style={{ gap: 12 }}>{importances.slice(0, 6).map((f) => (
            <div key={f.feature}><div className="row spread" style={{ marginBottom: 4 }}><span>{FEATURE[f.feature] ?? f.feature}</span><b className="num">{fmt(f.importance * 100, f.importance < 0.01 ? 1 : 0)}%</b></div><Meter value={f.importance} color={color} label={f.feature} /></div>))}</div> : <Skeleton h={180} />}
          <p className="muted" style={{ fontSize: 12, margin: '14px 0 0' }}>This forecast is based on these inputs and a gradient-boosted model, with the error shown beside the chart.</p>
        </Card>
      </div>

      <section className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))' }}>
        <Card title="Forecast accuracy" sub={metrics.data ? `Held-out last 10 days. Model run ${ago(metrics.data.lastRun)} ago` : 'Held-out last 10 days'} pad={false}>
          {metrics.data ? <table className="table"><thead><tr><th>Network</th><th className="r">MAE</th><th className="r">RMSE</th><th className="r">MAPE</th></tr></thead><tbody>
            {metrics.data.forecasting.map((m) => <tr key={m.network}><td><span className="row" style={{ gap: 8 }}><NetIcon n={m.network} />{NET[m.network].label}</span></td><td className="r num">{fmt(m.mae, 2)} <span className="muted">{m.unit}</span></td><td className="r num">{fmt(m.rmse, 2)} <span className="muted">{m.unit}</span></td><td className="r num"><b>{fmt(m.mape, 1)}%</b></td></tr>)}</tbody></table> : <div className="card-body"><Skeleton h={120} /></div>}
        </Card>
        <Card title="Fault detection" sub="Measured over labelled scenario runs against normal operation">
          {det ? <div className="grid" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
            {[['Precision', det.precision], ['Recall', det.recall], ['F1', det.f1]].map(([l, v]) => <div key={l as string}><div className="sub" style={{ fontSize: 12 }}>{l}</div><div className="num" style={{ fontSize: 26, fontWeight: 650 }}>{fmt((v as number) * 100)}%</div></div>)}
            <div style={{ gridColumn: '1 / -1' }} className="muted"><span style={{ fontSize: 12 }}>Precision is the share of alarms that were real. Recall is the share of real faults found. Failure model AUC {fmt(det.failureAuc ?? 0, 2)}.</span></div>
          </div> : <Skeleton h={100} />}
        </Card>
        <Card title="Assets most likely to fail" sub="Failure model, next 30 days">
          {risky.length ? <div className="stack" style={{ gap: 12 }}>{risky.map((a) => <div key={a.assetId}><div className="row spread" style={{ marginBottom: 4 }}><span>{name(a.assetId)}</span><b className="num">{fmt((a.failureProbability ?? 0) * 100)}%</b></div><Meter value={a.failureProbability ?? 0} color={(a.failureProbability ?? 0) > 0.3 ? 'var(--warn)' : 'var(--accent)'} label={name(a.assetId)} /></div>)}</div> : <Skeleton h={160} />}
          {failure.length > 0 && <div className="muted" style={{ fontSize: 12, marginTop: 12 }}>Main inputs: {failure.slice(0, 3).map((f) => FEATURE[f.feature] ?? f.feature).join(', ')}.</div>}
        </Card>
      </section>
    </div>
  );
}
