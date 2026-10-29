import { useMemo, useState } from 'react';
import { CircleAlert, Flame, ShieldCheck, Sun } from 'lucide-react';
import { useFetch, type DemandSeries, type ForecastEvidence } from '../api/dash';
import type { Forecast as ForecastT, ModelMetrics, Network } from '../api/types';
import { useStore } from '../store';
import { TimeChart } from '../charts/TimeChart';
import { Columns } from '../charts/Columns';
import { ModelBars } from '../charts/ModelBars';
import { comparisonSummary, modelLabel } from './forecastComparison';
import './forecast-comparison.css';
import { Card, Empty, Meter, NET, NetIcon, Seg, Skeleton } from '../ui';
import { EvidenceTag } from '../ui/Provenance';
import { ago, fmt, hhmm } from '../lib/format';

const FEATURE: Record<string, string> = {
  same_hour_last_week: 'Same hour last week', same_hour_yesterday: 'Same hour yesterday', hour_of_day: 'Hour of day', temperature: 'Temperature',
  weekday: 'Day of week', weekend: 'Weekend', holiday: 'Holiday', zone_size: 'District size', zone_type: 'District type',
  age_years: 'Asset age', run_hours_k: 'Running hours', vibration_trend: 'Deviation from the twin', pressure_variance: 'Signal variability', load_factor: 'Load factor', temp_excess: 'Temperature excess',
};
const ms = (iso: string) => Date.parse(iso);
const day = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'Africa/Cairo' });

function Stat({ label, value, sub, tone }: { label: string; value: string; sub?: string; tone?: 'ok' | 'warn' }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div className="sub" style={{ fontSize: 12 }}>{label}</div>
      <div className="num" style={{ fontSize: 24, fontWeight: 650, color: tone === 'ok' ? 'var(--ok-text)' : tone === 'warn' ? 'var(--warn-text)' : 'var(--text)' }}>{value}</div>
      {sub && <div className="muted" style={{ fontSize: 12 }}>{sub}</div>}
    </div>
  );
}

function Inputs({ s }: { s: DemandSeries }) {
  const d = s.drivers; if (!d) return null;
  const corr = (d.liveCorrection - 1) * 100;
  return (
    <div className="row wrap" style={{ gap: 8, marginBottom: 12 }} aria-label="What the forecast was given">
      <span className="chip">Air temperature {fmt(d.temperatureNow, 1)}°C now, peak {fmt(d.temperaturePeak, 1)}°C</span>
      {d.heatWave ? <span className="chip warn"><Flame size={13} />Heat wave {fmt(d.heatWave)}°C in the inputs</span>
        : d.holiday ? <span className="chip warn"><Sun size={13} />Holiday, residential ×{fmt(d.holiday, 2)} in the inputs</span>
        : <span className="chip ok">Normal day</span>}
      <span className="chip" title="Live demand divided by what the model says for this moment, applied to the near term and faded out">
        Live correction {Math.abs(corr) < 0.05 ? 'none' : `${corr > 0 ? '+' : ''}${fmt(corr, 1)}%`}{Math.abs(corr) >= 0.05 && ` fading over ${fmt(d.correctionHours)} h`}
      </span>
      <span className="chip" title="The weather feed is given a deliberate error so the measured accuracy is not flattered">Weather feed error ±{fmt(d.weatherErrorC, 1)}°C assumed</span>
      <span className="chip">Trained on data through {day(d.trainedThrough)}</span>
    </div>
  );
}

function ModelComparison({ ev, color }: { ev: ForecastEvidence; color: string }) {
  const rows = ev.comparison ?? [];
  const { live, bestOther, betterModel, betterBaseline } = comparisonSummary(rows, ev.baselines);
  const hourly = [live, bestOther].filter((row) => row && row.hourlyMape.length === 24).map((row) => ({
    id: row!.name, label: `${modelLabel(row!.name)}${row!.isLive ? ' · production' : ' · best other on test'}`,
    color: row!.isLive ? color : 'var(--text-2)', dash: !row!.isLive,
    points: row!.hourlyMape.map((v, hour) => ({ t: Date.UTC(2026, 0, 1, hour), v })),
  }));
  return <Card title="Which model, and why" sub="Chosen on dev out-of-fold MAE; test MAE shows independent evidence" style={{ marginBottom: 16 }}>
    {rows.length ? <div className="model-comparison">
      <p style={{ marginTop: 0 }}><b>{modelLabel(ev.selected)}</b> has the lowest dev out-of-fold MAE.
        {ev.selectedMatchesLive ? ' It is also the production model.' : ' The live forecast still uses LightGBM; the selected model is not deployed.'}</p>
      {betterModel && live && <p>{modelLabel(betterModel.name)} also beats LightGBM on test MAE by {fmt(live.mae - betterModel.mae, 3)} {ev.unit} ({fmt((1 - betterModel.mae / live.mae) * 100, 1)}%).</p>}
      {betterBaseline && live && <p>{betterBaseline.name} beats the production model on test MAE by {fmt(live.mae - betterBaseline.mae, 3)} {ev.unit}.</p>}
      <div className="comparison-layout">
        <div><p className="sub">Test MAE ({ev.unit}) · lower is better</p><ModelBars rows={rows.map((row) => ({ ...row, label: modelLabel(row.name) }))} color={color} unit={ev.unit} /></div>
        <div><p className="sub">Test MAPE by Cairo hour · LightGBM and the best other model on test MAE</p>
          {hourly.length === 2 ? <TimeChart label="Model test MAPE by Cairo hour" series={hourly} unit="%" digits={1} height={220} xTick={(t) => `${new Date(t).getUTCHours()}:00`} /> : <Empty title="Hourly comparison unavailable">Retrain to add the hourly scores.</Empty>}
        </div>
      </div>
      <table className="comparison-table" aria-label="Model comparison scores">
        <thead><tr><th>Model</th><th>Test MAE ({ev.unit})</th><th>Test MAPE</th><th>Out-of-fold MAE ({ev.unit})</th><th>LightGBM wins in X% of resamples</th></tr></thead>
        <tbody>{rows.map((row) => <tr key={row.name} data-live={row.isLive}>
          <td data-label="Model"><span><b>{modelLabel(row.name)}</b>{row.isLive && ' · production'}{row.isSelected && ' · selected'}</span></td>
          <td data-label="Test MAE" className="num">{fmt(row.mae, 3)} {ev.unit}</td>
          <td data-label="Test MAPE" className="num">{fmt(row.mape, 2)}%</td>
          <td data-label="Out-of-fold MAE" className="num">{fmt(row.oofMae, 3)} {ev.unit}</td>
          <td data-label="LightGBM wins" className="num">{row.isLive ? '— (self)' : `${fmt(row.pLgbmBetter * 100, 1)}%`}</td>
        </tr>)}</tbody>
      </table>
      <details style={{ marginTop: 12 }}><summary>Method and model settings</summary>
        <p className="sub">{ev.comparisonNote}</p>
        {rows.map((row) => <p className="sub" key={row.name}><b>{modelLabel(row.name)}</b> · {row.params} · fits {fmt(row.fitSeconds, 2)} s</p>)}
      </details>
    </div> : <Empty title="Model comparison unavailable">The current evidence predates model comparison. Scores will appear after retraining.</Empty>}
  </Card>;
}

function Evidence({ ev, color }: { ev: ForecastEvidence; color: string }) {
  const best = ev.baselines.reduce<(typeof ev.baselines)[number] | undefined>((a, b) => (!a || b.mae < a.mae ? b : a), undefined);
  const rows = [{ name: 'This model', mae: ev.model.mae, mape: ev.model.mape, us: true }, ...ev.baselines.map((b) => ({ ...b, us: false }))];
  const maxMae = Math.max(...rows.map((r) => r.mae));
  const nsc = ev.scenario;
  const lc = ev.liveCorrection;
  const gain = lc.maeWithout > 0 ? 1 - lc.maeWith / lc.maeWithout : 0;
  const cols = ev.byHour.map((h) => ({ key: String(h.hour), label: `${String(h.hour).padStart(2, '0')}:00`, value: h.mape, color }));
  return (
    <>
      <div className="page-head" style={{ marginTop: 8 }}>
        <div>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 650 }}>Can you trust this forecast?</h2>
          <p>Scored on the last {ev.testDays} days ({fmt(ev.points)} half-hour points) that nothing was fitted on, against simple methods anyone could use.</p>
        </div>
      </div>
      <ModelComparison ev={ev} color={color} />
      <section className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 340px), 1fr))' }}>
        <Card title="Against simple methods" sub="Average error over the test days, lower is better">
          <div className="stack" style={{ gap: 12 }}>
            {rows.map((r) => (
              <div key={r.name}>
                <div className="row spread" style={{ marginBottom: 4 }}>
                  <span style={{ fontWeight: r.us ? 650 : 400 }}>{r.name}</span>
                  <span className="num"><b>{fmt(r.mape, 1)}%</b> <span className="muted">· {fmt(r.mae, ev.unit === 'MW' ? 1 : 0)} {ev.unit}</span></span>
                </div>
                <Meter value={r.mae / maxMae} color={r.us ? color : 'var(--text-3)'} label={`${r.name} error`} />
              </div>
            ))}
          </div>
          {best && <div className="row" style={{ gap: 8, marginTop: 14 }}>
            <span className={`chip ${ev.skill > 0.15 ? 'ok' : 'warn'}`}>{ev.skill > 0 ? <ShieldCheck size={13} /> : <CircleAlert size={13} />}{fmt(Math.abs(ev.skill) * 100)}% {ev.skill >= 0 ? 'less' : 'more'} error than {best.name.toLowerCase()}</span>
          </div>}
        </Card>

        <Card title="Is the uncertainty band honest?" sub={`The band is built to contain ${fmt(ev.nominal * 100)}% of real values`}>
          <div className="row" style={{ gap: 24, alignItems: 'flex-end' }}>
            <Stat label="Real values inside the band" value={`${fmt(ev.coverage * 100)}%`} tone={ev.coverage >= ev.nominal - 0.05 ? 'ok' : 'warn'} sub={`on ${fmt(ev.points)} test points`} />
            <Stat label="Built for" value={`${fmt(ev.nominal * 100)}%`} />
          </div>
          <div style={{ margin: '14px 0 8px' }}><Meter value={ev.coverage} color={ev.coverage >= ev.nominal - 0.05 ? 'var(--ok)' : 'var(--warn)'} label="Band coverage" /></div>
          <p className="muted" style={{ fontSize: 12, margin: 0 }}>
            {ev.coverage >= ev.nominal ? 'At or above the target, so the band errs on the safe side.' : ev.coverage >= ev.nominal - 0.05 ? 'Within 5 points of the target, which is the noise of a ten-day test.' : 'Below the target, so treat the band as optimistic.'} The band comes from the model's own errors on days it had not seen, measured separately for normal, heat-wave and holiday days.
          </p>
        </Card>

        <Card title="Heat waves and holidays" sub={`${nsc.days} scenario days in the test window`}>
          <div className="row" style={{ gap: 24, alignItems: 'flex-end' }}>
            <Stat label="Error, scenario told" value={`${fmt(nsc.mape, 1)}%`} tone="ok" />
            <Stat label="If the model had not been told" value={`${fmt(nsc.mapeBlind, 1)}%`} tone="warn" />
          </div>
          <p className="muted" style={{ fontSize: 12, margin: '14px 0 0' }}>
            When you run a heat wave or a holiday in the Scenario lab, the forecast takes it as an input. The grey line on the chart shows the same day without it.
          </p>
        </Card>

        <Card title="Live correction" sub="Pulls the near term toward what is actually happening">
          <div className="row" style={{ gap: 24, alignItems: 'flex-end' }}>
            <Stat label="Error without" value={`${fmt(lc.maeWithout, ev.unit === 'MW' ? 1 : 0)}`} sub={ev.unit} />
            <Stat label="Error with" value={`${fmt(lc.maeWith, ev.unit === 'MW' ? 1 : 0)}`} sub={ev.unit} tone={gain > 0 ? 'ok' : 'warn'} />
            <Stat label="Improvement" value={`${fmt(gain * 100)}%`} tone={gain > 0 ? 'ok' : 'warn'} />
          </div>
          <p className="muted" style={{ fontSize: 12, margin: '14px 0 0' }}>Day-to-day swings cannot be known in advance, but they show up in the first hours. The correction fades over {fmt(lc.tauHours)} h, a value picked on past errors.</p>
        </Card>

        <Card title="Where the error sits" sub="Average error by hour of day, %">
          <Columns data={cols} height={180} unit="%" digits={1} label="Forecast error by hour" tick={(c, i) => (i % 6 === 0 ? c.label.slice(0, 2) : null)} />
        </Card>

        <Card title="By district" sub="Test window" pad={false}>
          <table className="table"><thead><tr><th>District</th><th className="r">Error</th><th className="r">MAE</th></tr></thead><tbody>
            {[...ev.zones].sort((a, b) => b.mape - a.mape).map((z) => <tr key={z.zoneId}><td>{z.name}</td><td className="r num"><b>{fmt(z.mape, 1)}%</b></td><td className="r num">{fmt(z.mae, ev.unit === 'MW' ? 2 : 0)} <span className="muted">{ev.unit}</span></td></tr>)}
          </tbody></table>
        </Card>
      </section>
      <Card style={{ marginTop: 16 }}>
        <div className="row" style={{ gap: 12, alignItems: 'flex-start' }}>
          <CircleAlert size={18} color="var(--text-3)" style={{ marginTop: 2, flexShrink: 0 }} />
          <div className="sub">
            <b style={{ color: 'var(--text)' }}>What this does and does not show.</b> These scores are measured on simulated history, so they prove the method and the pipeline, not that the real city will behave the same. Demand in the twin varies randomly by about 3.5% from one day to the next, and no model can know that in advance, so some error always remains. The model is retrained daily on the latest {ev.trainDays + ev.testDays} days; with real SCADA data the same scoring would run unchanged.
          </div>
        </div>
      </Card>
    </>
  );
}

export function Forecast() {
  const [net, setNet] = useState<Network>('water');
  const [zone, setZone] = useState('all');
  const topology = useStore((s) => s.topology.data);
  const assets = useStore((s) => s.assets);
  const series = useFetch<DemandSeries>(`/series/demand?network=${net}&zoneId=${zone}`, 15000);
  const evidence = useFetch<ForecastEvidence>(`/forecast/evidence?network=${net}`, 300000);
  const metrics = useFetch<ModelMetrics>('/metrics', 120000);
  const zoneFc = useFetch<ForecastT>(zone === 'all' ? null : `/forecast?network=${net}&zoneId=${zone}&horizonHours=24`);
  const color = NET[net].color;

  const view = useMemo(() => {
    const d = series.data; if (!d) return null;
    const hasBase = !!d.baseline?.length;
    return {
      series: [
        { id: 'a', label: 'Measured', color, fill: true, points: d.actual.map((p) => ({ t: ms(p.t), v: p.v })) },
        ...(d.hindcast.length ? [{ id: 'h', label: 'Model on past hours', color: 'var(--text-3)', dash: true, width: 1.4, points: d.hindcast.map((p) => ({ t: ms(p.t), v: p.v })) }] : []),
        ...(hasBase ? [{ id: 'b', label: 'Same day without the scenario', color: 'var(--text-2)', dash: true, width: 1.8, points: d.baseline!.map((p) => ({ t: ms(p.timestamp), v: p.predicted })) }] : []),
        { id: 'f', label: hasBase ? 'Forecast with the scenario' : 'Forecast', color, dash: true, width: 2, points: d.forecast.map((p) => ({ t: ms(p.timestamp), v: p.predicted })) },
      ],
      band: { label: 'Forecast band', color, points: d.forecast.map((p) => ({ t: ms(p.timestamp), lo: p.lower, hi: p.upper })) },
      peak: d.forecast.reduce((a, b) => (b.predicted > a.predicted ? b : a)),
      basePeak: hasBase ? d.baseline!.reduce((a, b) => (b.predicted > a.predicted ? b : a)) : null, now: ms(d.now),
    };
  }, [series.data, color]);

  const importances = (zone !== 'all' && zoneFc.data?.topFeatures) || evidence.data?.importances || metrics.data?.featureImportances[`forecast-${net}`] || [];
  const failure = metrics.data?.featureImportances.failure ?? [];
  const risky = Object.values(assets).filter((a) => a.failureProbability !== undefined).sort((a, b) => (b.failureProbability ?? 0) - (a.failureProbability ?? 0)).slice(0, 6);
  const name = (id: string) => topology?.assets.find((a) => a.id === id)?.name ?? id;
  const det = metrics.data?.detection as (Record<string, number> | undefined);
  const d = series.data;
  const lift = d && view?.basePeak ? (view.peak.predicted / view.basePeak.predicted - 1) * 100 : null;

  return (
    <div className="page forecast-page">
      <div className="page-head">
        <div><h1>Forecasts and model quality</h1><p>Every forecast shows what it was given, how it scored on days it had not seen, and how sure it is.</p></div>
        <div className="row wrap" style={{ gap: 10 }}>
          <Seg label="Network" value={net} onChange={(v) => { setNet(v); setZone('all'); }} options={[{ v: 'water', label: 'Water' }, { v: 'electricity', label: 'Electricity' }, { v: 'cooling', label: 'Cooling' }]} />
          <select className="field" style={{ width: 220 }} value={zone} onChange={(e) => setZone(e.target.value)} aria-label="District">
            <option value="all">All districts</option>
            {topology?.zones.map((z) => <option key={z.id} value={z.id}>{z.name}</option>)}
          </select>
        </div>
      </div>

      <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 420px), 1fr))' }}>
        <Card title={<span className="row" style={{ gap: 8 }}><NetIcon n={net} />{NET[net].label} demand</span>} sub="Last 24 hours measured, next 24 hours forecast with its band">
          {d && view ? <>
            <Inputs s={d} />
            <TimeChart label="Demand, measured and forecast" series={view.series} band={view.band} nowT={view.now} unit={d.unit} height={320} />
            <div className="row wrap spread" style={{ marginTop: 10, gap: 12 }}>
              <span className="muted" style={{ fontSize: 12 }}>
                <EvidenceTag e="model" /> Peak expected at <b className="num" style={{ color: 'var(--text)' }}>{hhmm(view.peak.timestamp)}</b> · <b className="num" style={{ color: 'var(--text)' }}>{fmt(view.peak.predicted)} {d.unit}</b>
                {lift !== null && <> · <b className="num" style={{ color: 'var(--warn-text)' }}>{lift > 0 ? '+' : ''}{fmt(lift)}%</b> against a normal day</>}
              </span>
              <span className="row" style={{ gap: 14 }}>
                {d.hindcastMape != null && <span className="num" title="Error of the model on the past 24 hours, compared with what was measured"><span className="muted">Last 24 h </span><b>{fmt(d.hindcastMape, 1)}</b> <span className="muted">%</span></span>}
                {[['MAE', d.metrics.mae, d.unit], ['MAPE', d.metrics.mape, '%']].map(([l, v, u]) => <span key={l as string} className="num" title="Scored on the test days"><span className="muted">{l} </span><b>{fmt(v as number, 1)}</b> <span className="muted">{u}</span></span>)}
              </span>
            </div>
          </> : series.error ? <Empty title="No forecast for this selection">Cooling only exists in districts with a cooling loop. Choose another district.</Empty> : <Skeleton h={320} />}
        </Card>
        <Card title="What drives this forecast" sub="Share of the model's decisions explained by each input">
          {importances.length ? <div className="stack" style={{ gap: 12 }}>{importances.slice(0, 6).map((f) => (
            <div key={f.feature}><div className="row spread" style={{ marginBottom: 4 }}><span>{FEATURE[f.feature] ?? f.feature}</span><b className="num">{fmt(f.importance * 100, f.importance < 0.01 ? 1 : 0)}%</b></div><Meter value={f.importance} color={color} label={f.feature} /></div>))}</div> : <Skeleton h={180} />}
          <p className="muted" style={{ fontSize: 12, margin: '14px 0 0' }}>A gradient-boosted model per network. It reads the clock, the weather feed, the holiday flag and demand at the same hour yesterday and last week.</p>
        </Card>
      </div>

      {evidence.data ? <Evidence ev={evidence.data} color={color} /> : <Card title="Can you trust this forecast?">{evidence.error ? <Empty title="Evidence unavailable">The comparison could not be loaded. Try again when the data feed is available.</Empty> : <Skeleton h={160} />}</Card>}

      <section className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))', marginTop: 16 }}>
        <Card title="Fault detection" sub="Measured over labelled scenario runs against normal operation">
          {det ? <div className="grid" style={{ gridTemplateColumns: 'repeat(3, minmax(0, 1fr))' }}>
            {[['Precision', det.precision], ['Recall', det.recall], ['F1', det.f1]].map(([l, v]) => <div key={l as string}><div className="sub" style={{ fontSize: 12 }}>{l}</div><div className="num" style={{ fontSize: 26, fontWeight: 650 }}>{fmt((v as number) * 100)}%</div></div>)}
            <div style={{ gridColumn: '1 / -1' }} className="muted"><span style={{ fontSize: 12 }}>Precision is the share of alarms that were real. Recall is the share of real faults found. Failure model AUC {fmt(det.failureAuc ?? 0, 2)}. Model run {metrics.data ? ago(metrics.data.lastRun) : ''} ago.</span></div>
          </div> : <Skeleton h={100} />}
        </Card>
        <Card title="Assets with the highest risk score" sub="A model score for the next 30 days, learned from simulated wear. It ranks assets; it is not a calibrated chance of failure." right={<EvidenceTag e="model" />}>
          {risky.length ? <div className="stack" style={{ gap: 12 }}>{risky.map((a) => <div key={a.assetId}><div className="row spread" style={{ marginBottom: 4 }}><span>{name(a.assetId)}</span><b className="num">{fmt((a.failureProbability ?? 0) * 100)}%</b></div><Meter value={a.failureProbability ?? 0} color={(a.failureProbability ?? 0) > 0.3 ? 'var(--warn)' : 'var(--accent)'} label={name(a.assetId)} /></div>)}</div> : <Skeleton h={160} />}
          {failure.length > 0 && <div className="muted" style={{ fontSize: 12, marginTop: 12 }}>Main inputs: {failure.slice(0, 3).map((f) => FEATURE[f.feature] ?? f.feature).join(', ')}.</div>}
        </Card>
      </section>
    </div>
  );
}
